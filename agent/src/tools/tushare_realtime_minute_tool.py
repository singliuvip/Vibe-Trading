"""Tool: ``get_realtime_minute_bars`` — A-share real-time minute K-line bars.

Requires the Tushare "A股分钟RT" formal permission (separate from rt_k).
This tool is strictly read-only and returns JSON-serialized results.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from langchain_core.tools import tool

logger = logging.getLogger(__name__)


@tool
def get_realtime_minute_bars(
    codes: str,
    frequency: str = "1MIN",
    max_rows: int = 1000,
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
