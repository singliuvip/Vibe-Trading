"""华为小艺 (Huawei Xiaoyi) 技能频道 — HTTP Webhook 适配器。

小艺技能开放平台允许开发者注册自定义技能（Skill），用户通过语音或文本
唤醒小艺后，技能平台将用户输入以 HTTP POST 请求发送到开发者配置的
Webhook URL，开发者处理并返回回复文本，小艺朗读/展示给用户。

架构::

    用户 → 小艺 App → 华为技能平台 → HTTP POST → XiaoyiChannel
        (webhook_url)                                       │
            ← HTTP Response ←  Agent Loop  ← MessageBus  ←┘

本频道以 HTTP 服务端模式运行，在 ``host:port/webhook_path`` 上监听
小艺技能平台的回调请求。你也可以通过 ``mode=api_server`` 将路由挂载
到主 FastAPI 服务器上（需要与 api_server.py 配合使用）。

小艺技能请求/响应协议（根据华为文档）::

    POST /webhook  Content-Type: application/json

    Request:
    {
        "session": {"session_id": "...", "new_session": true},
        "request": {"type": "text", "text": "用户输入"},
        "original": { ... }
    }

    Response:
    {
        "response": {"type": "text", "text": "回复内容"},
        "version": "1.0"
    }
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import Any

import httpx

from pydantic import Field, field_validator
from pydantic import BaseModel

from src.channels.bus.events import InboundMessage, OutboundMessage
from src.channels.bus.queue import MessageBus
from src.channels.base import BaseChannel
from src.channels.utils import get_runtime_subdir, safe_filename

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 小艺技能平台的典型请求/响应 Key
# ---------------------------------------------------------------------------
# 这些 Key 是小艺技能开放平台的约定字段名。
# 如果华为后续版本变更了字段路径，用户可以通过 config 中的
# request_text_path / response_text_path 覆盖。

_DEFAULT_REQUEST_TEXT_PATH = ("request", "text")
_DEFAULT_SESSION_ID_PATH = ("session", "session_id")
_DEFAULT_SENDER_ID_PATH = ("session", "user_id")
_DEFAULT_RESPONSE_TEXT_PATH = ("response", "text")

# ---------------------------------------------------------------------------
# 配置模型
# ---------------------------------------------------------------------------


class XiaoyiConfig(BaseModel):
    """华为小艺技能频道配置。

    Attributes:
        enabled: 是否启用本频道。
        mode: 运行模式。
            ``"standalone"`` — 启动独立 HTTP 服务监听 webhook。
            ``"api_server"`` — 挂载到主 FastAPI 服务器（需外部注册路由）。
        host: 独立模式下监听的主机地址。
        port: 独立模式下监听的端口。
        webhook_path: Webhook URL 路径（独立模式或 api_server 模式均使用）。
        app_id: 小艺技能平台分配的 App ID，用于回调鉴权。
        app_secret: 小艺技能平台分配的 App Secret，用于 HMAC 签名验证。
        verify_signature: 是否验证小艺回调请求的 HMAC 签名。
        allow_from: 允许的发送者 ID 列表。"*" 表示全部允许。
        streaming: 是否启用流式回复（小艺技能平台可能不支持，默认 False）。
        request_text_path: 请求 JSON 中用户文本的字段路径，如
            ``["request", "text"]``。
        request_session_id_path: 请求 JSON 中会话 ID 的字段路径。
        request_sender_id_path: 请求 JSON 中发送者 ID 的字段路径。
        response_text_path: 响应 JSON 中回复文本的字段路径。
        response_type: 响应类型字段值，如 ``"text"``。
        session_keepalive: 会话保持时间（秒），超过此时间的会话将被重置。
        max_message_bytes: 单条消息最大字节数。
        ssl_certfile: 可选 SSL 证书文件路径（独立模式 HTTPS）。
        ssl_keyfile: 可选 SSL 密钥文件路径。
    """

    enabled: bool = False
    mode: str = "standalone"  # "standalone" | "api_server"
    host: str = "0.0.0.0"
    port: int = 9800
    webhook_path: str = "/xiaoyi/webhook"
    app_id: str = ""
    app_secret: str = ""
    verify_signature: bool = True
    allow_from: list[str] = Field(default_factory=lambda: ["*"])
    streaming: bool = False
    request_text_path: list[str] = Field(
        default_factory=lambda: list(_DEFAULT_REQUEST_TEXT_PATH)
    )
    request_session_id_path: list[str] = Field(
        default_factory=lambda: list(_DEFAULT_SESSION_ID_PATH)
    )
    request_sender_id_path: list[str] = Field(
        default_factory=lambda: list(_DEFAULT_SENDER_ID_PATH)
    )
    response_text_path: list[str] = Field(
        default_factory=lambda: list(_DEFAULT_RESPONSE_TEXT_PATH)
    )
    response_type: str = "text"
    session_keepalive: int = 300
    max_message_bytes: int = 65_536
    ssl_certfile: str = ""
    ssl_keyfile: str = ""

    @field_validator("webhook_path")
    @classmethod
    def webhook_path_must_start_with_slash(cls, value: str) -> str:
        if not value.startswith("/"):
            raise ValueError('webhook_path must start with "/"')
        return value

    @field_validator("mode")
    @classmethod
    def mode_must_be_valid(cls, value: str) -> str:
        if value not in ("standalone", "api_server"):
            raise ValueError("mode must be 'standalone' or 'api_server'")
        return value


# ---------------------------------------------------------------------------
# JSON 深度取值/设值工具
# ---------------------------------------------------------------------------


def _deep_get(d: dict[str, Any], path: list[str], default: Any = "") -> Any:
    """从嵌套字典中按路径取值。"""
    current = d
    for key in path:
        if not isinstance(current, dict):
            return default
        current = current.get(key)
        if current is None:
            return default
    return current


def _deep_set(d: dict[str, Any], path: list[str], value: Any) -> None:
    """在嵌套字典中按路径设值。"""
    current = d
    for key in path[:-1]:
        if key not in current or not isinstance(current[key], dict):
            current[key] = {}
        current = current[key]
    current[path[-1]] = value


# ---------------------------------------------------------------------------
# 签名验证
# ---------------------------------------------------------------------------


def _verify_hmac(
    body: bytes,
    signature: str,
    secret: str,
) -> bool:
    """验证小艺回调请求的 HMAC-SHA256 签名。

    Args:
        body: 原始请求体字节。
        signature: 请求头中的签名字符串。
        secret: App Secret。

    Returns:
        True 表示签名有效。
    """
    if not secret or not signature:
        return False
    expected = hmac.new(
        secret.encode("utf-8"),
        body,
        "sha256",
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


# ---------------------------------------------------------------------------
# 频道实现
# ---------------------------------------------------------------------------


class XiaoyiChannel(BaseChannel):
    """华为小艺技能频道。

    以 HTTP Webhook 方式接收小艺技能平台的用户消息，转发给 Agent 处理，
    并将回复同步返回给小艺技能平台。
    """

    name = "xiaoyi"
    display_name = "华为小艺"

    # 小艺技能平台目前不支持流式输出
    send_progress = False
    send_tool_hints = False
    show_reasoning = False

    @classmethod
    def default_config(cls) -> dict[str, Any]:
        return XiaoyiConfig().model_dump(by_alias=True)

    def __init__(self, config: Any, bus: MessageBus) -> None:
        if isinstance(config, dict):
            config = XiaoyiConfig.model_validate(config)
        super().__init__(config, bus)
        self.config: XiaoyiConfig = config
        self._http_server: asyncio.Server | None = None
        self._pending_replies: dict[str, asyncio.Future[str]] = {}
        self._session_last_seen: dict[str, float] = {}
        self._cleanup_task: asyncio.Task[None] | None = None

    # ---- Lifecycle -------------------------------------------------------

    async def start(self) -> None:
        """启动频道。

        standalone 模式：启动独立 HTTP 服务监听 webhook。
        api_server 模式：仅启动会话清理任务，路由由外部注册。
        """
        self._running = True
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())

        if self.config.mode == "api_server":
            self.logger.info(
                "Xiaoyi channel running in api_server mode — "
                "register webhook route %s on the FastAPI server manually",
                self.config.webhook_path,
            )
            while self._running:
                await asyncio.sleep(1)
            return

        # standalone 模式：启动 HTTP 服务
        await self._start_http_server()

    async def stop(self) -> None:
        """停止频道。"""
        self._running = False
        if self._cleanup_task is not None:
            self._cleanup_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._cleanup_task
            self._cleanup_task = None
        if self._http_server is not None:
            self._http_server.close()
            with suppress(asyncio.CancelledError):
                await self._http_server.wait_closed()
            self._http_server = None

    async def send(self, msg: OutboundMessage) -> None:
        """发送回复到小艺。

        注意：小艺技能平台使用同步 HTTP 回调模式，回复通过 HTTP Response
        返回。因此 ``send()`` 主要用于异步推送场景（如定时消息），
        而 Webhook 请求的回复通过 _handle_webhook() 直接返回。
        """
        self.logger.warning(
            "Async send to Xiaoyi not supported yet — "
            "message to %s: %s",
            msg.chat_id,
            msg.content[:50] if msg.content else "(empty)",
        )

    # ---- HTTP Server -----------------------------------------------------

    async def _start_http_server(self) -> None:
        """启动独立 HTTP 服务。"""
        import asyncio

        # 使用 asyncio.start_server 实现轻量 HTTP
        self._http_server = await asyncio.start_server(
            self._handle_http_connection,
            host=self.config.host,
            port=self.config.port,
            ssl=self._build_ssl_context() if (self.config.ssl_certfile and self.config.ssl_keyfile) else None,
        )

        addr = self._http_server.sockets[0].getsockname()
        self.logger.info(
            "Xiaoyi webhook listening on http://%s:%s%s",
            addr[0],
            addr[1],
            self.config.webhook_path,
        )

        async with self._http_server:
            await self._http_server.serve_forever()

    def _build_ssl_context(self) -> Any:
        """构建 SSL 上下文（用于 HTTPS）。"""
        import ssl

        ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
        ctx.load_cert_chain(self.config.ssl_certfile, self.config.ssl_keyfile)
        return ctx

    async def _handle_http_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """处理一个 HTTP 连接。"""
        try:
            request_line = await reader.readline()
            if not request_line:
                writer.close()
                return

            request_str = request_line.decode("utf-8", errors="replace").strip()
            parts = request_str.split(" ")
            if len(parts) < 2:
                writer.close()
                return

            method = parts[0]
            path = parts[1]

            # 读取请求头
            headers: dict[str, str] = {}
            content_length = 0
            while True:
                header_line = await reader.readline()
                header_str = header_line.decode("utf-8", errors="replace").strip()
                if not header_str:
                    break
                if ":" in header_str:
                    key, value = header_str.split(":", 1)
                    headers[key.strip().lower()] = value.strip()
                    if key.strip().lower() == "content-length":
                        try:
                            content_length = int(value.strip())
                        except ValueError:
                            content_length = 0

            # 读取请求体
            body = b""
            if content_length > 0:
                body = await reader.readexactly(content_length)

            # 只处理 POST 到 webhook_path
            if method == "POST" and path == self.config.webhook_path:
                response_body = await self._handle_webhook(body, headers)
            else:
                response_body = json.dumps({"error": "not found"}).encode("utf-8")

            # 写 HTTP 响应
            status_line = "HTTP/1.1 200 OK\r\n"
            resp_headers = (
                "Content-Type: application/json; charset=utf-8\r\n"
                f"Content-Length: {len(response_body)}\r\n"
                "Connection: close\r\n"
                "\r\n"
            )
            writer.write(status_line.encode("utf-8"))
            writer.write(resp_headers.encode("utf-8"))
            writer.write(response_body)

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.logger.exception("HTTP handler error: %s", exc)
            try:
                writer.write(b"HTTP/1.1 500 Internal Server Error\r\n\r\n")
            except Exception:
                pass
        finally:
            try:
                writer.close()
            except Exception:
                pass

    # ---- Webhook Handler -------------------------------------------------

    async def _handle_webhook(self, body: bytes, headers: dict[str, str]) -> bytes:
        """处理小艺技能平台的 Webhook 回调。

        Args:
            body: 原始请求体。
            headers: 请求头字典。

        Returns:
            要返回的 JSON 响应体。
        """
        # 1. 签名验证
        if self.config.verify_signature and self.config.app_secret:
            signature = headers.get("x-huawei-signature") or headers.get("signature") or ""
            if not _verify_hmac(body, signature, self.config.app_secret):
                self.logger.warning("HMAC signature verification failed")
                return json.dumps({
                    "response": {"type": "text", "text": "签名验证失败"},
                }, ensure_ascii=False).encode("utf-8")

        # 2. 解析请求 JSON
        try:
            data = json.loads(body.decode("utf-8"))
        except json.JSONDecodeError as e:
            self.logger.warning("Invalid JSON body: %s", e)
            return json.dumps({
                "response": {"type": "text", "text": "请求格式错误"},
            }, ensure_ascii=False).encode("utf-8")

        # 3. 提取消息字段
        text = _deep_get(data, self.config.request_text_path, "")
        session_id = _deep_get(data, self.config.request_session_id_path, "")
        sender_id = _deep_get(data, self.config.request_sender_id_path, "unknown")
        chat_id = session_id or str(hash(body))

        if not text:
            self.logger.debug("Empty text in Xiaoyi request")
            return json.dumps({
                "response": {"type": "text", "text": ""},
            }, ensure_ascii=False).encode("utf-8")

        # 4. 权限检查
        if not self.is_allowed(sender_id):
            self.logger.warning("Access denied for sender: %s", sender_id)
            return json.dumps({
                "response": {"type": "text", "text": "抱歉，您没有权限使用此服务。"},
            }, ensure_ascii=False).encode("utf-8")

        # 5. 转发到消息总线
        text = text.strip()
        self.logger.info(
            "Xiaoyi webhook: sender=%s session=%s text=%s",
            sender_id, session_id, text[:80],
        )

        await self._handle_message(
            sender_id=sender_id,
            chat_id=chat_id,
            content=text,
            metadata={
                "xiaoyi_session_id": session_id,
                "xiaoyi_raw": data,
            },
            session_key=session_id or chat_id,
            is_dm=True,
        )

        # 6. 等待 Agent 回复（从总线消费）
        reply_text = await self._wait_for_reply(session_id or chat_id)

        # 7. 构建小艺响应
        response = {
            "response": {
                "type": self.config.response_type,
                "text": reply_text,
            },
            "version": "1.0",
        }
        return json.dumps(response, ensure_ascii=False).encode("utf-8")

    # ---- Reply Awaiting --------------------------------------------------

    async def _wait_for_reply(self, chat_key: str) -> str:
        """从出站队列中等待属于本会话的回复。

        Args:
            chat_key: 会话标识。

        Returns:
            回复文本。
        """
        timeout = 30.0  # 小艺技能平台通常有 5-30 秒超时限制
        deadline = asyncio.get_event_loop().time() + timeout

        while asyncio.get_event_loop().time() < deadline:
            try:
                msg = await asyncio.wait_for(
                    self.bus.consume_outbound(),
                    timeout=1.0,
                )
            except asyncio.TimeoutError:
                continue

            if msg.channel == self.name and msg.chat_id == chat_key:
                if msg.content:
                    return msg.content

            # 不匹配的消息放回队列
            await self.bus.publish_outbound(msg)

        return "抱歉，处理超时，请稍后重试。"

    # ---- Session Cleanup -------------------------------------------------

    async def _cleanup_loop(self) -> None:
        """定期清理过期会话。"""
        import time

        while self._running:
            await asyncio.sleep(60)
            now = time.monotonic()
            expired = [
                sid
                for sid, last in self._session_last_seen.items()
                if now - last > self.config.session_keepalive
            ]
            for sid in expired:
                self._session_last_seen.pop(sid, None)

    # ---- Public API: webhook route for api_server mode -------------------

    async def handle_webhook_request(self, body: bytes, headers: dict[str, str]) -> dict[str, Any]:
        """供外部 FastAPI 路由调用的 Webhook 处理接口。

        当 ``mode=api_server`` 时，在 ``api_server.py`` 中注册路由::

            @app.post(config.channels.xiaoyi.webhook_path)
            async def xiaoyi_webhook(request: Request):
                body = await request.body()
                headers = {k.lower(): v for k, v in request.headers.items()}
                result = await xiaoyi_channel.handle_webhook_request(body, headers)
                return result

        Returns:
            小艺技能平台格式的 JSON 响应字典。
        """
        response_bytes = await self._handle_webhook(body, headers)
        return json.loads(response_bytes.decode("utf-8"))
