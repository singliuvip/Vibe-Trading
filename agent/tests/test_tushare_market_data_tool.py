"""Tests for the Tushare advanced-privilege tool layer.

Covers:
- ``get_realtime_quotes`` tool  (param parsing + error handling)
- ``get_auction_data`` tool     (param parsing + session validation)

All tests mock the Service layer — no real Tushare SDK calls.

Note: tools use LangChain ``@tool`` decorator → need ``.invoke()`` to call them.
"""

from __future__ import annotations

import json
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, "")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_STANDARD_ENVELOPE = {
    "data_source": "tushare",
    "endpoint": "rt_k",
    "retrieved_at": "2026-07-16T10:30:00+00:00",
    "is_provisional": True,
    "data": {"000001.SZ": {"close": 12.65}},
    "_meta": {
        "data_source": "tushare",
        "endpoint": "rt_k",
        "retrieved_at": "2026-07-16T10:30:00+00:00",
        "is_provisional": True,
        "market_session": "active",
    },
}

_AUCTION_ENVELOPE = {
    "data_source": "tushare",
    "endpoint": "stk_auction",
    "retrieved_at": "2026-07-16T10:30:00+00:00",
    "is_provisional": True,
    "data": {"000001.SZ": {"price": 12.50}},
    "_meta": {
        "data_source": "tushare",
        "endpoint": "stk_auction",
        "retrieved_at": "2026-07-16T10:30:00+00:00",
        "is_provisional": True,
        "session": "current",
    },
}


# ---------------------------------------------------------------------------
# get_realtime_quotes tool
# ---------------------------------------------------------------------------


class TestGetRealtimeQuotesTool:
    def test_parses_comma_separated_codes(self):
        """Codes string is split and passed to the service correctly."""
        with (
            patch(
                "backtest.loaders.tushare_realtime.TushareRealtimeProvider.__init__",
                return_value=None,
            ),
            patch(
                "src.core.tushare_market_data.TushareMarketDataService.get_realtime_quotes",
                return_value=_STANDARD_ENVELOPE,
            ),
        ):
            from src.tools.tushare_realtime_tool import get_realtime_quotes

            result = get_realtime_quotes.invoke({"codes": "000001.SZ,600519.SH", "max_rows": 250})

        parsed = json.loads(result)
        assert "000001.SZ" in parsed.get("data", {})

    def test_code_validation_rejects_bad_code(self):
        """Invalid code → error envelope from Service layer."""
        with patch(
            "src.core.tushare_market_data.TushareMarketDataService.get_realtime_quotes",
            return_value={
                "error": "Invalid A-share code: 'AAPL.US'.",
                "data": {},
            },
        ):
            from src.tools.tushare_realtime_tool import get_realtime_quotes

            result = get_realtime_quotes.invoke({"codes": "AAPL.US"})

        parsed = json.loads(result)
        assert "error" in parsed

    def test_runtime_error_on_missing_token(self):
        """Provider raises RuntimeError → JSON error with hint."""
        with patch(
            "backtest.loaders.tushare_realtime.TushareRealtimeProvider.__init__",
            side_effect=RuntimeError("Tushare token not configured"),
        ):
            from src.tools.tushare_realtime_tool import get_realtime_quotes

            result = get_realtime_quotes.invoke({"codes": "000001.SZ"})

        parsed = json.loads(result)
        assert "Tushare token not configured" in parsed["error"]

    def test_empty_codes_and_patterns(self):
        """Neither codes nor patterns → Service-level error."""
        with patch(
            "src.core.tushare_market_data.TushareMarketDataService.get_realtime_quotes",
            return_value={
                "error": "At least one code or pattern is required.",
                "data": {},
            },
        ):
            from src.tools.tushare_realtime_tool import get_realtime_quotes

            result = get_realtime_quotes.invoke({})

        parsed = json.loads(result)
        assert "error" in parsed

    def test_passes_max_rows_zero(self):
        """max_rows=0 is forwarded (uncapped)."""
        with (
            patch(
                "backtest.loaders.tushare_realtime.TushareRealtimeProvider.__init__",
                return_value=None,
            ),
            patch(
                "src.core.tushare_market_data.TushareMarketDataService.get_realtime_quotes",
                return_value=_STANDARD_ENVELOPE,
            ) as mock_svc,
        ):
            from src.tools.tushare_realtime_tool import get_realtime_quotes

            get_realtime_quotes.invoke({"codes": "000001.SZ", "max_rows": 0})
            assert mock_svc.call_args[1]["max_rows"] == 0


# ---------------------------------------------------------------------------
# get_auction_data tool
# ---------------------------------------------------------------------------


class TestGetAuctionDataTool:
    def test_rejects_invalid_session(self):
        """Invalid session value → immediate tool-layer rejection."""
        from src.tools.tushare_auction_tool import get_auction_data

        result = get_auction_data.invoke({"session": "midday"})
        parsed = json.loads(result)

        assert "error" in parsed
        assert "midday" in parsed["error"]

    @pytest.mark.parametrize("session", ["current", "open", "close"])
    def test_accepts_valid_sessions(self, session):
        """All three valid session values are accepted."""
        endpoint = {"current": "stk_auction", "open": "stk_auction_o", "close": "stk_auction_c"}[session]
        envelope = {
            "data_source": "tushare",
            "endpoint": endpoint,
            "retrieved_at": "2026-07-16T10:30:00+00:00",
            "is_provisional": session == "current",
            "data": {"000001.SZ": {"price": 12.50}},
            "_meta": {
                "data_source": "tushare",
                "endpoint": endpoint,
                "retrieved_at": "2026-07-16T10:30:00+00:00",
                "is_provisional": session == "current",
                "session": session,
            },
        }

        with (
            patch(
                "backtest.loaders.tushare_auction.TushareAuctionProvider.__init__",
                return_value=None,
            ),
            patch(
                "src.core.tushare_market_data.TushareMarketDataService.get_auction_data",
                return_value=envelope,
            ),
        ):
            from src.tools.tushare_auction_tool import get_auction_data

            result = get_auction_data.invoke({"session": session, "codes": "000001.SZ"})

        parsed = json.loads(result)
        assert "error" not in parsed

    def test_runtime_error_on_missing_token(self):
        """Provider raises RuntimeError → JSON error."""
        with patch(
            "backtest.loaders.tushare_auction.TushareAuctionProvider.__init__",
            side_effect=RuntimeError("Tushare token not configured"),
        ):
            from src.tools.tushare_auction_tool import get_auction_data

            result = get_auction_data.invoke({"session": "current"})

        parsed = json.loads(result)
        assert "Tushare token not configured" in parsed["error"]

    def test_code_validation_rejects_bad_code(self):
        """Invalid code → Service error."""
        with patch(
            "src.core.tushare_market_data.TushareMarketDataService.get_auction_data",
            return_value={
                "error": "Invalid A-share code: 'BTC-USDT'.",
                "data": {},
            },
        ):
            from src.tools.tushare_auction_tool import get_auction_data

            result = get_auction_data.invoke({"session": "open", "codes": "BTC-USDT"})

        parsed = json.loads(result)
        assert "error" in parsed


# ---------------------------------------------------------------------------
# Tool JSON output validity
# ---------------------------------------------------------------------------


class TestToolJsonValidity:
    """Ensure all tool outputs are valid JSON."""

    def test_realtime_tool_output_is_valid_json(self):
        with patch(
            "src.core.tushare_market_data.TushareMarketDataService.get_realtime_quotes",
            return_value=_STANDARD_ENVELOPE,
        ):
            from src.tools.tushare_realtime_tool import get_realtime_quotes

            result = get_realtime_quotes.invoke({"codes": "000001.SZ"})
            parsed = json.loads(result)
            assert isinstance(parsed, dict)

    def test_auction_tool_output_is_valid_json(self):
        with patch(
            "src.core.tushare_market_data.TushareMarketDataService.get_auction_data",
            return_value=_AUCTION_ENVELOPE,
        ):
            from src.tools.tushare_auction_tool import get_auction_data

            result = get_auction_data.invoke({"session": "current", "codes": "000001.SZ"})
            parsed = json.loads(result)
            assert isinstance(parsed, dict)
