"""xtdata (miniQMT) A-share market data loader.

Uses xtquant.xtdata to download and read OHLCV data from the local miniQMT
process on Windows.  All xtdata calls are serialised through a module-level
lock to prevent BSON assertion crashes in the underlying C extension.

This loader requires a native Windows environment with miniQMT installed;
xtquant is not available on Linux/macOS.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

import pandas as pd

from backtest.loaders.base import NoAvailableSourceError, validate_ohlc
from backtest.loaders.registry import register

logger = logging.getLogger(__name__)

_xtdata_lock = threading.Lock()


@register
class XtDataLoader:
    name = "xtdata"

    #: Default price adjustment method.  ``"front"`` (前复权) is the xtdata
    #: default and matches the behaviour of other Alpha Zoo loaders (tushare /
    #: akshare) which also default to front-adjusted prices.
    DEFAULT_ADJUST = "front"

    #: Supported adjustment modes mapped to xtdata ``dividend_type`` values.
    ADJUST_MAP: dict[str | None, str] = {
        "front": "front",
        "back":  "back",
        None:    "none",
    }

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

        Uses native ``xtquant.xtdata`` directly (Windows only).

        Keyword Args:
            period: Bar period (``1d``, ``1h``, ``1m``, etc.).  Default ``1d``.
            adjust: Price adjustment mode — ``\"front\"`` (前复权, default),
                ``\"back\"`` (后复权), or ``None`` (不复权).
        """
        period = kwargs.get("period", "1d")
        xt_period = self.PERIOD_MAP.get(period, "1d")

        # Resolve adjustment / dividend_type (§9.5.1)
        adjust = kwargs.get("adjust", self.DEFAULT_ADJUST)
        if adjust not in self.ADJUST_MAP:
            logger.warning(
                "Unknown adjust=%r for xtdata loader, falling back to %r",
                adjust, self.DEFAULT_ADJUST,
            )
            adjust = self.DEFAULT_ADJUST
        dividend_type = self.ADJUST_MAP[adjust]

        # ── Native xtquant (Windows) ──
        try:
            import xtquant.xtdata as xtdata
        except ImportError as exc:
            raise NoAvailableSourceError(
                "xtdata",
                "xtquant is not installed. This loader requires a Windows host with miniQMT.",
            ) from exc

        try:
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
                    dividend_type=dividend_type,
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

            raise NoAvailableSourceError(
                "xtdata",
                f"No data returned for {symbol} ({start_date} → {end_date})",
            )
        except NoAvailableSourceError:
            raise
        except Exception as exc:
            raise NoAvailableSourceError(
                "xtdata",
                f"Native xtquant load failed for {symbol}: {exc}",
            ) from exc
