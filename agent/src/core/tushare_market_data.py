"""Service layer for Tushare real-time market data (rt_k + auction).

Provides:
- ``TushareMarketDataService``: validation, rate-limiting, market-session
  tagging, and row capping on top of the two data-layer providers
  (``TushareRealtimeProvider`` / ``TushareAuctionProvider``).

This module lives in ``src/core/`` — no direct Tushare SDK calls.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Privilege helpers
# ---------------------------------------------------------------------------


def get_required_privilege(endpoint: str) -> int | None:
    """Return the minimum Tushare points required for *endpoint*, or None if unknown."""
    from backtest.loaders._tushare_constants import TUSHARE_PRIVILEGE_MAP

    return TUSHARE_PRIVILEGE_MAP.get(endpoint)


# ---------------------------------------------------------------------------
# Validation constants
# ---------------------------------------------------------------------------

_A_SHARE_CODE_RE = re.compile(r"^\d{6}\.(SH|SZ|BJ)$", re.I)
_DEFAULT_MAX_CODES = 50
_DEFAULT_MAX_PATTERNS = 3
_DEFAULT_MAX_ROWS = 500
_DEFAULT_MAX_MINUTE_ROWS = 1000

# Allowed frequencies for rt_min.
_VALID_MINUTE_FREQUENCIES = frozenset({"1MIN", "5MIN", "15MIN", "30MIN", "60MIN"})

# ---------------------------------------------------------------------------
# Simple in-process rate limiter (per-endpoint, min 1 s between calls)
# ---------------------------------------------------------------------------

_last_call: dict[str, float] = {}
_MIN_INTERVAL_S = 1.0


def _rate_limit(endpoint: str) -> None:
    """Enforce a minimum interval between calls to the same endpoint."""
    now = time.time()
    prev = _last_call.get(endpoint, 0)
    elapsed = now - prev
    if elapsed < _MIN_INTERVAL_S:
        time.sleep(_MIN_INTERVAL_S - elapsed)
    _last_call[endpoint] = time.time()


# ---------------------------------------------------------------------------
# Market-session detection (Asia/Shanghai timezone)
# ---------------------------------------------------------------------------

_CN_MORNING_OPEN = (9, 25)    # 09:25
_CN_AFTERNOON_CLOSE = (15, 5)  # 15:05


def _is_cn_market_session() -> bool:
    """Return True when now is within the CN A-share continuous auction window.

    Uses the server's local clock — best-effort.  Outside this window
    ``rt_k`` will still return data from the last trading day's close.
    """
    import zoneinfo

    try:
        tz = zoneinfo.ZoneInfo("Asia/Shanghai")
        now = datetime.now(tz)
    except Exception:
        # zoneinfo unavailable — fall back to assuming in-session.
        return True

    if now.weekday() >= 5:  # Saturday / Sunday
        return False

    tm = (now.hour, now.minute)
    return _CN_MORNING_OPEN <= tm <= _CN_AFTERNOON_CLOSE


def _is_past_trade_date(ymd: str) -> bool:
    """Return True if *ymd* (YYYYMMDD) is before today in Asia/Shanghai."""
    import zoneinfo

    try:
        tz = zoneinfo.ZoneInfo("Asia/Shanghai")
        trade_dt = datetime.strptime(ymd, "%Y%m%d").replace(tzinfo=tz)
        return trade_dt.date() < datetime.now(tz).date()
    except (ValueError, Exception):
        return False


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class TushareMarketDataService:
    """Validates inputs, rate-limits, and decorates provider responses.

    Instantiate with the two data-layer providers::

        svc = TushareMarketDataService(
            realtime_provider=TushareRealtimeProvider(),
            auction_provider=TushareAuctionProvider(),
        )
    """

    def __init__(
        self,
        realtime_provider: Any = None,
        auction_provider: Any = None,
        minute_provider: Any = None,
        refdata_provider: Any = None,
        featured_provider: Any = None,
        fundamentals_provider: Any = None,
    ) -> None:
        self._rt = realtime_provider
        self._auction = auction_provider
        self._minute = minute_provider
        self._refdata = refdata_provider
        self._featured = featured_provider
        self._fundamentals = fundamentals_provider

    # ------------------------------------------------------------------
    # get_realtime_quotes
    # ------------------------------------------------------------------

    def get_realtime_quotes(
        self,
        codes: list[str] | None = None,
        patterns: list[str] | None = None,
        fields: list[str] | None = None,
        max_rows: int = _DEFAULT_MAX_ROWS,
    ) -> dict[str, Any]:
        """Fetch A-share real-time daily K-line snapshots (``rt_k``).

        Input validation, rate-limit, and row-capping are applied before
        returning the provider envelope.  Returns the ``_meta``-compliant
        dict for downstream JSON serialization.

        Args:
            codes: List of ts_codes (max 50).
            patterns: Wildcard patterns (max 3), e.g. ``["3*.SZ"]``.
            fields: Optional Tushare field list.
            max_rows: Per-symbol cap (default 500). 0 = uncapped.

        Returns:
            Envelope dict with ``_meta`` and per-symbol ``data`` keys.
        """
        # --- input validation ---
        codes = codes or []
        patterns = patterns or []

        if len(codes) > _DEFAULT_MAX_CODES:
            return self._error(
                f"Too many codes ({len(codes)}). Maximum is {_DEFAULT_MAX_CODES}.",
                endpoint="rt_k",
                is_provisional=True,
            )
        if len(patterns) > _DEFAULT_MAX_PATTERNS:
            return self._error(
                f"Too many patterns ({len(patterns)}). Maximum is {_DEFAULT_MAX_PATTERNS}.",
                endpoint="rt_k",
                is_provisional=True,
            )

        for c in codes:
            if not _A_SHARE_CODE_RE.match(c):
                return self._error(
                    f"Invalid A-share code: {c!r}. Expected format e.g. '000001.SZ'.",
                    endpoint="rt_k",
                    is_provisional=True,
                )

        if not codes and not patterns:
            return self._error(
                "At least one code or pattern is required.",
                endpoint="rt_k",
                is_provisional=True,
            )

        # --- rate-limit ---
        _rate_limit("rt_k")

        # --- call provider ---
        result = self._rt.fetch_quotes(
            codes=codes if codes else None,
            patterns=patterns if patterns else None,
            fields=fields,
        )

        # --- row cap ---
        if max_rows > 0:
            result["data"] = {
                code: self._cap_dict_rows(rows, max_rows)
                for code, rows in result.get("data", {}).items()
            }

        # --- session metadata ---
        result["market_session"] = "active" if _is_cn_market_session() else "outside_hours"
        result["_meta"] = {
            "data_source": result.get("data_source", "tushare"),
            "endpoint": result.get("endpoint", "rt_k"),
            "retrieved_at": result.get("retrieved_at", datetime.now(timezone.utc).isoformat()),
            "is_provisional": result.get("is_provisional", True),
            "market_session": result["market_session"],
            "required_privilege": get_required_privilege("rt_k"),
        }

        return result

    # ------------------------------------------------------------------
    # get_auction_data
    # ------------------------------------------------------------------

    def get_auction_data(
        self,
        session: str,
        codes: list[str] | None = None,
        trade_date: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        max_rows: int = _DEFAULT_MAX_ROWS,
        fields: list[str] | None = None,
    ) -> dict[str, Any]:
        """Fetch A-share call-auction data.

        Args:
            session: ``"current"`` (stk_auction), ``"open"`` (stk_auction_o),
                     or ``"close"`` (stk_auction_c).
            codes: List of ts_codes (max 50).
            trade_date: Single date (YYYYMMDD).
            start_date: Range start (YYYYMMDD).
            end_date: Range end (YYYYMMDD).
            max_rows: Per-symbol cap (default 500). 0 = uncapped.
            fields: Optional Tushare field list to pass to the provider.

        Returns:
            Envelope dict with ``_meta`` and per-symbol ``data`` keys.
        """
        # --- session routing ---
        session_map = {
            "current": ("fetch_current_auction", "stk_auction", True),
            "open": ("fetch_opening_auction_history", "stk_auction_o", False),
            "close": ("fetch_closing_auction_history", "stk_auction_c", False),
        }
        if session not in session_map:
            return self._error(
                f"Invalid session: {session!r}. Must be 'current', 'open', or 'close'.",
                endpoint="auction",
                is_provisional=False,
            )

        method_name, endpoint, _is_provisional_default = session_map[session]

        # --- input validation ---
        codes = codes or []

        # Determine is_provisional: historical range queries are settled data.
        if session == "current" and (start_date or end_date):
            is_provisional = False
        elif session == "current" and trade_date and _is_past_trade_date(trade_date):
            is_provisional = False
        else:
            is_provisional = _is_provisional_default
        if len(codes) > _DEFAULT_MAX_CODES:
            return self._error(
                f"Too many codes ({len(codes)}). Maximum is {_DEFAULT_MAX_CODES}.",
                endpoint=endpoint,
                is_provisional=is_provisional,
            )
        for c in codes:
            if not _A_SHARE_CODE_RE.match(c):
                return self._error(
                    f"Invalid A-share code: {c!r}. Expected format e.g. '000001.SZ'.",
                    endpoint=endpoint,
                    is_provisional=is_provisional,
                )

        # --- rate-limit ---
        _rate_limit(endpoint)

        # --- call provider ---
        method = getattr(self._auction, method_name)
        kwargs: dict[str, Any] = {}
        if codes:
            kwargs["codes"] = codes
        if trade_date:
            kwargs["trade_date"] = trade_date
        if start_date:
            kwargs["start_date"] = start_date
        if end_date:
            kwargs["end_date"] = end_date
        if fields:
            kwargs["fields"] = fields

        result = method(**kwargs)

        # --- row cap ---
        if max_rows > 0:
            result["data"] = {
                code: self._cap_dict_rows(rows, max_rows)
                for code, rows in result.get("data", {}).items()
            }

        # --- metadata ---
        result["_meta"] = {
            "data_source": result.get("data_source", "tushare"),
            "endpoint": result.get("endpoint", endpoint),
            "retrieved_at": result.get("retrieved_at", datetime.now(timezone.utc).isoformat()),
            "is_provisional": result.get("is_provisional", is_provisional),
            "session": session,
            "required_privilege": get_required_privilege(endpoint),
        }

        return result

    # ------------------------------------------------------------------
    # get_realtime_minute_bars
    # ------------------------------------------------------------------

    def get_realtime_minute_bars(
        self,
        codes: list[str],
        frequency: str = "1MIN",
        max_rows: int = _DEFAULT_MAX_MINUTE_ROWS,
    ) -> dict[str, Any]:
        """Fetch A-share real-time minute K-line bars (``rt_min``).

        Input validation, rate-limit, and row-capping are applied before
        returning the provider envelope.

        Args:
            codes: List of ts_codes.
            frequency: K-line period — 1MIN/5MIN/15MIN/30MIN/60MIN.
            max_rows: Per-symbol cap (default 1000). 0 = uncapped.

        Returns:
            Envelope dict with ``_meta`` and per-symbol ``data`` keys.
        """
        # --- provider guard ---
        if self._minute is None:
            return self._error(
                "get_realtime_minute_bars requires minute_provider to be set. "
                "Pass minute_provider=TushareRealtimeMinuteProvider() when constructing TushareMarketDataService.",
                endpoint="rt_min",
                is_provisional=True,
            )

        # --- input validation ---
        if not codes:
            return self._error(
                "At least one code is required.",
                endpoint="rt_min",
                is_provisional=True,
            )

        for c in codes:
            if not _A_SHARE_CODE_RE.match(c):
                return self._error(
                    f"Invalid A-share code: {c!r}. Expected format e.g. '000001.SZ'.",
                    endpoint="rt_min",
                    is_provisional=True,
                )

        if frequency not in _VALID_MINUTE_FREQUENCIES:
            return self._error(
                f"Invalid frequency: {frequency!r}. Must be one of {sorted(_VALID_MINUTE_FREQUENCIES)}.",
                endpoint="rt_min",
                is_provisional=True,
            )

        if max_rows < 0 or max_rows > _DEFAULT_MAX_MINUTE_ROWS:
            return self._error(
                f"max_rows must be between 0 and {_DEFAULT_MAX_MINUTE_ROWS}, got {max_rows}.",
                endpoint="rt_min",
                is_provisional=True,
            )

        # --- rate-limit (independent bucket, not shared with rt_k) ---
        _rate_limit("rt_min")

        # --- call provider ---
        result = self._minute.fetch_bars(codes=codes, frequency=frequency)

        # --- row cap ---
        row_limit = max_rows if max_rows > 0 else _DEFAULT_MAX_MINUTE_ROWS
        result["data"] = {
            code: self._cap_dict_rows(rows, row_limit)
            for code, rows in result.get("data", {}).items()
        }

        # --- session metadata ---
        result["market_session"] = "active" if _is_cn_market_session() else "outside_hours"

        # --- possibly_truncated flag ---
        possibly_truncated = any(
            isinstance(rows, list) and len(rows) >= row_limit
            for rows in result.get("data", {}).values()
        )

        result["_meta"] = {
            "data_source": result.get("data_source", "tushare"),
            "endpoint": result.get("endpoint", "rt_min"),
            "frequency": frequency,
            "retrieved_at": result.get("retrieved_at", datetime.now(timezone.utc).isoformat()),
            "is_provisional": result.get("is_provisional", True),
            "market_session": result["market_session"],
            "timezone": "Asia/Shanghai",
            "row_limit": row_limit,
            "required_privilege": get_required_privilege("rt_min"),
        }
        if possibly_truncated:
            result["_meta"]["possibly_truncated"] = True

        return result

    # ------------------------------------------------------------------
    # get_security_master
    # ------------------------------------------------------------------

    def get_security_master(
        self,
        kind: str,
        market: str = "",
        list_status: str = "L",
        max_rows: int = _DEFAULT_MAX_ROWS,
    ) -> dict[str, Any]:
        """Fetch security master data (stock/fund/option lists).

        Args:
            kind: Security type — "stock", "fund", or "option".
            market: Market filter (SH/SZ/BJ for stock; E/O for fund; SSE/SZSE for option).
            list_status: Only for stock: L/D/P. Default "L".
            max_rows: Row cap (default 500). 0 = uncapped.

        Returns:
            Envelope dict with ``_meta`` and ``data``.
        """
        if self._refdata is None:
            return self._error(
                "get_security_master requires refdata_provider. "
                "Pass refdata_provider=TushareRefDataProvider() when constructing TushareMarketDataService.",
                endpoint="stock_basic",
                is_provisional=False,
            )

        kind = kind.strip().lower()
        _VALID_REFDATA_KINDS = frozenset({"stock", "fund", "option"})
        if kind not in _VALID_REFDATA_KINDS:
            return self._error(
                f"Invalid kind: {kind!r}. Must be one of {sorted(_VALID_REFDATA_KINDS)}.",
                endpoint="stock_basic",
                is_provisional=False,
            )

        endpoint_map = {
            "stock": ("stock_basic", "L"),
            "fund": ("fund_basic", ""),
            "option": ("opt_basic", ""),
        }
        endpoint, _ = endpoint_map[kind]

        # Rate-limit
        _rate_limit(endpoint)

        try:
            if kind == "stock":
                mkt = market if market else None
                result = self._refdata.fetch_stock_list(market=mkt, list_status=list_status)
            elif kind == "fund":
                mkt = market if market else None
                result = self._refdata.fetch_fund_list(market=mkt)
            else:  # option
                mkt = market if market else None
                result = self._refdata.fetch_option_list(exchange=mkt)
        except Exception as exc:
            logger.exception("get_security_master failed for kind=%s", kind)
            return self._error(str(exc), endpoint=endpoint, is_provisional=False)

        # Row cap
        data_rows = result.get("data", [])
        if max_rows > 0 and isinstance(data_rows, list) and len(data_rows) > max_rows:
            result["data"] = data_rows[:max_rows]

        result["_meta"] = {
            "data_source": result.get("data_source", "tushare"),
            "endpoint": result.get("endpoint", endpoint),
            "retrieved_at": result.get("retrieved_at", datetime.now(timezone.utc).isoformat()),
            "is_provisional": result.get("is_provisional", False),
            "required_privilege": get_required_privilege(endpoint),
        }

        return result

    # ------------------------------------------------------------------
    # get_featured_data
    # ------------------------------------------------------------------

    # Map of kind -> (provider method name, tushare endpoint)
    _FEATURED_KIND_MAP: dict[str, tuple[str, str]] = {
        "cyq_perf": ("fetch_cyq_perf", "cyq_perf"),
        "cyq_chips": ("fetch_cyq_chips", "cyq_chips"),
        "limit_list": ("fetch_limit_list", "limit_list_d"),
        "ths_hot": ("fetch_ths_hot", "ths_hot"),
        "stk_factor_pro": ("fetch_stk_factor_pro", "stk_factor_pro"),
        "hm_list": ("fetch_hm_list", "hm_list"),
        "hm_detail": ("fetch_hm_detail", "hm_detail"),
        "limit_list_ths": ("fetch_limit_list_ths", "limit_list_ths"),
        "limit_step": ("fetch_limit_step", "limit_step"),
        "moneyflow": ("fetch_moneyflow", "moneyflow"),
        "top_list": ("fetch_top_list", "top_list"),
        "margin_detail": ("fetch_margin_detail", "margin_detail"),
        "ths_index": ("fetch_ths_index", "ths_index"),
        "ths_member": ("fetch_ths_member", "ths_member"),
        "share_float": ("fetch_share_float", "share_float"),
        "dc_hot": ("fetch_dc_hot", "dc_hot"),
        "kpl_list": ("fetch_kpl_list", "kpl_list"),
        "kpl_concept_cons": ("fetch_kpl_concept_cons", "kpl_concept_cons"),
        "ths_daily": ("fetch_ths_daily", "ths_daily"),
        "dc_daily": ("fetch_dc_daily", "dc_daily"),
        "pledge_detail": ("fetch_pledge_detail", "pledge_detail"),
        "margin": ("fetch_margin", "margin"),
        "margin_secs": ("fetch_margin_secs", "margin_secs"),
        "dc_index": ("fetch_dc_index", "dc_index"),
        "dc_member": ("fetch_dc_member", "dc_member"),
        "tdx_index": ("fetch_tdx_index", "tdx_index"),
        "tdx_member": ("fetch_tdx_member", "tdx_member"),
        "tdx_daily": ("fetch_tdx_daily", "tdx_daily"),
        "limit_cpt_list": ("fetch_limit_cpt_list", "limit_cpt_list"),
        "pledge_stat": ("fetch_pledge_stat", "pledge_stat"),
        "repurchase": ("fetch_repurchase", "repurchase"),
        "holdertrade": ("fetch_holdertrade", "stk_holdertrade"),
        "stock_st": ("fetch_stock_st", "stock_st"),
        "hsgt_stocks": ("fetch_hsgt_stocks", "stock_hsgt"),
        "stk_surv": ("fetch_stk_surv", "stk_surv"),
        "broker_recommend": ("fetch_broker_recommend", "broker_recommend"),
        "cn_macro": ("fetch_cn_macro", "cn_macro"),
        "forecast": ("fetch_forecast", "forecast"),
        "report_rc": ("fetch_report_rc", "report_rc"),
        "forecast_only": ("fetch_forecast_only", "forecast"),
        "top_inst": ("fetch_top_inst", "top_inst"),
        "idx_factor_pro": ("fetch_idx_factor_pro", "idx_factor_pro"),
        "fund_factor_pro": ("fetch_fund_factor_pro", "fund_factor_pro"),
    }

    def get_featured_data(
        self,
        kind: str,
        max_rows: int = _DEFAULT_MAX_ROWS,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Fetch Tushare featured data.

        Args:
            kind: Data type (cyq_perf, limit_list, cn_macro, etc. — 41+ kinds).
            max_rows: Row cap (default 500). 0 = uncapped.
            **kwargs: Forwarded to the provider method (ts_code, trade_date, etc.).

        Returns:
            Envelope dict with ``_meta`` and ``data``.
        """
        if self._featured is None:
            return self._error(
                "get_featured_data requires featured_provider. "
                "Pass featured_provider=TushareFeaturedProvider() when constructing TushareMarketDataService.",
                endpoint="featured",
                is_provisional=False,
            )

        kind = kind.strip().lower()
        kind_info = self._FEATURED_KIND_MAP.get(kind)
        if kind_info is None:
            return self._error(
                f"Invalid kind: {kind!r}. Must be one of {sorted(self._FEATURED_KIND_MAP)}.",
                endpoint="featured",
                is_provisional=False,
            )

        method_name, endpoint = kind_info

        # Rate-limit
        _rate_limit(endpoint)

        try:
            method = getattr(self._featured, method_name)
            result = method(**kwargs)
        except Exception as exc:
            logger.exception("get_featured_data failed for kind=%s", kind)
            return self._error(str(exc), endpoint=endpoint, is_provisional=False)

        # Row cap
        data_rows = result.get("data", [])
        if max_rows > 0 and isinstance(data_rows, list) and len(data_rows) > max_rows:
            result["data"] = data_rows[:max_rows]

        result["_meta"] = {
            "data_source": result.get("data_source", "tushare"),
            "endpoint": result.get("endpoint", endpoint),
            "retrieved_at": result.get("retrieved_at", datetime.now(timezone.utc).isoformat()),
            "is_provisional": result.get("is_provisional", False),
            "kind": kind,
            "required_privilege": get_required_privilege(endpoint),
        }

        return result

    # ------------------------------------------------------------------
    # get_financial_statements
    # ------------------------------------------------------------------

    def get_financial_statements(
        self,
        statement_type: str,
        ts_code: str,
        max_rows: int = _DEFAULT_MAX_ROWS,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Fetch financial statements via Tushare fundamentals.

        Args:
            statement_type: One of "balance", "income", "cashflow", "indicators".
            ts_code: Tushare stock code (e.g. "000001.SZ").
            max_rows: Row cap (default 500). 0 = uncapped.
            **kwargs: Additional Tushare parameters (start_date, end_date, etc.).

        Returns:
            Envelope dict with ``_meta`` and ``data``.
        """
        if self._fundamentals is None:
            return self._error(
                "get_financial_statements requires fundamentals_provider. "
                "Pass fundamentals_provider=TushareFundamentalProvider() when constructing TushareMarketDataService.",
                endpoint="fina_indicator",
                is_provisional=False,
            )

        _TABLE_MAP = {
            "balance": "balancesheet",
            "income": "income",
            "cashflow": "cashflow",
            "indicators": "fina_indicator",
        }

        statement_type = statement_type.strip().lower()
        table = _TABLE_MAP.get(statement_type)
        if table is None:
            return self._error(
                f"Invalid statement_type: {statement_type!r}. Must be one of {sorted(_TABLE_MAP)}.",
                endpoint="fina_indicator",
                is_provisional=False,
            )

        if not ts_code or not ts_code.strip():
            return self._error(
                "ts_code is required for financial statements.",
                endpoint=table,
                is_provisional=False,
            )

        # Rate-limit
        _rate_limit(table)

        try:
            import pandas as pd

            df = self._fundamentals.query_fundamentals(
                table=table,
                codes=[ts_code.strip().upper()],
                as_of=kwargs.pop("as_of", pd.Timestamp.now()),
                periods=kwargs.pop("periods", None),
                fields=kwargs.pop("fields", None),
            )
            # Convert DataFrame to list of dicts
            if df is not None and not df.empty:
                data_rows = df.to_dict(orient="records")
            else:
                data_rows = []
        except Exception as exc:
            logger.exception("get_financial_statements failed for %s/%s", statement_type, ts_code)
            return self._error(str(exc), endpoint=table, is_provisional=False)

        # Row cap
        if max_rows > 0 and len(data_rows) > max_rows:
            data_rows = data_rows[:max_rows]

        result: dict[str, Any] = {
            "data_source": "tushare",
            "endpoint": table,
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "is_provisional": False,
            "data": data_rows,
            "_meta": {
                "data_source": "tushare",
                "endpoint": table,
                "retrieved_at": datetime.now(timezone.utc).isoformat(),
                "is_provisional": False,
                "statement_type": statement_type,
                "ts_code": ts_code.strip().upper(),
                "row_count": len(data_rows),
                "required_privilege": get_required_privilege(table),
            },
        }

        return result

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _cap_dict_rows(rows: list | dict, max_rows: int) -> list | dict:
        """Truncate row lists to *max_rows*."""
        if isinstance(rows, dict):
            return rows
        if isinstance(rows, list) and len(rows) > max_rows:
            return rows[:max_rows]
        return rows

    @staticmethod
    def _error(msg: str, *, endpoint: str, is_provisional: bool) -> dict[str, Any]:
        return {
            "data_source": "tushare",
            "endpoint": endpoint,
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "is_provisional": is_provisional,
            "error": msg,
            "data": {},
            "_meta": {
                "data_source": "tushare",
                "endpoint": endpoint,
                "retrieved_at": datetime.now(timezone.utc).isoformat(),
                "is_provisional": is_provisional,
                "error": msg,
                "required_privilege": get_required_privilege(endpoint),
            },
        }
