"""Route-level tests for the automation control-plane endpoints.

Exercises the REST surface mounted by ``register_automation_routes``:
- ``GET  /live/automation/status``
- ``POST /live/automation/enable``
- ``POST /live/automation/disable``

All tests run against a tmp runtime root with monkeypatched stores — no real
broker or mandate infrastructure is touched.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import api_server
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
# Fixtures                                                                     #
# --------------------------------------------------------------------------- #


@pytest.fixture
def live_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect runtime root to tmp_path."""
    monkeypatch.setattr(paths, "get_runtime_root", lambda: tmp_path)
    return tmp_path


@pytest.fixture
def client(live_runtime: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """TestClient with loopback identity (require_auth passes for local)."""
    monkeypatch.delenv("API_AUTH_KEY", raising=False)
    monkeypatch.setattr(api_server, "_API_KEY", "", raising=False)
    return TestClient(api_server.app, client=("127.0.0.1", 50000))


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


# --------------------------------------------------------------------------- #
# GET /live/automation/status                                                  #
# --------------------------------------------------------------------------- #


def test_status_default_disabled(client: TestClient) -> None:
    """No policy file, no mandate → disabled, not active."""
    resp = client.get("/live/automation/status", params={"broker": "robinhood"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["policy_mode"] == "disabled"
    assert body["has_mandate"] is False
    assert body["has_automation_bounds"] is False
    assert body["effectively_active"] is False


def test_status_with_policy_and_mandate(client: TestClient, live_runtime: Path) -> None:
    """Policy paper_auto + v2 mandate → effectively_active=True."""
    from src.live.paths import broker_dir

    _write_mandate_with_bounds(broker_dir("robinhood"))
    save_policy(AutomationPolicy(mode=AutomationMode.PAPER_AUTO), "robinhood")

    resp = client.get("/live/automation/status", params={"broker": "robinhood"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["policy_mode"] == "paper_auto"
    assert body["has_mandate"] is True
    assert body["has_automation_bounds"] is True
    assert body["mandate_expired"] is False
    assert body["effectively_active"] is True


def test_status_v1_mandate_not_active(client: TestClient, live_runtime: Path) -> None:
    """Policy paper_auto + v1 mandate (no bounds) → not effectively active."""
    from src.live.paths import broker_dir

    _write_mandate_v1(broker_dir("robinhood"))
    save_policy(AutomationPolicy(mode=AutomationMode.PAPER_AUTO), "robinhood")

    resp = client.get("/live/automation/status", params={"broker": "robinhood"})
    body = resp.json()
    assert body["has_automation_bounds"] is False
    assert body["effectively_active"] is False


# --------------------------------------------------------------------------- #
# POST /live/automation/enable                                                 #
# --------------------------------------------------------------------------- #


def test_enable_ack_false_returns_400(client: TestClient) -> None:
    """ack must be true."""
    resp = client.post(
        "/live/automation/enable",
        json={"broker": "robinhood", "mode": "paper_auto", "ack": False},
    )
    assert resp.status_code == 400
    assert "ack" in resp.json()["detail"].lower()


def test_enable_no_mandate_returns_409(client: TestClient) -> None:
    """No committed mandate → 409."""
    resp = client.post(
        "/live/automation/enable",
        json={"broker": "robinhood", "mode": "paper_auto", "ack": True},
    )
    assert resp.status_code == 409
    assert "mandate" in resp.json()["detail"].lower()


def test_enable_v1_mandate_returns_409(client: TestClient, live_runtime: Path) -> None:
    """v1 mandate (no automation_bounds) → 409."""
    from src.live.paths import broker_dir

    _write_mandate_v1(broker_dir("robinhood"))
    resp = client.post(
        "/live/automation/enable",
        json={"broker": "robinhood", "mode": "paper_auto", "ack": True},
    )
    assert resp.status_code == 409
    assert "automation_bounds" in resp.json()["detail"]


def test_enable_expired_mandate_returns_409(client: TestClient, live_runtime: Path) -> None:
    """Expired mandate → 409."""
    from src.live.paths import broker_dir

    expired = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    _write_mandate_with_bounds(broker_dir("robinhood"), expires_at=expired)
    resp = client.post(
        "/live/automation/enable",
        json={"broker": "robinhood", "mode": "paper_auto", "ack": True},
    )
    assert resp.status_code == 409
    assert "expired" in resp.json()["detail"].lower()


def test_enable_kill_switch_returns_409(client: TestClient, live_runtime: Path) -> None:
    """Kill switch tripped → 409."""
    from src.live.halt import trip_halt
    from src.live.paths import broker_dir

    _write_mandate_with_bounds(broker_dir("robinhood"))
    trip_halt(by="cli", reason="test")
    resp = client.post(
        "/live/automation/enable",
        json={"broker": "robinhood", "mode": "paper_auto", "ack": True},
    )
    assert resp.status_code == 409
    assert "kill switch" in resp.json()["detail"].lower()


def test_enable_success(client: TestClient, live_runtime: Path) -> None:
    """Valid mandate + bounds + ack=True → 200, mode updated."""
    from src.live.automation.policy_store import load_policy
    from src.live.paths import broker_dir

    _write_mandate_with_bounds(broker_dir("robinhood"))
    resp = client.post(
        "/live/automation/enable",
        json={"broker": "robinhood", "mode": "live_bounded", "ack": True},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["enabled"] is True
    assert body["mode"] == "live_bounded"

    # Verify persisted.
    loaded = load_policy("robinhood")
    assert loaded.mode is AutomationMode.LIVE_BOUNDED


def test_enable_invalid_mode_returns_422(client: TestClient) -> None:
    """Mode not in {paper_auto, live_bounded} → 422 validation error."""
    resp = client.post(
        "/live/automation/enable",
        json={"broker": "robinhood", "mode": "yolo", "ack": True},
    )
    assert resp.status_code == 422


# --------------------------------------------------------------------------- #
# POST /live/automation/disable                                                #
# --------------------------------------------------------------------------- #


def test_disable_sets_mode_disabled(client: TestClient, live_runtime: Path) -> None:
    """Disable always succeeds and sets mode=disabled."""
    from src.live.automation.policy_store import load_policy

    # Start from an enabled state.
    save_policy(AutomationPolicy(mode=AutomationMode.PAPER_AUTO), "robinhood")

    resp = client.post(
        "/live/automation/disable",
        json={"broker": "robinhood"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["enabled"] is False
    assert body["mode"] == "disabled"

    loaded = load_policy("robinhood")
    assert loaded.mode is AutomationMode.DISABLED


def test_disable_preserves_other_fields(client: TestClient, live_runtime: Path) -> None:
    """Disable keeps thresholds intact, only mode changes."""
    from src.live.automation.policy_store import load_policy

    save_policy(
        AutomationPolicy(
            mode=AutomationMode.LIVE_BOUNDED,
            min_signal_score=0.9,
            max_orders_per_cycle=2,
        ),
        "robinhood",
    )
    resp = client.post("/live/automation/disable", json={"broker": "robinhood"})
    assert resp.status_code == 200

    loaded = load_policy("robinhood")
    assert loaded.mode is AutomationMode.DISABLED
    assert loaded.min_signal_score == 0.9
    assert loaded.max_orders_per_cycle == 2
