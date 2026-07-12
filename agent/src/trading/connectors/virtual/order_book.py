"""Virtual order book — account, positions, orders, matching, and persistence.

This module owns the core simulation state and all mutation logic. Every public
function follows the pattern::

    with lock:
        state = load_or_initialize()
        ... mutate ...
        save(state)
        return result

Thread safety is provided by a module-level ``threading.RLock`` so multiple
callers (agent tool, LiveRunner tick, API) never corrupt state concurrently.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from copy import deepcopy
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from src.trading.connectors.virtual.market_sim import (
    Quote,
    quote as market_quote,
    historical_bars as market_bars,
)
from src.trading.connectors.virtual.rules import (
    BuyLot,
    calculate_fees,
    can_sell_today,
    check_price_limit,
    consume_buy_lots,
    today_str,
    validate_quantity,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# State model
# ---------------------------------------------------------------------------


@dataclass
class VirtualPosition:
    """A position in one symbol.

    Attributes:
        symbol: Normalised symbol code.
        quantity: Signed quantity (positive = long, negative = short).
        avg_price: Weighted average entry price.
        market_value: Current market value (abs(quantity) * last_price).
        unrealized_pnl: Unrealised profit/loss.
        buy_lots: List of buy lot records for T+1 tracking (A-share only).
            Each dict has ``trade_date``, ``quantity``, ``price``.
    """

    symbol: str
    quantity: float
    avg_price: float
    market_value: float = 0.0
    unrealized_pnl: float = 0.0
    buy_lots: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class VirtualOrder:
    """A submitted order awaiting fill or already filled.

    Attributes:
        order_id: Unique order identifier.
        symbol: Normalised symbol code.
        side: ``"buy"`` or ``"sell"``.
        quantity: Requested quantity (base units). Zero for notional-sized orders.
        notional: Requested notional (quote currency). Zero for quantity-sized.
        order_type: ``"market"`` or ``"limit"``.
        limit_price: Limit price (``None`` for market orders).
        time_in_force: ``"day"``, ``"gtc"``, ``"ioc"``.
        status: ``"open"``, ``"filled"``, ``"cancelled"``, ``"rejected"``.
        created_at: ISO-8601 UTC timestamp of submission.
        updated_at: ISO-8601 UTC timestamp of last status change.
        filled_qty: Quantity filled so far.
        filled_avg_price: Volume-weighted average fill price.
    """

    order_id: str
    symbol: str
    side: str
    quantity: float
    notional: float
    order_type: str
    limit_price: float | None
    time_in_force: str
    status: str
    created_at: str
    updated_at: str
    filled_qty: float = 0.0
    filled_avg_price: float | None = None


@dataclass
class VirtualExecution:
    """One fill (full or partial) of an order.

    Attributes:
        execution_id: Unique execution identifier.
        order_id: The order that produced this fill.
        symbol: Normalised symbol code.
        side: ``"buy"`` or ``"sell"``.
        quantity: Quantity filled in this execution.
        price: Execution price after slippage.
        gross_notional: ``quantity * price``.
        fee: Commission charged for this execution.
        slippage: Slippage amount (price impact) in currency units.
        timestamp: ISO-8601 UTC timestamp.
    """

    execution_id: str
    order_id: str
    symbol: str
    side: str
    quantity: float
    price: float
    gross_notional: float
    fee: float
    slippage: float
    timestamp: str


@dataclass
class VirtualAccount:
    """The top-level simulation state.

    Attributes:
        account_id: Stable account identifier.
        currency: Base currency (``"USD"``).
        cash: Current cash balance.
        realized_pnl: Cumulative realised P&L.
        created_at: ISO-8601 UTC creation timestamp.
        updated_at: ISO-8601 UTC last-update timestamp.
    """

    account_id: str
    currency: str
    cash: float
    realized_pnl: float
    created_at: str
    updated_at: str


@dataclass
class VirtualState:
    """Complete simulation state persisted to disk.

    Attributes:
        version: Schema version for forward-compat.
        account: The :class:`VirtualAccount`.
        positions: Dict of symbol → :class:`VirtualPosition`.
        orders: Dict of ``order_id`` → :class:`VirtualOrder`.
        executions: List of :class:`VirtualExecution` (newest first).
        prices: Dict of symbol → last known price (cache for MarketSim).
    """

    version: int
    account: VirtualAccount
    positions: dict[str, VirtualPosition]
    orders: dict[str, VirtualOrder]
    executions: list[VirtualExecution]
    prices: dict[str, float]


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------

_STATE_VERSION = 2


def _now_iso() -> str:
    """Return the current UTC time as ISO-8601."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _new_id(prefix: str = "") -> str:
    """Return a short, unique identifier."""
    raw = uuid.uuid4().hex[:16]
    return f"{prefix}{raw}" if prefix else raw


def _state_to_dict(state: VirtualState) -> dict[str, Any]:
    """Serialize *state* to a JSON-compatible dict."""

    def _pos_dict(p: VirtualPosition) -> dict[str, Any]:
        return asdict(p)

    def _ord_dict(o: VirtualOrder) -> dict[str, Any]:
        return asdict(o)

    def _exec_dict(e: VirtualExecution) -> dict[str, Any]:
        return asdict(e)

    return {
        "version": state.version,
        "account": asdict(state.account),
        "positions": {sym: _pos_dict(p) for sym, p in state.positions.items()},
        "orders": {oid: _ord_dict(o) for oid, o in state.orders.items()},
        "executions": [_exec_dict(e) for e in state.executions],
        "prices": dict(state.prices),
    }


def _state_from_dict(data: dict[str, Any]) -> VirtualState:
    """Deserialize a dict to a VirtualState."""
    acct_raw = data.get("account", {})
    account = VirtualAccount(
        account_id=str(acct_raw.get("account_id", "default")),
        currency=str(acct_raw.get("currency", "USD")),
        cash=float(acct_raw.get("cash", 0.0)),
        realized_pnl=float(acct_raw.get("realized_pnl", 0.0)),
        created_at=str(acct_raw.get("created_at", _now_iso())),
        updated_at=str(acct_raw.get("updated_at", _now_iso())),
    )

    positions: dict[str, VirtualPosition] = {}
    for sym, raw in data.get("positions", {}).items():
        buy_lots_raw = raw.get("buy_lots")
        if buy_lots_raw is None or not isinstance(buy_lots_raw, list):
            buy_lots_raw = []
        positions[sym] = VirtualPosition(
            symbol=str(raw.get("symbol", sym)),
            quantity=float(raw.get("quantity", 0.0)),
            avg_price=float(raw.get("avg_price", 0.0)),
            market_value=float(raw.get("market_value", 0.0)),
            unrealized_pnl=float(raw.get("unrealized_pnl", 0.0)),
            buy_lots=list(buy_lots_raw),
        )

    orders: dict[str, VirtualOrder] = {}
    for oid, raw in data.get("orders", {}).items():
        orders[oid] = VirtualOrder(
            order_id=str(raw.get("order_id", oid)),
            symbol=str(raw.get("symbol", "")),
            side=str(raw.get("side", "")),
            quantity=float(raw.get("quantity", 0.0)),
            notional=float(raw.get("notional", 0.0)),
            order_type=str(raw.get("order_type", "market")),
            limit_price=(
                float(raw["limit_price"]) if raw.get("limit_price") is not None else None
            ),
            time_in_force=str(raw.get("time_in_force", "day")),
            status=str(raw.get("status", "open")),
            created_at=str(raw.get("created_at", _now_iso())),
            updated_at=str(raw.get("updated_at", _now_iso())),
            filled_qty=float(raw.get("filled_qty", 0.0)),
            filled_avg_price=(
                float(raw["filled_avg_price"])
                if raw.get("filled_avg_price") is not None
                else None
            ),
        )

    executions_raw = data.get("executions", [])
    executions: list[VirtualExecution] = []
    for raw in executions_raw:
        executions.append(
            VirtualExecution(
                execution_id=str(raw.get("execution_id", "")),
                order_id=str(raw.get("order_id", "")),
                symbol=str(raw.get("symbol", "")),
                side=str(raw.get("side", "")),
                quantity=float(raw.get("quantity", 0.0)),
                price=float(raw.get("price", 0.0)),
                gross_notional=float(raw.get("gross_notional", 0.0)),
                fee=float(raw.get("fee", 0.0)),
                slippage=float(raw.get("slippage", 0.0)),
                timestamp=str(raw.get("timestamp", _now_iso())),
            )
        )

    return VirtualState(
        version=data.get("version", _STATE_VERSION),
        account=account,
        positions=positions,
        orders=orders,
        executions=executions,
        prices=dict(data.get("prices", {})),
    )


# ---------------------------------------------------------------------------
# State directory and persistence
# ---------------------------------------------------------------------------


def _state_dir(account_id: str) -> Path:
    """Return the state directory for an account.

    Resolves to ``~/.vibe-trading/live/virtual/<account_id>/``.
    """
    from src.live.paths import broker_dir

    return broker_dir("virtual") / account_id


def _state_path(account_id: str) -> Path:
    """Return the state JSON file path."""
    return _state_dir(account_id) / "state.json"


def _load_state(account_id: str, initial_cash: float, currency: str) -> VirtualState:
    """Load state from disk, or create a fresh state.

    Args:
        account_id: Account identifier.
        initial_cash: Starting cash balance for a new account.
        currency: Base currency.

    Returns:
        A ``VirtualState`` (loaded from disk or freshly initialised).
    """
    path = _state_path(account_id)
    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            return _state_from_dict(raw)
        except (OSError, json.JSONDecodeError, ValueError, KeyError, TypeError) as exc:
            logger.warning("corrupt virtual state at %s: %s; reinitialising", path, exc)
            # Fall through to fresh state.

    now = _now_iso()
    return VirtualState(
        version=_STATE_VERSION,
        account=VirtualAccount(
            account_id=account_id,
            currency=currency,
            cash=initial_cash,
            realized_pnl=0.0,
            created_at=now,
            updated_at=now,
        ),
        positions={},
        orders={},
        executions=[],
        prices={},
    )


def _save_state(state: VirtualState) -> None:
    """Atomically persist *state* to disk.

    Uses a temp-file + ``os.replace`` pattern so a crash mid-write never
    corrupts the canonical file.
    """
    path = _state_path(state.account.account_id)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    payload = json.dumps(_state_to_dict(state), ensure_ascii=False, indent=2)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Matching engine
# ---------------------------------------------------------------------------


def _execution_price(
    quote: Quote,
    side: str,
    slippage_bps: float,
) -> float:
    """Calculate execution price after slippage.

    A buy order pays the ASK price plus slippage (price impact).
    A sell order receives the BID price minus slippage.

    Args:
        quote: Current market quote.
        side: ``"buy"`` or ``"sell"``.
        slippage_bps: Slippage in basis points.

    Returns:
        The execution price.
    """
    slip_frac = slippage_bps / 10_000.0
    if side == "buy":
        return quote.ask * (1.0 + slip_frac)
    else:
        return quote.bid * (1.0 - slip_frac)


def _should_fill(order: VirtualOrder, quote: Quote) -> bool:
    """Return whether *order* should be filled against *quote*.

    Market orders always fill. Limit orders fill when the limit price is
    satisfied by the current quote.
    """
    if order.status != "open":
        return False
    if order.order_type == "market":
        return True
    # Limit order check.
    if order.side == "buy":
        return quote.ask <= order.limit_price  # type: ignore[operator]
    else:  # sell
        return quote.bid >= order.limit_price  # type: ignore[operator]


def _fill_order(
    state: VirtualState,
    order: VirtualOrder,
    quote: Quote,
    slippage_bps: float,
    fee_bps: float,
    *,
    market: str = "us_equity",
) -> VirtualExecution:
    """Fill *order* against *quote*, updating state in-place.

    Args:
        state: The simulation state (mutated in-place).
        order: The order to fill (mutated in-place).
        quote: The current quote.
        slippage_bps: Slippage basis points.
        fee_bps: Commission basis points (ignored for A-share, which uses
            ``rules.calculate_fees()``).
        market: The target market (``"us_equity"``, ``"a_share"``,
            ``"hk_equity"``).

    Returns:
        The :class:`VirtualExecution` for this fill.
    """
    is_a_share = market == "a_share"
    price = _execution_price(quote, order.side, slippage_bps)

    # Resolve quantity: if notional-based, derive quantity from price.
    if order.quantity > 0:
        qty = order.quantity
    elif order.notional > 0:
        qty = order.notional / price
    else:
        qty = 1.0  # Should not happen due to validation.

    gross = qty * price

    if is_a_share:
        # A-share fee schedule: commission + stamp tax (sell only) + transfer fee.
        fees = calculate_fees(order.side, qty, price)
        fee = fees["total"]
    else:
        fee = gross * fee_bps / 10_000.0

    slippage_amount = (
        qty * (price - quote.ask) if order.side == "buy" else qty * (quote.bid - price)
    )

    signed_qty = qty if order.side == "buy" else -qty
    cash_delta = -gross - fee if order.side == "buy" else gross - fee

    # Update account cash.
    state.account.cash += cash_delta
    state.account.updated_at = _now_iso()

    # Update position.
    current_pos = state.positions.get(order.symbol)
    trade_day = today_str()
    if current_pos is not None and current_pos.quantity != 0:
        new_qty = current_pos.quantity + signed_qty
        if new_qty == 0:
            # Position closed — realise P&L.
            close_pnl = -current_pos.quantity * (price - current_pos.avg_price)
            state.account.realized_pnl += close_pnl
            del state.positions[order.symbol]
        elif (current_pos.quantity > 0 and signed_qty > 0) or (
            current_pos.quantity < 0 and signed_qty < 0
        ):
            # Increasing position — blend average price.
            total_cost = current_pos.avg_price * abs(current_pos.quantity) + price * qty
            total_qty = abs(current_pos.quantity) + qty
            current_pos.avg_price = total_cost / total_qty
            current_pos.quantity = new_qty
            # A-share: record new buy lot for T+1 tracking.
            if is_a_share and order.side == "buy":
                current_pos.buy_lots.append({
                    "trade_date": trade_day,
                    "quantity": qty,
                    "price": price,
                })
        else:
            # Reducing or flipping position.
            reduce_qty = min(abs(current_pos.quantity), qty)
            close_pnl = -reduce_qty * (price - current_pos.avg_price) * (
                1 if current_pos.quantity > 0 else -1
            )
            state.account.realized_pnl += close_pnl
            remaining_signed = current_pos.quantity + signed_qty
            # A-share: consume buy lots for the sold quantity.
            if is_a_share and order.side == "sell" and current_pos.buy_lots:
                buy_lots_objs = [
                    BuyLot(trade_date=bl["trade_date"], quantity=bl["quantity"], price=bl["price"])
                    for bl in current_pos.buy_lots
                ]
                updated = consume_buy_lots(buy_lots_objs, qty)
                current_pos.buy_lots = [
                    {"trade_date": bl.trade_date, "quantity": bl.quantity, "price": bl.price}
                    for bl in updated
                ]
            if remaining_signed == 0:
                del state.positions[order.symbol]
            elif abs(remaining_signed) < abs(current_pos.quantity):
                # Partial reduction — keep current avg_price.
                current_pos.quantity = remaining_signed
            else:
                # Direction flip — new position at flipped price.
                flip_qty = remaining_signed
                current_pos.quantity = flip_qty
                current_pos.avg_price = price
                # A-share: if flipped to long, record new buy lot.
                if is_a_share and remaining_signed > 0:
                    current_pos.buy_lots = [{
                        "trade_date": trade_day,
                        "quantity": abs(remaining_signed),
                        "price": price,
                    }]
    else:
        # New position.
        buy_lots_init: list[dict[str, Any]] = []
        if is_a_share and order.side == "buy":
            buy_lots_init = [{"trade_date": trade_day, "quantity": qty, "price": price}]
        state.positions[order.symbol] = VirtualPosition(
            symbol=order.symbol,
            quantity=signed_qty,
            avg_price=price,
            buy_lots=buy_lots_init,
        )

    # Update order.
    order.filled_qty += qty
    order.filled_avg_price = price
    order.status = "filled"
    order.updated_at = _now_iso()

    # Record execution.
    exec_id = _new_id("exec_")
    execution = VirtualExecution(
        execution_id=exec_id,
        order_id=order.order_id,
        symbol=order.symbol,
        side=order.side,
        quantity=qty,
        price=price,
        gross_notional=gross,
        fee=fee,
        slippage=abs(slippage_amount),
        timestamp=_now_iso(),
    )
    state.executions.insert(0, execution)

    # Update market prices in state.
    state.prices[order.symbol] = quote.last

    return execution


def _check_local_limits(
    state: VirtualState,
    order: VirtualOrder,
    quote: Quote,
    allow_short: bool,
    max_leverage: float,
    slippage_bps: float,
    *,
    market: str = "us_equity",
) -> str | None:
    """Check local account-level limits before filling.

    Returns ``None`` if the order passes, or an error string if rejected.

    Checks:
    - Sufficient cash for buy orders.
    - Sufficient shares for sell orders (unless shorting allowed).
    - A-share: shorting is always forbidden, regardless of *allow_short*.
    - A-share: T+1 sell eligibility.
    - Leverage post-trade does not exceed ``max_leverage``.
    """
    is_a_share = market == "a_share"
    # A-share forbids short selling regardless of config.
    effective_allow_short = False if is_a_share else allow_short

    price = _execution_price(quote, order.side, slippage_bps)
    qty = order.quantity if order.quantity > 0 else (order.notional / price if order.notional > 0 else 1.0)
    gross = qty * price
    # A-share: use precise fee calculation from rules; otherwise use bps.
    if is_a_share:
        fee = calculate_fees(order.side, qty, price)["total"]
    else:
        fee = gross * slippage_bps / 10_000.0

    if order.side == "buy":
        # A-share quantity validation.
        if is_a_share:
            qty_err = validate_quantity(qty)
            if qty_err is not None:
                return qty_err
        total_cost = gross + fee
        if state.account.cash < total_cost:
            return f"insufficient cash: need {total_cost:.2f}, have {state.account.cash:.2f}"
    else:  # sell
        current_pos = state.positions.get(order.symbol)
        current_qty = current_pos.quantity if current_pos is not None else 0.0

        # A-share: T+1 check before allowing sell.
        if is_a_share and current_pos is not None and current_pos.buy_lots:
            buy_lots_objs = [
                BuyLot(trade_date=bl["trade_date"], quantity=bl["quantity"], price=bl["price"])
                for bl in current_pos.buy_lots
            ]
            ok, t_err = can_sell_today(buy_lots_objs, qty)
            if not ok:
                return t_err

        if current_qty + (-qty) < 0 and not effective_allow_short:
            return f"short selling not allowed: need {qty} shares, have {current_qty}"

    # Post-trade leverage check.  Recompute fee for the projected side;
    # for A-shares the fee depends on side (stamp tax on sells only).
    if is_a_share:
        projected_fee = calculate_fees(order.side, qty, price)["total"]
    else:
        projected_fee = fee  # already calculated above
    projected_cash = state.account.cash - (gross + projected_fee) if order.side == "buy" else state.account.cash + (gross - projected_fee)
    projected_position_qty = (
        (state.positions.get(order.symbol).quantity if state.positions.get(order.symbol) is not None else 0.0)
        + (qty if order.side == "buy" else -qty)
    )
    projected_exposure = abs(projected_position_qty) * price
    projected_equity = projected_cash + projected_exposure * (1 if projected_position_qty > 0 else -1)
    if projected_equity <= 0:
        return "projected equity is non-positive after trade"
    leverage = projected_exposure / projected_equity
    if leverage > max_leverage:
        return f"leverage {leverage:.2f}x exceeds max {max_leverage}x"

    return None


# ---------------------------------------------------------------------------
# Public API (thread-safe)
# ---------------------------------------------------------------------------

_lock = threading.RLock()


def load_or_initialize(
    account_id: str = "default",
    initial_cash: float = 1_000_000.0,
    currency: str = "USD",
    symbols: tuple[str, ...] = (),
) -> VirtualState:
    """Load state from disk or create a fresh one (thread-safe).

    Args:
        account_id: Account identifier.
        initial_cash: Starting cash for a new account.
        currency: Base currency.
        symbols: Configured universe (unused at load time).

    Returns:
        The current ``VirtualState``.
    """
    with _lock:
        return _load_state(account_id, initial_cash, currency)


def save(state: VirtualState) -> None:
    """Persist *state* to disk (thread-safe).

    Args:
        state: The state to persist.
    """
    with _lock:
        _save_state(state)


def submit_order(
    state: VirtualState,
    *,
    symbol: str,
    side: str,
    quantity: float,
    notional: float,
    order_type: str,
    limit_price: float | None,
    time_in_force: str,
    slippage_bps: float,
    fee_bps: float,
    allow_short: bool,
    max_leverage: float,
    universe: tuple[str, ...],
    price_source: str,
    market: str = "us_equity",
    allow_dynamic_symbols: bool = False,
) -> dict[str, Any]:
    """Submit an order and attempt immediate matching (thread-safe).

    Market orders are filled immediately subject to local limit checks.
    Limit orders are placed as ``open`` and matched lazily on subsequent
    read/write calls.

    Args:
        state: The simulation state (mutated in-place).
        symbol: Normalised symbol code.
        side: ``"buy"`` or ``"sell"``.
        quantity: Base-currency quantity (0 for notional-sized).
        notional: Quote-currency notional (0 for quantity-sized).
        order_type: ``"market"`` or ``"limit"``.
        limit_price: Limit price for limit orders.
        time_in_force: Time-in-force.
        slippage_bps: Slippage basis points.
        fee_bps: Commission basis points.
        allow_short: Whether short selling is allowed.
        max_leverage: Maximum gross leverage.
        universe: Allowed symbols.
        price_source: Price source selection.

    Returns:
        A result dict with ``status``, ``order_id``, ``order_status``,
        and optional ``execution``.
    """
    with _lock:
        sym = symbol.strip().upper()
        # Fetch current quote.
        try:
            q = market_quote(
                sym,
                universe=universe,
                price_source=price_source,
                cached_prices=state.prices,
                market=market,
                allow_dynamic_symbols=allow_dynamic_symbols,
            )
        except ValueError as exc:
            return {"status": "error", "error": str(exc)}

        order_id = _new_id("ord_")
        now = _now_iso()

        order = VirtualOrder(
            order_id=order_id,
            symbol=sym,
            side=side,
            quantity=quantity,
            notional=notional,
            order_type=order_type,
            limit_price=limit_price,
            time_in_force=time_in_force,
            status="open",
            created_at=now,
            updated_at=now,
        )

        if _should_fill(order, q):
            # Check local limits before filling.
            limit_error = _check_local_limits(
                state, order, q, allow_short, max_leverage, slippage_bps, market=market,
            )
            if limit_error is not None:
                order.status = "rejected"
                order.updated_at = _now_iso()
                state.orders[order_id] = order
                _save_state(state)
                return {
                    "status": "error",
                    "error": limit_error,
                    "order_id": order_id,
                    "order_status": "rejected",
                }

            # A-share: check price limit.  Uses the last cached price as a
            # best-effort previous close; skipped when no cached price is
            # available (fallback mode).
            if market == "a_share":
                exec_price = _execution_price(q, order.side, slippage_bps)
                prev_close = state.prices.get(sym)
                limit_price_err = check_price_limit(order.side, exec_price, prev_close, sym)
                if limit_price_err is not None:
                    order.status = "rejected"
                    order.updated_at = _now_iso()
                    state.orders[order_id] = order
                    _save_state(state)
                    return {
                        "status": "error",
                        "error": limit_price_err,
                        "order_id": order_id,
                        "order_status": "rejected",
                    }

            execution = _fill_order(state, order, q, slippage_bps, fee_bps, market=market)
            state.orders[order_id] = order
            _save_state(state)
            return {
                "status": "ok",
                "order_id": order_id,
                "order_status": "filled",
                "symbol": sym,
                "side": side,
                "filled_quantity": execution.quantity,
                "filled_price": execution.price,
                "fee": execution.fee,
                "slippage": execution.slippage,
                "execution_id": execution.execution_id,
            }
        else:
            # Limit order not yet fillable — persist as open.
            state.orders[order_id] = order
            _save_state(state)
            return {
                "status": "ok",
                "order_id": order_id,
                "order_status": "open",
                "symbol": sym,
                "side": side,
                "message": "limit order placed, awaiting fill",
            }


def cancel_order(
    state: VirtualState,
    order_id: str,
    *,
    symbol: str | None = None,
) -> dict[str, Any]:
    """Cancel an open order (thread-safe).

    Args:
        state: The simulation state (mutated in-place).
        order_id: The order to cancel.
        symbol: Optional symbol filter (unused in virtual).

    Returns:
        A result dict with ``status``, ``order_id``, ``order_status``.
    """
    with _lock:
        order = state.orders.get(order_id)
        if order is None:
            return {"status": "error", "error": f"order not found: {order_id}"}
        if order.status != "open":
            return {
                "status": "error",
                "error": f"order {order_id} is {order.status}, not open",
            }

        order.status = "cancelled"
        order.updated_at = _now_iso()
        _save_state(state)
        return {
            "status": "ok",
            "order_id": order_id,
            "order_status": "cancelled",
        }


def match_open_orders(
    state: VirtualState,
    *,
    slippage_bps: float,
    fee_bps: float,
    allow_short: bool,
    max_leverage: float,
    universe: tuple[str, ...],
    price_source: str,
    market: str = "us_equity",
    allow_dynamic_symbols: bool = False,
) -> list[dict[str, Any]]:
    """Match all open limit orders against current quotes (thread-safe).

    This is called lazily before every read/write operation so limit orders
    eventually fill when the simulated price moves.

    Args:
        state: The simulation state (mutated in-place).
        slippage_bps: Slippage basis points.
        fee_bps: Commission basis points.
        allow_short: Whether short selling is allowed.
        max_leverage: Maximum gross leverage.
        universe: Allowed symbols.
        price_source: Price source selection.

    Returns:
        A list of execution result dicts for newly-filled orders.
    """
    with _lock:
        fills: list[dict[str, Any]] = []
        open_orders = [o for o in state.orders.values() if o.status == "open"]

        if not open_orders:
            return fills

        # Fetch quotes for all symbols with open orders.
        symbols_needed = {o.symbol for o in open_orders}
        quotes: dict[str, Quote] = {}
        for sym in symbols_needed:
            try:
                q = market_quote(
                    sym,
                    universe=universe,
                    price_source=price_source,
                    cached_prices=state.prices,
                    market=market,
                    allow_dynamic_symbols=allow_dynamic_symbols,
                )
                quotes[sym] = q
                state.prices[sym] = q.last
            except ValueError:
                continue

        for order in open_orders:
            q = quotes.get(order.symbol)
            if q is None:
                continue
            if not _should_fill(order, q):
                continue

            limit_error = _check_local_limits(
                state, order, q, allow_short, max_leverage, slippage_bps, market=market,
            )
            if limit_error is not None:
                order.status = "rejected"
                order.updated_at = _now_iso()
                fills.append({
                    "order_id": order.order_id,
                    "status": "rejected",
                    "error": limit_error,
                })
                continue

            execution = _fill_order(state, order, q, slippage_bps, fee_bps, market=market)
            fills.append({
                "status": "filled",
                "order_id": order.order_id,
                "symbol": order.symbol,
                "side": order.side,
                "filled_quantity": execution.quantity,
                "filled_price": execution.price,
                "fee": execution.fee,
            })

        if fills:
            _save_state(state)

        return fills


def mark_to_market(
    state: VirtualState,
    *,
    universe: tuple[str, ...],
    price_source: str,
    market: str = "us_equity",
    allow_dynamic_symbols: bool = False,
) -> None:
    """Update all positions' market value and unrealised P&L (thread-safe).

    Args:
        state: The simulation state (mutated in-place).
        universe: Allowed symbols.
        price_source: Price source selection.
        market: The target market (``"us_equity"``, ``"a_share"``, ``"hk_equity"``).
        allow_dynamic_symbols: If ``True``, symbols matching *market* pass
            the universe check.
    """
    with _lock:
        for pos in list(state.positions.values()):
            try:
                q = market_quote(
                    pos.symbol,
                    universe=universe,
                    price_source=price_source,
                    cached_prices=state.prices,
                    market=market,
                    allow_dynamic_symbols=allow_dynamic_symbols,
                )
                state.prices[pos.symbol] = q.last
                pos.market_value = abs(pos.quantity) * q.last
                pos.unrealized_pnl = (q.last - pos.avg_price) * pos.quantity
            except ValueError:
                continue
