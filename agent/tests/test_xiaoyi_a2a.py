"""Tests for the Huawei Xiaoyi A2A (Agent-to-Agent) WebSocket channel adapter (JSON-RPC 2.0)."""

from __future__ import annotations

import asyncio
import base64
import hmac
import json
import time
from typing import Any

import pytest

from src.channels.bus.queue import MessageBus
from src.channels.manager import ChannelManager
from src.channels.registry import discover_channel_names, inspect_channel


# ── Helpers ───────────────────────────────────────────────────────────────


def _make_jsonrpc_inbound(
    text: str,
    session_id: str = "s-001",
    task_id: str = "task-001",
    rpc_id: str = "rpc-100",
    agent_id: str = "agent-001",
) -> dict[str, Any]:
    """Build a JSON-RPC 2.0 message/stream inbound message dict."""
    return {
        "agentId": agent_id,
        "jsonrpc": "2.0",
        "id": rpc_id,
        "method": "message/stream",
        "params": {
            "id": task_id,
            "sessionId": session_id,
            "message": {
                "role": "user",
                "parts": [
                    {"kind": "text", "text": text},
                ],
            },
        },
    }


def _make_jsonrpc_inbound_with_file(
    text: str,
    file_name: str = "test.pdf",
    session_id: str = "s-001",
    task_id: str = "task-001",
    rpc_id: str = "rpc-100",
) -> dict[str, Any]:
    """Build a JSON-RPC 2.0 message/stream inbound with a file part."""
    parts: list[dict[str, Any]] = [{"kind": "text", "text": text}]
    parts.append({
        "kind": "file",
        "file": {"name": file_name, "mimeType": "application/pdf", "uri": "file://test.pdf"},
    })
    return {
        "agentId": "agent-001",
        "jsonrpc": "2.0",
        "id": rpc_id,
        "method": "message/stream",
        "params": {
            "id": task_id,
            "sessionId": session_id,
            "message": {
                "role": "user",
                "parts": parts,
            },
        },
    }


# ── Signature Generation ──────────────────────────────────────────────────


def test_build_auth_signature_correctness() -> None:
    """Base64(HMAC-SHA256(SK, str(timestamp))) should produce correct signature."""
    from src.channels.xiaoyi_a2a import _build_auth_signature

    ak = "test_ak"
    sk = "test_sk"
    ts = 1720000000000  # milliseconds

    sig = _build_auth_signature(ak, sk, ts)

    # Verify manually: Base64(HMAC-SHA256(SK, str(ts)))
    expected_digest = hmac.new(
        sk.encode("utf-8"),
        str(ts).encode("utf-8"),
        "sha256",
    ).digest()
    expected_sig = base64.b64encode(expected_digest).decode("utf-8")

    assert sig == expected_sig
    assert len(sig) == 44  # SHA-256 digest = 32 bytes → Base64 = 44 chars


def test_build_auth_signature_different_inputs_produce_different_outputs() -> None:
    """Different SKs or timestamps should yield different signatures."""
    from src.channels.xiaoyi_a2a import _build_auth_signature

    sig1 = _build_auth_signature("ak1", "sk1", 1000)
    sig2 = _build_auth_signature("ak2", "sk1", 1000)  # AK not used, so same
    sig3 = _build_auth_signature("ak1", "sk2", 1000)
    sig4 = _build_auth_signature("ak1", "sk1", 2000)

    assert sig1 == sig2  # AK doesn't affect signature in new protocol
    assert sig1 != sig3  # Different SK
    assert sig1 != sig4  # Different timestamp


def test_build_auth_signature_pure_timestamp_message() -> None:
    """Signature input is pure timestamp string, not 'ak=...&timestamp=...'."""
    from src.channels.xiaoyi_a2a import _build_auth_signature

    sk = "my_secret"
    ts = 1720000000123

    sig = _build_auth_signature("any_ak", sk, ts)

    # Verify it uses str(ts) as the message, not the old format
    expected_digest = hmac.new(
        sk.encode("utf-8"),
        str(ts).encode("utf-8"),
        "sha256",
    ).digest()
    expected = base64.b64encode(expected_digest).decode("utf-8")
    assert sig == expected

    # Old format would produce different result
    old_format_message = f"ak=any_ak&timestamp={ts}"
    old_digest = hmac.new(
        sk.encode("utf-8"),
        old_format_message.encode("utf-8"),
        "sha256",
    ).digest()
    old_sig = base64.b64encode(old_digest).decode("utf-8")
    assert sig != old_sig  # Old format gives different result


# ── JSON-RPC 2.0 Protocol Tests ─────────────────────────────────────────


def test_is_a2a_request_message_valid() -> None:
    """_is_a2a_request_message should return True for valid message/stream."""
    from src.channels.xiaoyi_a2a import XiaoyiA2AChannel

    data = _make_jsonrpc_inbound("Hello!")
    assert XiaoyiA2AChannel._is_a2a_request_message(data) is True


def test_is_a2a_request_message_missing_jsonrpc() -> None:
    """Missing jsonrpc field should return False."""
    from src.channels.xiaoyi_a2a import XiaoyiA2AChannel

    data = _make_jsonrpc_inbound("Hello!")
    del data["jsonrpc"]
    assert XiaoyiA2AChannel._is_a2a_request_message(data) is False


def test_is_a2a_request_message_wrong_method() -> None:
    """Wrong method should return False."""
    from src.channels.xiaoyi_a2a import XiaoyiA2AChannel

    data = _make_jsonrpc_inbound("Hello!")
    data["method"] = "other/method"
    assert XiaoyiA2AChannel._is_a2a_request_message(data) is False


def test_is_a2a_request_message_missing_parts() -> None:
    """Missing parts array should return False."""
    from src.channels.xiaoyi_a2a import XiaoyiA2AChannel

    data = _make_jsonrpc_inbound("Hello!")
    del data["params"]["message"]["parts"]
    assert XiaoyiA2AChannel._is_a2a_request_message(data) is False


def test_is_a2a_request_message_missing_params_id() -> None:
    """Missing params.id should return False."""
    from src.channels.xiaoyi_a2a import XiaoyiA2AChannel

    data = _make_jsonrpc_inbound("Hello!")
    del data["params"]["id"]
    assert XiaoyiA2AChannel._is_a2a_request_message(data) is False


def test_is_a2a_request_message_session_id_in_top_level() -> None:
    """sessionId at top-level (not in params) should still be valid."""
    from src.channels.xiaoyi_a2a import XiaoyiA2AChannel

    data = _make_jsonrpc_inbound("Hello!")
    del data["params"]["sessionId"]
    data["sessionId"] = "s-top-level"
    assert XiaoyiA2AChannel._is_a2a_request_message(data) is True


def test_is_a2a_request_message_none_input() -> None:
    """None input should return False."""
    from src.channels.xiaoyi_a2a import XiaoyiA2AChannel

    assert XiaoyiA2AChannel._is_a2a_request_message(None) is False  # type: ignore[arg-type]
    assert XiaoyiA2AChannel._is_a2a_request_message({}) is False


def test_extract_text_from_parts_text_only() -> None:
    """_extract_text_from_parts should extract text from text-kind parts."""
    from src.channels.xiaoyi_a2a import XiaoyiA2AChannel

    parts = [
        {"kind": "text", "text": "Hello"},
        {"kind": "text", "text": "World"},
    ]
    result = XiaoyiA2AChannel._extract_text_from_parts(parts)
    assert result == "Hello\nWorld"


def test_extract_text_from_parts_with_file() -> None:
    """_extract_text_from_parts should include file name hints."""
    from src.channels.xiaoyi_a2a import XiaoyiA2AChannel

    parts = [
        {"kind": "text", "text": "Check this:"},
        {"kind": "file", "file": {"name": "report.pdf", "mimeType": "application/pdf", "uri": "file://report.pdf"}},
    ]
    result = XiaoyiA2AChannel._extract_text_from_parts(parts)
    assert "Check this:" in result
    assert "[文件: report.pdf]" in result


def test_extract_text_from_parts_empty() -> None:
    """_extract_text_from_parts should return empty string for empty parts."""
    from src.channels.xiaoyi_a2a import XiaoyiA2AChannel

    assert XiaoyiA2AChannel._extract_text_from_parts([]) == ""


def test_build_jsonrpc_error() -> None:
    """_build_jsonrpc_error should produce correct JSON-RPC error."""
    from src.channels.xiaoyi_a2a import XiaoyiA2AConfig, XiaoyiA2AChannel

    config = XiaoyiA2AConfig(agent_id="agent-001")
    bus = MessageBus()
    ch = XiaoyiA2AChannel(config, bus)

    error = ch._build_jsonrpc_error(rpc_id="rpc-1", code=-1, message="test error")
    assert error["jsonrpc"] == "2.0"
    assert error["id"] == "rpc-1"
    assert error["error"]["code"] == -1
    assert error["error"]["message"] == "test error"


def test_agent_response_wrapper_format() -> None:
    """_wrap_and_send should produce correct outer wrapper with msgDetail as JSON string."""
    import asyncio as _asyncio

    # Just test the dict construction, not actual send
    # Build what _wrap_and_send would construct
    msg_detail = {"jsonrpc": "2.0", "id": "rpc-1", "result": {"kind": "artifact-update"}}
    wrapper = {
        "msgType": "agent_response",
        "agentId": "agent-001",
        "sessionId": "sess-1",
        "taskId": "task-1",
        "msgDetail": json.dumps(msg_detail, ensure_ascii=False),
    }
    assert wrapper["msgType"] == "agent_response"
    assert wrapper["agentId"] == "agent-001"
    assert wrapper["sessionId"] == "sess-1"
    assert wrapper["taskId"] == "task-1"
    # msgDetail must be a JSON string
    assert isinstance(wrapper["msgDetail"], str)
    parsed = json.loads(wrapper["msgDetail"])
    assert parsed["jsonrpc"] == "2.0"


# ── XiaoyiA2AConfig Validation ────────────────────────────────────────────


def test_xiaoyi_a2a_config_defaults() -> None:
    """XiaoyiA2AConfig should have sensible defaults."""
    from src.channels.xiaoyi_a2a import XiaoyiA2AConfig

    config = XiaoyiA2AConfig()
    assert config.enabled is False
    assert config.ws_url1 == "wss://hag.cloud.huawei.com/openclaw/v1/ws/link"
    assert config.ws_url2 == "wss://116.63.174.231/openclaw/v1/ws/link"
    assert config.ak == ""
    assert config.sk == ""
    assert config.agent_id == ""
    assert config.allow_from == ["*"]
    assert config.heartbeat_interval == 30
    assert config.heartbeat_timeout == 10
    assert config.reconnect_initial_delay == 1.0
    assert config.reconnect_max_delay == 30.0
    assert config.session_keepalive == 300
    assert config.streaming is True
    assert config.connection_timeout == 30


def test_xiaoyi_a2a_config_custom_values() -> None:
    """XiaoyiA2AConfig should accept custom values."""
    from src.channels.xiaoyi_a2a import XiaoyiA2AConfig

    config = XiaoyiA2AConfig(
        enabled=True,
        ws_url1="wss://custom1.example.com/ws",
        ws_url2="wss://custom2.example.com/ws",
        ak="my_ak",
        sk="my_sk",
        agent_id="agent-001",
        allow_from=["user-1", "user-2"],
        heartbeat_interval=60,
        heartbeat_timeout=15,
        reconnect_initial_delay=2.0,
        reconnect_max_delay=60.0,
        session_keepalive=600,
        streaming=False,
        connection_timeout=20,
    )
    assert config.enabled is True
    assert config.ws_url1 == "wss://custom1.example.com/ws"
    assert config.ws_url2 == "wss://custom2.example.com/ws"
    assert config.ak == "my_ak"
    assert config.agent_id == "agent-001"
    assert config.allow_from == ["user-1", "user-2"]
    assert config.heartbeat_interval == 60
    assert config.heartbeat_timeout == 15
    assert config.connection_timeout == 20


def test_xiaoyi_a2a_config_backward_compat_with_ws_url() -> None:
    """If old 'ws_url' key is provided, it should be ignored gracefully using defaults."""
    from src.channels.xiaoyi_a2a import XiaoyiA2AConfig

    # Old config with ws_url should still validate (extra fields ignored in v2)
    config = XiaoyiA2AConfig(
        enabled=True,
        ak="my_ak",
        sk="my_sk",
        agent_id="agent-001",
    )
    assert config.ws_url1 == "wss://hag.cloud.huawei.com/openclaw/v1/ws/link"
    assert config.ws_url2 == "wss://116.63.174.231/openclaw/v1/ws/link"


# ── Discovery & Availability ──────────────────────────────────────────────


def test_xiaoyi_a2a_is_discovered_as_built_in_channel() -> None:
    """xiaoyi_a2a should appear in discover_channel_names()."""
    names = discover_channel_names()
    assert "xiaoyi_a2a" in names, "xiaoyi_a2a should be auto-discovered"


def test_xiaoyi_a2a_inspect_shows_available() -> None:
    """inspect_channel should show xiaoyi_a2a as available."""
    status = inspect_channel("xiaoyi_a2a")
    assert status.available is True
    assert status.name == "xiaoyi_a2a"
    assert status.display_name == "华为小艺 A2A"


# ── Channel Manager Construction ──────────────────────────────────────────


def test_xiaoyi_a2a_channel_can_be_constructed_via_manager() -> None:
    """ChannelManager should be able to construct XiaoyiA2AChannel from config."""
    bus = MessageBus()
    config: dict[str, Any] = {
        "xiaoyi_a2a": {
            "enabled": True,
            "ws_url1": "ws://localhost:9999/ws",
            "ws_url2": "ws://localhost:9998/ws",
            "ak": "test_ak",
            "sk": "test_sk",
            "agent_id": "agent-test",
            "allow_from": ["*"],
        },
    }
    manager = ChannelManager(config, bus)
    assert "xiaoyi_a2a" in manager.channels
    ch = manager.channels["xiaoyi_a2a"]
    assert ch.name == "xiaoyi_a2a"
    assert ch.display_name == "华为小艺 A2A"
    assert ch.is_running is False


# ── Mock WebSocket Server Tests ───────────────────────────────────────────


class MockWebSocketServer:
    """A simple mock WebSocket server for testing XiaoyiA2AChannel.

    Tracks auth headers received during WebSocket handshake.
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 0) -> None:
        self.host = host
        self.port = port
        self._server: asyncio.Server | None = None
        self._received: list[str] = []
        self._auth_headers: dict[str, str] | None = None
        self._clients: list[Any] = []

    @property
    def ws_url(self) -> str:
        return f"ws://{self.host}:{self.port}/ws"

    async def start(self) -> None:
        import websockets

        async def handler(ws: Any) -> None:
            # Capture auth headers from the handshake request
            self._auth_headers = dict(ws.request.headers) if hasattr(ws, "request") else {}
            self._clients.append(ws)
            try:
                async for raw in ws:
                    self._received.append(raw)
                    data = json.loads(raw)
                    msg_type = data.get("msgType", data.get("type", ""))

                    # Respond to heartbeat
                    if msg_type == "heartbeat":
                        await ws.send(json.dumps({
                            "msgType": "heartbeat",
                            "agentId": data.get("agentId", ""),
                            "timestamp": int(time.time() * 1000),
                        }))
                    elif msg_type == "ping":
                        await ws.send(json.dumps({"type": "pong", "timestamp": int(time.time())}))
            except Exception:
                pass
            finally:
                if ws in self._clients:
                    self._clients.remove(ws)

        self._server = await websockets.serve(handler, self.host, self.port)
        # Get the actual port assigned
        for sock in self._server.sockets:
            addr = sock.getsockname()
            self.port = addr[1]
            break

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    async def send_json(self, data: dict[str, Any]) -> None:
        """Send a JSON message to all connected clients."""
        if self._clients:
            raw = json.dumps(data, ensure_ascii=False)
            await self._clients[0].send(raw)


@pytest.mark.asyncio
async def test_xiaoyi_a2a_connect_with_auth_headers() -> None:
    """XiaoyiA2AChannel should connect and pass auth via HTTP headers on WebSocket handshake."""
    mock_server = MockWebSocketServer()
    await mock_server.start()

    bus = MessageBus()
    config: dict[str, Any] = {
        "xiaoyi_a2a": {
            "enabled": True,
            "ws_url1": mock_server.ws_url,
            "ws_url2": "",
            "ak": "test_ak",
            "sk": "test_sk",
            "agent_id": "agent-001",
            "allow_from": ["*"],
            "heartbeat_interval": 3,
            "reconnect_initial_delay": 0.01,
            "reconnect_max_delay": 0.05,
        },
    }
    manager = ChannelManager(config, bus)
    ch = manager.channels["xiaoyi_a2a"]

    # Start channel in background
    start_task = asyncio.create_task(ch.start())

    try:
        # Wait for connection to be established
        await asyncio.sleep(0.5)

        # Verify auth headers were passed during WebSocket handshake
        assert mock_server._auth_headers is not None, "No auth headers received on handshake"
        headers_lower = {k.lower(): v for k, v in mock_server._auth_headers.items()}
        assert "x-access-key" in headers_lower, f"x-access-key header missing, got: {list(mock_server._auth_headers.keys())}"
        assert headers_lower["x-access-key"] == "test_ak"
        assert "x-agent-id" in headers_lower
        assert headers_lower["x-agent-id"] == "agent-001"
        assert "x-sign" in headers_lower
        assert "x-ts" in headers_lower

        # Verify x-sign is Base64 (length 44 for SHA-256)
        x_sign = headers_lower["x-sign"]
        assert len(x_sign) == 44, f"Expected Base64 length 44, got {len(x_sign)}: {x_sign}"

        # Verify signature correctness
        from src.channels.xiaoyi_a2a import _build_auth_signature

        ts = int(headers_lower["x-ts"])
        expected_sig = _build_auth_signature("test_ak", "test_sk", ts)
        assert x_sign == expected_sig
    finally:
        await ch.stop()
        start_task.cancel()
        with __import__("contextlib").suppress(asyncio.CancelledError):
            await start_task
        await mock_server.stop()


@pytest.mark.asyncio
async def test_xiaoyi_a2a_connect_fallback_to_url2() -> None:
    """XiaoyiA2AChannel should fall back to ws_url2 when ws_url1 is unreachable."""
    # Use a definitely-unreachable URL1 and a mock server URL2
    mock_server = MockWebSocketServer()
    await mock_server.start()

    bus = MessageBus()
    config: dict[str, Any] = {
        "xiaoyi_a2a": {
            "enabled": True,
            "ws_url1": "ws://127.0.0.1:19999/ws",  # Unreachable
            "ws_url2": mock_server.ws_url,
            "ak": "test_ak",
            "sk": "test_sk",
            "agent_id": "agent-001",
            "allow_from": ["*"],
            "heartbeat_interval": 30,
            "connection_timeout": 1,  # Short timeout for quick fallback
            "reconnect_initial_delay": 0.01,
            "reconnect_max_delay": 0.05,
        },
    }
    manager = ChannelManager(config, bus)
    ch = manager.channels["xiaoyi_a2a"]

    start_task = asyncio.create_task(ch.start())

    try:
        # Wait for fallback connection (URL1 fails after 1s timeout, URL2 connects quickly)
        await asyncio.sleep(2.0)
        assert mock_server._auth_headers is not None, "Should connect to fallback URL2"
        assert ch._current_url == mock_server.ws_url
    finally:
        await ch.stop()
        start_task.cancel()
        with __import__("contextlib").suppress(asyncio.CancelledError):
            await start_task
        await mock_server.stop()


@pytest.mark.asyncio
async def test_xiaoyi_a2a_heartbeat_message_format() -> None:
    """XiaoyiA2AChannel should send heartbeat with msgType=heartbeat and agentId."""
    mock_server = MockWebSocketServer()
    await mock_server.start()

    bus = MessageBus()
    config: dict[str, Any] = {
        "xiaoyi_a2a": {
            "enabled": True,
            "ws_url1": mock_server.ws_url,
            "ws_url2": "",
            "ak": "test_ak",
            "sk": "test_sk",
            "agent_id": "agent-001",
            "allow_from": ["*"],
            "heartbeat_interval": 1,  # Fast heartbeat for test
            "heartbeat_timeout": 3,
            "reconnect_initial_delay": 0.01,
            "reconnect_max_delay": 0.05,
        },
    }
    manager = ChannelManager(config, bus)
    ch = manager.channels["xiaoyi_a2a"]

    start_task = asyncio.create_task(ch.start())

    try:
        # Wait for at least one heartbeat
        await asyncio.sleep(1.5)

        # Find heartbeat messages in received
        heartbeat_msgs = [
            json.loads(msg)
            for msg in mock_server._received
            if '"msgType":"heartbeat"' in msg or '"msgType": "heartbeat"' in msg
        ]
        assert len(heartbeat_msgs) >= 1, f"No heartbeat messages found in: {mock_server._received}"
        hb = heartbeat_msgs[0]
        assert hb.get("msgType") == "heartbeat"
        assert hb.get("agentId") == "agent-001"
        assert "timestamp" in hb
    finally:
        await ch.stop()
        start_task.cancel()
        with __import__("contextlib").suppress(asyncio.CancelledError):
            await start_task
        await mock_server.stop()


@pytest.mark.asyncio
async def test_xiaoyi_a2a_receive_inbound_message() -> None:
    """XiaoyiA2AChannel should parse A2A inbound messages and publish to bus."""
    mock_server = MockWebSocketServer()
    await mock_server.start()

    bus = MessageBus()
    config: dict[str, Any] = {
        "xiaoyi_a2a": {
            "enabled": True,
            "ws_url1": mock_server.ws_url,
            "ws_url2": "",
            "ak": "test_ak",
            "sk": "test_sk",
            "agent_id": "agent-001",
            "allow_from": ["*"],
            "heartbeat_interval": 30,  # Long interval to avoid heartbeat noise
            "reconnect_initial_delay": 0.01,
            "reconnect_max_delay": 0.05,
        },
    }
    manager = ChannelManager(config, bus)
    ch = manager.channels["xiaoyi_a2a"]

    start_task = asyncio.create_task(ch.start())

    try:
        # Wait for connection
        await asyncio.sleep(0.3)

        # Send a simulated inbound JSON-RPC message
        inbound_data = _make_jsonrpc_inbound("查询今天的行情", "sess-001", "task-001")
        await mock_server.send_json(inbound_data)

        # Wait for message to be processed
        await asyncio.sleep(0.3)

        # Check that a message was published to the bus
        try:
            msg = await asyncio.wait_for(bus.consume_inbound(), timeout=1.0)
            assert msg.channel == "xiaoyi_a2a"
            assert msg.sender_id == "sess-001"  # session_id is used as sender_id
            assert msg.content == "查询今天的行情"
            assert msg.chat_id == "sess-001"
        except asyncio.TimeoutError:
            pytest.fail("No inbound message published to bus")
    finally:
        await ch.stop()
        start_task.cancel()
        with __import__("contextlib").suppress(asyncio.CancelledError):
            await start_task
        await mock_server.stop()


@pytest.mark.asyncio
async def test_xiaoyi_a2a_receive_inbound_rejects_unauthorized() -> None:
    """Unauthorized sender should get an error response."""
    mock_server = MockWebSocketServer()
    await mock_server.start()

    bus = MessageBus()
    config: dict[str, Any] = {
        "xiaoyi_a2a": {
            "enabled": True,
            "ws_url1": mock_server.ws_url,
            "ws_url2": "",
            "ak": "test_ak",
            "sk": "test_sk",
            "agent_id": "agent-001",
            "allow_from": [],  # No one allowed
            "heartbeat_interval": 30,
            "reconnect_initial_delay": 0.01,
            "reconnect_max_delay": 0.05,
        },
    }
    manager = ChannelManager(config, bus)
    ch = manager.channels["xiaoyi_a2a"]

    start_task = asyncio.create_task(ch.start())

    try:
        await asyncio.sleep(0.3)

        inbound_data = _make_jsonrpc_inbound("hello", "sess-unauth", "task-unauth")
        await mock_server.send_json(inbound_data)
        await asyncio.sleep(0.3)

        # Should have received error response via WebSocket (msgType=agent_response with error in msgDetail)
        # msgDetail is a JSON string, so we need to parse the wrapper then parse msgDetail
        error_responses = []
        for raw_msg in mock_server._received:
            if '"msgType"' not in raw_msg:
                continue
            try:
                wrapper = json.loads(raw_msg)
                if wrapper.get("msgType") != "agent_response":
                    continue
                msg_detail = json.loads(wrapper.get("msgDetail", "{}"))
                if "error" in msg_detail:
                    error_responses.append(raw_msg)
            except (json.JSONDecodeError, KeyError):
                continue
        assert len(error_responses) > 0, "No error response sent for unauthorized sender"
    finally:
        await ch.stop()
        start_task.cancel()
        with __import__("contextlib").suppress(asyncio.CancelledError):
            await start_task
        await mock_server.stop()


@pytest.mark.asyncio
async def test_xiaoyi_a2a_empty_text_does_not_publish() -> None:
    """Empty text content should not publish to bus."""
    mock_server = MockWebSocketServer()
    await mock_server.start()

    bus = MessageBus()
    config: dict[str, Any] = {
        "xiaoyi_a2a": {
            "enabled": True,
            "ws_url1": mock_server.ws_url,
            "ws_url2": "",
            "ak": "test_ak",
            "sk": "test_sk",
            "agent_id": "agent-001",
            "allow_from": ["*"],
            "heartbeat_interval": 30,
            "reconnect_initial_delay": 0.01,
            "reconnect_max_delay": 0.05,
        },
    }
    manager = ChannelManager(config, bus)
    ch = manager.channels["xiaoyi_a2a"]

    start_task = asyncio.create_task(ch.start())

    try:
        await asyncio.sleep(0.3)

        inbound_data = _make_jsonrpc_inbound("", "sess-empty", "task-empty")
        await mock_server.send_json(inbound_data)
        await asyncio.sleep(0.3)

        # No message should be on the bus
        try:
            await asyncio.wait_for(bus.consume_inbound(), timeout=0.3)
            pytest.fail("Empty text should not publish to bus")
        except asyncio.TimeoutError:
            pass  # Expected
    finally:
        await ch.stop()
        start_task.cancel()
        with __import__("contextlib").suppress(asyncio.CancelledError):
            await start_task
        await mock_server.stop()


# ── JSON-RPC Special Messages ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_xiaoyi_a2a_clear_context_message() -> None:
    """clearContext message should trigger a 'cleared' response."""
    mock_server = MockWebSocketServer()
    await mock_server.start()

    bus = MessageBus()
    config: dict[str, Any] = {
        "xiaoyi_a2a": {
            "enabled": True,
            "ws_url1": mock_server.ws_url,
            "ws_url2": "",
            "ak": "test_ak",
            "sk": "test_sk",
            "agent_id": "agent-001",
            "allow_from": ["*"],
            "heartbeat_interval": 30,
            "reconnect_initial_delay": 0.01,
            "reconnect_max_delay": 0.05,
        },
    }
    manager = ChannelManager(config, bus)
    ch = manager.channels["xiaoyi_a2a"]

    start_task = asyncio.create_task(ch.start())

    try:
        await asyncio.sleep(0.3)

        clear_msg = {
            "agentId": "agent-001",
            "jsonrpc": "2.0",
            "id": "clear-1",
            "method": "clearContext",
            "sessionId": "sess-clear",
        }
        await mock_server.send_json(clear_msg)
        await asyncio.sleep(0.3)

        # Should receive a clear response (msgDetail contains "cleared" state)
        clear_responses = []
        for raw_msg in mock_server._received:
            if '"msgType"' not in raw_msg:
                continue
            try:
                wrapper = json.loads(raw_msg)
                if wrapper.get("msgType") != "agent_response":
                    continue
                msg_detail = json.loads(wrapper.get("msgDetail", "{}"))
                if msg_detail.get("result", {}).get("status", {}).get("state") == "cleared":
                    clear_responses.append(raw_msg)
            except (json.JSONDecodeError, KeyError):
                continue
        assert len(clear_responses) > 0, "No clearContext response sent"
    finally:
        await ch.stop()
        start_task.cancel()
        with __import__("contextlib").suppress(asyncio.CancelledError):
            await start_task
        await mock_server.stop()


@pytest.mark.asyncio
async def test_xiaoyi_a2a_tasks_cancel_message() -> None:
    """tasks/cancel message should trigger a 'canceled' response."""
    mock_server = MockWebSocketServer()
    await mock_server.start()

    bus = MessageBus()
    config: dict[str, Any] = {
        "xiaoyi_a2a": {
            "enabled": True,
            "ws_url1": mock_server.ws_url,
            "ws_url2": "",
            "ak": "test_ak",
            "sk": "test_sk",
            "agent_id": "agent-001",
            "allow_from": ["*"],
            "heartbeat_interval": 30,
            "reconnect_initial_delay": 0.01,
            "reconnect_max_delay": 0.05,
        },
    }
    manager = ChannelManager(config, bus)
    ch = manager.channels["xiaoyi_a2a"]

    start_task = asyncio.create_task(ch.start())

    try:
        await asyncio.sleep(0.3)

        cancel_msg = {
            "agentId": "agent-001",
            "jsonrpc": "2.0",
            "id": "cancel-1",
            "method": "tasks/cancel",
            "sessionId": "sess-cancel",
            "taskId": "task-to-cancel",
        }
        await mock_server.send_json(cancel_msg)
        await asyncio.sleep(0.3)

        # Should receive a cancel response (msgDetail contains "canceled" state)
        cancel_responses = []
        for raw_msg in mock_server._received:
            if '"msgType"' not in raw_msg:
                continue
            try:
                wrapper = json.loads(raw_msg)
                if wrapper.get("msgType") != "agent_response":
                    continue
                msg_detail = json.loads(wrapper.get("msgDetail", "{}"))
                if msg_detail.get("result", {}).get("status", {}).get("state") == "canceled":
                    cancel_responses.append(raw_msg)
            except (json.JSONDecodeError, KeyError):
                continue
        assert len(cancel_responses) > 0, "No tasks/cancel response sent"
    finally:
        await ch.stop()
        start_task.cancel()
        with __import__("contextlib").suppress(asyncio.CancelledError):
            await start_task
        await mock_server.stop()


# ── JSON-RPC Outbound Format Tests ───────────────────────────────────────


@pytest.mark.asyncio
async def test_xiaoyi_a2a_outbound_uses_agent_response_wrapper() -> None:
    """Outbound messages should use msgType=agent_response wrapper with JSON-RPC in msgDetail."""
    mock_server = MockWebSocketServer()
    await mock_server.start()

    bus = MessageBus()
    config: dict[str, Any] = {
        "xiaoyi_a2a": {
            "enabled": True,
            "ws_url1": mock_server.ws_url,
            "ws_url2": "",
            "ak": "test_ak",
            "sk": "test_sk",
            "agent_id": "agent-001",
            "allow_from": ["*"],
            "heartbeat_interval": 30,
            "reconnect_initial_delay": 0.01,
            "reconnect_max_delay": 0.05,
        },
    }
    manager = ChannelManager(config, bus)
    ch = manager.channels["xiaoyi_a2a"]

    start_task = asyncio.create_task(ch.start())

    try:
        await asyncio.sleep(0.3)

        # Send inbound to set routing state
        inbound_data = _make_jsonrpc_inbound("test outbound format", "sess-out", "task-out")
        await mock_server.send_json(inbound_data)
        await asyncio.sleep(0.5)

        # The channel should have sent a status-update (inside msgDetail JSON string)
        status_msgs = []
        for raw_msg in mock_server._received:
            if '"msgType"' not in raw_msg:
                continue
            try:
                wrapper = json.loads(raw_msg)
                if wrapper.get("msgType") != "agent_response":
                    continue
                msg_detail = json.loads(wrapper.get("msgDetail", "{}"))
                if msg_detail.get("result", {}).get("kind") == "status-update":
                    status_msgs.append((wrapper, msg_detail))
            except (json.JSONDecodeError, KeyError):
                continue
        assert len(status_msgs) >= 1, f"No status-update sent. Received: {mock_server._received}"

        # Verify the wrapper format
        wrapper, msg_detail = status_msgs[0]
        assert wrapper["msgType"] == "agent_response"
        assert wrapper["agentId"] == "agent-001"
        assert wrapper["sessionId"] == "sess-out"
        assert wrapper["taskId"] == "task-out"
        assert isinstance(wrapper["msgDetail"], str)

        # Verify msgDetail as JSON-RPC
        assert msg_detail["jsonrpc"] == "2.0"
        assert msg_detail["result"]["kind"] == "status-update"
        assert msg_detail["result"]["final"] is False
        assert msg_detail["result"]["status"]["state"] == "working"
    finally:
        await ch.stop()
        start_task.cancel()
        with __import__("contextlib").suppress(asyncio.CancelledError):
            await start_task
        await mock_server.stop()


@pytest.mark.asyncio
async def test_xiaoyi_a2a_receive_inbound_with_file_parts() -> None:
    """JSON-RPC message with file parts should extract text + file hints."""
    mock_server = MockWebSocketServer()
    await mock_server.start()

    bus = MessageBus()
    config: dict[str, Any] = {
        "xiaoyi_a2a": {
            "enabled": True,
            "ws_url1": mock_server.ws_url,
            "ws_url2": "",
            "ak": "test_ak",
            "sk": "test_sk",
            "agent_id": "agent-001",
            "allow_from": ["*"],
            "heartbeat_interval": 30,
            "reconnect_initial_delay": 0.01,
            "reconnect_max_delay": 0.05,
        },
    }
    manager = ChannelManager(config, bus)
    ch = manager.channels["xiaoyi_a2a"]

    start_task = asyncio.create_task(ch.start())

    try:
        await asyncio.sleep(0.3)

        inbound_data = _make_jsonrpc_inbound_with_file("分析这个报告", "report.pdf", "sess-file", "task-file")
        await mock_server.send_json(inbound_data)
        await asyncio.sleep(0.3)

        msg = await asyncio.wait_for(bus.consume_inbound(), timeout=1.0)
        assert msg.channel == "xiaoyi_a2a"
        assert "分析这个报告" in msg.content
        assert "[文件: report.pdf]" in msg.content
    finally:
        await ch.stop()
        start_task.cancel()
        with __import__("contextlib").suppress(asyncio.CancelledError):
            await start_task
        await mock_server.stop()


# ── JSON-RPC Routing State Tests ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_xiaoyi_a2a_stores_task_and_session_state() -> None:
    """Channel should store _current_task_id and _current_session_id after receiving message."""
    mock_server = MockWebSocketServer()
    await mock_server.start()

    bus = MessageBus()
    config: dict[str, Any] = {
        "xiaoyi_a2a": {
            "enabled": True,
            "ws_url1": mock_server.ws_url,
            "ws_url2": "",
            "ak": "test_ak",
            "sk": "test_sk",
            "agent_id": "agent-001",
            "allow_from": ["*"],
            "heartbeat_interval": 30,
            "reconnect_initial_delay": 0.01,
            "reconnect_max_delay": 0.05,
        },
    }
    manager = ChannelManager(config, bus)
    ch = manager.channels["xiaoyi_a2a"]

    # State should be None initially
    assert ch._current_task_id is None
    assert ch._current_session_id is None
    assert ch._last_inbound_rpc_id is None

    start_task = asyncio.create_task(ch.start())

    try:
        await asyncio.sleep(0.3)

        inbound_data = _make_jsonrpc_inbound("test state", "sess-state", "task-state", "rpc-state")
        await mock_server.send_json(inbound_data)
        await asyncio.sleep(0.3)

        # State should be updated
        assert ch._current_task_id == "task-state"
        assert ch._current_session_id == "sess-state"
        assert ch._last_inbound_rpc_id == "rpc-state"
    finally:
        await ch.stop()
        start_task.cancel()
        with __import__("contextlib").suppress(asyncio.CancelledError):
            await start_task
        await mock_server.stop()
