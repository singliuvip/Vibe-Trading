"""Tushare reference/master data provider: stock_basic / fund_basic / opt_basic.

Wraps ``ts.pro_api()`` stock_basic, fund_basic, and opt_basic endpoints.
No disk cache is written — the data is always fetched live from Tushare.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

from backtest.loaders._tushare_constants import TUSHARE_TOKEN_PLACEHOLDERS


class TushareRefDataProvider:
    """Provider for Tushare reference/master data endpoints.

    Supports three endpoints:
    - ``stock_basic``: A-share stock list with area/industry metadata.
    - ``fund_basic``: ETF/fund list.
    - ``opt_basic``: Option contract list.
    """

    name = "tushare_refdata"

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
            endpoint: Tushare endpoint name (e.g. ``"stock_basic"``).
            data: List of row dicts, one per security.

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
        # frame may be empty; check via len or empty attribute
        try:
            if hasattr(frame, "empty") and frame.empty:
                return []
        except Exception:
            pass
        if not hasattr(frame, "to_dict"):
            return []
        import numpy as np

        # Replace NaN/NaT with None for clean JSON serialization.
        frame = frame.where(frame.notna(), None)
        rows: list[dict[str, Any]] = frame.to_dict(orient="records")
        # Ensure numpy types are converted to Python types.
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

    def fetch_stock_list(
        self,
        market: str | None = None,
        list_status: str = "L",
    ) -> dict[str, Any]:
        """Fetch A-share stock list via ``stock_basic``.

        Args:
            market: Exchange filter — ``None`` (all markets), ``"SH"``
                (Shanghai), ``"SZ"`` (Shenzhen), or ``"BJ"`` (Beijing).
            list_status: Listing status — ``"L"`` (listed, default),
                ``"D"`` (delisted), or ``"P"`` (suspended).

        Returns:
            Envelope with ``data`` as a list of row dicts, each containing
            ``ts_code``, ``symbol``, ``name``, ``area``, ``industry``,
            ``market``, and ``list_date``.
        """
        endpoint = "stock_basic"

        # --- parameter validation ---
        valid_markets = {"SH", "SZ", "BJ"}
        if market is not None and market not in valid_markets:
            return self._make_error_envelope(
                endpoint,
                f"Invalid market: {market!r}. Must be one of {sorted(valid_markets)} or None.",
            )
        valid_statuses = {"L", "D", "P"}
        if list_status not in valid_statuses:
            return self._make_error_envelope(
                endpoint,
                f"Invalid list_status: {list_status!r}. Must be one of {sorted(valid_statuses)}.",
            )

        fields = "ts_code,symbol,name,area,industry,market,list_date"

        try:
            kwargs: dict[str, Any] = {
                "list_status": list_status,
                "fields": fields,
            }
            if market:
                kwargs["exchange"] = market
            frame = self._pro.stock_basic(**kwargs)
        except Exception as exc:
            logger.warning("stock_basic API call failed: %s", exc)
            return self._make_error_envelope(endpoint, str(exc))

        rows = self._rows_from_frame(frame)
        return self._make_envelope(endpoint, rows)

    def fetch_fund_list(
        self,
        market: str | None = None,
    ) -> dict[str, Any]:
        """Fetch ETF/fund list via ``fund_basic``.

        Args:
            market: Market filter — ``None`` (all), ``"E"`` (ETF),
                ``"O"`` (open-end), etc. See Tushare docs for full list.

        Returns:
            Envelope with ``data`` as a list of row dicts.
        """
        endpoint = "fund_basic"

        try:
            kwargs: dict[str, Any] = {}
            if market:
                kwargs["market"] = market
            frame = self._pro.fund_basic(**kwargs)
        except Exception as exc:
            logger.warning("fund_basic API call failed: %s", exc)
            return self._make_error_envelope(endpoint, str(exc))

        rows = self._rows_from_frame(frame)
        return self._make_envelope(endpoint, rows)

    def fetch_option_list(
        self,
        exchange: str | None = None,
    ) -> dict[str, Any]:
        """Fetch option contract list via ``opt_basic``.

        Args:
            exchange: Exchange filter — ``None`` (all), ``"SSE"``
                (Shanghai Stock Exchange), or ``"SZSE"`` (Shenzhen).

        Returns:
            Envelope with ``data`` as a list of row dicts, each containing
            ``ts_code``, ``name``, ``exercise_type``, ``list_date``,
            and ``delist_date``.
        """
        endpoint = "opt_basic"

        # --- parameter validation ---
        valid_exchanges = {"SSE", "SZSE"}
        if exchange is not None and exchange not in valid_exchanges:
            return self._make_error_envelope(
                endpoint,
                f"Invalid exchange: {exchange!r}. Must be one of {sorted(valid_exchanges)} or None.",
            )

        fields = "ts_code,name,exercise_type,list_date,delist_date"

        try:
            kwargs: dict[str, Any] = {"fields": fields}
            if exchange:
                kwargs["exchange"] = exchange
            frame = self._pro.opt_basic(**kwargs)
        except Exception as exc:
            logger.warning("opt_basic API call failed: %s", exc)
            return self._make_error_envelope(endpoint, str(exc))

        rows = self._rows_from_frame(frame)
        return self._make_envelope(endpoint, rows)
