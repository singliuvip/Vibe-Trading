"""Runtime state provider for automation bounds checks.

Reads broker truth (positions, account, open orders) via the injected service
callables and computes the runtime state needed by check_automation_bounds.
All dependencies are injected for testability. Fail-closed: any read failure
produces a state that blocks trading.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Optional

logger = logging.getLogger(__name__)

#: Signatures of the injected broker READ callables.
ReadCallable = Callable[..., Mapping[str, Any]]


@dataclass(frozen=True)
class AutomationRuntimeState:
    """Snapshot of runtime state needed by check_automation_bounds.

    All fields are fail-closed: when a read fails, the state is marked
    unavailable and the caller must block trading.

    Attributes:
        available: Whether all reads succeeded. False → block all trading.
        daily_turnover_usd: Cumulative traded notional today (UTC).
        symbol_exposures: Mapping of symbol → current market value (USD).
        open_position_count: Number of distinct open positions.
        open_order_count: Number of resting open orders.
        daily_realized_loss_usd: Realized loss today (positive number).
        peak_equity_usd: Peak portfolio equity (for drawdown calc).
        current_equity_usd: Current portfolio equity.
        long_symbols: Set of symbols with existing long positions.
        error: Error message when available=False.
    """

    available: bool
    daily_turnover_usd: float = 0.0
    symbol_exposures: Mapping[str, float] = field(default_factory=dict)
    open_position_count: int = 0
    open_order_count: int = 0
    daily_realized_loss_usd: float = 0.0
    peak_equity_usd: float = 0.0
    current_equity_usd: float = 0.0
    long_symbols: frozenset[str] = field(default_factory=frozenset)
    error: str = ""

    @property
    def drawdown_pct(self) -> float:
        """Current drawdown fraction from peak equity (0 when no peak)."""
        if self.peak_equity_usd <= 0:
            return 0.0
        return max(0.0, (self.peak_equity_usd - self.current_equity_usd) / self.peak_equity_usd)

    def symbol_exposure(self, symbol: str) -> float:
        """Return the current market value for a symbol (0 if not held)."""
        return self.symbol_exposures.get(symbol, 0.0)

    def has_long(self, symbol: str) -> bool:
        """Return whether there is an existing long position in symbol."""
        return symbol in self.long_symbols


def read_runtime_state(
    *,
    read_positions: ReadCallable,
    read_account: ReadCallable,
    read_open_orders: ReadCallable,
    daily_turnover_usd: float = 0.0,
    daily_realized_loss_usd: float = 0.0,
    peak_equity_usd: float = 0.0,
) -> AutomationRuntimeState:
    """Read broker truth and compute the automation runtime state.

    Fail-closed: any read failure returns an unavailable state.

    Args:
        read_positions: Callable returning positions dict (service.get_positions).
        read_account: Callable returning account dict (service.get_account).
        read_open_orders: Callable returning open orders dict (service.get_open_orders).
        daily_turnover_usd: Cumulative traded notional today (from caller's
            persistent counter; 0 if unknown).
        daily_realized_loss_usd: Realized loss today (from caller; 0 if unknown).
        peak_equity_usd: Peak equity (from caller's persistent high-water mark).

    Returns:
        An AutomationRuntimeState snapshot.
    """
    try:
        positions_raw = read_positions()
        account_raw = read_account()
        orders_raw = read_open_orders()
    except Exception as exc:  # noqa: BLE001 - fail-closed
        logger.warning("automation runtime state read failed: %s", exc)
        return AutomationRuntimeState(available=False, error=str(exc))

    try:
        return _parse_runtime_state(
            positions_raw, account_raw, orders_raw,
            daily_turnover_usd=daily_turnover_usd,
            daily_realized_loss_usd=daily_realized_loss_usd,
            peak_equity_usd=peak_equity_usd,
        )
    except Exception as exc:  # noqa: BLE001 - fail-closed on parse error
        logger.warning("automation runtime state parse failed: %s", exc)
        return AutomationRuntimeState(available=False, error=str(exc))


def _parse_runtime_state(
    positions_raw: Mapping[str, Any],
    account_raw: Mapping[str, Any],
    orders_raw: Mapping[str, Any],
    *,
    daily_turnover_usd: float,
    daily_realized_loss_usd: float,
    peak_equity_usd: float,
) -> AutomationRuntimeState:
    """Parse raw broker responses into a runtime state (defensive).

    The exact response shapes vary by connector; this parses defensively,
    treating missing/ambiguous fields as zero/empty rather than failing —
    EXCEPT when the top-level structure is entirely unrecognized, which
    raises and triggers the fail-closed path in read_runtime_state.
    """
    # Positions: expect {"positions": [...]} or {"data": [...]} or a list.
    positions = _extract_list(positions_raw, ("positions", "data"))
    symbol_exposures: dict[str, float] = {}
    long_symbols: set[str] = set()
    for pos in positions:
        if not isinstance(pos, dict):
            continue
        symbol = str(pos.get("symbol", "")).strip().upper()
        if not symbol:
            continue
        # Market value: prefer "market_value", fall back to quantity * price.
        mv = _safe_float(pos.get("market_value"))
        if mv is None:
            qty = _safe_float(pos.get("quantity")) or _safe_float(pos.get("qty")) or 0.0
            price = _safe_float(pos.get("price")) or _safe_float(pos.get("last_price")) or 0.0
            mv = abs(qty * price)
        symbol_exposures[symbol] = symbol_exposures.get(symbol, 0.0) + mv
        # Long detection: positive quantity or side == "long".
        qty = _safe_float(pos.get("quantity")) or _safe_float(pos.get("qty"))
        side = str(pos.get("side", "")).lower()
        if (qty is not None and qty > 0) or side == "long":
            long_symbols.add(symbol)

    # Open orders count.
    orders = _extract_list(orders_raw, ("orders", "data"))
    open_order_count = len(orders)

    # Account equity.
    current_equity = (
        _safe_float(account_raw.get("equity"))
        or _safe_float(account_raw.get("total_equity"))
        or _safe_float(account_raw.get("net_liquidation"))
        or 0.0
    )

    return AutomationRuntimeState(
        available=True,
        daily_turnover_usd=daily_turnover_usd,
        symbol_exposures=symbol_exposures,
        open_position_count=len(symbol_exposures),
        open_order_count=open_order_count,
        daily_realized_loss_usd=daily_realized_loss_usd,
        peak_equity_usd=peak_equity_usd,
        current_equity_usd=current_equity,
        long_symbols=frozenset(long_symbols),
    )


def _extract_list(raw: Mapping[str, Any], keys: tuple[str, ...]) -> list:
    """Extract a list from a response dict by trying known keys, else []."""
    if isinstance(raw, list):
        return raw
    for key in keys:
        value = raw.get(key)
        if isinstance(value, list):
            return value
    return []


def _safe_float(value: Any) -> float | None:
    """Coerce a value to float, returning None on failure."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
