"""Built-in virtual broker connector profiles.

Two profiles are provided: a read-only view profile and a live-runner-capable
trading profile. Both are fully local paper profiles — no credentials, no
network access, no real funds.
"""

from __future__ import annotations

from src.trading.types import READ_CAPABILITIES, TradingProfile

VIRTUAL_PROFILES: tuple[TradingProfile, ...] = (
    TradingProfile(
        id="virtual-paper-sdk",
        connector="virtual",
        label="Virtual Broker · Local Paper Read-Only",
        environment="paper",
        transport="broker_sdk",
        capabilities=READ_CAPABILITIES,
        readonly=True,
        config={
            "profile": "paper",
            "account_id": "default",
            "initial_cash": 1_000_000.0,
            "currency": "USD",
            "slippage_bps": 10.0,
            "fee_bps": 5.0,
            "allow_short": True,
            "max_leverage": 2.0,
            "symbols": ["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "TSLA", "SPY", "QQQ"],
            "price_source": "auto",
        },
        notes=(
            "Reads a fully local virtual paper account. "
            "No external broker, credentials, or real funds."
        ),
    ),
    TradingProfile(
        id="virtual-paper-trade",
        connector="virtual",
        label="Virtual Broker · Local Paper Trading",
        environment="paper",
        transport="broker_sdk",
        capabilities=READ_CAPABILITIES + (
            "orders.place",
            "orders.cancel",
            "orders.place.requires_mandate",
            "runner.manage.requires_mandate",
        ),
        readonly=False,
        config={
            "profile": "paper",
            "account_id": "default",
            "initial_cash": 1_000_000.0,
            "currency": "USD",
            "slippage_bps": 10.0,
            "fee_bps": 5.0,
            "allow_short": True,
            "max_leverage": 2.0,
            "symbols": ["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "TSLA", "SPY", "QQQ"],
            "price_source": "auto",
        },
        notes=(
            "Places simulated paper orders against local virtual account state. "
            "Designed for LiveRunner dry-runs under a mandate prompt, "
            "with no real funds at risk."
        ),
    ),
    TradingProfile(
        id="virtual-paper-trade-cn",
        connector="virtual",
        label="Virtual Broker · Local Paper A-Share Trading",
        environment="paper",
        transport="broker_sdk",
        capabilities=READ_CAPABILITIES + (
            "orders.place",
            "orders.cancel",
            "orders.place.requires_mandate",
            "runner.manage.requires_mandate",
        ),
        readonly=False,
        config={
            "profile": "paper",
            "account_id": "cn-default",
            "initial_cash": 1_000_000.0,
            "currency": "CNY",
            "slippage_bps": 10.0,
            "fee_bps": 5.0,
            "allow_short": False,
            "max_leverage": 1.0,
            "symbols": [],
            "price_source": "auto",
            "market": "a_share",
            "allow_dynamic_symbols": True,
            "base_prices": {
                "000001.SZ": 15.0,
                "000002.SZ": 12.0,
                "000333.SZ": 68.0,
                "000858.SZ": 150.0,
                "002415.SZ": 45.0,
                "600000.SH": 8.0,
                "600036.SH": 40.0,
                "600519.SH": 1800.0,
                "600900.SH": 28.0,
                "601318.SH": 55.0,
            },
        },
        notes=(
            "A-share simulated paper trading. "
            "Allows any A-share symbol (SZ/SH/BJ/CN prefix) dynamically. "
            "No short selling, no leverage, CNY settlement."
        ),
    ),
)
