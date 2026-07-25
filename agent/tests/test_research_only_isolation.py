"""Test research_only hard isolation at tool execution layer."""

import json
import pytest
from unittest.mock import Mock, MagicMock

from src.agent.loop import AgentLoop
from src.agent.tools import ToolRegistry, BaseTool
from src.core.invocation import InvocationContext


class MockTradingPlaceOrderTool(BaseTool):
    name = "trading_place_order"
    description = "Mock place order tool"
    is_readonly = False

    def execute(self, **kwargs):
        return json.dumps({"status": "ok", "order_id": "12345"})


class MockTradingCancelOrderTool(BaseTool):
    name = "trading_cancel_order"
    description = "Mock cancel order tool"
    is_readonly = False

    def execute(self, **kwargs):
        return json.dumps({"status": "ok", "cancelled": True})


class MockTradingAccountTool(BaseTool):
    name = "trading_account"
    description = "Mock account tool"
    is_readonly = True

    def execute(self, **kwargs):
        return json.dumps({"status": "ok", "balance": 10000})


def _make_loop(registry=None):
    """Create an AgentLoop with mocked dependencies."""
    reg = registry or ToolRegistry()
    loop = AgentLoop(registry=reg, llm=Mock(), memory=Mock())
    return loop


def _make_tool_call(name, arguments=None, call_id="call_1"):
    tc = Mock()
    tc.id = call_id
    tc.name = name
    tc.arguments = arguments or {}
    return tc


def _make_context():
    context = Mock()
    context.format_tool_result = Mock(side_effect=lambda tid, tname, content: {
        "role": "tool",
        "tool_call_id": tid,
        "name": tname,
        "content": content,
    })
    return context


class TestResearchOnlyBlocksWriteTools:
    """research_only=True should block broker write tools."""

    def test_blocks_trading_place_order(self):
        registry = ToolRegistry()
        registry.register(MockTradingPlaceOrderTool())
        loop = _make_loop(registry)
        loop._invocation_context = InvocationContext(research_only=True)

        tc = _make_tool_call("trading_place_order", {"symbol": "AAPL", "side": "buy", "quantity": 10})
        context = _make_context()
        messages = []
        trace = Mock()
        react_trace = []

        compact_requested, focus_topic = loop._process_tool_calls(
            [tc], context, messages, trace, react_trace, iteration=1
        )

        # Should be blocked — error message appended
        assert len(messages) == 1
        result = json.loads(messages[0]["content"])
        assert result["error_code"] == "research_only_violation"
        assert result["tool"] == "trading_place_order"
        assert result["status"] == "error"

        # Trace should record the block
        trace.write.assert_called_once()
        call_args = trace.write.call_args[0][0]
        assert call_args["type"] == "tool_blocked"
        assert call_args["reason"] == "research_only"

        # react_trace should also record
        assert len(react_trace) == 1
        assert react_trace[0]["type"] == "tool_blocked"

        # No compact requested
        assert compact_requested is False

    def test_blocks_trading_cancel_order(self):
        registry = ToolRegistry()
        registry.register(MockTradingCancelOrderTool())
        loop = _make_loop(registry)
        loop._invocation_context = InvocationContext(research_only=True)

        tc = _make_tool_call("trading_cancel_order", {"order_id": "12345"})
        context = _make_context()
        messages = []
        trace = Mock()
        react_trace = []

        loop._process_tool_calls([tc], context, messages, trace, react_trace, iteration=1)

        assert len(messages) == 1
        result = json.loads(messages[0]["content"])
        assert result["error_code"] == "research_only_violation"
        assert result["tool"] == "trading_cancel_order"

    def test_blocks_mcp_place_order(self):
        """MCP tools with place_order/cancel_order in name should be blocked."""
        loop = _make_loop()
        loop._invocation_context = InvocationContext(research_only=True)

        tc = _make_tool_call("alpaca_place_order", {"symbol": "AAPL"})
        context = _make_context()
        messages = []
        trace = Mock()
        react_trace = []

        loop._process_tool_calls([tc], context, messages, trace, react_trace, iteration=1)

        assert len(messages) == 1
        result = json.loads(messages[0]["content"])
        assert result["error_code"] == "research_only_violation"
        assert result["tool"] == "alpaca_place_order"

    def test_blocks_mcp_cancel_all_orders(self):
        loop = _make_loop()
        loop._invocation_context = InvocationContext(research_only=True)

        tc = _make_tool_call("binance_cancel_all_orders")
        context = _make_context()
        messages = []
        trace = Mock()
        react_trace = []

        loop._process_tool_calls([tc], context, messages, trace, react_trace, iteration=1)

        assert len(messages) == 1
        result = json.loads(messages[0]["content"])
        assert result["error_code"] == "research_only_violation"


class TestResearchOnlyAllowsReadonlyTools:
    """research_only=True should allow read-only tools."""

    def test_allows_trading_account(self):
        registry = ToolRegistry()
        registry.register(MockTradingAccountTool())
        loop = _make_loop(registry)
        loop._invocation_context = InvocationContext(research_only=True)

        tc = _make_tool_call("trading_account")
        context = _make_context()
        messages = []
        trace = Mock()
        react_trace = []

        loop._process_tool_calls([tc], context, messages, trace, react_trace, iteration=1)

        # Should NOT be blocked — tool executes normally
        # The tool result should be from actual execution, not a research_only_violation
        assert len(messages) == 1
        result = json.loads(messages[0]["content"])
        assert result.get("error_code") != "research_only_violation"
        assert result["status"] == "ok"
        assert result["balance"] == 10000

    def test_is_broker_write_tool_classification(self):
        loop = _make_loop()

        # Write tools
        assert loop._is_broker_write_tool("trading_place_order") is True
        assert loop._is_broker_write_tool("trading_cancel_order") is True
        assert loop._is_broker_write_tool("alpaca_place_order") is True
        assert loop._is_broker_write_tool("okx_cancel_order") is True
        assert loop._is_broker_write_tool("binance_cancel_all_orders") is True

        # Read-only tools
        assert loop._is_broker_write_tool("trading_account") is False
        assert loop._is_broker_write_tool("trading_positions") is False
        assert loop._is_broker_write_tool("trading_orders") is False
        assert loop._is_broker_write_tool("web_search") is False
        assert loop._is_broker_write_tool("run_backtest") is False


class TestNoIsolationWithoutContext:
    """Without invocation_context or with research_only=False, all tools run normally."""

    def test_no_invocation_context_allows_all(self):
        registry = ToolRegistry()
        registry.register(MockTradingPlaceOrderTool())
        loop = _make_loop(registry)
        loop._invocation_context = None

        tc = _make_tool_call("trading_place_order", {"symbol": "AAPL"})
        context = _make_context()
        messages = []
        trace = Mock()
        react_trace = []

        loop._process_tool_calls([tc], context, messages, trace, react_trace, iteration=1)

        # Should execute normally
        assert len(messages) == 1
        result = json.loads(messages[0]["content"])
        assert result["status"] == "ok"
        assert result["order_id"] == "12345"

    def test_research_only_false_allows_all(self):
        registry = ToolRegistry()
        registry.register(MockTradingPlaceOrderTool())
        loop = _make_loop(registry)
        loop._invocation_context = InvocationContext(research_only=False)

        tc = _make_tool_call("trading_place_order", {"symbol": "AAPL"})
        context = _make_context()
        messages = []
        trace = Mock()
        react_trace = []

        loop._process_tool_calls([tc], context, messages, trace, react_trace, iteration=1)

        # Should execute normally
        assert len(messages) == 1
        result = json.loads(messages[0]["content"])
        assert result["status"] == "ok"
        assert result["order_id"] == "12345"


class TestMixedToolCalls:
    """When a batch contains both write and read tools, only write tools are blocked."""

    def test_mixed_batch_blocks_only_write(self):
        registry = ToolRegistry()
        registry.register(MockTradingPlaceOrderTool())
        registry.register(MockTradingAccountTool())
        loop = _make_loop(registry)
        loop._invocation_context = InvocationContext(research_only=True)

        tc_write = _make_tool_call("trading_place_order", {"symbol": "AAPL"}, call_id="call_w")
        tc_read = _make_tool_call("trading_account", {}, call_id="call_r")
        context = _make_context()
        messages = []
        trace = Mock()
        react_trace = []

        loop._process_tool_calls([tc_write, tc_read], context, messages, trace, react_trace, iteration=1)

        # First message: blocked write tool error
        blocked = json.loads(messages[0]["content"])
        assert blocked["error_code"] == "research_only_violation"
        assert blocked["tool"] == "trading_place_order"

        # Second message: read tool executed normally
        read_result = json.loads(messages[1]["content"])
        assert read_result["status"] == "ok"
        assert read_result["balance"] == 10000
