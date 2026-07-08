"""Built-in XTQuant (miniQMT) connector profiles.

Four profiles cover the read/write × paper/live matrix for the A-share market
through a local miniQMT process on Windows. Paper write profiles route orders
through Shadow Account simulation; live write profiles gate on the user mandate
(``orders.place.requires_mandate``).
"""

from __future__ import annotations

from src.trading.types import READ_CAPABILITIES, TradingProfile

XTQUANT_PROFILES: tuple[TradingProfile, ...] = (
    TradingProfile(
        id="xtquant-paper",
        connector="xtquant",
        label="miniQMT Paper · Read-Only",
        environment="paper",
        transport="broker_sdk",
        capabilities=READ_CAPABILITIES,
        readonly=True,
        config={"profile": "paper"},
        notes=(
            "读操作走 xtquant SDK。需要本地 miniQMT 进程运行。仅限 Windows。"
        ),
    ),
    TradingProfile(
        id="xtquant-paper-trade",
        connector="xtquant",
        label="miniQMT Paper · Shadow Trading",
        environment="paper",
        transport="broker_sdk",
        capabilities=READ_CAPABILITIES + ("orders.place",),
        readonly=False,
        config={"profile": "paper"},
        notes=(
            "Paper 下单通过 Shadow Account 模拟撮合。需要本地 miniQMT 进程运行。"
        ),
    ),
    TradingProfile(
        id="xtquant-live-readonly",
        connector="xtquant",
        label="miniQMT Live · Read-Only",
        environment="live",
        transport="broker_sdk",
        capabilities=READ_CAPABILITIES,
        readonly=True,
        config={"profile": "live-readonly"},
        notes=(
            "只读实盘账户。需要本地 miniQMT 进程运行。"
        ),
    ),
    TradingProfile(
        id="xtquant-live-trade",
        connector="xtquant",
        label="miniQMT Live · Trading",
        environment="live",
        transport="broker_sdk",
        capabilities=READ_CAPABILITIES + ("orders.place.requires_mandate",),
        readonly=False,
        config={"profile": "live"},
        notes=(
            "实盘下单。所有订单经 Mandate Gate + Kill Switch + Audit。高风险！"
        ),
    ),
)
