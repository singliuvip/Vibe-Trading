"""Tushare real-time quotes provider for the ``rt_k`` endpoint.

Requires the Tushare "A股日线RT" privilege (membership tier with 15000+ points).
Wraps ``ts.pro_api().rt_k()`` — no caching (Service layer handles that).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

from backtest.loaders._tushare_constants import TUSHARE_TOKEN_PLACEHOLDERS

# Valid suffix for A-share codes (Shanghai / Shenzhen / Beijing).
_TS_CODE_RE = r"^\d{6}\.(SH|SZ|BJ)$"

# A pattern is a wildcard prefix ending with "*" and a valid suffix.
_PATTERN_RE = r"^\d+\*\.(SH|SZ|BJ)$"


class TushareRealtimeProvider:
    """Provider for Tushare ``rt_k`` (real-time daily K-line snapshot).

    The ``rt_k`` endpoint returns intraday-updated OHLCV for A-shares.
    It supports both explicit codes and wildcard prefix patterns (batch).

    No disk cache is written — ``rt_k`` data is always provisional.
    """

    name = "tushare_realtime"
    endpoint = "rt_k"

    def __init__(self) -> None:
        """Resolve the Tushare token and instantiate the pro API client."""
        from src.config.accessor import get_env_config

        token = get_env_config().data.tushare_token.strip()
        if token in TUSHARE_TOKEN_PLACEHOLDERS:
            raise RuntimeError("Tushare token not configured — set TUSHARE_TOKEN in your environment")
        import tushare as ts

        self._pro = ts.pro_api(token)

    def fetch_quotes(
        self,
        codes: list[str] | None = None,
        patterns: list[str] | None = None,
        fields: list[str] | None = None,
    ) -> dict[str, Any]:
        """Fetch real-time quotes via ``rt_k``.

        Args:
            codes: Explicit ts_code list, e.g. ``["000001.SZ", "600519.SH"]``.
            patterns: Wildcard prefix patterns, e.g. ``["3*.SZ", "6*.SH"]``.
            fields: Optional Tushare field list to select specific columns.

        Returns:
            Envelope with ``data_source``, ``endpoint``, ``retrieved_at``,
            ``is_provisional``, and ``data`` (dict of ts_code → row dict).
            On error the envelope carries ``error`` and empty ``data``.
        """
        import re

        resolved_codes: list[str] = []

        if codes:
            for c in codes:
                if not re.match(_TS_CODE_RE, c, re.I):
                    return {
                        "data_source": "tushare",
                        "endpoint": self.endpoint,
                        "retrieved_at": datetime.now(timezone.utc).isoformat(),
                        "is_provisional": True,
                        "error": f"Invalid code format: {c!r}. Expected e.g. '000001.SZ', '600519.SH', or '430139.BJ'.",
                        "data": {},
                    }
            resolved_codes.extend(codes)

        if patterns:
            for p in patterns:
                if not re.match(_PATTERN_RE, p, re.I):
                    return {
                        "data_source": "tushare",
                        "endpoint": self.endpoint,
                        "retrieved_at": datetime.now(timezone.utc).isoformat(),
                        "is_provisional": True,
                        "error": f"Invalid pattern format: {p!r}. Pattern must end with '*' plus a valid suffix, e.g. '3*.SZ'.",
                        "data": {},
                    }
            resolved_codes.extend(patterns)

        ts_code_arg = ",".join(resolved_codes) if resolved_codes else ""

        try:
            kwargs: dict[str, Any] = {"ts_code": ts_code_arg}
            if fields:
                kwargs["fields"] = ",".join(fields)
            df = self._pro.rt_k(**kwargs)
        except Exception as exc:
            logger.exception("rt_k call failed for codes=%s patterns=%s", codes, patterns)
            return {
                "data_source": "tushare",
                "endpoint": self.endpoint,
                "retrieved_at": datetime.now(timezone.utc).isoformat(),
                "is_provisional": True,
                "error": str(exc),
                "data": {},
            }

        if df is None or (hasattr(df, "empty") and df.empty):
            return {
                "data_source": "tushare",
                "endpoint": self.endpoint,
                "retrieved_at": datetime.now(timezone.utc).isoformat(),
                "is_provisional": True,
                "data": {},
            }

        import pandas as pd

        records = df.to_dict(orient="records")
        data: dict[str, Any] = {}
        for row in records:
            ts_code = row.get("ts_code", "")
            if ts_code:
                data[ts_code] = row

        return {
            "data_source": "tushare",
            "endpoint": self.endpoint,
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "is_provisional": True,
            "data": data,
        }


# Self-register in the realtime provider registry (not the historical LOADER_REGISTRY).
try:
    from backtest.loaders.registry import register_realtime_provider

    register_realtime_provider("tushare", "rt_k", TushareRealtimeProvider)
except Exception:
    pass
