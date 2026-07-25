"""Tests for the AutomationExecutor (signal → decision → plan → order)."""

from typing import Any, Mapping

import pytest

from src.live.automation.executor import AutomationExecutor, build_trade_plan
from src.live.automation.models import (
    DecisionOutcome,
    ExecutionDecision,
    SignalCandidate,
    SignalDirection,
)
from src.live.automation.policy import AutomationMode, AutomationPolicy
from src.live.mandate.model import InstrumentType

# generated_at "2026-07-24T09:25:00Z" == 1784885100000 epoch ms.
GENERATED_MS = 1_784_885_100_000
NOW_MS = GENERATED_MS + 60_000


def _signal(**overrides) -> SignalCandidate:
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
    defaults = dict(
        mode=AutomationMode.PAPER_AUTO,
        min_signal_score=0.7,
        max_signal_age_seconds=300,
        require_exit_protection=False,  # keep sizing-only tests simple
        allowed_strategies=(),
        max_orders_per_cycle=5,
        symbol_cooldown_seconds=3600,
    )
    defaults.update(overrides)
    return AutomationPolicy(**defaults)


class FakePlaceOrder:
    """Records calls; mimics src.trading.service.place_order's signature."""

    def __init__(self, raise_exc: bool = False) -> None:
        self.calls: list[dict[str, Any]] = []
        self.raise_exc = raise_exc

    def __call__(self, symbol: str, profile_id: str | None = None, **kwargs: Any) -> Mapping[str, Any]:
        if self.raise_exc:
            raise RuntimeError("broker unavailable")
        call = {"symbol": symbol, "profile_id": profile_id, **kwargs}
        self.calls.append(call)
        return {"status": "accepted", "order_id": f"ord-{len(self.calls)}", "symbol": symbol}


def _executor(policy: AutomationPolicy, place: FakePlaceOrder) -> AutomationExecutor:
    return AutomationExecutor(
        policy,
        place,
        now_fn=lambda: NOW_MS,
        profile_id="paper-1",
        session_id="sess-1",
    )


# --------------------------------------------------------------------------- #
# Allowed signal → order placed with correct params                           #
# --------------------------------------------------------------------------- #


def test_allowed_signal_places_order_with_correct_params():
    place = FakePlaceOrder()
    executor = _executor(_policy(), place)

    results = executor.run_cycle([_signal()], instrument_type=InstrumentType.EQUITY)

    assert len(results) == 1
    assert results[0].placed
    assert len(place.calls) == 1
    call = place.calls[0]
    assert call["symbol"] == "688598.SH"
    assert call["side"] == "buy"
    assert call["quantity"] == 1000.0
    assert call["profile_id"] == "paper-1"
    assert call["session_id"] == "sess-1"


# --------------------------------------------------------------------------- #
# Rejected signal → no order                                                  #
# --------------------------------------------------------------------------- #


def test_rejected_signal_does_not_place_order():
    place = FakePlaceOrder()
    executor = _executor(_policy(min_signal_score=0.99), place)

    results = executor.run_cycle([_signal(score=0.5)], instrument_type=InstrumentType.EQUITY)

    assert len(results) == 1
    assert not results[0].placed
    assert results[0].decision.outcome is DecisionOutcome.REJECTED_SCORE
    assert place.calls == []


# --------------------------------------------------------------------------- #
# max_orders_per_cycle cap                                                    #
# --------------------------------------------------------------------------- #


def test_max_orders_per_cycle_caps_extra_signals():
    place = FakePlaceOrder()
    policy = _policy(max_orders_per_cycle=1)
    executor = _executor(policy, place)

    signals = [
        _signal(symbol="AAA", idempotency_key="k1"),
        _signal(symbol="BBB", idempotency_key="k2"),
    ]
    results = executor.run_cycle(signals, instrument_type=InstrumentType.EQUITY)

    assert results[0].placed
    assert not results[1].placed
    assert results[1].decision.outcome is DecisionOutcome.REJECTED_POLICY
    assert "max_orders_per_cycle" in results[1].decision.reason
    assert len(place.calls) == 1


# --------------------------------------------------------------------------- #
# Cooldown update after placement                                             #
# --------------------------------------------------------------------------- #


def test_cooldown_blocks_second_order_on_same_symbol():
    place = FakePlaceOrder()
    executor = _executor(_policy(), place)

    signals = [
        _signal(symbol="688598.SH", idempotency_key="k1"),
        _signal(symbol="688598.SH", idempotency_key="k2"),
    ]
    cooldown: dict[str, int] = {}
    results = executor.run_cycle(
        signals, instrument_type=InstrumentType.EQUITY, cooldown_deadlines=cooldown
    )

    assert results[0].placed
    assert not results[1].placed
    assert results[1].decision.outcome is DecisionOutcome.REJECTED_COOLDOWN
    assert len(place.calls) == 1
    # Cooldown deadline recorded for the symbol.
    assert "688598.SH" in cooldown
    assert cooldown["688598.SH"] == NOW_MS + 3600 * 1000


# --------------------------------------------------------------------------- #
# Idempotency key update after placement                                      #
# --------------------------------------------------------------------------- #


def test_seen_keys_updated_after_placement():
    place = FakePlaceOrder()
    executor = _executor(_policy(), place)

    seen: set[str] = set()
    signal = _signal(idempotency_key="unique-key")
    executor.run_cycle([signal], instrument_type=InstrumentType.EQUITY, seen_keys=seen)

    assert "unique-key" in seen


# --------------------------------------------------------------------------- #
# place_order raises → fail-closed, no crash, not marked seen                 #
# --------------------------------------------------------------------------- #


def test_place_order_exception_is_fail_closed():
    place = FakePlaceOrder(raise_exc=True)
    executor = _executor(_policy(), place)

    seen: set[str] = set()
    results = executor.run_cycle(
        [_signal(idempotency_key="boom")], instrument_type=InstrumentType.EQUITY, seen_keys=seen
    )

    assert len(results) == 1
    assert not results[0].placed
    assert results[0].error is not None
    assert "boom" not in seen  # not marked seen → allows retry next cycle


# --------------------------------------------------------------------------- #
# Direction → side mapping                                                    #
# --------------------------------------------------------------------------- #


def test_long_maps_to_buy():
    place = FakePlaceOrder()
    executor = _executor(_policy(), place)
    executor.run_cycle([_signal(direction=SignalDirection.LONG)], instrument_type=InstrumentType.EQUITY)
    assert place.calls[0]["side"] == "buy"


def test_close_maps_to_sell():
    place = FakePlaceOrder()
    executor = _executor(_policy(), place)
    executor.run_cycle([_signal(direction=SignalDirection.CLOSE)], instrument_type=InstrumentType.EQUITY)
    assert place.calls[0]["side"] == "sell"


def test_short_maps_to_sell():
    place = FakePlaceOrder()
    executor = _executor(_policy(), place)
    executor.run_cycle([_signal(direction=SignalDirection.SHORT)], instrument_type=InstrumentType.EQUITY)
    assert place.calls[0]["side"] == "sell"


# --------------------------------------------------------------------------- #
# Limit price defaults to price_ref                                           #
# --------------------------------------------------------------------------- #


def test_limit_order_defaults_limit_price_to_price_ref():
    place = FakePlaceOrder()
    executor = _executor(_policy(), place)
    executor.run_cycle(
        [_signal(price_ref=27.55)], instrument_type=InstrumentType.EQUITY, order_type="limit"
    )
    assert place.calls[0]["order_type"] == "limit"
    assert place.calls[0]["limit_price"] == 27.55


# --------------------------------------------------------------------------- #
# build_trade_plan direct                                                     #
# --------------------------------------------------------------------------- #


def test_build_trade_plan_requires_allowed_decision():
    signal = _signal()
    rejected = ExecutionDecision(signal=signal, outcome=DecisionOutcome.REJECTED_SCORE)
    with pytest.raises(ValueError, match="allowed"):
        build_trade_plan(rejected, instrument_type=InstrumentType.EQUITY, client_order_id="x")


def test_build_trade_plan_market_order_no_limit_price():
    signal = _signal()
    allowed = ExecutionDecision(signal=signal, outcome=DecisionOutcome.ALLOWED)
    plan = build_trade_plan(
        allowed, instrument_type=InstrumentType.EQUITY, client_order_id="coid", order_type="market"
    )
    assert plan.order_type == "market"
    assert plan.limit_price is None
    assert plan.quantity == 1000.0
    assert plan.client_order_id == "coid"


# --------------------------------------------------------------------------- #
# Warning 3: limit order + price_ref=None → fail-closed (skip, no order)      #
# --------------------------------------------------------------------------- #


def test_limit_order_no_price_ref_fails_closed():
    """A limit order with signal.price_ref=None should fail-closed (skip, no order)."""
    place = FakePlaceOrder()
    executor = _executor(_policy(), place)

    signal = _signal(price_ref=None, target_quantity=1000.0)
    results = executor.run_cycle(
        [signal], instrument_type=InstrumentType.EQUITY, order_type="limit"
    )

    assert len(results) == 1
    assert not results[0].placed
    assert results[0].error is not None
    assert "limit_price" in results[0].error
    assert place.calls == []  # no order submitted


def test_build_trade_plan_limit_no_price_ref_raises():
    """build_trade_plan raises ValueError for limit order without price_ref."""
    signal = _signal(price_ref=None, target_quantity=1000.0)
    allowed = ExecutionDecision(signal=signal, outcome=DecisionOutcome.ALLOWED)
    with pytest.raises(ValueError, match="limit_price"):
        build_trade_plan(
            allowed,
            instrument_type=InstrumentType.EQUITY,
            client_order_id="coid",
            order_type="limit",
        )


# --------------------------------------------------------------------------- #
# pre_submit_check callback                                                   #
# --------------------------------------------------------------------------- #


def test_pre_submit_check_false_blocks_submission():
    """pre_submit_check returning False → not submitted, error recorded."""
    place = FakePlaceOrder()
    executor = _executor(_policy(), place)

    results = executor.run_cycle(
        [_signal()],
        instrument_type=InstrumentType.EQUITY,
        pre_submit_check=lambda plan: False,
    )

    assert len(results) == 1
    assert not results[0].placed
    assert results[0].error == "pre-submit bounds check failed"
    assert results[0].plan is not None  # plan was built but not submitted
    assert place.calls == []


def test_pre_submit_check_exception_fail_closed():
    """pre_submit_check raising → fail-closed, not submitted."""
    place = FakePlaceOrder()
    executor = _executor(_policy(), place)

    def bad_check(plan):
        raise RuntimeError("bounds service unavailable")

    results = executor.run_cycle(
        [_signal()],
        instrument_type=InstrumentType.EQUITY,
        pre_submit_check=bad_check,
    )

    assert len(results) == 1
    assert not results[0].placed
    assert "pre-submit check error" in results[0].error
    assert "bounds service unavailable" in results[0].error
    assert place.calls == []


def test_pre_submit_check_true_allows_submission():
    """pre_submit_check returning True → order submitted normally."""
    place = FakePlaceOrder()
    executor = _executor(_policy(), place)

    results = executor.run_cycle(
        [_signal()],
        instrument_type=InstrumentType.EQUITY,
        pre_submit_check=lambda plan: True,
    )

    assert len(results) == 1
    assert results[0].placed
    assert len(place.calls) == 1


def test_pre_submit_check_none_backward_compatible():
    """pre_submit_check=None (default) → order submitted normally."""
    place = FakePlaceOrder()
    executor = _executor(_policy(), place)

    results = executor.run_cycle(
        [_signal()],
        instrument_type=InstrumentType.EQUITY,
    )

    assert len(results) == 1
    assert results[0].placed
    assert len(place.calls) == 1


def test_pre_submit_check_receives_correct_plan():
    """pre_submit_check receives the fully constructed TradePlan."""
    place = FakePlaceOrder()
    executor = _executor(_policy(), place)
    received_plans = []

    def capture_check(plan):
        received_plans.append(plan)
        return True

    executor.run_cycle(
        [_signal(price_ref=27.55, target_quantity=1000.0)],
        instrument_type=InstrumentType.EQUITY,
        order_type="limit",
        pre_submit_check=capture_check,
    )

    assert len(received_plans) == 1
    plan = received_plans[0]
    assert plan.symbol == "688598.SH"
    assert plan.side == "buy"
    assert plan.quantity == 1000.0
    assert plan.limit_price == 27.55
    assert plan.order_type == "limit"
