"""Tool: ``search_security_master`` — A-share security master data lookup.

This tool is strictly read-only and returns JSON-serialized results.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from langchain_core.tools import tool

from src.agent.tools import BaseTool

logger = logging.getLogger(__name__)

_VALID_KINDS = frozenset({"stock", "fund", "option"})


def _error(message: str) -> str:
    """Build the failure envelope as a JSON string.

    Args:
        message: Human-readable error description.

    Returns:
        A ``{"ok": false, "error": ...}`` JSON string.
    """
    return json.dumps({"ok": False, "error": message}, ensure_ascii=False)


# 核心实现函数（无装饰器，可被 BaseTool.execute() 直接调用）
def _execute_search_security_master(
    kind: str,
    market: str = "",
    list_status: str = "L",
    **kwargs: Any,
) -> str:
    """查询证券主数据（股票/ETF/期权列表）。

    获取 A 股市场的证券列表：
    - ``kind="stock"``：股票列表，可按市场（SH/SZ/BJ）和上市状态过滤。
    - ``kind="fund"``：基金/ETF 列表，可按市场类型过滤（E=ETF，O=开放式）。
    - ``kind="option"``：期权合约列表，可按交易所（SSE=上交所，SZSE=深交所）过滤。

    Args:
        kind: 证券类型 — "stock"（股票）、"fund"（基金/ETF）、"option"（期权）。
        market: 可选市场过滤。
            stock: "SH"(上交所) / "SZ"(深交所) / "BJ"(北交所)，空字符串=全市场。
            fund: "E"(ETF) / "O"(开放式) 等，空字符串=全部。
            option: "SSE"(上交所) / "SZSE"(深交所)，空字符串=全部。
        list_status: 仅 stock 生效：L(上市，默认) / D(退市) / P(暂停)。

    Returns:
        JSON 字符串，包含 ``ok``、``data_source``、``endpoint``、``retrieved_at``、
        ``is_provisional`` 和 ``data``（证券列表）。
    """
    from src.core.tushare_market_data import TushareMarketDataService

    kind = kind.strip().lower()
    if kind not in _VALID_KINDS:
        return _error(
            f"Invalid kind: {kind!r}. Must be one of {sorted(_VALID_KINDS)}."
        )

    market = market.strip() if market else ""

    try:
        from backtest.loaders.tushare_refdata import TushareRefDataProvider

        svc = TushareMarketDataService(
            refdata_provider=TushareRefDataProvider(),
        )
        result = svc.get_security_master(kind=kind, market=market, list_status=list_status)
    except RuntimeError as exc:
        return json.dumps(
            {"ok": False, "error": str(exc),
             "hint": "Set TUSHARE_TOKEN in your environment."},
            ensure_ascii=False,
        )

    # Merge in ok flag.
    envelope: dict[str, Any] = {"ok": "error" not in result, **result}
    return json.dumps(envelope, ensure_ascii=False, indent=2, allow_nan=False)


@tool
def search_security_master(
    kind: str,
    market: str = "",
    list_status: str = "L",
    **kwargs: Any,
) -> str:
    """查询证券主数据（股票/ETF/期权列表）。"""
    return _execute_search_security_master(
        kind=kind,
        market=market,
        list_status=list_status,
        **kwargs,
    )


class TushareRefDataTool(BaseTool):
    """BaseTool adapter for search_security_master."""
    name = "search_security_master"
    description = search_security_master.__doc__
    parameters = {
        "type": "object",
        "properties": {
            "kind": {
                "type": "string",
                "enum": sorted(_VALID_KINDS),
                "description": "Security type: 'stock', 'fund', or 'option'.",
            },
            "market": {
                "type": "string",
                "description": "Optional market filter. stock: SH/SZ/BJ; fund: E/O etc; option: SSE/SZSE.",
            },
            "list_status": {
                "type": "string",
                "enum": ["L", "D", "P"],
                "description": "Listing status (stock only): L(listed), D(delisted), P(paused).",
                "default": "L",
            },
        },
        "required": ["kind"],
    }
    is_readonly = True

    def execute(self, **kwargs: Any) -> str:
        return _execute_search_security_master(**kwargs)
