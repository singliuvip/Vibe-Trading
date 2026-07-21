"""Loader registry with market-level fallback chains.

Loaders self-register via the ``@register`` decorator when their module is
first imported.  The ``_ensure_registered()`` helper lazily imports every
known loader module so that callers of ``resolve_loader`` /
``get_loader_cls_with_fallback`` never see an empty registry — regardless
of import order.
"""

from __future__ import annotations

import logging
from typing import Any, Type

from backtest.loaders.base import NoAvailableSourceError

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Global registry: source_name -> loader class
# ---------------------------------------------------------------------------

LOADER_REGISTRY: dict[str, Type[Any]] = {}

_registered = False

# Canonical set of accepted data-source names: every registered loader plus the
# ``"auto"`` cross-market selector. Single source of truth shared by the backtest
# config schema (``backtest.runner.BacktestConfigSchema``) and the agent-facing
# backtest tool (``src.tools.backtest_tool``) so the two can never drift apart.
# Keep in sync with ``_loader_modules`` below — the regression test
# ``test_valid_sources_covers_all_registered_loaders`` enforces full coverage.
VALID_SOURCES: set[str] = {
    "tushare",
    "okx",
    "yfinance",
    "akshare",
    "baostock",
    "tencent",
    "mootdx",
    "ccxt",
    "futu",
    "eastmoney",
    "sina",
    "stooq",
    "yahoo",
    "finnhub",
    "alphavantage",
    "tiingo",
    "fmp",
    "qveris",  # QVERIS-INTEGRATION
    "india_broker",
    "local",
    "auto",
}


def register(cls: Type[Any]) -> Type[Any]:
    """Class decorator: register a loader into the global registry.

    The class must have a ``name`` class attribute.
    """
    LOADER_REGISTRY[cls.name] = cls
    return cls


def _ensure_registered() -> None:
    """Import every known loader module so ``@register`` decorators fire.

    Safe to call multiple times — only runs the imports once.
    Loaders whose dependencies are missing (e.g. ``akshare`` not installed)
    are silently skipped.
    """
    global _registered
    if _registered:
        return
    _registered = True

    _loader_modules = [
        "backtest.loaders.tushare",
        "backtest.loaders.okx",
        "backtest.loaders.yfinance_loader",
        "backtest.loaders.akshare_loader",
        "backtest.loaders.baostock_loader",
        "backtest.loaders.tencent_loader",
        "backtest.loaders.mootdx_loader",
        "backtest.loaders.ccxt_loader",
        "backtest.loaders.futu",
        "backtest.loaders.eastmoney_loader",
        "backtest.loaders.sina_loader",
        "backtest.loaders.stooq_loader",
        "backtest.loaders.yahoo_loader",
        "backtest.loaders.finnhub_loader",
        "backtest.loaders.alphavantage_loader",
        "backtest.loaders.tiingo_loader",
        "backtest.loaders.fmp_loader",
        "backtest.loaders.qveris_loader",  # QVERIS-INTEGRATION
        "backtest.loaders.india_broker_loader",
        "backtest.loaders.local_loader",
    ]
    import importlib
    for mod in _loader_modules:
        try:
            importlib.import_module(mod)
        except Exception:
            pass


# Sources that must NEVER silently fall through to a network loader when the
# caller asked for them explicitly. ``local`` reads the user's own configured
# files (``~/.vibe-trading/data-bridge/config.yaml``); its ``markets`` set spans
# every market only so the cross-market auto-resolver can *reach* it, not so an
# unavailable ``local`` request can degrade into an unrelated network source.
# An explicit ``local`` request that is unavailable is a config problem the user
# must see, not something to paper over with a Yahoo/Tencent fetch.
_NO_NETWORK_FALLBACK_SOURCES: frozenset[str] = frozenset({"local", "qveris"})  # QVERIS-INTEGRATION


# ---------------------------------------------------------------------------
# Fallback chains: market_type -> ordered list of source names
# ---------------------------------------------------------------------------

# Tushare first for A-share (authorized premium source), Yahoo first for US/HK
# (with Stooq/AKShare as 403 fallback), Eastmoney demoted to last resort due to
# aggressive rate limiting.
FALLBACK_CHAINS: dict[str, list[str]] = {
    "a_share":   ["tushare", "tencent", "mootdx", "baostock", "akshare", "eastmoney", "local"],
    "us_equity": ["yahoo", "stooq", "sina", "eastmoney", "yfinance", "tiingo", "fmp", "finnhub", "alphavantage", "akshare", "local"],
    "hk_equity": ["yahoo", "akshare", "futu", "yfinance", "eastmoney", "local"],
    "india_equity": ["yahoo", "yfinance", "india_broker", "local"],
    "crypto":    ["okx", "ccxt", "yfinance", "local"],
    "futures":   ["tushare", "akshare", "local"],
    "fund":      ["tushare", "akshare", "local"],
    "macro":     ["akshare", "tushare", "local"],
    "forex":     ["akshare", "yfinance", "local"],
}


def resolve_loader(market: str) -> Any:
    """Return the first *available* loader instance for *market*.

    Walks the fallback chain and returns the first loader whose
    ``is_available()`` returns ``True``.

    Args:
        market: Market type key (e.g. ``"a_share"``, ``"crypto"``).

    Returns:
        A loader instance.

    Raises:
        NoAvailableSourceError: If every candidate is unavailable.
    """
    _ensure_registered()
    chain = FALLBACK_CHAINS.get(market, [])
    tried: list[str] = []
    for name in chain:
        if name not in LOADER_REGISTRY:
            continue
        tried.append(name)
        # Issue #50 — some loaders (e.g. Tushare) call into the SDK during
        # __init__ and raise on missing credentials. Treat that the same as
        # is_available()=False so the fallback chain keeps walking.
        try:
            loader = LOADER_REGISTRY[name]()
        except Exception as exc:
            logger.debug("loader %s failed to construct: %s", name, exc)
            continue
        if loader.is_available():
            return loader
    raise NoAvailableSourceError(
        f"No available data source for market '{market}'. "
        f"Tried: {tried or chain}. Check network and API token config."
    )


def get_loader_cls_with_fallback(source: str) -> Type[Any]:
    """Return a loader *class* for *source*, falling back if unavailable.

    Args:
        source: Requested data source name.

    Returns:
        A DataLoader class (not instance).

    Raises:
        NoAvailableSourceError: If the source and all fallbacks are unavailable.
    """
    _ensure_registered()
    if source not in LOADER_REGISTRY:
        raise NoAvailableSourceError(f"Unknown data source: {source}")

    loader_cls = LOADER_REGISTRY[source]
    try:
        instance = loader_cls()
    except Exception as exc:
        logger.debug("loader %s failed to construct: %s", source, exc)
        instance = None
    if instance is not None and instance.is_available():
        return loader_cls

    # Some sources must never silently degrade to an unrelated network loader
    # when explicitly requested. ``local`` is the canonical case: its broad
    # ``markets`` set exists only to make it reachable from the cross-market
    # auto-resolver, so falling back through it would fetch network data the
    # user never asked for and mask a Data Bridge config problem. Fail loudly.
    if source in _NO_NETWORK_FALLBACK_SOURCES:
        raise NoAvailableSourceError(
            f"Data source '{source}' is unavailable and does not fall back to a "
            f"network source. Check your local Data Bridge config "
            f"(~/.vibe-trading/data-bridge/config.yaml) — it must exist and list "
            f"at least one source."
        )

    # Source unavailable — try same-market fallback
    for market in loader_cls.markets:
        try:
            fallback = resolve_loader(market)
            logger.warning(
                "%s is unavailable, falling back to %s for market %s",
                source, fallback.name, market,
            )
            return type(fallback)
        except NoAvailableSourceError:
            continue

    raise NoAvailableSourceError(
        f"Data source '{source}' is unavailable and no fallback found."
    )


# ---------------------------------------------------------------------------
# Realtime provider registry (separate from LOADER_REGISTRY / FALLBACK_CHAINS)
#
# Realtime providers are NOT historical loaders — they serve live intraday data
# (rt_k, rt_min, stk_auction, etc.) and must not enter the fallback chain.
# ---------------------------------------------------------------------------

REALTIME_PROVIDER_REGISTRY: dict[tuple[str, str], type] = {}

_registered_realtime = False


def register_realtime_provider(source: str, capability: str, provider_cls: type) -> None:
    """Register a realtime provider class in the realtime-only registry.

    Args:
        source: Data source name (e.g. "tushare").
        capability: Endpoint capability (e.g. "rt_k", "rt_min", "stk_auction").
        provider_cls: The provider class (not instance).
    """
    REALTIME_PROVIDER_REGISTRY[(source, capability)] = provider_cls


def resolve_realtime_provider(source: str, capability: str) -> type | None:
    """Resolve a realtime provider class by (source, capability).

    Args:
        source: Data source name.
        capability: Endpoint capability.

    Returns:
        Provider class or None if not found.
    """
    _ensure_realtime_registered()
    return REALTIME_PROVIDER_REGISTRY.get((source, capability))


def _ensure_realtime_registered() -> None:
    """Lazily import realtime provider modules so they self-register.

    Safe to call multiple times — only runs the imports once.
    Providers whose dependencies are missing are silently skipped.
    """
    global _registered_realtime
    if _registered_realtime:
        return
    _registered_realtime = True

    _realtime_modules = [
        "backtest.loaders.tushare_realtime",
        "backtest.loaders.tushare_realtime_minute",
        "backtest.loaders.tushare_auction",
    ]
    import importlib

    for mod in _realtime_modules:
        try:
            importlib.import_module(mod)
        except Exception:
            pass


# Self-registration: each realtime provider module calls this at import time.
def _auto_register_realtime_providers() -> None:
    """Called by provider modules to register themselves."""
    # Individual providers register themselves via register_realtime_provider()
    # at module level. This function exists as a hook point for tests.
    pass
