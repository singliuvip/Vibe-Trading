"""Automation policy — the user-authorized bounds for automated execution.

Frozen dataclass (immutability convention). This is the in-memory policy
contract; durable storage/loading is a later step. The DecisionGate evaluates
signals against this policy.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class AutomationMode(str, Enum):
    """User-authorized automation mode."""

    DISABLED = "disabled"  # scan + notify only, never trade
    PAPER_AUTO = "paper_auto"  # auto paper trading
    LIVE_BOUNDED = "live_bounded"  # auto live within mandate + policy


@dataclass(frozen=True)
class AutomationPolicy:
    """Deterministic bounds the DecisionGate enforces on every signal.

    Attributes:
        mode: Automation mode. DISABLED rejects all signals.
        min_signal_score: Minimum signal score [0,1] to allow.
        max_signal_age_seconds: Max age of a signal (now - generated_at) before
            it is considered stale and rejected.
        require_exit_protection: When True, a signal must carry enough info to
            build a TradePlan with stop_loss/take_profit/max_holding (enforced
            by the caller supplying them); the gate rejects signals whose
            strategy is not whitelisted for exit handling.
        allowed_strategies: Whitelist of strategy_id permitted to auto-trade.
            Empty tuple == all strategies allowed.
        max_orders_per_cycle: Max orders the executor may place in one cycle.
        symbol_cooldown_seconds: Min seconds between two auto orders on the
            same symbol.
    """

    mode: AutomationMode = AutomationMode.DISABLED
    min_signal_score: float = 0.7
    max_signal_age_seconds: int = 300
    require_exit_protection: bool = True
    allowed_strategies: tuple[str, ...] = field(default_factory=tuple)
    max_orders_per_cycle: int = 5
    symbol_cooldown_seconds: int = 3600

    def __post_init__(self) -> None:
        if not (0.0 <= self.min_signal_score <= 1.0):
            raise ValueError(f"min_signal_score must be in [0,1], got {self.min_signal_score}")
        if self.max_signal_age_seconds <= 0:
            raise ValueError("max_signal_age_seconds must be > 0")
        if self.max_orders_per_cycle <= 0:
            raise ValueError("max_orders_per_cycle must be > 0")
        if self.symbol_cooldown_seconds < 0:
            raise ValueError("symbol_cooldown_seconds must be >= 0")
