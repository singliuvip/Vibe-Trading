"""Tushare featured data provider (19 endpoints).

Wraps ``ts.pro_api()`` for: cyq_perf, cyq_chips, limit_list, ths_hot,
moneyflow, top_list, margin_detail, ths_index, ths_member, share_float,
pledge_stat, repurchase, holdertrade, stock_st, hsgt_stocks, stk_surv,
broker_recommend, cn_macro (gdp/cpi/ppi/shibor), forecast.
Requires the corresponding Tushare privileges.
No disk cache is written — data is always fetched live from Tushare.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

from backtest.loaders._tushare_constants import TUSHARE_TOKEN_PLACEHOLDERS

# Valid ``limit_type`` values for the ``limit_list_d`` endpoint.
_VALID_LIMIT_TYPES = frozenset({"U", "D", "Z", ""})

# Valid ``market`` values for the ``ths_hot`` endpoint.
_VALID_THS_HOT_MARKETS = frozenset({"A", "HK", ""})


class TushareFeaturedProvider:
    """Provider for Tushare featured data (19 endpoints).

    P0 — chip distribution / limit boards / hot rank:
      ``cyq_perf``, ``cyq_chips``, ``limit_list_d``, ``ths_hot``
    P1 — fund flow / dragon-tiger / margin / sector / share float:
      ``moneyflow``, ``top_list``, ``margin_detail``, ``ths_index``,
      ``ths_member``, ``share_float``
    P2 — pledge / repurchase / holder trade / ST / HSGT / survey /
          broker picks / macro / forecast:
      ``pledge_stat``, ``repurchase``, ``holdertrade``, ``stock_st``,
      ``hsgt_stocks``, ``stk_surv``, ``broker_recommend``, ``cn_macro``
      (gdp/cpi/ppi/shibor), ``forecast`` (report_rc + forecast fallback)
    """

    name = "tushare_featured"

    def __init__(self) -> None:
        """Resolve the Tushare token and instantiate the pro API client.

        Raises:
            RuntimeError: If the Tushare token is not configured or is a
                placeholder value.
        """
        from src.config.accessor import get_env_config

        token = get_env_config().data.tushare_token.strip()
        if token in TUSHARE_TOKEN_PLACEHOLDERS:
            raise RuntimeError(
                "Tushare token not configured — set TUSHARE_TOKEN in your environment"
            )
        import tushare as ts

        self._pro = ts.pro_api(token)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _make_envelope(
        self,
        endpoint: str,
        data: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Build the standard response envelope.

        Args:
            endpoint: Tushare endpoint name (e.g. ``"cyq_perf"``).
            data: List of row dicts.

        Returns:
            Envelope dict with ``data_source``, ``endpoint``, ``retrieved_at``,
            ``is_provisional``, and ``data``.
        """
        return {
            "data_source": "tushare",
            "endpoint": endpoint,
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "is_provisional": False,
            "data": data,
        }

    def _make_error_envelope(
        self,
        endpoint: str,
        error: str,
    ) -> dict[str, Any]:
        """Build an error envelope with an empty data list.

        Args:
            endpoint: Tushare endpoint name.
            error: Human-readable error message.

        Returns:
            Envelope dict with ``error`` and empty ``data``.
        """
        return {
            "data_source": "tushare",
            "endpoint": endpoint,
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "is_provisional": False,
            "error": error,
            "data": [],
        }

    def _rows_from_frame(self, frame: Any) -> list[dict[str, Any]]:
        """Convert a pandas DataFrame to a list of dicts, JSON-safe.

        Args:
            frame: A pandas DataFrame returned by the Tushare API, or None.

        Returns:
            A list of row dicts; empty list if ``frame`` is None or empty.
        """
        if frame is None:
            return []
        try:
            if hasattr(frame, "empty") and frame.empty:
                return []
        except Exception:
            pass
        if not hasattr(frame, "to_dict"):
            return []
        import numpy as np

        frame = frame.where(frame.notna(), None)
        rows: list[dict[str, Any]] = frame.to_dict(orient="records")
        clean_rows: list[dict[str, Any]] = []
        for row in rows:
            clean: dict[str, Any] = {}
            for k, v in row.items():
                if isinstance(v, (np.integer,)):
                    clean[k] = int(v)
                elif isinstance(v, (np.floating,)):
                    clean[k] = float(v) if not np.isnan(v) else None
                elif isinstance(v, np.bool_):
                    clean[k] = bool(v)
                else:
                    clean[k] = v
            clean_rows.append(clean)
        return clean_rows

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def fetch_cyq_perf(
        self,
        ts_code: str = "",
        trade_date: str = "",
        start_date: str = "",
        end_date: str = "",
    ) -> dict[str, Any]:
        """每日筹码及胜率。pro.cyq_perf(...)

        Args:
            ts_code: 股票代码（如 000001.SZ），空字符串=全市场。
            trade_date: 交易日期 YYYYMMDD。
            start_date: 起始日期 YYYYMMDD。
            end_date: 结束日期 YYYYMMDD。

        Returns:
            Envelope with data list containing ts_code, trade_date, his_low,
            his_high, cost_5pct, cost_15pct, cost_50pct, cost_85pct, cost_95pct,
            weight_avg, winner_rate.
        """
        endpoint = "cyq_perf"

        try:
            kwargs: dict[str, Any] = {}
            if ts_code:
                kwargs["ts_code"] = ts_code
            if trade_date:
                kwargs["trade_date"] = trade_date
            if start_date:
                kwargs["start_date"] = start_date
            if end_date:
                kwargs["end_date"] = end_date
            df = self._pro.cyq_perf(**kwargs)
        except Exception as exc:
            logger.exception("cyq_perf call failed")
            return self._make_error_envelope(endpoint, str(exc))

        rows = self._rows_from_frame(df)
        return self._make_envelope(endpoint, rows)

    def fetch_cyq_chips(
        self,
        ts_code: str = "",
        trade_date: str = "",
        start_date: str = "",
        end_date: str = "",
    ) -> dict[str, Any]:
        """每日筹码分布。pro.cyq_chips(...)

        Args:
            ts_code: 股票代码（如 000001.SZ），空字符串=全市场。
            trade_date: 交易日期 YYYYMMDD。
            start_date: 起始日期 YYYYMMDD。
            end_date: 结束日期 YYYYMMDD。

        Returns:
            Envelope with data list containing ts_code, trade_date, price,
            volume.
        """
        endpoint = "cyq_chips"

        try:
            kwargs: dict[str, Any] = {}
            if ts_code:
                kwargs["ts_code"] = ts_code
            if trade_date:
                kwargs["trade_date"] = trade_date
            if start_date:
                kwargs["start_date"] = start_date
            if end_date:
                kwargs["end_date"] = end_date
            df = self._pro.cyq_chips(**kwargs)
        except Exception as exc:
            logger.exception("cyq_chips call failed")
            return self._make_error_envelope(endpoint, str(exc))

        rows = self._rows_from_frame(df)
        return self._make_envelope(endpoint, rows)

    def fetch_limit_list(
        self,
        trade_date: str = "",
        ts_code: str = "",
        limit_type: str = "",
    ) -> dict[str, Any]:
        """涨跌停榜单（含封单、炸板、连板）。pro.limit_list_d(...)

        Args:
            trade_date: 交易日期 YYYYMMDD。
            ts_code: 股票代码（如 000001.SZ），空字符串=全市场。
            limit_type: "U"(涨停) / "D"(跌停) / "Z"(炸板)，空字符串=全部。

        Returns:
            Envelope with data list.
        """
        endpoint = "limit_list_d"

        if limit_type and limit_type not in _VALID_LIMIT_TYPES:
            return self._make_error_envelope(
                endpoint,
                f"Invalid limit_type: {limit_type!r}. Must be one of "
                f"{sorted(t for t in _VALID_LIMIT_TYPES if t)} or empty string.",
            )

        try:
            kwargs: dict[str, Any] = {}
            if trade_date:
                kwargs["trade_date"] = trade_date
            if ts_code:
                kwargs["ts_code"] = ts_code
            if limit_type:
                kwargs["limit_type"] = limit_type
            df = self._pro.limit_list_d(**kwargs)
        except Exception as exc:
            logger.exception("limit_list_d call failed")
            return self._make_error_envelope(endpoint, str(exc))

        rows = self._rows_from_frame(df)
        return self._make_envelope(endpoint, rows)

    def fetch_ths_hot(
        self,
        trade_date: str = "",
        market: str = "",
    ) -> dict[str, Any]:
        """同花顺热榜（个股/概念/ETF/转债）。pro.ths_hot(...)

        Args:
            trade_date: 交易日期 YYYYMMDD。
            market: "A"(A股) / "HK"(港股)，空字符串=全部。

        Returns:
            Envelope with data list.
        """
        endpoint = "ths_hot"

        if market and market not in _VALID_THS_HOT_MARKETS:
            return self._make_error_envelope(
                endpoint,
                f"Invalid market: {market!r}. Must be one of "
                f"{sorted(t for t in _VALID_THS_HOT_MARKETS if t)} or empty string.",
            )

        try:
            kwargs: dict[str, Any] = {}
            if trade_date:
                kwargs["trade_date"] = trade_date
            if market:
                kwargs["market"] = market
            df = self._pro.ths_hot(**kwargs)
        except Exception as exc:
            logger.exception("ths_hot call failed")
            return self._make_error_envelope(endpoint, str(exc))

        rows = self._rows_from_frame(df)
        return self._make_envelope(endpoint, rows)

    # ------------------------------------------------------------------
    # P1-1: fund flow / dragon-tiger / margin trading
    # ------------------------------------------------------------------

    def fetch_moneyflow(
        self,
        ts_code: str = "",
        trade_date: str = "",
        start_date: str = "",
        end_date: str = "",
    ) -> dict[str, Any]:
        """个股资金流向。pro.moneyflow(ts_code=..., trade_date=..., ...)

        Args:
            ts_code: 股票代码（如 000001.SZ），空字符串=全市场。
            trade_date: 交易日期 YYYYMMDD。
            start_date: 起始日期 YYYYMMDD。
            end_date: 结束日期 YYYYMMDD。

        Returns:
            Envelope with data list containing ts_code, trade_date, buy_sm_vol,
            buy_sm_amount, sell_sm_vol, sell_sm_amount, buy_md_vol, buy_md_amount,
            sell_md_vol, sell_md_amount, buy_lg_vol, buy_lg_amount, sell_lg_vol,
            sell_lg_amount, buy_elg_vol, buy_elg_amount, sell_elg_vol,
            sell_elg_amount, net_mf_vol, net_mf_amount.
        """
        endpoint = "moneyflow"

        try:
            kwargs: dict[str, Any] = {}
            if ts_code:
                kwargs["ts_code"] = ts_code
            if trade_date:
                kwargs["trade_date"] = trade_date
            if start_date:
                kwargs["start_date"] = start_date
            if end_date:
                kwargs["end_date"] = end_date
            df = self._pro.moneyflow(**kwargs)
        except Exception as exc:
            logger.exception("moneyflow call failed")
            return self._make_error_envelope(endpoint, str(exc))

        rows = self._rows_from_frame(df)
        return self._make_envelope(endpoint, rows)

    def fetch_top_list(
        self,
        trade_date: str = "",
        ts_code: str = "",
    ) -> dict[str, Any]:
        """龙虎榜每日明细。pro.top_list(trade_date=..., ts_code=...)

        Args:
            trade_date: 交易日期 YYYYMMDD。
            ts_code: 股票代码（如 000001.SZ），空字符串=全市场。

        Returns:
            Envelope with data list containing trade_date, ts_code, name,
            close, pct_change, turnover_rate, amount, l_buy, l_sell, l_net,
            and seat-level buy/sell details.
        """
        endpoint = "top_list"

        try:
            kwargs: dict[str, Any] = {}
            if trade_date:
                kwargs["trade_date"] = trade_date
            if ts_code:
                kwargs["ts_code"] = ts_code
            df = self._pro.top_list(**kwargs)
        except Exception as exc:
            logger.exception("top_list call failed")
            return self._make_error_envelope(endpoint, str(exc))

        rows = self._rows_from_frame(df)
        return self._make_envelope(endpoint, rows)

    def fetch_margin_detail(
        self,
        ts_code: str = "",
        trade_date: str = "",
        start_date: str = "",
        end_date: str = "",
    ) -> dict[str, Any]:
        """融资融券每日明细。pro.margin_detail(ts_code=..., ...)

        Args:
            ts_code: 股票代码（如 000001.SZ），空字符串=全市场。
            trade_date: 交易日期 YYYYMMDD。
            start_date: 起始日期 YYYYMMDD。
            end_date: 结束日期 YYYYMMDD。

        Returns:
            Envelope with data list containing ts_code, trade_date, rzye(融资余额),
            rqye(融券余额), rzmre(融资买入额), rzche(融资偿还额), rqyl(融券余量),
            rqmcl(融券卖出量), rzrqye(融资融券余额).
        """
        endpoint = "margin_detail"

        try:
            kwargs: dict[str, Any] = {}
            if ts_code:
                kwargs["ts_code"] = ts_code
            if trade_date:
                kwargs["trade_date"] = trade_date
            if start_date:
                kwargs["start_date"] = start_date
            if end_date:
                kwargs["end_date"] = end_date
            df = self._pro.margin_detail(**kwargs)
        except Exception as exc:
            logger.exception("margin_detail call failed")
            return self._make_error_envelope(endpoint, str(exc))

        rows = self._rows_from_frame(df)
        return self._make_envelope(endpoint, rows)

    # ------------------------------------------------------------------
    # P1-2: concept sector / share float
    # ------------------------------------------------------------------

    def fetch_ths_index(
        self,
        ts_code: str = "",
        exchange: str = "",
        type: str = "",
    ) -> dict[str, Any]:
        """同花顺概念板块列表。pro.ths_index(...)

        Args:
            ts_code: 板块代码（如 883900.TI），空字符串=全部。
            exchange: 交易所代码（如 "A"=A股），空字符串=全部。
            type: 板块类型 — "N"(概念) / "I"(行业) / "R"(地域)，空字符串=全部。

        Returns:
            Envelope with data list containing ts_code, name, type,
            count(成分股数量), exchange, list_date.
        """
        endpoint = "ths_index"

        try:
            kwargs: dict[str, Any] = {}
            if ts_code:
                kwargs["ts_code"] = ts_code
            if exchange:
                kwargs["exchange"] = exchange
            if type:
                kwargs["type"] = type
            df = self._pro.ths_index(**kwargs)
        except Exception as exc:
            logger.exception("ths_index call failed")
            return self._make_error_envelope(endpoint, str(exc))

        rows = self._rows_from_frame(df)
        return self._make_envelope(endpoint, rows)

    def fetch_ths_member(
        self,
        ts_code: str = "",
    ) -> dict[str, Any]:
        """同花顺概念板块成分。pro.ths_member(ts_code=...)

        Args:
            ts_code: 板块代码（如 883900.TI）。

        Returns:
            Envelope with data list containing ts_code(板块代码),
            con_code(成分代码), name(成分名称).
        """
        endpoint = "ths_member"

        try:
            kwargs: dict[str, Any] = {}
            if ts_code:
                kwargs["ts_code"] = ts_code
            df = self._pro.ths_member(**kwargs)
        except Exception as exc:
            logger.exception("ths_member call failed")
            return self._make_error_envelope(endpoint, str(exc))

        rows = self._rows_from_frame(df)
        return self._make_envelope(endpoint, rows)

    def fetch_share_float(
        self,
        ts_code: str = "",
        trade_date: str = "",
        start_date: str = "",
        end_date: str = "",
    ) -> dict[str, Any]:
        """解禁/流通股本。pro.share_float(ts_code=..., ...)

        Args:
            ts_code: 股票代码（如 000001.SZ），空字符串=全市场。
            trade_date: 交易日期 YYYYMMDD。
            start_date: 起始日期 YYYYMMDD。
            end_date: 结束日期 YYYYMMDD。

        Returns:
            Envelope with data list containing ts_code, trade_date,
            float_share(流通股本), free_share(自由流通股本),
            total_share(总股本), ann_date(公告日期).
        """
        endpoint = "share_float"

        try:
            kwargs: dict[str, Any] = {}
            if ts_code:
                kwargs["ts_code"] = ts_code
            if trade_date:
                kwargs["trade_date"] = trade_date
            if start_date:
                kwargs["start_date"] = start_date
            if end_date:
                kwargs["end_date"] = end_date
            df = self._pro.share_float(**kwargs)
        except Exception as exc:
            logger.exception("share_float call failed")
            return self._make_error_envelope(endpoint, str(exc))

        rows = self._rows_from_frame(df)
        return self._make_envelope(endpoint, rows)

    # ------------------------------------------------------------------
    # P2-1: pledge / repurchase / holdertrade / stock_st / hsgt_stocks
    # ------------------------------------------------------------------

    def fetch_pledge_stat(
        self,
        ts_code: str = "",
    ) -> dict[str, Any]:
        """股权质押统计。pro.pledge_stat(ts_code=...)

        Args:
            ts_code: 股票代码（如 000001.SZ），空字符串=全市场。

        Returns:
            Envelope with data list containing ts_code, end_date,
            pledge_ratio(质押比例), pledge_total(质押股数) 等.
        """
        endpoint = "pledge_stat"

        try:
            kwargs: dict[str, Any] = {}
            if ts_code:
                kwargs["ts_code"] = ts_code
            df = self._pro.pledge_stat(**kwargs)
        except Exception as exc:
            logger.exception("pledge_stat call failed")
            return self._make_error_envelope(endpoint, str(exc))

        rows = self._rows_from_frame(df)
        return self._make_envelope(endpoint, rows)

    def fetch_repurchase(
        self,
        ts_code: str = "",
        ann_date: str = "",
        start_date: str = "",
        end_date: str = "",
    ) -> dict[str, Any]:
        """股票回购。pro.repurchase(...)

        Args:
            ts_code: 股票代码（如 000001.SZ），空字符串=全市场。
            ann_date: 公告日期 YYYYMMDD。
            start_date: 起始日期 YYYYMMDD。
            end_date: 结束日期 YYYYMMDD。

        Returns:
            Envelope with data list containing ts_code, ann_date,
            vol, amount, high_limit, low_limit 等.
        """
        endpoint = "repurchase"

        try:
            kwargs: dict[str, Any] = {}
            if ts_code:
                kwargs["ts_code"] = ts_code
            if ann_date:
                kwargs["ann_date"] = ann_date
            if start_date:
                kwargs["start_date"] = start_date
            if end_date:
                kwargs["end_date"] = end_date
            df = self._pro.repurchase(**kwargs)
        except Exception as exc:
            logger.exception("repurchase call failed")
            return self._make_error_envelope(endpoint, str(exc))

        rows = self._rows_from_frame(df)
        return self._make_envelope(endpoint, rows)

    def fetch_holdertrade(
        self,
        ts_code: str = "",
        ann_date: str = "",
        start_date: str = "",
        end_date: str = "",
    ) -> dict[str, Any]:
        """股东增减持。pro.stk_holdertrade(...)

        Args:
            ts_code: 股票代码（如 000001.SZ），空字符串=全市场。
            ann_date: 公告日期 YYYYMMDD。
            start_date: 起始日期 YYYYMMDD。
            end_date: 结束日期 YYYYMMDD。

        Returns:
            Envelope with data list containing ts_code, ann_date,
            holder_name, change_vol, change_ratio 等.
        """
        endpoint = "holdertrade"

        try:
            kwargs: dict[str, Any] = {}
            if ts_code:
                kwargs["ts_code"] = ts_code
            if ann_date:
                kwargs["ann_date"] = ann_date
            if start_date:
                kwargs["start_date"] = start_date
            if end_date:
                kwargs["end_date"] = end_date
            df = self._pro.stk_holdertrade(**kwargs)
        except Exception as exc:
            logger.exception("holdertrade call failed")
            return self._make_error_envelope(endpoint, str(exc))

        rows = self._rows_from_frame(df)
        return self._make_envelope(endpoint, rows)

    def fetch_stock_st(
        self,
        ts_code: str = "",
        trade_date: str = "",
    ) -> dict[str, Any]:
        """ST股票列表。pro.stock_st(...)

        Args:
            ts_code: 股票代码（如 000001.SZ），空字符串=全市场。
            trade_date: 交易日期 YYYYMMDD。

        Returns:
            Envelope with data list containing ts_code, name,
            type(S/ST/*ST), ann_date 等.
        """
        endpoint = "stock_st"

        try:
            kwargs: dict[str, Any] = {}
            if ts_code:
                kwargs["ts_code"] = ts_code
            if trade_date:
                kwargs["trade_date"] = trade_date
            df = self._pro.stock_st(**kwargs)
        except Exception as exc:
            logger.exception("stock_st call failed")
            return self._make_error_envelope(endpoint, str(exc))

        rows = self._rows_from_frame(df)
        return self._make_envelope(endpoint, rows)

    def fetch_hsgt_stocks(
        self,
        trade_date: str = "",
    ) -> dict[str, Any]:
        """沪港通标的列表。pro.stock_hsgt(...)

        Args:
            trade_date: 交易日期 YYYYMMDD。

        Returns:
            Envelope with data list containing ts_code, name,
            market, buy_amount, sell_amount 等.
        """
        endpoint = "hsgt_stocks"

        try:
            kwargs: dict[str, Any] = {}
            if trade_date:
                kwargs["trade_date"] = trade_date
            df = self._pro.stock_hsgt(**kwargs)
        except Exception as exc:
            logger.exception("hsgt_stocks call failed")
            return self._make_error_envelope(endpoint, str(exc))

        rows = self._rows_from_frame(df)
        return self._make_envelope(endpoint, rows)

    # ------------------------------------------------------------------
    # P2-2: stk_surv / broker_recommend / cn_macro / forecast
    # ------------------------------------------------------------------

    def fetch_stk_surv(
        self,
        ts_code: str = "",
        start_date: str = "",
        end_date: str = "",
    ) -> dict[str, Any]:
        """机构调研。pro.stk_surv(ts_code=..., ...)

        Args:
            ts_code: 股票代码（如 000001.SZ），空字符串=全市场。
            start_date: 起始日期 YYYYMMDD。
            end_date: 结束日期 YYYYMMDD。

        Returns:
            Envelope with data list containing ts_code, surv_date,
            fund_name(调研机构), content(调研内容) 等.
        """
        endpoint = "stk_surv"

        try:
            kwargs: dict[str, Any] = {}
            if ts_code:
                kwargs["ts_code"] = ts_code
            if start_date:
                kwargs["start_date"] = start_date
            if end_date:
                kwargs["end_date"] = end_date
            df = self._pro.stk_surv(**kwargs)
        except Exception as exc:
            logger.exception("stk_surv call failed")
            return self._make_error_envelope(endpoint, str(exc))

        rows = self._rows_from_frame(df)
        return self._make_envelope(endpoint, rows)

    def fetch_broker_recommend(
        self,
        month: str = "",
    ) -> dict[str, Any]:
        """券商金股。pro.broker_recommend(month=YYYYMM)

        Args:
            month: 月份 YYYYMM。

        Returns:
            Envelope with data list containing month, ts_code, name,
            broker(券商名称), reason(推荐理由) 等.
        """
        endpoint = "broker_recommend"

        try:
            kwargs: dict[str, Any] = {}
            if month:
                kwargs["month"] = month
            df = self._pro.broker_recommend(**kwargs)
        except Exception as exc:
            logger.exception("broker_recommend call failed")
            return self._make_error_envelope(endpoint, str(exc))

        rows = self._rows_from_frame(df)
        return self._make_envelope(endpoint, rows)

    def fetch_cn_macro(
        self,
        indicator: str = "",
        start_date: str = "",
        end_date: str = "",
    ) -> dict[str, Any]:
        """中国宏观经济。根据 indicator 路由到对应 Tushare 端点。

        Args:
            indicator: 指标类型 — "gdp" / "cpi" / "ppi" / "shibor"。
            start_date: 起始日期 YYYYMMDD。
            end_date: 结束日期 YYYYMMDD。

        Returns:
            Envelope with data list.
        """
        endpoint_map = {
            "gdp": ("cn_gdp", "cn_gdp"),
            "cpi": ("cn_cpi", "cn_cpi"),
            "ppi": ("cn_ppi", "cn_ppi"),
            "shibor": ("shibor", "shibor"),
        }

        if not indicator:
            return self._make_error_envelope(
                "cn_macro",
                "indicator is required for cn_macro. Must be one of: gdp, cpi, ppi, shibor.",
            )

        indicator = indicator.strip().lower()
        if indicator not in endpoint_map:
            return self._make_error_envelope(
                "cn_macro",
                f"Invalid indicator: {indicator!r}. Must be one of: gdp, cpi, ppi, shibor.",
            )

        tushare_method, endpoint = endpoint_map[indicator]

        try:
            kwargs: dict[str, Any] = {}
            if start_date:
                kwargs["start_date"] = start_date
            if end_date:
                kwargs["end_date"] = end_date
            df = getattr(self._pro, tushare_method)(**kwargs)
        except Exception as exc:
            logger.exception("%s call failed", tushare_method)
            return self._make_error_envelope(endpoint, str(exc))

        rows = self._rows_from_frame(df)
        return self._make_envelope(endpoint, rows)

    def fetch_forecast(
        self,
        ts_code: str = "",
        start_date: str = "",
        end_date: str = "",
    ) -> dict[str, Any]:
        """盈利预测。优先 pro.report_rc（机构预测），fallback pro.forecast（业绩预告）。

        Args:
            ts_code: 股票代码（如 000001.SZ），空字符串=全市场。
            start_date: 起始日期 YYYYMMDD。
            end_date: 结束日期 YYYYMMDD。

        Returns:
            Envelope with data list.
        """
        endpoint = "forecast"

        kwargs: dict[str, Any] = {}
        if ts_code:
            kwargs["ts_code"] = ts_code
        if start_date:
            kwargs["start_date"] = start_date
        if end_date:
            kwargs["end_date"] = end_date

        # 优先 report_rc（机构预测）
        try:
            df = self._pro.report_rc(**kwargs)
        except Exception:
            logger.warning("report_rc call failed, falling back to forecast")
            df = None

        if df is not None and not (hasattr(df, "empty") and df.empty):
            rows = self._rows_from_frame(df)
            result = self._make_envelope("report_rc", rows)
            result["endpoint"] = endpoint  # 统一为 forecast
            return result

        # fallback: forecast（业绩预告）
        try:
            df = self._pro.forecast(**kwargs)
        except Exception as exc:
            logger.exception("forecast call failed")
            return self._make_error_envelope(endpoint, str(exc))

        rows = self._rows_from_frame(df)
        return self._make_envelope(endpoint, rows)
