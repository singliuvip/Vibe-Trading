"""xtdata (miniQMT) A-share market data loader.

Uses xtquant.xtdata to download and read OHLCV data from the local miniQMT
process.  All xtdata calls are serialised through a module-level lock to
prevent BSON assertion crashes in the underlying C extension.
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
        """
        try:
            import xtquant.xtdata as xtdata
        except ImportError as exc:
            raise NoAvailableSourceError(
                "xtdata",
                "xtquant is not installed. Windows only: obtain xtquant from QMT.",
            ) from exc

        period = kwargs.get("period", "1d")
        xt_period = self.PERIOD_MAP.get(period, "1d")

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

        if data is None or symbol not in data or data[symbol] is None:
            raise NoAvailableSourceError("xtdata", f"no data for {symbol}")

        df: pd.DataFrame = data[symbol].copy()

        if df.empty:
            raise NoAvailableSourceError("xtdata", f"empty DataFrame for {symbol}")

        # Normalise column names
        df.columns = ["open", "high", "low", "close", "volume", "amount"]

        # Keep only standard columns
        keep = ["open", "high", "low", "close", "volume"]
        if "amount" in df.columns:
            keep.append("amount")
        df = df[[c for c in keep if c in df.columns]]

        return validate_ohlc(df)
