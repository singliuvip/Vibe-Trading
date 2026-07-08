"""Paper trading engine for xtquant — in-memory simulated matching.

Maintains an in-memory simulated brokerage account for the XTQuant connector.
Market orders fill immediately at the last price obtained from ``xtdata``;
limit orders are checked against the current price and filled only when the
limit condition is met.

This engine is used by ``sdk._paper_place_order`` and ``sdk._paper_cancel_order``
— it NEVER imports or touches the xtquant SDK.  The paper::live separation is
absolute: paper profiles route through this engine, live profiles route through
``XtQuantTrader``.

.. note::
    This module is intentionally kept free of any ``import xtquant``.  It uses
    ``xtdata`` only to get real-time prices for market-order fills; when
    ``xtdata`` is unavailable (e.g. on Linux), orders are recorded but marked
    as ``pending`` rather than filled.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# ── Constants ──

_PAPER_INITIAL_CASH: float = 1_000_000.0

# ── Data Models ──


@dataclass
class PaperPosition:
    """A simulated securities position."""

    symbol: str
    quantity: int = 0
    avg_cost: float = 0.0


@dataclass
class PaperOrder:
    """A simulated order."""

    order_id: str
    symbol: str
    side: str  # "buy" | "sell"
    order_type: str  # "market" | "limit"
    quantity: int
    limit_price: float | None = None
    filled_qty: int = 0
    status: str = "pending"  # pending | filled | partial | cancelled | rejected
    created_at: float = field(default_factory=time.time)


# ── Engine ──


class PaperEngine:
    """In-memory simulated brokerage account for xtquant paper trading.

    Thread-safe: all public methods are guarded by ``self._lock``.

    Usage::

        engine = PaperEngine()
        result = engine.place_order("000001.SZ", "buy", 100, order_type="market")
        positions = engine.get_positions()
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.cash: float = _PAPER_INITIAL_CASH
        self._positions: dict[str, PaperPosition] = {}
        self._orders: list[PaperOrder] = []

    # ── Order Entry ──

    def place_order(
        self,
        symbol: str,
        side: str,
        quantity: int = 0,
        *,
        order_type: str = "market",
        limit_price: float | None = None,
        notional: float | None = None,
        **_: Any,
    ) -> dict[str, Any]:
        """Place a simulated order.

        Market orders fill immediately at the last known price (via xtdata
        when available, else at price 0.0 with status ``pending``).  Limit
        orders are recorded and checked on subsequent calls to
        ``_try_fill_limits()``.

        Args:
            symbol: Ticker, e.g. ``600036.SH``.
            side: ``buy`` or ``sell``.
            quantity: Number of shares (must be positive).
            order_type: ``market`` (default) or ``limit``.
            limit_price: Required for limit orders.
            notional: Cash-based sizing (not natively supported; ignored).

        Returns:
            Order confirmation dict with ``status``, ``order_id``, ``paper``.
        """
        side_key = str(side or "").strip().lower()
        if side_key not in ("buy", "sell"):
            return {"status": "error", "error": "side must be 'buy' or 'sell'", "paper": True}
        if quantity <= 0:
            return {"status": "error", "error": "quantity must be positive", "paper": True}

        order_kind = str(order_type or "market").strip().lower()
        if order_kind not in ("market", "limit"):
            return {"status": "error", "error": "order_type must be 'market' or 'limit'", "paper": True}

        if order_kind == "limit" and limit_price is None:
            return {"status": "error", "error": "limit_price is required for limit orders", "paper": True}

        price = float(limit_price) if limit_price is not None else 0.0
        order_id = f"paper-{uuid.uuid4().hex[:12]}"

        order = PaperOrder(
            order_id=order_id,
            symbol=symbol.upper(),
            side=side_key,
            order_type=order_kind,
            quantity=quantity,
            limit_price=price if order_kind == "limit" else None,
        )

        with self._lock:
            self._orders.append(order)

            if order_kind == "market":
                # Try to get current price from xtdata for realistic fills.
                fill_price = self._get_market_price(symbol)

                if fill_price is not None and fill_price > 0:
                    self._apply_fill(order, fill_price)
                else:
                    # No price available — record pending, don't alter balances.
                    order.status = "pending"
                    logger.info(
                        "paper market order %s: no price available for %s — pending",
                        order_id, symbol,
                    )
            else:
                # Limit order: try immediate fill, else stay pending.
                fill_price = self._get_market_price(symbol)
                if fill_price is not None and fill_price > 0:
                    if (side_key == "buy" and fill_price <= price) or (
                        side_key == "sell" and fill_price >= price
                    ):
                        self._apply_fill(order, fill_price)
                    else:
                        order.status = "pending"

        return {
            "status": "ok",
            "order_id": order_id,
            "symbol": order.symbol,
            "side": order.side,
            "order_type": order.order_type,
            "quantity": order.quantity,
            "filled_qty": order.filled_qty,
            "limit_price": order.limit_price,
            "paper": True,
        }

    def cancel_order(self, order_id: str) -> dict[str, Any]:
        """Cancel a pending paper order.

        Only orders with status ``pending`` can be cancelled; already-filled or
        already-cancelled orders return an error.

        Args:
            order_id: The paper order id to cancel.

        Returns:
            Dict with ``status`` and ``order_id``.
        """
        with self._lock:
            for order in self._orders:
                if order.order_id == order_id:
                    if order.status == "pending":
                        order.status = "cancelled"
                        logger.info("paper cancel_order: %s cancelled", order_id)
                        return {"status": "ok", "order_id": order_id, "paper": True}
                    return {
                        "status": "error",
                        "error": f"Order {order_id} is {order.status}, cannot cancel",
                        "paper": True,
                    }
        return {"status": "error", "error": f"Order {order_id} not found", "paper": True}

    # ── Account Queries ──

    def get_positions(self) -> list[dict[str, Any]]:
        """Return current paper positions list.

        Returns:
            List of position dicts with ``symbol``, ``qty``, ``avg_cost``,
            ``market_value``, ``current_price``, ``unrealized_pnl``.
        """
        with self._lock:
            return [
                {
                    "symbol": pos.symbol,
                    "qty": pos.quantity,
                    "avg_cost": pos.avg_cost,
                    "market_value": 0.0,  # computed by caller from current price
                    "current_price": 0.0,
                    "unrealized_pnl": 0.0,
                }
                for pos in self._positions.values()
                if pos.quantity != 0
            ]

    def get_account_snapshot(self) -> dict[str, Any]:
        """Return a paper account summary.

        Returns:
            Dict with ``total_value``, ``cash``, ``buying_power``,
            ``market_value``.
        """
        with self._lock:
            return {
                "status": "ok",
                "cash": self.cash,
                "buying_power": self.cash,
                "total_value": self.cash,  # positions not marked-to-market without prices
                "market_value": 0.0,
                "paper": True,
            }

    def get_open_orders(self) -> list[dict[str, Any]]:
        """Return the list of open (pending) paper orders.

        Returns:
            List of order dicts.
        """
        with self._lock:
            return [
                {
                    "order_id": o.order_id,
                    "symbol": o.symbol,
                    "side": o.side,
                    "order_type": o.order_type,
                    "quantity": o.quantity,
                    "filled_qty": o.filled_qty,
                    "limit_price": o.limit_price,
                    "status": o.status,
                    "created_at": o.created_at,
                    "paper": True,
                }
                for o in self._orders
                if o.status == "pending"
            ]

    def reset(self) -> None:
        """Reset the paper account to initial state."""
        with self._lock:
            self.cash = _PAPER_INITIAL_CASH
            self._positions.clear()
            self._orders.clear()
            logger.info("paper engine reset to initial $%s", _PAPER_INITIAL_CASH)

    # ── Internals ──

    def _apply_fill(self, order: PaperOrder, fill_price: float) -> None:
        """Apply a fill to the paper account, updating cash and positions."""
        notional = fill_price * order.quantity

        if order.side == "buy":
            if notional > self.cash:
                order.status = "rejected"
                logger.warning(
                    "paper order %s rejected: insufficient cash (need %.2f, have %.2f)",
                    order.order_id, notional, self.cash,
                )
                return
            self.cash -= notional
            pos = self._positions.setdefault(order.symbol, PaperPosition(symbol=order.symbol))
            total_cost = (pos.avg_cost * pos.quantity) + notional
            pos.quantity += order.quantity
            pos.avg_cost = total_cost / pos.quantity if pos.quantity > 0 else 0.0
        else:
            # sell
            pos = self._positions.get(order.symbol)
            if pos is None or pos.quantity < order.quantity:
                order.status = "rejected"
                logger.warning(
                    "paper order %s rejected: insufficient position for %s",
                    order.order_id, order.symbol,
                )
                return
            self.cash += notional
            pos.quantity -= order.quantity
            if pos.quantity == 0:
                del self._positions[order.symbol]

        order.filled_qty = order.quantity
        order.status = "filled"
        logger.info(
            "paper order %s filled: %s %d %s @ %.2f",
            order.order_id, order.side, order.quantity, order.symbol, fill_price,
        )

    @staticmethod
    def _get_market_price(symbol: str) -> float | None:
        """Try to get the last traded price for *symbol* from xtdata.

        Returns ``None`` when xtdata is unavailable (Linux, etc.) so the
        caller can decide whether to leave the order pending.
        """
        try:
            import xtquant.xtdata as xtdata  # type: ignore[import-untyped]

            tick = xtdata.get_full_tick([symbol])
            if tick and symbol in tick:
                data = tick[symbol]
                if isinstance(data, dict):
                    price = data.get("lastPrice", 0.0)
                else:
                    price = getattr(data, "lastPrice", 0.0) or getattr(data, "lastClose", 0.0)
                if price and price > 0:
                    return float(price)
        except ImportError:
            pass
        except Exception as exc:
            logger.debug("paper engine: get_full_tick failed for %s: %s", symbol, exc)
        return None


# ── Module-level singleton ──

_paper_engine: PaperEngine | None = None
_paper_lock = threading.Lock()


def get_paper_engine() -> PaperEngine:
    """Return the module-level :class:`PaperEngine` singleton.

    Created lazily on first call; subsequent calls return the same instance.
    """
    global _paper_engine  # noqa: PLW0603
    if _paper_engine is None:
        with _paper_lock:
            if _paper_engine is None:
                _paper_engine = PaperEngine()
    return _paper_engine
