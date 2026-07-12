"""Tests for VirtualConfig and build_config (sdk.py)."""

from __future__ import annotations

import pytest

from src.trading.connectors.virtual.market_sim import DEFAULT_SYMBOLS
from src.trading.connectors.virtual.sdk import VirtualConfig, build_config

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# VirtualConfig defaults
# ---------------------------------------------------------------------------


def test_virtual_config_defaults() -> None:
    """默认值：market="us_equity", allow_dynamic_symbols=False, symbols=DEFAULT_SYMBOLS."""
    cfg = VirtualConfig()
    assert cfg.profile == "paper"
    assert cfg.account_id == "default"
    assert cfg.currency == "USD"
    assert cfg.market == "us_equity"
    assert cfg.allow_dynamic_symbols is False
    assert cfg.symbols == DEFAULT_SYMBOLS
    assert cfg.initial_cash == 1_000_000.0


def test_virtual_config_from_mapping() -> None:
    """从 dict 构建配置（含 market/a_share 字段）."""
    data = {
        "market": "a_share",
        "currency": "CNY",
        "allow_short": False,
        "allow_dynamic_symbols": True,
        "symbols": [],
        "account_id": "cn-test",
        "base_prices": {"000001.SZ": 15.0},
    }
    cfg = VirtualConfig.from_mapping(data)
    assert cfg.market == "a_share"
    assert cfg.currency == "CNY"
    assert cfg.allow_short is False
    assert cfg.allow_dynamic_symbols is True
    assert cfg.symbols == ()
    assert cfg.account_id == "cn-test"
    assert cfg.base_prices == {"000001.SZ": 15.0}


def test_virtual_config_from_mapping_empty_symbols() -> None:
    """symbols=[] 时保持空列表（不回退到 DEFAULT_SYMBOLS）."""
    cfg = VirtualConfig.from_mapping({"symbols": []})
    assert cfg.symbols == ()


def test_virtual_config_with_overrides() -> None:
    """覆盖配置."""
    cfg = VirtualConfig()
    overridden = cfg.with_overrides(
        account_id="new-account",
        slippage_bps=20.0,
        allow_short=False,
        market="a_share",
    )
    assert overridden.account_id == "new-account"
    assert overridden.slippage_bps == 20.0
    assert overridden.allow_short is False
    assert overridden.market == "a_share"
    # Unchanged fields
    assert overridden.currency == "USD"
    assert overridden.fee_bps == 5.0


def test_virtual_config_market_validation() -> None:
    """无效 market 值回退到 "us_equity"."""
    cfg = VirtualConfig.from_mapping({"market": "invalid_market"})
    assert cfg.market == "us_equity"


def test_virtual_config_environment() -> None:
    """始终返回 "paper"."""
    cfg = VirtualConfig()
    assert cfg.environment == "paper"


def test_virtual_config_is_demo() -> None:
    """始终返回 True."""
    cfg = VirtualConfig()
    assert cfg.is_demo is True


# ---------------------------------------------------------------------------
# build_config
# ---------------------------------------------------------------------------


def test_build_config_no_profile_config() -> None:
    """无配置时使用默认值."""
    cfg = build_config(None, None)
    assert isinstance(cfg, VirtualConfig)
    assert cfg.market == "us_equity"
    assert cfg.symbols == DEFAULT_SYMBOLS


def test_build_config_with_profile_config() -> None:
    """含 profile config 时正确解析."""
    profile = {
        "market": "a_share",
        "currency": "CNY",
        "allow_dynamic_symbols": True,
        "symbols": [],
    }
    cfg = build_config(profile, None)
    assert cfg.market == "a_share"
    assert cfg.currency == "CNY"
    assert cfg.allow_dynamic_symbols is True


def test_build_config_with_overrides() -> None:
    """含 overrides 时覆盖 profile config."""
    profile = {
        "market": "a_share",
        "currency": "CNY",
        "account_id": "profile-account",
    }
    overrides = {
        "account_id": "override-account",
        "slippage_bps": "15.0",
    }
    cfg = build_config(profile, overrides)
    assert cfg.market == "a_share"  # from profile
    assert cfg.account_id == "override-account"  # overridden
    assert cfg.slippage_bps == 15.0  # overridden


def test_build_config_overrides_ignore_empty() -> None:
    """空字符串 overrides 被忽略."""
    profile = {"account_id": "profile-account"}
    overrides = {"account_id": ""}
    cfg = build_config(profile, overrides)
    assert cfg.account_id == "profile-account"
