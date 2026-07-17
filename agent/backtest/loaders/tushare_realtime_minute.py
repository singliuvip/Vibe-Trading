"""Tushare real-time minute bars provider for the ``rt_min`` endpoint.

Requires the Tushare "A股分钟RT" formal permission (separate from rt_k).
Wraps ``ts.pro_api().rt_min()`` — no disk cache (provisional data).
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

from backtest.loaders._tushare_constants import TUSHARE_TOKEN_PLACEHOLDERS

# Valid A-share code format (Shanghai / Shenzhen / Beijing).
_TS_CODE_RE = re.compile(r"^\d{6}\.(SH|SZ|BJ)$", re.I)

# Allowed frequencies for rt_min.
_VALID_FREQUENCIES = frozenset({"1MIN", "5MIN", "15MIN", "30MIN", "60MIN"})

# Columns that should be treated as numeric for rt_min output.
_NUMERIC_COLS = {"open", "high", "low", "close", "volume", "amount"}


class TushareRealtimeMinuteProvider:
    """Provider for Tushare ``rt_min`` (real-time minute K-line).

    The ``rt_min`` endpoint returns intraday minute-level OHLCV bars
    for A-shares and ETFs.  Requires the separate "A股分钟RT" formal
    permission — not included in the basic rt_k privilege.

    Data is always provisional (intraday).  No disk cache is written.
    """

    name = "tushare_realtime_minute"
    endpoint = "rt_min"

    def __init__(self) -> None:
        """Resolve the Tushare token and instantiate the pro API client."""
        from src.config.accessor import get_env_config

        token = get_env_config().data.tushare_token.strip()
        if token in TUSHARE_TOKEN_PLACEHOLDERS:
            raise RuntimeError("Tushare token not configured — set TUSHARE_TOKEN in your environment")
        import tushare as ts

        self._pro = ts.pro_api(token)

    # ------------------------------------------------------------------
    # fetch_bars
    # ------------------------------------------------------------------

    def fetch_bars(
        self,
        codes: list[str],
        frequency: str,
    ) -> dict[str, Any]:
        """Fetch real-time minute bars via ``rt_min``.

        Args:
            codes: Explicit ts_code list, e.g. ``["600000.SH", "000001.SZ"]``.
            frequency: K-line frequency — one of 1MIN/5MIN/15MIN/30MIN/60MIN.

        Returns:
            Envelope with ``data_source``, ``endpoint``, ``retrieved_at``,
            ``is_provisional``, and ``data`` (dict of ts_code → list of bar
            dicts).  On error the envelope carries ``error`` and empty ``data``.
        """
        import math

        # --- input validation (defense in depth — Service layer does this too) ---
        if not codes:
            return self._error_envelope("At least one code is required.")

        for c in codes:
            if not _TS_CODE_RE.match(c):
                return self._error_envelope(
                    f"Invalid code format: {c!r}. Expected e.g. '600000.SH' or '000001.SZ'."
                )

        if frequency not in _VALID_FREQUENCIES:
            return self._error_envelope(
                f"Invalid frequency: {frequency!r}. Must be one of {sorted(_VALID_FREQUENCIES)}."
            )

        ts_code_arg = ",".join(codes)

        # --- call Tushare ---
        try:
            df = self._pro.rt_min(ts_code=ts_code_arg, freq=frequency)
        except Exception as exc:
            logger.exception("rt_min call failed for codes=%s freq=%s", codes, frequency)
            return self._error_envelope(str(exc))

        if df is None or (hasattr(df, "empty") and df.empty):
            return {
                "data_source": "tushare",
                "endpoint": self.endpoint,
                "retrieved_at": datetime.now(timezone.utc).isoformat(),
                "is_provisional": True,
                "data": {},
            }

        # --- normalize ---
        import pandas as pd

        records = df.to_dict(orient="records")

        # Normalize each row:
        #   - time → Asia/Shanghai-aware timestamp ISO string
        #   - vol → volume (rename)
        #   - numeric cols → float (NaN → None for JSON allow_nan=False)
        #   - drop rows where ts_code is missing
        import zoneinfo

        try:
            cn_tz = zoneinfo.ZoneInfo("Asia/Shanghai")
        except Exception:
            cn_tz = None

        normalized: list[dict[str, Any]] = []
        for row in records:
            ts_code = row.get("ts_code", "")
            if not ts_code:
                continue

            # Normalize time column to ISO 8601 with Asia/Shanghai offset
            time_val = row.get("time", "")
            if time_val and cn_tz:
                try:
                    time_str = str(time_val)
                    # Detect if time_val already contains a date (YYYY-MM-DD or YYYYMMDD)
                    has_date = bool(re.match(r"^\d{4}[-/]\d{2}[-/]\d{2}", time_str)) or bool(
                        re.match(r"^\d{8}", time_str)
                    )
                    if not has_date:
                        # Bare HH:MM or HH:MM:SS — prepend today's date in CN timezone
                        today_cn = datetime.now(cn_tz).strftime("%Y-%m-%d")
                        time_str = f"{today_cn}T{time_str}"
                        if ":" not in time_str.split("T", 1)[-1]:
                            time_str += ":00"
                    ts_dt = pd.Timestamp(time_str)
                    if ts_dt.tz is None:
                        ts_dt = ts_dt.tz_localize(cn_tz)
                    else:
                        ts_dt = ts_dt.tz_convert(cn_tz)
                    timestamp = ts_dt.isoformat()
                except Exception:
                    timestamp = str(time_val)
            else:
                timestamp = str(time_val) if time_val else ""

            bar: dict[str, Any] = {"ts_code": ts_code, "timestamp": timestamp}

            # Map columns: ts_code and time already handled; vol → volume
            for col in ("open", "high", "low", "close", "volume", "amount", "vol"):
                val = row.get(col)
                if col == "vol":
                    col = "volume"
                if col in _NUMERIC_COLS:
                    if val is None or (isinstance(val, float) and math.isnan(val)):
                        bar[col] = None
                    else:
                        try:
                            bar[col] = float(val)
                        except (ValueError, TypeError):
                            bar[col] = None
                elif col not in ("ts_code",):
                    bar[col] = val

            normalized.append(bar)

        # Group by ts_code, sort by timestamp ascending within each group
        data: dict[str, list[dict[str, Any]]] = {}
        for bar in normalized:
            ts_code = bar.pop("ts_code", "")
            if ts_code not in data:
                data[ts_code] = []
            data[ts_code].append(bar)

        for code_bars in data.values():
            code_bars.sort(key=lambda b: b.get("timestamp", ""))

        # Deduplicate by (ts_code, timestamp) — keep last
        for code, code_bars in data.items():
            seen: set[str] = set()
            deduped: list[dict[str, Any]] = []
            for bar in reversed(code_bars):
                key = f"{code}|{bar.get('timestamp', '')}"
                if key not in seen:
                    seen.add(key)
                    deduped.append(bar)
            deduped.reverse()
            data[code] = deduped

        return {
            "data_source": "tushare",
            "endpoint": self.endpoint,
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "is_provisional": True,
            "data": data,
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _error_envelope(msg: str) -> dict[str, Any]:
        return {
            "data_source": "tushare",
            "endpoint": "rt_min",
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "is_provisional": True,
            "error": msg,
            "data": {},
        }


# Self-register in the realtime provider registry (not the historical LOADER_REGISTRY).
try:
    from backtest.loaders.registry import register_realtime_provider

    register_realtime_provider("tushare", "rt_min", TushareRealtimeMinuteProvider)
except Exception:
    pass
