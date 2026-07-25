"""Automation notifications — non-blocking IM push of trading results.

Notifications are a side-channel: a notification failure must NEVER affect
the order result. The notifier converts an AutomationCycleResult into an
OutboundMessage and publishes it to the MessageBus outbound queue. If the
bus is unavailable or the publish fails, the error is logged and swallowed.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from src.live.automation.service import AutomationCycleResult

logger = logging.getLogger(__name__)

#: Signature of the injected outbound publisher (MessageBus.publish_outbound).
PublishOutboundFn = Callable[[Any], Any]  # async callable


def format_cycle_notification(result: AutomationCycleResult) -> str:
    """Format an AutomationCycleResult as a human-readable notification string.

    The message is concise and actionable: it summarizes what happened and
    flags any errors or rejections that need attention.
    """
    lines = [
        f"🤖 Auto-trading cycle — {result.broker} ({result.mode})",
        f"Signals: {result.signals_evaluated} | Placed: {result.orders_placed} | "
        f"Rejected: {result.orders_rejected} | Errors: {result.errors}",
    ]

    for r in result.results:
        symbol = r.get("symbol", "?")
        decision = r.get("decision", "?")
        if r.get("placed"):
            plan = r.get("plan", {})
            side = plan.get("side", "?")
            qty = plan.get("quantity") or plan.get("notional") or "?"
            price = plan.get("limit_price")
            price_str = f" @ {price}" if price else ""
            lines.append(f"  ✅ {symbol}: {side} {qty}{price_str}")
        elif r.get("error"):
            lines.append(f"  ❌ {symbol}: ERROR — {r['error']}")
        else:
            reason = r.get("reason", "")
            lines.append(f"  ⏭️ {symbol}: {decision}" + (f" — {reason}" if reason else ""))

    return "\n".join(lines)


async def notify_cycle_result(
    result: AutomationCycleResult,
    *,
    publish_outbound: Optional[PublishOutboundFn] = None,
    channel: str = "telegram",
    chat_id: str = "",
) -> bool:
    """Publish a cycle result notification to the outbound message bus.

    Non-blocking and fail-safe: returns True if published, False otherwise.
    A False return never indicates a trading problem — only a notification
    delivery issue.

    Args:
        result: The cycle result to notify about.
        publish_outbound: Async callable to publish an OutboundMessage.
            When None, notification is skipped (no bus configured).
        channel: Target channel name.
        chat_id: Target chat/channel id.

    Returns:
        True if the notification was published, False otherwise.
    """
    if publish_outbound is None:
        logger.debug("automation notification skipped: no outbound publisher configured")
        return False
    if not chat_id:
        logger.debug("automation notification skipped: no chat_id configured")
        return False

    try:
        from src.channels.bus.events import OutboundMessage

        content = format_cycle_notification(result)
        msg = OutboundMessage(
            channel=channel,
            chat_id=chat_id,
            content=content,
            metadata={"_automation_notification": True, "broker": result.broker},
        )
        await publish_outbound(msg)
        return True
    except Exception as exc:  # noqa: BLE001 - notification must never break trading
        logger.warning("automation notification failed (non-fatal): %s", exc)
        return False
