"""Built-in XTQuant (miniQMT) connector profiles.

Eight profiles cover the read/write × paper/live × native/http matrix for the
A-share market.

Native (``broker_sdk``) profiles require a local miniQMT process on Windows.
HTTP (``broker_http``) profiles talk to a QMT Bridge on the host — suitable
when Vibe-Trading runs inside a Docker container.

Paper write profiles route orders through Shadow Account simulation; live write
profiles gate on the user mandate (``orders.place.requires_mandate``).
"""

from __future__ import annotations

from src.trading.types import READ_CAPABILITIES, TradingProfile

XTQUANT_PROFILES: tuple[TradingProfile, ...] = (
    # ── Native (broker_sdk) on Windows ──
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
    # ── HTTP bridge (broker_http) for Docker / Linux ──
    TradingProfile(
        id="xtquant-http-paper",
        connector="xtquant",
        label="miniQMT Paper · HTTP Bridge",
        environment="paper",
        transport="broker_http",
        capabilities=READ_CAPABILITIES,
        readonly=True,
        config={"profile": "paper"},
        notes=(
            "Docker/Linux 兼容：通过 QMT Bridge HTTP 代理访问 xtquant。"
            "需要宿主机运行 QMT Bridge (python -m qmt_bridge.server)。"
        ),
    ),
    TradingProfile(
        id="xtquant-http-paper-trade",
        connector="xtquant",
        label="miniQMT Paper · HTTP Bridge + Shadow",
        environment="paper",
        transport="broker_http",
        capabilities=READ_CAPABILITIES + ("orders.place",),
        readonly=False,
        config={"profile": "paper"},
        notes=(
            "Docker/Linux 兼容：读操作经 QMT Bridge，下单通过 Shadow Account 模拟。"
            "需要宿主机运行 QMT Bridge。"
        ),
    ),
    TradingProfile(
        id="xtquant-http-live-readonly",
        connector="xtquant",
        label="miniQMT Live · HTTP Bridge Read-Only",
        environment="live",
        transport="broker_http",
        capabilities=READ_CAPABILITIES,
        readonly=True,
        config={"profile": "live-readonly"},
        notes=(
            "Docker/Linux 兼容：通过 QMT Bridge 只读访问实盘账户。"
            "需要宿主机运行 QMT Bridge。"
        ),
    ),
    TradingProfile(
        id="xtquant-http-live-trade",
        connector="xtquant",
        label="miniQMT Live · HTTP Bridge Trading",
        environment="live",
        transport="broker_http",
        capabilities=READ_CAPABILITIES + ("orders.place.requires_mandate",),
        readonly=False,
        config={"profile": "live"},
        notes=(
            "Docker/Linux 兼容：读操作 + 实盘下单经 QMT Bridge。"
            "所有订单经 Mandate Gate + Kill Switch + Audit。高风险！"
        ),
    ),
)
