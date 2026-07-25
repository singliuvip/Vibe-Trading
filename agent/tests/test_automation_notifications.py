"""Tests for automation notifications (format + non-blocking publish)."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from src.live.automation.notifications import (
    format_cycle_notification,
    notify_cycle_result,
)
from src.live.automation.service import AutomationCycleResult


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


def _cycle_result(**overrides) -> AutomationCycleResult:
    defaults = dict(
        broker="virtual",
        mode="paper_auto",
        signals_evaluated=3,
        orders_placed=1,
        orders_rejected=1,
        errors=1,
        results=(
            {
                "symbol": "AAPL",
                "decision": "allowed",
                "placed": True,
                "plan": {"side": "buy", "quantity": 100, "limit_price": 150.0},
            },
            {
                "symbol": "TSLA",
                "decision": "rejected_score",
                "placed": False,
                "reason": "score below threshold",
            },
            {
                "symbol": "GOOG",
                "decision": "allowed",
                "placed": False,
                "error": "submission failed",
            },
        ),
    )
    defaults.update(overrides)
    return AutomationCycleResult(**defaults)


# --------------------------------------------------------------------------- #
# format_cycle_notification                                                   #
# --------------------------------------------------------------------------- #


def test_format_includes_summary_line():
    text = format_cycle_notification(_cycle_result())
    assert "🤖 Auto-trading cycle — virtual (paper_auto)" in text
    assert "Signals: 3" in text
    assert "Placed: 1" in text
    assert "Rejected: 1" in text
    assert "Errors: 1" in text


def test_format_includes_placed_order():
    text = format_cycle_notification(_cycle_result())
    assert "✅ AAPL: buy 100 @ 150.0" in text


def test_format_includes_rejected_signal():
    text = format_cycle_notification(_cycle_result())
    assert "⏭️ TSLA: rejected_score — score below threshold" in text


def test_format_includes_error_signal():
    text = format_cycle_notification(_cycle_result())
    assert "❌ GOOG: ERROR — submission failed" in text


def test_format_placed_without_limit_price():
    result = _cycle_result(
        results=({"symbol": "X", "placed": True, "plan": {"side": "sell", "quantity": 50}},),
        signals_evaluated=1, orders_placed=1, orders_rejected=0, errors=0,
    )
    text = format_cycle_notification(result)
    assert "✅ X: sell 50" in text
    assert "@" not in text.split("✅")[1]


# --------------------------------------------------------------------------- #
# notify_cycle_result — no publisher                                          #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_notify_no_publisher_returns_false():
    result = _cycle_result()
    ok = await notify_cycle_result(result, publish_outbound=None, chat_id="123")
    assert ok is False


# --------------------------------------------------------------------------- #
# notify_cycle_result — no chat_id                                            #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_notify_no_chat_id_returns_false():
    result = _cycle_result()

    async def fake_publish(msg: Any) -> None:
        pass

    ok = await notify_cycle_result(result, publish_outbound=fake_publish, chat_id="")
    assert ok is False


# --------------------------------------------------------------------------- #
# notify_cycle_result — publisher raises → returns False (non-blocking)       #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_notify_publisher_exception_returns_false():
    result = _cycle_result()

    async def bad_publish(msg: Any) -> None:
        raise RuntimeError("bus unavailable")

    ok = await notify_cycle_result(result, publish_outbound=bad_publish, chat_id="chat-1")
    assert ok is False  # must not raise


# --------------------------------------------------------------------------- #
# notify_cycle_result — success                                               #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_notify_success_returns_true_and_publishes():
    result = _cycle_result()
    published: list[Any] = []

    async def capture_publish(msg: Any) -> None:
        published.append(msg)

    ok = await notify_cycle_result(
        result, publish_outbound=capture_publish, channel="telegram", chat_id="chat-99"
    )

    assert ok is True
    assert len(published) == 1
    msg = published[0]
    assert msg.channel == "telegram"
    assert msg.chat_id == "chat-99"
    assert "🤖 Auto-trading cycle" in msg.content
    assert msg.metadata["_automation_notification"] is True
    assert msg.metadata["broker"] == "virtual"
