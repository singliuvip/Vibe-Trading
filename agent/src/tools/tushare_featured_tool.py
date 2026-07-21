"""Tool: ``get_featured_data`` — Tushare featured data (41 kinds).

Requires the corresponding Tushare privileges.
This tool is strictly read-only and returns JSON-serialized results.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from langchain_core.tools import tool

from src.agent.tools import BaseTool

logger = logging.getLogger(__name__)

_VALID_KINDS = frozenset({
    # Existing 16
    "cyq_perf", "cyq_chips", "limit_list", "ths_hot",
    "ths_index", "ths_member", "share_float",
    "pledge_stat", "repurchase", "holdertrade", "stock_st", "hsgt_stocks",
    "stk_surv", "broker_recommend", "cn_macro", "forecast", "report_rc", "forecast_only",
    # New P0 (5)
    "stk_factor_pro", "hm_list", "hm_detail", "limit_list_ths", "limit_step",
    # New P1 (8 + 3 bridge + 6 new)
    "dc_hot", "kpl_list", "kpl_concept_cons", "ths_daily", "dc_daily",
    "pledge_detail", "margin", "margin_secs",
    "moneyflow", "top_list", "margin_detail",
    "dc_index", "dc_member", "tdx_index", "tdx_member", "tdx_daily", "limit_cpt_list",
    # New P2 (3, macro indicators routed via cn_macro)
    "top_inst", "idx_factor_pro", "fund_factor_pro",
})


# 核心实现函数（无装饰器，可被任何调用者安全使用）
def execute_featured_data_json(
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
    name: str = "",
    hm_name: str = "",
    tag: str = "",
    con_code: str = "",
    hot_type: str = "",
    is_new: str = "",
    idx_type: str = "",
    nums: str = "",
    exchange_id: str = "",
    start_month: str = "",
    end_month: str = "",
) -> str:
    """查询 Tushare 特色数据（需要相应特权）。

    支持四十一种数据类型：

    P0 — 筹码/涨跌停/热榜/技术因子/游资/连板：
    - ``kind="cyq_perf"``：每日筹码及胜率（筹码成本、加权平均成本、胜率）。
    - ``kind="cyq_chips"``：每日筹码分布（各价格区间的持仓量）。
    - ``kind="limit_list"``：涨跌停榜单（含封单、炸板、连板信息）。
    - ``kind="ths_hot"``：同花顺热榜（个股/概念/ETF/转债热度排名）。
    - ``kind="stk_factor_pro"``：股票技术面因子（专业版）。
    - ``kind="hm_list"``：游资名录。
    - ``kind="hm_detail"``：游资每日交易明细。
    - ``kind="limit_list_ths"``：同花顺涨跌停榜单（完整版）。
    - ``kind="limit_step"``：连板天梯。

    P1 — 资金流向/龙虎榜/融资融券/板块/流通/热点/开盘啦：
    - ``kind="moneyflow"``：个股资金流向（小单/中单/大单/超大单买卖）。
    - ``kind="top_list"``：龙虎榜每日明细（营业部买卖席位）。
    - ``kind="margin_detail"``：融资融券每日明细（融资余额/融券余量）。
    - ``kind="ths_index"``：同花顺概念板块列表（概念/行业/地域）。
    - ``kind="ths_member"``：同花顺概念板块成分股。
    - ``kind="share_float"``：解禁/流通股本（流通股本、自由流通股本、总股本）。
    - ``kind="dc_hot"``：东方财富热榜。
    - ``kind="kpl_list"``：开盘啦榜单。
    - ``kind="kpl_concept_cons"``：开盘啦题材成分。
    - ``kind="ths_daily"``：同花顺板块行情。
    - ``kind="dc_daily"``：东方财富板块行情。
    - ``kind="pledge_detail"``：股权质押明细（必填 ts_code）。
    - ``kind="margin"``：融资融券汇总。
    - ``kind="margin_secs"``：融资融券标的列表。
    - ``kind="dc_index"``：东方财富概念板块。
    - ``kind="dc_member"``：东方财富板块成分（必填 ts_code）。
    - ``kind="tdx_index"``：通达信板块信息。
    - ``kind="tdx_member"``：通达信板块成分（必填 ts_code）。
    - ``kind="tdx_daily"``：通达信板块行情。
    - ``kind="limit_cpt_list"``：涨停最强板块统计。

    P2 — 质押/回购/增减持/ST/沪深港通/调研/金股/宏观/预测/龙虎榜机构/因子：
    - ``kind="pledge_stat"``：股权质押统计（质押比例、质押股数）。
    - ``kind="repurchase"``：股票回购（回购股数、金额、价格区间）。
    - ``kind="holdertrade"``：股东增减持（变动数量、变动比例）。
    - ``kind="stock_st"``：ST股票列表（S/ST/*ST）。
    - ``kind="hsgt_stocks"``：沪港通标的列表（买卖金额）。
    - ``kind="stk_surv"``：机构调研（调研机构、调研内容）。
    - ``kind="broker_recommend"``：券商金股（月度推荐金股及理由）。
    - ``kind="cn_macro"``：中国宏观经济（GDP/CPI/PPI/Shibor/PMI/货币供应/社融/LPR）。
    - ``kind="forecast"``：盈利预测（机构预测+业绩预告）。
    - ``kind="top_inst"``：龙虎榜机构交易明细。
    - ``kind="idx_factor_pro"``：指数专业因子。
    - ``kind="fund_factor_pro"``：场内基金专业因子。

    Args:
        kind: 数据类型，详见上方列表。
        ts_code: 股票/板块代码（如 000001.SZ 或 883900.TI），空字符串=全市场/全部。
        trade_date: 交易日期 YYYYMMDD。
        start_date: 起始日期 YYYYMMDD。
        end_date: 结束日期 YYYYMMDD。
        limit_type: limit_list(U/D/Z) 或 limit_list_ths(涨停池/连板池/冲刺涨停/炸板池/跌停池)。
        market: ths_hot(A/HK) 或 dc_hot 或 limit_list_ths(HS/GEM/STAR)。
        exchange: ths_index 交易所代码，或 margin_secs(SSE/SZSE/BSE)。
        type: ths_index 板块类型 N(概念)/I(行业)/R(地域)。
        ann_date: 公告日期 YYYYMMDD，用于 pledge_stat / repurchase / holdertrade。
        month: 月份 YYYYMM，用于 broker_recommend（券商金股）。
        indicator: 宏观指标，仅 cn_macro：gdp/cpi/ppi/shibor/pmi/money_supply/social_financing/lpr。
        name: 游资名称模糊查询，仅 hm_list。
        hm_name: 游资名称，仅 hm_detail。
        tag: 标签，仅 kpl_list。
        con_code: 股票代码，仅 kpl_concept_cons。
        hot_type: 热榜类型，仅 dc_hot。
        is_new: Y/N，仅 dc_hot。
        idx_type: 板块类型，仅 dc_daily。
        nums: 连板天数，仅 limit_step。
        exchange_id: SSE/SZSE/BSE，仅 margin.
        start_month: 起始月份 YYYYMM，用于 cn_pmi/cn_m/sf_month（由 cn_macro 内部路由）。
        end_month: 结束月份 YYYYMM，用于 cn_pmi/cn_m/sf_month（由 cn_macro 内部路由）。

    Returns:
        JSON 字符串，包含 ``data_source``、``endpoint``、``retrieved_at``、
        ``is_provisional`` 和 ``data``（数据列表）。
    """
    kind = kind.strip().lower()
    if kind not in _VALID_KINDS:
        return json.dumps(
            {
                "error": f"Invalid kind: {kind!r}. Must be one of {sorted(_VALID_KINDS)}.",
                "hint": "Use one of: cyq_perf, cyq_chips, limit_list, ths_hot, "
                "stk_factor_pro, hm_list, hm_detail, limit_list_ths, limit_step, "
                "moneyflow, top_list, margin_detail, "
                "ths_index, ths_member, share_float, dc_hot, kpl_list, "
                "kpl_concept_cons, ths_daily, dc_daily, pledge_detail, margin, "
                "margin_secs, dc_index, dc_member, tdx_index, tdx_member, "
                "tdx_daily, limit_cpt_list, "
                "pledge_stat, repurchase, holdertrade, stock_st, "
                "hsgt_stocks, stk_surv, broker_recommend, cn_macro, forecast, "
                "report_rc, forecast_only, "
                "top_inst, idx_factor_pro, fund_factor_pro.",
            },
            ensure_ascii=False,
            indent=2,
        )

    try:
        from backtest.loaders.tushare_featured import TushareFeaturedProvider
        from src.core.tushare_market_data import TushareMarketDataService

        svc = TushareMarketDataService(
            featured_provider=TushareFeaturedProvider(),
        )

        # Build kwargs dict from the function parameters
        call_kwargs: dict[str, Any] = {}
        if ts_code:
            call_kwargs["ts_code"] = ts_code
        if trade_date:
            call_kwargs["trade_date"] = trade_date
        if start_date:
            call_kwargs["start_date"] = start_date
        if end_date:
            call_kwargs["end_date"] = end_date
        if limit_type:
            call_kwargs["limit_type"] = limit_type
        if market:
            call_kwargs["market"] = market
        if exchange:
            call_kwargs["exchange"] = exchange
        if type:
            call_kwargs["type"] = type
        if ann_date:
            call_kwargs["ann_date"] = ann_date
        if month:
            call_kwargs["month"] = month
        if indicator:
            call_kwargs["indicator"] = indicator
        if name:
            call_kwargs["name"] = name
        if hm_name:
            call_kwargs["hm_name"] = hm_name
        if tag:
            call_kwargs["tag"] = tag
        if con_code:
            call_kwargs["con_code"] = con_code
        if hot_type:
            call_kwargs["hot_type"] = hot_type
        if is_new:
            call_kwargs["is_new"] = is_new
        if idx_type:
            call_kwargs["idx_type"] = idx_type
        if nums:
            call_kwargs["nums"] = nums
        if exchange_id:
            call_kwargs["exchange_id"] = exchange_id
        if start_month:
            call_kwargs["start_month"] = start_month
        if end_month:
            call_kwargs["end_month"] = end_month

        result = svc.get_featured_data(kind=kind, **call_kwargs)
    except RuntimeError as exc:
        return json.dumps(
            {"error": str(exc), "hint": "Set TUSHARE_TOKEN in your environment."},
            ensure_ascii=False,
            indent=2,
        )
    except Exception as exc:
        logger.exception("get_featured_data failed for kind=%s", kind)
        return json.dumps(
            {"error": str(exc)},
            ensure_ascii=False,
            indent=2,
        )

    return json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)


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
    name: str = "",
    hm_name: str = "",
    tag: str = "",
    con_code: str = "",
    hot_type: str = "",
    is_new: str = "",
    idx_type: str = "",
    nums: str = "",
    exchange_id: str = "",
    start_month: str = "",
    end_month: str = "",
) -> str:
    """查询 Tushare 特色数据（需要相应特权）。"""
    return execute_featured_data_json(
        kind=kind,
        ts_code=ts_code,
        trade_date=trade_date,
        start_date=start_date,
        end_date=end_date,
        limit_type=limit_type,
        market=market,
        exchange=exchange,
        type=type,
        ann_date=ann_date,
        month=month,
        indicator=indicator,
        name=name,
        hm_name=hm_name,
        tag=tag,
        con_code=con_code,
        hot_type=hot_type,
        is_new=is_new,
        idx_type=idx_type,
        nums=nums,
        exchange_id=exchange_id,
        start_month=start_month,
        end_month=end_month,
    )


class TushareFeaturedTool(BaseTool):
    """BaseTool adapter for get_featured_data."""
    name = "get_featured_data"
    description = get_featured_data.__doc__
    parameters = {
        "type": "object",
        "properties": {
            "kind": {
                "type": "string",
                "enum": sorted(_VALID_KINDS),
                "description": "Data type. See @tool docstring for full list.",
            },
            "ts_code": {"type": "string", "description": "Stock/concept code"},
            "trade_date": {"type": "string", "description": "YYYYMMDD"},
            "start_date": {"type": "string", "description": "YYYYMMDD"},
            "end_date": {"type": "string", "description": "YYYYMMDD"},
            "limit_type": {"type": "string", "description": "U/D/Z"},
            "market": {"type": "string", "description": "A/HK etc"},
            "exchange": {"type": "string", "description": "SSE/SZSE/BSE"},
            "type": {"type": "string", "description": "N/I/R"},
            "ann_date": {"type": "string", "description": "YYYYMMDD"},
            "month": {"type": "string", "description": "YYYYMM"},
            "indicator": {"type": "string", "description": "Macro indicator name"},
            "name": {"type": "string", "description": "HM name fuzzy search"},
            "hm_name": {"type": "string", "description": "HM exact name"},
            "tag": {"type": "string"},
            "con_code": {"type": "string"},
            "hot_type": {"type": "string"},
            "is_new": {"type": "string"},
            "idx_type": {"type": "string"},
            "nums": {"type": "string"},
            "exchange_id": {"type": "string"},
            "start_month": {"type": "string", "description": "YYYYMM"},
            "end_month": {"type": "string", "description": "YYYYMM"},
        },
        "required": ["kind"],
    }
    is_readonly = True

    def execute(self, **kwargs: Any) -> str:
        return execute_featured_data_json(**kwargs)
