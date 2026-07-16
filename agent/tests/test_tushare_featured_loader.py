"""Tests for TushareFeaturedProvider (``tushare_featured.py``).

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
def sample_cyq_perf_df():
    """A minimal cyq_perf DataFrame."""
    return pd.DataFrame([
        {
            "ts_code": "000001.SZ",
            "trade_date": "20240715",
            "his_low": 8.50,
            "his_high": 15.20,
            "cost_5pct": 9.80,
            "cost_15pct": 10.20,
            "cost_50pct": 11.50,
            "cost_85pct": 12.80,
            "cost_95pct": 13.50,
            "weight_avg": 11.30,
            "winner_rate": 65.5,
        },
        {
            "ts_code": "600519.SH",
            "trade_date": "20240715",
            "his_low": 1500.00,
            "his_high": 1900.00,
            "cost_5pct": 1600.00,
            "cost_15pct": 1650.00,
            "cost_50pct": 1720.00,
            "cost_85pct": 1780.00,
            "cost_95pct": 1820.00,
            "weight_avg": 1710.00,
            "winner_rate": 72.0,
        },
    ])


@pytest.fixture
def sample_cyq_chips_df():
    """A minimal cyq_chips DataFrame."""
    return pd.DataFrame([
        {"ts_code": "000001.SZ", "trade_date": "20240715", "price": 9.80, "volume": 100000},
        {"ts_code": "000001.SZ", "trade_date": "20240715", "price": 10.20, "volume": 150000},
        {"ts_code": "000001.SZ", "trade_date": "20240715", "price": 11.50, "volume": 300000},
    ])


@pytest.fixture
def sample_limit_list_df():
    """A minimal limit_list_d DataFrame."""
    return pd.DataFrame([
        {
            "ts_code": "000001.SZ",
            "trade_date": "20240715",
            "name": "平安银行",
            "limit": "U",
            "pct_chg": 10.05,
            "close": 12.50,
            "limit_times": 1,
        },
        {
            "ts_code": "600519.SH",
            "trade_date": "20240715",
            "name": "贵州茅台",
            "limit": "U",
            "pct_chg": 10.00,
            "close": 1800.00,
            "limit_times": 2,
        },
    ])


@pytest.fixture
def sample_ths_hot_df():
    """A minimal ths_hot DataFrame."""
    return pd.DataFrame([
        {
            "trade_date": "20240715",
            "ts_code": "000001.SZ",
            "name": "平安银行",
            "rank": 1,
            "hot_score": 9850.5,
        },
        {
            "trade_date": "20240715",
            "ts_code": "600519.SH",
            "name": "贵州茅台",
            "rank": 2,
            "hot_score": 9720.0,
        },
    ])


# ---------------------------------------------------------------------------
# Provider init
# ---------------------------------------------------------------------------


class TestProviderInit:
    def test_raises_when_token_empty(self, monkeypatch):
        """Token is empty → RuntimeError."""
        monkeypatch.setenv("TUSHARE_TOKEN", "")
        from backtest.loaders.tushare_featured import TushareFeaturedProvider

        with pytest.raises(RuntimeError, match="Tushare token not configured"):
            TushareFeaturedProvider()

    def test_raises_when_token_is_placeholder(self, monkeypatch):
        """Token is the placeholder string → RuntimeError."""
        monkeypatch.setenv("TUSHARE_TOKEN", "your-tushare-token")
        from backtest.loaders.tushare_featured import TushareFeaturedProvider

        with pytest.raises(RuntimeError, match="Tushare token not configured"):
            TushareFeaturedProvider()

    def test_creates_pro_api_with_valid_token(self, mock_tushare_token):
        """Valid token → pro_api is instantiated."""
        with patch("tushare.pro_api") as mock_pro:
            from backtest.loaders.tushare_featured import TushareFeaturedProvider

            provider = TushareFeaturedProvider()
            mock_pro.assert_called_once_with("mock-token-1234567890abcdef")
            assert provider._pro is mock_pro.return_value


# ---------------------------------------------------------------------------
# fetch_cyq_perf
# ---------------------------------------------------------------------------


class TestFetchCyqPerf:
    def test_normal_return(self, mock_tushare_token, sample_cyq_perf_df):
        """Normal cyq_perf → returns envelope with data."""
        with patch("tushare.pro_api") as mock_pro:
            mock_pro.return_value.cyq_perf.return_value = sample_cyq_perf_df
            from backtest.loaders.tushare_featured import TushareFeaturedProvider

            provider = TushareFeaturedProvider()
            result = provider.fetch_cyq_perf(ts_code="000001.SZ")

        assert result["endpoint"] == "cyq_perf"
        assert result["data_source"] == "tushare"
        assert result["is_provisional"] is False
        assert "retrieved_at" in result
        assert len(result["data"]) == 2
        assert result["data"][0]["ts_code"] == "000001.SZ"
        assert result["data"][0]["winner_rate"] == 65.5

    def test_empty_return(self, mock_tushare_token):
        """Empty DataFrame → returns envelope with empty data list."""
        with patch("tushare.pro_api") as mock_pro:
            mock_pro.return_value.cyq_perf.return_value = pd.DataFrame()
            from backtest.loaders.tushare_featured import TushareFeaturedProvider

            provider = TushareFeaturedProvider()
            result = provider.fetch_cyq_perf()

        assert result["endpoint"] == "cyq_perf"
        assert result["data"] == []

    def test_exception(self, mock_tushare_token):
        """API exception → returns error envelope."""
        with patch("tushare.pro_api") as mock_pro:
            mock_pro.return_value.cyq_perf.side_effect = RuntimeError("Network error")
            from backtest.loaders.tushare_featured import TushareFeaturedProvider

            provider = TushareFeaturedProvider()
            result = provider.fetch_cyq_perf()

        assert result["endpoint"] == "cyq_perf"
        assert "Network error" in result["error"]
        assert result["data"] == []

    def test_with_date_range(self, mock_tushare_token, sample_cyq_perf_df):
        """Passes start_date/end_date to Tushare."""
        with patch("tushare.pro_api") as mock_pro:
            mock_pro.return_value.cyq_perf.return_value = sample_cyq_perf_df
            from backtest.loaders.tushare_featured import TushareFeaturedProvider

            provider = TushareFeaturedProvider()
            result = provider.fetch_cyq_perf(start_date="20240701", end_date="20240715")

        assert result["endpoint"] == "cyq_perf"
        call_kwargs = mock_pro.return_value.cyq_perf.call_args[1]
        assert call_kwargs["start_date"] == "20240701"
        assert call_kwargs["end_date"] == "20240715"


# ---------------------------------------------------------------------------
# fetch_cyq_chips
# ---------------------------------------------------------------------------


class TestFetchCyqChips:
    def test_normal_return(self, mock_tushare_token, sample_cyq_chips_df):
        """Normal cyq_chips → returns envelope with data."""
        with patch("tushare.pro_api") as mock_pro:
            mock_pro.return_value.cyq_chips.return_value = sample_cyq_chips_df
            from backtest.loaders.tushare_featured import TushareFeaturedProvider

            provider = TushareFeaturedProvider()
            result = provider.fetch_cyq_chips(ts_code="000001.SZ", trade_date="20240715")

        assert result["endpoint"] == "cyq_chips"
        assert result["data_source"] == "tushare"
        assert result["is_provisional"] is False
        assert len(result["data"]) == 3
        assert result["data"][0]["price"] == 9.80
        assert result["data"][0]["volume"] == 100000

    def test_empty_return(self, mock_tushare_token):
        """Empty DataFrame → returns envelope with empty data list."""
        with patch("tushare.pro_api") as mock_pro:
            mock_pro.return_value.cyq_chips.return_value = pd.DataFrame()
            from backtest.loaders.tushare_featured import TushareFeaturedProvider

            provider = TushareFeaturedProvider()
            result = provider.fetch_cyq_chips()

        assert result["endpoint"] == "cyq_chips"
        assert result["data"] == []


# ---------------------------------------------------------------------------
# fetch_limit_list
# ---------------------------------------------------------------------------


class TestFetchLimitList:
    def test_normal_return(self, mock_tushare_token, sample_limit_list_df):
        """Normal limit_list_d → returns envelope with data."""
        with patch("tushare.pro_api") as mock_pro:
            mock_pro.return_value.limit_list_d.return_value = sample_limit_list_df
            from backtest.loaders.tushare_featured import TushareFeaturedProvider

            provider = TushareFeaturedProvider()
            result = provider.fetch_limit_list(trade_date="20240715")

        assert result["endpoint"] == "limit_list_d"
        assert result["data_source"] == "tushare"
        assert result["is_provisional"] is False
        assert len(result["data"]) == 2
        assert result["data"][0]["limit"] == "U"
        assert result["data"][1]["limit_times"] == 2

    def test_empty_return(self, mock_tushare_token):
        """Empty DataFrame → returns envelope with empty data list."""
        with patch("tushare.pro_api") as mock_pro:
            mock_pro.return_value.limit_list_d.return_value = pd.DataFrame()
            from backtest.loaders.tushare_featured import TushareFeaturedProvider

            provider = TushareFeaturedProvider()
            result = provider.fetch_limit_list()

        assert result["endpoint"] == "limit_list_d"
        assert result["data"] == []

    def test_limit_type_filter(self, mock_tushare_token, sample_limit_list_df):
        """limit_type='U' → only limit-up stocks."""
        with patch("tushare.pro_api") as mock_pro:
            mock_pro.return_value.limit_list_d.return_value = pd.DataFrame([
                sample_limit_list_df.iloc[0]
            ])
            from backtest.loaders.tushare_featured import TushareFeaturedProvider

            provider = TushareFeaturedProvider()
            result = provider.fetch_limit_list(trade_date="20240715", limit_type="U")

        assert result["endpoint"] == "limit_list_d"
        call_kwargs = mock_pro.return_value.limit_list_d.call_args[1]
        assert call_kwargs["limit_type"] == "U"

    def test_invalid_limit_type(self, mock_tushare_token):
        """Invalid limit_type → error envelope."""
        with patch("tushare.pro_api"):
            from backtest.loaders.tushare_featured import TushareFeaturedProvider

            provider = TushareFeaturedProvider()
            result = provider.fetch_limit_list(limit_type="X")

        assert "Invalid limit_type" in result["error"]
        assert result["data"] == []

    def test_exception(self, mock_tushare_token):
        """API exception → returns error envelope."""
        with patch("tushare.pro_api") as mock_pro:
            mock_pro.return_value.limit_list_d.side_effect = RuntimeError("Network error")
            from backtest.loaders.tushare_featured import TushareFeaturedProvider

            provider = TushareFeaturedProvider()
            result = provider.fetch_limit_list()

        assert result["endpoint"] == "limit_list_d"
        assert "Network error" in result["error"]
        assert result["data"] == []


# ---------------------------------------------------------------------------
# fetch_ths_hot
# ---------------------------------------------------------------------------


class TestFetchThsHot:
    def test_normal_return(self, mock_tushare_token, sample_ths_hot_df):
        """Normal ths_hot → returns envelope with data."""
        with patch("tushare.pro_api") as mock_pro:
            mock_pro.return_value.ths_hot.return_value = sample_ths_hot_df
            from backtest.loaders.tushare_featured import TushareFeaturedProvider

            provider = TushareFeaturedProvider()
            result = provider.fetch_ths_hot(trade_date="20240715")

        assert result["endpoint"] == "ths_hot"
        assert result["data_source"] == "tushare"
        assert result["is_provisional"] is False
        assert len(result["data"]) == 2
        assert result["data"][0]["rank"] == 1
        assert result["data"][0]["hot_score"] == 9850.5

    def test_empty_return(self, mock_tushare_token):
        """Empty DataFrame → returns envelope with empty data list."""
        with patch("tushare.pro_api") as mock_pro:
            mock_pro.return_value.ths_hot.return_value = pd.DataFrame()
            from backtest.loaders.tushare_featured import TushareFeaturedProvider

            provider = TushareFeaturedProvider()
            result = provider.fetch_ths_hot()

        assert result["endpoint"] == "ths_hot"
        assert result["data"] == []

    def test_market_filter(self, mock_tushare_token, sample_ths_hot_df):
        """market='A' → only A-share hot rank."""
        with patch("tushare.pro_api") as mock_pro:
            mock_pro.return_value.ths_hot.return_value = sample_ths_hot_df
            from backtest.loaders.tushare_featured import TushareFeaturedProvider

            provider = TushareFeaturedProvider()
            result = provider.fetch_ths_hot(market="A")

        assert result["endpoint"] == "ths_hot"
        call_kwargs = mock_pro.return_value.ths_hot.call_args[1]
        assert call_kwargs["market"] == "A"

    def test_invalid_market(self, mock_tushare_token):
        """Invalid market → error envelope."""
        with patch("tushare.pro_api"):
            from backtest.loaders.tushare_featured import TushareFeaturedProvider

            provider = TushareFeaturedProvider()
            result = provider.fetch_ths_hot(market="US")

        assert "Invalid market" in result["error"]
        assert result["data"] == []

    def test_exception(self, mock_tushare_token):
        """API exception → returns error envelope."""
        with patch("tushare.pro_api") as mock_pro:
            mock_pro.return_value.ths_hot.side_effect = RuntimeError("Network error")
            from backtest.loaders.tushare_featured import TushareFeaturedProvider

            provider = TushareFeaturedProvider()
            result = provider.fetch_ths_hot()

        assert result["endpoint"] == "ths_hot"
        assert "Network error" in result["error"]
        assert result["data"] == []


# ---------------------------------------------------------------------------
# fetch_moneyflow
# ---------------------------------------------------------------------------


class TestFetchMoneyflow:
    def test_normal_return(self, mock_tushare_token):
        """Normal moneyflow → returns envelope with data."""
        df = pd.DataFrame([
            {
                "ts_code": "000001.SZ",
                "trade_date": "20240715",
                "buy_sm_vol": 10000,
                "buy_sm_amount": 120000.0,
                "sell_sm_vol": 8000,
                "sell_sm_amount": 96000.0,
                "buy_md_vol": 25000,
                "buy_md_amount": 300000.0,
                "sell_md_vol": 20000,
                "sell_md_amount": 240000.0,
                "buy_lg_vol": 50000,
                "buy_lg_amount": 600000.0,
                "sell_lg_vol": 45000,
                "sell_lg_amount": 540000.0,
                "buy_elg_vol": 80000,
                "buy_elg_amount": 960000.0,
                "sell_elg_vol": 75000,
                "sell_elg_amount": 900000.0,
                "net_mf_vol": 17000,
                "net_mf_amount": 204000.0,
            },
        ])
        with patch("tushare.pro_api") as mock_pro:
            mock_pro.return_value.moneyflow.return_value = df
            from backtest.loaders.tushare_featured import TushareFeaturedProvider

            provider = TushareFeaturedProvider()
            result = provider.fetch_moneyflow(ts_code="000001.SZ")

        assert result["endpoint"] == "moneyflow"
        assert result["data_source"] == "tushare"
        assert result["is_provisional"] is False
        assert len(result["data"]) == 1
        assert result["data"][0]["ts_code"] == "000001.SZ"
        assert result["data"][0]["net_mf_amount"] == 204000.0

    def test_empty_return(self, mock_tushare_token):
        """Empty DataFrame → returns envelope with empty data list."""
        with patch("tushare.pro_api") as mock_pro:
            mock_pro.return_value.moneyflow.return_value = pd.DataFrame()
            from backtest.loaders.tushare_featured import TushareFeaturedProvider

            provider = TushareFeaturedProvider()
            result = provider.fetch_moneyflow()

        assert result["endpoint"] == "moneyflow"
        assert result["data"] == []

    def test_with_date_range(self, mock_tushare_token):
        """Passes start_date/end_date to Tushare."""
        df = pd.DataFrame([
            {"ts_code": "000001.SZ", "trade_date": "20240715", "net_mf_amount": 100000.0}
        ])
        with patch("tushare.pro_api") as mock_pro:
            mock_pro.return_value.moneyflow.return_value = df
            from backtest.loaders.tushare_featured import TushareFeaturedProvider

            provider = TushareFeaturedProvider()
            result = provider.fetch_moneyflow(
                start_date="20240701", end_date="20240715"
            )

        assert result["endpoint"] == "moneyflow"
        call_kwargs = mock_pro.return_value.moneyflow.call_args[1]
        assert call_kwargs["start_date"] == "20240701"
        assert call_kwargs["end_date"] == "20240715"

    def test_exception(self, mock_tushare_token):
        """API exception → returns error envelope."""
        with patch("tushare.pro_api") as mock_pro:
            mock_pro.return_value.moneyflow.side_effect = RuntimeError("Network error")
            from backtest.loaders.tushare_featured import TushareFeaturedProvider

            provider = TushareFeaturedProvider()
            result = provider.fetch_moneyflow()

        assert result["endpoint"] == "moneyflow"
        assert "Network error" in result["error"]
        assert result["data"] == []


# ---------------------------------------------------------------------------
# fetch_top_list
# ---------------------------------------------------------------------------


class TestFetchTopList:
    def test_normal_return(self, mock_tushare_token):
        """Normal top_list → returns envelope with data."""
        df = pd.DataFrame([
            {
                "trade_date": "20240715",
                "ts_code": "000001.SZ",
                "name": "平安银行",
                "close": 12.50,
                "pct_change": 10.05,
                "turnover_rate": 5.2,
                "amount": 500000000.0,
                "l_buy": 30000000.0,
                "l_sell": 20000000.0,
                "net_amount": 10000000.0,
                "reason": "日涨幅偏离值达到7%",
            },
        ])
        with patch("tushare.pro_api") as mock_pro:
            mock_pro.return_value.top_list.return_value = df
            from backtest.loaders.tushare_featured import TushareFeaturedProvider

            provider = TushareFeaturedProvider()
            result = provider.fetch_top_list(trade_date="20240715")

        assert result["endpoint"] == "top_list"
        assert result["data_source"] == "tushare"
        assert result["is_provisional"] is False
        assert len(result["data"]) == 1
        assert result["data"][0]["ts_code"] == "000001.SZ"
        assert result["data"][0]["name"] == "平安银行"
        assert "reason" in result["data"][0]

    def test_empty_return(self, mock_tushare_token):
        """Empty DataFrame → returns envelope with empty data list."""
        with patch("tushare.pro_api") as mock_pro:
            mock_pro.return_value.top_list.return_value = pd.DataFrame()
            from backtest.loaders.tushare_featured import TushareFeaturedProvider

            provider = TushareFeaturedProvider()
            result = provider.fetch_top_list()

        assert result["endpoint"] == "top_list"
        assert result["data"] == []

    def test_with_ts_code_filter(self, mock_tushare_token):
        """Passes ts_code to Tushare for single-stock lookup."""
        df = pd.DataFrame([
            {"trade_date": "20240715", "ts_code": "600519.SH",
             "name": "贵州茅台", "close": 1800.0, "pct_change": 10.0}
        ])
        with patch("tushare.pro_api") as mock_pro:
            mock_pro.return_value.top_list.return_value = df
            from backtest.loaders.tushare_featured import TushareFeaturedProvider

            provider = TushareFeaturedProvider()
            result = provider.fetch_top_list(
                trade_date="20240715", ts_code="600519.SH"
            )

        assert result["endpoint"] == "top_list"
        call_kwargs = mock_pro.return_value.top_list.call_args[1]
        assert call_kwargs["ts_code"] == "600519.SH"

    def test_exception(self, mock_tushare_token):
        """API exception → returns error envelope."""
        with patch("tushare.pro_api") as mock_pro:
            mock_pro.return_value.top_list.side_effect = RuntimeError("Network error")
            from backtest.loaders.tushare_featured import TushareFeaturedProvider

            provider = TushareFeaturedProvider()
            result = provider.fetch_top_list()

        assert result["endpoint"] == "top_list"
        assert "Network error" in result["error"]
        assert result["data"] == []


# ---------------------------------------------------------------------------
# fetch_margin_detail
# ---------------------------------------------------------------------------


class TestFetchMarginDetail:
    def test_normal_return(self, mock_tushare_token):
        """Normal margin_detail → returns envelope with data."""
        df = pd.DataFrame([
            {
                "ts_code": "000001.SZ",
                "trade_date": "20240715",
                "rzye": 5000000000.0,
                "rqye": 200000000.0,
                "rzmre": 300000000.0,
                "rzche": 250000000.0,
                "rqyl": 1000000,
                "rqmcl": 50000,
                "rzrqye": 5200000000.0,
            },
        ])
        with patch("tushare.pro_api") as mock_pro:
            mock_pro.return_value.margin_detail.return_value = df
            from backtest.loaders.tushare_featured import TushareFeaturedProvider

            provider = TushareFeaturedProvider()
            result = provider.fetch_margin_detail(ts_code="000001.SZ")

        assert result["endpoint"] == "margin_detail"
        assert result["data_source"] == "tushare"
        assert result["is_provisional"] is False
        assert len(result["data"]) == 1
        assert result["data"][0]["ts_code"] == "000001.SZ"
        assert result["data"][0]["rzye"] == 5000000000.0
        assert result["data"][0]["rzrqye"] == 5200000000.0

    def test_empty_return(self, mock_tushare_token):
        """Empty DataFrame → returns envelope with empty data list."""
        with patch("tushare.pro_api") as mock_pro:
            mock_pro.return_value.margin_detail.return_value = pd.DataFrame()
            from backtest.loaders.tushare_featured import TushareFeaturedProvider

            provider = TushareFeaturedProvider()
            result = provider.fetch_margin_detail()

        assert result["endpoint"] == "margin_detail"
        assert result["data"] == []

    def test_with_date_range(self, mock_tushare_token):
        """Passes start_date/end_date to Tushare."""
        df = pd.DataFrame([
            {"ts_code": "000001.SZ", "trade_date": "20240715", "rzye": 5e9}
        ])
        with patch("tushare.pro_api") as mock_pro:
            mock_pro.return_value.margin_detail.return_value = df
            from backtest.loaders.tushare_featured import TushareFeaturedProvider

            provider = TushareFeaturedProvider()
            result = provider.fetch_margin_detail(
                start_date="20240701", end_date="20240715"
            )

        assert result["endpoint"] == "margin_detail"
        call_kwargs = mock_pro.return_value.margin_detail.call_args[1]
        assert call_kwargs["start_date"] == "20240701"
        assert call_kwargs["end_date"] == "20240715"

    def test_exception(self, mock_tushare_token):
        """API exception → returns error envelope."""
        with patch("tushare.pro_api") as mock_pro:
            mock_pro.return_value.margin_detail.side_effect = RuntimeError(
                "Network error"
            )
            from backtest.loaders.tushare_featured import TushareFeaturedProvider

            provider = TushareFeaturedProvider()
            result = provider.fetch_margin_detail()

        assert result["endpoint"] == "margin_detail"
        assert "Network error" in result["error"]
        assert result["data"] == []


# ---------------------------------------------------------------------------
# P1-2: fetch_ths_index
# ---------------------------------------------------------------------------


class TestFetchThsIndex:
    def test_normal_return(self, mock_tushare_token):
        """Normal ths_index → returns envelope with data."""
        df = pd.DataFrame([
            {
                "ts_code": "883900.TI",
                "name": "人工智能",
                "type": "N",
                "count": 150,
                "exchange": "A",
                "list_date": "20180101",
            },
            {
                "ts_code": "883901.TI",
                "name": "芯片",
                "type": "N",
                "count": 120,
                "exchange": "A",
                "list_date": "20180101",
            },
        ])
        with patch("tushare.pro_api") as mock_pro:
            mock_pro.return_value.ths_index.return_value = df
            from backtest.loaders.tushare_featured import TushareFeaturedProvider

            provider = TushareFeaturedProvider()
            result = provider.fetch_ths_index(type="N")

        assert result["endpoint"] == "ths_index"
        assert result["data_source"] == "tushare"
        assert result["is_provisional"] is False
        assert len(result["data"]) == 2
        assert result["data"][0]["name"] == "人工智能"
        assert result["data"][0]["type"] == "N"

    def test_empty_return(self, mock_tushare_token):
        """Empty DataFrame → returns envelope with empty data list."""
        with patch("tushare.pro_api") as mock_pro:
            mock_pro.return_value.ths_index.return_value = pd.DataFrame()
            from backtest.loaders.tushare_featured import TushareFeaturedProvider

            provider = TushareFeaturedProvider()
            result = provider.fetch_ths_index()

        assert result["endpoint"] == "ths_index"
        assert result["data"] == []

    def test_exception(self, mock_tushare_token):
        """API exception → returns error envelope."""
        with patch("tushare.pro_api") as mock_pro:
            mock_pro.return_value.ths_index.side_effect = RuntimeError(
                "Network error"
            )
            from backtest.loaders.tushare_featured import TushareFeaturedProvider

            provider = TushareFeaturedProvider()
            result = provider.fetch_ths_index()

        assert result["endpoint"] == "ths_index"
        assert "Network error" in result["error"]
        assert result["data"] == []


# ---------------------------------------------------------------------------
# P1-2: fetch_ths_member
# ---------------------------------------------------------------------------


class TestFetchThsMember:
    def test_normal_return(self, mock_tushare_token):
        """Normal ths_member → returns envelope with data."""
        df = pd.DataFrame([
            {
                "ts_code": "883900.TI",
                "con_code": "000001.SZ",
                "name": "平安银行",
            },
            {
                "ts_code": "883900.TI",
                "con_code": "600519.SH",
                "name": "贵州茅台",
            },
        ])
        with patch("tushare.pro_api") as mock_pro:
            mock_pro.return_value.ths_member.return_value = df
            from backtest.loaders.tushare_featured import TushareFeaturedProvider

            provider = TushareFeaturedProvider()
            result = provider.fetch_ths_member(ts_code="883900.TI")

        assert result["endpoint"] == "ths_member"
        assert result["data_source"] == "tushare"
        assert result["is_provisional"] is False
        assert len(result["data"]) == 2
        assert result["data"][0]["con_code"] == "000001.SZ"
        assert result["data"][1]["name"] == "贵州茅台"

    def test_empty_return(self, mock_tushare_token):
        """Empty DataFrame → returns envelope with empty data list."""
        with patch("tushare.pro_api") as mock_pro:
            mock_pro.return_value.ths_member.return_value = pd.DataFrame()
            from backtest.loaders.tushare_featured import TushareFeaturedProvider

            provider = TushareFeaturedProvider()
            result = provider.fetch_ths_member()

        assert result["endpoint"] == "ths_member"
        assert result["data"] == []


# ---------------------------------------------------------------------------
# P1-2: fetch_share_float
# ---------------------------------------------------------------------------


class TestFetchShareFloat:
    def test_normal_return(self, mock_tushare_token):
        """Normal share_float → returns envelope with data."""
        df = pd.DataFrame([
            {
                "ts_code": "000001.SZ",
                "trade_date": "20240715",
                "float_share": 5000000000.0,
                "free_share": 3000000000.0,
                "total_share": 10000000000.0,
                "ann_date": "20240710",
            },
        ])
        with patch("tushare.pro_api") as mock_pro:
            mock_pro.return_value.share_float.return_value = df
            from backtest.loaders.tushare_featured import TushareFeaturedProvider

            provider = TushareFeaturedProvider()
            result = provider.fetch_share_float(ts_code="000001.SZ")

        assert result["endpoint"] == "share_float"
        assert result["data_source"] == "tushare"
        assert result["is_provisional"] is False
        assert len(result["data"]) == 1
        assert result["data"][0]["ts_code"] == "000001.SZ"
        assert result["data"][0]["float_share"] == 5000000000.0
        assert result["data"][0]["total_share"] == 10000000000.0

    def test_empty_return(self, mock_tushare_token):
        """Empty DataFrame → returns envelope with empty data list."""
        with patch("tushare.pro_api") as mock_pro:
            mock_pro.return_value.share_float.return_value = pd.DataFrame()
            from backtest.loaders.tushare_featured import TushareFeaturedProvider

            provider = TushareFeaturedProvider()
            result = provider.fetch_share_float()

        assert result["endpoint"] == "share_float"
        assert result["data"] == []

    def test_exception(self, mock_tushare_token):
        """API exception → returns error envelope."""
        with patch("tushare.pro_api") as mock_pro:
            mock_pro.return_value.share_float.side_effect = RuntimeError(
                "Network error"
            )
            from backtest.loaders.tushare_featured import TushareFeaturedProvider

            provider = TushareFeaturedProvider()
            result = provider.fetch_share_float()

        assert result["endpoint"] == "share_float"
        assert "Network error" in result["error"]
        assert result["data"] == []


# ---------------------------------------------------------------------------
# P2-1: fetch_pledge_stat
# ---------------------------------------------------------------------------


class TestFetchPledgeStat:
    def test_normal_return(self, mock_tushare_token):
        """Normal pledge_stat → returns envelope with data."""
        df = pd.DataFrame([
            {
                "ts_code": "000001.SZ",
                "end_date": "20240715",
                "pledge_ratio": 15.5,
                "pledge_total": 5000000,
            },
        ])
        with patch("tushare.pro_api") as mock_pro:
            mock_pro.return_value.pledge_stat.return_value = df
            from backtest.loaders.tushare_featured import TushareFeaturedProvider

            provider = TushareFeaturedProvider()
            result = provider.fetch_pledge_stat(ts_code="000001.SZ")

        assert result["endpoint"] == "pledge_stat"
        assert result["data_source"] == "tushare"
        assert result["is_provisional"] is False
        assert len(result["data"]) == 1
        assert result["data"][0]["ts_code"] == "000001.SZ"
        assert result["data"][0]["pledge_ratio"] == 15.5

    def test_empty_return(self, mock_tushare_token):
        """Empty DataFrame → returns envelope with empty data list."""
        with patch("tushare.pro_api") as mock_pro:
            mock_pro.return_value.pledge_stat.return_value = pd.DataFrame()
            from backtest.loaders.tushare_featured import TushareFeaturedProvider

            provider = TushareFeaturedProvider()
            result = provider.fetch_pledge_stat()

        assert result["endpoint"] == "pledge_stat"
        assert result["data"] == []


# ---------------------------------------------------------------------------
# P2-1: fetch_repurchase
# ---------------------------------------------------------------------------


class TestFetchRepurchase:
    def test_normal_return(self, mock_tushare_token):
        """Normal repurchase → returns envelope with data."""
        df = pd.DataFrame([
            {
                "ts_code": "000001.SZ",
                "ann_date": "20240710",
                "vol": 10000000.0,
                "amount": 120000000.0,
                "high_limit": 13.00,
                "low_limit": 11.00,
            },
        ])
        with patch("tushare.pro_api") as mock_pro:
            mock_pro.return_value.repurchase.return_value = df
            from backtest.loaders.tushare_featured import TushareFeaturedProvider

            provider = TushareFeaturedProvider()
            result = provider.fetch_repurchase(ts_code="000001.SZ", ann_date="20240710")

        assert result["endpoint"] == "repurchase"
        assert result["data_source"] == "tushare"
        assert result["is_provisional"] is False
        assert len(result["data"]) == 1
        assert result["data"][0]["ts_code"] == "000001.SZ"
        assert result["data"][0]["amount"] == 120000000.0

    def test_empty_return(self, mock_tushare_token):
        """Empty DataFrame → returns envelope with empty data list."""
        with patch("tushare.pro_api") as mock_pro:
            mock_pro.return_value.repurchase.return_value = pd.DataFrame()
            from backtest.loaders.tushare_featured import TushareFeaturedProvider

            provider = TushareFeaturedProvider()
            result = provider.fetch_repurchase()

        assert result["endpoint"] == "repurchase"
        assert result["data"] == []


# ---------------------------------------------------------------------------
# P2-1: fetch_holdertrade
# ---------------------------------------------------------------------------


class TestFetchHoldertrade:
    def test_normal_return(self, mock_tushare_token):
        """Normal holdertrade → returns envelope with data."""
        df = pd.DataFrame([
            {
                "ts_code": "000001.SZ",
                "ann_date": "20240710",
                "holder_name": "张三",
                "change_vol": 500000,
                "change_ratio": 0.05,
            },
        ])
        with patch("tushare.pro_api") as mock_pro:
            mock_pro.return_value.stk_holdertrade.return_value = df
            from backtest.loaders.tushare_featured import TushareFeaturedProvider

            provider = TushareFeaturedProvider()
            result = provider.fetch_holdertrade(ts_code="000001.SZ", ann_date="20240710")

        assert result["endpoint"] == "holdertrade"
        assert result["data_source"] == "tushare"
        assert result["is_provisional"] is False
        assert len(result["data"]) == 1
        assert result["data"][0]["ts_code"] == "000001.SZ"
        assert result["data"][0]["holder_name"] == "张三"

    def test_empty_return(self, mock_tushare_token):
        """Empty DataFrame → returns envelope with empty data list."""
        with patch("tushare.pro_api") as mock_pro:
            mock_pro.return_value.stk_holdertrade.return_value = pd.DataFrame()
            from backtest.loaders.tushare_featured import TushareFeaturedProvider

            provider = TushareFeaturedProvider()
            result = provider.fetch_holdertrade()

        assert result["endpoint"] == "holdertrade"
        assert result["data"] == []


# ---------------------------------------------------------------------------
# P2-1: fetch_stock_st
# ---------------------------------------------------------------------------


class TestFetchStockSt:
    def test_normal_return(self, mock_tushare_token):
        """Normal stock_st → returns envelope with data."""
        df = pd.DataFrame([
            {
                "ts_code": "000001.SZ",
                "name": "平安银行",
                "type": "ST",
                "ann_date": "20240710",
            },
        ])
        with patch("tushare.pro_api") as mock_pro:
            mock_pro.return_value.stock_st.return_value = df
            from backtest.loaders.tushare_featured import TushareFeaturedProvider

            provider = TushareFeaturedProvider()
            result = provider.fetch_stock_st(trade_date="20240715")

        assert result["endpoint"] == "stock_st"
        assert result["data_source"] == "tushare"
        assert result["is_provisional"] is False
        assert len(result["data"]) == 1
        assert result["data"][0]["ts_code"] == "000001.SZ"
        assert result["data"][0]["type"] == "ST"

    def test_empty_return(self, mock_tushare_token):
        """Empty DataFrame → returns envelope with empty data list."""
        with patch("tushare.pro_api") as mock_pro:
            mock_pro.return_value.stock_st.return_value = pd.DataFrame()
            from backtest.loaders.tushare_featured import TushareFeaturedProvider

            provider = TushareFeaturedProvider()
            result = provider.fetch_stock_st()

        assert result["endpoint"] == "stock_st"
        assert result["data"] == []


# ---------------------------------------------------------------------------
# P2-1: fetch_hsgt_stocks
# ---------------------------------------------------------------------------


class TestFetchHsgtStocks:
    def test_normal_return(self, mock_tushare_token):
        """Normal hsgt_stocks → returns envelope with data."""
        df = pd.DataFrame([
            {
                "ts_code": "000001.SZ",
                "name": "平安银行",
                "market": "SH",
                "buy_amount": 500000000.0,
                "sell_amount": 300000000.0,
            },
        ])
        with patch("tushare.pro_api") as mock_pro:
            mock_pro.return_value.stock_hsgt.return_value = df
            from backtest.loaders.tushare_featured import TushareFeaturedProvider

            provider = TushareFeaturedProvider()
            result = provider.fetch_hsgt_stocks(trade_date="20240715")

        assert result["endpoint"] == "hsgt_stocks"
        assert result["data_source"] == "tushare"
        assert result["is_provisional"] is False
        assert len(result["data"]) == 1
        assert result["data"][0]["ts_code"] == "000001.SZ"
        assert result["data"][0]["market"] == "SH"

    def test_empty_return(self, mock_tushare_token):
        """Empty DataFrame → returns envelope with empty data list."""
        with patch("tushare.pro_api") as mock_pro:
            mock_pro.return_value.stock_hsgt.return_value = pd.DataFrame()
            from backtest.loaders.tushare_featured import TushareFeaturedProvider

            provider = TushareFeaturedProvider()
            result = provider.fetch_hsgt_stocks()

        assert result["endpoint"] == "hsgt_stocks"
        assert result["data"] == []


# ---------------------------------------------------------------------------
# P2-2: fetch_stk_surv
# ---------------------------------------------------------------------------


class TestFetchStkSurv:
    def test_normal_return(self, mock_tushare_token):
        """Normal stk_surv → returns envelope with data."""
        df = pd.DataFrame([
            {
                "ts_code": "000001.SZ",
                "surv_date": "20240715",
                "fund_name": "中信证券",
                "content": "调研公司上半年经营情况",
            },
        ])
        with patch("tushare.pro_api") as mock_pro:
            mock_pro.return_value.stk_surv.return_value = df
            from backtest.loaders.tushare_featured import TushareFeaturedProvider

            provider = TushareFeaturedProvider()
            result = provider.fetch_stk_surv(ts_code="000001.SZ")

        assert result["endpoint"] == "stk_surv"
        assert result["data_source"] == "tushare"
        assert result["is_provisional"] is False
        assert len(result["data"]) == 1
        assert result["data"][0]["ts_code"] == "000001.SZ"
        assert result["data"][0]["fund_name"] == "中信证券"

    def test_empty_return(self, mock_tushare_token):
        """Empty DataFrame → returns envelope with empty data list."""
        with patch("tushare.pro_api") as mock_pro:
            mock_pro.return_value.stk_surv.return_value = pd.DataFrame()
            from backtest.loaders.tushare_featured import TushareFeaturedProvider

            provider = TushareFeaturedProvider()
            result = provider.fetch_stk_surv()

        assert result["endpoint"] == "stk_surv"
        assert result["data"] == []


# ---------------------------------------------------------------------------
# P2-2: fetch_broker_recommend
# ---------------------------------------------------------------------------


class TestFetchBrokerRecommend:
    def test_normal_return(self, mock_tushare_token):
        """Normal broker_recommend → returns envelope with data."""
        df = pd.DataFrame([
            {
                "month": "202407",
                "ts_code": "000001.SZ",
                "name": "平安银行",
                "broker": "中信证券",
                "reason": "业绩超预期",
            },
        ])
        with patch("tushare.pro_api") as mock_pro:
            mock_pro.return_value.broker_recommend.return_value = df
            from backtest.loaders.tushare_featured import TushareFeaturedProvider

            provider = TushareFeaturedProvider()
            result = provider.fetch_broker_recommend(month="202407")

        assert result["endpoint"] == "broker_recommend"
        assert result["data_source"] == "tushare"
        assert result["is_provisional"] is False
        assert len(result["data"]) == 1
        assert result["data"][0]["ts_code"] == "000001.SZ"
        assert result["data"][0]["broker"] == "中信证券"

    def test_empty_return(self, mock_tushare_token):
        """Empty DataFrame → returns envelope with empty data list."""
        with patch("tushare.pro_api") as mock_pro:
            mock_pro.return_value.broker_recommend.return_value = pd.DataFrame()
            from backtest.loaders.tushare_featured import TushareFeaturedProvider

            provider = TushareFeaturedProvider()
            result = provider.fetch_broker_recommend()

        assert result["endpoint"] == "broker_recommend"
        assert result["data"] == []


# ---------------------------------------------------------------------------
# P2-2: fetch_cn_macro
# ---------------------------------------------------------------------------


class TestFetchCnMacro:
    def test_gdp_normal_return(self, mock_tushare_token):
        """indicator='gdp' → returns envelope with data."""
        df = pd.DataFrame([
            {"year": "2024", "quarter": "Q2", "gdp": 320000.0},
        ])
        with patch("tushare.pro_api") as mock_pro:
            mock_pro.return_value.cn_gdp.return_value = df
            from backtest.loaders.tushare_featured import TushareFeaturedProvider

            provider = TushareFeaturedProvider()
            result = provider.fetch_cn_macro(indicator="gdp")

        assert result["endpoint"] == "cn_gdp"
        assert result["data_source"] == "tushare"
        assert len(result["data"]) == 1
        assert result["data"][0]["gdp"] == 320000.0

    def test_gdp_empty_return(self, mock_tushare_token):
        """indicator='gdp' with empty DF."""
        with patch("tushare.pro_api") as mock_pro:
            mock_pro.return_value.cn_gdp.return_value = pd.DataFrame()
            from backtest.loaders.tushare_featured import TushareFeaturedProvider

            provider = TushareFeaturedProvider()
            result = provider.fetch_cn_macro(indicator="gdp")

        assert result["endpoint"] == "cn_gdp"
        assert result["data"] == []

    def test_cpi_normal_return(self, mock_tushare_token):
        """indicator='cpi' → returns envelope with data."""
        df = pd.DataFrame([
            {"year": "2024", "month": "07", "cpi": 0.3},
        ])
        with patch("tushare.pro_api") as mock_pro:
            mock_pro.return_value.cn_cpi.return_value = df
            from backtest.loaders.tushare_featured import TushareFeaturedProvider

            provider = TushareFeaturedProvider()
            result = provider.fetch_cn_macro(indicator="cpi")

        assert result["endpoint"] == "cn_cpi"
        assert result["data_source"] == "tushare"
        assert len(result["data"]) == 1
        assert result["data"][0]["cpi"] == 0.3

    def test_cpi_empty_return(self, mock_tushare_token):
        """indicator='cpi' with empty DF."""
        with patch("tushare.pro_api") as mock_pro:
            mock_pro.return_value.cn_cpi.return_value = pd.DataFrame()
            from backtest.loaders.tushare_featured import TushareFeaturedProvider

            provider = TushareFeaturedProvider()
            result = provider.fetch_cn_macro(indicator="cpi")

        assert result["endpoint"] == "cn_cpi"
        assert result["data"] == []

    def test_ppi_normal_return(self, mock_tushare_token):
        """indicator='ppi' → returns envelope with data."""
        df = pd.DataFrame([
            {"year": "2024", "month": "07", "ppi": -1.2},
        ])
        with patch("tushare.pro_api") as mock_pro:
            mock_pro.return_value.cn_ppi.return_value = df
            from backtest.loaders.tushare_featured import TushareFeaturedProvider

            provider = TushareFeaturedProvider()
            result = provider.fetch_cn_macro(indicator="ppi")

        assert result["endpoint"] == "cn_ppi"
        assert result["data_source"] == "tushare"
        assert len(result["data"]) == 1
        assert result["data"][0]["ppi"] == -1.2

    def test_ppi_empty_return(self, mock_tushare_token):
        """indicator='ppi' with empty DF."""
        with patch("tushare.pro_api") as mock_pro:
            mock_pro.return_value.cn_ppi.return_value = pd.DataFrame()
            from backtest.loaders.tushare_featured import TushareFeaturedProvider

            provider = TushareFeaturedProvider()
            result = provider.fetch_cn_macro(indicator="ppi")

        assert result["endpoint"] == "cn_ppi"
        assert result["data"] == []

    def test_shibor_normal_return(self, mock_tushare_token):
        """indicator='shibor' → returns envelope with data."""
        df = pd.DataFrame([
            {"date": "20240715", "on": 1.8, "1w": 1.9},
        ])
        with patch("tushare.pro_api") as mock_pro:
            mock_pro.return_value.shibor.return_value = df
            from backtest.loaders.tushare_featured import TushareFeaturedProvider

            provider = TushareFeaturedProvider()
            result = provider.fetch_cn_macro(indicator="shibor")

        assert result["endpoint"] == "shibor"
        assert result["data_source"] == "tushare"
        assert len(result["data"]) == 1
        assert result["data"][0]["on"] == 1.8

    def test_shibor_empty_return(self, mock_tushare_token):
        """indicator='shibor' with empty DF."""
        with patch("tushare.pro_api") as mock_pro:
            mock_pro.return_value.shibor.return_value = pd.DataFrame()
            from backtest.loaders.tushare_featured import TushareFeaturedProvider

            provider = TushareFeaturedProvider()
            result = provider.fetch_cn_macro(indicator="shibor")

        assert result["endpoint"] == "shibor"
        assert result["data"] == []

    def test_missing_indicator(self, mock_tushare_token):
        """Missing indicator → error envelope."""
        with patch("tushare.pro_api"):
            from backtest.loaders.tushare_featured import TushareFeaturedProvider

            provider = TushareFeaturedProvider()
            result = provider.fetch_cn_macro()

        assert "indicator is required" in result["error"]
        assert result["data"] == []

    def test_invalid_indicator(self, mock_tushare_token):
        """Invalid indicator → error envelope."""
        with patch("tushare.pro_api"):
            from backtest.loaders.tushare_featured import TushareFeaturedProvider

            provider = TushareFeaturedProvider()
            result = provider.fetch_cn_macro(indicator="money_supply")

        assert "Invalid indicator" in result["error"]
        assert result["data"] == []


# ---------------------------------------------------------------------------
# P2-2: fetch_forecast
# ---------------------------------------------------------------------------


class TestFetchForecast:
    def test_normal_return_report_rc(self, mock_tushare_token):
        """report_rc succeeds → returns forecast envelope with report_rc data."""
        df = pd.DataFrame([
            {
                "ts_code": "000001.SZ",
                "ann_date": "20240710",
                "f_ann_date": "20241231",
                "eps": 1.50,
                "pe": 8.0,
            },
        ])
        with patch("tushare.pro_api") as mock_pro:
            mock_pro.return_value.report_rc.return_value = df
            from backtest.loaders.tushare_featured import TushareFeaturedProvider

            provider = TushareFeaturedProvider()
            result = provider.fetch_forecast(ts_code="000001.SZ")

        assert result["endpoint"] == "forecast"
        assert result["data_source"] == "tushare"
        assert result["is_provisional"] is False
        assert len(result["data"]) == 1
        assert result["data"][0]["ts_code"] == "000001.SZ"
        assert result["data"][0]["eps"] == 1.50
        # report_rc was the underlying call (endpoint was rewritten to forecast)
        assert "error" not in result

    def test_normal_return_forecast_fallback(self, mock_tushare_token):
        """report_rc fails → falls back to forecast."""
        # report_rc raises, forecast returns data
        df = pd.DataFrame([
            {
                "ts_code": "000001.SZ",
                "end_date": "20241231",
                "type": "预增",
                "p_change_min": 20.0,
                "p_change_max": 50.0,
            },
        ])
        with patch("tushare.pro_api") as mock_pro:
            mock_pro.return_value.report_rc.side_effect = RuntimeError("No data")
            mock_pro.return_value.forecast.return_value = df
            from backtest.loaders.tushare_featured import TushareFeaturedProvider

            provider = TushareFeaturedProvider()
            result = provider.fetch_forecast(ts_code="000001.SZ")

        assert result["endpoint"] == "forecast"
        assert result["data_source"] == "tushare"
        assert len(result["data"]) == 1
        assert result["data"][0]["type"] == "预增"
