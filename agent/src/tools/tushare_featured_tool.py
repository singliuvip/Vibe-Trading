"""Tool: ``get_featured_data`` — Tushare featured data (41 kinds).

Requires the corresponding Tushare privileges.
This tool is strictly read-only and returns JSON-serialized results.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
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
    from backtest.loaders.tushare_featured import TushareFeaturedProvider

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
                trade_date=trade_date,
                start_date=start_date,
                end_date=end_date,
                start_month=start_month,
                end_month=end_month,
            )
        elif kind == "forecast":
            result = provider.fetch_forecast(
                ts_code=ts_code,
                start_date=start_date,
                end_date=end_date,
            )
        elif kind == "report_rc":
            result = provider.fetch_report_rc(
                ts_code=ts_code,
                start_date=start_date,
                end_date=end_date,
            )
        elif kind == "forecast_only":
            result = provider.fetch_forecast_only(
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

    # ── New P0/P1/P2: all 16 new kinds ──
    try:
        if kind == "stk_factor_pro":
            result = provider.fetch_stk_factor_pro(
                ts_code=ts_code,
                trade_date=trade_date,
                start_date=start_date,
                end_date=end_date,
            )
        elif kind == "hm_list":
            result = provider.fetch_hm_list(
                name=name,
            )
        elif kind == "hm_detail":
            result = provider.fetch_hm_detail(
                trade_date=trade_date,
                ts_code=ts_code,
                hm_name=hm_name,
                start_date=start_date,
                end_date=end_date,
            )
        elif kind == "limit_list_ths":
            result = provider.fetch_limit_list_ths(
                trade_date=trade_date,
                ts_code=ts_code,
                limit_type=limit_type,
                market=market,
                start_date=start_date,
                end_date=end_date,
            )
        elif kind == "limit_step":
            result = provider.fetch_limit_step(
                trade_date=trade_date,
                ts_code=ts_code,
                start_date=start_date,
                end_date=end_date,
                nums=nums,
            )
        elif kind == "dc_hot":
            result = provider.fetch_dc_hot(
                trade_date=trade_date,
                ts_code=ts_code,
                market=market,
                hot_type=hot_type,
                is_new=is_new,
            )
        elif kind == "kpl_list":
            result = provider.fetch_kpl_list(
                ts_code=ts_code,
                trade_date=trade_date,
                tag=tag,
                start_date=start_date,
                end_date=end_date,
            )
        elif kind == "kpl_concept_cons":
            result = provider.fetch_kpl_concept_cons(
                trade_date=trade_date,
                ts_code=ts_code,
                con_code=con_code,
            )
        elif kind == "ths_daily":
            result = provider.fetch_ths_daily(
                ts_code=ts_code,
                trade_date=trade_date,
                start_date=start_date,
                end_date=end_date,
            )
        elif kind == "dc_daily":
            result = provider.fetch_dc_daily(
                ts_code=ts_code,
                trade_date=trade_date,
                start_date=start_date,
                end_date=end_date,
                idx_type=idx_type,
            )
        elif kind == "pledge_detail":
            if not ts_code.strip():
                result = {
                    "data_source": "tushare",
                    "endpoint": "pledge_detail",
                    "retrieved_at": datetime.now(timezone.utc).isoformat(),
                    "is_provisional": False,
                    "error": "ts_code is required for pledge_detail",
                    "data": [],
                }
            else:
                result = provider.fetch_pledge_detail(ts_code=ts_code)
        elif kind == "margin":
            result = provider.fetch_margin(
                trade_date=trade_date,
                start_date=start_date,
                end_date=end_date,
                exchange_id=exchange_id,
            )
        elif kind == "margin_secs":
            result = provider.fetch_margin_secs(
                ts_code=ts_code,
                trade_date=trade_date,
                exchange=exchange,
                start_date=start_date,
                end_date=end_date,
            )
        elif kind == "top_inst":
            if not trade_date.strip():
                result = {
                    "data_source": "tushare",
                    "endpoint": "top_inst",
                    "retrieved_at": datetime.now(timezone.utc).isoformat(),
                    "is_provisional": False,
                    "error": "trade_date is required for top_inst",
                    "data": [],
                }
            else:
                result = provider.fetch_top_inst(
                    trade_date=trade_date,
                    ts_code=ts_code,
                )
        elif kind == "idx_factor_pro":
            result = provider.fetch_idx_factor_pro(
                ts_code=ts_code,
                trade_date=trade_date,
                start_date=start_date,
                end_date=end_date,
            )
        elif kind == "fund_factor_pro":
            result = provider.fetch_fund_factor_pro(
                ts_code=ts_code,
                trade_date=trade_date,
                start_date=start_date,
                end_date=end_date,
            )
        # ── P1 bridging: moneyflow / top_list / margin_detail ──
        elif kind == "moneyflow":
            result = provider.fetch_moneyflow(
                ts_code=ts_code,
                trade_date=trade_date,
                start_date=start_date,
                end_date=end_date,
            )
        elif kind == "top_list":
            result = provider.fetch_top_list(
                trade_date=trade_date,
                ts_code=ts_code,
            )
        elif kind == "margin_detail":
            result = provider.fetch_margin_detail(
                ts_code=ts_code,
                trade_date=trade_date,
                start_date=start_date,
                end_date=end_date,
            )
        # ── P1 new: dc_index / dc_member / tdx_index / tdx_member / tdx_daily / limit_cpt_list ──
        elif kind == "dc_index":
            result = provider.fetch_dc_index(
                ts_code=ts_code,
                trade_date=trade_date,
                start_date=start_date,
                end_date=end_date,
                market=market,
            )
        elif kind == "dc_member":
            if not ts_code.strip():
                result = {
                    "data_source": "tushare",
                    "endpoint": "dc_member",
                    "retrieved_at": datetime.now(timezone.utc).isoformat(),
                    "is_provisional": False,
                    "error": "ts_code is required for dc_member",
                    "data": [],
                }
            else:
                result = provider.fetch_dc_member(ts_code=ts_code)
        elif kind == "tdx_index":
            result = provider.fetch_tdx_index(
                ts_code=ts_code,
                trade_date=trade_date,
                start_date=start_date,
                end_date=end_date,
                market=market,
            )
        elif kind == "tdx_member":
            if not ts_code.strip():
                result = {
                    "data_source": "tushare",
                    "endpoint": "tdx_member",
                    "retrieved_at": datetime.now(timezone.utc).isoformat(),
                    "is_provisional": False,
                    "error": "ts_code is required for tdx_member",
                    "data": [],
                }
            else:
                result = provider.fetch_tdx_member(ts_code=ts_code)
        elif kind == "tdx_daily":
            result = provider.fetch_tdx_daily(
                ts_code=ts_code,
                trade_date=trade_date,
                start_date=start_date,
                end_date=end_date,
            )
        elif kind == "limit_cpt_list":
            result = provider.fetch_limit_cpt_list(
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

    return json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)


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
        return get_featured_data(**kwargs)
