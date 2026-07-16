"""Tests for TushareAuctionProvider (``tushare_auction.py``).

All tests mock ``tushare.pro_api`` — no real API calls are made.
"""

from __future__ import annotations

import sys
from unittest.mock import patch

import pandas as pd
import pytest

sys.path.insert(0, "")

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_token(monkeypatch):
    monkeypatch.setenv("TUSHARE_TOKEN", "mock-token-for-auction-tests")


@pytest.fixture
def sample_auction_df():
    """Minimal DataFrame matching the stk_auction schema."""
    return pd.DataFrame([
        {"ts_code": "000001.SZ", "trade_date": "20260716", "price": 12.50,
         "volume": 500000.0, "amount": 6250000.0},
    ])


# ---------------------------------------------------------------------------
# Provider init
# ---------------------------------------------------------------------------


class TestAuctionProviderInit:
    def test_raises_when_token_empty(self, monkeypatch):
        monkeypatch.setenv("TUSHARE_TOKEN", "")
        from backtest.loaders.tushare_auction import TushareAuctionProvider

        with pytest.raises(RuntimeError, match="Tushare token not configured"):
            TushareAuctionProvider()

    def test_raises_when_token_placeholder(self, monkeypatch):
        monkeypatch.setenv("TUSHARE_TOKEN", "your-tushare-token")
        from backtest.loaders.tushare_auction import TushareAuctionProvider

        with pytest.raises(RuntimeError, match="Tushare token not configured"):
            TushareAuctionProvider()


# ---------------------------------------------------------------------------
# fetch_current_auction (stk_auction)
# ---------------------------------------------------------------------------


class TestCurrentAuction:
    def test_is_provisional_true(self, mock_token, sample_auction_df):
        with patch("tushare.pro_api") as mock_pro:
            mock_pro.return_value.stk_auction.return_value = sample_auction_df
            from backtest.loaders.tushare_auction import TushareAuctionProvider

            provider = TushareAuctionProvider()
            result = provider.fetch_current_auction(codes=["000001.SZ"])

        assert result["is_provisional"] is True
        assert result["endpoint"] == "stk_auction"
        assert result["data_source"] == "tushare"

    def test_returns_data_per_code(self, mock_token, sample_auction_df):
        with patch("tushare.pro_api") as mock_pro:
            mock_pro.return_value.stk_auction.return_value = sample_auction_df
            from backtest.loaders.tushare_auction import TushareAuctionProvider

            provider = TushareAuctionProvider()
            result = provider.fetch_current_auction(codes=["000001.SZ"])

        assert "000001.SZ" in result["data"]

    def test_api_exception_caught(self, mock_token):
        with patch("tushare.pro_api") as mock_pro:
            mock_pro.return_value.stk_auction.side_effect = RuntimeError("boom")
            from backtest.loaders.tushare_auction import TushareAuctionProvider

            provider = TushareAuctionProvider()
            result = provider.fetch_current_auction()

        assert "error" in result
        assert "boom" in result["error"]
        assert result["data"] == {}


# ---------------------------------------------------------------------------
# fetch_opening_auction_history (stk_auction_o)
# ---------------------------------------------------------------------------


class TestOpeningAuctionHistory:
    def test_is_provisional_false(self, mock_token, sample_auction_df):
        with patch("tushare.pro_api") as mock_pro:
            mock_pro.return_value.stk_auction_o.return_value = sample_auction_df
            from backtest.loaders.tushare_auction import TushareAuctionProvider

            provider = TushareAuctionProvider()
            result = provider.fetch_opening_auction_history(codes=["000001.SZ"])

        assert result["is_provisional"] is False
        assert result["endpoint"] == "stk_auction_o"

    def test_passes_date_range(self, mock_token, sample_auction_df):
        with patch("tushare.pro_api") as mock_pro:
            mock_pro.return_value.stk_auction_o.return_value = sample_auction_df
            from backtest.loaders.tushare_auction import TushareAuctionProvider

            provider = TushareAuctionProvider()
            provider.fetch_opening_auction_history(
                codes=["000001.SZ"],
                start_date="20260101",
                end_date="20260716",
            )

        call_args = mock_pro.return_value.stk_auction_o.call_args
        assert call_args[1]["start_date"] == "20260101"
        assert call_args[1]["end_date"] == "20260716"

    def test_passes_fields(self, mock_token, sample_auction_df):
        with patch("tushare.pro_api") as mock_pro:
            mock_pro.return_value.stk_auction_o.return_value = sample_auction_df
            from backtest.loaders.tushare_auction import TushareAuctionProvider

            provider = TushareAuctionProvider()
            provider.fetch_opening_auction_history(
                codes=["000001.SZ"],
                fields=["price", "volume"],
            )

        call_args = mock_pro.return_value.stk_auction_o.call_args
        assert "price,volume" in call_args[1]["fields"]


# ---------------------------------------------------------------------------
# fetch_closing_auction_history (stk_auction_c)
# ---------------------------------------------------------------------------


class TestClosingAuctionHistory:
    def test_is_provisional_false(self, mock_token, sample_auction_df):
        with patch("tushare.pro_api") as mock_pro:
            mock_pro.return_value.stk_auction_c.return_value = sample_auction_df
            from backtest.loaders.tushare_auction import TushareAuctionProvider

            provider = TushareAuctionProvider()
            result = provider.fetch_closing_auction_history(codes=["000001.SZ"])

        assert result["is_provisional"] is False
        assert result["endpoint"] == "stk_auction_c"

    def test_api_exception_caught(self, mock_token):
        with patch("tushare.pro_api") as mock_pro:
            mock_pro.return_value.stk_auction_c.side_effect = ValueError("bad param")
            from backtest.loaders.tushare_auction import TushareAuctionProvider

            provider = TushareAuctionProvider()
            result = provider.fetch_closing_auction_history()

        assert "error" in result
        assert "bad param" in result["error"]


# ---------------------------------------------------------------------------
# Empty / None returns
# ---------------------------------------------------------------------------


class TestEmptyReturns:
    @pytest.mark.parametrize("method_name", [
        "fetch_current_auction",
        "fetch_opening_auction_history",
        "fetch_closing_auction_history",
    ])
    def test_none_return_gives_empty_data(self, mock_token, method_name):
        with patch("tushare.pro_api") as mock_pro:
            # All three endpoint methods return None
            mock_pro.return_value.stk_auction.return_value = None
            mock_pro.return_value.stk_auction_o.return_value = None
            mock_pro.return_value.stk_auction_c.return_value = None
            from backtest.loaders.tushare_auction import TushareAuctionProvider

            provider = TushareAuctionProvider()
            method = getattr(provider, method_name)
            result = method()

        assert result["data"] == {}
        assert "error" not in result

    @pytest.mark.parametrize("method_name", [
        "fetch_current_auction",
        "fetch_opening_auction_history",
        "fetch_closing_auction_history",
    ])
    def test_empty_df_gives_empty_data(self, mock_token, method_name):
        with patch("tushare.pro_api") as mock_pro:
            mock_pro.return_value.stk_auction.return_value = pd.DataFrame()
            mock_pro.return_value.stk_auction_o.return_value = pd.DataFrame()
            mock_pro.return_value.stk_auction_c.return_value = pd.DataFrame()
            from backtest.loaders.tushare_auction import TushareAuctionProvider

            provider = TushareAuctionProvider()
            method = getattr(provider, method_name)
            result = method()

        assert result["data"] == {}
