"""Tests for AutomationRunner — the deterministic closed-loop orchestrator.

Uses fakes for every injected dependency to cover the full fail-closed chain:
policy load error → disabled skip → signal scan error → runtime state error →
normal cycle → notification (success/failure/absent). Also covers run_loop
guard and cross-cycle state persistence.
"""

from __future__ import annotations

from typing import Any, Optional, Sequence

import pytest

from src.live.automation.models import SignalCandidate, SignalDirection
from src.live.automation.policy import AutomationMode, AutomationPolicy
from src.live.automation.runner import (
    AutomationRunner,
    AutomationRunnerConfig,
    CycleOutcome,
)
from src.live.automation.runtime_state import AutomationRuntimeState
from src.live.automation.service import AutomationCycleResult
from src.live.mandate.model import AssetClass, InstrumentType

NOW_MS = 1_784_885_100_000


# --------------------------------------------------------------------------- #
# Fakes                                                                       #
# --------------------------------------------------------------------------- #


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


class FakeSignalProvider:
    """Signal provider that records scan() calls; optionally raises."""

    def __init__(self, signals: Sequence[SignalCandidate] = (), *, raise_on_scan: bool = False) -> None:
        self._signals = list(signals)
        self._raise = raise_on_scan
        self.scan_calls = 0

    def scan(self) -> Sequence[SignalCandidate]:
        self.scan_calls += 1
        if self._raise:
            raise RuntimeError("scan boom")
        return list(self._signals)


class FakeService:
    """AutomationService stand-in that records run_cycle calls."""

    def __init__(self, result: Optional[AutomationCycleResult] = None, *, raise_on_cycle: bool = False) -> None:
        self._result = result
        self._raise = raise_on_cycle
        self.calls: list[dict[str, Any]] = []

    def run_cycle(self, broker, signals, policy, **kwargs) -> AutomationCycleResult:
        self.calls.append({"broker": broker, "signals": list(signals), "policy": policy, **kwargs})
        if self._raise:
            raise RuntimeError("cycle boom")
        if self._result is not None:
            return self._result
        return AutomationCycleResult(
            broker=broker,
            mode=policy.mode.value,
            signals_evaluated=len(list(signals)),
            orders_placed=0,
            orders_rejected=0,
            errors=0,
            results=(),
        )


class FakeRuntimeReader:
    """read_runtime_state stand-in; optionally raises."""

    def __init__(self, state: Optional[AutomationRuntimeState] = None, *, raise_on_read: bool = False) -> None:
        self._state = state or AutomationRuntimeState(available=True)
        self._raise = raise_on_read
        self.calls = 0

    def __call__(self) -> AutomationRuntimeState:
        self.calls += 1
        if self._raise:
            raise RuntimeError("runtime read boom")
        return self._state


def _policy(mode: AutomationMode = AutomationMode.PAPER_AUTO) -> AutomationPolicy:
    return AutomationPolicy(mode=mode, require_exit_protection=False)


class FakePolicyLoader:
    """load_policy stand-in; returns a fixed policy or raises."""

    def __init__(self, policy: Optional[AutomationPolicy] = None, *, raise_on_load: bool = False) -> None:
        self._policy = policy if policy is not None else _policy()
        self._raise = raise_on_load
        self.calls: list[tuple] = []

    def __call__(self, broker, account_id=None) -> AutomationPolicy:
        self.calls.append((broker, account_id))
        if self._raise:
            raise RuntimeError("policy load boom")
        return self._policy


class FakeNotifier:
    """Async notify_fn stand-in; records calls, optionally raises."""

    def __init__(self, *, returns: bool = True, raise_on_notify: bool = False) -> None:
        self._returns = returns
        self._raise = raise_on_notify
        self.calls: list[AutomationCycleResult] = []

    async def __call__(self, result: AutomationCycleResult) -> bool:
        self.calls.append(result)
        if self._raise:
            raise RuntimeError("notify boom")
        return self._returns


def _config(**overrides) -> AutomationRunnerConfig:
    defaults = dict(
        broker="virtual",
        instrument_type=InstrumentType.EQUITY,
        schedule="25 9 * * 1-5",
        job_id="automation-cycle",
        account_id="acct1",
        asset_class=AssetClass.CN_EQUITY,
    )
    defaults.update(overrides)
    return AutomationRunnerConfig(**defaults)


def _make_runner(
    *,
    signal_provider=None,
    service=None,
    runtime_reader=None,
    policy_loader=None,
    scheduler=None,
    notify_fn=None,
    config=None,
) -> AutomationRunner:
    return AutomationRunner(
        config or _config(),
        signal_provider=signal_provider or FakeSignalProvider(),
        service=service or FakeService(),
        read_runtime_state_fn=runtime_reader or FakeRuntimeReader(),
        load_policy_fn=policy_loader or FakePolicyLoader(),
        scheduler=scheduler,
        notify_fn=notify_fn,
        now_fn=lambda: NOW_MS,
    )


# --------------------------------------------------------------------------- #
# Fail-closed chain                                                           #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_policy_load_error_skips_cycle() -> None:
    runner = _make_runner(
        policy_loader=FakePolicyLoader(raise_on_load=True),
        signal_provider=FakeSignalProvider([_signal()]),
        service=FakeService(),
    )
    outcome = await runner.run_once()

    assert outcome.ran is False
    assert outcome.skipped_reason == "policy load failed"
    assert outcome.error is not None
    assert runner.last_outcome is outcome


@pytest.mark.asyncio
async def test_policy_disabled_skips_scan_and_service() -> None:
    provider = FakeSignalProvider([_signal()])
    service = FakeService()
    runner = _make_runner(
        policy_loader=FakePolicyLoader(_policy(AutomationMode.DISABLED)),
        signal_provider=provider,
        service=service,
    )
    outcome = await runner.run_once()

    assert outcome.ran is False
    assert outcome.skipped_reason == "automation disabled"
    # scan and service must NOT be invoked when disabled.
    assert provider.scan_calls == 0
    assert service.calls == []


@pytest.mark.asyncio
async def test_signal_scan_error_skips_service() -> None:
    service = FakeService()
    runner = _make_runner(
        signal_provider=FakeSignalProvider(raise_on_scan=True),
        service=service,
    )
    outcome = await runner.run_once()

    assert outcome.ran is False
    assert outcome.skipped_reason == "signal scan failed"
    assert outcome.error is not None
    assert service.calls == []


@pytest.mark.asyncio
async def test_runtime_state_error_still_calls_service_unavailable() -> None:
    service = FakeService()
    runner = _make_runner(
        signal_provider=FakeSignalProvider([_signal()]),
        service=service,
        runtime_reader=FakeRuntimeReader(raise_on_read=True),
    )
    outcome = await runner.run_once()

    assert outcome.ran is True
    assert len(service.calls) == 1
    runtime_state = service.calls[0]["runtime_state"]
    assert runtime_state.available is False
    assert runtime_state.error  # carries the error message


@pytest.mark.asyncio
async def test_normal_cycle_passes_correct_args() -> None:
    sig = _signal()
    provider = FakeSignalProvider([sig])
    service = FakeService()
    policy_loader = FakePolicyLoader(_policy(AutomationMode.PAPER_AUTO))
    runner = _make_runner(
        signal_provider=provider,
        service=service,
        policy_loader=policy_loader,
    )
    outcome = await runner.run_once()

    assert outcome.ran is True
    assert outcome.result is not None
    assert outcome.error is None
    assert len(service.calls) == 1

    call = service.calls[0]
    assert call["broker"] == "virtual"
    assert list(call["signals"]) == [sig]
    assert call["policy"].mode is AutomationMode.PAPER_AUTO
    assert call["instrument_type"] is InstrumentType.EQUITY
    assert call["asset_class"] is AssetClass.CN_EQUITY
    assert call["account_id"] == "acct1"
    assert policy_loader.calls == [("virtual", "acct1")]


@pytest.mark.asyncio
async def test_service_error_captured_fail_closed() -> None:
    runner = _make_runner(
        signal_provider=FakeSignalProvider([_signal()]),
        service=FakeService(raise_on_cycle=True),
    )
    outcome = await runner.run_once()

    assert outcome.ran is True
    assert outcome.error is not None
    assert outcome.result is None


# --------------------------------------------------------------------------- #
# Notification                                                                #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_notify_success_sets_notified_true() -> None:
    notifier = FakeNotifier(returns=True)
    runner = _make_runner(
        signal_provider=FakeSignalProvider([_signal()]),
        notify_fn=notifier,
    )
    outcome = await runner.run_once()

    assert outcome.ran is True
    assert outcome.notified is True
    assert len(notifier.calls) == 1


@pytest.mark.asyncio
async def test_notify_error_does_not_break_cycle() -> None:
    notifier = FakeNotifier(raise_on_notify=True)
    runner = _make_runner(
        signal_provider=FakeSignalProvider([_signal()]),
        notify_fn=notifier,
    )
    outcome = await runner.run_once()

    # Notification failure must not raise nor affect trading outcome.
    assert outcome.ran is True
    assert outcome.notified is False
    assert outcome.result is not None
    assert outcome.error is None


@pytest.mark.asyncio
async def test_notify_none_results_in_not_notified() -> None:
    runner = _make_runner(
        signal_provider=FakeSignalProvider([_signal()]),
        notify_fn=None,
    )
    outcome = await runner.run_once()

    assert outcome.ran is True
    assert outcome.notified is False


# --------------------------------------------------------------------------- #
# run_loop / scheduler                                                        #
# --------------------------------------------------------------------------- #


def test_run_loop_without_scheduler_raises() -> None:
    runner = _make_runner(scheduler=None)
    with pytest.raises(RuntimeError):
        runner.run_loop()


def test_run_loop_with_scheduler_adds_job_and_starts() -> None:
    class FakeScheduler:
        def __init__(self) -> None:
            self.jobs: list[Any] = []
            self.started = False
            self.stopped = False

        def add_job(self, job) -> None:
            self.jobs.append(job)

        def start(self) -> None:
            self.started = True

        def stop(self) -> None:
            self.stopped = True

    scheduler = FakeScheduler()
    runner = _make_runner(scheduler=scheduler)
    runner.run_loop()

    assert scheduler.started is True
    assert len(scheduler.jobs) == 1
    job = scheduler.jobs[0]
    assert job.id == "automation-cycle"
    assert job.schedule == "25 9 * * 1-5"
    assert job.next_run_at == NOW_MS
    assert job.payload == {"broker": "virtual", "account_id": "acct1"}


@pytest.mark.asyncio
async def test_stop_loop_stops_scheduler() -> None:
    class FakeScheduler:
        def __init__(self) -> None:
            self.stopped = False

        def add_job(self, job) -> None: ...
        def start(self) -> None: ...

        async def stop(self) -> None:
            self.stopped = True

    scheduler = FakeScheduler()
    runner = _make_runner(scheduler=scheduler)
    await runner.stop_loop()
    assert scheduler.stopped is True


@pytest.mark.asyncio
async def test_stop_loop_without_scheduler_is_noop() -> None:
    runner = _make_runner(scheduler=None)
    await runner.stop_loop()  # must not raise


# --------------------------------------------------------------------------- #
# Cross-cycle state persistence                                               #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_cross_cycle_state_persists() -> None:
    """seen_keys / cooldown_deadlines are the same objects across cycles."""
    service = FakeService()
    runner = _make_runner(
        signal_provider=FakeSignalProvider([_signal()]),
        service=service,
    )

    await runner.run_once()
    await runner.run_once()

    assert len(service.calls) == 2
    seen0 = service.calls[0]["seen_keys"]
    seen1 = service.calls[1]["seen_keys"]
    cd0 = service.calls[0]["cooldown_deadlines"]
    cd1 = service.calls[1]["cooldown_deadlines"]

    # Same underlying objects → mutations in the service propagate across cycles.
    assert seen0 is seen1
    assert cd0 is cd1

    # Mutate via the first reference and confirm it is visible in the second.
    seen0.add("k1")
    cd0["688598.SH"] = NOW_MS + 3600_000
    assert "k1" in seen1
    assert cd1["688598.SH"] == NOW_MS + 3600_000
