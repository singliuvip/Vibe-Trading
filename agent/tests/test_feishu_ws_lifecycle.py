"""WS lifecycle regression tests for the Feishu channel.

Covers:
- ``stop()`` terminating the WebSocket thread and reclaiming the connection
- ``start()`` being idempotent (never two live WS threads / connections)
- ``_on_message_sync`` scheduling failures being observable (logged, not silent)
- the receive watchdog logging a stalled pipeline

The lark-oapi SDK is fully mocked here: no real Feishu network is contacted.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import pytest

from src.channels import feishu as feishu_module
from src.channels.bus.queue import MessageBus
from src.channels.feishu import FeishuChannel, FeishuConfig


async def _block_forever() -> None:
    """Never-completing coroutine that mimics lark's ``_select()``."""
    while True:
        await asyncio.sleep(3600)


class FakeWsClient:
    """Stand-in for ``lark.ws.Client`` whose ``start()`` blocks like the real one.

    Blocks on the event loop that ``run_ws`` patched into the module-level
    ``lark_oapi.ws.client.loop``.  Stopping that loop (as ``stop()`` does)
    raises ``RuntimeError`` out of ``run_until_complete``, exactly like the SDK.
    """

    _active: list[FakeWsClient] = []
    _all: list[FakeWsClient] = []

    def __init__(self) -> None:
        self.start_calls = 0
        self.active = False
        self.exited = False
        FakeWsClient._all.append(self)

    def start(self) -> None:
        import lark_oapi.ws.client as _lark_ws_client

        self.start_calls += 1
        self.active = True
        FakeWsClient._active.append(self)
        loop = _lark_ws_client.loop
        try:
            loop.run_until_complete(_block_forever())
        except RuntimeError:
            # Event loop stopped -> connection torn down (SDK reconnect would
            # normally take over, but the run_ws loop is shutting down too).
            pass
        finally:
            self.active = False
            self.exited = True
            FakeWsClient._active.remove(self)

    @classmethod
    def reset(cls) -> None:
        cls._active.clear()
        cls._all.clear()


class _FakeSendClient:
    pass


class _FakeClientBuilder:
    def app_id(self, value: str) -> _FakeClientBuilder:
        del value
        return self

    def app_secret(self, value: str) -> _FakeClientBuilder:
        del value
        return self

    def domain(self, value: str) -> _FakeClientBuilder:
        del value
        return self

    def log_level(self, value: Any) -> _FakeClientBuilder:
        del value
        return self

    def build(self) -> _FakeSendClient:
        return _FakeSendClient()


class _FakeEventHandlerBuilder:
    @staticmethod
    def builder(encrypt_key: str, verification_token: str) -> _FakeEventHandlerBuilder:
        del encrypt_key, verification_token
        return _FakeEventHandlerBuilder()

    def register_p2_im_message_receive_v1(self, handler: Any) -> _FakeEventHandlerBuilder:
        del handler
        return self

    def build(self) -> _FakeEventHandlerBuilder:
        return self


class _FakeLarkClient:
    @staticmethod
    def builder() -> _FakeClientBuilder:
        return _FakeClientBuilder()


class _FakeLark:
    LogLevel = type("LogLevel", (), {"INFO": 1})

    class ws:
        @staticmethod
        def Client(*args: Any, **kwargs: Any) -> FakeWsClient:
            del args, kwargs
            return FakeWsClient()

    Client = _FakeLarkClient
    EventDispatcherHandler = _FakeEventHandlerBuilder


def _make_channel() -> FeishuChannel:
    config = FeishuConfig(app_id="test-app-id", app_secret="test-app-secret")
    return FeishuChannel(config, MessageBus())


def _patch_feishu(monkeypatch: pytest.MonkeyPatch, channel: FeishuChannel) -> None:
    """Replace the SDK runtime and network-touching helpers with fakes."""
    monkeypatch.setattr(
        feishu_module,
        "_load_lark_runtime",
        lambda: (_FakeLark, "https://open.feishu.cn", "https://open.larksuite.com"),
    )
    monkeypatch.setattr(channel, "_fetch_bot_open_id", lambda: None)


async def _wait_until(predicate: Any, timeout: float = 20.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.01)
    return False


def _ws_connected(channel: FeishuChannel) -> bool:
    """True once the WS thread is alive AND the fake connection is established.

    The WS thread stays alive while it imports the (slow) real lark_oapi
    module inside ``run_ws``, so checking thread liveness alone is not enough.
    """
    return (
        channel._ws_thread is not None
        and channel._ws_thread.is_alive()
        and len(FakeWsClient._active) == 1
    )


def test_feishu_stop_terminates_ws_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    """stop() breaks the blocking SDK call, joins the WS thread and reclaims the loop."""
    FakeWsClient.reset()

    async def scenario() -> None:
        channel = _make_channel()
        _patch_feishu(monkeypatch, channel)
        task = asyncio.create_task(channel.start())
        try:
            assert await _wait_until(lambda: _ws_connected(channel))
            thread = channel._ws_thread
            assert thread is not None and thread.is_alive()
            assert len(FakeWsClient._active) == 1  # exactly one live connection

            await channel.stop()

            assert await _wait_until(lambda: not thread.is_alive())
            assert len(FakeWsClient._active) == 0  # connection torn down
            assert channel._ws_loop is None  # WS loop reclaimed
        finally:
            await channel.stop()
            if not task.done():
                await task

    asyncio.run(scenario())


def test_feishu_start_idempotent_no_double_ws(monkeypatch: pytest.MonkeyPatch) -> None:
    """Re-entrant start() is idempotent; stop/start cycles never run two WS threads."""
    FakeWsClient.reset()

    async def scenario() -> None:
        channel = _make_channel()
        _patch_feishu(monkeypatch, channel)

        task1 = asyncio.create_task(channel.start())
        try:
            assert await _wait_until(lambda: _ws_connected(channel))
            thread1 = channel._ws_thread
            assert len(FakeWsClient._active) == 1

            # Re-entrant start() while running -> idempotent, same thread.
            await channel.start()
            assert channel._ws_thread is thread1
            assert thread1 is not None and thread1.is_alive()
            assert len(FakeWsClient._active) == 1

            # Stop fully reaps the thread + connection.
            await channel.stop()
            assert await _wait_until(lambda: not thread1.is_alive())
            assert len(FakeWsClient._active) == 0
            await task1
        finally:
            await channel.stop()
            if not task1.done():
                await task1

        # Restart -> fresh single thread; the old one is gone.
        task2 = asyncio.create_task(channel.start())
        try:
            assert await _wait_until(lambda: _ws_connected(channel))
            thread2 = channel._ws_thread
            assert thread2 is not thread1
            assert not thread1.is_alive()
            assert len(FakeWsClient._active) == 1

            await channel.stop()
            assert await _wait_until(lambda: not thread2.is_alive())
            assert len(FakeWsClient._active) == 0
            await task2
        finally:
            await channel.stop()
            if not task2.done():
                await task2

    asyncio.run(scenario())


def test_on_message_sync_loop_drop_logs(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    """Scheduling failures in _on_message_sync are logged, never silent."""
    async def scenario() -> None:
        async def _stub(data: Any) -> None:
            del data

        channel = _make_channel()
        monkeypatch.setattr(channel, "_on_message", _stub)
        logger_name = "src.channels.base.feishu"

        # 1) Loop not initialized -> warning, not silent.
        channel._loop = None
        caplog.clear()
        with caplog.at_level(logging.WARNING, logger=logger_name):
            channel._on_message_sync({"type": "test"})
        assert any("main event loop not initialized" in r.message for r in caplog.records)

        # 2) Loop present but not running -> warning, not silent.
        idle_loop = asyncio.new_event_loop()
        channel._loop = idle_loop
        caplog.clear()
        with caplog.at_level(logging.WARNING, logger=logger_name):
            channel._on_message_sync({"type": "test"})
        assert any("main event loop not running" in r.message for r in caplog.records)
        idle_loop.close()

        # 3) Loop "running" but scheduling fails -> error, not silent.
        class _FakeRunningLoop:
            def is_running(self) -> bool:
                return True

            def call_soon_threadsafe(self, callback: Any, *args: Any) -> Any:
                del callback, args
                raise RuntimeError("event loop shutting down")

        channel._loop = _FakeRunningLoop()
        caplog.clear()
        with caplog.at_level(logging.ERROR, logger=logger_name):
            channel._on_message_sync({"type": "test"})
        assert any("failed to schedule feishu message" in r.message for r in caplog.records)

    asyncio.run(scenario())


def test_feishu_watchdog_logs_stalled_receive(caplog: pytest.LogCaptureFixture) -> None:
    """A WS connection with no receive_v1 inbound for too long is observable."""
    channel = _make_channel()
    channel._WS_WATCHDOG_SECONDS = 0.1
    channel._ws_last_receive_ts = time.monotonic() - 1.0  # stale

    with caplog.at_level(logging.WARNING, logger="src.channels.base.feishu"):
        channel._check_ws_watchdog()

    assert any("no receive_v1 inbound" in r.message for r in caplog.records)

    # Throttled: an immediate re-check within the same stall period stays quiet.
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="src.channels.base.feishu"):
        channel._check_ws_watchdog()
    assert not any("no receive_v1 inbound" in r.message for r in caplog.records)
