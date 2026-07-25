"""End-to-end integration tests for the full automation pipeline.

These tests wire the real AutomationRunner with real components (un-mocked
AutomationService, real AutomationExecutor, real DecisionGate, real bounds
check) and only fake the broker sink (FakePlaceOrder) and the scheduler.

The goal is to verify that the full pipeline — signal → decision → plan →
order → notification — works correctly when all components are wired together
by the real factories, catching any wiring bugs, type mismatches, or integration
issues that unit tests alone cannot detect.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional, Sequence

import pytest

from src.live.automation.models import (
    SignalCandidate,
    SignalDirection,
    TradePlan,
)
from src.live.automation.policy import AutomationMode, AutomationPolicy
from src.live.automation.runner import (
    AutomationRunner,
    AutomationRunnerConfig,
    CycleOutcome,
)
from src.live.automation.runtime_state import (
    AutomationRuntimeState,
    read_runtime_state,
)
from src.live.automation.service import AutomationService
from src.live.automation.signal_provider import StaticSignalProvider
from src.live.mandate.model import (
    AssetClass,
    AutomationBounds,
    ConsentMeta,
    HardCaps,
    InstrumentType,
    Mandate,
    UniverseConstraint,
)


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #

NOW_MS = 1_783_345_500_000  # 2026-07-06T13:45:00Z
NOW_ISO = "2026-07-06T13:44:55+00:00"  # 5 seconds before NOW_MS
FUTURE_EXPIRY = "2027-01-01T00:00:00+00:00"


def _signal(**overrides: Any) -> SignalCandidate:
    """Build a valid A-share buy signal."""
    defaults = dict(
        strategy_id="v17.13",
        strategy_version="abc123",
        symbol="688598.SH",
        direction=SignalDirection.LONG,
        score=0.85,
        generated_at=NOW_ISO,
        idempotency_key="v17.13:688598.SH:1783345500000",
        price_ref=27.55,
        target_quantity=1000.0,
    )
    defaults.update(overrides)
    return SignalCandidate(**defaults)


def _policy(**overrides: Any) -> AutomationPolicy:
    """Build a PAPER_AUTO policy with exit protection disabled."""
    defaults = dict(
        mode=AutomationMode.PAPER_AUTO,
        min_signal_score=0.7,
        max_signal_age_seconds=300,
        require_exit_protection=False,
        allowed_strategies=(),
        max_orders_per_cycle=5,
        symbol_cooldown_seconds=0,
    )
    defaults.update(overrides)
    return AutomationPolicy(**defaults)


def _mandate(**overrides: Any) -> Mandate:
    """Build a v1 mandate with automation_bounds for CN_EQUITY."""
    defaults = dict(
        hard_caps=HardCaps(
            account_funding_usd=100_000.0,
            max_order_notional_usd=50_000.0,
            max_total_exposure_usd=100_000.0,
            max_leverage=1.0,
            allowed_instruments=(InstrumentType.EQUITY,),
            max_trades_per_day=10,
        ),
        universe=UniverseConstraint(
            asset_classes=(AssetClass.CN_EQUITY,),
            min_market_cap_usd=None,
            min_avg_daily_volume_usd=None,
            exclude_symbols=(),
        ),
        consent=ConsentMeta(
            created_at="2026-07-01T00:00:00+00:00",
            consent_token_sha256="a" * 64,
            broker="virtual",
            account_ref="cn-default",
            expires_at=FUTURE_EXPIRY,
        ),
        automation_bounds=AutomationBounds(
            max_daily_turnover_usd=200_000.0,
            allowed_order_types=("limit", "market"),
            require_stop_loss=False,
            allow_short=False,
        ),
    )
    defaults.update(overrides)
    return Mandate(
        schema_version=1,
        hard_caps=defaults["hard_caps"],
        universe=defaults["universe"],
        consent=defaults["consent"],
        automation_bounds=defaults.get("automation_bounds"),
    )


def _runtime_state(**overrides: Any) -> AutomationRuntimeState:
    """Build a healthy runtime state."""
    defaults = dict(
        available=True,
        daily_turnover_usd=0.0,
        symbol_exposures={},
        open_position_count=0,
        open_order_count=0,
        daily_realized_loss_usd=0.0,
        peak_equity_usd=100_000.0,
        current_equity_usd=100_000.0,
        long_symbols=frozenset(),
    )
    defaults.update(overrides)
    return AutomationRuntimeState(**defaults)


class FakePlaceOrder:
    """Records calls to place_order; returns a success response."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def __call__(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        self.calls.append({"args": args, "kwargs": kwargs})
        return {"status": "ok", "order_id": f"fake-{len(self.calls)}"}


def _now_fn() -> int:
    return NOW_MS


# --------------------------------------------------------------------------- #
# End-to-end: full pipeline with real components, fake broker                 #
# --------------------------------------------------------------------------- #


def _make_runner(
    signals: Sequence[SignalCandidate],
    policy: AutomationPolicy | None = None,
    mandate: Mandate | None = None,
    runtime_state: AutomationRuntimeState | None = None,
    **config_overrides: Any,
) -> tuple[AutomationRunner, FakePlaceOrder]:
    """Build a fully-wired AutomationRunner with real components, fake broker.

    Returns:
        (runner, place_order_spy) — the runner and a spy on the order sink.
    """
    policy = policy or _policy()
    mandate = mandate or _mandate()
    runtime_state = runtime_state or _runtime_state()

    place = FakePlaceOrder()

    service = AutomationService(
        place_order_fn=place,
        load_mandate_fn=lambda b, aid=None: mandate,
        now_fn=_now_fn,
    )

    config = AutomationRunnerConfig(
        broker="virtual",
        instrument_type=InstrumentType.EQUITY,
        schedule="0 9 * * 1-5",
        asset_class=AssetClass.CN_EQUITY,
        **config_overrides,
    )

    runner = AutomationRunner(
        config,
        signal_provider=StaticSignalProvider(signals),
        service=service,
        read_runtime_state_fn=lambda: runtime_state,
        load_policy_fn=lambda b, aid=None: policy,
        now_fn=_now_fn,
    )

    return runner, place


# --------------------------------------------------------------------------- #
# Tests                                                                        #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_e2e_valid_signal_places_order():
    """A valid signal flows through the full pipeline and places an order."""
    runner, place = _make_runner([_signal()])

    outcome = await runner.run_once()

    assert outcome.ran is True
    assert outcome.result is not None
    assert outcome.result.orders_placed == 1
    assert outcome.result.orders_rejected == 0
    assert outcome.result.errors == 0

    # Verify the order sink received the correct parameters.
    assert len(place.calls) == 1
    call = place.calls[0]
    args = call["args"]
    kwargs = call["kwargs"]
    # symbol is the first positional arg in _submit()
    assert args[0] == "688598.SH"
    assert kwargs["side"] == "buy"
    assert kwargs["quantity"] == 1000.0
    assert kwargs["order_type"] == "limit"
    assert kwargs["limit_price"] == 27.55


@pytest.mark.asyncio
async def test_e2e_rejected_signal_does_not_place_order():
    """A signal with score below threshold is rejected by the DecisionGate."""
    runner, place = _make_runner(
        [_signal(score=0.3)],
        policy=_policy(min_signal_score=0.7),
    )

    outcome = await runner.run_once()

    assert outcome.ran is True
    assert outcome.result is not None
    assert outcome.result.orders_placed == 0
    assert outcome.result.orders_rejected == 1
    assert len(place.calls) == 0


@pytest.mark.asyncio
async def test_e2e_mixed_signals():
    """One valid + one rejected signal → correct statistics."""
    runner, place = _make_runner([
        _signal(),
        _signal(score=0.3, idempotency_key="rejected:key:1"),
    ])

    outcome = await runner.run_once()

    assert outcome.ran is True
    assert outcome.result.orders_placed == 1
    assert outcome.result.orders_rejected == 1
    assert len(place.calls) == 1


@pytest.mark.asyncio
async def test_e2e_policy_disabled_skips_cycle():
    """DISABLED policy → no scan, no orders."""
    runner, place = _make_runner(
        [_signal()],
        policy=_policy(mode=AutomationMode.DISABLED),
    )

    outcome = await runner.run_once()

    assert outcome.ran is False
    assert outcome.skipped_reason == "automation disabled"
    assert len(place.calls) == 0


@pytest.mark.asyncio
async def test_e2e_bounds_exceeded_blocks_order():
    """Bounds violation (turnover cap) blocks the order at pre-submit."""
    runner, place = _make_runner(
        [_signal()],
        mandate=_mandate(
            automation_bounds=AutomationBounds(
                max_daily_turnover_usd=100.0,  # very tight cap
                allowed_order_types=("limit",),
                require_stop_loss=False,
            )
        ),
    )

    outcome = await runner.run_once()

    assert outcome.ran is True
    assert outcome.result is not None
    assert outcome.result.orders_placed == 0
    # The order was blocked at pre-submit, not rejected by the gate.
    assert outcome.result.errors == 1
    assert "pre-submit bounds check failed" in outcome.result.results[0]["error"]
    assert len(place.calls) == 0


@pytest.mark.asyncio
async def test_e2e_runtime_state_unavailable_blocks_cycle():
    """Unavailable runtime state → fail-closed, entire cycle blocked."""
    runner, place = _make_runner(
        [_signal()],
        runtime_state=AutomationRuntimeState(available=False, error="broker unreachable"),
    )

    outcome = await runner.run_once()

    assert outcome.ran is True
    assert outcome.result is not None
    assert outcome.result.orders_placed == 0
    # Service returns orders_rejected=len(signals) when runtime_state unavailable.
    assert outcome.result.orders_rejected == 1
    assert outcome.result.errors == 1
    assert "runtime state unavailable" in outcome.result.results[0]["error"]
    assert len(place.calls) == 0


@pytest.mark.asyncio
async def test_e2e_notify_called_on_success():
    """The notify function is called when a cycle completes successfully."""
    notified: list[Any] = []

    async def _fake_notify(result: Any) -> bool:
        notified.append(result)
        return True

    runner, place = _make_runner([_signal()])
    # Inject the notify function via attribute (simpler than re-wiring constructor).
    runner._notify = _fake_notify  # type: ignore[attr-defined]

    outcome = await runner.run_once()

    assert outcome.ran is True
    assert outcome.notified is True
    assert len(notified) == 1
    assert notified[0].orders_placed == 1


@pytest.mark.asyncio
async def test_e2e_notify_error_does_not_break_cycle():
    """A failing notify function does not affect the cycle outcome."""
    async def _failing_notify(result: Any) -> bool:
        raise RuntimeError("notification failed")

    runner, place = _make_runner([_signal()])
    runner._notify = _failing_notify  # type: ignore[attr-defined]

    outcome = await runner.run_once()

    # Cycle still completed despite notification failure.
    assert outcome.ran is True
    assert outcome.result.orders_placed == 1
    assert outcome.notified is False
    assert len(place.calls) == 1


@pytest.mark.asyncio
async def test_e2e_trade_plan_fields_preserved():
    """The TradePlan built from a signal carries correct fields to the broker."""
    runner, place = _make_runner(
        [_signal(
            symbol="600519.SH",
            target_quantity=100.0,
            price_ref=1800.0,
        )],
    )

    await runner.run_once()

    call = place.calls[0]
    args = call["args"]
    kwargs = call["kwargs"]
    # symbol is positional arg 0 in _submit()
    assert args[0] == "600519.SH"
    assert kwargs["quantity"] == 100.0
    assert kwargs["side"] == "buy"
    assert kwargs["limit_price"] == 1800.0
    assert kwargs["order_type"] == "limit"


@pytest.mark.asyncio
async def test_e2e_max_orders_per_cycle_enforced():
    """max_orders_per_cycle caps the number of orders placed in one cycle."""
    signals = [
        _signal(symbol=f"TEST{i}.SH", idempotency_key=f"key:{i}")
        for i in range(5)
    ]
    runner, place = _make_runner(
        signals,
        policy=_policy(max_orders_per_cycle=2),
    )

    outcome = await runner.run_once()

    assert outcome.ran is True
    assert outcome.result.orders_placed == 2
    assert outcome.result.orders_rejected == 3  # 3 capped by max_orders_per_cycle
    assert len(place.calls) == 2


@pytest.mark.asyncio
async def test_e2e_cycle_outcome_has_correct_summary():
    """The CycleOutcome returned by run_once has correct summary fields."""
    runner, place = _make_runner([_signal(), _signal(score=0.3, idempotency_key="rej:1")])

    outcome = await runner.run_once()

    assert outcome.ran is True
    assert outcome.error is None
    assert outcome.result is not None
    assert outcome.result.broker == "virtual"
    assert outcome.result.mode == "paper_auto"
    assert outcome.result.signals_evaluated == 2
    assert outcome.result.orders_placed == 1
    assert outcome.result.orders_rejected == 1
    assert outcome.result.errors == 0