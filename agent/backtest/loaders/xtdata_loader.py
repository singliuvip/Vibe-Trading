"""xtdata (miniQMT) A-share market data loader.

Uses xtquant.xtdata to download and read OHLCV data from the local miniQMT
process.  All xtdata calls are serialised through a module-level lock to
prevent BSON assertion crashes in the underlying C extension.

When running inside a Docker container (Linux), xtquant is not available and
the loader falls back to the QMT Bridge HTTP API on the host machine.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Any

import pandas as pd

from backtest.loaders.base import NoAvailableSourceError, validate_ohlc
from backtest.loaders.registry import register

logger = logging.getLogger(__name__)

_xtdata_lock = threading.Lock()

# QMT Bridge HTTP fallback config
_BRIDGE_URL = os.getenv("XTQUANT_BRIDGE_URL", "http://host.docker.internal:8888")
_BRIDGE_TOKEN = os.getenv("XTQUANT_BEARER_TOKEN", "qmt-ql-8f3a2d1e9c")


def _http_load_bars(
    symbol: str,
    period: str,
    limit: int,
) -> pd.DataFrame:
    """Load OHLCV bars via QMT Bridge HTTP API (Docker/Linux fallback).

    Args:
        symbol: A-share ticker, e.g. ``600036.SH``.
        period: Bar period (``1d``, ``1h``, etc.).
        limit: Max number of bars to fetch.

    Returns:
        DataFrame with columns open/high/low/close/volume.

    Raises:
        NoAvailableSourceError: If the bridge is unreachable or returns an error.
    """
    import json
    import urllib.request

    url = f"{_BRIDGE_URL.rstrip('/')}/api/v1/market/bars"
    body = json.dumps({
        "symbol": symbol,
        "period": period,
        "limit": limit,
    }).encode("utf-8")

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {_BRIDGE_TOKEN}",
    }

    try:
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        raise NoAvailableSourceError(
            "xtdata",
            f"QMT Bridge HTTP request failed: {exc}",
        ) from exc

    if data.get("status") != "ok":
        raise NoAvailableSourceError(
            "xtdata",
            f"QMT Bridge returned error: {data.get('error', 'unknown')}",
        )

    bars = data.get("bars", [])
    if not bars:
        raise NoAvailableSourceError("xtdata", f"empty bars for {symbol}")

    df = pd.DataFrame(bars)
    if df.empty:
        raise NoAvailableSourceError("xtdata", f"empty DataFrame for {symbol}")

    # Rename columns to OHLCV standard
    col_map = {"t": "time", "o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"}
    df = df.rename(columns=col_map)

    # Set time as index if present
    if "time" in df.columns:
        df["time"] = pd.to_datetime(df["time"])
        df = df.set_index("time")

    keep = ["open", "high", "low", "close", "volume"]
    if "amount" in df.columns:
        keep.append("amount")
    df = df[[c for c in keep if c in df.columns]]

    return validate_ohlc(df)


@register
class XtDataLoader:
    name = "xtdata"

    PERIOD_MAP = {
        "1m": "1m",
        "5m": "5m",
        "15m": "15m",
        "30m": "30m",
        "1h": "60m",
        "1d": "1d",
        "1w": "1w",
        "1M": "1mon",
    }

    def load(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        **kwargs: Any,
    ) -> pd.DataFrame:
        """Load OHLCV data for *symbol* between *start_date* and *end_date*.

        Returns a DataFrame with columns open/high/low/close/volume.

        Tries native xtquant first (Windows), then falls back to QMT Bridge
        HTTP API (Docker/Linux).
        """
        period = kwargs.get("period", "1d")
        xt_period = self.PERIOD_MAP.get(period, "1d")

        # ── Attempt 1: native xtquant (Windows) ──
        try:
            import xtquant.xtdata as xtdata

            # Format dates as YYYYMMDD (xtdata format)
            start = str(start_date).replace("-", "")[:8]
            end = str(end_date).replace("-", "")[:8]

            with _xtdata_lock:
                xtdata.download_history_data(symbol, xt_period, start, end)
                data = xtdata.get_local_data(
                    fields=["open", "high", "low", "close", "volume", "amount"],
                    stock_codes=[symbol],
                    period=xt_period,
                    start_time=start,
                    end_time=end,
                    dividend_type="front",
                    fill_data=True,
                )

            if data is not None and symbol in data and data[symbol] is not None:
                df: pd.DataFrame = data[symbol].copy()
                if not df.empty:
                    df.columns = ["open", "high", "low", "close", "volume", "amount"]
                    keep = ["open", "high", "low", "close", "volume"]
                    if "amount" in df.columns:
                        keep.append("amount")
                    df = df[[c for c in keep if c in df.columns]]
                    return validate_ohlc(df)
        except ImportError:
            logger.info("xtquant not installed, trying HTTP bridge fallback for %s", symbol)
        except Exception as exc:
            logger.warning("Native xtquant load failed for %s: %s", symbol, exc)

        # ── Attempt 2: QMT Bridge HTTP (Docker/Linux) ──
        try:
            # Estimate limit from date range
            days = 90
            try:
                from datetime import datetime
                s = datetime.strptime(str(start_date)[:10], "%Y-%m-%d")
                e = datetime.strptime(str(end_date)[:10], "%Y-%m-%d")
                days = max(1, (e - s).days)
            except Exception:
                pass

            return _http_load_bars(symbol, period, limit=days)
        except NoAvailableSourceError:
            raise
        except Exception as exc:
            raise NoAvailableSourceError(
                "xtdata",
                f"Both native xtquant and HTTP bridge failed for {symbol}: {exc}",
            ) from exc
