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
        result = cancel_order(config, "test-order-001")
        assert result["status"] == "ok"
        assert result.get("paper") is True


class TestLiveOrderValidation:
    """Live order input validation (no real SDK)."""

    @patch("src.trading.connectors.xtquant.sdk._ensure_connected")
    @patch("src.trading.connectors.xtquant.sdk._import_xtquant")
    def test_invalid_side(self, _mock_import, _mock_connected):
        config = XtQuantConfig(profile="live", mini_qmt_path="D:\\QMT", account_id="123")
        result = place_order(config, "000001.SZ", "invalid", quantity=100)
        assert result["status"] == "error"
        assert "side" in result["error"]

    @patch("src.trading.connectors.xtquant.sdk._ensure_connected")
    @patch("src.trading.connectors.xtquant.sdk._import_xtquant")
    def test_zero_quantity(self, _mock_import, _mock_connected):
        config = XtQuantConfig(profile="live", mini_qmt_path="D:\\QMT", account_id="123")
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
