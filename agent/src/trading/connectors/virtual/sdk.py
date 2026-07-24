"""Virtual broker SDK — unified connector interface.

This module exports the standard connector functions expected by
``src.trading.service`` for a ``broker_sdk`` transport. All functions are
thread-safe and work fully locally with no network or credentials required.

Exports (matching the ``sdk.py`` contract):
    build_config, check_status, get_account_snapshot, get_positions,
    get_open_orders, get_quote, get_historical_bars, place_order, cancel_order
"""

from __future__ import annotations

import logging
from copy import deepcopy
from dataclasses import dataclass, asdict, field
from typing import Any, Mapping

from src.trading.connectors.virtual.market_sim import (
    DEFAULT_SYMBOLS,
    historical_bars as market_historical_bars,
    normalize_symbol,
    quote as market_quote,
)
from src.trading.connectors.virtual.order_book import (
    cancel_order as book_cancel_order,
    load_or_initialize,
    match_open_orders,
    mark_to_market,
    save as book_save_state,
    submit_order as book_submit_order,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

_PROFILE_PROFILES = ("paper",)


@dataclass(frozen=True)
class VirtualConfig:
    """Configuration for the virtual broker connector.

    All fields have defaults so the connector works out of the box with no
    configuration file.

    Attributes:
        profile: Always ``"paper"``.
        account_id: Account identifier (default ``"default"``).
        currency: Base currency (default ``"USD"``).
        initial_cash: Starting cash balance for a new account.
        slippage_bps: Slippage in basis points (default 10 = 0.1%).
        fee_bps: Commission in basis points (default 5 = 0.05%).
        allow_short: Whether short selling is allowed.
        max_leverage: Maximum gross leverage (default 2.0).
        symbols: Tuple of allowed symbols.
        price_source: ``"auto"`` (try yfinance first) or ``"fallback"``.
        market: Target market (``"us_equity"``, ``"a_share"``, ``"hk_equity"``).
        allow_dynamic_symbols: If ``True``, symbols matching *market* are
            accepted even when not listed in *symbols*.
        base_prices: Custom base prices for fallback simulation.
    """

    profile: str = "paper"
    account_id: str = "default"
    currency: str = "USD"
    initial_cash: float = 1_000_000.0
    slippage_bps: float = 10.0
    fee_bps: float = 5.0
    allow_short: bool = True
    max_leverage: float = 2.0
    symbols: tuple[str, ...] = DEFAULT_SYMBOLS
    price_source: str = "auto"
    market: str = "us_equity"
    allow_dynamic_symbols: bool = False
    base_prices: dict[str, float] = field(default_factory=dict)

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any] | None = None) -> "VirtualConfig":
        """Build a config from a JSON-like mapping."""
        payload = dict(data or {})
        profile = str(payload.get("profile") or "paper").strip().lower()
        if profile not in _PROFILE_PROFILES:
            profile = "paper"
        symbols_raw = payload.get("symbols")
        if symbols_raw is None:
            symbols_raw = list(DEFAULT_SYMBOLS)
        market_val = str(payload.get("market") or "us_equity").strip().lower()
        if market_val not in ("us_equity", "a_share", "hk_equity"):
            market_val = "us_equity"
        base_prices_raw = payload.get("base_prices") or {}
        try:
            base_prices = {str(k): float(v) for k, v in dict(base_prices_raw).items()}
        except (TypeError, ValueError):
            base_prices = {}
        return cls(
            profile=profile,
            account_id=str(payload.get("account_id") or "default").strip(),
            currency=str(payload.get("currency") or "USD").strip().upper(),
            initial_cash=float(payload.get("initial_cash") or 1_000_000.0),
            slippage_bps=float(payload.get("slippage_bps") or 10.0),
            fee_bps=float(payload.get("fee_bps") or 5.0),
            allow_short=bool(payload.get("allow_short", True)),
            max_leverage=float(payload.get("max_leverage") or 2.0),
            symbols=tuple(str(s).strip().upper() for s in symbols_raw if str(s).strip()),
            price_source=str(payload.get("price_source") or "auto").strip().lower(),
            market=market_val,
            allow_dynamic_symbols=bool(payload.get("allow_dynamic_symbols", False)),
            base_prices=base_prices,
        )

    def with_overrides(
        self,
        *,
        account_id: str | None = None,
        initial_cash: float | None = None,
        slippage_bps: float | None = None,
        fee_bps: float | None = None,
        allow_short: bool | None = None,
        max_leverage: float | None = None,
        price_source: str | None = None,
        symbols: tuple[str, ...] | None = None,
        market: str | None = None,
        allow_dynamic_symbols: bool | None = None,
        base_prices: dict[str, float] | None = None,
    ) -> "VirtualConfig":
        """Return a copy with CLI/tool overrides applied."""
        payload = asdict(self)
        if account_id is not None:
            payload["account_id"] = account_id
        if initial_cash is not None:
            payload["initial_cash"] = initial_cash
        if slippage_bps is not None:
            payload["slippage_bps"] = slippage_bps
        if fee_bps is not None:
            payload["fee_bps"] = fee_bps
        if allow_short is not None:
            payload["allow_short"] = allow_short
        if max_leverage is not None:
            payload["max_leverage"] = max_leverage
        if price_source is not None:
            payload["price_source"] = price_source
        if symbols is not None:
            payload["symbols"] = list(symbols)
        if market is not None:
            payload["market"] = market
        if allow_dynamic_symbols is not None:
            payload["allow_dynamic_symbols"] = allow_dynamic_symbols
        if base_prices is not None:
            payload["base_prices"] = base_prices
        return VirtualConfig.from_mapping(payload)

    @property
    def environment(self) -> str:
        """Return ``"paper"`` (all virtual profiles are paper)."""
        return "paper"

    @property
    def is_demo(self) -> bool:
        """Return ``True`` (all virtual accounts are demo/paper)."""
        return True


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

_OVERRIDE_KEYS = ("account_id", "initial_cash", "slippage_bps", "fee_bps", "allow_short", "max_leverage", "price_source", "symbols", "market", "allow_dynamic_symbols", "base_prices")


def build_config(
    profile_config: Mapping[str, Any] | None = None,
    overrides: Mapping[str, Any] | None = None,
) -> VirtualConfig:
    """Resolve config: profile defaults ← CLI overrides.

    Unlike real connectors, there is no config file to load — the virtual
    broker has zero required configuration.

    Args:
        profile_config: Profile-level config dict from the ``TradingProfile``.
        overrides: CLI/tool overrides.

    Returns:
        A resolved ``VirtualConfig``.
    """
    base = {}
    if profile_config:
        base.update(dict(profile_config))
    cfg = VirtualConfig.from_mapping(base)
    if overrides:
        clean = {k: v for k, v in dict(overrides).items() if k in _OVERRIDE_KEYS and v not in (None, "")}
        if clean:
            return cfg.with_overrides(**clean)
    return cfg


# ---------------------------------------------------------------------------
# SDK public API
# ---------------------------------------------------------------------------


def check_status(config: VirtualConfig | None = None) -> dict[str, Any]:
    """Check virtual broker readiness (always succeeds).

    Returns a JSON-serializable health report. No network or credentials
    required.

    Args:
        config: Resolved config; defaults when ``None``.

    Returns:
        A dict with ``status``, ``profile``, ``account``, ``configured``.
    """
    cfg = config or build_config()
    return {
        "status": "ok",
        "profile": cfg.profile,
        "is_demo": True,
        "account_id": cfg.account_id,
        "currency": cfg.currency,
        "initial_cash": cfg.initial_cash,
        "configured": True,
        "storage": f"~/.vibe-trading/live/virtual/{cfg.account_id}/",
        "universe": list(cfg.symbols),
        "market": cfg.market,
        "allow_dynamic_symbols": cfg.allow_dynamic_symbols,
        "slippage_bps": cfg.slippage_bps,
        "fee_bps": cfg.fee_bps,
        "allow_short": cfg.allow_short,
        "max_leverage": cfg.max_leverage,
        "notes": "Fully local virtual broker. No credentials or network required.",
    }


def get_account_snapshot(config: VirtualConfig | None = None) -> dict[str, Any]:
    """Fetch the virtual account snapshot.

    Args:
        config: Resolved config.

    Returns:
        A dict with account balance, equity, and buying power.
    """
    cfg = config or build_config()
    state = load_or_initialize(cfg.account_id, cfg.initial_cash, cfg.currency, cfg.symbols)
    mark_to_market(state, universe=cfg.symbols, price_source=cfg.price_source, market=cfg.market, allow_dynamic_symbols=cfg.allow_dynamic_symbols, base_prices=cfg.base_prices)
    try:
        match_open_orders(
            state,
            slippage_bps=cfg.slippage_bps,
            fee_bps=cfg.fee_bps,
            allow_short=cfg.allow_short,
            max_leverage=cfg.max_leverage,
            universe=cfg.symbols,
            price_source=cfg.price_source,
            market=cfg.market,
            allow_dynamic_symbols=cfg.allow_dynamic_symbols,
            base_prices=cfg.base_prices,
        )
    except Exception:
        logger.debug("match_open_orders failed during account snapshot", exc_info=True)

    total_equity = state.account.cash + sum(
        abs(p.market_value) for p in state.positions.values()
    )
    # Buying power: 2x cash for simplicity (ignoring position-based margin).
    buying_power = state.account.cash * 2.0 if cfg.allow_short else state.account.cash

    return {
        "status": "ok",
        "profile": cfg.profile,
        "is_demo": True,
        "account": {
            "account_id": state.account.account_id,
            "currency": state.account.currency,
            "cash": round(state.account.cash, 2),
            "realized_pnl": round(state.account.realized_pnl, 2),
            "total_equity": round(total_equity, 2),
            "buying_power": round(buying_power, 2),
            "created_at": state.account.created_at,
            "updated_at": state.account.updated_at,
        },
        "positions": [
            {
                "symbol": pos.symbol,
                "quantity": pos.quantity,
                "avg_price": round(pos.avg_price, 4),
                "market_value": round(pos.market_value, 2),
                "unrealized_pnl": round(pos.unrealized_pnl, 2),
            }
            for pos in state.positions.values()
        ],
    }


def get_positions(config: VirtualConfig | None = None) -> dict[str, Any]:
    """Fetch current virtual positions.

    Args:
        config: Resolved config.

    Returns:
        A dict with positions list.
    """
    cfg = config or build_config()
    state = load_or_initialize(cfg.account_id, cfg.initial_cash, cfg.currency, cfg.symbols)
    mark_to_market(state, universe=cfg.symbols, price_source=cfg.price_source, market=cfg.market, allow_dynamic_symbols=cfg.allow_dynamic_symbols, base_prices=cfg.base_prices)
    try:
        match_open_orders(
            state,
            slippage_bps=cfg.slippage_bps,
            fee_bps=cfg.fee_bps,
            allow_short=cfg.allow_short,
            max_leverage=cfg.max_leverage,
            universe=cfg.symbols,
            price_source=cfg.price_source,
            market=cfg.market,
            allow_dynamic_symbols=cfg.allow_dynamic_symbols,
            base_prices=cfg.base_prices,
        )
    except Exception:
        logger.debug("match_open_orders failed during positions fetch", exc_info=True)

    return {
        "status": "ok",
        "profile": cfg.profile,
        "is_demo": True,
        "positions": [
            {
                "symbol": pos.symbol,
                "quantity": pos.quantity,
                "avg_price": round(pos.avg_price, 4),
                "market_value": round(pos.market_value, 2),
                "unrealized_pnl": round(pos.unrealized_pnl, 2),
            }
            for pos in state.positions.values()
        ],
    }


def get_open_orders(
    config: VirtualConfig | None = None,
    *,
    include_executions: bool = False,
) -> dict[str, Any]:
    """Fetch open orders and optionally recent executions.

    Args:
        config: Resolved config.
        include_executions: Whether to include recent execution history.

    Returns:
        A dict with open_orders and optionally executions.
    """
    cfg = config or build_config()
    state = load_or_initialize(cfg.account_id, cfg.initial_cash, cfg.currency, cfg.symbols)
    # Match limit orders before returning.
    try:
        match_open_orders(
            state,
            slippage_bps=cfg.slippage_bps,
            fee_bps=cfg.fee_bps,
            allow_short=cfg.allow_short,
            max_leverage=cfg.max_leverage,
            universe=cfg.symbols,
            price_source=cfg.price_source,
            market=cfg.market,
            allow_dynamic_symbols=cfg.allow_dynamic_symbols,
            base_prices=cfg.base_prices,
        )
    except Exception:
        logger.debug("match_open_orders failed during open orders fetch", exc_info=True)

    result: dict[str, Any] = {
        "status": "ok",
        "profile": cfg.profile,
        "is_demo": True,
        "open_orders": [
            {
                "order_id": o.order_id,
                "symbol": o.symbol,
                "side": o.side,
                "quantity": o.quantity,
                "notional": o.notional,
                "order_type": o.order_type,
                "limit_price": o.limit_price,
                "time_in_force": o.time_in_force,
                "status": o.status,
                "filled_qty": o.filled_qty,
                "filled_avg_price": o.filled_avg_price,
                "created_at": o.created_at,
                "updated_at": o.updated_at,
            }
            for o in sorted(state.orders.values(), key=lambda x: x.created_at, reverse=True)
        ],
    }

    if include_executions:
        result["executions"] = [
            {
                "execution_id": e.execution_id,
                "order_id": e.order_id,
                "symbol": e.symbol,
                "side": e.side,
                "quantity": e.quantity,
                "price": e.price,
                "gross_notional": e.gross_notional,
                "fee": e.fee,
                "timestamp": e.timestamp,
            }
            for e in state.executions[:100]  # last 100 executions
        ]

    return result


def get_quote(
    symbol: str,
    *,
    config: VirtualConfig | None = None,
    **_: Any,
) -> dict[str, Any]:
    """Fetch a simulated quote for ``symbol``.

    Args:
        symbol: The symbol to quote.
        config: Resolved config.
        **_: Unused (accepts and ignores extra kwargs for interface parity).

    Returns:
        A dict with bid, ask, last, and source.
    """
    cfg = config or build_config()
    state = load_or_initialize(cfg.account_id, cfg.initial_cash, cfg.currency, cfg.symbols)
    try:
        q = market_quote(
            symbol,
            universe=cfg.symbols,
            price_source=cfg.price_source,
            cached_prices=state.prices,
            market=cfg.market,
            allow_dynamic_symbols=cfg.allow_dynamic_symbols,
            base_prices=cfg.base_prices,
        )
    except ValueError as exc:
        return {"status": "error", "error": str(exc)}

    if q.source == "price_unavailable":
        return {
            "status": "error",
            "error": f"no price available for {q.symbol!r} — all data sources failed",
            "symbol": q.symbol,
            "quote": {"bid": 0.0, "ask": 0.0, "last": 0.0, "source": "price_unavailable"},
        }

    return {
        "status": "ok",
        "profile": cfg.profile,
        "is_demo": True,
        "symbol": q.symbol,
        "quote": {
            "bid": q.bid,
            "ask": q.ask,
            "last": q.last,
            "time": q.time,
            "source": q.source,
        },
    }


def get_historical_bars(
    symbol: str,
    *,
    config: VirtualConfig | None = None,
    period: str = "1d",
    limit: int = 90,
    **_: Any,
) -> dict[str, Any]:
    """Fetch simulated historical OHLCV bars.

    Args:
        symbol: The symbol to fetch.
        config: Resolved config.
        period: Bar interval (``1m``, ``5m``, ``1h``, ``1d``, etc.).
        limit: Number of bars.
        **_: Unused (accepts extra kwargs for interface parity).

    Returns:
        A dict with bars list.
    """
    cfg = config or build_config()
    state = load_or_initialize(cfg.account_id, cfg.initial_cash, cfg.currency, cfg.symbols)
    return market_historical_bars(
        symbol,
        period=period,
        limit=limit,
        universe=cfg.symbols,
        price_source=cfg.price_source,
        cached_prices=state.prices,
        market=cfg.market,
        allow_dynamic_symbols=cfg.allow_dynamic_symbols,
        base_prices=cfg.base_prices,
    )


def place_order(
    config: VirtualConfig | None = None,
    *,
    symbol: str,
    side: str,
    quantity: float | None = None,
    notional: float | None = None,
    order_type: str = "market",
    limit_price: float | None = None,
    time_in_force: str = "day",
) -> dict[str, Any]:
    """Place a simulated order against the virtual account.

    Args:
        config: Resolved config.
        symbol: Symbol code.
        side: ``"buy"`` or ``"sell"``.
        quantity: Base-currency quantity. Exactly one of ``quantity``/``notional``.
        notional: Quote-currency notional (market orders only).
        order_type: ``"market"`` or ``"limit"``.
        limit_price: Limit price (required for limit orders).
        time_in_force: ``"day"``, ``"gtc"``, ``"ioc"``.

    Returns:
        A dict with ``status``, ``order_id``, ``order_status``, and fill details
        for market orders.
    """
    cfg = config or build_config()

    # Validate inputs.
    clean_side = str(side or "").strip().lower()
    if clean_side not in ("buy", "sell"):
        return {"status": "error", "error": "side must be 'buy' or 'sell'", "symbol": symbol}

    clean_type = str(order_type or "").strip().lower()
    if clean_type not in ("market", "limit"):
        return {"status": "error", "error": "order_type must be 'market' or 'limit'", "symbol": symbol}

    clean_symbol = normalize_symbol(symbol or "")
    if not clean_symbol:
        return {"status": "error", "error": "symbol is required"}

    has_qty = quantity is not None
    has_notional = notional is not None
    if has_qty == has_notional:
        return {
            "status": "error",
            "error": "exactly one of quantity or notional is required",
            "symbol": clean_symbol,
        }

    if clean_type == "limit":
        if limit_price is None:
            return {"status": "error", "error": "limit order requires limit_price", "symbol": clean_symbol}
        if not has_qty:
            return {
                "status": "error",
                "error": "limit order must be sized with quantity (base size)",
                "symbol": clean_symbol,
            }

    qty_val = float(quantity) if has_qty else 0.0
    notional_val = float(notional) if has_notional else 0.0

    state = load_or_initialize(cfg.account_id, cfg.initial_cash, cfg.currency, cfg.symbols)
    return book_submit_order(
        state,
        symbol=clean_symbol,
        side=clean_side,
        quantity=qty_val,
        notional=notional_val,
        order_type=clean_type,
        limit_price=float(limit_price) if limit_price is not None else None,
        time_in_force=str(time_in_force or "day").strip().lower(),
        slippage_bps=cfg.slippage_bps,
        fee_bps=cfg.fee_bps,
        allow_short=cfg.allow_short,
        max_leverage=cfg.max_leverage,
        universe=cfg.symbols,
        price_source=cfg.price_source,
        market=cfg.market,
        allow_dynamic_symbols=cfg.allow_dynamic_symbols,
        base_prices=cfg.base_prices,
    )


def cancel_order(
    config: VirtualConfig | None = None,
    order_id: str = "",
    *,
    symbol: str | None = None,
) -> dict[str, Any]:
    """Cancel an open virtual order.

    Args:
        config: Resolved config.
        order_id: The order to cancel.
        symbol: Optional symbol filter.

    Returns:
        A dict with ``status`` and ``order_status``.
    """
    cfg = config or build_config()
    if not order_id:
        return {"status": "error", "error": "order_id is required"}
    state = load_or_initialize(cfg.account_id, cfg.initial_cash, cfg.currency, cfg.symbols)
    return book_cancel_order(state, order_id, symbol=symbol)
