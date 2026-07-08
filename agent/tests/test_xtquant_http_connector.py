"""Tests for xtquant HTTP bridge connector (sdk_http.py).

These tests mock the QMT Bridge HTTP API and verify that sdk_http.py correctly
translates function calls into HTTP requests and parses responses.
"""

from __future__ import annotations

import pytest

from unittest import mock


class TestXtQuantHttpConfig:
    """Tests for XtQuantHttpConfig."""

    def test_defaults(self):
        from src.trading.connectors.xtquant.http_config import XtQuantHttpConfig

        cfg = XtQuantHttpConfig()
        assert cfg.bridge_url == "http://host.docker.internal:8888"
        assert cfg.bearer_token == "qmt-ql-8f3a2d1e9c"
        assert cfg.timeout == 15.0
        assert cfg.max_retries == 3
        assert cfg.readonly is True
        assert cfg.profile == "paper"

    def test_from_env_defaults(self):
        from src.trading.connectors.xtquant.http_config import XtQuantHttpConfig

        cfg = XtQuantHttpConfig.from_env()
        assert cfg.bridge_url == "http://host.docker.internal:8888"
        assert cfg.bearer_token == "qmt-ql-8f3a2d1e9c"

    def test_from_env_with_overrides(self):
        from src.trading.connectors.xtquant.http_config import XtQuantHttpConfig

        cfg = XtQuantHttpConfig.from_env(
            bridge_url="http://custom:9999",
            bearer_token="custom-token",
            account_id="123456",
            profile="live",
        )
        assert cfg.bridge_url == "http://custom:9999"
        assert cfg.bearer_token == "custom-token"
        assert cfg.account_id == "123456"
        assert cfg.profile == "live"

    def test_auth_header(self):
        from src.trading.connectors.xtquant.http_config import XtQuantHttpConfig

        cfg = XtQuantHttpConfig(bearer_token="test-token")
        assert cfg.auth_header == {"Authorization": "Bearer test-token"}

    def test_is_live(self):
        from src.trading.connectors.xtquant.http_config import XtQuantHttpConfig

        assert XtQuantHttpConfig(profile="paper").is_live is False
        assert XtQuantHttpConfig(profile="live").is_live is True
        assert XtQuantHttpConfig(profile="live-readonly").is_live is True

    def test_to_dict_masks_token(self):
        from src.trading.connectors.xtquant.http_config import XtQuantHttpConfig

        cfg = XtQuantHttpConfig(bearer_token="qmt-ql-secret-token-123")
        d = cfg.to_dict()
        assert "secret" not in d["bearer_token"]
        assert "****" in d["bearer_token"]


class TestSdkHttpCheckStatus:
    """Tests for check_status function."""

    def test_health_check_success(self):
        from src.trading.connectors.xtquant.http_config import XtQuantHttpConfig
        from src.trading.connectors.xtquant import sdk_http

        cfg = XtQuantHttpConfig(bridge_url="http://test:8888")

        with mock.patch("httpx.get") as mock_get:
            mock_get.return_value.status_code = 200
            mock_get.return_value.json.return_value = {
                "status": "ok",
                "platform": "win32",
                "xtquant_installed": True,
                "connected": True,
            }

            result = sdk_http.check_status(cfg)
            assert result["status"] == "ok"
            assert result["bridge"]["connected"] is True

    def test_health_check_xtquant_missing(self):
        from src.trading.connectors.xtquant.http_config import XtQuantHttpConfig
        from src.trading.connectors.xtquant import sdk_http

        cfg = XtQuantHttpConfig(bridge_url="http://test:8888")

        with mock.patch("httpx.get") as mock_get:
            mock_get.return_value.status_code = 200
            mock_get.return_value.json.return_value = {
                "status": "ok",
                "platform": "linux",
                "xtquant_installed": False,
                "connected": False,
            }

            result = sdk_http.check_status(cfg)
            assert result["status"] == "error"

    def test_health_check_connection_error(self):
        from src.trading.connectors.xtquant.http_config import XtQuantHttpConfig
        from src.trading.connectors.xtquant import sdk_http

        cfg = XtQuantHttpConfig(bridge_url="http://test:8888")

        with mock.patch("httpx.get") as mock_get:
            import httpx
            mock_get.side_effect = httpx.ConnectError("Connection refused")

            result = sdk_http.check_status(cfg)
            assert result["status"] == "error"
            assert "Cannot connect" in result["error"]


class TestSdkHttpGetAccountSnapshot:
    """Tests for get_account_snapshot."""

    def test_success(self):
        from src.trading.connectors.xtquant.http_config import XtQuantHttpConfig
        from src.trading.connectors.xtquant import sdk_http

        cfg = XtQuantHttpConfig(
            bridge_url="http://test:8888",
            account_id="666632400907",
        )

        with mock.patch("httpx.post") as mock_post:
            mock_post.return_value.status_code = 200
            mock_post.return_value.json.return_value = {
                "status": "ok",
                "account_id": "666632400907",
                "account_type": "STOCK",
                "total_value": 100000.0,
                "cash": 50000.0,
                "buying_power": 50000.0,
                "market_value": 50000.0,
                "frozen_cash": 0.0,
            }

            result = sdk_http.get_account_snapshot(cfg)
            assert result["status"] == "ok"
            assert result["total_value"] == 100000.0
            assert result["cash"] == 50000.0

    def test_bridge_returns_error(self):
        from src.trading.connectors.xtquant.http_config import XtQuantHttpConfig
        from src.trading.connectors.xtquant import sdk_http
        from src.trading.connectors.xtquant.sdk_http import XtQuantConnectionError

        cfg = XtQuantHttpConfig(bridge_url="http://test:8888")

        with mock.patch("httpx.post") as mock_post:
            mock_post.return_value.status_code = 200
            mock_post.return_value.json.return_value = {
                "status": "error",
                "error": "miniQMT disconnected",
                "error_code": "MINIQMT_DISCONNECTED",
            }

            with pytest.raises(XtQuantConnectionError, match="miniQMT disconnected"):
                sdk_http.get_account_snapshot(cfg)


class TestSdkHttpGetPositions:
    """Tests for get_positions."""

    def test_success_with_positions(self):
        from src.trading.connectors.xtquant.http_config import XtQuantHttpConfig
        from src.trading.connectors.xtquant import sdk_http

        cfg = XtQuantHttpConfig(bridge_url="http://test:8888")

        with mock.patch("httpx.post") as mock_post:
            mock_post.return_value.status_code = 200
            mock_post.return_value.json.return_value = {
                "status": "ok",
                "account_id": "666632400907",
                "positions": [
                    {"symbol": "600036.SH", "qty": 1000, "avg_cost": 35.5,
                     "market_value": 36000.0, "current_price": 36.0,
                     "unrealized_pnl": 0.0},
                ],
            }

            result = sdk_http.get_positions(cfg)
            assert result["status"] == "ok"
            assert len(result["positions"]) == 1
            assert result["positions"][0]["symbol"] == "600036.SH"


class TestSdkHttpGetOpenOrders:
    """Tests for get_open_orders."""

    def test_success(self):
        from src.trading.connectors.xtquant.http_config import XtQuantHttpConfig
        from src.trading.connectors.xtquant import sdk_http

        cfg = XtQuantHttpConfig(bridge_url="http://test:8888")

        with mock.patch("httpx.post") as mock_post:
            mock_post.return_value.status_code = 200
            mock_post.return_value.json.return_value = {
                "status": "ok",
                "account_id": "666632400907",
                "orders": [],
            }

            result = sdk_http.get_open_orders(cfg)
            assert result["status"] == "ok"
            assert result["orders"] == []

    def test_with_executions(self):
        from src.trading.connectors.xtquant.http_config import XtQuantHttpConfig
        from src.trading.connectors.xtquant import sdk_http

        cfg = XtQuantHttpConfig(bridge_url="http://test:8888")

        with mock.patch("httpx.post") as mock_post:
            mock_post.return_value.status_code = 200
            mock_post.return_value.json.return_value = {
                "status": "ok",
                "account_id": "666632400907",
                "orders": [],
                "executions": [],
            }

            result = sdk_http.get_open_orders(cfg, include_executions=True)
            assert result["status"] == "ok"
            assert result["executions"] == []


class TestSdkHttpGetQuote:
    """Tests for get_quote."""

    def test_success(self):
        from src.trading.connectors.xtquant.http_config import XtQuantHttpConfig
        from src.trading.connectors.xtquant import sdk_http

        cfg = XtQuantHttpConfig(bridge_url="http://test:8888")

        with mock.patch("httpx.post") as mock_post:
            mock_post.return_value.status_code = 200
            mock_post.return_value.json.return_value = {
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

            result = sdk_http.get_quote("600036.SH", config=cfg)
            assert result["status"] == "ok"
            assert result["last"] == 36.0


class TestSdkHttpGetHistoricalBars:
    """Tests for get_historical_bars."""

    def test_success(self):
        from src.trading.connectors.xtquant.http_config import XtQuantHttpConfig
        from src.trading.connectors.xtquant import sdk_http

        cfg = XtQuantHttpConfig(bridge_url="http://test:8888")

        with mock.patch("httpx.post") as mock_post:
            mock_post.return_value.status_code = 200
            mock_post.return_value.json.return_value = {
                "status": "ok",
                "symbol": "600036.SH",
                "period": "1d",
                "bars": [
                    {"t": "2026-07-06", "o": 35.5, "h": 36.2, "l": 35.3,
                     "c": 36.0, "v": 12345678},
                ],
            }

            result = sdk_http.get_historical_bars(
                "600036.SH", config=cfg, period="1d", limit=90,
            )
            assert result["status"] == "ok"
            assert len(result["bars"]) == 1
            assert result["bars"][0]["c"] == 36.0

    def test_empty_bars(self):
        from src.trading.connectors.xtquant.http_config import XtQuantHttpConfig
        from src.trading.connectors.xtquant import sdk_http

        cfg = XtQuantHttpConfig(bridge_url="http://test:8888")

        with mock.patch("httpx.post") as mock_post:
            mock_post.return_value.status_code = 200
            mock_post.return_value.json.return_value = {
                "status": "ok",
                "symbol": "UNKNOWN.SH",
                "period": "1d",
                "bars": [],
            }

            result = sdk_http.get_historical_bars(
                "UNKNOWN.SH", config=cfg, period="1d", limit=90,
            )
            assert result["status"] == "ok"
            assert result["bars"] == []


class TestSdkHttpBuildConfig:
    """Tests for build_config."""

    def test_build_with_profile_config(self):
        from src.trading.connectors.xtquant.http_config import XtQuantHttpConfig
        from src.trading.connectors.xtquant import sdk_http

        cfg = sdk_http.build_config(
            profile_config={"profile": "live", "account_id": "999999"},
        )
        assert cfg.profile == "live"
        assert cfg.account_id == "999999"

    def test_build_with_overrides(self):
        from src.trading.connectors.xtquant import sdk_http

        cfg = sdk_http.build_config(
            profile_config={"profile": "paper"},
            overrides={"profile": "live-readonly", "account_id": "888888"},
        )
        assert cfg.profile == "live-readonly"
        assert cfg.account_id == "888888"


class TestSdkHttpRetry:
    """Tests for HTTP retry behavior."""

    def test_retry_on_timeout_then_succeed(self):
        from src.trading.connectors.xtquant.http_config import XtQuantHttpConfig
        from src.trading.connectors.xtquant import sdk_http

        cfg = XtQuantHttpConfig(
            bridge_url="http://test:8888",
            max_retries=3,
            timeout=1.0,
        )

        import httpx

        with mock.patch("httpx.post") as mock_post:
            # First call times out, second succeeds
            mock_post.side_effect = [
                httpx.TimeoutException("timeout"),
                mock.MagicMock(
                    status_code=200,
                    json=lambda: {"status": "ok", "account_id": "test",
                                  "account_type": "STOCK", "total_value": 100.0,
                                  "cash": 50.0, "buying_power": 50.0,
                                  "market_value": 50.0, "frozen_cash": 0.0},
                ),
            ]

            result = sdk_http.get_account_snapshot(cfg)
            assert result["status"] == "ok"
            assert mock_post.call_count == 2

    def test_all_retries_exhausted(self):
        from src.trading.connectors.xtquant.http_config import XtQuantHttpConfig
        from src.trading.connectors.xtquant import sdk_http
        from src.trading.connectors.xtquant.sdk_http import XtQuantConnectionError

        cfg = XtQuantHttpConfig(
            bridge_url="http://test:8888",
            max_retries=2,
            timeout=1.0,
        )

        import httpx

        with mock.patch("httpx.post") as mock_post:
            mock_post.side_effect = httpx.TimeoutException("timeout")

            with pytest.raises(XtQuantConnectionError, match="retries"):
                sdk_http.get_account_snapshot(cfg)
            assert mock_post.call_count == 2

    def test_401_raises_config_error(self):
        from src.trading.connectors.xtquant.http_config import XtQuantHttpConfig
        from src.trading.connectors.xtquant import sdk_http
        from src.trading.connectors.xtquant.sdk_http import XtQuantConfigError

        cfg = XtQuantHttpConfig(bridge_url="http://test:8888")

        with mock.patch("httpx.post") as mock_post:
            mock_post.return_value.status_code = 401
            mock_post.return_value.json.return_value = {"detail": "Invalid token"}

            with pytest.raises(XtQuantConfigError, match="401"):
                sdk_http.get_account_snapshot(cfg)


class TestSdkHttpModuleExports:
    """Verify sdk_http exports the expected public interface."""

    def test_all_exports(self):
        from src.trading.connectors.xtquant import sdk_http

        expected = [
            "XtQuantHttpConfig",
            "build_config",
            "check_status",
            "get_account_snapshot",
            "get_positions",
            "get_open_orders",
            "get_quote",
            "get_historical_bars",
            "load_config",
        ]
        for name in expected:
            assert hasattr(sdk_http, name), f"sdk_http missing {name}"


class TestProfiles:
    """Verify xtquant-http profiles are defined."""

    def test_http_profiles_exist(self):
        from src.trading.connectors.xtquant.profiles import XTQUANT_PROFILES

        http_ids = [p.id for p in XTQUANT_PROFILES if p.transport == "broker_http"]
        assert "xtquant-http-paper" in http_ids
        assert "xtquant-http-paper-trade" in http_ids
        assert "xtquant-http-live-readonly" in http_ids
        assert "xtquant-http-live-trade" in http_ids

    def test_all_http_profiles_use_broker_http(self):
        from src.trading.connectors.xtquant.profiles import XTQUANT_PROFILES

        for p in XTQUANT_PROFILES:
            if p.connector == "xtquant-http":
                assert p.transport == "broker_http", f"{p.id} should use broker_http"
