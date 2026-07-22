"""Tool: ``get_auction_data`` — A-share call-auction data.

Requires the Tushare "集合竞价成交" privilege (15000+ membership points).
This tool is strictly read-only and returns JSON-serialized results.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from langchain_core.tools import tool

from src.agent.tools import BaseTool

logger = logging.getLogger(__name__)


# 核心实现函数（无装饰器，可被 BaseTool.execute() 直接调用）
def _execute_auction_data(
    session: str,
    codes: str = "",
    trade_date: str = "",
    start_date: str = "",
    end_date: str = "",
    max_rows: int = 500,
    fields: str = "",
    **kwargs: Any,
) -> str:
    """获取 A 股集合竞价数据（需要 Tushare 集合竞价成交 特权，15000+ 积分）。

    Args:
        session: 竞价时段 — "current"（当日竞价 stk_auction）、"open"（开盘竞价历史 stk_auction_o）、"close"（收盘竞价历史 stk_auction_c）。
        codes: 股票代码列表，逗号分隔，如 "000001.SZ,600519.SH"（最多50个）。
        trade_date: 交易日期 YYYYMMDD（默认今天）。
        start_date: 起始日期 YYYYMMDD（历史查询）。
        end_date: 结束日期 YYYYMMDD（历史查询）。
        max_rows: 每个标的返回的最大行数（默认500，0=不限制）。
        fields: 要返回的字段（逗号分隔，可选，透传到 Tushare）。

    Returns:
        JSON 字符串，包含 _meta 元数据和每个 ts_code 的竞价数据。
    """
    from src.core.tushare_market_data import TushareMarketDataService

    if session not in ("current", "open", "close"):
        return json.dumps(
            {"error": f"Invalid session: {session!r}. Must be 'current', 'open', or 'close'."},
            ensure_ascii=False,
            indent=2,
        )

    code_list = [c.strip() for c in codes.split(",") if c.strip()] if codes else []
    td = trade_date.strip() or None
    sd = start_date.strip() or None
    ed = end_date.strip() or None
    field_list = [f.strip() for f in fields.split(",") if f.strip()] if fields else None

    try:
        from backtest.loaders.tushare_auction import TushareAuctionProvider

        svc = TushareMarketDataService(
            realtime_provider=None,  # not used
            auction_provider=TushareAuctionProvider(),
        )
        result = svc.get_auction_data(
            session=session,
            codes=code_list or None,
            trade_date=td,
            start_date=sd,
            end_date=ed,
            max_rows=max_rows,
            fields=field_list,
        )
        return json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)
    except RuntimeError as exc:
        return json.dumps(
            {"error": str(exc), "hint": "Set TUSHARE_TOKEN in your environment."},
            ensure_ascii=False,
            indent=2,
        )
    except Exception as exc:
        logger.exception("get_auction_data failed")
        return json.dumps(
            {"error": str(exc)},
            ensure_ascii=False,
            indent=2,
        )


@tool
def get_auction_data(
    session: str,
    codes: str = "",
    trade_date: str = "",
    start_date: str = "",
    end_date: str = "",
    max_rows: int = 500,
    fields: str = "",
    **kwargs: Any,
) -> str:
    """获取 A 股集合竞价数据（需要 Tushare 集合竞价成交 特权，15000+ 积分）。"""
    return _execute_auction_data(
        session=session,
        codes=codes,
        trade_date=trade_date,
        start_date=start_date,
        end_date=end_date,
        max_rows=max_rows,
        fields=fields,
        **kwargs,
    )


class TushareAuctionTool(BaseTool):
    """BaseTool adapter for get_auction_data."""
    name = "get_auction_data"
    description = get_auction_data.__doc__
    parameters = {
        "type": "object",
        "properties": {
            "session": {
                "type": "string",
                "enum": ["current", "open", "close"],
                "description": "竞价时段：current(当日竞价)、open(开盘竞价历史)、close(收盘竞价历史)。",
            },
            "codes": {
                "type": "string",
                "description": "逗号分隔的股票代码列表，如 '000001.SZ,600519.SH'（最多50个）。",
            },
            "trade_date": {
                "type": "string",
                "description": "交易日期 YYYYMMDD（默认今天）。",
            },
            "start_date": {
                "type": "string",
                "description": "起始日期 YYYYMMDD（历史查询）。",
            },
            "end_date": {
                "type": "string",
                "description": "结束日期 YYYYMMDD（历史查询）。",
            },
            "max_rows": {
                "type": "integer",
                "description": "每个标的返回的最大行数（默认500，0=不限制）。",
                "default": 500,
            },
            "fields": {
                "type": "string",
                "description": "要返回的字段（逗号分隔，可选，透传到 Tushare）。",
            },
        },
        "required": ["session"],
    }
    is_readonly = True

    def execute(self, **kwargs: Any) -> str:
        return _execute_auction_data(**kwargs)
