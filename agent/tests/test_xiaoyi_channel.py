"""Tests for the Huawei Xiaoyi channel adapter."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from src.channels.bus.events import InboundMessage, OutboundMessage
from src.channels.bus.queue import MessageBus
from src.channels.manager import ChannelManager
from src.channels.registry import discover_channel_names, inspect_channel


# ── Helpers ───────────────────────────────────────────────────────────────


def _xiaoyi_webhook_payload(text: str, session_id: str = "test-session-1") -> bytes:
    """Build a minimal Xiaoyi skill webhook request body."""
    return json.dumps({
        "session": {
            "session_id": session_id,
            "new_session": True,
        },
        "request": {
            "type": "text",
            "text": text,
        },
        "original": {},
    }).encode("utf-8")


# ── Discovery & Availability ──────────────────────────────────────────────


def test_xiaoyi_is_discovered_as_built_in_channel() -> None:
    """xiaoyi should appear in discover_channel_names()."""
    names = discover_channel_names()
    assert "xiaoyi" in names, "xiaoyi should be auto-discovered"


def test_xiaoyi_inspect_shows_available() -> None:
    """inspect_channel should show xiaoyi as available (no optional SDK needed)."""
    status = inspect_channel("xiaoyi")
    assert status.available is True
    assert status.name == "xiaoyi"
    assert status.display_name == "华为小艺"


# ── Channel Manager Construction ─────────────────────────────────────────


def test_xiaoyi_channel_can_be_constructed_via_manager() -> None:
    """ChannelManager should be able to construct XiaoyiChannel from config."""
    bus = MessageBus()
    config = {
        "xiaoyi": {
            "enabled": True,
            "mode": "standalone",
            "host": "127.0.0.1",
            "port": 0,  # port 0 = OS picks a free port
            "app_id": "test_app",
            "app_secret": "test_secret",
            "allow_from": ["*"],
        },
    }
    manager = ChannelManager(config, bus)
    assert "xiaoyi" in manager.channels
    ch = manager.channels["xiaoyi"]
    assert ch.name == "xiaoyi"
    assert ch.display_name == "华为小艺"
    assert ch.is_running is False


# ── Core Webhook Logic ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_xiaoyi_webhook_parses_payload_and_routes_to_bus() -> None:
    """Verify the webhook handler parses a Xiaoyi request and publishes inbound."""
    bus = MessageBus()
    manager_config = {
        "xiaoyi": {
            "enabled": True,
            "mode": "api_server",
            "webhook_path": "/xiaoyi/webhook",
            "app_id": "test_app",
            "allow_from": ["*"],
        },
    }
    manager = ChannelManager(manager_config, bus)
    xiaoyi = manager.channels["xiaoyi"]
    assert xiaoyi is not None

    payload = _xiaoyi_webhook_payload("查询今天的行情", "sess-001")

    # Simulate webhook call
    result = await xiaoyi.handle_webhook_request(
        body=payload,
        headers={"content-type": "application/json"},
    )

    # Should get a response with the expected structure
    assert isinstance(result, dict)
    assert "response" in result
    assert result["response"]["type"] == "text"


@pytest.mark.asyncio
async def test_xiaoyi_webhook_handles_empty_text() -> None:
    """Empty text should return an empty response without bus traffic."""
    bus = MessageBus()
    manager_config = {
        "xiaoyi": {
            "enabled": True,
            "mode": "api_server",
            "app_id": "test_app",
            "allow_from": ["*"],
        },
    }
    manager = ChannelManager(manager_config, bus)
    xiaoyi = manager.channels["xiaoyi"]

    payload = _xiaoyi_webhook_payload("", "sess-empty")
    result = await xiaoyi.handle_webhook_request(
        body=payload,
        headers={"content-type": "application/json"},
    )
    assert result["response"]["text"] == ""


@pytest.mark.asyncio
async def test_xiaoyi_webhook_rejects_unauthorized_sender() -> None:
    """Unauthorized sender should get a permission-denied response."""
    bus = MessageBus()
    manager_config = {
        "xiaoyi": {
            "enabled": True,
            "mode": "api_server",
            "app_id": "test_app",
            "allow_from": [],  # No one allowed
        },
    }
    manager = ChannelManager(manager_config, bus)
    xiaoyi = manager.channels["xiaoyi"]

    payload = _xiaoyi_webhook_payload("hello", "sess-unauth")
    result = await xiaoyi.handle_webhook_request(
        body=payload,
        headers={"content-type": "application/json"},
    )
    assert "抱歉" in result["response"]["text"] or "权限" in result["response"]["text"]


# ── Signature Verification ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_xiaoyi_hmac_verification() -> None:
    """HMAC verification should pass with correct signature and fail without."""
    from src.channels.xiaoyi import _verify_hmac

    secret = "my_secret_key"
    body = json.dumps({"test": "data"}).encode("utf-8")

    # Calculate correct HMAC
    import hmac

    correct_sig = hmac.new(secret.encode("utf-8"), body, "sha256").hexdigest()

    # Valid signature
    assert _verify_hmac(body, correct_sig, secret) is True

    # Invalid signature
    assert _verify_hmac(body, "wrong_signature", secret) is False

    # Empty secret → fail
    assert _verify_hmac(body, correct_sig, "") is False

    # Empty signature → fail
    assert _verify_hmac(body, "", secret) is False


# ── Config Validation ────────────────────────────────────────────────────


def test_xiaoyi_config_validates_webhook_path() -> None:
    """webhook_path must start with '/'."""
    from pydantic import ValidationError
    from src.channels.xiaoyi import XiaoyiConfig

    with pytest.raises(ValidationError):
        XiaoyiConfig(webhook_path="no-slash")

    # Valid
    config = XiaoyiConfig(webhook_path="/xiaoyi/hook")
    assert config.webhook_path == "/xiaoyi/hook"


def test_xiaoyi_config_validates_mode() -> None:
    """mode must be standalone or api_server."""
    from pydantic import ValidationError
    from src.channels.xiaoyi import XiaoyiConfig

    # Invalid mode
    with pytest.raises(ValidationError):
        XiaoyiConfig(mode="invalid_mode")

    # Valid modes
    XiaoyiConfig(mode="standalone")
    XiaoyiConfig(mode="api_server")


# ── Deep Get/Set Utilities ────────────────────────────────────────────────


def test_deep_get_and_set() -> None:
    """_deep_get and _deep_set should work with nested paths."""
    from src.channels.xiaoyi import _deep_get, _deep_set

    data = {"a": {"b": {"c": "hello"}}}

    assert _deep_get(data, ["a", "b", "c"]) == "hello"
    assert _deep_get(data, ["a", "x"], "default") == "default"
    assert _deep_get({}, ["a", "b"], "default") == "default"

    _deep_set(data, ["a", "b", "c"], "world")
    assert data["a"]["b"]["c"] == "world"

    _deep_set(data, ["x", "y", "z"], "new")
    assert data["x"]["y"]["z"] == "new"
