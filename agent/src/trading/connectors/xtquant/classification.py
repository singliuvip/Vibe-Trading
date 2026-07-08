"""Curated read/write classification for XTQuant (miniQMT) SDK operations.

The trading layer classifies each connector operation as READ or WRITE so the
live gate can keep writes behind the mandate. XTQuant is a direct local-SDK
connector (the SDK talks to a local miniQMT process, not a remote MCP server),
so the keys here are the SDK's own method names rather than remote MCP tool
names. Anything not listed and not a known read resolves to WRITE (fail-closed)
when the live gate consults this map.
"""

from __future__ import annotations

from src.live.classification import ToolClass

#: XTQuant SDK operation read/write catalog. Read operations mirror the
#: connector's public read functions; write operations are the order-mutating
#: SDK calls, pinned WRITE so the live gate never treats them as plain reads.
XTQUANT_TOOL_CLASS: dict[str, ToolClass] = {
    # READ
    "get_asset": ToolClass.READ,
    "query_stock_positions": ToolClass.READ,
    "query_stock_orders": ToolClass.READ,
    "query_stock_trades": ToolClass.READ,
    "get_full_tick": ToolClass.READ,
    "get_local_data": ToolClass.READ,
    "download_history_data": ToolClass.READ,
    # WRITE
    "order_stock": ToolClass.WRITE,
    "cancel_order_stock": ToolClass.WRITE,
    "order_stock_async": ToolClass.WRITE,
    "cancel_order_stock_async": ToolClass.WRITE,
}
