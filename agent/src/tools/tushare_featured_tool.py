"""Tool: ``get_featured_data`` — Tushare featured data (筹码分布 / 涨跌停榜单 / 热榜).

Requires the corresponding Tushare privileges.
This tool is strictly read-only and returns JSON-serialized results.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from langchain_core.tools import tool

logger = logging.getLogger(__name__)

_VALID_KINDS = frozenset({
    "cyq_perf", "cyq_chips", "limit_list", "ths_hot",
    "ths_index", "ths_member", "share_float",
    "pledge_stat", "repurchase", "holdertrade", "stock_st", "hsgt_stocks",
    "stk_surv", "broker_recommend", "cn_macro", "forecast",
})


@tool
def get_featured_data(
    kind: str,
    ts_code: str = "",
    trade_date: str = "",
    start_date: str = "",
    end_date: str = "",
    limit_type: str = "",
    market: str = "",
    exchange: str = "",
    type: str = "",
    ann_date: str = "",
    month: str = "",
    indicator: str = "",
) -> str:
    """查询 Tushare 特色数据（需要相应特权）。

    支持十六种数据类型：
    - ``kind="cyq_perf"``：每日筹码及胜率（筹码成本、加权平均成本、胜率）。
    - ``kind="cyq_chips"``：每日筹码分布（各价格区间的持仓量）。
    - ``kind="limit_list"``：涨跌停榜单（含封单、炸板、连板信息）。
    - ``kind="ths_hot"``：同花顺热榜（个股/概念/ETF/转债热度排名）。
    - ``kind="ths_index"``：同花顺概念板块列表（概念/行业/地域）。
    - ``kind="ths_member"``：同花顺概念板块成分股。
    - ``kind="share_float"``：解禁/流通股本（流通股本、自由流通股本、总股本）。
    - ``kind="pledge_stat"``：股权质押统计（质押比例、质押股数）。
    - ``kind="repurchase"``：股票回购（回购股数、金额、价格区间）。
    - ``kind="holdertrade"``：股东增减持（变动数量、变动比例）。
    - ``kind="stock_st"``：ST股票列表（S/ST/*ST）。
    - ``kind="hsgt_stocks"``：沪港通标的列表（买卖金额）。
    - ``kind="stk_surv"``：机构调研（调研机构、调研内容）。
    - ``kind="broker_recommend"``：券商金股（月度推荐金股及理由）。
    - ``kind="cn_macro"``：中国宏观经济（GDP/CPI/PPI/Shibor）。
    - ``kind="forecast"``：盈利预测（机构预测+业绩预告）。

    Args:
        kind: 数据类型 — "cyq_perf"（筹码胜率）、"cyq_chips"（筹码分布）、
            "limit_list"（涨跌停榜单）、"ths_hot"（热榜）、
            "ths_index"（概念板块列表）、"ths_member"（概念板块成分）、
            "share_float"（解禁/流通股本）、"pledge_stat"（股权质押统计）、
            "repurchase"（股票回购）、"holdertrade"（股东增减持）、
            "stock_st"（ST列表）、"hsgt_stocks"（沪港通标的）、
            "stk_surv"（机构调研）、"broker_recommend"（券商金股）、
            "cn_macro"（中国宏观）、"forecast"（盈利预测）。
        ts_code: 股票/板块代码（如 000001.SZ 或 883900.TI），空字符串=全市场/全部。
        trade_date: 交易日期 YYYYMMDD。
        start_date: 起始日期 YYYYMMDD。
        end_date: 结束日期 YYYYMMDD。
        limit_type: 仅 limit_list：U(涨停)/D(跌停)/Z(炸板)，空字符串=全部。
        market: 仅 ths_hot：A(A股)/HK(港股)，空字符串=全部。
        exchange: 仅 ths_index：交易所代码，空字符串=全部。
        type: 仅 ths_index：板块类型 N(概念)/I(行业)/R(地域)，空字符串=全部。
        ann_date: 公告日期 YYYYMMDD，用于 pledge_stat / repurchase / holdertrade。
        month: 月份 YYYYMM，用于 broker_recommend（券商金股）。
        indicator: 宏观指标，仅 cn_macro：gdp/cpi/ppi/shibor。

    Returns:
        JSON 字符串，包含 ``data_source``、``endpoint``、``retrieved_at``、
        ``is_provisional`` 和 ``data``（数据列表）。
    """
    from backtest.loaders.tushare_featured import TushareFeaturedProvider

    kind = kind.strip().lower()
    if kind not in _VALID_KINDS:
        return json.dumps(
            {
                "error": f"Invalid kind: {kind!r}. Must be one of {sorted(_VALID_KINDS)}.",
                "hint": "Use 'cyq_perf', 'cyq_chips', 'limit_list', 'ths_hot', "
                "'ths_index', 'ths_member', 'share_float', 'pledge_stat', "
                "'repurchase', 'holdertrade', 'stock_st', 'hsgt_stocks', "
                "'stk_surv', 'broker_recommend', 'cn_macro', or 'forecast'.",
            },
            ensure_ascii=False,
            indent=2,
        )

    try:
        provider = TushareFeaturedProvider()
    except RuntimeError as exc:
        return json.dumps(
            {"error": str(exc), "hint": "Set TUSHARE_TOKEN in your environment."},
            ensure_ascii=False,
            indent=2,
        )

    try:
        if kind == "cyq_perf":
            result = provider.fetch_cyq_perf(
                ts_code=ts_code,
                trade_date=trade_date,
                start_date=start_date,
                end_date=end_date,
            )
        elif kind == "cyq_chips":
            result = provider.fetch_cyq_chips(
                ts_code=ts_code,
                trade_date=trade_date,
                start_date=start_date,
                end_date=end_date,
            )
        elif kind == "limit_list":
            result = provider.fetch_limit_list(
                trade_date=trade_date,
                ts_code=ts_code,
                limit_type=limit_type,
            )
        elif kind == "ths_hot":
            result = provider.fetch_ths_hot(
                trade_date=trade_date,
                market=market,
            )
    except Exception as exc:
        logger.exception("get_featured_data failed for kind=%s", kind)
        return json.dumps(
            {"error": str(exc)},
            ensure_ascii=False,
            indent=2,
        )

    # ── P1-2: concept sector / share float ──
    try:
        if kind == "ths_index":
            result = provider.fetch_ths_index(
                ts_code=ts_code,
                exchange=exchange,
                type=type,
            )
        elif kind == "ths_member":
            result = provider.fetch_ths_member(
                ts_code=ts_code,
            )
        elif kind == "share_float":
            result = provider.fetch_share_float(
                ts_code=ts_code,
                trade_date=trade_date,
                start_date=start_date,
                end_date=end_date,
            )
    except Exception as exc:
        logger.exception("get_featured_data failed for kind=%s", kind)
        return json.dumps(
            {"error": str(exc)},
            ensure_ascii=False,
            indent=2,
        )

    # ── P2-1: reference data ──
    try:
        if kind == "pledge_stat":
            result = provider.fetch_pledge_stat(
                ts_code=ts_code,
            )
        elif kind == "repurchase":
            result = provider.fetch_repurchase(
                ts_code=ts_code,
                ann_date=ann_date,
                start_date=start_date,
                end_date=end_date,
            )
        elif kind == "holdertrade":
            result = provider.fetch_holdertrade(
                ts_code=ts_code,
                ann_date=ann_date,
                start_date=start_date,
                end_date=end_date,
            )
        elif kind == "stock_st":
            result = provider.fetch_stock_st(
                ts_code=ts_code,
                trade_date=trade_date,
            )
        elif kind == "hsgt_stocks":
            result = provider.fetch_hsgt_stocks(
                trade_date=trade_date,
            )
    except Exception as exc:
        logger.exception("get_featured_data failed for kind=%s", kind)
        return json.dumps(
            {"error": str(exc)},
            ensure_ascii=False,
            indent=2,
        )

    # ── P2-2: stk_surv / broker_recommend / cn_macro / forecast ──
    try:
        if kind == "stk_surv":
            result = provider.fetch_stk_surv(
                ts_code=ts_code,
                start_date=start_date,
                end_date=end_date,
            )
        elif kind == "broker_recommend":
            result = provider.fetch_broker_recommend(
                month=month,
            )
        elif kind == "cn_macro":
            result = provider.fetch_cn_macro(
                indicator=indicator,
                start_date=start_date,
                end_date=end_date,
            )
        elif kind == "forecast":
            result = provider.fetch_forecast(
                ts_code=ts_code,
                start_date=start_date,
                end_date=end_date,
            )
    except Exception as exc:
        logger.exception("get_featured_data failed for kind=%s", kind)
        return json.dumps(
            {"error": str(exc)},
            ensure_ascii=False,
            indent=2,
        )

    return json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)
