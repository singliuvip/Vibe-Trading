"""Tushare auction data provider for call-auction endpoints.

Requires the Tushare "集合竞价成交" privilege (membership tier with 15000+ points).

Endpoints:
- ``stk_auction``: current-day call auction (intraday, provisional).
- ``stk_auction_o``: historical opening call auction.
- ``stk_auction_c``: historical closing call auction.

No caching — the Service layer decides what to cache.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

from backtest.loaders._tushare_constants import TUSHARE_TOKEN_PLACEHOLDERS


class TushareAuctionProvider:
    """Provider for Tushare call-auction endpoints."""

    name = "tushare_auction"

    def __init__(self) -> None:
        """Resolve the Tushare token and instantiate the pro API client."""
        from src.config.accessor import get_env_config

        token = get_env_config().data.tushare_token.strip()
        if token in TUSHARE_TOKEN_PLACEHOLDERS:
            raise RuntimeError("Tushare token not configured — set TUSHARE_TOKEN in your environment")
        import tushare as ts

        self._pro = ts.pro_api(token)

    # ------------------------------------------------------------------
    # fetch_current_auction  (stk_auction — intraday, provisional)
    # ------------------------------------------------------------------

    def fetch_current_auction(
        self,
        trade_date: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        codes: list[str] | None = None,
        fields: list[str] | None = None,
    ) -> dict[str, Any]:
        """Fetch call auction data (``stk_auction``).

        Supports both current-day (intraday, 9:26–9:29) and historical
        queries via ``start_date``/``end_date`` (data available from 2025-01).

        Args:
            trade_date: Single trade date in YYYYMMDD format.
            start_date: Start date for historical range query (YYYYMMDD).
            end_date: End date for historical range query (YYYYMMDD).
            codes: Optional ts_code list, e.g. ``["000001.SZ"]``.
            fields: Optional Tushare field list.

        Returns:
            Envelope dict — ``is_provisional`` is set by the Service layer.
        """
        return self._call_auction_endpoint(
            endpoint="stk_auction",
            is_provisional=True,  # Service layer overrides based on query type
            trade_date=trade_date,
            start_date=start_date,
            end_date=end_date,
            codes=codes,
            fields=fields,
        )

    # ------------------------------------------------------------------
    # fetch_opening_auction_history  (stk_auction_o — historical)
    # ------------------------------------------------------------------

    def fetch_opening_auction_history(
        self,
        codes: list[str] | None = None,
        trade_date: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        fields: list[str] | None = None,
    ) -> dict[str, Any]:
        """Fetch historical opening call auction data (``stk_auction_o``).

        Args:
            codes: ts_code list, e.g. ``["000001.SZ"]``.
            trade_date: Single trade date (YYYYMMDD).
            start_date: Start date for range query (YYYYMMDD).
            end_date: End date for range query (YYYYMMDD).
            fields: Optional Tushare field list.

        Returns:
            Envelope with ``is_provisional=False``.
        """
        return self._call_auction_endpoint(
            endpoint="stk_auction_o",
            is_provisional=False,
            trade_date=trade_date,
            start_date=start_date,
            end_date=end_date,
            codes=codes,
            fields=fields,
        )

    # ------------------------------------------------------------------
    # fetch_closing_auction_history  (stk_auction_c — historical)
    # ------------------------------------------------------------------

    def fetch_closing_auction_history(
        self,
        codes: list[str] | None = None,
        trade_date: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        fields: list[str] | None = None,
    ) -> dict[str, Any]:
        """Fetch historical closing call auction data (``stk_auction_c``).

        Args:
            codes: ts_code list, e.g. ``["000001.SZ"]``.
            trade_date: Single trade date (YYYYMMDD).
            start_date: Start date for range query (YYYYMMDD).
            end_date: End date for range query (YYYYMMDD).
            fields: Optional Tushare field list.

        Returns:
            Envelope with ``is_provisional=False``.
        """
        return self._call_auction_endpoint(
            endpoint="stk_auction_c",
            is_provisional=False,
            trade_date=trade_date,
            start_date=start_date,
            end_date=end_date,
            codes=codes,
            fields=fields,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _call_auction_endpoint(
        self,
        *,
        endpoint: str,
        is_provisional: bool,
        trade_date: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        codes: list[str] | None = None,
        fields: list[str] | None = None,
    ) -> dict[str, Any]:
        """Generic call-auction dispatcher shared by all three endpoints.

        Args:
            endpoint: One of ``stk_auction``, ``stk_auction_o``, ``stk_auction_c``.
            is_provisional: Whether the data is intraday / subject to change.
            trade_date: Single trade date (YYYYMMDD).
            start_date: Start date (YYYYMMDD).
            end_date: End date (YYYYMMDD).
            codes: ts_code list or None.
            fields: Optional Tushare field list.

        Returns:
            Normalized envelope: ``{data_source, endpoint, retrieved_at,
            is_provisional, data}``.  On error carries ``error`` + empty ``data``.
        """
        kwargs: dict[str, Any] = {}

        if trade_date:
            kwargs["trade_date"] = trade_date
        if start_date:
            kwargs["start_date"] = start_date
        if end_date:
            kwargs["end_date"] = end_date
        if codes:
            kwargs["ts_code"] = ",".join(codes)
        if fields:
            kwargs["fields"] = ",".join(fields)

        # Default trade_date to today when no date range is given.
        if not trade_date and not start_date and not end_date:
            kwargs["trade_date"] = datetime.now(timezone.utc).strftime("%Y%m%d")

        try:
            api_method = getattr(self._pro, endpoint)
            df = api_method(**kwargs)
        except Exception as exc:
            logger.exception("Tushare %s call failed", endpoint)
            return {
                "data_source": "tushare",
                "endpoint": endpoint,
                "retrieved_at": datetime.now(timezone.utc).isoformat(),
                "is_provisional": is_provisional,
                "error": str(exc),
                "data": {},
            }

        if df is None or (hasattr(df, "empty") and df.empty):
            return {
                "data_source": "tushare",
                "endpoint": endpoint,
                "retrieved_at": datetime.now(timezone.utc).isoformat(),
                "is_provisional": is_provisional,
                "data": {},
            }

        records = df.to_dict(orient="records")
        data: dict[str, Any] = {}
        for row in records:
            ts_code = row.get("ts_code", "")
            if ts_code:
                data[ts_code] = row

        return {
            "data_source": "tushare",
            "endpoint": endpoint,
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "is_provisional": is_provisional,
            "data": data,
        }
