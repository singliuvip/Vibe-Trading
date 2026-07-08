"""XTQuant / miniQMT trading connector — native xtquant SDK (Windows only).

Vibe-Trading imports ``xtquant`` directly on Windows hosts.  miniQMT runs as a
local process on the same machine; the SDK communicates via IPC.

Architecture::

    Vibe-Trading (Windows)  --SDK-->  miniQMT (local)

- ``broker_sdk`` transport: all operations go through ``sdk.py`` which
  imports and calls ``xtquant`` directly.
- Paper mode (``profile == "paper"``) is handled **locally** via PaperEngine
  (no SDK call).
- Live mode goes through ``XtQuantTrader``; the caller ``service.py`` enforces
  Mandate Gate + Kill Switch + Audit.
"""

from src.trading.connectors.xtquant.sdk import (  # noqa: F401
    XtQuantConfig,
    XtQuantConfigError,
    XtQuantConnectionError,
    XtQuantDependencyError,
    XtQuantPlatformError,
    build_config,
    cancel_order,
    check_status,
    get_account_snapshot,
    get_heartbeat_status,
    get_historical_bars,
    get_open_orders,
    get_positions,
    get_quote,
    get_today_trades,
    load_config,
    place_order,
    probe_connection,
    start_heartbeat,
    stop_heartbeat,
)

from src.trading.connectors.xtquant.paper_engine import (  # noqa: F401
    PaperEngine,
    PaperOrder,
    PaperPosition,
    get_paper_engine,
)
