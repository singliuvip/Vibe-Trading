"""Tests for the DecisionGate (pure, fail-closed signal evaluation)."""

import pytest

from src.live.automation.decision_gate import evaluate_signal
from src.live.automation.models import (
    DecisionOutcome,
    SignalCandidate,
    SignalDirection,
)
from src.live.automation.policy import AutomationMode, AutomationPolicy

# generated_at "2026-07-24T09:25:00Z" == 1784885100000 epoch ms.
GENERATED_MS = 1_784_885_100_000
# now 60s after generation → age 60s, comfortably within the 300s default.
NOW_MS = GENERATED_MS + 60_000


def _signal(**overrides) -> SignalCandidate:
    """Build a valid SignalCandidate with optional overrides."""
    defaults = dict(
        strategy_id="v17.13",
        strategy_version="abc123",
        symbol="688598.SH",
        direction=SignalDirection.LONG,
        score=0.85,
        generated_at="2026-07-24T09:25:00Z",
        idempotency_key="v17.13:688598.SH:1784885100000",
        price_ref=27.55,
        target_quantity=1000.0,
    )
    defaults.update(overrides)
    return SignalCandidate(**defaults)


def _policy(**overrides) -> AutomationPolicy:
    """Build an enabled policy with optional overrides."""
    defaults = dict(
        mode=AutomationMode.PAPER_AUTO,
        min_signal_score=0.7,
        max_signal_age_seconds=300,
        require_exit_protection=True,
        allowed_strategies=(),
        max_orders_per_cycle=5,
        symbol_cooldown_seconds=3600,
    )
    defaults.update(overrides)
    return AutomationPolicy(**defaults)


# --------------------------------------------------------------------------- #
# 1. Mode gate                                                                #
# --------------------------------------------------------------------------- #


def test_disabled_mode_rejects_all():
    decision = evaluate_signal(
        _signal(),
        _policy(mode=AutomationMode.DISABLED),
        now_ms=NOW_MS,
        exit_protection_available=True,
    )
    assert decision.outcome is DecisionOutcome.REJECTED_POLICY
    assert not decision.is_allowed


# --------------------------------------------------------------------------- #
# 2. Strategy whitelist                                                       #
# --------------------------------------------------------------------------- #


def test_strategy_not_in_whitelist_rejected():
    decision = evaluate_signal(
        _signal(strategy_id="other"),
        _policy(allowed_strategies=("v17.13",)),
        now_ms=NOW_MS,
        exit_protection_available=True,
    )
    assert decision.outcome is DecisionOutcome.REJECTED_POLICY


def test_empty_whitelist_allows_all_strategies():
    decision = evaluate_signal(
        _signal(strategy_id="anything"),
        _policy(allowed_strategies=()),
        now_ms=NOW_MS,
        exit_protection_available=True,
    )
    assert decision.outcome is DecisionOutcome.ALLOWED


def test_strategy_in_whitelist_allowed():
    decision = evaluate_signal(
        _signal(strategy_id="v17.13"),
        _policy(allowed_strategies=("v17.13", "v18")),
        now_ms=NOW_MS,
        exit_protection_available=True,
    )
    assert decision.outcome is DecisionOutcome.ALLOWED


# --------------------------------------------------------------------------- #
# 3. Score threshold                                                          #
# --------------------------------------------------------------------------- #


def test_score_below_threshold_rejected():
    decision = evaluate_signal(
        _signal(score=0.5),
        _policy(min_signal_score=0.7),
        now_ms=NOW_MS,
        exit_protection_available=True,
    )
    assert decision.outcome is DecisionOutcome.REJECTED_SCORE


def test_score_at_threshold_allowed():
    decision = evaluate_signal(
        _signal(score=0.7),
        _policy(min_signal_score=0.7),
        now_ms=NOW_MS,
        exit_protection_available=True,
    )
    assert decision.outcome is DecisionOutcome.ALLOWED


# --------------------------------------------------------------------------- #
# 4. Staleness                                                                #
# --------------------------------------------------------------------------- #


def test_stale_signal_rejected():
    # age 400s > 300s max.
    decision = evaluate_signal(
        _signal(),
        _policy(max_signal_age_seconds=300),
        now_ms=GENERATED_MS + 400_000,
        exit_protection_available=True,
    )
    assert decision.outcome is DecisionOutcome.REJECTED_STALE


def test_unparseable_generated_at_rejected_fail_closed():
    decision = evaluate_signal(
        _signal(generated_at="not-a-timestamp"),
        _policy(),
        now_ms=NOW_MS,
        exit_protection_available=True,
    )
    assert decision.outcome is DecisionOutcome.REJECTED_STALE


def test_future_generated_at_rejected_fail_closed():
    # now is before generation → negative age → anomalous.
    decision = evaluate_signal(
        _signal(),
        _policy(),
        now_ms=GENERATED_MS - 10_000,
        exit_protection_available=True,
    )
    assert decision.outcome is DecisionOutcome.REJECTED_STALE


# --------------------------------------------------------------------------- #
# 5. Idempotency                                                              #
# --------------------------------------------------------------------------- #


def test_duplicate_idempotency_key_rejected():
    signal = _signal()
    decision = evaluate_signal(
        signal,
        _policy(),
        now_ms=NOW_MS,
        seen_keys={signal.idempotency_key},
        exit_protection_available=True,
    )
    assert decision.outcome is DecisionOutcome.REJECTED_DUPLICATE


# --------------------------------------------------------------------------- #
# 6. Per-symbol cooldown                                                      #
# --------------------------------------------------------------------------- #


def test_symbol_in_cooldown_rejected():
    decision = evaluate_signal(
        _signal(),
        _policy(),
        now_ms=NOW_MS,
        cooldown_deadlines={"688598.SH": NOW_MS + 100_000},
        exit_protection_available=True,
    )
    assert decision.outcome is DecisionOutcome.REJECTED_COOLDOWN


def test_symbol_past_cooldown_allowed():
    decision = evaluate_signal(
        _signal(),
        _policy(),
        now_ms=NOW_MS,
        cooldown_deadlines={"688598.SH": NOW_MS - 1},
        exit_protection_available=True,
    )
    assert decision.outcome is DecisionOutcome.ALLOWED


# --------------------------------------------------------------------------- #
# 7. Exit protection                                                          #
# --------------------------------------------------------------------------- #


def test_exit_protection_required_but_unavailable_rejected():
    decision = evaluate_signal(
        _signal(),
        _policy(require_exit_protection=True),
        now_ms=NOW_MS,
        exit_protection_available=False,
    )
    assert decision.outcome is DecisionOutcome.REJECTED_NO_EXIT


def test_exit_protection_not_required_allows_without_it():
    decision = evaluate_signal(
        _signal(),
        _policy(require_exit_protection=False),
        now_ms=NOW_MS,
        exit_protection_available=False,
    )
    assert decision.outcome is DecisionOutcome.ALLOWED


# --------------------------------------------------------------------------- #
# All conditions satisfied                                                    #
# --------------------------------------------------------------------------- #


def test_all_conditions_met_allowed():
    decision = evaluate_signal(
        _signal(),
        _policy(),
        now_ms=NOW_MS,
        exit_protection_available=True,
    )
    assert decision.outcome is DecisionOutcome.ALLOWED
    assert decision.is_allowed
    assert decision.signal.symbol == "688598.SH"


def test_check_order_policy_before_score():
    """Disabled mode wins even if the score would also fail (first failure wins)."""
    decision = evaluate_signal(
        _signal(score=0.1),
        _policy(mode=AutomationMode.DISABLED, min_signal_score=0.7),
        now_ms=NOW_MS,
        exit_protection_available=True,
    )
    assert decision.outcome is DecisionOutcome.REJECTED_POLICY
