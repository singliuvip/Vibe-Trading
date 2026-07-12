"""End-to-end tests for virtual paper trade risk controls.

Covers scope resolution, mandate isolation, daily count isolation, and
kill switch enforcement for virtual broker accounts (US + CN).
These are integration tests that touch the filesystem
(``~/.vibe-trading/live/``) and should be run in a clean environment.
"""

from __future__ import annotations

import pytest

from src.live.risk_scope import resolve_scope
from src.live.halt import clear_halt, halt_flag_set, trip_halt
from src.live.daily_count import increment_daily_count, read_daily_count

pytestmark = pytest.mark.integration


def _posix_str(p) -> str:
    """Convert a native path to a forward-slash string for cross-platform asserts."""
    return p.as_posix()


class TestVirtualRiskScope:
    """Test scope resolution for virtual accounts."""

    def test_us_scope(self):
        scope = resolve_scope("virtual", "default")
        assert scope.broker == "virtual"
        assert scope.account_id == "default"
        assert _posix_str(scope.mandate_dir).endswith("live/virtual/default")

    def test_cn_scope(self):
        scope = resolve_scope("virtual", "cn-default")
        assert scope.account_id == "cn-default"
        assert _posix_str(scope.mandate_dir).endswith("live/virtual/cn-default")

    def test_real_broker_scope(self):
        scope = resolve_scope("robinhood")
        assert scope.account_id == "default"
        assert _posix_str(scope.mandate_dir).endswith("live/robinhood/default")

    def test_scope_paths_includes_mandate_file(self):
        scope = resolve_scope("virtual", "default")
        assert _posix_str(scope.mandate_path).endswith("live/virtual/default/mandate.json")

    def test_scope_counter_path(self):
        scope = resolve_scope("virtual", "cn-default")
        assert _posix_str(scope.counter_path).endswith(
            "live/virtual/cn-default/trade_counter.json"
        )


class TestVirtualMandateIsolation:
    """Test that US and CN virtual accounts have independent mandates."""

    def test_mandate_paths_differ(self):
        us = resolve_scope("virtual", "default")
        cn = resolve_scope("virtual", "cn-default")
        assert us.mandate_path != cn.mandate_path
        assert us.counter_path != cn.counter_path

    def test_scope_account_ids_differ(self):
        us = resolve_scope("virtual", "default")
        cn = resolve_scope("virtual", "cn-default")
        assert us.account_id != cn.account_id


class TestVirtualDailyCountIsolation:
    """Test that US and CN counters are independent."""

    def test_counters_isolated(self):
        # Read initial state — the counter is date-keyed, so reads are safe.
        us_before = read_daily_count("virtual", "default")
        cn_before = read_daily_count("virtual", "cn-default")

        # Increment US only.
        us_after = increment_daily_count("virtual", "default")
        assert us_after == us_before + 1

        # CN counter must be unchanged.
        cn_after = read_daily_count("virtual", "cn-default")
        assert cn_after == cn_before

        # Now increment CN.
        cn_after2 = increment_daily_count("virtual", "cn-default")
        assert cn_after2 == cn_before + 1

        # US must still be at its incremented value.
        us_check = read_daily_count("virtual", "default")
        assert us_check == us_after


class TestVirtualKillSwitch:
    """Test kill switch affects virtual broker."""

    def test_halt_blocks_virtual(self):
        broker = "virtual"
        clear_halt(broker)  # ensure clean start

        assert halt_flag_set(broker) is False

        trip_halt("test", "testing halt for virtual", broker=broker)
        assert halt_flag_set(broker) is True

        clear_halt(broker)
        assert halt_flag_set(broker) is False

    def test_global_halt_blocks_virtual(self):
        clear_halt()  # clear global

        assert halt_flag_set("virtual") is False

        trip_halt("test", "testing global halt")
        assert halt_flag_set("virtual") is True

        clear_halt()
        assert halt_flag_set("virtual") is False

    def test_per_broker_halt_does_not_affect_other(self):
        clear_halt("virtual")
        clear_halt("robinhood")

        trip_halt("test", "halt virtual only", broker="virtual")
        assert halt_flag_set("virtual") is True
        assert halt_flag_set("robinhood") is False

        clear_halt("virtual")
        clear_halt("robinhood")


class TestVirtualMandateMigration:
    """Test legacy mandate migration (T9)."""

    def test_migrate_legacy_mandate_no_files(self):
        from src.live.mandate.store import migrate_legacy_mandate

        # When neither legacy nor scoped file exists, migration is a no-op.
        # The function should return False safely.
        # (We can't easily test with real files in a unit context,
        #  but we can verify the function exists and is callable.)
        assert callable(migrate_legacy_mandate)

    def test_load_mandate_accepts_account_id(self):
        from src.live.mandate.store import load_mandate

        # When no mandate exists for a scoped path, returns None (doesn't crash).
        result = load_mandate("virtual", account_id="nonexistent-test-scope")
        assert result is None


class TestSDKGateOnboardingGuidance:
    """Test that blocked orders include onboarding guidance (T8)."""

    def test_no_mandate_refusal_includes_onboarding(self):
        """Verify _refusal includes onboarding when reason is 'no valid mandate'."""
        from src.live.sdk_order_gate import _refusal

        result = _refusal(
            "virtual",
            decision="deny",
            reason="no valid mandate on file",
            reauth=False,
        )
        assert result["status"] == "blocked"
        assert "onboarding" in result
        assert result["onboarding"]["action"] == "propose_mandate_profiles"
        assert "propose_mandate_profiles" in str(result["onboarding"]["hint"])

    def test_other_refusal_no_onboarding(self):
        """Verify _refusal does NOT include onboarding for non-mandate reasons."""
        from src.live.sdk_order_gate import _refusal

        result = _refusal(
            "virtual",
            decision="deny",
            reason="live trading halted",
            reauth=False,
        )
        assert result["status"] == "blocked"
        assert "onboarding" not in result
