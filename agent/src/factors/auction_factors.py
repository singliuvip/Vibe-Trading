"""Auction-based micro-structure factors.

All functions accept auction data in Tushare-normalized form and return
pandas objects with point-in-time safety annotations.

This module is **not** registered in the Alpha Zoo — auction data is
not a standard OHLCV panel and the zoo's pure-function contract does
not apply to pre-market / closing-auction snapshots.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def _safe_div(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    """Element-wise division: ``numerator / denominator``, div-by-zero → NaN.

    Also replaces ±inf with NaN so callers never receive unbounded values.
    """
    result = numerator / denominator.replace(0.0, np.nan)
    return result.replace([np.inf, -np.inf], np.nan)


def _zscore(series: pd.Series) -> pd.Series:
    """Cross-sectional z-score (sample std, ddof=1).

    A row whose std is zero or NaN returns NaN — no silent zero fill.
    """
    mean = series.mean(skipna=True)
    std = series.std(ddof=1, skipna=True)
    if std == 0.0 or pd.isna(std):
        return pd.Series(np.nan, index=series.index)
    return (series - mean) / std


# ------------------------------------------------------------------ factors


def auction_gap(prices: pd.Series, pre_close: pd.Series) -> pd.Series:
    """竞价涨幅: ``(auction_price - pre_close) / pre_close``.

    Args:
        prices: Auction reference price (``price`` column from Tushare
            ``stk_auction`` endpoint).
        pre_close: Previous trading day's close price (``pre_close`` column
            from the same endpoint), aligned by symbol.

    Returns:
        Series of fractional gaps (e.g. 0.02 = +2 %).  NaN where either input
        is missing or ``pre_close`` is zero.
    """
    return _safe_div(prices - pre_close, pre_close)


def auction_turnover_ratio(turnover_rate: pd.Series) -> pd.Series:
    """竞价换手率 (direct passthrough of the Tushare ``turnover_rate`` field).

    Args:
        turnover_rate: Raw turnover-rate series from the auction provider.

    Returns:
        A copy of *turnover_rate* with ±inf replaced by NaN.

    Note:
        Tushare's ``stk_auction`` endpoint already computes this field;
        this function merely sanitises it.
    """
    return turnover_rate.replace([np.inf, -np.inf], np.nan)


def auction_volume_ratio(volume_ratio: pd.Series) -> pd.Series:
    """竞价量比 (direct passthrough of the Tushare ``volume_ratio`` field).

    Args:
        volume_ratio: Raw volume-ratio series from the auction provider.

    Returns:
        A copy of *volume_ratio* with ±inf replaced by NaN.

    Note:
        Volume ratio = auction volume / average volume over a reference
        window (often 5 days).  The provider computes the window, so this
        function only sanitises the output.
    """
    return volume_ratio.replace([np.inf, -np.inf], np.nan)


def opening_gap(open_price: pd.Series, pre_close: pd.Series) -> pd.Series:
    """开盘缺口: ``(open - pre_close) / pre_close``.

    Args:
        open_price: First trade price of the continuous session.
        pre_close: Previous trading day's close price.

    Returns:
        Series of fractional gaps.  NaN where ``pre_close`` is zero or
        either input is missing.
    """
    return _safe_div(open_price - pre_close, pre_close)


def auction_vwap_deviation(price: pd.Series, vwap: pd.Series) -> pd.Series:
    """竞价 VWAP 偏离: ``(price - vwap) / vwap``.

    Args:
        price: Individual auction match price.
        vwap: Volume-weighted average price for the same auction session.

    Returns:
        Series of signed deviations.  NaN where *vwap* is zero or missing.
    """
    return _safe_div(price - vwap, vwap)


def auction_amount_concentration(amount: pd.Series, total_amount: pd.Series) -> pd.Series:
    """竞价成交额集中度: ``amount / total_amount``.

    Args:
        amount: Individual-stock auction turnover amount.
        total_amount: Sum of all stocks' auction turnover amount for the
            same session (scalar broadcast to the same index).

    Returns:
        Series ∈ [0, 1] (NaN-safe).  NaN where *total_amount* is zero or
        either input is missing.
    """
    return _safe_div(amount, total_amount)


def auction_liquidity_impact(vol: pd.Series, float_share: pd.Series) -> pd.Series:
    """竞价流动性冲击: ``vol / float_share``.

    Args:
        vol: Auction-match volume (shares).
        float_share: Free-float shares outstanding.

    Returns:
        Series of impact fractions.  NaN where *float_share* is zero or
        either input is missing.
    """
    return _safe_div(vol, float_share)


def composite_auction_sentiment(
    gap: pd.Series,
    turnover: pd.Series,
    vol_ratio: pd.Series,
) -> pd.Series:
    """综合竞价情绪得分: average of z-score-normalised gap, turnover, and volume-ratio.

    Args:
        gap: ``auction_gap`` output.
        turnover: ``auction_turnover_ratio`` output.
        vol_ratio: ``auction_volume_ratio`` output.

    Returns:
        Series of composite scores.  If any of the three components is
        NaN for a symbol, that symbol's composite score is also NaN
        (no silent imputation).

    Formula:
        ``sentiment = (gap_zscore + turnover_zscore + vol_ratio_zscore) / 3``
    """
    gap_z = _zscore(gap)
    turnover_z = _zscore(turnover)
    vol_z = _zscore(vol_ratio)
    return (gap_z + turnover_z + vol_z) / 3.0
