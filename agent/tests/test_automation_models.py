"""Tests for live-trading automation structured contracts."""

import pytest

from src.live.automation.models import (
    DecisionOutcome,
    ExecutionDecision,
    SignalCandidate,
    SignalDirection,
    TradePlan,
)
from src.live.mandate.model import AssetClass, InstrumentType


# --------------------------------------------------------------------------- #
# SignalCandidate                                                             #
# --------------------------------------------------------------------------- #


def _signal(**overrides) -> SignalCandidate:
    """Build a valid SignalCandidate with optional overrides."""
    defaults = dict(
        strategy_id="v17.13",
        strategy_version="abc123",
        symbol="688598.SH",
        direction=SignalDirection.LONG,
        score=0.85,
        generated_at="2026-07-24T09:25:00Z",
        idempotency_key="v17.13:688598.SH:1753345500000",
        price_ref=27.55,
        target_quantity=1000.0,
    )
    defaults.update(overrides)
    return SignalCandidate(**defaults)


def test_signal_valid_construction():
    s = _signal()
    assert s.strategy_id == "v17.13"
    assert s.symbol == "688598.SH"
    assert s.score == 0.85
    assert s.direction is SignalDirection.LONG


def test_signal_rejects_empty_strategy_id():
    with pytest.raises(ValueError, match="strategy_id"):
        _signal(strategy_id="")


def test_signal_rejects_empty_symbol():
    with pytest.raises(ValueError, match="symbol"):
        _signal(symbol="")


def test_signal_rejects_score_out_of_range():
    with pytest.raises(ValueError, match="score"):
        _signal(score=1.5)
    with pytest.raises(ValueError, match="score"):
        _signal(score=-0.1)


def test_signal_rejects_both_quantity_and_notional():
    with pytest.raises(ValueError, match="exactly one"):
        _signal(target_quantity=100, target_notional=5000)


def test_signal_allows_neither_quantity_nor_notional():
    s = _signal(target_quantity=None, target_notional=None)
    assert s.target_quantity is None
    assert s.target_notional is None


def test_signal_is_frozen():
    s = _signal()
    with pytest.raises(AttributeError):
        s.score = 0.9  # type: ignore


# --------------------------------------------------------------------------- #
# ExecutionDecision                                                           #
# --------------------------------------------------------------------------- #


def test_decision_allowed():
    s = _signal()
    d = ExecutionDecision(signal=s, outcome=DecisionOutcome.ALLOWED)
    assert d.is_allowed is True
    assert d.signal is s


def test_decision_rejected():
    s = _signal()
    d = ExecutionDecision(
        signal=s,
        outcome=DecisionOutcome.REJECTED_SCORE,
        reason="score 0.3 < min 0.7",
    )
    assert d.is_allowed is False
    assert "score" in d.reason


def test_decision_has_timestamp():
    s = _signal()
    d = ExecutionDecision(signal=s, outcome=DecisionOutcome.ALLOWED)
    assert d.decided_at  # non-empty ISO string


# --------------------------------------------------------------------------- #
# TradePlan                                                                   #
# --------------------------------------------------------------------------- #


def _plan(**overrides) -> TradePlan:
    """Build a valid TradePlan with optional overrides."""
    s = _signal()
    d = ExecutionDecision(signal=s, outcome=DecisionOutcome.ALLOWED)
    defaults = dict(
        decision=d,
        symbol="688598.SH",
        side="buy",
        instrument_type=InstrumentType.EQUITY,
        quantity=1000.0,
        order_type="limit",
        limit_price=27.55,
        client_order_id="auto-v17.13:688598.SH:1753345500000-1753345500000",
    )
    defaults.update(overrides)
    return TradePlan(**defaults)


def test_plan_valid_construction():
    p = _plan()
    assert p.symbol == "688598.SH"
    assert p.side == "buy"
    assert p.quantity == 1000.0
    assert p.limit_price == 27.55
    assert p.signal.strategy_id == "v17.13"


def test_plan_rejects_invalid_side():
    with pytest.raises(ValueError, match="side"):
        _plan(side="hold")


def test_plan_rejects_limit_without_price():
    with pytest.raises(ValueError, match="limit_price"):
        _plan(order_type="limit", limit_price=None)


def test_plan_rejects_both_quantity_and_notional():
    with pytest.raises(ValueError, match="exactly one"):
        _plan(quantity=100, notional=5000)


def test_plan_rejects_neither_quantity_nor_notional():
    with pytest.raises(ValueError, match="one of"):
        _plan(quantity=None, notional=None)


def test_plan_rejects_zero_quantity():
    with pytest.raises(ValueError, match="quantity"):
        _plan(quantity=0)


def test_plan_rejects_negative_notional():
    with pytest.raises(ValueError, match="notional"):
        _plan(quantity=None, notional=-100)


def test_plan_market_order_no_limit_price():
    p = _plan(order_type="market", limit_price=None)
    assert p.order_type == "market"
    assert p.limit_price is None


def test_plan_is_frozen():
    p = _plan()
    with pytest.raises(AttributeError):
        p.symbol = "AAPL"  # type: ignore


def test_plan_with_exit_protection():
    p = _plan(stop_loss=25.0, take_profit=32.0, max_holding_seconds=86400 * 5)
    assert p.stop_loss == 25.0
    assert p.take_profit == 32.0
    assert p.max_holding_seconds == 432000


# --------------------------------------------------------------------------- #
# TradePlan — new constraint tests (step-4 review fixes)                      #
# --------------------------------------------------------------------------- #


def test_plan_rejects_rejected_decision():
    """A TradePlan cannot be built from a rejected ExecutionDecision."""
    s = _signal()
    d = ExecutionDecision(signal=s, outcome=DecisionOutcome.REJECTED_SCORE, reason="too low")
    with pytest.raises(ValueError, match="allowed"):
        TradePlan(
            decision=d,
            symbol="688598.SH",
            side="buy",
            instrument_type=InstrumentType.EQUITY,
            quantity=1000.0,
            client_order_id="auto-x",
        )


def test_plan_rejects_nonpositive_limit_price():
    with pytest.raises(ValueError, match="limit_price"):
        _plan(limit_price=0)
    with pytest.raises(ValueError, match="limit_price"):
        _plan(limit_price=-1.0)


def test_plan_rejects_nonpositive_stop_loss():
    with pytest.raises(ValueError, match="stop_loss"):
        _plan(stop_loss=0)


def test_plan_rejects_nonpositive_take_profit():
    with pytest.raises(ValueError, match="take_profit"):
        _plan(take_profit=-5.0)


def test_plan_rejects_nonpositive_max_holding():
    with pytest.raises(ValueError, match="max_holding_seconds"):
        _plan(max_holding_seconds=0)


def test_plan_rejects_empty_client_order_id():
    with pytest.raises(ValueError, match="client_order_id"):
        _plan(client_order_id="")


def test_plan_carries_instrument_type_and_asset_class():
    p = _plan(instrument_type=InstrumentType.EQUITY, asset_class=AssetClass.CN_EQUITY)
    assert p.instrument_type is InstrumentType.EQUITY
    assert p.asset_class is AssetClass.CN_EQUITY


def test_plan_asset_class_defaults_none():
    p = _plan()
    assert p.asset_class is None


# --------------------------------------------------------------------------- #
# SignalCandidate — target sizing positivity (step-4 review fix)             #
# --------------------------------------------------------------------------- #


def test_signal_rejects_nonpositive_target_quantity():
    with pytest.raises(ValueError, match="target_quantity"):
        _signal(target_quantity=0)


def test_signal_rejects_nonpositive_target_notional():
    with pytest.raises(ValueError, match="target_notional"):
        _signal(target_quantity=None, target_notional=-100)


def test_signal_rejects_empty_strategy_version():
    with pytest.raises(ValueError, match="strategy_version"):
        _signal(strategy_version="")
