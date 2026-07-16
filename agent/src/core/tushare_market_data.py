"""Service layer for Tushare advanced privilege data (rt_k + auction).

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
# Validation constants
# ---------------------------------------------------------------------------

_A_SHARE_CODE_RE = re.compile(r"^\d{6}\.(SH|SZ|BJ)$", re.I)
_DEFAULT_MAX_CODES = 50
_DEFAULT_MAX_PATTERNS = 3
_DEFAULT_MAX_ROWS = 500

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
        realtime_provider: Any,
        auction_provider: Any,
    ) -> None:
        self._rt = realtime_provider
        self._auction = auction_provider

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
            },
        }
