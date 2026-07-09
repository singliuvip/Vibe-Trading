"""华为小艺 A2A (Agent-to-Agent) 频道 — WebSocket 适配器 (JSON-RPC 2.0 协议).

小艺 A2A 开放平台允许第三方 Agent 通过 WebSocket 长连接与小艺平台
进行双向通信。本频道以 WebSocket 客户端模式运行，连接到小艺云平台后：
- 接收小艺平台转发的用户消息（JSON-RPC 2.0 message/stream）
- 将 Agent 回复通过 WebSocket 发送回小艺平台（agent_response 包裹的 JSON-RPC）
- 支持流式输出（artifact-update 增量文本块）
- 支持中间状态通知（status-update）
- 支持 clearContext / tasks/cancel 特殊消息

鉴权方式：通过 HTTP Headers 在 WebSocket 握手时传递（x-access-key / x-sign / x-ts / x-agent-id），
不需要额外发送 auth message frame。

心跳方式：应用层 {"msgType":"heartbeat","agentId":"...","timestamp":...} + 协议层 WS ping。

架构::

    用户 → 小艺 App → 小艺云平台 → WebSocket ←→ XiaoyiA2AChannel
                                            │
                                        Agent Loop
                                            │
                                        MessageBus

入站消息格式（JSON-RPC 2.0 message/stream）::

    {
        "agentId": "agentxxx",
        "jsonrpc": "2.0",
        "id": "msg-sequence-id",
        "method": "message/stream",
        "params": {
            "id": "task-id",
            "sessionId": "session-xxx",
            "message": {
                "role": "user",
                "parts": [
                    {"kind": "text", "text": "用户输入"},
                    {"kind": "file", "file": {"name": "...", "mimeType": "...", "uri": "..."}}
                ]
            }
        }
    }

出站消息格式（agent_response 包裹的 JSON-RPC artifact-update）::

    {
        "msgType": "agent_response",
        "agentId": "agentxxx",
        "sessionId": "session-xxx",
        "taskId": "task-id",
        "msgDetail": "{...json-rpc-response-string...}"
    }

msgDetail 内为 JSON-RPC 2.0 响应，kind 可以是：
- artifact-update（正文流式）
- status-update（中间状态）
- error（错误）
"""

from __future__ import annotations

import asyncio
import base64
import hmac
import json
import logging
import time as time_module
import uuid
from contextlib import suppress
from typing import Any

import websockets
from pydantic import BaseModel, Field

from src.channels.base import BaseChannel
from src.channels.bus.events import InboundMessage, OutboundMessage
from src.channels.bus.queue import MessageBus

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 配置模型
# ---------------------------------------------------------------------------


class XiaoyiA2AConfig(BaseModel):
    """华为小艺 A2A 频道配置。

    Attributes:
        enabled: 是否启用本频道。
        ws_url1: 小艺云平台主 WebSocket URL。
        ws_url2: 小艺云平台备用 WebSocket URL。
        ak: Access Key（从 ~/.vibe-trading/.env 读取）。
        sk: Secret Key。
        agent_id: 小艺开放平台分配的 agentId。
        allow_from: 允许的发送者 ID 列表。"*" 表示全部允许。
        heartbeat_interval: 心跳间隔（秒）。
        heartbeat_timeout: 心跳超时（秒）。
        reconnect_initial_delay: 重连初始延迟（秒）。
        reconnect_max_delay: 重连最大延迟（秒）。
        session_keepalive: 会话保持时间（秒）。
        streaming: 是否启用流式输出。
        connection_timeout: 连接超时（秒）。
    """

    enabled: bool = False
    ws_url1: str = "wss://hag.cloud.huawei.com/openclaw/v1/ws/link"
    ws_url2: str = "wss://116.63.174.231/openclaw/v1/ws/link"
    ak: str = ""
    sk: str = ""
    agent_id: str = ""
    allow_from: list[str] = Field(default_factory=lambda: ["*"])
    heartbeat_interval: int = 30
    heartbeat_timeout: int = 10
    reconnect_initial_delay: float = 1.0
    reconnect_max_delay: float = 30.0
    session_keepalive: int = 300
    streaming: bool = True
    connection_timeout: int = 30


# ---------------------------------------------------------------------------
# JSON-RPC 2.0 协议辅助模型
# ---------------------------------------------------------------------------


class _JsonRpcPart(BaseModel):
    """JSON-RPC part（消息片段）。"""
    kind: str = "text"
    text: str = ""
    file: dict[str, str] | None = None


class _JsonRpcMessage(BaseModel):
    """JSON-RPC message（params.message）。"""
    role: str = "user"
    parts: list[_JsonRpcPart] = Field(default_factory=list)


class _JsonRpcParams(BaseModel):
    """JSON-RPC params。"""
    id: str = ""  # task-id
    sessionId: str = ""
    message: _JsonRpcMessage = Field(default_factory=_JsonRpcMessage)


# ---------------------------------------------------------------------------
# 签名工具
# ---------------------------------------------------------------------------


def _build_auth_signature(ak: str, sk: str, timestamp: int) -> str:
    """构建 A2A 鉴权签名。

    签名算法: Base64(HMAC-SHA256(SK, str(timestamp)))

    Args:
        ak: Access Key（保留参数，新签名算法不使用 AK）。
        sk: Secret Key。
        timestamp: Unix 时间戳（毫秒）。

    Returns:
        Base64 编码的 HMAC-SHA256 签名字符串（长度 44）。
    """
    message = str(timestamp)
    digest = hmac.new(
        sk.encode("utf-8"),
        message.encode("utf-8"),
        "sha256",
    ).digest()
    return base64.b64encode(digest).decode("utf-8")


# ---------------------------------------------------------------------------
# 频道实现
# ---------------------------------------------------------------------------


class XiaoyiA2AChannel(BaseChannel):
    """华为小艺 A2A 频道（JSON-RPC 2.0 协议）。

    通过 WebSocket 长连接与小艺云平台进行双向通信。
    支持双服务器（主 + 备），优先连接主 URL，失败时自动切换备用 URL。
    鉴权通过 WebSocket HTTP Headers 完成，不需要额外 auth frame。
    """

    name = "xiaoyi_a2a"
    display_name = "华为小艺 A2A"

    send_progress = False
    send_tool_hints = False
    show_reasoning = False

    @classmethod
    def default_config(cls) -> dict[str, Any]:
        return XiaoyiA2AConfig().model_dump(by_alias=True)

    def __init__(self, config: Any, bus: MessageBus) -> None:
        if isinstance(config, dict):
            config = XiaoyiA2AConfig.model_validate(config)
        super().__init__(config, bus)
        self.config: XiaoyiA2AConfig = config
        self._ws: Any = None
        self._ws_lock: asyncio.Lock = asyncio.Lock()
        self._recv_task: asyncio.Task[None] | None = None
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._reconnect_count: int = 0
        self._session_last_seen: dict[str, float] = {}
        self._cleanup_task: asyncio.Task[None] | None = None
        self._message_counter: int = 0
        self._current_url: str = ""  # 当前使用的 URL

        # JSON-RPC 路由状态
        self._current_task_id: str | None = None
        self._current_session_id: str | None = None
        # 保存最近一次入站消息的 id（JSON-RPC id），用于响应中的 id 字段
        self._last_inbound_rpc_id: str | None = None

    # ---- Lifecycle -------------------------------------------------------

    async def start(self) -> None:
        """启动频道：建立 WebSocket 连接，启动心跳和接收循环。"""
        self._running = True
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        await self._connect_and_receive()

    async def stop(self) -> None:
        """停止频道：关闭 WebSocket，取消所有任务。"""
        self._running = False
        if self._cleanup_task is not None:
            self._cleanup_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._cleanup_task
            self._cleanup_task = None
        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._heartbeat_task
            self._heartbeat_task = None
        if self._recv_task is not None:
            self._recv_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._recv_task
            self._recv_task = None
        await self._close_ws()

    # ---- Send -------------------------------------------------------------

    async def send(self, msg: OutboundMessage) -> None:
        """发送最终回复到小艺 A2A 平台（artifact-update + final=true）。

        Args:
            msg: 要发送的出站消息。
        """
        await self._send_artifact_update(
            session_id=msg.chat_id,
            text=msg.content,
            is_final=True,
            append=True,
        )

    async def send_delta(
        self, chat_id: str, delta: str, metadata: dict[str, Any] | None = None
    ) -> None:
        """发送流式文本块到小艺 A2A 平台（artifact-update + final=false）。

        Args:
            chat_id: 会话 ID。
            delta: 增量文本内容。
            metadata: 附加元数据。
        """
        await self._send_artifact_update(
            session_id=chat_id,
            text=delta,
            is_final=False,
            append=True,
        )

    # ---- Internal: Connection --------------------------------------------

    async def _connect_and_receive(self) -> None:
        """建立连接并进入接收循环，支持双服务器容错，断线时自动重连。"""
        urls = [url for url in (self.config.ws_url1, self.config.ws_url2) if url]

        while self._running:
            connected = False
            for url in urls:
                if not self._running:
                    break
                try:
                    await self._connect_and_auth(url)
                    connected = True
                    self._current_url = url
                    self._reconnect_count = 0
                    self.logger.info("Connected and authenticated to Xiaoyi A2A: %s", url)
                    break
                except asyncio.CancelledError:
                    raise
                except Exception:
                    self.logger.warning("Xiaoyi A2A connection to %s failed", url)
                    await self._close_ws()

            if not connected:
                self.logger.warning("Xiaoyi A2A: all URLs failed, will retry after backoff")
                await self._close_ws()
                if self._running:
                    await self._reconnect()
                continue

            try:
                # 进入接收循环（阻塞直到连接断开）
                await self._run_loops()
            except asyncio.CancelledError:
                break
            except Exception:
                self.logger.exception("Xiaoyi A2A connection error")
            finally:
                if self._heartbeat_task is not None:
                    self._heartbeat_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await self._heartbeat_task
                    self._heartbeat_task = None
                if self._recv_task is not None:
                    self._recv_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await self._recv_task
                    self._recv_task = None
                await self._close_ws()

            if self._running:
                await self._reconnect()

    async def _connect_and_auth(self, url: str) -> None:
        """建立 WebSocket 连接并通过 HTTP Headers 鉴权。

        鉴权通过 WebSocket 握手时的 HTTP Headers 传递：
        - x-access-key: Access Key
        - x-sign: Base64(HMAC-SHA256(SK, timestamp))
        - x-ts: 时间戳（毫秒）
        - x-agent-id: Agent ID

        Args:
            url: WebSocket URL。
        """
        ts = int(time_module.time() * 1000)
        signature = _build_auth_signature(
            self.config.ak or "",
            self.config.sk or "",
            ts,
        )

        extra_headers = {
            "x-access-key": self.config.ak or "",
            "x-sign": signature,
            "x-ts": str(ts),
            "x-agent-id": self.config.agent_id or "",
        }

        self.logger.info("Connecting to Xiaoyi A2A: %s (agent_id=%s)", url, self.config.agent_id)
        self._ws = await websockets.connect(
            url,
            additional_headers=extra_headers,
            ping_interval=None,
            close_timeout=5,
            open_timeout=self.config.connection_timeout,
        )

    async def _run_loops(self) -> None:
        """启动心跳和接收循环，等待任一结束。"""
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        self._recv_task = asyncio.create_task(self._recv_loop())

        # 等待任一任务完成（通常 recv_loop 在断线时返回）
        done, pending = await asyncio.wait(
            [self._recv_task, self._heartbeat_task],
            return_when=asyncio.FIRST_COMPLETED,
        )

        for task in pending:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

        # 检查是否有异常
        for task in done:
            exc = task.exception()
            if exc and not isinstance(exc, asyncio.CancelledError):
                self.logger.error("Xiaoyi A2A task failed: %s", exc)

    async def _close_ws(self) -> None:
        """安全关闭 WebSocket 连接。"""
        async with self._ws_lock:
            if self._ws is not None:
                with suppress(Exception):
                    await self._ws.close()
                self._ws = None

    async def _reconnect(self) -> None:
        """指数退避重连。"""
        delay = min(
            self.config.reconnect_initial_delay * (2 ** self._reconnect_count),
            self.config.reconnect_max_delay,
        )
        self._reconnect_count += 1
        self.logger.warning(
            "Xiaoyi A2A reconnecting in %.1fs (attempt %d)", delay, self._reconnect_count
        )
        await asyncio.sleep(delay)

    # ---- Internal: Loops --------------------------------------------------

    async def _recv_loop(self) -> None:
        """接收 WebSocket 消息循环（JSON-RPC 2.0 协议）。"""
        while self._running and self._ws is not None:
            try:
                raw = await asyncio.wait_for(
                    self._ws.recv(),
                    timeout=self.config.heartbeat_interval + self.config.heartbeat_timeout,
                )
            except asyncio.TimeoutError:
                # 心跳超时，发送 WS ping 检查连接
                try:
                    pong_waiter = await self._ws.ping()
                    await asyncio.wait_for(pong_waiter, timeout=5)
                except Exception:
                    self.logger.warning("Xiaoyi A2A ping/pong failed, reconnecting")
                    break
                continue
            except asyncio.CancelledError:
                break
            except Exception:
                self.logger.exception("Xiaoyi A2A recv error")
                break

            if raw is None:
                self.logger.info("Xiaoyi A2A connection closed by server")
                break

            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                self.logger.warning("Invalid JSON from Xiaoyi A2A: %s", raw[:200])
                continue

            msg_type = data.get("msgType", data.get("type", ""))

            # 服务端心跳请求 → 回复 heartbeat
            if msg_type == "heartbeat":
                pong_frame = {
                    "msgType": "heartbeat",
                    "agentId": self.config.agent_id,
                    "timestamp": int(time_module.time() * 1000),
                }
                await self._ws_send_json(pong_frame)
                continue

            # 服务端 ping → 回复 pong
            if msg_type == "ping":
                pong_frame = {"type": "pong", "timestamp": int(time_module.time())}
                await self._ws_send_json(pong_frame)
                continue

            if msg_type == "error":
                self.logger.error("Xiaoyi A2A server error: %s", data.get("message", ""))
                continue

            # --- JSON-RPC 2.0 消息处理 ---

            # 验证 agentId（如果存在）
            if data.get("agentId") and data.get("agentId") != self.config.agent_id:
                self.logger.debug("Mismatched agentId: %s, discarding", data.get("agentId"))
                continue

            method = data.get("method", "")

            # clearContext → 处理会话清理
            if method == "clearContext":
                await self._handle_clear_context(data)
                continue

            # tasks/cancel → 处理任务取消
            if method == "tasks/cancel":
                await self._handle_tasks_cancel(data)
                continue

            # message/stream → A2A 请求消息
            if self._is_a2a_request_message(data):
                await self._handle_inbound(data)
                continue

            self.logger.debug("Unrecognized A2A message: %s", json.dumps(data, ensure_ascii=False)[:200])

    async def _heartbeat_loop(self) -> None:
        """心跳循环，每隔 heartbeat_interval 秒发送应用层 heartbeat + WS ping。"""
        while self._running and self._ws is not None:
            await asyncio.sleep(self.config.heartbeat_interval)
            if not self._running:
                break

            # 1. 发送应用层 heartbeat 消息
            heartbeat_frame = {
                "msgType": "heartbeat",
                "agentId": self.config.agent_id,
                "timestamp": int(time_module.time() * 1000),
            }
            try:
                await self._ws_send_json(heartbeat_frame)
            except Exception:
                self.logger.warning("Xiaoyi A2A heartbeat send failed")
                break

            # 2. 同时发送协议层 WS ping
            try:
                pong_waiter = await self._ws.ping()
                await asyncio.wait_for(pong_waiter, timeout=self.config.heartbeat_timeout)
            except Exception:
                self.logger.warning("Xiaoyi A2A WS ping failed")
                break

    async def _cleanup_loop(self) -> None:
        """定期清理过期会话。"""
        while self._running:
            await asyncio.sleep(60)
            now = time_module.monotonic()
            expired = [
                sid
                for sid, last in self._session_last_seen.items()
                if now - last > self.config.session_keepalive
            ]
            for sid in expired:
                self._session_last_seen.pop(sid, None)

    # ---- Internal: Message Handling --------------------------------------

    @staticmethod
    def _is_a2a_request_message(data: dict[str, Any]) -> bool:
        """判断是否为 A2A 请求消息（JSON-RPC 2.0 message/stream）。

        对齐 npm 源码 isA2ARequestMessage 条件：
        - data.jsonrpc === "2.0"
        - data.method === "message/stream"
        - data.params.id 存在
        - data.params.message.role 存在
        - data.params.message.parts 是数组
        - sessionId 在 params 或顶层
        """
        try:
            return (
                isinstance(data, dict)
                and isinstance(data.get("agentId"), str)
                and data.get("jsonrpc") == "2.0"
                and isinstance(data.get("id"), str)
                and data.get("method") == "message/stream"
                and isinstance(data.get("params"), dict)
                and isinstance(data["params"].get("id"), str)
                and (
                    isinstance(data["params"].get("sessionId"), str)
                    or isinstance(data.get("sessionId"), str)
                )
                and isinstance(data["params"].get("message"), dict)
                and isinstance(data["params"]["message"].get("role"), str)
                and isinstance(data["params"]["message"].get("parts"), list)
            )
        except Exception:
            return False

    @staticmethod
    def _extract_text_from_parts(parts: list[dict[str, Any]]) -> str:
        """从 JSON-RPC message.parts 中提取文本内容。

        Args:
            parts: message.parts 列表，每项可能是 {"kind":"text","text":"..."}
                   或 {"kind":"file","file":{...}}

        Returns:
            拼接后的文本字符串。
        """
        texts: list[str] = []
        for part in parts:
            if isinstance(part, dict):
                kind = part.get("kind", "")
                if kind == "text" and part.get("text"):
                    texts.append(part["text"])
                elif kind == "file":
                    # 文件引用：提取文件名作为提示
                    file_info = part.get("file", {})
                    if isinstance(file_info, dict) and file_info.get("name"):
                        texts.append(f"[文件: {file_info['name']}]")
        return "\n".join(texts)

    async def _handle_inbound(self, data: dict[str, Any]) -> None:
        """处理 JSON-RPC 2.0 message/stream 入站消息。

        Args:
            data: 原始 JSON-RPC 消息字典。
        """
        params = data.get("params", {})
        task_id = params.get("id", "")
        session_id = params.get("sessionId", data.get("sessionId", ""))
        message = params.get("message", {})
        parts = message.get("parts", [])
        rpc_id = data.get("id", "")

        text = self._extract_text_from_parts(parts).strip()
        chat_id = session_id or task_id or "unknown"

        # 保存路由状态
        self._current_task_id = task_id or None
        self._current_session_id = session_id or None
        self._last_inbound_rpc_id = rpc_id or None

        if not text:
            self.logger.debug("Empty text in A2A JSON-RPC message, task_id=%s", task_id)
            return

        # 权限检查
        sender_id = session_id  # 在小艺 A2A 中，session 代表用户
        if not self.is_allowed(sender_id):
            self.logger.warning("Access denied for A2A sender: %s", sender_id)
            error_jsonrpc = self._build_jsonrpc_error(
                rpc_id=rpc_id or task_id,
                code=-1,
                message="抱歉，您没有权限使用此服务。",
            )
            await self._wrap_and_send(
                session_id=chat_id,
                task_id=task_id or rpc_id,
                msg_detail=error_jsonrpc,
            )
            return

        # 更新会话时间
        self._session_last_seen[chat_id] = time_module.monotonic()

        self.logger.info(
            "Xiaoyi A2A inbound: session=%s task=%s text=%s",
            session_id, task_id, text[:80],
        )

        # 发送中间状态通知
        await self._send_status_update(
            session_id=chat_id,
            task_id=task_id or rpc_id,
            message="处理中...",
        )

        await self._handle_message(
            sender_id=sender_id,
            chat_id=chat_id,
            content=text,
            metadata={
                "xiaoyi_a2a_session_id": session_id,
                "xiaoyi_a2a_task_id": task_id,
                "xiaoyi_a2a_rpc_id": rpc_id,
            },
            session_key=session_id or chat_id,
            is_dm=True,
        )

    async def _handle_clear_context(self, data: dict[str, Any]) -> None:
        """处理 clearContext 请求。

        Args:
            data: 原始 JSON-RPC 消息字典。
        """
        session_id = data.get("sessionId", "")
        rpc_id = data.get("id", "")
        self.logger.info("Xiaoyi A2A clearContext: session=%s, id=%s", session_id, rpc_id)

        # 发送成功响应
        clear_response = {
            "jsonrpc": "2.0",
            "id": rpc_id,
            "result": {
                "status": {"state": "cleared"},
            },
        }
        await self._wrap_and_send(
            session_id=session_id,
            task_id=rpc_id,
            msg_detail=clear_response,
        )

    async def _handle_tasks_cancel(self, data: dict[str, Any]) -> None:
        """处理 tasks/cancel 请求。

        Args:
            data: 原始 JSON-RPC 消息字典。
        """
        session_id = data.get("sessionId", "")
        task_id = data.get("taskId", data.get("id", ""))
        rpc_id = data.get("id", "")
        self.logger.info("Xiaoyi A2A tasks/cancel: session=%s, task=%s", session_id, task_id)

        # 发送成功响应
        cancel_response = {
            "jsonrpc": "2.0",
            "id": rpc_id,
            "result": {
                "id": rpc_id,
                "status": {"state": "canceled"},
            },
        }
        await self._wrap_and_send(
            session_id=session_id,
            task_id=rpc_id,
            msg_detail=cancel_response,
        )

    # ---- Internal: Outbound Builders -------------------------------------

    async def _send_artifact_update(
        self,
        session_id: str,
        text: str,
        is_final: bool = False,
        append: bool = True,
    ) -> None:
        """发送 artifact-update 消息（正文内容）。

        Args:
            session_id: 会话 ID。
            text: 文本内容（当 is_final=True 时可为空）。
            append: 是否追加模式。
            is_final: 是否为最后一条消息。
        """
        artifact_id = f"artifact_{uuid.uuid4().hex[:12]}"
        task_id = self._current_task_id or ""
        rpc_id = self._last_inbound_rpc_id or task_id

        artifact_event = {
            "taskId": task_id,
            "kind": "artifact-update",
            "append": append,
            "lastChunk": is_final,
            "final": is_final,
            "artifact": {
                "artifactId": artifact_id,
                "parts": [
                    {"kind": "text", "text": text},
                ],
            },
        }

        jsonrpc_response = {
            "jsonrpc": "2.0",
            "id": rpc_id,
            "result": artifact_event,
        }

        await self._wrap_and_send(
            session_id=session_id or (self._current_session_id or ""),
            task_id=task_id,
            msg_detail=jsonrpc_response,
        )

    async def _send_status_update(
        self,
        session_id: str,
        task_id: str,
        message: str,
    ) -> None:
        """发送 status-update 消息（中间状态通知）。

        Args:
            session_id: 会话 ID。
            task_id: 任务 ID。
            message: 状态文本。
        """
        status_id = f"status_{int(time_module.time() * 1000)}_{uuid.uuid4().hex[:8]}"

        status_event = {
            "taskId": task_id,
            "kind": "status-update",
            "final": False,
            "status": {
                "message": {
                    "role": "agent",
                    "parts": [
                        {"kind": "text", "text": message},
                    ],
                },
                "state": "working",
            },
        }

        jsonrpc_response = {
            "jsonrpc": "2.0",
            "id": status_id,
            "result": status_event,
        }

        await self._wrap_and_send(
            session_id=session_id,
            task_id=task_id,
            msg_detail=jsonrpc_response,
        )

    def _build_jsonrpc_error(
        self,
        rpc_id: str,
        code: int = -1,
        message: str = "Unknown error",
    ) -> dict[str, Any]:
        """构建 JSON-RPC 错误响应。

        Args:
            rpc_id: JSON-RPC 消息 ID。
            code: 错误码。
            message: 错误文本。

        Returns:
            JSON-RPC 错误响应字典。
        """
        return {
            "jsonrpc": "2.0",
            "id": rpc_id,
            "error": {
                "code": code,
                "message": message,
            },
        }

    async def _wrap_and_send(
        self,
        session_id: str,
        task_id: str,
        msg_detail: dict[str, Any],
    ) -> None:
        """用 agent_response 外层包裹 JSON-RPC 响应并发送。

        出站外层格式::

            {
                "msgType": "agent_response",
                "agentId": "...",
                "sessionId": "...",
                "taskId": "...",
                "msgDetail": "{...json-rpc-response-string...}"
            }

        Args:
            session_id: 会话 ID。
            task_id: 任务 ID。
            msg_detail: JSON-RPC 响应字典（会被 JSON 序列化到 msgDetail 字段）。
        """
        wrapper = {
            "msgType": "agent_response",
            "agentId": self.config.agent_id,
            "sessionId": session_id,
            "taskId": task_id,
            "msgDetail": json.dumps(msg_detail, ensure_ascii=False),
        }
        await self._ws_send_json(wrapper)

    async def _ws_send_json(self, data: dict[str, Any]) -> None:
        """通过 WebSocket 发送 JSON 数据。

        Args:
            data: 要发送的字典数据。
        """
        async with self._ws_lock:
            if self._ws is not None:
                raw = json.dumps(data, ensure_ascii=False)
                await self._ws.send(raw)
