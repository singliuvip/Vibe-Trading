"""Tests for TushareRefDataProvider (``tushare_refdata.py``).

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
def sample_stock_df():
    """A minimal stock_basic DataFrame matching expected fields."""
    return pd.DataFrame([
        {
            "ts_code": "000001.SZ",
            "symbol": "000001",
            "name": "平安银行",
            "area": "深圳",
            "industry": "银行",
            "market": "SZ",
            "list_date": "19910403",
        },
        {
            "ts_code": "600519.SH",
            "symbol": "600519",
            "name": "贵州茅台",
            "area": "贵州",
            "industry": "白酒",
            "market": "SH",
            "list_date": "20010827",
        },
    ])


@pytest.fixture
def sample_fund_df():
    """A minimal fund_basic DataFrame."""
    return pd.DataFrame([
        {
            "ts_code": "510050.SH",
            "name": "华夏上证50ETF",
            "management": "华夏基金",
            "found_date": "20041230",
            "type": "E",
        },
        {
            "ts_code": "510300.SH",
            "name": "华泰柏瑞沪深300ETF",
            "management": "华泰柏瑞基金",
            "found_date": "20120504",
            "type": "E",
        },
    ])


@pytest.fixture
def sample_option_df():
    """A minimal opt_basic DataFrame."""
    return pd.DataFrame([
        {
            "ts_code": "10002588.SH",
            "name": "50ETF购1月2500",
            "exercise_type": "欧式",
            "list_date": "20240101",
            "delist_date": "20250122",
        },
        {
            "ts_code": "10002589.SH",
            "name": "50ETF沽1月2500",
            "exercise_type": "欧式",
            "list_date": "20240101",
            "delist_date": "20250122",
        },
    ])


# ---------------------------------------------------------------------------
# Provider init
# ---------------------------------------------------------------------------


class TestProviderInit:
    def test_raises_when_token_empty(self, monkeypatch):
        """Token is empty → RuntimeError."""
        monkeypatch.setenv("TUSHARE_TOKEN", "")
        from backtest.loaders.tushare_refdata import TushareRefDataProvider

        with pytest.raises(RuntimeError, match="Tushare token not configured"):
            TushareRefDataProvider()

    def test_raises_when_token_is_placeholder(self, monkeypatch):
        """Token is the placeholder string → RuntimeError."""
        monkeypatch.setenv("TUSHARE_TOKEN", "your-tushare-token")
        from backtest.loaders.tushare_refdata import TushareRefDataProvider

        with pytest.raises(RuntimeError, match="Tushare token not configured"):
            TushareRefDataProvider()

    def test_creates_pro_api_with_valid_token(self, mock_tushare_token):
        """Valid token → pro_api is instantiated."""
        with patch("tushare.pro_api") as mock_pro:
            from backtest.loaders.tushare_refdata import TushareRefDataProvider

            provider = TushareRefDataProvider()
            mock_pro.assert_called_once_with("mock-token-1234567890abcdef")
            assert provider._pro is mock_pro.return_value


# ---------------------------------------------------------------------------
# fetch_stock_list — happy path
# ---------------------------------------------------------------------------


class TestFetchStockList:
    def test_fetches_all_markets(self, mock_tushare_token, sample_stock_df):
        """No market filter → returns all stocks."""
        with patch("tushare.pro_api") as mock_pro:
            mock_pro.return_value.stock_basic.return_value = sample_stock_df
            from backtest.loaders.tushare_refdata import TushareRefDataProvider

            provider = TushareRefDataProvider()
            result = provider.fetch_stock_list()

        assert result["endpoint"] == "stock_basic"
        assert result["data_source"] == "tushare"
        assert result["is_provisional"] is False
        assert "retrieved_at" in result
        assert len(result["data"]) == 2
        assert result["data"][0]["ts_code"] == "000001.SZ"
        assert result["data"][0]["name"] == "平安银行"

        call_kwargs = mock_pro.return_value.stock_basic.call_args[1]
        assert call_kwargs["list_status"] == "L"
        assert "exchange" not in call_kwargs

    def test_fetches_with_market_filter(self, mock_tushare_token, sample_stock_df):
        """Market='SH' → only Shanghai stocks."""
        with patch("tushare.pro_api") as mock_pro:
            mock_pro.return_value.stock_basic.return_value = pd.DataFrame([
                sample_stock_df.iloc[1]
            ])
            from backtest.loaders.tushare_refdata import TushareRefDataProvider

            provider = TushareRefDataProvider()
            result = provider.fetch_stock_list(market="SH")

        assert len(result["data"]) == 1
        assert result["data"][0]["ts_code"] == "600519.SH"

        call_kwargs = mock_pro.return_value.stock_basic.call_args[1]
        assert call_kwargs["exchange"] == "SH"

    def test_fetches_with_list_status(self, mock_tushare_token, sample_stock_df):
        """list_status='D' → delisted stocks."""
        with patch("tushare.pro_api") as mock_pro:
            mock_pro.return_value.stock_basic.return_value = sample_stock_df
            from backtest.loaders.tushare_refdata import TushareRefDataProvider

            provider = TushareRefDataProvider()
            result = provider.fetch_stock_list(list_status="D")

        call_kwargs = mock_pro.return_value.stock_basic.call_args[1]
        assert call_kwargs["list_status"] == "D"

    def test_empty_dataframe(self, mock_tushare_token):
        """API returns empty DataFrame → empty data list."""
        with patch("tushare.pro_api") as mock_pro:
            mock_pro.return_value.stock_basic.return_value = pd.DataFrame()
            from backtest.loaders.tushare_refdata import TushareRefDataProvider

            provider = TushareRefDataProvider()
            result = provider.fetch_stock_list()

        assert result["data"] == []
        assert "error" not in result

    def test_none_return(self, mock_tushare_token):
        """API returns None → empty data list."""
        with patch("tushare.pro_api") as mock_pro:
            mock_pro.return_value.stock_basic.return_value = None
            from backtest.loaders.tushare_refdata import TushareRefDataProvider

            provider = TushareRefDataProvider()
            result = provider.fetch_stock_list()

        assert result["data"] == []
        assert "error" not in result


# ---------------------------------------------------------------------------
# fetch_stock_list — error paths
# ---------------------------------------------------------------------------


class TestFetchStockListErrors:
    def test_invalid_market(self, mock_tushare_token):
        """Invalid market code → error envelope."""
        with patch("tushare.pro_api"):
            from backtest.loaders.tushare_refdata import TushareRefDataProvider

            provider = TushareRefDataProvider()
            result = provider.fetch_stock_list(market="HK")

        assert "error" in result
        assert "Invalid market" in result["error"]
        assert result["data"] == []

    def test_invalid_list_status(self, mock_tushare_token):
        """Invalid list_status → error envelope."""
        with patch("tushare.pro_api"):
            from backtest.loaders.tushare_refdata import TushareRefDataProvider

            provider = TushareRefDataProvider()
            result = provider.fetch_stock_list(list_status="X")

        assert "error" in result
        assert "Invalid list_status" in result["error"]
        assert result["data"] == []

    def test_api_exception_returns_error_envelope(self, mock_tushare_token):
        """Tushare SDK raises → error envelope with exception message."""
        with patch("tushare.pro_api") as mock_pro:
            mock_pro.return_value.stock_basic.side_effect = ConnectionError("timeout")
            from backtest.loaders.tushare_refdata import TushareRefDataProvider

            provider = TushareRefDataProvider()
            result = provider.fetch_stock_list()

        assert "error" in result
        assert "timeout" in result["error"]
        assert result["data"] == []


# ---------------------------------------------------------------------------
# fetch_fund_list — happy path
# ---------------------------------------------------------------------------


class TestFetchFundList:
    def test_fetches_all_funds(self, mock_tushare_token, sample_fund_df):
        """No market filter → returns all funds."""
        with patch("tushare.pro_api") as mock_pro:
            mock_pro.return_value.fund_basic.return_value = sample_fund_df
            from backtest.loaders.tushare_refdata import TushareRefDataProvider

            provider = TushareRefDataProvider()
            result = provider.fetch_fund_list()

        assert result["endpoint"] == "fund_basic"
        assert result["data_source"] == "tushare"
        assert result["is_provisional"] is False
        assert len(result["data"]) == 2
        assert result["data"][0]["ts_code"] == "510050.SH"

        call_kwargs = mock_pro.return_value.fund_basic.call_args[1]
        assert "market" not in call_kwargs

    def test_fetches_with_market_filter(self, mock_tushare_token, sample_fund_df):
        """market='E' → ETF only."""
        with patch("tushare.pro_api") as mock_pro:
            mock_pro.return_value.fund_basic.return_value = sample_fund_df
            from backtest.loaders.tushare_refdata import TushareRefDataProvider

            provider = TushareRefDataProvider()
            result = provider.fetch_fund_list(market="E")

        call_kwargs = mock_pro.return_value.fund_basic.call_args[1]
        assert call_kwargs["market"] == "E"
        assert len(result["data"]) == 2

    def test_empty_dataframe(self, mock_tushare_token):
        """API returns empty DataFrame → empty data list."""
        with patch("tushare.pro_api") as mock_pro:
            mock_pro.return_value.fund_basic.return_value = pd.DataFrame()
            from backtest.loaders.tushare_refdata import TushareRefDataProvider

            provider = TushareRefDataProvider()
            result = provider.fetch_fund_list()

        assert result["data"] == []
        assert "error" not in result

    def test_api_exception_returns_error_envelope(self, mock_tushare_token):
        """Tushare SDK raises → error envelope."""
        with patch("tushare.pro_api") as mock_pro:
            mock_pro.return_value.fund_basic.side_effect = RuntimeError("api down")
            from backtest.loaders.tushare_refdata import TushareRefDataProvider

            provider = TushareRefDataProvider()
            result = provider.fetch_fund_list()

        assert "error" in result
        assert "api down" in result["error"]
        assert result["data"] == []


# ---------------------------------------------------------------------------
# fetch_option_list — happy path
# ---------------------------------------------------------------------------


class TestFetchOptionList:
    def test_fetches_all_options(self, mock_tushare_token, sample_option_df):
        """No exchange filter → returns all options."""
        with patch("tushare.pro_api") as mock_pro:
            mock_pro.return_value.opt_basic.return_value = sample_option_df
            from backtest.loaders.tushare_refdata import TushareRefDataProvider

            provider = TushareRefDataProvider()
            result = provider.fetch_option_list()

        assert result["endpoint"] == "opt_basic"
        assert result["data_source"] == "tushare"
        assert result["is_provisional"] is False
        assert len(result["data"]) == 2
        assert result["data"][0]["ts_code"] == "10002588.SH"
        assert result["data"][0]["exercise_type"] == "欧式"

        call_kwargs = mock_pro.return_value.opt_basic.call_args[1]
        assert "exchange" not in call_kwargs

    def test_fetches_with_exchange_filter(self, mock_tushare_token, sample_option_df):
        """exchange='SSE' → Shanghai options only."""
        with patch("tushare.pro_api") as mock_pro:
            mock_pro.return_value.opt_basic.return_value = sample_option_df
            from backtest.loaders.tushare_refdata import TushareRefDataProvider

            provider = TushareRefDataProvider()
            result = provider.fetch_option_list(exchange="SSE")

        call_kwargs = mock_pro.return_value.opt_basic.call_args[1]
        assert call_kwargs["exchange"] == "SSE"
        assert len(result["data"]) == 2

    def test_empty_dataframe(self, mock_tushare_token):
        """API returns empty DataFrame → empty data list."""
        with patch("tushare.pro_api") as mock_pro:
            mock_pro.return_value.opt_basic.return_value = pd.DataFrame()
            from backtest.loaders.tushare_refdata import TushareRefDataProvider

            provider = TushareRefDataProvider()
            result = provider.fetch_option_list()

        assert result["data"] == []
        assert "error" not in result


# ---------------------------------------------------------------------------
# fetch_option_list — error paths
# ---------------------------------------------------------------------------


class TestFetchOptionListErrors:
    def test_invalid_exchange(self, mock_tushare_token):
        """Invalid exchange → error envelope."""
        with patch("tushare.pro_api"):
            from backtest.loaders.tushare_refdata import TushareRefDataProvider

            provider = TushareRefDataProvider()
            result = provider.fetch_option_list(exchange="CFFEX")

        assert "error" in result
        assert "Invalid exchange" in result["error"]
        assert result["data"] == []

    def test_api_exception_returns_error_envelope(self, mock_tushare_token):
        """Tushare SDK raises → error envelope."""
        with patch("tushare.pro_api") as mock_pro:
            mock_pro.return_value.opt_basic.side_effect = ValueError("bad request")
            from backtest.loaders.tushare_refdata import TushareRefDataProvider

            provider = TushareRefDataProvider()
            result = provider.fetch_option_list()

        assert "error" in result
        assert "bad request" in result["error"]
        assert result["data"] == []
