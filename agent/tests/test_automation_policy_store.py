"""Tests for the durable automation policy store (fail-closed, atomic, 0600)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import src.live.paths as paths
from src.live.automation.policy import AutomationMode, AutomationPolicy
from src.live.automation.policy_store import _parse_policy, load_policy, policy_path, save_policy


@pytest.fixture
def live_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the live root at a tmp dir so tests never touch the real store."""
    monkeypatch.setattr(paths, "get_runtime_root", lambda: tmp_path)
    return tmp_path


# --------------------------------------------------------------------------- #
# load_policy: fail-closed defaults                                            #
# --------------------------------------------------------------------------- #


def test_load_missing_file_returns_disabled(live_runtime: Path) -> None:
    """No file on disk → default DISABLED policy."""
    policy = load_policy("robinhood")
    assert policy.mode is AutomationMode.DISABLED
    assert policy.min_signal_score == 0.7
    assert policy.max_orders_per_cycle == 5


def test_load_malformed_json_returns_disabled(live_runtime: Path) -> None:
    """Corrupt JSON → fail-closed DISABLED (never guess)."""
    path = policy_path("robinhood")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not valid json!!", encoding="utf-8")
    policy = load_policy("robinhood")
    assert policy.mode is AutomationMode.DISABLED


def test_load_invalid_mode_returns_disabled(live_runtime: Path) -> None:
    """A policy with an unrecognized mode string → fail-closed DISABLED."""
    path = policy_path("robinhood")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"mode": "yolo_mode"}), encoding="utf-8")
    policy = load_policy("robinhood")
    assert policy.mode is AutomationMode.DISABLED


# --------------------------------------------------------------------------- #
# save_policy → load_policy round-trip                                         #
# --------------------------------------------------------------------------- #


def test_save_load_round_trip(live_runtime: Path) -> None:
    """Saved policy loads back with identical field values."""
    original = AutomationPolicy(
        mode=AutomationMode.PAPER_AUTO,
        min_signal_score=0.85,
        max_signal_age_seconds=120,
        require_exit_protection=False,
        allowed_strategies=("momentum", "mean_reversion"),
        max_orders_per_cycle=3,
        symbol_cooldown_seconds=7200,
    )
    path = save_policy(original, "robinhood")
    assert path.is_file()

    loaded = load_policy("robinhood")
    assert loaded.mode is AutomationMode.PAPER_AUTO
    assert loaded.min_signal_score == 0.85
    assert loaded.max_signal_age_seconds == 120
    assert loaded.require_exit_protection is False
    assert loaded.allowed_strategies == ("momentum", "mean_reversion")
    assert loaded.max_orders_per_cycle == 3
    assert loaded.symbol_cooldown_seconds == 7200


def test_save_creates_0600_file(live_runtime: Path) -> None:
    """The persisted policy file has 0600 permissions (owner-only)."""
    policy = AutomationPolicy(mode=AutomationMode.LIVE_BOUNDED)
    path = save_policy(policy, "robinhood")
    # On Windows os.chmod is limited; verify the file exists and is readable.
    assert path.is_file()
    # On POSIX, check permission bits.
    import os
    import sys

    if sys.platform != "win32":
        mode_bits = os.stat(path).st_mode & 0o777
        assert mode_bits == 0o600


def test_save_with_account_id(live_runtime: Path) -> None:
    """Policy is scoped per account_id."""
    policy = AutomationPolicy(mode=AutomationMode.PAPER_AUTO)
    path = save_policy(policy, "robinhood", account_id="acct-1")
    assert "acct-1" in str(path)
    loaded = load_policy("robinhood", account_id="acct-1")
    assert loaded.mode is AutomationMode.PAPER_AUTO
    # Default scope remains DISABLED.
    assert load_policy("robinhood").mode is AutomationMode.DISABLED


# --------------------------------------------------------------------------- #
# _parse_policy validation                                                     #
# --------------------------------------------------------------------------- #


def test_parse_policy_non_dict_raises() -> None:
    """Root must be a JSON object."""
    with pytest.raises(TypeError, match="JSON object"):
        _parse_policy([1, 2, 3])


def test_parse_policy_invalid_mode_raises() -> None:
    """An unknown mode string raises ValueError (from Enum constructor)."""
    with pytest.raises(ValueError):
        _parse_policy({"mode": "not_a_mode"})


def test_parse_policy_defaults() -> None:
    """Minimal dict → defaults filled in."""
    policy = _parse_policy({"mode": "disabled"})
    assert policy.mode is AutomationMode.DISABLED
    assert policy.min_signal_score == 0.7
    assert policy.max_signal_age_seconds == 300
