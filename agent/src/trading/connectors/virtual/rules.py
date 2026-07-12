"""A-share market simulation rules for the virtual broker connector.

Provides market-specific validation, rounding, fee calculation, and
trading-day tracking used by the order book when ``ruleset="china_a"``.

Each function accepts a ``market_info`` dict (or keyword arguments) so
callers can pass per-call context without coupling to a configuration
object.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Commission: 0.025% of turnover, minimum ¥5
COMMISSION_RATE = 0.00025
MIN_COMMISSION = 5.0

# Stamp tax: 0.05% on sell only (collected since 2023-08-28 reduction)
STAMP_TAX_RATE = 0.0005

# Transfer fee (过户费): 0.001% of turnover, both sides
TRANSFER_FEE_RATE = 0.00001

# Minimum trading unit (shares)
SHARE_ROUND_SIZE = 100

# Price limit brackets by board
PRICE_LIMITS: dict[str, float] = {
    "main_board": 0.10,    # 000xxx.SZ / 600xxx.SH / 601xxx.SH
    "gem": 0.20,           # 300xxx.SZ
    "star": 0.20,          # 688xxx.SH
    "bj": 0.30,            # 8xxxxx.BJ
    "st": 0.05,            # ST stocks (heuristic)
}


# ---------------------------------------------------------------------------
# Board detection
# ---------------------------------------------------------------------------

def infer_board(symbol: str) -> str:
    """Infer the A-share board/price-limit bracket from a symbol code.

    Args:
        symbol: Normalised symbol (e.g. ``"000001.SZ"``, ``"600519.SH"``).

    Returns:
        One of ``"main_board"``, ``"gem"``, ``"star"``, ``"bj"``, or
        ``"main_board"`` as fallback.
    """
    sym = symbol.strip().upper()
    code = sym.split(".")[0] if "." in sym else sym
    exchange = sym.split(".")[-1] if "." in sym else ""

    # 创业板 (ChiNext / Gem) — 300xxx
    if exchange == "SZ" and code.startswith("30"):
        return "gem"
    # 科创板 (STAR Market) — 688xxx
    if exchange == "SH" and code.startswith("688"):
        return "star"
    # 北交所 (Beijing Stock Exchange) — 8xxxxx with BJ suffix
    if exchange == "BJ" or (code.startswith("8") and exchange == "SZ"):
        return "bj"
    # 主板 (Main Board) — everything else
    return "main_board"


def get_price_limit(symbol: str) -> float:
    """Return the daily price limit fraction for *symbol*.

    Args:
        symbol: Normalised symbol.

    Returns:
        The limit as a fraction (e.g. 0.10 for ±10%).
    """
    return PRICE_LIMITS.get(infer_board(symbol), 0.10)


# ---------------------------------------------------------------------------
# Quantity rules
# ---------------------------------------------------------------------------

def round_quantity(
    quantity: float,
    round_size: int = SHARE_ROUND_SIZE,
) -> float:
    """Round *quantity* down to the nearest multiple of *round_size*.

    Args:
        quantity: Requested quantity.
        round_size: Round lot size (default 100 for A-shares).

    Returns:
        Rounded quantity. Returns 0 if quantity is less than one round lot.
    """
    lots = int(quantity // round_size)
    return float(lots * round_size)


def validate_quantity(quantity: float) -> str | None:
    """Validate A-share quantity rules.

    Returns ``None`` if valid, or an error string if rejected.

    Rules:
    - Minimum one round lot (100 shares).
    - Must be a multiple of 100 shares.
    """
    if quantity <= 0:
        return "quantity must be positive"
    if quantity < SHARE_ROUND_SIZE:
        return f"A-share quantity must be at least {SHARE_ROUND_SIZE} shares"
    if int(quantity) % SHARE_ROUND_SIZE != 0:
        return f"A-share quantity must be a multiple of {SHARE_ROUND_SIZE}"
    return None


# ---------------------------------------------------------------------------
# Fee calculation
# ---------------------------------------------------------------------------

def calculate_fees(
    side: str,
    quantity: float,
    price: float,
) -> dict[str, float]:
    """Calculate A-share transaction fees.

    Args:
        side: ``"buy"`` or ``"sell"``.
        quantity: Number of shares.
        price: Execution price per share.

    Returns:
        A dict with ``commission``, ``stamp_tax``, ``transfer_fee``, and
        ``total``.
    """
    gross = quantity * price
    commission = max(gross * COMMISSION_RATE, MIN_COMMISSION)
    stamp_tax = gross * STAMP_TAX_RATE if side == "sell" else 0.0
    transfer_fee = gross * TRANSFER_FEE_RATE
    total = round(commission + stamp_tax + transfer_fee, 4)
    return {
        "commission": round(commission, 4),
        "stamp_tax": round(stamp_tax, 4),
        "transfer_fee": round(transfer_fee, 4),
        "total": total,
    }


# ---------------------------------------------------------------------------
# T+1 tracking
# ---------------------------------------------------------------------------

@dataclass
class BuyLot:
    """A buy lot record for T+1 tracking.

    Attributes:
        trade_date: The trading day (ISO date string).
        quantity: Number of shares bought.
        price: Average entry price for this lot.
    """
    trade_date: str
    quantity: float
    price: float


def today_str() -> str:
    """Return today's date as ISO string for T+1 comparisons."""
    return date.today().isoformat()


def can_sell_today(
    buy_lots: list[BuyLot],
    sell_quantity: float,
    trade_date: str | None = None,
) -> tuple[bool, str | None]:
    """Check if *sell_quantity* shares can be sold today under T+1 rules.

    Args:
        buy_lots: List of open buy lots for the symbol.
        sell_quantity: Number of shares to sell.
        trade_date: Current trading day (ISO date). Defaults to today.

    Returns:
        ``(True, None)`` if the sale is valid, or ``(False, error_msg)``
        if T+1 would be violated.
    """
    if not buy_lots:
        return False, "no buy lots available for this symbol"

    today = trade_date or today_str()
    # Sum shares that were bought on or before yesterday (T+1 eligible).
    eligible_qty = sum(
        lot.quantity for lot in buy_lots if lot.trade_date < today
    )
    if sell_quantity > eligible_qty:
        return False, (
            f"T+1 restriction: can only sell {eligible_qty:.0f} shares "
            f"today (shares bought today are not eligible). "
            f"Requested: {sell_quantity:.0f}"
        )
    return True, None


def consume_buy_lots(
    buy_lots: list[BuyLot],
    sell_quantity: float,
) -> list[BuyLot]:
    """Consume *sell_quantity* shares from *buy_lots* in FIFO order.

    Returns the updated list of remaining buy lots (may be empty or shorter).
    Fully consumed lots are removed; partially consumed lots have their
    quantity reduced.
    """
    remaining = sell_quantity
    new_lots: list[BuyLot] = []
    for lot in buy_lots:
        if remaining <= 0:
            new_lots.append(lot)
            continue
        if lot.quantity <= remaining:
            remaining -= lot.quantity
        else:
            new_lots.append(BuyLot(
                trade_date=lot.trade_date,
                quantity=lot.quantity - remaining,
                price=lot.price,
            ))
            remaining = 0
    return new_lots


# ---------------------------------------------------------------------------
# Price limit checks
# ---------------------------------------------------------------------------

def check_price_limit(
    side: str,
    price: float,
    previous_close: float | None,
    symbol: str = "",
) -> str | None:
    """Check if *price* violates the daily price limit.

    Args:
        side: ``"buy"`` or ``"sell"``.
        price: Intended execution price.
        previous_close: Previous trading day's close price. If ``None``,
            the check is skipped (fallback mode).
        symbol: Symbol for board detection (used when *previous_close* is
            available).

    Returns:
        ``None`` if the price is within limits, or an error string if
        the limit would be breached.
    """
    if previous_close is None or previous_close <= 0:
        return None  # No data to check against — skip.

    limit_frac = get_price_limit(symbol)
    if side == "buy":
        max_price = previous_close * (1.0 + limit_frac)
        if price > max_price:
            return (
                f"buy price {price:.2f} exceeds upper limit "
                f"{max_price:.2f} (+{limit_frac*100:.0f}%) for {symbol}"
            )
    else:  # sell
        min_price = previous_close * (1.0 - limit_frac)
        if price < min_price:
            return (
                f"sell price {price:.2f} below lower limit "
                f"{min_price:.2f} ({-limit_frac*100:.0f}%) for {symbol}"
            )
    return None
