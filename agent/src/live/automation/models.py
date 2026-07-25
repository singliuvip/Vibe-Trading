"""Structured data contracts for the automated trading pipeline.

All models are frozen dataclasses — immutable once constructed. This follows
the project's convention for safety-critical data (see Mandate, OrderIntent).
No Pydantic: plain frozen dataclasses give the strongest immutability guarantee
with zero validation surface the agent could exploit.

Data flow:
    SignalProvider → SignalCandidate
    DecisionGate(SignalCandidate) → ExecutionDecision
    ExecutionDecision(allowed) → TradePlan
    TradePlan → OrderIntent (existing enforcement layer)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from src.live.mandate.model import AssetClass, InstrumentType


def _utc_now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


class SignalDirection(str, Enum):
    """Direction of a trading signal."""

    LONG = "long"
    SHORT = "short"
    CLOSE = "close"  # exit existing position


class DecisionOutcome(str, Enum):
    """Outcome of the decision gate evaluation."""

    ALLOWED = "allowed"
    REJECTED_SCORE = "rejected_score"  # signal score below threshold
    REJECTED_STALE = "rejected_stale"  # signal too old
    REJECTED_COOLDOWN = "rejected_cooldown"  # symbol in cooldown period
    REJECTED_DUPLICATE = "rejected_duplicate"  # idempotency key collision
    REJECTED_NO_EXIT = "rejected_no_exit"  # no exit protection defined
    REJECTED_POLICY = "rejected_policy"  # violates automation policy
    REJECTED_MANDATE = "rejected_mandate"  # violates mandate limits


@dataclass(frozen=True)
class SignalCandidate:
    """A structured trading signal from a verified strategy engine.

    This is the input to the DecisionGate. It must be produced by a
    deterministic strategy engine (not free-text LLM output).

    Attributes:
        strategy_id: Unique identifier of the strategy that produced this signal.
        strategy_version: Version hash of the strategy code/parameters.
        symbol: Normalized upper-case symbol (e.g. "688598.SH", "AAPL").
        direction: Signal direction (long/short/close).
        score: Signal confidence score [0.0, 1.0]. Higher = stronger signal.
        generated_at: ISO-8601 UTC timestamp when the signal was generated.
        idempotency_key: Unique key for deduplication. Convention:
            "{strategy_id}:{symbol}:{generated_at_epoch_ms}".
        metadata: Optional strategy-specific context (e.g. indicator values,
            pattern name). Must be JSON-serializable. Never used for order
            parameters — those come from TradePlan.
        price_ref: Reference price at signal generation (e.g. close price).
            Used for slippage calculation. None if not available.
        target_quantity: Suggested order quantity from the strategy.
            The DecisionGate/Policy may override this.
        target_notional: Suggested order notional (account currency).
            Exactly one of target_quantity/target_notional should be set.
    """

    strategy_id: str
    strategy_version: str
    symbol: str
    direction: SignalDirection
    score: float
    generated_at: str
    idempotency_key: str
    metadata: dict = field(default_factory=dict)
    price_ref: Optional[float] = None
    target_quantity: Optional[float] = None
    target_notional: Optional[float] = None

    def __post_init__(self) -> None:
        """Validate invariants on construction (frozen = no mutation after)."""
        if not self.strategy_id:
            raise ValueError("strategy_id must be non-empty")
        if not self.strategy_version:
            raise ValueError("strategy_version must be non-empty")
        if not self.symbol:
            raise ValueError("symbol must be non-empty")
        if not (0.0 <= self.score <= 1.0):
            raise ValueError(f"score must be in [0.0, 1.0], got {self.score}")
        if not self.idempotency_key:
            raise ValueError("idempotency_key must be non-empty")
        if not self.generated_at:
            raise ValueError("generated_at must be non-empty")
        if self.target_quantity is not None and self.target_notional is not None:
            raise ValueError("exactly one of target_quantity/target_notional may be set")
        if self.target_quantity is not None and self.target_quantity <= 0:
            raise ValueError(f"target_quantity must be > 0, got {self.target_quantity}")
        if self.target_notional is not None and self.target_notional <= 0:
            raise ValueError(f"target_notional must be > 0, got {self.target_notional}")


@dataclass(frozen=True)
class ExecutionDecision:
    """The DecisionGate's verdict on a SignalCandidate.

    Every signal gets exactly one decision, recorded for audit. A rejected
    signal never produces a TradePlan.

    Attributes:
        signal: The original signal that was evaluated.
        outcome: The decision outcome (allowed or specific rejection reason).
        decided_at: ISO-8601 UTC timestamp of the decision.
        reason: Human-readable explanation (especially for rejections).
        policy_ref: Reference to the automation policy used for evaluation.
        mandate_snapshot_ref: Reference to the mandate snapshot at decision time.
    """

    signal: SignalCandidate
    outcome: DecisionOutcome
    decided_at: str = field(default_factory=_utc_now_iso)
    reason: str = ""
    policy_ref: str = ""
    mandate_snapshot_ref: str = ""

    @property
    def is_allowed(self) -> bool:
        """Return whether the signal was allowed to proceed."""
        return self.outcome is DecisionOutcome.ALLOWED


@dataclass(frozen=True)
class TradePlan:
    """A concrete order plan derived from an allowed signal + policy.

    This is the output of the DecisionGate (when allowed) and the input to
    the trading service layer (Mandate Gate → Broker Connector). It contains
    ALL order parameters — the broker connector never infers anything.

    Attributes:
        decision: The execution decision that produced this plan. Must be
            allowed (``decision.is_allowed is True``).
        symbol: Normalized symbol (from signal).
        side: "buy" or "sell" (derived from signal direction).
        instrument_type: Mapped :class:`~src.live.mandate.model.InstrumentType`,
            carried explicitly so the TradePlan → OrderIntent conversion needs
            no inference.
        quantity: Order quantity in units/shares/contracts. Exactly one of
            quantity/notional is set.
        notional: Order notional in account currency. Exactly one of
            quantity/notional is set.
        order_type: "market" or "limit".
        limit_price: Required when order_type is "limit". None for market.
            Must be > 0 when set.
        time_in_force: "day" or "gtc".
        stop_loss: Stop-loss price (None if not required by policy). Must be
            > 0 when set.
        take_profit: Take-profit price (None if not required by policy). Must
            be > 0 when set.
        max_holding_seconds: Maximum holding period before forced exit.
            None if no time-based exit is required. Must be > 0 when set.
        asset_class: Explicit universe :class:`~src.live.mandate.model.AssetClass`
            (e.g. CN_EQUITY for A-shares). Optional; when None the gate falls
            back to the instrument-type default.
        client_order_id: Unique client-side order ID for idempotency with
            the broker. Required (non-empty). Convention:
            "auto-{idempotency_key}-{timestamp_ms}".
        created_at: ISO-8601 UTC timestamp of plan creation.
    """

    decision: ExecutionDecision
    symbol: str
    side: str
    instrument_type: InstrumentType
    quantity: Optional[float] = None
    notional: Optional[float] = None
    order_type: str = "market"
    limit_price: Optional[float] = None
    time_in_force: str = "day"
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    max_holding_seconds: Optional[int] = None
    asset_class: Optional[AssetClass] = None
    client_order_id: str = ""
    created_at: str = field(default_factory=_utc_now_iso)

    def __post_init__(self) -> None:
        """Validate invariants."""
        if not self.decision.is_allowed:
            raise ValueError("TradePlan requires an allowed ExecutionDecision")
        if not self.symbol:
            raise ValueError("symbol must be non-empty")
        if self.side not in ("buy", "sell"):
            raise ValueError(f"side must be 'buy' or 'sell', got {self.side!r}")
        if self.order_type not in ("market", "limit"):
            raise ValueError(f"order_type must be 'market' or 'limit', got {self.order_type!r}")
        if self.order_type == "limit" and self.limit_price is None:
            raise ValueError("limit_price is required for limit orders")
        if self.limit_price is not None and self.limit_price <= 0:
            raise ValueError(f"limit_price must be > 0, got {self.limit_price}")
        if self.stop_loss is not None and self.stop_loss <= 0:
            raise ValueError(f"stop_loss must be > 0, got {self.stop_loss}")
        if self.take_profit is not None and self.take_profit <= 0:
            raise ValueError(f"take_profit must be > 0, got {self.take_profit}")
        if self.max_holding_seconds is not None and self.max_holding_seconds <= 0:
            raise ValueError(f"max_holding_seconds must be > 0, got {self.max_holding_seconds}")
        if self.time_in_force not in ("day", "gtc"):
            raise ValueError(f"time_in_force must be 'day' or 'gtc', got {self.time_in_force!r}")
        if self.quantity is not None and self.notional is not None:
            raise ValueError("exactly one of quantity/notional may be set")
        if self.quantity is None and self.notional is None:
            raise ValueError("one of quantity/notional must be set")
        if self.quantity is not None and self.quantity <= 0:
            raise ValueError(f"quantity must be > 0, got {self.quantity}")
        if self.notional is not None and self.notional <= 0:
            raise ValueError(f"notional must be > 0, got {self.notional}")
        if not self.client_order_id:
            raise ValueError("client_order_id must be non-empty")

    @property
    def signal(self) -> SignalCandidate:
        """Convenience accessor for the originating signal."""
        return self.decision.signal
