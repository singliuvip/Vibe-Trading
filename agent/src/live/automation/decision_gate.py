"""DecisionGate — deterministic signal evaluation (pure, fail-closed).

The gate evaluates a SignalCandidate against an AutomationPolicy and produces
an ExecutionDecision. It is a pure function: all time/state is passed in as
arguments (now_ms, seen idempotency keys, per-symbol cooldown deadlines), so it
is fully unit-testable without a clock or storage.

Check order (first failure wins, fail-closed):
    1. policy mode != DISABLED
    2. strategy whitelist
    3. signal score >= min
    4. signal age <= max (staleness)
    5. idempotency (duplicate key)
    6. per-symbol cooldown
    7. exit protection (when required)
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import AbstractSet, Mapping

from src.live.automation.models import (
    DecisionOutcome,
    ExecutionDecision,
    SignalCandidate,
)
from src.live.automation.policy import AutomationMode, AutomationPolicy


def _parse_generated_at_epoch_ms(generated_at: str) -> int | None:
    """Parse an ISO-8601 timestamp to epoch ms; None if unparseable (fail-closed)."""
    if not generated_at:
        return None
    normalized = generated_at.strip()
    if normalized.endswith(("Z", "z")):
        normalized = normalized[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.timestamp() * 1000)


def evaluate_signal(
    signal: SignalCandidate,
    policy: AutomationPolicy,
    *,
    now_ms: int,
    seen_keys: AbstractSet[str] = frozenset(),
    cooldown_deadlines: Mapping[str, int] | None = None,
    exit_protection_available: bool = False,
) -> ExecutionDecision:
    """Evaluate one signal against the policy; return the decision (fail-closed).

    Args:
        signal: The candidate signal.
        policy: The active automation policy.
        now_ms: Current wall-clock time in epoch ms (injected for determinism).
        seen_keys: Set of idempotency keys already acted upon (duplicate check).
        cooldown_deadlines: Mapping of symbol -> epoch-ms deadline before which
            a new order on that symbol is rejected.
        exit_protection_available: Whether the caller can supply stop-loss /
            take-profit / max-holding for this signal's TradePlan. When the
            policy requires exit protection and this is False, reject.

    Returns:
        An ExecutionDecision (allowed or a specific rejection reason).
    """
    cooldown_deadlines = cooldown_deadlines or {}

    # 1. Mode gate.
    if policy.mode is AutomationMode.DISABLED:
        return ExecutionDecision(
            signal=signal,
            outcome=DecisionOutcome.REJECTED_POLICY,
            reason="automation mode is disabled",
        )

    # 2. Strategy whitelist.
    if policy.allowed_strategies and signal.strategy_id not in policy.allowed_strategies:
        return ExecutionDecision(
            signal=signal,
            outcome=DecisionOutcome.REJECTED_POLICY,
            reason=f"strategy {signal.strategy_id!r} not in whitelist",
        )

    # 3. Score threshold.
    if signal.score < policy.min_signal_score:
        return ExecutionDecision(
            signal=signal,
            outcome=DecisionOutcome.REJECTED_SCORE,
            reason=f"score {signal.score} < min {policy.min_signal_score}",
        )

    # 4. Staleness (fail-closed on unparseable timestamp).
    generated_ms = _parse_generated_at_epoch_ms(signal.generated_at)
    if generated_ms is None:
        return ExecutionDecision(
            signal=signal,
            outcome=DecisionOutcome.REJECTED_STALE,
            reason="unparseable generated_at timestamp (fail-closed)",
        )
    age_s = (now_ms - generated_ms) / 1000.0
    if age_s > policy.max_signal_age_seconds:
        return ExecutionDecision(
            signal=signal,
            outcome=DecisionOutcome.REJECTED_STALE,
            reason=f"signal age {age_s:.0f}s > max {policy.max_signal_age_seconds}s",
        )
    if age_s < 0:
        # Future-dated signal is anomalous — fail-closed.
        return ExecutionDecision(
            signal=signal,
            outcome=DecisionOutcome.REJECTED_STALE,
            reason="signal generated_at is in the future (fail-closed)",
        )

    # 5. Idempotency.
    if signal.idempotency_key in seen_keys:
        return ExecutionDecision(
            signal=signal,
            outcome=DecisionOutcome.REJECTED_DUPLICATE,
            reason=f"idempotency key {signal.idempotency_key!r} already acted upon",
        )

    # 6. Per-symbol cooldown.
    deadline = cooldown_deadlines.get(signal.symbol)
    if deadline is not None and now_ms < deadline:
        return ExecutionDecision(
            signal=signal,
            outcome=DecisionOutcome.REJECTED_COOLDOWN,
            reason=f"symbol {signal.symbol!r} in cooldown until {deadline}",
        )

    # 7. Exit protection.
    if policy.require_exit_protection and not exit_protection_available:
        return ExecutionDecision(
            signal=signal,
            outcome=DecisionOutcome.REJECTED_NO_EXIT,
            reason="policy requires exit protection but none is available",
        )

    return ExecutionDecision(signal=signal, outcome=DecisionOutcome.ALLOWED)
