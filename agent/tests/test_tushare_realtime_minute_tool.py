"""Tests for the Tushare realtime-minute-bars tool layer.

Covers:
- ``get_realtime_minute_bars`` tool (param parsing + error handling)

All tests mock the Service layer — no real Tushare SDK calls.
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


_STANDARD_MINUTE_ENVELOPE = {
    "data_source": "tushare",
    "endpoint": "rt_min",
    "retrieved_at": "2026-07-17T02:30:00+00:00",
    "is_provisional": True,
    "data": {
        "600000.SH": [
            {"timestamp": "2026-07-17T09:31:00+08:00", "open": 10.12, "high": 10.15, "low": 10.10, "close": 10.14, "volume": 12500, "amount": 126700},
        ]
    },
    "_meta": {
        "data_source": "tushare",
        "endpoint": "rt_min",
        "frequency": "1MIN",
        "retrieved_at": "2026-07-17T02:30:00+00:00",
        "is_provisional": True,
        "market_session": "active",
        "timezone": "Asia/Shanghai",
        "row_limit": 1000,
    },
}


# ---------------------------------------------------------------------------
# get_realtime_minute_bars tool
# ---------------------------------------------------------------------------


class TestGetRealtimeMinuteBarsTool:
    def test_parses_comma_separated_codes(self):
        """Codes string is split and passed to the service correctly."""
        with (
            patch(
                "backtest.loaders.tushare_realtime_minute.TushareRealtimeMinuteProvider.__init__",
                return_value=None,
            ),
            patch(
                "src.core.tushare_market_data.TushareMarketDataService.get_realtime_minute_bars",
                return_value=_STANDARD_MINUTE_ENVELOPE,
            ),
        ):
            from src.tools.tushare_realtime_minute_tool import get_realtime_minute_bars

            result = get_realtime_minute_bars.invoke({"codes": "600000.SH,000001.SZ", "frequency": "1MIN"})

        parsed = json.loads(result)
        assert "600000.SH" in parsed.get("data", {})

    def test_default_frequency_is_1min(self):
        """Default frequency is 1MIN."""
        with (
            patch(
                "backtest.loaders.tushare_realtime_minute.TushareRealtimeMinuteProvider.__init__",
                return_value=None,
            ),
            patch(
                "src.core.tushare_market_data.TushareMarketDataService.get_realtime_minute_bars",
                return_value=_STANDARD_MINUTE_ENVELOPE,
            ) as mock_svc,
        ):
            from src.tools.tushare_realtime_minute_tool import get_realtime_minute_bars

            get_realtime_minute_bars.invoke({"codes": "600000.SH"})

        assert mock_svc.call_args[1]["frequency"] == "1MIN"

    def test_default_max_rows_is_1000(self):
        """Default max_rows is 1000."""
        with (
            patch(
                "backtest.loaders.tushare_realtime_minute.TushareRealtimeMinuteProvider.__init__",
                return_value=None,
            ),
            patch(
                "src.core.tushare_market_data.TushareMarketDataService.get_realtime_minute_bars",
                return_value=_STANDARD_MINUTE_ENVELOPE,
            ) as mock_svc,
        ):
            from src.tools.tushare_realtime_minute_tool import get_realtime_minute_bars

            get_realtime_minute_bars.invoke({"codes": "600000.SH"})

        assert mock_svc.call_args[1]["max_rows"] == 1000

    def test_passes_max_rows_zero(self):
        """max_rows=0 is forwarded."""
        with (
            patch(
                "backtest.loaders.tushare_realtime_minute.TushareRealtimeMinuteProvider.__init__",
                return_value=None,
            ),
            patch(
                "src.core.tushare_market_data.TushareMarketDataService.get_realtime_minute_bars",
                return_value=_STANDARD_MINUTE_ENVELOPE,
            ) as mock_svc,
        ):
            from src.tools.tushare_realtime_minute_tool import get_realtime_minute_bars

            get_realtime_minute_bars.invoke({"codes": "600000.SH", "max_rows": 0})

        assert mock_svc.call_args[1]["max_rows"] == 0

    @pytest.mark.parametrize("freq", ["1MIN", "5MIN", "15MIN", "30MIN", "60MIN"])
    def test_accepts_valid_frequencies(self, freq):
        """All five valid frequencies are accepted."""
        envelope = dict(_STANDARD_MINUTE_ENVELOPE)
        envelope["_meta"] = dict(_STANDARD_MINUTE_ENVELOPE["_meta"])
        envelope["_meta"]["frequency"] = freq

        with (
            patch(
                "backtest.loaders.tushare_realtime_minute.TushareRealtimeMinuteProvider.__init__",
                return_value=None,
            ),
            patch(
                "src.core.tushare_market_data.TushareMarketDataService.get_realtime_minute_bars",
                return_value=envelope,
            ),
        ):
            from src.tools.tushare_realtime_minute_tool import get_realtime_minute_bars

            result = get_realtime_minute_bars.invoke({"codes": "600000.SH", "frequency": freq})

        parsed = json.loads(result)
        assert "error" not in parsed

    def test_rejects_invalid_frequency_at_tool_layer(self):
        """Invalid frequency is rejected at the tool layer before service call."""
        from src.tools.tushare_realtime_minute_tool import get_realtime_minute_bars

        result = get_realtime_minute_bars.invoke({"codes": "600000.SH", "frequency": "2MIN"})

        parsed = json.loads(result)
        assert "error" in parsed
        assert "2MIN" in parsed["error"]

    def test_rejects_empty_codes_at_tool_layer(self):
        """Empty codes string is rejected at the tool layer."""
        from src.tools.tushare_realtime_minute_tool import get_realtime_minute_bars

        result = get_realtime_minute_bars.invoke({"codes": ""})

        parsed = json.loads(result)
        assert "error" in parsed

    def test_runtime_error_on_missing_token(self):
        """Provider raises RuntimeError → JSON error with hint."""
        with patch(
            "backtest.loaders.tushare_realtime_minute.TushareRealtimeMinuteProvider.__init__",
            side_effect=RuntimeError("Tushare token not configured"),
        ):
            from src.tools.tushare_realtime_minute_tool import get_realtime_minute_bars

            result = get_realtime_minute_bars.invoke({"codes": "600000.SH"})

        parsed = json.loads(result)
        assert "Tushare token not configured" in parsed["error"]

    def test_returns_strict_json_no_nan(self):
        """Output passes json.dumps with allow_nan=False."""
        with (
            patch(
                "backtest.loaders.tushare_realtime_minute.TushareRealtimeMinuteProvider.__init__",
                return_value=None,
            ),
            patch(
                "src.core.tushare_market_data.TushareMarketDataService.get_realtime_minute_bars",
                return_value=_STANDARD_MINUTE_ENVELOPE,
            ),
        ):
            from src.tools.tushare_realtime_minute_tool import get_realtime_minute_bars

            result = get_realtime_minute_bars.invoke({"codes": "600000.SH"})

        # Must be valid JSON
        parsed = json.loads(result)
        assert "error" not in parsed

    def test_service_error_propagated(self):
        """Service-layer error is propagated through the tool."""
        with (
            patch(
                "backtest.loaders.tushare_realtime_minute.TushareRealtimeMinuteProvider.__init__",
                return_value=None,
            ),
            patch(
                "src.core.tushare_market_data.TushareMarketDataService.get_realtime_minute_bars",
                return_value={
                    "error": "Invalid A-share code: 'AAPL.US'.",
                    "data": {},
                },
            ),
        ):
            from src.tools.tushare_realtime_minute_tool import get_realtime_minute_bars

            result = get_realtime_minute_bars.invoke({"codes": "AAPL.US"})

        parsed = json.loads(result)
        assert "error" in parsed
