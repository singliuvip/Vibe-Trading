"""Tests for TushareRealTimeMinuteProvider — the rt_min data-layer provider."""

from __future__ import annotations

import json
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, "")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_df(records: list[dict]):
    """Return a mock DataFrame-like object from a list of dicts."""
    import pandas as pd

    return pd.DataFrame(records)


_STANDARD_RECORDS = [
    {
        "ts_code": "600000.SH",
        "time": "09:31",
        "open": 10.12,
        "high": 10.15,
        "low": 10.10,
        "close": 10.14,
        "vol": 12500,
        "amount": 126700.0,
    },
    {
        "ts_code": "600000.SH",
        "time": "09:32",
        "open": 10.14,
        "high": 10.18,
        "low": 10.13,
        "close": 10.16,
        "vol": 8900,
        "amount": 90300.0,
    },
    {
        "ts_code": "000001.SZ",
        "time": "09:31",
        "open": 12.50,
        "high": 12.55,
        "low": 12.48,
        "close": 12.52,
        "vol": 35000,
        "amount": 438200.0,
    },
]


# ---------------------------------------------------------------------------
# Provider tests
# ---------------------------------------------------------------------------


class TestTushareRealtimeMinuteProvider:
    def test_fetch_bars_returns_correct_envelope_shape(self):
        """Standard envelope keys are present."""
        with patch(
            "src.config.accessor.get_env_config",
            return_value=MagicMock(data=MagicMock(tushare_token="test-token-123")),
        ):
            from backtest.loaders.tushare_realtime_minute import TushareRealtimeMinuteProvider

            provider = TushareRealtimeMinuteProvider()

        with patch.object(provider, "_pro") as mock_pro:
            mock_pro.rt_min.return_value = _make_mock_df(_STANDARD_RECORDS)

            result = provider.fetch_bars(["600000.SH", "000001.SZ"], "1MIN")

        assert result["data_source"] == "tushare"
        assert result["endpoint"] == "rt_min"
        assert result["is_provisional"] is True
        assert "retrieved_at" in result
        assert isinstance(result["data"], dict)

    def test_fetch_bars_groups_by_ts_code(self):
        """Data is grouped by ts_code."""
        with patch(
            "src.config.accessor.get_env_config",
            return_value=MagicMock(data=MagicMock(tushare_token="test-token-123")),
        ):
            from backtest.loaders.tushare_realtime_minute import TushareRealtimeMinuteProvider

            provider = TushareRealtimeMinuteProvider()

        with patch.object(provider, "_pro") as mock_pro:
            mock_pro.rt_min.return_value = _make_mock_df(_STANDARD_RECORDS)

            result = provider.fetch_bars(["600000.SH", "000001.SZ"], "1MIN")

        data = result["data"]
        assert "600000.SH" in data
        assert "000001.SZ" in data
        assert len(data["600000.SH"]) == 2
        assert len(data["000001.SZ"]) == 1

    def test_fetch_bars_sorts_by_timestamp_ascending(self):
        """Bars within each code are sorted by timestamp ascending."""
        records = [
            {"ts_code": "600000.SH", "time": "09:35", "open": 10.2, "high": 10.2, "low": 10.2, "close": 10.2, "vol": 100, "amount": 1020},
            {"ts_code": "600000.SH", "time": "09:31", "open": 10.0, "high": 10.0, "low": 10.0, "close": 10.0, "vol": 50, "amount": 500},
        ]
        with patch(
            "src.config.accessor.get_env_config",
            return_value=MagicMock(data=MagicMock(tushare_token="test-token-123")),
        ):
            from backtest.loaders.tushare_realtime_minute import TushareRealtimeMinuteProvider

            provider = TushareRealtimeMinuteProvider()

        with patch.object(provider, "_pro") as mock_pro:
            mock_pro.rt_min.return_value = _make_mock_df(records)

            result = provider.fetch_bars(["600000.SH"], "1MIN")

        bars = result["data"]["600000.SH"]
        assert "09:31" in bars[0]["timestamp"]
        assert "09:35" in bars[1]["timestamp"]

    def test_fetch_bars_deduplicates_by_timestamp(self):
        """Duplicate (ts_code, timestamp) rows are deduplicated (last wins)."""
        records = [
            {"ts_code": "600000.SH", "time": "09:31", "open": 10.0, "high": 10.0, "low": 10.0, "close": 10.0, "vol": 50, "amount": 500},
            {"ts_code": "600000.SH", "time": "09:31", "open": 10.5, "high": 10.5, "low": 10.5, "close": 10.5, "vol": 100, "amount": 1050},
        ]
        with patch(
            "src.config.accessor.get_env_config",
            return_value=MagicMock(data=MagicMock(tushare_token="test-token-123")),
        ):
            from backtest.loaders.tushare_realtime_minute import TushareRealtimeMinuteProvider

            provider = TushareRealtimeMinuteProvider()

        with patch.object(provider, "_pro") as mock_pro:
            mock_pro.rt_min.return_value = _make_mock_df(records)

            result = provider.fetch_bars(["600000.SH"], "1MIN")

        bars = result["data"]["600000.SH"]
        assert len(bars) == 1
        assert bars[0]["close"] == 10.5  # last wins

    def test_fetch_bars_renames_vol_to_volume(self):
        """The 'vol' column is renamed to 'volume'."""
        records = [
            {"ts_code": "600000.SH", "time": "09:31", "open": 10.0, "high": 10.0, "low": 10.0, "close": 10.0, "vol": 12500, "amount": 126700},
        ]
        with patch(
            "src.config.accessor.get_env_config",
            return_value=MagicMock(data=MagicMock(tushare_token="test-token-123")),
        ):
            from backtest.loaders.tushare_realtime_minute import TushareRealtimeMinuteProvider

            provider = TushareRealtimeMinuteProvider()

        with patch.object(provider, "_pro") as mock_pro:
            mock_pro.rt_min.return_value = _make_mock_df(records)

            result = provider.fetch_bars(["600000.SH"], "1MIN")

        bar = result["data"]["600000.SH"][0]
        assert "volume" in bar
        assert bar["volume"] == 12500

    def test_fetch_bars_nan_converted_to_none(self):
        """NaN values are converted to None for JSON serialization."""
        import math

        records = [
            {"ts_code": "600000.SH", "time": "09:31", "open": float("nan"), "high": 10.0, "low": 10.0, "close": 10.0, "vol": 100, "amount": float("nan")},
        ]
        with patch(
            "src.config.accessor.get_env_config",
            return_value=MagicMock(data=MagicMock(tushare_token="test-token-123")),
        ):
            from backtest.loaders.tushare_realtime_minute import TushareRealtimeMinuteProvider

            provider = TushareRealtimeMinuteProvider()

        with patch.object(provider, "_pro") as mock_pro:
            mock_pro.rt_min.return_value = _make_mock_df(records)

            result = provider.fetch_bars(["600000.SH"], "1MIN")

        bar = result["data"]["600000.SH"][0]
        assert bar["open"] is None
        assert bar["amount"] is None
        # Verify allow_nan=False works
        json.dumps(result, allow_nan=False)

    def test_fetch_bars_invalid_code_returns_error(self):
        """Provider returns error envelope for invalid codes."""
        with patch(
            "src.config.accessor.get_env_config",
            return_value=MagicMock(data=MagicMock(tushare_token="test-token-123")),
        ):
            from backtest.loaders.tushare_realtime_minute import TushareRealtimeMinuteProvider

            provider = TushareRealtimeMinuteProvider()

        result = provider.fetch_bars(["AAPL.US"], "1MIN")
        assert "error" in result
        assert result["data"] == {}

    def test_fetch_bars_invalid_frequency_returns_error(self):
        """Provider returns error envelope for invalid frequency."""
        with patch(
            "src.config.accessor.get_env_config",
            return_value=MagicMock(data=MagicMock(tushare_token="test-token-123")),
        ):
            from backtest.loaders.tushare_realtime_minute import TushareRealtimeMinuteProvider

            provider = TushareRealtimeMinuteProvider()

        result = provider.fetch_bars(["600000.SH"], "2MIN")
        assert "error" in result
        assert "2MIN" in result["error"]

    def test_fetch_bars_empty_codes_returns_error(self):
        """Provider returns error envelope for empty codes."""
        with patch(
            "src.config.accessor.get_env_config",
            return_value=MagicMock(data=MagicMock(tushare_token="test-token-123")),
        ):
            from backtest.loaders.tushare_realtime_minute import TushareRealtimeMinuteProvider

            provider = TushareRealtimeMinuteProvider()

        result = provider.fetch_bars([], "1MIN")
        assert "error" in result

    def test_fetch_bars_tushare_exception_returns_error_envelope(self):
        """Provider wraps tushare SDK errors in error envelope."""
        with patch(
            "src.config.accessor.get_env_config",
            return_value=MagicMock(data=MagicMock(tushare_token="test-token-123")),
        ):
            from backtest.loaders.tushare_realtime_minute import TushareRealtimeMinuteProvider

            provider = TushareRealtimeMinuteProvider()

        with patch.object(provider, "_pro") as mock_pro:
            mock_pro.rt_min.side_effect = RuntimeError("Permission denied: rt_min")

            result = provider.fetch_bars(["600000.SH"], "1MIN")

        assert "error" in result
        assert "Permission denied" in result["error"]

    def test_fetch_bars_empty_dataframe_returns_empty_data(self):
        """Empty DataFrame returns empty data dict."""
        with patch(
            "src.config.accessor.get_env_config",
            return_value=MagicMock(data=MagicMock(tushare_token="test-token-123")),
        ):
            from backtest.loaders.tushare_realtime_minute import TushareRealtimeMinuteProvider

            provider = TushareRealtimeMinuteProvider()

        with patch.object(provider, "_pro") as mock_pro:
            mock_pro.rt_min.return_value = _make_mock_df([])

            result = provider.fetch_bars(["600000.SH"], "1MIN")

        assert result["data"] == {}

    def test_constructor_raises_on_missing_token(self):
        """Provider raises RuntimeError when token is a placeholder."""
        with patch(
            "src.config.accessor.get_env_config",
            return_value=MagicMock(data=MagicMock(tushare_token="")),
        ):
            from backtest.loaders.tushare_realtime_minute import TushareRealtimeMinuteProvider

            with pytest.raises(RuntimeError, match="Tushare token not configured"):
                TushareRealtimeMinuteProvider()

    def test_fetch_bars_passes_correct_params_to_rt_min(self):
        """Provider passes ts_code and freq correctly to pro.rt_min()."""
        with patch(
            "src.config.accessor.get_env_config",
            return_value=MagicMock(data=MagicMock(tushare_token="test-token-123")),
        ):
            from backtest.loaders.tushare_realtime_minute import TushareRealtimeMinuteProvider

            provider = TushareRealtimeMinuteProvider()

        with patch.object(provider, "_pro") as mock_pro:
            mock_pro.rt_min.return_value = _make_mock_df(_STANDARD_RECORDS)

            provider.fetch_bars(["600000.SH", "000001.SZ"], "5MIN")

        mock_pro.rt_min.assert_called_once_with(
            ts_code="600000.SH,000001.SZ", freq="5MIN"
        )
