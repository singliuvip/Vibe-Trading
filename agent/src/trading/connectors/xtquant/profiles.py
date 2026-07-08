"""Built-in XTQuant (miniQMT) connector profiles — native SDK.

All profiles use the ``broker_sdk`` transport: Vibe-Trading imports ``xtquant``
directly on Windows hosts and communicates with the local miniQMT process via IPC.

**Important architecture decision**: Vibe-Trading imports xtquant directly on
Windows. miniQMT runs on the same host; no HTTP bridge is needed.

Paper write profiles route orders through local PaperEngine simulation (no
SDK call for paper trades).  Live write profiles gate on the user mandate
(``orders.place.requires_mandate``).

Four profiles cover the read/write × paper/live matrix:
  - ``xtquant-paper``          Paper 只读
  - ``xtquant-paper-trade``    Paper 写（本地模拟）
  - ``xtquant-live-readonly``  实盘只读
  - ``xtquant-live-trade``     实盘下单
"""

from __future__ import annotations

from src.trading.types import READ_CAPABILITIES, TradingProfile

XTQUANT_PROFILES: tuple[TradingProfile, ...] = (
    TradingProfile(
        id="xtquant-paper",
        connector="xtquant",
        label="miniQMT Paper · Read-Only (Native SDK)",
        environment="paper",
        transport="broker_sdk",
        capabilities=READ_CAPABILITIES,
        readonly=True,
        config={"profile": "paper"},
        notes=(
            "通过原生 xtquant SDK 只读访问。"
            "需要 Windows 宿主机运行 miniQMT。"
        ),
    ),
    TradingProfile(
        id="xtquant-paper-trade",
        connector="xtquant",
        label="miniQMT Paper · Shadow Trading (Native SDK)",
        environment="paper",
        transport="broker_sdk",
        capabilities=READ_CAPABILITIES + ("orders.place",),
        readonly=False,
        config={"profile": "paper"},
        notes=(
            "读操作经原生 xtquant SDK，下单通过本地 PaperEngine 模拟。"
            "需要 Windows 宿主机运行 miniQMT。"
        ),
    ),
    TradingProfile(
        id="xtquant-live-readonly",
        connector="xtquant",
        label="miniQMT Live · Read-Only (Native SDK)",
        environment="live",
        transport="broker_sdk",
        capabilities=READ_CAPABILITIES,
        readonly=True,
        config={"profile": "live-readonly"},
        notes=(
            "通过原生 xtquant SDK 只读访问实盘账户。"
            "需要 Windows 宿主机运行 miniQMT。"
        ),
    ),
    TradingProfile(
        id="xtquant-live-trade",
        connector="xtquant",
        label="miniQMT Live · Trading (Native SDK)",
        environment="live",
        transport="broker_sdk",
        capabilities=READ_CAPABILITIES + ("orders.place.requires_mandate",),
        readonly=False,
        config={"profile": "live"},
        notes=(
            "读操作 + 实盘下单经原生 xtquant SDK。"
            "所有订单经 Mandate Gate + Kill Switch + Audit。高风险！"
            "需要 Windows 宿主机运行 miniQMT。"
        ),
    ),
)
