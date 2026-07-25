"""AutomationExecutor — orchestrates signal → decision → plan → order.

The executor wires the deterministic pipeline together. It NEVER calls a broker
connector directly: orders flow through ``src.trading.service.place_order``,
which owns the mandate gate, kill switch, and audit. All side-effecting
dependencies (the place-order callable, the clock) are injected so the executor
is unit-testable with a fake order sink and a fixed clock.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional, Sequence

from src.live.automation.decision_gate import evaluate_signal
from src.live.automation.models import (
    DecisionOutcome,
    ExecutionDecision,
    SignalCandidate,
    SignalDirection,
    TradePlan,
)
from src.live.automation.policy import AutomationPolicy
from src.live.mandate.model import AssetClass, InstrumentType

logger = logging.getLogger(__name__)

#: Signature of the injected order sink (bound to src.trading.service.place_order).
PlaceOrderFn = Callable[..., Mapping[str, Any]]


def _now_ms() -> int:
    return int(time.time() * 1000)


def _direction_to_side(direction: SignalDirection) -> str:
    """Map a signal direction to an order side."""
    if direction is SignalDirection.LONG:
        return "buy"
    # SHORT and CLOSE both exit → "sell"
    return "sell"


def policy_cooldown_ms(policy: AutomationPolicy) -> int:
    """Return the policy's per-symbol cooldown in milliseconds."""
    return policy.symbol_cooldown_seconds * 1000


def build_trade_plan(
    decision: ExecutionDecision,
    *,
    instrument_type: InstrumentType,
    client_order_id: str,
    stop_loss: Optional[float] = None,
    take_profit: Optional[float] = None,
    max_holding_seconds: Optional[int] = None,
    asset_class: Optional[AssetClass] = None,
    order_type: str = "limit",
    limit_price: Optional[float] = None,
    time_in_force: str = "day",
) -> TradePlan:
    """Build a TradePlan from an allowed decision + sizing/exit parameters.

    The sizing comes from the signal's target_quantity/target_notional. For a
    limit order the limit_price defaults to the signal's price_ref when not
    explicitly supplied.
    """
    signal = decision.signal
    side = _direction_to_side(signal.direction)

    effective_limit_price = limit_price
    if order_type == "limit" and effective_limit_price is None:
        effective_limit_price = signal.price_ref

    return TradePlan(
        decision=decision,
        symbol=signal.symbol,
        side=side,
        instrument_type=instrument_type,
        quantity=signal.target_quantity,
        notional=signal.target_notional,
        order_type=order_type,
        limit_price=effective_limit_price,
        time_in_force=time_in_force,
        stop_loss=stop_loss,
        take_profit=take_profit,
        max_holding_seconds=max_holding_seconds,
        asset_class=asset_class,
        client_order_id=client_order_id,
    )


@dataclass
class ExecutionResult:
    """Outcome of executing one signal through the full pipeline."""

    signal: SignalCandidate
    decision: ExecutionDecision
    plan: Optional[TradePlan] = None
    order_response: Optional[Mapping[str, Any]] = None
    error: Optional[str] = None

    @property
    def placed(self) -> bool:
        return self.order_response is not None and self.error is None


class AutomationExecutor:
    """Orchestrates the deterministic signal-to-order pipeline.

    Dependencies are injected:
        place_order_fn: Bound to src.trading.service.place_order (never a raw
            connector). Receives the normalized order kwargs.
        now_fn: Injectable clock returning epoch ms.
    """

    def __init__(
        self,
        policy: AutomationPolicy,
        place_order_fn: PlaceOrderFn,
        *,
        now_fn: Callable[[], int] = _now_ms,
        profile_id: Optional[str] = None,
        session_id: str = "",
    ) -> None:
        self._policy = policy
        self._place_order = place_order_fn
        self._now_fn = now_fn
        self._profile_id = profile_id
        self._session_id = session_id

    def run_cycle(
        self,
        signals: Sequence[SignalCandidate],
        *,
        instrument_type: InstrumentType,
        seen_keys: set[str] | None = None,
        cooldown_deadlines: dict[str, int] | None = None,
        exit_protection_available: bool = False,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        max_holding_seconds: Optional[int] = None,
        asset_class: Optional[AssetClass] = None,
        order_type: str = "limit",
        time_in_force: str = "day",
        pre_submit_check: Optional[Callable[[TradePlan], bool]] = None,
    ) -> list[ExecutionResult]:
        """Evaluate and (when allowed) place orders for a batch of signals.

        Enforces max_orders_per_cycle and updates seen_keys + cooldown_deadlines
        in place as orders are placed, so later signals in the same cycle see
        the dedup/cooldown state of earlier ones.
        """
        seen_keys = seen_keys if seen_keys is not None else set()
        cooldown_deadlines = cooldown_deadlines if cooldown_deadlines is not None else {}
        results: list[ExecutionResult] = []
        placed_count = 0

        for signal in signals:
            decision = evaluate_signal(
                signal,
                self._policy,
                now_ms=self._now_fn(),
                seen_keys=seen_keys,
                cooldown_deadlines=cooldown_deadlines,
                exit_protection_available=exit_protection_available,
            )

            if not decision.is_allowed:
                results.append(ExecutionResult(signal=signal, decision=decision))
                continue

            if placed_count >= self._policy.max_orders_per_cycle:
                # Cycle cap reached — record as policy rejection, do not place.
                capped = ExecutionDecision(
                    signal=signal,
                    outcome=DecisionOutcome.REJECTED_POLICY,
                    reason=f"max_orders_per_cycle ({self._policy.max_orders_per_cycle}) reached",
                )
                results.append(ExecutionResult(signal=signal, decision=capped))
                continue

            client_order_id = f"auto-{signal.idempotency_key}-{self._now_fn()}"
            try:
                plan = build_trade_plan(
                    decision,
                    instrument_type=instrument_type,
                    client_order_id=client_order_id,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    max_holding_seconds=max_holding_seconds,
                    asset_class=asset_class,
                    order_type=order_type,
                    time_in_force=time_in_force,
                )
            except ValueError as exc:
                # Plan construction failed (e.g. no sizing) — fail-closed, skip.
                logger.warning("automation plan build failed for %s: %s", signal.symbol, exc)
                results.append(ExecutionResult(signal=signal, decision=decision, error=str(exc)))
                continue

            # Pre-submit bounds check (fail-closed: exception → block).
            if pre_submit_check is not None:
                try:
                    if not pre_submit_check(plan):
                        results.append(
                            ExecutionResult(
                                signal=signal, decision=decision, plan=plan,
                                error="pre-submit bounds check failed",
                            )
                        )
                        continue
                except Exception as exc:  # noqa: BLE001 - fail-closed
                    logger.warning(
                        "pre-submit check raised for %s (fail-closed): %s", signal.symbol, exc
                    )
                    results.append(
                        ExecutionResult(
                            signal=signal, decision=decision, plan=plan,
                            error=f"pre-submit check error: {exc}",
                        )
                    )
                    continue

            response = self._submit(plan)
            if response is None:
                # Submission failed hard — do NOT mark key as seen (allow retry
                # next cycle per no-auto-retry-on-ambiguous rule; the caller
                # reconciles). Record the error.
                results.append(
                    ExecutionResult(signal=signal, decision=decision, plan=plan, error="submission failed")
                )
                continue

            # Placed successfully — consume dedup key + set cooldown.
            seen_keys.add(signal.idempotency_key)
            cooldown_deadlines[signal.symbol] = self._now_fn() + policy_cooldown_ms(self._policy)
            placed_count += 1
            results.append(
                ExecutionResult(signal=signal, decision=decision, plan=plan, order_response=response)
            )

        return results

    def _submit(self, plan: TradePlan) -> Optional[Mapping[str, Any]]:
        """Submit a TradePlan through the service-layer place_order.

        Exit-protection parameters (stop_loss/take_profit/max_holding_seconds)
        are forwarded to the service layer via ``**overrides`` so downstream
        connectors/audit can persist them. Returns the response dict, or None
        on a hard submission failure.
        """
        try:
            overrides: dict[str, Any] = {}
            if plan.stop_loss is not None:
                overrides["stop_loss"] = plan.stop_loss
            if plan.take_profit is not None:
                overrides["take_profit"] = plan.take_profit
            if plan.max_holding_seconds is not None:
                overrides["max_holding_seconds"] = plan.max_holding_seconds
            return self._place_order(
                plan.symbol,
                self._profile_id,
                side=plan.side,
                quantity=plan.quantity,
                notional=plan.notional,
                order_type=plan.order_type,
                limit_price=plan.limit_price,
                time_in_force=plan.time_in_force,
                session_id=self._session_id,
                **overrides,
            )
        except Exception as exc:  # noqa: BLE001 - fail-closed, never crash the cycle
            logger.exception("automation order submission raised for %s: %s", plan.symbol, exc)
            return None
