"""Tests for TushareRealtimeProvider (``tushare_realtime.py``).

All tests mock ``tushare.pro_api`` — no real API calls are made.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

# Ensure backtest.loaders is importable
sys.path.insert(0, "")

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_tushare_token(monkeypatch):
    """Set a valid-looking token so the provider doesn't raise at init."""
    monkeypatch.setenv("TUSHARE_TOKEN", "mock-token-1234567890abcdef")


@pytest.fixture
def sample_rt_df():
    """A minimal rt_k DataFrame matching the Tushare schema."""
    return pd.DataFrame([
        {"ts_code": "000001.SZ", "trade_date": "20260716", "open": 12.50,
         "high": 12.80, "low": 12.40, "close": 12.65, "vol": 50000000,
         "amount": 630000000.0},
        {"ts_code": "600519.SH", "trade_date": "20260716", "open": 1680.00,
         "high": 1695.00, "low": 1675.00, "close": 1690.00, "vol": 3000000,
         "amount": 5070000000.0},
    ])


# ---------------------------------------------------------------------------
# Provider init
# ---------------------------------------------------------------------------


class TestProviderInit:
    def test_raises_when_token_empty(self, monkeypatch):
        """Token is empty → RuntimeError."""
        monkeypatch.setenv("TUSHARE_TOKEN", "")
        from backtest.loaders.tushare_realtime import TushareRealtimeProvider

        with pytest.raises(RuntimeError, match="Tushare token not configured"):
            TushareRealtimeProvider()

    def test_raises_when_token_is_placeholder(self, monkeypatch):
        """Token is the placeholder string → RuntimeError."""
        monkeypatch.setenv("TUSHARE_TOKEN", "your-tushare-token")
        from backtest.loaders.tushare_realtime import TushareRealtimeProvider

        with pytest.raises(RuntimeError, match="Tushare token not configured"):
            TushareRealtimeProvider()

    def test_creates_pro_api_with_valid_token(self, mock_tushare_token):
        """Valid token → pro_api is instantiated."""
        with patch("tushare.pro_api") as mock_pro:
            from backtest.loaders.tushare_realtime import TushareRealtimeProvider

            provider = TushareRealtimeProvider()
            mock_pro.assert_called_once_with("mock-token-1234567890abcdef")
            assert provider._pro is mock_pro.return_value


# ---------------------------------------------------------------------------
# fetch_quotes — happy path
# ---------------------------------------------------------------------------


class TestFetchQuotes:
    def test_fetches_with_codes(self, mock_tushare_token, sample_rt_df):
        """Codes only → provider calls rt_k and returns normalized envelope."""
        with patch("tushare.pro_api") as mock_pro:
            mock_pro.return_value.rt_k.return_value = sample_rt_df
            from backtest.loaders.tushare_realtime import TushareRealtimeProvider

            provider = TushareRealtimeProvider()
            result = provider.fetch_quotes(codes=["000001.SZ", "600519.SH"])

        assert result["data_source"] == "tushare"
        assert result["endpoint"] == "rt_k"
        assert result["is_provisional"] is True
        assert "retrieved_at" in result
        assert "000001.SZ" in result["data"]
        assert "600519.SH" in result["data"]
        assert result["data"]["000001.SZ"]["close"] == 12.65
        assert result["data"]["600519.SH"]["close"] == 1690.00

    def test_fetches_with_patterns(self, mock_tushare_token, sample_rt_df):
        """Patterns → ts_code is comma-joined with codes and patterns together."""
        with patch("tushare.pro_api") as mock_pro:
            mock_pro.return_value.rt_k.return_value = sample_rt_df
            from backtest.loaders.tushare_realtime import TushareRealtimeProvider

            provider = TushareRealtimeProvider()
            result = provider.fetch_quotes(patterns=["3*.SZ", "6*.SH"])

        assert result["endpoint"] == "rt_k"
        call_args = mock_pro.return_value.rt_k.call_args
        assert "3*.SZ,6*.SH" in call_args[1]["ts_code"]

    def test_fetches_with_both_codes_and_patterns(self, mock_tushare_token, sample_rt_df):
        """Both codes and patterns → concatenated in ts_code."""
        with patch("tushare.pro_api") as mock_pro:
            mock_pro.return_value.rt_k.return_value = sample_rt_df
            from backtest.loaders.tushare_realtime import TushareRealtimeProvider

            provider = TushareRealtimeProvider()
            result = provider.fetch_quotes(
                codes=["000001.SZ"], patterns=["6*.SH"]
            )

        assert result["endpoint"] == "rt_k"
        call_args = mock_pro.return_value.rt_k.call_args
        assert "000001.SZ,6*.SH" in call_args[1]["ts_code"]

    def test_passes_fields(self, mock_tushare_token, sample_rt_df):
        """Fields are forwarded as comma-separated string."""
        with patch("tushare.pro_api") as mock_pro:
            mock_pro.return_value.rt_k.return_value = sample_rt_df
            from backtest.loaders.tushare_realtime import TushareRealtimeProvider

            provider = TushareRealtimeProvider()
            provider.fetch_quotes(codes=["000001.SZ"], fields=["open", "close", "vol"])

        call_args = mock_pro.return_value.rt_k.call_args
        assert "open,close,vol" in call_args[1]["fields"]


# ---------------------------------------------------------------------------
# fetch_quotes — error paths
# ---------------------------------------------------------------------------


class TestFetchQuotesErrors:
    def test_invalid_code_format(self, mock_tushare_token):
        """Non-A-share code → error envelope."""
        with patch("tushare.pro_api"):
            from backtest.loaders.tushare_realtime import TushareRealtimeProvider

            provider = TushareRealtimeProvider()
            result = provider.fetch_quotes(codes=["AAPL.US"])

        assert "error" in result
        assert "Invalid code format" in result["error"]
        assert result["data"] == {}

    def test_invalid_pattern_format(self, mock_tushare_token):
        """Pattern without * suffix → error."""
        with patch("tushare.pro_api"):
            from backtest.loaders.tushare_realtime import TushareRealtimeProvider

            provider = TushareRealtimeProvider()
            result = provider.fetch_quotes(patterns=["3.SZ"])

        assert "error" in result
        assert "Invalid pattern format" in result["error"]
        assert result["data"] == {}

    def test_pattern_without_valid_suffix(self, mock_tushare_token):
        """Pattern without .SH/.SZ/.BJ suffix → error."""
        with patch("tushare.pro_api"):
            from backtest.loaders.tushare_realtime import TushareRealtimeProvider

            provider = TushareRealtimeProvider()
            result = provider.fetch_quotes(patterns=["3*.XX"])

        assert "error" in result
        assert "Invalid pattern format" in result["error"]

    def test_api_exception_returns_error_envelope(self, mock_tushare_token):
        """Tushare SDK raises → error envelope with exception message."""
        with patch("tushare.pro_api") as mock_pro:
            mock_pro.return_value.rt_k.side_effect = ConnectionError("timeout")
            from backtest.loaders.tushare_realtime import TushareRealtimeProvider

            provider = TushareRealtimeProvider()
            result = provider.fetch_quotes(codes=["000001.SZ"])

        assert "error" in result
        assert "timeout" in result["error"]
        assert result["data"] == {}

    def test_empty_dataframe(self, mock_tushare_token):
        """API returns empty DataFrame → empty data dict."""
        with patch("tushare.pro_api") as mock_pro:
            mock_pro.return_value.rt_k.return_value = pd.DataFrame()
            from backtest.loaders.tushare_realtime import TushareRealtimeProvider

            provider = TushareRealtimeProvider()
            result = provider.fetch_quotes(codes=["000001.SZ"])

        assert result["data"] == {}
        assert "error" not in result

    def test_none_return(self, mock_tushare_token):
        """API returns None → empty data dict."""
        with patch("tushare.pro_api") as mock_pro:
            mock_pro.return_value.rt_k.return_value = None
            from backtest.loaders.tushare_realtime import TushareRealtimeProvider

            provider = TushareRealtimeProvider()
            result = provider.fetch_quotes(codes=["000001.SZ"])

        assert result["data"] == {}
        assert "error" not in result

    def test_no_codes_or_patterns_returns_empty(self, mock_tushare_token):
        """Neither codes nor patterns → empty call (Tushare returns all)."""
        with patch("tushare.pro_api") as mock_pro:
            mock_pro.return_value.rt_k.return_value = pd.DataFrame()
            from backtest.loaders.tushare_realtime import TushareRealtimeProvider

            provider = TushareRealtimeProvider()
            result = provider.fetch_quotes()

        assert result["data"] == {}
