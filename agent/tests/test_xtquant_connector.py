"""Unit tests for xtquant broker connector (all mocked, no real xtquant)."""

from __future__ import annotations

import json
import platform
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.trading.connectors.xtquant.sdk import (
    XtQuantConfig,
    XtQuantConfigError,
    XtQuantPlatformError,
    XtQuantDependencyError,
    XtQuantConnectionError,
    build_config,
    load_config,
    config_path,
    place_order,
    cancel_order,
    check_status,
    _normalize_symbol,
    _map_period,
    _map_order_status,
    _mask_id,
    _discover_account_ids,
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


class TestHelpers:
    """Internal utility functions."""

    def test_normalize_symbol_sh_6digit(self):
        assert _normalize_symbol("600036") == "600036.SH"

    def test_normalize_symbol_sz_6digit(self):
        assert _normalize_symbol("000001") == "000001.SZ"
        assert _normalize_symbol("300750") == "300750.SZ"

    def test_normalize_symbol_already_suffixed(self):
        assert _normalize_symbol("600036.SH") == "600036.SH"
        assert _normalize_symbol("000001.SZ") == "000001.SZ"

    def test_normalize_symbol_empty(self):
        assert _normalize_symbol("") == ""

    def test_map_period(self):
        assert _map_period("1d") == "1d"
        assert _map_period("1h") == "60m"
        assert _map_period("4h") == "240m"
        assert _map_period("1m") == "1m"
        assert _map_period("5m") == "5m"
        assert _map_period("1w") == "1w"
        assert _map_period("1M") == "1M"

    def test_map_order_status_pending(self):
        assert _map_order_status(48) == "pending"
        assert _map_order_status(50) == "pending"

    def test_map_order_status_filled(self):
        assert _map_order_status(54) == "filled"
        assert _map_order_status(56) == "rejected"

    def test_map_order_status_cancelled(self):
        assert _map_order_status(55) == "cancelled"
        assert _map_order_status(57) == "rejected"

    def test_map_order_status_unknown(self):
        assert _map_order_status(999) == "unknown"
        assert _map_order_status("") == "unknown"

    def test_mask_id(self):
        assert _mask_id("8800000001") == "88****01"
        assert _mask_id("1234") == "12****34"
        assert _mask_id("ab") == "****"  # len < 4 → fully masked
        assert _mask_id("") == "****"

    @patch("pathlib.Path.is_dir", return_value=True)
    @patch("pathlib.Path.iterdir")
    def test_discover_account_ids(self, mock_iterdir, _mock_is_dir):
        mock_iterdir.return_value = [
            Path("userdata_mini/8800000001"),
            Path("userdata_mini/8800000002"),
            Path("userdata_mini/settings.dat"),
        ]
        result = _discover_account_ids("D:\\QMT")
        assert result == ["8800000001", "8800000002"]

    @patch("pathlib.Path.is_dir", return_value=False)
    def test_discover_account_ids_no_path(self, _mock_is_dir):
        assert _discover_account_ids("") == []
        assert _discover_account_ids("D:\\invalid") == []


class TestCheckStatus:
    """Health check reports."""

    @patch("platform.system", return_value="Linux")
    def test_non_windows(self, _mock):
        result = check_status(XtQuantConfig())
        assert result["status"] == "error"
        assert "Windows" in result["error"]

    def test_no_mini_qmt_path(self):
        result = check_status(XtQuantConfig(mini_qmt_path=""))
        assert result["status"] == "error"


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
        # Place an order first so it exists in the paper engine
        place_result = place_order(config, "000001.SZ", "buy", quantity=100)
        order_id = place_result["order_id"]
        result = cancel_order(config, order_id)
        assert result["status"] == "ok"
        assert result.get("paper") is True


class TestLiveOrderValidation:
    """Live order input validation (no real SDK)."""

    @mock.patch("src.trading.connectors.xtquant.sdk._check_platform")
    @mock.patch("src.trading.connectors.xtquant.sdk._ensure_connected")
    @mock.patch("src.trading.connectors.xtquant.sdk._import_xtquant")
    def test_invalid_side(self, _mock_import, _mock_connected, _mock_platform):
        config = XtQuantConfig(
            profile="live", transport="native",
            mini_qmt_path="D:\\QMT", account_id="123",
        )
        result = place_order(config, "000001.SZ", "invalid", quantity=100)
        assert result["status"] == "error"
        assert "side" in result["error"]

    @mock.patch("src.trading.connectors.xtquant.sdk._check_platform")
    @mock.patch("src.trading.connectors.xtquant.sdk._ensure_connected")
    @mock.patch("src.trading.connectors.xtquant.sdk._import_xtquant")
    def test_zero_quantity(self, _mock_import, _mock_connected, _mock_platform):
        config = XtQuantConfig(
            profile="live", transport="native",
            mini_qmt_path="D:\\QMT", account_id="123",
        )
        result = place_order(config, "000001.SZ", "buy", quantity=0)
        assert result["status"] == "error"
        assert "quantity" in result["error"]


class TestConfigPath:
    """File-system path helpers."""

    def test_config_path_value(self):
        path = config_path()
        assert path.name == "xtquant.json"
        assert "vibe-trading" in str(path)


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


# ============================================================================
# HTTP bridge tests (merged from test_xtquant_http_connector.py)
# ============================================================================


class TestXtQuantConfigHttpFields:
    """HTTP bridge fields on the unified XtQuantConfig."""

    def test_http_defaults(self):
        cfg = XtQuantConfig()
        assert cfg.transport == "auto"
        assert cfg.bridge_url == "http://host.docker.internal:8888"
        assert cfg.bearer_token == ""
        assert cfg.max_retries == 3

    def test_http_config_explicit_fields(self):
        cfg = XtQuantConfig(
            transport="http",
            bridge_url="http://custom:9999",
            bearer_token="test-token",
            max_retries=5,
            timeout=15.0,
        )
        assert cfg.transport == "http"
        assert cfg.bridge_url == "http://custom:9999"
        assert cfg.bearer_token == "test-token"
        assert cfg.max_retries == 5

    def test_from_mapping_with_http_fields(self):
        cfg = XtQuantConfig.from_mapping({
            "transport": "http",
            "bridge_url": "http://bridge:8888",
            "bearer_token": "abc123",
            "max_retries": 2,
        })
        assert cfg.transport == "http"
        assert cfg.bridge_url == "http://bridge:8888"
        assert cfg.bearer_token == "abc123"
        assert cfg.max_retries == 2

    def test_with_overrides_http_fields(self):
        cfg = XtQuantConfig(profile="paper")
        new = cfg.with_overrides(
            transport="http",
            bridge_url="http://overridden:9999",
            bearer_token="override-token",
            max_retries=10,
        )
        assert new.transport == "http"
        assert new.bridge_url == "http://overridden:9999"
        assert new.bearer_token == "override-token"
        assert new.max_retries == 10


class TestTransportResolution:
    """Tests for _resolve_transport."""

    @mock.patch("platform.system", return_value="Windows")
    def test_auto_on_windows_returns_native(self, _mock):
        from src.trading.connectors.xtquant.sdk import _resolve_transport
        cfg = XtQuantConfig(transport="auto")
        assert _resolve_transport(cfg) == "native"

    @mock.patch("platform.system", return_value="Linux")
    def test_auto_on_linux_returns_http(self, _mock):
        from src.trading.connectors.xtquant.sdk import _resolve_transport
        cfg = XtQuantConfig(transport="auto")
        assert _resolve_transport(cfg) == "http"

    def test_explicit_native(self):
        from src.trading.connectors.xtquant.sdk import _resolve_transport
        cfg = XtQuantConfig(transport="native")
        assert _resolve_transport(cfg) == "native"

    def test_explicit_http(self):
        from src.trading.connectors.xtquant.sdk import _resolve_transport
        cfg = XtQuantConfig(transport="http")
        assert _resolve_transport(cfg) == "http"


class TestLoadHttpConfig:
    """Tests for _load_http_config."""

    def test_loads_from_env(self):
        from src.trading.connectors.xtquant.sdk import _load_http_config

        with mock.patch.dict("os.environ", {
            "XTQUANT_BRIDGE_URL": "http://mybridge:9999",
            "XTQUANT_BEARER_TOKEN": "my-token",
            "XTQUANT_MINI_QMT_PATH": "D:\\QMT",
            "XTQUANT_ACCOUNT_ID": "888888",
            "XTQUANT_ACCOUNT_TYPE": "STOCK",
            "XTQUANT_PROFILE": "live",
            "XTQUANT_HTTP_TIMEOUT": "30.0",
        }):
            cfg = _load_http_config()
            assert cfg.transport == "http"
            assert cfg.bridge_url == "http://mybridge:9999"
            assert cfg.bearer_token == "my-token"
            assert cfg.mini_qmt_path == "D:\\QMT"
            assert cfg.account_id == "888888"
            assert cfg.profile == "live"
            assert cfg.timeout == 30.0

    def test_defaults_when_no_env(self):
        from src.trading.connectors.xtquant.sdk import _load_http_config

        with mock.patch.dict("os.environ", {}, clear=True):
            cfg = _load_http_config()
            assert cfg.transport == "http"
            assert cfg.bridge_url == "http://host.docker.internal:8888"
            assert cfg.bearer_token == ""
            assert cfg.profile == "paper"


class TestHttpCheckStatus:
    """Tests for check_status with HTTP transport."""

    def test_health_check_success(self):
        cfg = XtQuantConfig(
            transport="http",
            bridge_url="http://test:8888",
        )

        with mock.patch("src.trading.connectors.xtquant.sdk.httpx") as mock_httpx:
            mock_resp = mock.MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {
                "status": "ok",
                "platform": "win32",
                "xtquant_installed": True,
                "connected": True,
            }
            mock_httpx.get.return_value = mock_resp

            result = check_status(cfg)
            assert result["status"] == "ok"
            assert result["bridge"]["connected"] is True

    def test_health_check_xtquant_missing(self):
        cfg = XtQuantConfig(
            transport="http",
            bridge_url="http://test:8888",
        )

        with mock.patch("src.trading.connectors.xtquant.sdk.httpx") as mock_httpx:
            mock_resp = mock.MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {
                "status": "ok",
                "platform": "linux",
                "xtquant_installed": False,
                "connected": False,
            }
            mock_httpx.get.return_value = mock_resp

            result = check_status(cfg)
            assert result["status"] == "error"

    def test_health_check_connection_error(self):
        cfg = XtQuantConfig(
            transport="http",
            bridge_url="http://test:8888",
        )

        with mock.patch("src.trading.connectors.xtquant.sdk.httpx") as mock_httpx:
            mock_httpx.ConnectError = type("ConnectError", (Exception,), {})
            mock_httpx.get.side_effect = mock_httpx.ConnectError("Connection refused")

            result = check_status(cfg)
            assert result["status"] == "error"
            assert "Cannot connect" in result["error"]


class TestHttpGetAccountSnapshot:
    """Tests for get_account_snapshot with HTTP transport."""

    def test_success(self):
        cfg = XtQuantConfig(
            transport="http",
            bridge_url="http://test:8888",
            account_id="666632400907",
        )

        with mock.patch("src.trading.connectors.xtquant.sdk.httpx") as mock_httpx:
            mock_resp = mock.MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {
                "status": "ok",
                "account_id": "666632400907",
                "account_type": "STOCK",
                "total_value": 100000.0,
                "cash": 50000.0,
                "buying_power": 50000.0,
                "market_value": 50000.0,
                "frozen_cash": 0.0,
            }
            mock_httpx.post.return_value = mock_resp

            result = get_account_snapshot(cfg)
            assert result["status"] == "ok"
            assert result["total_value"] == 100000.0
            assert result["cash"] == 50000.0

    def test_bridge_returns_error(self):
        cfg = XtQuantConfig(
            transport="http",
            bridge_url="http://test:8888",
        )

        with mock.patch("src.trading.connectors.xtquant.sdk.httpx") as mock_httpx:
            mock_resp = mock.MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {
                "status": "error",
                "error": "miniQMT disconnected",
                "error_code": "MINIQMT_DISCONNECTED",
            }
            mock_httpx.post.return_value = mock_resp

            with pytest.raises(XtQuantConnectionError, match="miniQMT disconnected"):
                get_account_snapshot(cfg)


class TestHttpGetPositions:
    """Tests for get_positions with HTTP transport."""

    def test_success_with_positions(self):
        cfg = XtQuantConfig(
            transport="http",
            bridge_url="http://test:8888",
        )

        with mock.patch("src.trading.connectors.xtquant.sdk.httpx") as mock_httpx:
            mock_resp = mock.MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {
                "status": "ok",
                "account_id": "666632400907",
                "positions": [
                    {"symbol": "600036.SH", "qty": 1000, "avg_cost": 35.5,
                     "market_value": 36000.0, "current_price": 36.0,
                     "unrealized_pnl": 0.0},
                ],
            }
            mock_httpx.post.return_value = mock_resp

            result = get_positions(cfg)
            assert result["status"] == "ok"
            assert len(result["positions"]) == 1
            assert result["positions"][0]["symbol"] == "600036.SH"


class TestHttpGetOpenOrders:
    """Tests for get_open_orders with HTTP transport."""

    def test_success(self):
        cfg = XtQuantConfig(
            transport="http",
            bridge_url="http://test:8888",
        )

        with mock.patch("src.trading.connectors.xtquant.sdk.httpx") as mock_httpx:
            mock_resp = mock.MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {
                "status": "ok",
                "account_id": "666632400907",
                "orders": [],
            }
            mock_httpx.post.return_value = mock_resp

            result = get_open_orders(cfg)
            assert result["status"] == "ok"
            assert result["orders"] == []

    def test_with_executions(self):
        cfg = XtQuantConfig(
            transport="http",
            bridge_url="http://test:8888",
        )

        with mock.patch("src.trading.connectors.xtquant.sdk.httpx") as mock_httpx:
            mock_resp = mock.MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {
                "status": "ok",
                "account_id": "666632400907",
                "orders": [],
                "executions": [],
            }
            mock_httpx.post.return_value = mock_resp

            result = get_open_orders(cfg, include_executions=True)
            assert result["status"] == "ok"
            assert result["executions"] == []


class TestHttpGetQuote:
    """Tests for get_quote with HTTP transport."""

    def test_success(self):
        cfg = XtQuantConfig(
            transport="http",
            bridge_url="http://test:8888",
        )

        with mock.patch("src.trading.connectors.xtquant.sdk.httpx") as mock_httpx:
            mock_resp = mock.MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {
                "status": "ok",
                "symbol": "600036.SH",
                "last": 36.0,
                "bid": 35.98,
                "ask": 36.02,
                "volume": 12345678,
                "open": 35.5,
                "high": 36.2,
                "low": 35.3,
                "pre_close": 35.4,
                "upper_limit": 38.94,
                "lower_limit": 31.86,
            }
            mock_httpx.post.return_value = mock_resp

            result = get_quote("600036.SH", config=cfg)
            assert result["status"] == "ok"
            assert result["last"] == 36.0


class TestHttpGetHistoricalBars:
    """Tests for get_historical_bars with HTTP transport."""

    def test_success(self):
        cfg = XtQuantConfig(
            transport="http",
            bridge_url="http://test:8888",
        )

        with mock.patch("src.trading.connectors.xtquant.sdk.httpx") as mock_httpx:
            mock_resp = mock.MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {
                "status": "ok",
                "symbol": "600036.SH",
                "period": "1d",
                "bars": [
                    {"t": "2026-07-06", "o": 35.5, "h": 36.2, "l": 35.3,
                     "c": 36.0, "v": 12345678},
                ],
            }
            mock_httpx.post.return_value = mock_resp

            result = get_historical_bars(
                "600036.SH", config=cfg, period="1d", limit=90,
            )
            assert result["status"] == "ok"
            assert len(result["bars"]) == 1
            assert result["bars"][0]["c"] == 36.0

    def test_empty_bars(self):
        cfg = XtQuantConfig(
            transport="http",
            bridge_url="http://test:8888",
        )

        with mock.patch("src.trading.connectors.xtquant.sdk.httpx") as mock_httpx:
            mock_resp = mock.MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {
                "status": "ok",
                "symbol": "UNKNOWN.SH",
                "period": "1d",
                "bars": [],
            }
            mock_httpx.post.return_value = mock_resp

            result = get_historical_bars(
                "UNKNOWN.SH", config=cfg, period="1d", limit=90,
            )
            assert result["status"] == "ok"
            assert result["bars"] == []


class TestHttpRetry:
    """Tests for HTTP retry behavior via unified sdk."""

    def test_retry_on_timeout_then_succeed(self):
        cfg = XtQuantConfig(
            transport="http",
            bridge_url="http://test:8888",
            max_retries=3,
            timeout=1.0,
        )

        import httpx

        with mock.patch("src.trading.connectors.xtquant.sdk.httpx") as mock_httpx:
            mock_httpx.TimeoutException = httpx.TimeoutException
            # First call times out, second succeeds
            ok_resp = mock.MagicMock()
            ok_resp.status_code = 200
            ok_resp.json.return_value = {
                "status": "ok", "account_id": "test",
                "account_type": "STOCK", "total_value": 100.0,
                "cash": 50.0, "buying_power": 50.0,
                "market_value": 50.0, "frozen_cash": 0.0,
            }
            mock_httpx.post.side_effect = [
                httpx.TimeoutException("timeout"),
                ok_resp,
            ]

            result = get_account_snapshot(cfg)
            assert result["status"] == "ok"
            assert mock_httpx.post.call_count == 2

    def test_all_retries_exhausted(self):
        cfg = XtQuantConfig(
            transport="http",
            bridge_url="http://test:8888",
            max_retries=2,
            timeout=1.0,
        )

        import httpx

        with mock.patch("src.trading.connectors.xtquant.sdk.httpx") as mock_httpx:
            mock_httpx.TimeoutException = httpx.TimeoutException
            mock_httpx.post.side_effect = httpx.TimeoutException("timeout")

            with pytest.raises(XtQuantConnectionError, match="retries"):
                get_account_snapshot(cfg)
            assert mock_httpx.post.call_count == 2

    def test_401_raises_config_error(self):
        cfg = XtQuantConfig(
            transport="http",
            bridge_url="http://test:8888",
        )

        with mock.patch("src.trading.connectors.xtquant.sdk.httpx") as mock_httpx:
            mock_resp = mock.MagicMock()
            mock_resp.status_code = 401
            mock_resp.json.return_value = {"detail": "Invalid token"}
            mock_httpx.post.return_value = mock_resp

            with pytest.raises(XtQuantConfigError, match="401"):
                get_account_snapshot(cfg)


class TestProfilesAfterMerge:
    """Verify xtquant profiles are still defined after merge."""

    def test_profiles_exist(self):
        from src.trading.connectors.xtquant.profiles import XTQUANT_PROFILES

        ids = [p.id for p in XTQUANT_PROFILES]
        assert "xtquant-paper" in ids
        assert "xtquant-paper-trade" in ids
        assert "xtquant-live-readonly" in ids
        assert "xtquant-live-trade" in ids
        assert "xtquant-http-paper" in ids
        assert "xtquant-http-paper-trade" in ids
        assert "xtquant-http-live-readonly" in ids
        assert "xtquant-http-live-trade" in ids

    def test_all_profiles_exist(self):
        from src.trading.connectors.xtquant.profiles import XTQUANT_PROFILES
        assert len(XTQUANT_PROFILES) == 8
