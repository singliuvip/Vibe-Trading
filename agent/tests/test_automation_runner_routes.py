"""Route-level tests for the automation runner start/stop endpoints.

Exercises the REST surface mounted by ``register_automation_routes``:
- ``POST /live/automation/start``
- ``POST /live/automation/stop``

Starting the runner is a privileged surface action gated on the same five
checks as the enable endpoint (kill switch + mandate + automation_bounds +
expiry + policy mode). Gate-failure tests let the REAL factory run (it only
constructs objects — no broker reads or scheduler starts happen at build time,
so the mandate/policy checks execute without side effects). Success tests
monkeypatch ``_build_automation_runner`` so no real scheduler loop runs.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import pytest
from fastapi.testclient import TestClient

import api_server
import src.api.automation_routes as automation_routes
import src.live.paths as paths
from src.live.automation.policy import AutomationMode, AutomationPolicy
from src.live.automation.policy_store import save_policy
from src.live.mandate.model import (
    MANDATE_SCHEMA_VERSION,
    AssetClass,
    AutomationBounds,
    ConsentMeta,
    HardCaps,
    InstrumentType,
    Mandate,
    UniverseConstraint,
)


# --------------------------------------------------------------------------- #
# Fakes                                                                        #
# --------------------------------------------------------------------------- #


class FakeRunner:
    """Stand-in AutomationRunner that records lifecycle calls.

    ``run_loop`` is a no-op (returns None) so the driving coroutine completes
    immediately — mirroring how the LiveRunner route tests stub the driver. The
    stop endpoint still observes ``stop_loop`` calls via the holder.
    """

    def __init__(self) -> None:
        self.run_loop_calls = 0
        self.stop_loop_calls = 0

    def run_loop(self) -> None:
        self.run_loop_calls += 1

    async def stop_loop(self) -> None:
        self.stop_loop_calls += 1


def _fake_build_runner(broker: str, account_id: Optional[str] = None, schedule: Optional[str] = None, shadow_id: Optional[str] = None) -> FakeRunner:
    """Factory replacement that returns a FakeRunner (no real wiring)."""
    return FakeRunner()


async def _noop_drive(runner: Any) -> None:
    """No-op driver that completes immediately (no real scheduler loop)."""
    return None


class _PendingTask:
    """Fake asyncio.Task stand-in that never reports done (for idempotency tests)."""

    def __init__(self) -> None:
        self.cancelled = False

    def done(self) -> bool:
        return False

    def cancel(self) -> None:
        self.cancelled = True


# --------------------------------------------------------------------------- #
# Fixtures                                                                     #
# --------------------------------------------------------------------------- #


@pytest.fixture
def live_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect runtime root to tmp_path."""
    monkeypatch.setattr(paths, "get_runtime_root", lambda: tmp_path)
    return tmp_path


@pytest.fixture
def client(live_runtime: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """TestClient with loopback identity (require_auth passes for local).

    Does NOT monkeypatch the runner factory — gate-failure tests rely on the
    real factory's mandate/policy checks. Success tests install the fake
    factory themselves via ``monkeypatch``.
    """
    monkeypatch.delenv("API_AUTH_KEY", raising=False)
    monkeypatch.setattr(api_server, "_API_KEY", "", raising=False)
    return TestClient(api_server.app, client=("127.0.0.1", 50000))


@pytest.fixture(autouse=True)
def _clear_runner_registries() -> Any:
    """Ensure no task leaks between tests."""
    automation_routes._automation_runner_tasks.clear()
    automation_routes._automation_runner_holder.clear()
    yield
    for task in list(automation_routes._automation_runner_tasks.values()):
        if not task.done():
            task.cancel()
    automation_routes._automation_runner_tasks.clear()
    automation_routes._automation_runner_holder.clear()


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #


def _write_mandate_with_bounds(broker_path: Path, *, expires_at: str | None = None) -> None:
    """Write a mandate.json WITH automation_bounds (v2 shape)."""
    broker_path.mkdir(parents=True, exist_ok=True)
    if expires_at is None:
        expires_at = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
    payload = {
        "schema_version": MANDATE_SCHEMA_VERSION,
        "flatten_on_halt": False,
        "hard_caps": {
            "account_funding_usd": 5000.0,
            "max_order_notional_usd": 750.0,
            "max_total_exposure_usd": 5000.0,
            "max_leverage": 1.0,
            "allowed_instruments": ["equity"],
            "max_trades_per_day": 5,
        },
        "universe": {
            "asset_classes": ["us_equity"],
            "min_market_cap_usd": None,
            "min_avg_daily_volume_usd": None,
            "exclude_symbols": [],
        },
        "consent": {
            "created_at": "2026-06-01T00:00:00Z",
            "consent_token_sha256": "a" * 64,
            "broker": "robinhood",
            "account_ref": "rh_acct_test",
            "expires_at": expires_at,
        },
        "automation_bounds": {
            "max_daily_turnover_usd": 10000.0,
            "max_symbol_exposure_usd": 5000.0,
            "max_positions": 5,
            "max_open_orders": 3,
            "max_daily_realized_loss_usd": 500.0,
            "max_drawdown_pct": 0.10,
            "allowed_order_types": ["market", "limit"],
            "max_slippage_pct": 0.02,
            "require_stop_loss": True,
            "allow_short": False,
        },
    }
    (broker_path / "mandate.json").write_text(json.dumps(payload), encoding="utf-8")


def _write_mandate_v1(broker_path: Path) -> None:
    """Write a v1 mandate.json WITHOUT automation_bounds."""
    broker_path.mkdir(parents=True, exist_ok=True)
    expires_at = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
    payload = {
        "schema_version": MANDATE_SCHEMA_VERSION,
        "flatten_on_halt": False,
        "hard_caps": {
            "account_funding_usd": 5000.0,
            "max_order_notional_usd": 750.0,
            "max_total_exposure_usd": 5000.0,
            "max_leverage": 1.0,
            "allowed_instruments": ["equity"],
            "max_trades_per_day": 5,
        },
        "universe": {
            "asset_classes": ["us_equity"],
            "min_market_cap_usd": None,
            "min_avg_daily_volume_usd": None,
            "exclude_symbols": [],
        },
        "consent": {
            "created_at": "2026-06-01T00:00:00Z",
            "consent_token_sha256": "a" * 64,
            "broker": "robinhood",
            "account_ref": "rh_acct_test",
            "expires_at": expires_at,
        },
    }
    (broker_path / "mandate.json").write_text(json.dumps(payload), encoding="utf-8")


def _install_fake_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    """Monkeypatch the runner factory + driver to avoid real scheduler/broker wiring.

    The driver is stubbed to a no-op coroutine so the spawned task completes
    immediately (mirroring the LiveRunner route tests). Tests that need to
    observe a still-pending task pre-seed a ``_PendingTask`` into the registry.
    """
    monkeypatch.setattr(automation_routes, "_build_automation_runner", _fake_build_runner)
    monkeypatch.setattr(automation_routes, "_drive_automation_runner", _noop_drive)


# --------------------------------------------------------------------------- #
# POST /live/automation/start — gate failures (real factory runs the checks)  #
# --------------------------------------------------------------------------- #


def test_start_no_mandate_returns_409(client: TestClient) -> None:
    """No committed mandate → 409 (factory raises before any task is created)."""
    resp = client.post("/live/automation/start", json={"broker": "robinhood"})
    assert resp.status_code == 409
    assert "mandate" in resp.json()["detail"].lower()
    # No task should be registered on failure.
    assert "robinhood" not in automation_routes._automation_runner_tasks


def test_start_v1_mandate_returns_409(client: TestClient, live_runtime: Path) -> None:
    """v1 mandate (no automation_bounds) → 409."""
    from src.live.paths import broker_dir

    _write_mandate_v1(broker_dir("robinhood"))
    resp = client.post("/live/automation/start", json={"broker": "robinhood"})
    assert resp.status_code == 409
    assert "automation_bounds" in resp.json()["detail"]


def test_start_expired_mandate_returns_409(client: TestClient, live_runtime: Path) -> None:
    """Expired mandate → 409."""
    from src.live.paths import broker_dir

    expired = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    _write_mandate_with_bounds(broker_dir("robinhood"), expires_at=expired)
    resp = client.post("/live/automation/start", json={"broker": "robinhood"})
    assert resp.status_code == 409
    assert "expired" in resp.json()["detail"].lower()


def test_start_policy_disabled_returns_409(client: TestClient, live_runtime: Path) -> None:
    """Policy mode DISABLED → 409 (even with a valid v2 mandate)."""
    from src.live.paths import broker_dir

    _write_mandate_with_bounds(broker_dir("robinhood"))
    # Default policy is DISABLED (fail-closed), so no save needed.
    resp = client.post("/live/automation/start", json={"broker": "robinhood"})
    assert resp.status_code == 409
    assert "disabled" in resp.json()["detail"].lower()


def test_start_kill_switch_tripped_returns_409(
    client: TestClient, live_runtime: Path
) -> None:
    """Kill switch tripped → 409 (checked before the factory)."""
    from src.live.halt import trip_halt
    from src.live.paths import broker_dir

    _write_mandate_with_bounds(broker_dir("robinhood"))
    save_policy(AutomationPolicy(mode=AutomationMode.PAPER_AUTO), "robinhood")
    trip_halt(by="cli", reason="test")
    resp = client.post("/live/automation/start", json={"broker": "robinhood"})
    assert resp.status_code == 409
    assert "kill switch" in resp.json()["detail"].lower()


def test_start_blank_broker_returns_400(client: TestClient) -> None:
    """Whitespace-only broker → 400 (passes Pydantic min_length, fails strip)."""
    resp = client.post("/live/automation/start", json={"broker": "   "})
    assert resp.status_code == 400


# --------------------------------------------------------------------------- #
# POST /live/automation/start — success + idempotency (fake factory)          #
# --------------------------------------------------------------------------- #


def test_start_success(
    client: TestClient, live_runtime: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Valid mandate + bounds + policy non-DISABLED → 200, started=True."""
    from src.live.paths import broker_dir

    _write_mandate_with_bounds(broker_dir("robinhood"))
    save_policy(AutomationPolicy(mode=AutomationMode.PAPER_AUTO), "robinhood")
    _install_fake_factory(monkeypatch)

    resp = client.post("/live/automation/start", json={"broker": "robinhood"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["broker"] == "robinhood"
    assert body["started"] is True
    assert body["already_running"] is False

    # The no-op driver completes immediately, so the done-callback pops the
    # registries — that is expected and mirrors the LiveRunner route tests.
    # The success signal is the 200 + started=True response.


def test_start_already_running_returns_already_running(
    client: TestClient, live_runtime: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A second start while a task is alive → already_running=True."""
    from src.live.paths import broker_dir

    _write_mandate_with_bounds(broker_dir("robinhood"))
    save_policy(AutomationPolicy(mode=AutomationMode.PAPER_AUTO), "robinhood")
    _install_fake_factory(monkeypatch)

    # First start (no-op driver → completes immediately).
    resp1 = client.post("/live/automation/start", json={"broker": "robinhood"})
    assert resp1.status_code == 200
    assert resp1.json()["started"] is True

    # Pre-seed a still-pending task so the idempotency branch is deterministic
    # (mirrors the LiveRunner route test pattern).
    pending = _PendingTask()
    automation_routes._automation_runner_tasks["robinhood"] = pending

    # Second start → idempotent no-op (must NOT replace the pending task).
    resp2 = client.post("/live/automation/start", json={"broker": "robinhood"})
    assert resp2.status_code == 200
    body = resp2.json()
    assert body["started"] is False
    assert body["already_running"] is True
    # The pre-seeded pending task must still be the registered one.
    assert automation_routes._automation_runner_tasks["robinhood"] is pending


# --------------------------------------------------------------------------- #
# POST /live/automation/stop                                                   #
# --------------------------------------------------------------------------- #


def test_stop_not_running_returns_was_running_false(client: TestClient) -> None:
    """Stop with no running task → stopped=False, was_running=False."""
    resp = client.post("/live/automation/stop", json={"broker": "robinhood"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["broker"] == "robinhood"
    assert body["stopped"] is False
    assert body["was_running"] is False


def test_stop_blank_broker_returns_400(client: TestClient) -> None:
    """Whitespace-only broker → 400 (passes Pydantic min_length, fails strip)."""
    resp = client.post("/live/automation/stop", json={"broker": "   "})
    assert resp.status_code == 400


def test_stop_running_task_returns_was_running_true(
    client: TestClient, live_runtime: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stop a running task → stopped=True, was_running=True, stop_loop awaited."""
    from src.live.paths import broker_dir

    _write_mandate_with_bounds(broker_dir("robinhood"))
    save_policy(AutomationPolicy(mode=AutomationMode.PAPER_AUTO), "robinhood")
    _install_fake_factory(monkeypatch)

    # Pre-seed a still-pending task + a fake runner in the holder so the stop
    # endpoint has something to cancel and a runner whose stop_loop we can
    # observe (mirrors the LiveRunner route test pattern).
    pending = _PendingTask()
    runner = FakeRunner()
    automation_routes._automation_runner_tasks["robinhood"] = pending
    automation_routes._automation_runner_holder["robinhood"] = runner

    resp = client.post("/live/automation/stop", json={"broker": "robinhood"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["broker"] == "robinhood"
    assert body["stopped"] is True
    assert body["was_running"] is True

    # The pending task must have been cancelled.
    assert pending.cancelled is True
    # The runner's stop_loop should have been awaited (best-effort release).
    assert runner.stop_loop_calls >= 1
    # Registries should be cleared.
    assert "robinhood" not in automation_routes._automation_runner_tasks
    assert "robinhood" not in automation_routes._automation_runner_holder


# --------------------------------------------------------------------------- #
# Independence from LiveRunner's _runner_tasks                                 #
# --------------------------------------------------------------------------- #


def test_automation_registry_is_independent_from_live_runner(
    client: TestClient, live_runtime: Path
) -> None:
    """The automation task dict must not be LiveRunner's _runner_tasks."""
    import src.api.live_routes as live_routes

    assert automation_routes._automation_runner_tasks is not live_routes._runner_tasks
    assert automation_routes._automation_runner_holder is not live_routes._runner_tasks


# --------------------------------------------------------------------------- #
# profile_id routing (real factory, monkeypatched resolver + driver)         #
# --------------------------------------------------------------------------- #


def test_start_resolves_profile_id_via_connector_resolver(
    client: TestClient, live_runtime: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The real factory must resolve profile_id via connector_profile_id_for_broker.

    Uses a spy wrapper around the real factory to capture the constructed runner
    (the task completes immediately via _noop_drive, so the holder is cleaned up
    by the done-callback — we capture the runner before that happens).
    """
    from src.live.paths import broker_dir

    _write_mandate_with_bounds(broker_dir("robinhood"))
    save_policy(AutomationPolicy(mode=AutomationMode.PAPER_AUTO), "robinhood")

    # Monkeypatch the profile resolver to return a known id.
    import src.trading.service as svc_mod

    resolved_id = "robinhood-live-mcp"
    monkeypatch.setattr(
        svc_mod, "connector_profile_id_for_broker", lambda b: resolved_id
    )

    # Spy on the real factory to capture the runner.
    captured: dict = {}
    real_build = automation_routes._build_automation_runner

    def _spy_build(broker: str, account_id: Optional[str] = None, schedule: Optional[str] = None, shadow_id: Optional[str] = None) -> Any:
        runner = real_build(broker, account_id, schedule=schedule, shadow_id=shadow_id)
        captured["runner"] = runner
        return runner

    monkeypatch.setattr(automation_routes, "_build_automation_runner", _spy_build)
    monkeypatch.setattr(automation_routes, "_drive_automation_runner", _noop_drive)

    resp = client.post("/live/automation/start", json={"broker": "robinhood"})
    assert resp.status_code == 200
    assert resp.json()["started"] is True

    # Verify the captured runner carries the resolved profile_id.
    runner = captured["runner"]
    assert runner._config.profile_id == resolved_id


def test_start_profile_resolver_failure_is_fail_closed(
    client: TestClient, live_runtime: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If connector_profile_id_for_broker raises, profile_id falls back to None (fail-closed)."""
    from src.live.paths import broker_dir

    _write_mandate_with_bounds(broker_dir("robinhood"))
    save_policy(AutomationPolicy(mode=AutomationMode.PAPER_AUTO), "robinhood")

    import src.trading.service as svc_mod

    def _raise(_b: str) -> str:
        raise RuntimeError("no profile configured")

    monkeypatch.setattr(svc_mod, "connector_profile_id_for_broker", _raise)

    captured: dict = {}
    real_build = automation_routes._build_automation_runner

    def _spy_build(broker: str, account_id: Optional[str] = None, schedule: Optional[str] = None, shadow_id: Optional[str] = None) -> Any:
        runner = real_build(broker, account_id, schedule=schedule, shadow_id=shadow_id)
        captured["runner"] = runner
        return runner

    monkeypatch.setattr(automation_routes, "_build_automation_runner", _spy_build)
    monkeypatch.setattr(automation_routes, "_drive_automation_runner", _noop_drive)

    resp = client.post("/live/automation/start", json={"broker": "robinhood"})
    assert resp.status_code == 200
    assert resp.json()["started"] is True

    runner = captured["runner"]
    assert runner._config.profile_id is None  # fail-closed fallback


# --------------------------------------------------------------------------- #
# schedule configuration                                                      #
# --------------------------------------------------------------------------- #


def test_start_with_custom_schedule(
    client: TestClient, live_runtime: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A custom schedule in the request body is passed to the runner config."""
    from src.live.paths import broker_dir

    _write_mandate_with_bounds(broker_dir("robinhood"))
    save_policy(AutomationPolicy(mode=AutomationMode.PAPER_AUTO), "robinhood")

    captured: dict = {}
    real_build = automation_routes._build_automation_runner

    def _spy_build(broker: str, account_id: Optional[str] = None, schedule: Optional[str] = None, shadow_id: Optional[str] = None) -> Any:
        runner = real_build(broker, account_id, schedule=schedule, shadow_id=shadow_id)
        captured["runner"] = runner
        return runner

    monkeypatch.setattr(automation_routes, "_build_automation_runner", _spy_build)
    monkeypatch.setattr(automation_routes, "_drive_automation_runner", _noop_drive)

    resp = client.post(
        "/live/automation/start",
        json={"broker": "robinhood", "schedule": "30 1 * * 1-5"},
    )
    assert resp.status_code == 200
    assert resp.json()["started"] is True

    runner = captured["runner"]
    assert runner._config.schedule == "30 1 * * 1-5"


def test_start_without_schedule_uses_default(
    client: TestClient, live_runtime: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Omitting schedule falls back to the default '0 9 * * 1-5'."""
    from src.live.paths import broker_dir

    _write_mandate_with_bounds(broker_dir("robinhood"))
    save_policy(AutomationPolicy(mode=AutomationMode.PAPER_AUTO), "robinhood")

    captured: dict = {}
    real_build = automation_routes._build_automation_runner

    def _spy_build(broker: str, account_id: Optional[str] = None, schedule: Optional[str] = None, shadow_id: Optional[str] = None) -> Any:
        runner = real_build(broker, account_id, schedule=schedule, shadow_id=shadow_id)
        captured["runner"] = runner
        return runner

    monkeypatch.setattr(automation_routes, "_build_automation_runner", _spy_build)
    monkeypatch.setattr(automation_routes, "_drive_automation_runner", _noop_drive)

    resp = client.post("/live/automation/start", json={"broker": "robinhood"})
    assert resp.status_code == 200

    runner = captured["runner"]
    assert runner._config.schedule == "0 9 * * 1-5"
