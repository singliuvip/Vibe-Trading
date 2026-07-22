"""Tool: ``get_realtime_minute_bars`` — A-share real-time minute K-line bars.

Requires the Tushare "A股分钟RT" formal permission (separate from rt_k).
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
def _execute_realtime_minute_bars(
    codes: str,
    frequency: str = "1MIN",
    max_rows: int = 1000,
    **kwargs: Any,
) -> str:
    """获取 A 股/ETF 当日实时分钟 K 线（需要 Tushare rt_min 正式权限）。

    Args:
        codes: 股票代码，逗号分隔，如 "600000.SH,000001.SZ"
        frequency: K线周期，可选 1MIN/5MIN/15MIN/30MIN/60MIN
        max_rows: 每个标的最大返回行数（默认1000，0=不限制）

    Returns:
        JSON 字符串，包含 _meta 元数据和每个 ts_code 的实时分钟K线数据。
    """
    from src.core.tushare_market_data import TushareMarketDataService

    code_list = [c.strip() for c in codes.split(",") if c.strip()]

    if not code_list:
        return json.dumps(
            {"error": "At least one code is required."},
            ensure_ascii=False,
            indent=2,
        )

    if frequency not in ("1MIN", "5MIN", "15MIN", "30MIN", "60MIN"):
        return json.dumps(
            {"error": f"Invalid frequency: {frequency!r}. Must be one of 1MIN/5MIN/15MIN/30MIN/60MIN."},
            ensure_ascii=False,
            indent=2,
        )

    try:
        from backtest.loaders.tushare_realtime_minute import TushareRealtimeMinuteProvider

        svc = TushareMarketDataService(
            minute_provider=TushareRealtimeMinuteProvider(),
        )
        result = svc.get_realtime_minute_bars(
            codes=code_list,
            frequency=frequency,
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
        logger.exception("get_realtime_minute_bars failed")
        return json.dumps(
            {"error": str(exc)},
            ensure_ascii=False,
            indent=2,
        )


@tool
def get_realtime_minute_bars(
    codes: str,
    frequency: str = "1MIN",
    max_rows: int = 1000,
    **kwargs: Any,
) -> str:
    """获取 A 股/ETF 当日实时分钟 K 线（需要 Tushare rt_min 正式权限）。"""
    return _execute_realtime_minute_bars(
        codes=codes,
        frequency=frequency,
        max_rows=max_rows,
        **kwargs,
    )


class TushareRealtimeMinuteTool(BaseTool):
    """BaseTool adapter for get_realtime_minute_bars."""
    name = "get_realtime_minute_bars"
    description = get_realtime_minute_bars.__doc__
    parameters = {
        "type": "object",
        "properties": {
            "codes": {
                "type": "string",
                "description": "股票代码，逗号分隔，如 '600000.SH,000001.SZ'",
            },
            "frequency": {
                "type": "string",
                "enum": ["1MIN", "5MIN", "15MIN", "30MIN", "60MIN"],
                "description": "K线周期，可选 1MIN/5MIN/15MIN/30MIN/60MIN（默认1MIN）。",
                "default": "1MIN",
            },
            "max_rows": {
                "type": "integer",
                "description": "每个标的最大返回行数（默认1000，0=不限制）。",
                "default": 1000,
            },
        },
        "required": ["codes"],
    }
    is_readonly = True

    def execute(self, **kwargs: Any) -> str:
        return _execute_realtime_minute_bars(**kwargs)
