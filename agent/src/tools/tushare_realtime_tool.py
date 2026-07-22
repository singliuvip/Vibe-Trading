"""Tool: ``get_realtime_quotes`` — A-share real-time daily K-line snapshots.

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
def _execute_realtime_quotes(
    codes: str = "",
    patterns: str = "",
    fields: str = "",
    max_rows: int = 500,
    **kwargs: Any,
) -> str:
    """获取 A 股盘中实时日K线快照（Tushare A股日线RT 实时数据）。

    Args:
        codes: 股票代码列表，逗号分隔，如 "000001.SZ,600519.SH"（最多50个）。
        patterns: 通配符前缀模式，逗号分隔，如 "3*.SZ,6*.SH"（最多3个）。
        fields: 要返回的字段，逗号分隔（可选）。
        max_rows: 每个标的返回的最大行数（默认500，0=不限制）。

    Returns:
        JSON 字符串，包含 _meta 元数据和每个 ts_code 的实时行情数据。
    """
    from src.core.tushare_market_data import TushareMarketDataService

    code_list = [c.strip() for c in codes.split(",") if c.strip()] if codes else []
    pattern_list = [p.strip() for p in patterns.split(",") if p.strip()] if patterns else []
    field_list = [f.strip() for f in fields.split(",") if f.strip()] if fields else None

    try:
        from backtest.loaders.tushare_realtime import TushareRealtimeProvider

        svc = TushareMarketDataService(
            realtime_provider=TushareRealtimeProvider(),
            auction_provider=None,  # not used
        )
        result = svc.get_realtime_quotes(
            codes=code_list or None,
            patterns=pattern_list or None,
            fields=field_list,
            max_rows=max_rows,
        )
        return json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)
    except RuntimeError as exc:
        return json.dumps(
            {"error": str(exc), "hint": "Set TUSHARE_TOKEN in your environment."},
            ensure_ascii=False,
            indent=2,
        )
    except Exception as exc:
        logger.exception("get_realtime_quotes failed")
        return json.dumps(
            {"error": str(exc)},
            ensure_ascii=False,
            indent=2,
        )


@tool
def get_realtime_quotes(
    codes: str = "",
    patterns: str = "",
    fields: str = "",
    max_rows: int = 500,
    **kwargs: Any,
) -> str:
    """获取 A 股盘中实时日K线快照（Tushare A股日线RT 实时数据）。"""
    return _execute_realtime_quotes(
        codes=codes,
        patterns=patterns,
        fields=fields,
        max_rows=max_rows,
        **kwargs,
    )


class TushareRealtimeTool(BaseTool):
    """BaseTool adapter for get_realtime_quotes."""
    name = "get_realtime_quotes"
    description = get_realtime_quotes.__doc__
    parameters = {
        "type": "object",
        "properties": {
            "codes": {
                "type": "string",
                "description": "逗号分隔的股票代码列表，如 '000001.SZ,600519.SH'（最多50个）。",
            },
            "patterns": {
                "type": "string",
                "description": "通配符前缀模式，逗号分隔，如 '3*.SZ,6*.SH'（最多3个）。",
            },
            "fields": {
                "type": "string",
                "description": "要返回的字段，逗号分隔（可选）。",
            },
            "max_rows": {
                "type": "integer",
                "description": "每个标的返回的最大行数（默认500，0=不限制）。",
                "default": 500,
            },
        },
        "required": [],
    }
    is_readonly = True

    def execute(self, **kwargs: Any) -> str:
        return _execute_realtime_quotes(**kwargs)
