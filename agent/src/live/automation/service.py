"""AutomationService — the control-plane entry point for automated trading.

This service orchestrates the full automated pipeline: load policy + mandate,
run the executor, and return structured results. It is called by the API
control plane (never by the agent loop or tools).

The service is stateless per call: all durable state (policy, mandate, dedup
keys, cooldown deadlines) is loaded from the filesystem or passed in. This
makes it safe to call from multiple API requests without shared mutable state.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Optional, Sequence

from src.live.automation.bounds_check import check_automation_bounds, check_slippage
from src.live.automation.executor import AutomationExecutor, ExecutionResult
from src.live.automation.models import SignalCandidate, TradePlan
from src.live.automation.policy import AutomationMode, AutomationPolicy
from src.live.automation.runtime_state import AutomationRuntimeState
from src.live.mandate.model import AssetClass, AutomationBounds, InstrumentType, Mandate

logger = logging.getLogger(__name__)

#: Signature of the injected order sink (bound to src.trading.service.place_order).
PlaceOrderFn = Callable[..., Mapping[str, Any]]


@dataclass(frozen=True)
class AutomationCycleResult:
    """Structured result of one automation cycle."""

    broker: str
    mode: str
    signals_evaluated: int
    orders_placed: int
    orders_rejected: int
    errors: int
    results: tuple[dict[str, Any], ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "broker": self.broker,
            "mode": self.mode,
            "signals_evaluated": self.signals_evaluated,
            "orders_placed": self.orders_placed,
            "orders_rejected": self.orders_rejected,
            "errors": self.errors,
            "results": list(self.results),
        }


class AutomationService:
    """Control-plane service for automated trading cycles.

    Dependencies are injected:
        place_order_fn: Bound to src.trading.service.place_order.
        load_mandate_fn: Bound to src.live.mandate.store.load_mandate.
        now_fn: Injectable clock returning epoch ms.
    """

    def __init__(
        self,
        *,
        place_order_fn: PlaceOrderFn,
        load_mandate_fn: Callable[..., Optional[Mandate]],
        now_fn: Callable[[], int] | None = None,
    ) -> None:
        self._place_order = place_order_fn
        self._load_mandate = load_mandate_fn
        self._now_fn = now_fn

    def run_cycle(
        self,
        broker: str,
        signals: Sequence[SignalCandidate],
        policy: AutomationPolicy,
        *,
        instrument_type: InstrumentType,
        asset_class: Optional[AssetClass] = None,
        account_id: Optional[str] = None,
        profile_id: Optional[str] = None,
        session_id: str = "",
        seen_keys: set[str] | None = None,
        cooldown_deadlines: dict[str, int] | None = None,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
        max_holding_seconds: Optional[int] = None,
        order_type: str = "limit",
        time_in_force: str = "day",
        runtime_state: Optional[AutomationRuntimeState] = None,
    ) -> AutomationCycleResult:
        """Run one automation cycle: validate mandate, execute signals.

        Fail-closed: if the mandate is missing, expired, or lacks
        automation_bounds, the cycle is rejected entirely.
        """
        # 1. Load and validate mandate.
        mandate = self._load_mandate(broker, account_id)
        if mandate is None:
            return AutomationCycleResult(
                broker=broker, mode=policy.mode.value,
                signals_evaluated=len(signals), orders_placed=0,
                orders_rejected=len(signals), errors=0,
                results=({"error": "no valid mandate on file"},),
            )

        # 2. Check mandate has automation_bounds (v1 mandates cannot auto-trade).
        if mandate.automation_bounds is None:
            return AutomationCycleResult(
                broker=broker, mode=policy.mode.value,
                signals_evaluated=len(signals), orders_placed=0,
                orders_rejected=len(signals), errors=0,
                results=({"error": "mandate lacks automation_bounds; v1 mandates cannot enable automation"},),
            )

        # 3. Check policy mode is not DISABLED.
        if policy.mode is AutomationMode.DISABLED:
            return AutomationCycleResult(
                broker=broker, mode=policy.mode.value,
                signals_evaluated=len(signals), orders_placed=0,
                orders_rejected=len(signals), errors=0,
                results=({"error": "automation mode is disabled"},),
            )

        # 4. Fail-closed: if runtime_state is provided but unavailable, block.
        if runtime_state is not None and not runtime_state.available:
            return AutomationCycleResult(
                broker=broker, mode=policy.mode.value,
                signals_evaluated=len(signals), orders_placed=0,
                orders_rejected=len(signals), errors=1,
                results=({"error": f"runtime state unavailable: {runtime_state.error}"},),
            )

        # 5. Build executor kwargs.
        executor_kwargs: dict[str, Any] = {}
        if self._now_fn is not None:
            executor_kwargs["now_fn"] = self._now_fn

        executor = AutomationExecutor(
            policy=policy,
            place_order_fn=self._place_order,
            profile_id=profile_id,
            session_id=session_id,
            **executor_kwargs,
        )

        # 6. Build pre-submit bounds checker (only when runtime_state is available).
        pre_submit_check = None
        if runtime_state is not None and mandate.automation_bounds is not None:
            pre_submit_check = _make_bounds_checker(mandate.automation_bounds, runtime_state)

        # 7. Run the executor.
        exec_results = executor.run_cycle(
            signals,
            instrument_type=instrument_type,
            seen_keys=seen_keys,
            cooldown_deadlines=cooldown_deadlines,
            exit_protection_available=(stop_loss is not None or not mandate.automation_bounds.require_stop_loss),
            stop_loss=stop_loss,
            take_profit=take_profit,
            max_holding_seconds=max_holding_seconds,
            asset_class=asset_class,
            order_type=order_type,
            time_in_force=time_in_force,
            pre_submit_check=pre_submit_check,
        )

        # 8. Summarize.
        placed = sum(1 for r in exec_results if r.placed)
        rejected = sum(1 for r in exec_results if not r.placed and r.error is None)
        errors = sum(1 for r in exec_results if r.error is not None)

        return AutomationCycleResult(
            broker=broker,
            mode=policy.mode.value,
            signals_evaluated=len(signals),
            orders_placed=placed,
            orders_rejected=rejected,
            errors=errors,
            results=tuple(_result_to_dict(r) for r in exec_results),
        )


def _estimate_notional(plan: TradePlan) -> float:
    """Estimate order notional from a TradePlan (quantity * limit_price or notional)."""
    if plan.notional is not None:
        return plan.notional
    if plan.quantity is not None:
        price = plan.limit_price or 0.0
        return abs(plan.quantity * price)
    return 0.0


def _make_bounds_checker(
    bounds: AutomationBounds,
    runtime_state: AutomationRuntimeState,
) -> Callable[[TradePlan], bool]:
    """Build a pre_submit_check callback that evaluates automation bounds.

    Returns True if the plan passes all bounds, False otherwise (fail-closed).
    """

    def check(plan: TradePlan) -> bool:
        verdict = check_automation_bounds(
            bounds,
            plan,
            daily_turnover_usd=runtime_state.daily_turnover_usd,
            order_notional_usd=_estimate_notional(plan),
            symbol_exposure_usd=runtime_state.symbol_exposure(plan.symbol),
            open_position_count=runtime_state.open_position_count,
            open_order_count=runtime_state.open_order_count,
            daily_realized_loss_usd=runtime_state.daily_realized_loss_usd,
            drawdown_pct=runtime_state.drawdown_pct,
            has_existing_long=runtime_state.has_long(plan.symbol),
        )
        if not verdict.allowed:
            logger.info(
                "automation bounds check rejected %s: %s — %s",
                plan.symbol, verdict.limit, verdict.reason,
            )
            return False

        # Slippage check (only when bounds.max_slippage_pct is configured).
        slip_verdict = check_slippage(bounds, plan, plan.signal)
        if not slip_verdict.allowed:
            logger.info(
                "automation slippage check rejected %s: %s — %s",
                plan.symbol, slip_verdict.limit, slip_verdict.reason,
            )
            return False

        return True

    return check


def _result_to_dict(result: ExecutionResult) -> dict[str, Any]:
    """Convert an ExecutionResult to a JSON-serializable dict."""
    d: dict[str, Any] = {
        "symbol": result.signal.symbol,
        "strategy_id": result.signal.strategy_id,
        "score": result.signal.score,
        "decision": result.decision.outcome.value,
        "reason": result.decision.reason,
        "placed": result.placed,
    }
    if result.plan is not None:
        d["plan"] = {
            "side": result.plan.side,
            "quantity": result.plan.quantity,
            "notional": result.plan.notional,
            "order_type": result.plan.order_type,
            "limit_price": result.plan.limit_price,
            "client_order_id": result.plan.client_order_id,
        }
    if result.order_response is not None:
        d["order_response"] = dict(result.order_response)
    if result.error is not None:
        d["error"] = result.error
    return d
