"""Unit tests for xtquant broker connector (all mocked, no real xtquant)."""

from __future__ import annotations

import json
import platform
from unittest import mock
from unittest.mock import MagicMock, patch

import pytest

from src.trading.connectors.xtquant.sdk import (
    XtQuantConfig,
    XtQuantConfigError,
    XtQuantPlatformError,
    XtQuantDependencyError,
    XtQuantConnectionError,
    build_config,
    cancel_order,
    check_status,
    get_account_snapshot,
    get_historical_bars,
    get_quote,
    get_today_trades,
    load_config,
    place_order,
    probe_connection,
)


class TestXtQuantConfig:
    """Configuration parsing and validation."""

    def test_defaults(self):
        cfg = XtQuantConfig()
        assert cfg.profile == "paper"
        assert cfg.readonly is True
        assert cfg.account_type == "STOCK"
        assert cfg.timeout == 10.0
        assert cfg.is_live is False

    def test_from_mapping_paper(self):
        cfg = XtQuantConfig.from_mapping({"profile": "paper"})
        assert cfg.environment == "paper"
        assert cfg.is_live is False

    def test_from_mapping_live(self):
        cfg = XtQuantConfig.from_mapping({"profile": "live"})
        assert cfg.environment == "live"
        assert cfg.is_live is True

    def test_from_mapping_invalid_profile(self):
        with pytest.raises(XtQuantConfigError):
            XtQuantConfig.from_mapping({"profile": "invalid"})

    def test_from_mapping_all_fields(self):
        data = {
            "mini_qmt_path": "D:\\QMT",
            "account_id": "8800000001",
            "account_type": "STOCK",
            "profile": "live-readonly",
            "session_id": 12345,
            "timeout": 30.0,
            "readonly": True,
        }
        cfg = XtQuantConfig.from_mapping(data)
        assert cfg.mini_qmt_path == "D:\\QMT"
        assert cfg.account_id == "8800000001"
        assert cfg.session_id == 12345
        assert cfg.timeout == 30.0

    def test_with_overrides(self):
        cfg = XtQuantConfig(profile="paper")
        new_cfg = cfg.with_overrides(account_id="999", timeout=5.0)
        assert new_cfg.account_id == "999"
        assert new_cfg.timeout == 5.0
        assert cfg.account_id == ""  # original unchanged
        assert cfg.timeout == 10.0   # original unchanged


class TestBuildConfig:
    """Config resolution chain."""

    def test_defaults(self):
        cfg = build_config({}, {})
        assert cfg.profile == "paper"

    def test_profile_overrides_default(self):
        cfg = build_config({"profile": "live"}, {})
        assert cfg.environment == "live"

    def test_cli_overrides_profile(self):
        cfg = build_config({"profile": "paper"}, {"account_id": "888888"})
        assert cfg.account_id == "888888"

    def test_cli_skips_none(self):
        cfg = build_config({"profile": "paper"}, {"account_id": None, "timeout": ""})
        assert cfg.profile == "paper"  # no crash


class TestCheckStatus:
    """Health check reports."""

    @patch("platform.system", return_value="Linux")
    def test_non_windows(self, _mock):
        result = check_status(XtQuantConfig())
        assert result["status"] == "error"
        assert "Windows" in result["error"]

    def test_no_mini_qmt_path(self):
        result = check_status(XtQuantConfig(mini_qmt_path=""))
        # Paper profile doesn't require miniQMT path.
        assert result["status"] == "ok"
        assert result.get("note", "").startswith("paper")


class TestPlaceOrderPaper:
    """Paper mode order placement — MUST NOT touch xtquant SDK."""

    def test_paper_buy_returns_success(self):
        config = XtQuantConfig(profile="paper")
        result = place_order(config, "000001.SZ", "buy", quantity=100)
        assert result["status"] == "ok"
        assert "order_id" in result
        assert result.get("paper") is True

    def test_paper_sell_returns_success(self):
        config = XtQuantConfig(profile="paper")
        result = place_order(config, "600036.SH", "sell", quantity=200, order_type="limit", limit_price=15.50)
        assert result["status"] == "ok"
        assert result.get("paper") is True

    def test_paper_cancel_returns_success(self):
        config = XtQuantConfig(profile="paper")
        # Place a limit order (won't fill) so it stays pending and can be cancelled.
        place_result = place_order(
            config, "000001.SZ", "buy", quantity=100,
            order_type="limit", limit_price=0.01,
        )
        order_id = place_result["order_id"]
        result = cancel_order(config, order_id)
        assert result["status"] == "ok"
        assert result.get("paper") is True


class TestLoadConfig:
    """Disk config loading."""

    @patch("pathlib.Path.exists", return_value=False)
    def test_no_file_returns_defaults(self, _mock):
        cfg = load_config()
        assert cfg.profile == "paper"

    @patch("pathlib.Path.exists", return_value=True)
    @patch("pathlib.Path.read_text")
    def test_valid_file(self, mock_read, _mock):
        mock_read.return_value = json.dumps({
            "mini_qmt_path": "D:\\QMT",
            "account_id": "8800000001",
            "profile": "live",
            "timeout": 20.0,
        })
        cfg = load_config()
        assert cfg.mini_qmt_path == "D:\\QMT"
        assert cfg.account_id == "8800000001"
        assert cfg.profile == "live"
        assert cfg.timeout == 20.0

    @patch("pathlib.Path.exists", return_value=True)
    @patch("pathlib.Path.read_text")
    def test_invalid_json_raises(self, mock_read, _mock):
        mock_read.return_value = "not valid json {{{"
        with pytest.raises(XtQuantConfigError):
            load_config()


class TestProfilesAfterMerge:
    """Verify xtquant profiles are still defined after merge."""

    def test_profiles_exist(self):
        from src.trading.connectors.xtquant.profiles import XTQUANT_PROFILES

        ids = [p.id for p in XTQUANT_PROFILES]
        assert "xtquant-paper" in ids
        assert "xtquant-paper-trade" in ids
        assert "xtquant-live-readonly" in ids
        assert "xtquant-live-trade" in ids

    def test_all_profiles_exist(self):
        from src.trading.connectors.xtquant.profiles import XTQUANT_PROFILES
        assert len(XTQUANT_PROFILES) == 4


class TestTPlusOneCheck:
    """Tests for T+1 settlement rule check."""

    def test_buy_not_checked(self):
        from src.live.a_stock_guard import check_t_plus_1
        result = check_t_plus_1("600036.SH", "buy", [])
        assert result is None

    def test_sell_no_today_buys_allowed(self):
        from src.live.a_stock_guard import check_t_plus_1
        result = check_t_plus_1("600036.SH", "sell", [])
        assert result is None

    def test_sell_with_today_buy_blocked(self):
        from src.live.a_stock_guard import check_t_plus_1
        today_buys = [{"symbol": "600036.SH", "side": "buy"}]
        result = check_t_plus_1("600036.SH", "sell", today_buys)
        assert result is not None
        assert "T+1" in result

    def test_sell_different_symbol_allowed(self):
        from src.live.a_stock_guard import check_t_plus_1
        today_buys = [{"symbol": "000001.SZ", "side": "buy"}]
        result = check_t_plus_1("600036.SH", "sell", today_buys)
        assert result is None

    def test_etf_t0_exemption(self):
        from src.live.a_stock_guard import check_t_plus_1, is_t0_eligible
        assert is_t0_eligible("510050.SH") is True
        assert is_t0_eligible("159915.SZ") is True
        assert is_t0_eligible("600036.SH") is False
        # ETF sell with today buy should be allowed (T+0)
        today_buys = [{"symbol": "510050.SH", "side": "buy"}]
        result = check_t_plus_1("510050.SH", "sell", today_buys)
        assert result is None

    def test_convertible_bond_t0(self):
        from src.live.a_stock_guard import is_t0_eligible
        assert is_t0_eligible("110044.SH") is True
        assert is_t0_eligible("123456.SZ") is True


class TestGetTodayTrades:
    """Tests for get_today_trades."""

    def test_paper_returns_empty(self):
        cfg = XtQuantConfig(profile="paper")
        result = get_today_trades(cfg)
        assert result["status"] == "ok"
        assert result["trades"] == []

    @mock.patch("src.trading.connectors.xtquant.sdk._ensure_connected")
    @mock.patch("src.trading.connectors.xtquant.sdk._to_stock_account")
    def test_live_returns_trades(self, mock_acc, mock_connect):
        cfg = XtQuantConfig(profile="live-readonly", account_id="1000003")

        # Mock trade record
        mock_trade = mock.MagicMock()
        mock_trade.stock_code = "600036.SH"
        mock_trade.direction = 1  # buy
        mock_trade.trade_time = "20260707 10:30:00"

        mock_trader = mock.MagicMock()
        mock_trader.get_order_stock_trades.return_value = [mock_trade]
        mock_connect.return_value = (mock_trader, None)
        mock_acc.return_value = mock.MagicMock()

        result = get_today_trades(cfg)
        assert result["status"] == "ok"
        assert len(result["trades"]) == 1
        assert result["trades"][0]["symbol"] == "600036.SH"
        assert result["trades"][0]["side"] == "buy"


class TestCheckStatusLiveFields:
    """check_status live-mode extra fields."""

    @mock.patch("src.trading.connectors.xtquant.sdk._ensure_connected")
    @mock.patch("src.trading.connectors.xtquant.sdk._resolve_account_id", return_value="10888003")
    @mock.patch("src.trading.connectors.xtquant.sdk._import_xtquant")
    @mock.patch("src.trading.connectors.xtquant.sdk._check_platform")
    @mock.patch("src.trading.connectors.xtquant.sdk.platform.system", return_value="Windows")
    def test_live_status_includes_extra_fields(self, _ps, _cp, _import, _resolve, _connect):
        """Verify check_status live mode returns account_type, account_id_raw, heartbeat."""
        from src.trading.connectors.xtquant.sdk import get_heartbeat_status
        cfg = XtQuantConfig(profile="live-readonly", mini_qmt_path="D:\\QMT", account_type="STOCK")
        result = check_status(cfg)
        assert result["status"] == "ok"
        assert result["connected"] is True
        assert result["account_type"] == "STOCK"
        assert result["account_id_raw"] == "10888003"
        assert "heartbeat" in result


class TestGetAccountSnapshotLive:
    """get_account_snapshot live-mode extra fields."""

    @mock.patch("src.trading.connectors.xtquant.sdk._ensure_connected")
    @mock.patch("src.trading.connectors.xtquant.sdk._resolve_account_id", return_value="10888003")
    @mock.patch("src.trading.connectors.xtquant.sdk._import_xtquant")
    @mock.patch("src.trading.connectors.xtquant.sdk._check_platform")
    @mock.patch("src.trading.connectors.xtquant.sdk.platform.system", return_value="Windows")
    def test_live_account_snapshot_includes_account_type(self, _ps, _cp, _import, _resolve, mock_connect):
        """Verify get_account_snapshot live mode returns account_type."""
        mock_trader = mock.MagicMock()
        mock_asset = mock.MagicMock()
        mock_asset.total_asset = 150000.0
        mock_asset.cash = 50000.0
        mock_asset.market_value = 100000.0
        mock_trader.query_stock_asset.return_value = mock_asset
        mock_connect.return_value = (mock_trader, None)

        cfg = XtQuantConfig(profile="live-readonly", mini_qmt_path="D:\\QMT", account_type="FUTURES")
        result = get_account_snapshot(cfg)
        assert result["status"] == "ok"
        assert result["account_type"] == "FUTURES"
        assert result["total_value"] == 150000.0


class TestProbeConnectionPaper:
    """probe_connection paper mode early return."""

    @mock.patch("src.trading.connectors.xtquant.sdk.load_config")
    def test_probe_connection_paper_returns_early(self, mock_load):
        """Verify probe_connection returns early for paper profiles without connecting."""
        mock_load.return_value = XtQuantConfig(profile="paper")
        result = probe_connection(XtQuantConfig(profile="paper"))
        assert result["status"] == "ok"
        assert result["connected"] is False
        assert "no probe needed" in result["note"]


class TestQuotePaper:
    """get_quote paper mode error."""

    def test_get_quote_paper_returns_error(self):
        """Verify get_quote returns error for paper profiles."""
        cfg = XtQuantConfig(profile="paper")
        result = get_quote("000001.SZ", config=cfg)
        assert result["status"] == "error"
        assert "live miniQMT connection" in result["error"]


class TestHistoricalBarsPaper:
    """get_historical_bars paper mode error."""

    def test_get_historical_bars_paper_returns_error(self):
        """Verify get_historical_bars returns error for paper profiles."""
        cfg = XtQuantConfig(profile="paper")
        result = get_historical_bars("000001.SZ", config=cfg, period="1d", limit=30)
        assert result["status"] == "error"
        assert "live miniQMT connection" in result["error"]
