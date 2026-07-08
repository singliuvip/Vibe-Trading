"""A-share (China equity) order validation rules.

This module enforces A-share-specific trading constraints that are not
covered by the generic mandate gate:

  - Lot size: quantity must be a multiple of 100 shares
  - Price precision: minimum tick is 0.01 CNY
  - Price limits: ±10% (main board), ±20% (STAR/ChiNext), ±30% (BJ)
  - T+1 settlement: shares bought today cannot be sold today
  - Symbol format: XXXXXX.SH / .SZ / .BJ
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Symbol format pattern: 6 digits + .SH/.SZ/.BJ
_SYMBOL_PATTERN = re.compile(r"^\d{6}\.(SH|SZ|BJ)$")

# Board-specific price limit ratios
_PRICE_LIMITS: dict[str, float] = {
    "main": 0.10,       # 主板 600xxx.SH / 000xxx.SZ / 002xxx.SZ
    "star": 0.20,       # 科创板 688xxx.SH
    "chinext": 0.20,    # 创业板 300xxx.SZ / 301xxx.SZ
    "bj": 0.30,         # 北交所 8xxxxx.BJ
}


@dataclass
class AStockOrderValidation:
    """Inputs needed to validate an A-share order."""

    symbol: str
    side: str               # "buy" or "sell"
    quantity: int
    limit_price: float = 0.0
    pre_close: float = 0.0
    upper_limit: float = 0.0
    lower_limit: float = 0.0
    violations: list[str] = field(default_factory=list)


# ------------------------------------------------------------------ #
# Validation
# ------------------------------------------------------------------ #


def validate_a_stock_order(order: AStockOrderValidation) -> list[str]:
    """Validate A-share trading rules.

    Returns:
        List of violation messages.  Empty list == no violations.
    """
    violations: list[str] = []

    # 1. Symbol format: XXXXXX.SH / .SZ / .BJ
    if not _SYMBOL_PATTERN.match(order.symbol):
        violations.append(
            f"Invalid symbol format: {order.symbol} "
            f"(expected 6-digit code with .SH/.SZ/.BJ suffix)"
        )
        return violations  # cannot proceed with other checks without valid symbol

    # 2. Lot size: must be multiple of 100
    if order.quantity % 100 != 0:
        violations.append(
            f"Quantity {order.quantity} must be a multiple of 100 "
            f"(A-share minimum lot = 100 shares)"
        )

    # 3. Price precision: minimum tick 0.01 CNY
    if order.limit_price > 0 and round(order.limit_price, 2) != order.limit_price:
        violations.append(
            f"Price {order.limit_price} exceeds A-share tick precision (0.01 CNY)"
        )

    # 4. Price limits (for limit orders)
    if order.limit_price > 0 and order.upper_limit > 0 and order.limit_price > order.upper_limit:
        violations.append(
            f"Price {order.limit_price:.2f} exceeds upper limit {order.upper_limit:.2f}"
        )

    if order.limit_price > 0 and order.lower_limit > 0 and order.limit_price < order.lower_limit:
        violations.append(
            f"Price {order.limit_price:.2f} below lower limit {order.lower_limit:.2f}"
        )

    return violations


# ------------------------------------------------------------------ #
# Price limit helpers
# ------------------------------------------------------------------ #


def get_price_limit_ratio(symbol: str) -> float:
    """Return the price-limit ratio for a stock based on its board.

    - 688xxx.SH → 0.20 (STAR / 科创板)
    - 300xxx.SZ, 301xxx.SZ → 0.20 (ChiNext / 创业板)
    - 8xxxxx.BJ → 0.30 (Beijing Stock Exchange / 北交所)
    - All others → 0.10 (Main board / 主板)

    Note: ST stocks (±5%) are NOT detected here because there is no
    reliable way to identify them from the symbol alone. Users trading
    ST stocks should set their own risk controls via the mandate.
    """
    if symbol.endswith(".BJ"):
        return _PRICE_LIMITS["bj"]
    if symbol.startswith("688"):
        return _PRICE_LIMITS["star"]
    if symbol.startswith("30") and symbol.endswith(".SZ"):
        return _PRICE_LIMITS["chinext"]
    return _PRICE_LIMITS["main"]


def compute_price_limits(prev_close: float, limit_ratio: float) -> tuple[float, float]:
    """Compute upper and lower price limits from previous close and ratio."""
    if prev_close <= 0:
        return 0.0, 0.0
    lower = round(prev_close * (1.0 - limit_ratio), 2)
    upper = round(prev_close * (1.0 + limit_ratio), 2)
    return lower, upper


# ------------------------------------------------------------------ #
# Gate integration helper
# ------------------------------------------------------------------ #


def build_validation_from_quote(
    symbol: str,
    side: str,
    quantity: int,
    limit_price: float,
    quote: dict,
) -> AStockOrderValidation:
    """Build an ``AStockOrderValidation`` from a connector quote envelope.

    ``quote`` is the dict returned by ``connector.get_quote(symbol, ...)``.
    It should contain ``pre_close``, ``upper_limit``, ``lower_limit``.
    """
    pre_close = float(quote.get("pre_close", 0) or 0)
    upper = float(quote.get("upper_limit", 0) or 0)
    lower = float(quote.get("lower_limit", 0) or 0)

    # Fallback: compute from ratio if limits not in quote
    if upper <= 0 or lower <= 0:
        ratio = get_price_limit_ratio(symbol)
        lower, upper = compute_price_limits(pre_close, ratio)

    return AStockOrderValidation(
        symbol=symbol,
        side=side,
        quantity=quantity,
        limit_price=limit_price,
        pre_close=pre_close,
        upper_limit=upper,
        lower_limit=lower,
    )


# ------------------------------------------------------------------ #
# T+0 / T+1 settlement
# ------------------------------------------------------------------ #

# T+0 eligible instrument prefixes (ETF, 可转债等)
_T0_PREFIXES: tuple[str, ...] = (
    "51",   # 510xxx.SH ETF (上证)
    "159",  # 159xxx.SZ ETF (深证)
    "512",  # 512xxx.SH 行业ETF
    "513",  # 513xxx.SH 跨境ETF
    "515",  # 515xxx.SH 主题ETF
    "518",  # 518xxx.SH 黄金ETF
    "52",   # 52xxxx.SH ETF / 可转债
    "11",   # 11xxxx.SZ 可转债
    "12",   # 12xxxx.SZ 可转债
)


def is_t0_eligible(symbol: str) -> bool:
    """Check if a symbol is T+0 eligible (ETF, 可转债).

    A-share stocks are T+1 (cannot sell shares bought today).
    ETFs and 可转债 support T+0 settlement.
    """
    code = symbol.split(".")[0] if "." in symbol else symbol
    return any(code.startswith(prefix) for prefix in _T0_PREFIXES)


def check_t_plus_1(
    symbol: str,
    side: str,
    today_buys: list[dict],
) -> str | None:
    """Check T+1 settlement rule for A-share sell orders.

    Args:
        symbol: Stock code in XXXXXX.SH/.SZ/.BJ format.
        side: ``buy`` or ``sell``.
        today_buys: List of today's buy execution dicts, each with
            at least ``symbol`` and ``side`` keys.

    Returns:
        Violation description string if the order violates T+1,
        ``None`` if the order is allowed.
    """
    if side != "sell":
        return None

    if is_t0_eligible(symbol):
        return None

    for exec_ in today_buys:
        if exec_.get("symbol") == symbol and exec_.get("side") == "buy":
            return (
                f"T+1 violation: {symbol} was bought today, "
                f"cannot sell until the next trading day"
            )

    return None
