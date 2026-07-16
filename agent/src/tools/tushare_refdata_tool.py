"""Tool: ``search_security_master`` — A-share security master data lookup.

Requires Tushare basic data permission (stock_basic / fund_basic / opt_basic).
This tool is strictly read-only and returns JSON-serialized results.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from langchain_core.tools import tool

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


@tool
def search_security_master(
    kind: str,
    market: str = "",
    list_status: str = "L",
) -> str:
    """查询证券主数据（股票/ETF/期权列表），需要 Tushare 基础数据权限。

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
    from backtest.loaders.tushare_refdata import TushareRefDataProvider

    kind = kind.strip().lower()
    if kind not in _VALID_KINDS:
        return _error(
            f"Invalid kind: {kind!r}. Must be one of {sorted(_VALID_KINDS)}."
        )

    market = market.strip() if market else ""

    try:
        provider = TushareRefDataProvider()
    except RuntimeError as exc:
        return json.dumps(
            {"ok": False, "error": str(exc),
             "hint": "Set TUSHARE_TOKEN in your environment."},
            ensure_ascii=False,
        )

    try:
        if kind == "stock":
            mkt: str | None = market if market else None
            result = provider.fetch_stock_list(market=mkt, list_status=list_status)
        elif kind == "fund":
            mkt = market if market else None
            result = provider.fetch_fund_list(market=mkt)
        else:  # option
            mkt = market if market else None
            result = provider.fetch_option_list(exchange=mkt)
    except Exception as exc:
        logger.exception("search_security_master failed for kind=%s", kind)
        return _error(str(exc))

    # Merge in ok flag.
    envelope: dict[str, Any] = {"ok": "error" not in result, **result}
    return json.dumps(envelope, ensure_ascii=False, indent=2, allow_nan=False)
