"""Simulated price source for the virtual broker connector.

Provides bid/ask quotes and historical OHLCV bars through a three-tier data
strategy:

1. **yfinance** (optional) — best-effort live price fetch. Silently degrades
   when the package is not installed or the network is unreachable.
2. **Cache** — previously-fetched prices persisted in the state directory.
3. **Fallback** — deterministic drift applied to a built-in base price table.
   This guarantees the connector works with zero network access.

Every returned payload carries a ``source`` field (``"yfinance"`` / ``"cache"`` /
``"fallback"``) so callers can distinguish simulated from fetched prices.
"""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_SYMBOLS: tuple[str, ...] = (
    "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "TSLA", "SPY", "QQQ",
)

# Base prices used when no external source is available.  These are roughly
# mid-2026 levels for the configured universe.
FALLBACK_PRICES: dict[str, float] = {
    "AAPL": 210.0,
    "MSFT": 500.0,
    "GOOGL": 180.0,
    "AMZN": 220.0,
    "NVDA": 160.0,
    "TSLA": 300.0,
    "SPY": 620.0,
    "QQQ": 550.0,
}

#: Default bid-ask spread as fraction of mid price.
_DEFAULT_SPREAD_FRAC = 0.0002  # 2 bps minimum

#: How often (seconds) a cached quote is considered fresh.
_CACHE_TTL_S = 60.0

#: Deterministic drift magnitude (fraction).  The drift per symbol is
#: ``(-DRIFT_RANGE, +DRIFT_RANGE)`` and varies with the 5-minute time bucket.
_DRIFT_RANGE = 0.015  # ±1.5%


# ---------------------------------------------------------------------------
# Quote model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Quote:
    """A simulated bid/ask/last quote for one symbol.

    Attributes:
        symbol: Normalised symbol code.
        bid: Best bid price.
        ask: Best ask price.
        last: Most recent trade price (mid).
        time: ISO-8601 UTC timestamp of the quote.
        source: One of ``"yfinance"``, ``"cache"``, ``"fallback"``.
    """

    symbol: str
    bid: float
    ask: float
    last: float
    time: str
    source: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def normalize_symbol(symbol: str) -> str:
    """Normalise a symbol to upper-case stripped form.

    Also converts prefix-style exchange codes to suffix-style:
    ``SZ.000001`` → ``000001.SZ``, ``SH.600000`` → ``600000.SH``,
    ``BJ.830000`` → ``830000.BJ``, ``HK.00001`` → ``00001.HK``,
    ``CN.000001.SZ`` → ``000001.SZ.CN``.
    """
    sym = symbol.strip().upper()
    # Convert prefix-style A-share / HK codes to suffix-style.
    for prefix in ("SZ.", "SH.", "BJ.", "HK.", "CN."):
        if sym.startswith(prefix) and len(sym) > len(prefix):
            code = sym[len(prefix):]
            if code.isdigit():
                return f"{code}.{prefix[:-1]}"
    return sym


def infer_market(symbol: str) -> str:
    """Infer the market from a symbol code.

    Returns:
        ``"a_share"`` for A-shares, ``"us_equity"`` for US equities,
        ``"hk_equity"`` for Hong Kong equities.
    """
    sym = normalize_symbol(symbol)
    # A-share patterns
    if sym.endswith(".SZ") or sym.endswith(".SH") or sym.endswith(".SS") or sym.endswith(".BJ") or sym.endswith(".CN"):
        return "a_share"
    if sym.startswith("CN."):
        return "a_share"
    # HK patterns
    if sym.endswith(".HK") or sym.startswith("HK."):
        return "hk_equity"
    # Everything else → US
    return "us_equity"


def infer_currency(market: str) -> str:
    """Return the base currency for a market.

    Returns:
        ``"CNY"`` for a_share, ``"USD"`` for us_equity, ``"HKD"`` for hk_equity.
    """
    return {"a_share": "CNY", "us_equity": "USD", "hk_equity": "HKD"}.get(market, "USD")


def _deterministic_noise(symbol: str, bucket: int) -> float:
    """Return a deterministic noise value in ``(-DRIFT_RANGE, +DRIFT_RANGE)``.

    Args:
        symbol: The symbol code.
        bucket: An integer time bucket (e.g. Unix epoch seconds // 300 for 5-min
            buckets, or an index into a bar series).

    Returns:
        A float in ``(-DRIFT_RANGE, +DRIFT_RANGE)``.
    """
    seed = f"{symbol}:{bucket}"
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    # Use first 8 hex digits as a signed 32-bit int in [-1, 1).
    raw = int(digest[:8], 16)  # 0..0xFFFFFFFF
    normalised = (raw / 0x80000000) - 1.0  # [-1.0, 1.0)
    return normalised * _DRIFT_RANGE


def _current_time_bucket() -> int:
    """Return a 5-minute time bucket index (deterministic drift anchor)."""
    return int(time.time()) // 300


def _now_iso() -> str:
    """Return the current UTC time as ISO-8601."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _make_quote(symbol: str, last: float, source: str) -> Quote:
    """Build a Quote with a small bid-ask spread around *last*."""
    spread = max(last * _DEFAULT_SPREAD_FRAC, 0.01)
    return Quote(
        symbol=symbol,
        bid=round(last - spread / 2, 4),
        ask=round(last + spread / 2, 4),
        last=round(last, 4),
        time=_now_iso(),
        source=source,
    )


# ---------------------------------------------------------------------------
# Loader Registry bridge (optional, for A-share etc.)
# ---------------------------------------------------------------------------


def _resolve_loader(market: str):
    """Best-effort resolve a loader for *market* from the backtest loader registry.

    Returns:
        A loader instance, or ``None`` if the registry is unavailable or no
        loader could be constructed for *market*.
    """
    try:
        from backtest.loaders.registry import resolve_loader
        return resolve_loader(market)
    except Exception:
        logger.debug("resolve_loader(%r) failed", market, exc_info=True)
        return None


def _try_loader_quote(symbol: str, market: str) -> Quote | None:
    """Try to get a quote via the backtest loader registry.

    Only attempts when *market* is ``"a_share"``. Calls the first available
    loader's ``fetch()`` method for a recent 5-day window and extracts the
    close price from the last bar.

    Args:
        symbol: The normalized symbol (e.g. ``"000001.SZ"``).
        market: The target market.

    Returns:
        A :class:`Quote` with ``source="loader:<name>"``, or ``None``.
    """
    if market != "a_share":
        return None
    loader = _resolve_loader(market)
    if loader is None:
        return None
    try:
        from datetime import date, timedelta
        end_date = date.today().isoformat()
        start_date = (date.today() - timedelta(days=5)).isoformat()
        result = loader.fetch(
            [symbol],
            start_date=start_date,
            end_date=end_date,
            interval="1D",
        )
        if result and symbol in result:
            df = result[symbol]
            if not df.empty:
                close = float(df["close"].iloc[-1])
                if close > 0:
                    src_name = getattr(loader, "name", "unknown")
                    return _make_quote(symbol, close, f"loader:{src_name}")
    except Exception:
        logger.debug("loader quote failed for %s", symbol, exc_info=True)
    return None


def _try_loader_bars(
    symbol: str,
    market: str,
    period: str = "1d",
    limit: int = 90,
) -> list[dict[str, Any]] | None:
    """Try to get historical bars via the backtest loader registry.

    Only attempts when *market* is ``"a_share"``. Calls the first available
    loader's ``fetch()`` method with a window sized to cover *limit* bars
    and returns OHLCV dicts.

    Args:
        symbol: The normalized symbol.
        market: The target market.
        period: Bar interval (``"1d"``, ``"1h"``, etc.).
        limit: Number of bars desired.

    Returns:
        A list of OHLCV dicts, or ``None``.
    """
    if market != "a_share":
        return None
    loader = _resolve_loader(market)
    if loader is None:
        return None
    try:
        from datetime import date, timedelta
        # Estimate how many calendar days we need for *limit* trading bars.
        # A generous multiplier handles weekends / holidays.
        interval_norm = str(period).strip().lower()
        if interval_norm in ("1m", "5m", "15m", "30m", "1h"):
            # Intraday: fetch last 5 trading days.
            day_window = 7
        else:
            day_window = max(limit * 2, 90)
        end_date = date.today().isoformat()
        start_date = (date.today() - timedelta(days=day_window)).isoformat()
        # Map our period to the loader's interval convention.
        # Warn when the requested interval does not exactly match a loader
        # interval. E.g. "4h" is mapped to "60m" (1-hour) bars.
        if interval_norm == "4h":
            logger.info(
                "A-share loader does not support 4h interval; falling back to 1h bars for %s",
                symbol,
            )
        _period_map = {
            "1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m",
            "1h": "60m", "4h": "60m", "1d": "1D", "1w": "1W", "1M": "1M",
        }
        loader_interval = _period_map.get(interval_norm, "1D")
        result = loader.fetch(
            [symbol],
            start_date=start_date,
            end_date=end_date,
            interval=loader_interval,
        )
        if result and symbol in result:
            df = result[symbol]
            if not df.empty:
                src_name = getattr(loader, "name", "unknown")
                bars: list[dict[str, Any]] = []
                for idx, row in df.tail(limit).iterrows():
                    bars.append({
                        "timestamp": str(idx),
                        "open": round(float(row.get("open", 0)), 4),
                        "high": round(float(row.get("high", 0)), 4),
                        "low": round(float(row.get("low", 0)), 4),
                        "close": round(float(row.get("close", 0)), 4),
                        "volume": int(float(row.get("volume", 0))),
                        "source": f"loader:{src_name}",
                    })
                return bars
    except Exception:
        logger.debug("loader bars failed for %s", symbol, exc_info=True)
    return None


# ---------------------------------------------------------------------------
# External price source (optional)
# ---------------------------------------------------------------------------


def _try_yfinance_quote(symbol: str) -> Quote | None:
    """Best-effort fetch a live quote via yfinance (optional)."""
    try:
        import yfinance as yf  # type: ignore[import-untyped]

        ticker = yf.Ticker(symbol)
        info = ticker.info or {}
        # Prefer ``currentPrice`` or ``regularMarketPrice`` from info.
        price: float | None = info.get("currentPrice") or info.get("regularMarketPrice")
        if price is not None and price > 0:
            return _make_quote(symbol, float(price), "yfinance")
    except Exception:
        logger.debug("yfinance quote failed for %s", symbol, exc_info=True)
    return None


def _try_yfinance_bars(
    symbol: str,
    period: str = "1d",
    limit: int = 90,
) -> list[dict[str, Any]] | None:
    """Best-effort fetch historical bars via yfinance (optional).

    Returns:
        A list of OHLCV dicts, or ``None`` on failure.
    """
    try:
        import yfinance as yf  # type: ignore[import-untyped]

        # Map period to yfinance interval.
        interval_map = {
            "1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m",
            "1h": "60m", "4h": "60m", "1d": "1d", "1w": "1wk", "1M": "1mo",
        }
        yf_interval = interval_map.get(period, "1d")
        ticker = yf.Ticker(symbol)
        df = ticker.history(period=f"{limit}{yf_interval}", interval=yf_interval)
        if df is not None and not df.empty:
            bars: list[dict[str, Any]] = []
            for idx, row in df.iterrows():
                bars.append({
                    "timestamp": str(idx),
                    "open": round(float(row.get("Open", 0)), 4),
                    "high": round(float(row.get("High", 0)), 4),
                    "low": round(float(row.get("Low", 0)), 4),
                    "close": round(float(row.get("Close", 0)), 4),
                    "volume": int(float(row.get("Volume", 0))),
                })
            return bars
    except Exception:
        logger.debug("yfinance bars failed for %s", symbol, exc_info=True)
    return None


# ---------------------------------------------------------------------------
# Market simulator
# ---------------------------------------------------------------------------


def _fallback_quote(symbol: str, base_price: float | None = None) -> Quote:
    """Generate a deterministic-drift fallback quote.

    Args:
        symbol: The symbol code.
        base_price: Explicit base price; falls back to ``FALLBACK_PRICES``.

    Returns:
        A ``Quote`` with ``source="fallback"``.
    """
    base = base_price if base_price is not None else FALLBACK_PRICES.get(symbol)
    if base is None:
        base = 100.0  # arbitrary fallback for unknown symbols
    bucket = _current_time_bucket()
    noise = _deterministic_noise(symbol, bucket)
    last = base * (1.0 + noise)
    return _make_quote(symbol, last, "fallback")


def _synthetic_bars(
    symbol: str,
    last_price: float,
    period: str,
    limit: int,
) -> list[dict[str, Any]]:
    """Generate synthetic OHLCV bars from a current price using deterministic noise.

    Args:
        symbol: The symbol code.
        last_price: The most recent price to anchor the bar series.
        period: Bar interval (used for bar size in output only).
        limit: Number of bars to generate.

    Returns:
        A list of OHLCV dicts, oldest first.
    """
    bars: list[dict[str, Any]] = []
    for i in range(limit):
        noise_factor = 1.0 + _deterministic_noise(symbol, i)
        close = last_price * noise_factor
        open_ = last_price * (1.0 + _deterministic_noise(symbol, i + limit))
        high = max(open_, close) * (1.0 + abs(_deterministic_noise(symbol, i + 2 * limit)) * 0.5)
        low = min(open_, close) * (1.0 - abs(_deterministic_noise(symbol, i + 3 * limit)) * 0.5)
        volume = int(1_000_000 * (1.0 + _deterministic_noise(symbol, i + 4 * limit)))
        timestamp = (
            datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        )
        bars.append({
            "timestamp": timestamp,
            "open": round(open_, 4),
            "high": round(high, 4),
            "low": round(low, 4),
            "close": round(close, 4),
            "volume": volume,
        })
    return bars


def _symbol_in_universe(
    sym: str,
    *,
    universe: tuple[str, ...],
    market: str = "us_equity",
    allow_dynamic_symbols: bool = False,
) -> bool:
    """Check whether *sym* is allowed.

    When *allow_dynamic_symbols* is ``True``, any symbol whose inferred market
    matches *market* is accepted even if not explicitly listed in *universe*.
    """
    if sym in universe:
        return True
    if allow_dynamic_symbols and infer_market(sym) == market:
        return True
    return False


def quote(
    symbol: str,
    *,
    universe: tuple[str, ...] = DEFAULT_SYMBOLS,
    price_source: str = "auto",
    cached_prices: dict[str, float] | None = None,
    market: str = "us_equity",
    allow_dynamic_symbols: bool = False,
) -> Quote:
    """Fetch a simulated quote for *symbol*.

    Resolution order depends on *market*:

    - ``"us_equity"`` / ``"hk_equity"``: yfinance → cache → fallback
      deterministic drift.
    - ``"a_share"``: loader → cache → fallback deterministic drift.

    Args:
        symbol: The symbol to quote.
        universe: Allowed symbols.
        price_source: ``"auto"`` (try primary external source first),
            ``"loader"`` (external A-share loader only),
            ``"yfinance"`` (external yfinance only),
            ``"fallback"`` (skip external).
        cached_prices: Optional dict of previously-fetched prices keyed by
            symbol, used as the cache tier.
        market: The target market (``"us_equity"``, ``"a_share"``, ``"hk_equity"``).
        allow_dynamic_symbols: If ``True``, any symbol whose inferred market
            matches *market* passes the universe check.

    Returns:
        A :class:`Quote` instance.

    Raises:
        ValueError: If the symbol is not in the configured universe.
    """
    sym = normalize_symbol(symbol)
    if not _symbol_in_universe(sym, universe=universe, market=market, allow_dynamic_symbols=allow_dynamic_symbols):
        raise ValueError(f"symbol {sym!r} is not in the configured universe")

    # A-share routing: prefer loaders over yfinance.
    if market == "a_share":
        if price_source in ("auto", "loader"):
            ext = _try_loader_quote(sym, market)
            if ext is not None:
                return ext
            if price_source == "loader":
                raise ValueError(
                    f"loader quote failed for {sym!r} (market={market}). "
                    f"Ensure a loader (e.g. akshare, eastmoney) is installed and available."
                )

        # yfinance explicitly requested for A-share → degrade gracefully.
        if price_source == "yfinance":
            logger.info("yfinance requested for A-share %s; falling back to loader", sym)
            ext = _try_loader_quote(sym, market)
            if ext is not None:
                return ext

    # US / HK routing: prefer yfinance.
    else:
        if price_source in ("auto", "yfinance"):
            ext = _try_yfinance_quote(sym)
            if ext is not None:
                return ext

    # Tier 2: cache (fresh enough)
    if cached_prices is not None and sym in cached_prices:
        base = cached_prices[sym]
        return _fallback_quote(sym, base_price=base)

    # Tier 3: fallback deterministic drift
    return _fallback_quote(sym)


def historical_bars(
    symbol: str,
    *,
    period: str = "1d",
    limit: int = 90,
    universe: tuple[str, ...] = DEFAULT_SYMBOLS,
    price_source: str = "auto",
    cached_prices: dict[str, float] | None = None,
    market: str = "us_equity",
    allow_dynamic_symbols: bool = False,
) -> dict[str, Any]:
    """Fetch simulated historical OHLCV bars.

    Resolution order: yfinance (if allowed) → synthetic bars from current
    fallback price.

    Args:
        symbol: The symbol to fetch bars for.
        period: Bar interval (``1m``, ``5m``, ``1h``, ``1d``, etc.).
        limit: Number of bars.
        universe: Allowed symbols.
        price_source: Price source selection.
        cached_prices: Optional cached price dict.
        market: The target market (``"us_equity"``, ``"a_share"``, ``"hk_equity"``).
        allow_dynamic_symbols: If ``True``, any symbol whose inferred market
            matches *market* passes the universe check.

    Returns:
        A dict with ``status``, ``symbol``, ``period``, ``bars``, ``source``.

    Raises:
        ValueError: If the symbol is not in the configured universe.
    """
    sym = normalize_symbol(symbol)
    if not _symbol_in_universe(sym, universe=universe, market=market, allow_dynamic_symbols=allow_dynamic_symbols):
        raise ValueError(f"symbol {sym!r} is not in the configured universe")

    # A-share routing: prefer loader bars.
    if market == "a_share":
        if price_source in ("auto", "loader"):
            ext = _try_loader_bars(sym, market, period, limit)
            if ext is not None:
                return {
                    "status": "ok",
                    "symbol": sym,
                    "period": period,
                    "source": "loader",
                    "bars": ext,
                }
            if price_source == "loader":
                raise ValueError(
                    f"loader bars failed for {sym!r} (market={market}). "
                    f"Ensure a loader (e.g. akshare, eastmoney) is installed and available."
                )

        if price_source == "yfinance":
            logger.info("yfinance requested for A-share %s; falling back to loader bars", sym)
            ext = _try_loader_bars(sym, market, period, limit)
            if ext is not None:
                return {
                    "status": "ok",
                    "symbol": sym,
                    "period": period,
                    "source": "loader",
                    "bars": ext,
                }

    # US / HK routing: prefer yfinance bars.
    else:
        if price_source in ("auto", "yfinance"):
            ext = _try_yfinance_bars(sym, period, limit)
            if ext is not None:
                return {
                    "status": "ok",
                    "symbol": sym,
                    "period": period,
                    "source": "yfinance",
                    "bars": ext,
                }

    # Tier 2: synthetic bars from current fallback price
    q = quote(sym, universe=universe, price_source="fallback", cached_prices=cached_prices, market=market, allow_dynamic_symbols=allow_dynamic_symbols)
    bars = _synthetic_bars(sym, q.last, period, limit)
    return {
        "status": "ok",
        "symbol": sym,
        "period": period,
        "source": "fallback",
        "bars": bars,
    }
