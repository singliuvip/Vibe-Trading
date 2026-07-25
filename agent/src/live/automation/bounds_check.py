"""Automation-specific mandate bound checks (pure, fail-closed).

These checks layer ON TOP of the shared ``check_mandate`` gate. They are only
consulted by the automated execution pipeline — manual/agent-driven trading
never reaches this module. A mandate without automation_bounds (v1 shape)
cannot enable live automation; the automation executor checks that separately.

Every check is fail-closed and pure: all state (current turnover, positions,
drawdown) is passed in as arguments. No clock, no storage, no broker access.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.live.automation.models import SignalCandidate, TradePlan
from src.live.mandate.model import AutomationBounds


@dataclass(frozen=True)
class BoundsVerdict:
    """Result of an automation-bounds check.

    Attributes:
        allowed: Whether the order may proceed.
        limit: Which limit tripped (empty when allowed).
        reason: Human-readable explanation.
    """

    allowed: bool
    limit: str = ""
    reason: str = ""


def check_automation_bounds(
    bounds: AutomationBounds,
    plan: TradePlan,
    *,
    daily_turnover_usd: float,
    order_notional_usd: float,
    symbol_exposure_usd: float,
    open_position_count: int,
    open_order_count: int,
    daily_realized_loss_usd: float,
    drawdown_pct: float,
    has_existing_long: bool = False,
) -> BoundsVerdict:
    """Evaluate a TradePlan against the mandate's automation bounds (fail-closed).

    Args:
        bounds: The mandate's automation_bounds (must be non-None; caller checks).
        plan: The trade plan about to be submitted.
        daily_turnover_usd: Cumulative traded notional so far today (UTC).
        order_notional_usd: Resolved notional of this order (USD).
        symbol_exposure_usd: Current post-trade market value in plan.symbol.
        open_position_count: Current number of distinct open positions.
        open_order_count: Current number of resting open orders.
        daily_realized_loss_usd: Realized loss so far today (positive number).
        drawdown_pct: Current portfolio drawdown fraction from peak (>= 0).
        has_existing_long: Whether the account currently holds a long position
            in plan.symbol. Used by the shorting gate: when allow_short is
            False, a sell is only permitted if it closes an existing long.

    Returns:
        A BoundsVerdict; allowed=False names the first tripped limit.
    """
    # Order-type whitelist (empty == deny all automation orders, fail-closed).
    if not bounds.allowed_order_types:
        return BoundsVerdict(False, "allowed_order_types", "no automation order types permitted")
    if plan.order_type not in bounds.allowed_order_types:
        return BoundsVerdict(
            False, "allowed_order_types",
            f"order_type {plan.order_type!r} not in automation whitelist",
        )

    # Shorting gate.
    if plan.side == "sell" and not bounds.allow_short and not has_existing_long:
        return BoundsVerdict(
            False, "allow_short",
            "shorting disabled and no existing long position to close",
        )

    # Stop-loss requirement.
    if bounds.require_stop_loss and plan.stop_loss is None:
        return BoundsVerdict(False, "require_stop_loss", "automation order missing stop_loss")

    # Daily turnover cap.
    if bounds.max_daily_turnover_usd is not None:
        if daily_turnover_usd + order_notional_usd > bounds.max_daily_turnover_usd:
            return BoundsVerdict(
                False, "max_daily_turnover_usd",
                f"daily turnover {daily_turnover_usd + order_notional_usd:.2f} > cap {bounds.max_daily_turnover_usd:.2f}",
            )

    # Per-symbol exposure cap.
    if bounds.max_symbol_exposure_usd is not None:
        if symbol_exposure_usd > bounds.max_symbol_exposure_usd:
            return BoundsVerdict(
                False, "max_symbol_exposure_usd",
                f"symbol exposure {symbol_exposure_usd:.2f} > cap {bounds.max_symbol_exposure_usd:.2f}",
            )

    # Max positions (only a buy adds a position; executor passes post-trade count).
    if bounds.max_positions is not None and open_position_count > bounds.max_positions:
        return BoundsVerdict(
            False, "max_positions",
            f"open positions {open_position_count} > cap {bounds.max_positions}",
        )

    # Max open orders.
    if bounds.max_open_orders is not None and open_order_count >= bounds.max_open_orders:
        return BoundsVerdict(
            False, "max_open_orders",
            f"open orders {open_order_count} >= cap {bounds.max_open_orders}",
        )

    # Daily realized loss cap.
    if bounds.max_daily_realized_loss_usd is not None:
        if daily_realized_loss_usd > bounds.max_daily_realized_loss_usd:
            return BoundsVerdict(
                False, "max_daily_realized_loss_usd",
                f"daily realized loss {daily_realized_loss_usd:.2f} > cap {bounds.max_daily_realized_loss_usd:.2f}",
            )

    # Drawdown cap.
    if bounds.max_drawdown_pct is not None:
        if drawdown_pct > bounds.max_drawdown_pct:
            return BoundsVerdict(
                False, "max_drawdown_pct",
                f"drawdown {drawdown_pct:.4f} > cap {bounds.max_drawdown_pct:.4f}",
            )

    return BoundsVerdict(True)


def check_slippage(
    bounds: AutomationBounds,
    plan: TradePlan,
    signal: SignalCandidate,
) -> BoundsVerdict:
    """Reject an order whose limit price slipped too far from the signal price.

    Fail-closed: if slippage checking is configured but either price is missing,
    the order is rejected.
    """
    if bounds.max_slippage_pct is None:
        return BoundsVerdict(True)
    ref = signal.price_ref
    exec_price = plan.limit_price
    if ref is None or exec_price is None or ref <= 0:
        return BoundsVerdict(False, "max_slippage_pct", "slippage check configured but price unavailable (fail-closed)")
    slippage = abs(exec_price - ref) / ref
    if slippage > bounds.max_slippage_pct:
        return BoundsVerdict(
            False, "max_slippage_pct",
            f"slippage {slippage:.4f} > cap {bounds.max_slippage_pct:.4f}",
        )
    return BoundsVerdict(True)
