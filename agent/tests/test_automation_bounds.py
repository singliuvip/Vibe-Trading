"""Tests for automation-specific mandate bound checks (bounds_check.py).

These checks layer ON TOP of the shared check_mandate gate and are only
consulted by the automated execution pipeline. Every check is fail-closed
and pure: all state is passed in as arguments.
"""

from __future__ import annotations

import pytest

from src.live.automation.bounds_check import (
    BoundsVerdict,
    check_automation_bounds,
    check_slippage,
)
from src.live.automation.models import (
    DecisionOutcome,
    ExecutionDecision,
    SignalCandidate,
    SignalDirection,
    TradePlan,
)
from src.live.mandate.model import AutomationBounds, InstrumentType


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
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


def _plan(**overrides) -> TradePlan:
    """Build a valid TradePlan with optional overrides."""
    signal = _signal()
    decision = ExecutionDecision(signal=signal, outcome=DecisionOutcome.ALLOWED)
    defaults = dict(
        decision=decision,
        symbol="688598.SH",
        side="buy",
        instrument_type=InstrumentType.EQUITY,
        quantity=1000.0,
        order_type="market",
        client_order_id="auto-test-001",
    )
    defaults.update(overrides)
    return TradePlan(**defaults)


def _bounds(**overrides) -> AutomationBounds:
    """Build a permissive AutomationBounds with optional overrides."""
    defaults = dict(
        allowed_order_types=("market", "limit"),
    )
    defaults.update(overrides)
    return AutomationBounds(**defaults)


def _check(bounds: AutomationBounds, plan: TradePlan, **state) -> BoundsVerdict:
    """Call check_automation_bounds with sane default state."""
    defaults = dict(
        daily_turnover_usd=0.0,
        order_notional_usd=1000.0,
        symbol_exposure_usd=0.0,
        open_position_count=0,
        open_order_count=0,
        daily_realized_loss_usd=0.0,
        drawdown_pct=0.0,
    )
    defaults.update(state)
    return check_automation_bounds(bounds, plan, **defaults)


# --------------------------------------------------------------------------- #
# AutomationBounds construction validation                                    #
# --------------------------------------------------------------------------- #


class TestAutomationBoundsConstruction:
    def test_empty_defaults(self):
        b = AutomationBounds()
        assert b.max_daily_turnover_usd is None
        assert b.allowed_order_types == ()
        assert b.require_stop_loss is False
        assert b.allow_short is False

    def test_valid_construction(self):
        b = _bounds(
            max_daily_turnover_usd=10000.0,
            max_positions=5,
            max_drawdown_pct=0.10,
            max_slippage_pct=0.02,
        )
        assert b.max_daily_turnover_usd == 10000.0
        assert b.max_positions == 5

    @pytest.mark.parametrize("field_name", [
        "max_daily_turnover_usd",
        "max_symbol_exposure_usd",
        "max_daily_realized_loss_usd",
    ])
    def test_rejects_nonpositive_usd(self, field_name):
        with pytest.raises(ValueError, match=field_name):
            AutomationBounds(**{field_name: 0.0})
        with pytest.raises(ValueError, match=field_name):
            AutomationBounds(**{field_name: -100.0})

    @pytest.mark.parametrize("field_name", ["max_positions", "max_open_orders"])
    def test_rejects_nonpositive_count(self, field_name):
        with pytest.raises(ValueError, match=field_name):
            AutomationBounds(**{field_name: 0})
        with pytest.raises(ValueError, match=field_name):
            AutomationBounds(**{field_name: -1})

    def test_rejects_drawdown_out_of_range(self):
        with pytest.raises(ValueError, match="max_drawdown_pct"):
            AutomationBounds(max_drawdown_pct=0.0)
        with pytest.raises(ValueError, match="max_drawdown_pct"):
            AutomationBounds(max_drawdown_pct=1.5)

    def test_rejects_slippage_out_of_range(self):
        with pytest.raises(ValueError, match="max_slippage_pct"):
            AutomationBounds(max_slippage_pct=0.0)
        with pytest.raises(ValueError, match="max_slippage_pct"):
            AutomationBounds(max_slippage_pct=2.0)

    def test_rejects_invalid_order_type(self):
        with pytest.raises(ValueError, match="allowed_order_types"):
            AutomationBounds(allowed_order_types=("stop",))

    def test_is_frozen(self):
        b = _bounds()
        with pytest.raises(AttributeError):
            b.max_positions = 5  # type: ignore


# --------------------------------------------------------------------------- #
# check_automation_bounds — limit paths                                       #
# --------------------------------------------------------------------------- #


class TestCheckAutomationBounds:
    def test_empty_order_types_denies(self):
        """Empty allowed_order_types == deny all automation orders (fail-closed)."""
        v = _check(AutomationBounds(), _plan())
        assert v.allowed is False
        assert v.limit == "allowed_order_types"

    def test_order_type_not_in_whitelist_denies(self):
        bounds = _bounds(allowed_order_types=("limit",))
        v = _check(bounds, _plan(order_type="market"))
        assert v.allowed is False
        assert v.limit == "allowed_order_types"

    def test_require_stop_loss_missing_denies(self):
        bounds = _bounds(require_stop_loss=True)
        v = _check(bounds, _plan(stop_loss=None))
        assert v.allowed is False
        assert v.limit == "require_stop_loss"

    def test_require_stop_loss_present_allows(self):
        bounds = _bounds(require_stop_loss=True)
        v = _check(bounds, _plan(order_type="limit", limit_price=27.5, stop_loss=26.0))
        assert v.allowed is True

    def test_daily_turnover_exceeded_denies(self):
        bounds = _bounds(max_daily_turnover_usd=5000.0)
        v = _check(bounds, _plan(), daily_turnover_usd=4500.0, order_notional_usd=1000.0)
        assert v.allowed is False
        assert v.limit == "max_daily_turnover_usd"

    def test_daily_turnover_within_cap_allows(self):
        bounds = _bounds(max_daily_turnover_usd=5000.0)
        v = _check(bounds, _plan(), daily_turnover_usd=3000.0, order_notional_usd=1000.0)
        assert v.allowed is True

    def test_symbol_exposure_exceeded_denies(self):
        bounds = _bounds(max_symbol_exposure_usd=10000.0)
        v = _check(bounds, _plan(), symbol_exposure_usd=12000.0)
        assert v.allowed is False
        assert v.limit == "max_symbol_exposure_usd"

    def test_max_positions_exceeded_denies(self):
        bounds = _bounds(max_positions=3)
        v = _check(bounds, _plan(), open_position_count=4)
        assert v.allowed is False
        assert v.limit == "max_positions"

    def test_max_positions_at_cap_allows(self):
        bounds = _bounds(max_positions=3)
        v = _check(bounds, _plan(), open_position_count=3)
        assert v.allowed is True

    def test_max_open_orders_exceeded_denies(self):
        bounds = _bounds(max_open_orders=2)
        v = _check(bounds, _plan(), open_order_count=2)
        assert v.allowed is False
        assert v.limit == "max_open_orders"

    def test_max_open_orders_below_cap_allows(self):
        bounds = _bounds(max_open_orders=2)
        v = _check(bounds, _plan(), open_order_count=1)
        assert v.allowed is True

    def test_daily_realized_loss_exceeded_denies(self):
        bounds = _bounds(max_daily_realized_loss_usd=500.0)
        v = _check(bounds, _plan(), daily_realized_loss_usd=600.0)
        assert v.allowed is False
        assert v.limit == "max_daily_realized_loss_usd"

    def test_drawdown_exceeded_denies(self):
        bounds = _bounds(max_drawdown_pct=0.10)
        v = _check(bounds, _plan(), drawdown_pct=0.15)
        assert v.allowed is False
        assert v.limit == "max_drawdown_pct"

    def test_drawdown_at_cap_allows(self):
        bounds = _bounds(max_drawdown_pct=0.10)
        v = _check(bounds, _plan(), drawdown_pct=0.10)
        assert v.allowed is True

    def test_all_satisfied_allows(self):
        bounds = _bounds(
            max_daily_turnover_usd=50000.0,
            max_symbol_exposure_usd=20000.0,
            max_positions=10,
            max_open_orders=5,
            max_daily_realized_loss_usd=1000.0,
            max_drawdown_pct=0.20,
        )
        v = _check(
            bounds, _plan(),
            daily_turnover_usd=1000.0,
            order_notional_usd=1000.0,
            symbol_exposure_usd=5000.0,
            open_position_count=2,
            open_order_count=1,
            daily_realized_loss_usd=100.0,
            drawdown_pct=0.05,
        )
        assert v.allowed is True
        assert v.limit == ""


# --------------------------------------------------------------------------- #
# check_automation_bounds — allow_short gate                                  #
# --------------------------------------------------------------------------- #


class TestAllowShortGate:
    def test_allow_short_false_no_long_denies(self):
        """sell with allow_short=False and no existing long → denied."""
        bounds = _bounds(allow_short=False)
        plan = _plan(side="sell", order_type="limit", limit_price=27.5)
        v = _check(bounds, plan, has_existing_long=False)
        assert v.allowed is False
        assert v.limit == "allow_short"

    def test_allow_short_false_with_long_allows(self):
        """sell with allow_short=False but has_existing_long=True → allowed (closing long)."""
        bounds = _bounds(allow_short=False)
        plan = _plan(side="sell", order_type="limit", limit_price=27.5)
        v = _check(bounds, plan, has_existing_long=True)
        assert v.allowed is True

    def test_allow_short_true_allows(self):
        """sell with allow_short=True → allowed regardless of position."""
        bounds = _bounds(allow_short=True)
        plan = _plan(side="sell", order_type="limit", limit_price=27.5)
        v = _check(bounds, plan, has_existing_long=False)
        assert v.allowed is True

    def test_buy_unaffected_by_allow_short(self):
        """buy orders are never blocked by the shorting gate."""
        bounds = _bounds(allow_short=False)
        plan = _plan(side="buy")
        v = _check(bounds, plan, has_existing_long=False)
        assert v.allowed is True


# --------------------------------------------------------------------------- #
# check_slippage                                                              #
# --------------------------------------------------------------------------- #


class TestCheckSlippage:
    def test_no_slippage_config_allows(self):
        bounds = _bounds(max_slippage_pct=None)
        v = check_slippage(bounds, _plan(), _signal())
        assert v.allowed is True

    def test_missing_price_ref_denies(self):
        bounds = _bounds(max_slippage_pct=0.05)
        v = check_slippage(bounds, _plan(order_type="limit", limit_price=27.5), _signal(price_ref=None))
        assert v.allowed is False
        assert v.limit == "max_slippage_pct"

    def test_missing_limit_price_denies(self):
        bounds = _bounds(max_slippage_pct=0.05)
        v = check_slippage(bounds, _plan(), _signal())
        assert v.allowed is False
        assert v.limit == "max_slippage_pct"

    def test_slippage_exceeded_denies(self):
        bounds = _bounds(max_slippage_pct=0.02)
        # ref=27.55, exec=29.0 → slippage ≈ 0.0526 > 0.02
        v = check_slippage(bounds, _plan(order_type="limit", limit_price=29.0), _signal(price_ref=27.55))
        assert v.allowed is False
        assert v.limit == "max_slippage_pct"

    def test_slippage_within_cap_allows(self):
        bounds = _bounds(max_slippage_pct=0.05)
        # ref=27.55, exec=27.6 → slippage ≈ 0.0018 < 0.05
        v = check_slippage(bounds, _plan(order_type="limit", limit_price=27.6), _signal(price_ref=27.55))
        assert v.allowed is True
