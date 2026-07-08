"""XTQuant / miniQMT trading connector.

Read-only account/market access in Layer A via the official ``xtquant`` Python
SDK talking to a LOCAL miniQMT process on Windows. Like the Futu connector,
this is a local-SDK transport: miniQMT runs on the operator's machine, holds
the QMT login, and the SDK speaks to it over local IPC. Vibe-Trading never
sees QMT credentials and exposes no order-placement method (no ``order_stock``,
no ``passorder``) in this layer; order placement (paper, then mandate-gated
live) is layered on top later.

Paper-vs-live separation: xtquant's XtQuantTrader determines the environment by
the miniQMT path. Paper accounts use a dedicated paper miniQMT instance; live
accounts connect to a live miniQMT. The connector resolves the account ID whose
``account_type`` matches the selected profile and auto-discovers account IDs by
scanning the ``userdata_mini`` directory.
"""

from src.trading.connectors.xtquant.sdk import (  # noqa: F401
    XtQuantConfig,
    XtQuantConfigError,
    XtQuantConnectionError,
    XtQuantDependencyError,
    XtQuantPlatformError,
    build_config,
    check_status,
    config_path,
    get_account_snapshot,
    get_historical_bars,
    get_open_orders,
    get_positions,
    get_quote,
    load_config,
)

__all__ = [
    "XtQuantConfig",
    "XtQuantConfigError",
    "XtQuantConnectionError",
    "XtQuantDependencyError",
    "XtQuantPlatformError",
    "build_config",
    "check_status",
    "config_path",
    "get_account_snapshot",
    "get_historical_bars",
    "get_open_orders",
    "get_positions",
    "get_quote",
    "load_config",
]
