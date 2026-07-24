"""Tests for virtual market simulator (market_sim.py)."""

from __future__ import annotations

import pytest

from src.trading.connectors.virtual.market_sim import (
    DEFAULT_SYMBOLS,
    Quote,
    historical_bars,
    infer_currency,
    infer_market,
    normalize_symbol,
    quote as market_quote,
)


def _make_quote_stub(symbol: str, last: float, source: str = "stub") -> Quote:
    """Minimal Quote factory for monkeypatch stubs."""
    return Quote(
        symbol=symbol,
        bid=round(last * 0.9998, 4),
        ask=round(last * 1.0002, 4),
        last=round(last, 4),
        time="2026-07-12T00:00:00.000Z",
        source=source,
    )


pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Market inference
# ---------------------------------------------------------------------------


def test_infer_market_a_share() -> None:
    """000001.SZ → a_share, 600519.SH → a_share, 833171.BJ → a_share."""
    assert infer_market("000001.SZ") == "a_share"
    assert infer_market("600519.SH") == "a_share"
    assert infer_market("833171.BJ") == "a_share"


def test_infer_market_us_equity() -> None:
    """AAPL → us_equity."""
    assert infer_market("AAPL") == "us_equity"
    assert infer_market("MSFT") == "us_equity"


def test_infer_market_hk_equity() -> None:
    """00700.HK → hk_equity."""
    assert infer_market("00700.HK") == "hk_equity"


# ---------------------------------------------------------------------------
# Symbol normalization
# ---------------------------------------------------------------------------


def test_normalize_symbol_prefix() -> None:
    """SZ.000001 → 000001.SZ."""
    assert normalize_symbol("SZ.000001") == "000001.SZ"
    assert normalize_symbol("SH.600000") == "600000.SH"
    assert normalize_symbol("BJ.830000") == "830000.BJ"
    assert normalize_symbol("HK.00001") == "00001.HK"


def test_normalize_symbol_no_change() -> None:
    """AAPL → AAPL."""
    assert normalize_symbol("AAPL") == "AAPL"
    assert normalize_symbol("000001.SZ") == "000001.SZ"


def test_normalize_symbol_cn() -> None:
    """CN. 前缀：纯数字 code 转为后缀 .CN."""
    # CN.000001 → 000001.CN (code is all digits)
    assert normalize_symbol("CN.000001") == "000001.CN"


# ---------------------------------------------------------------------------
# Currency inference
# ---------------------------------------------------------------------------


def test_infer_currency() -> None:
    """a_share → CNY, us_equity → USD, hk_equity → HKD."""
    assert infer_currency("a_share") == "CNY"
    assert infer_currency("us_equity") == "USD"
    assert infer_currency("hk_equity") == "HKD"


def test_infer_currency_unknown() -> None:
    """未知 market → USD (fallback)."""
    assert infer_currency("unknown") == "USD"


# ---------------------------------------------------------------------------
# Quote — A-share fallback
# ---------------------------------------------------------------------------


def test_quote_a_share_fallback(monkeypatch) -> None:
    """A 股 fallback quote（loader 不可用时走 deterministic drift，使用 base_prices）."""
    # Stub out the fallback chain so all loaders are skipped.
    monkeypatch.setattr(
        "backtest.loaders.registry.FALLBACK_CHAINS",
        {"a_share": []},
    )
    q = market_quote(
        "000001.SZ",
        universe=("000001.SZ",),
        price_source="auto",
        market="a_share",
        allow_dynamic_symbols=False,
        base_prices={"000001.SZ": 15.0},
    )
    assert q.symbol == "000001.SZ"
    assert q.last > 0
    assert q.bid > 0
    assert q.ask > 0
    assert q.source == "base_prices"


def test_quote_a_share_no_base_prices(monkeypatch) -> None:
    """A 股动态标的无 base_prices 时返回 price_unavailable（不静默使用 100.0）."""
    # Stub out the fallback chain so all loaders are skipped.
    monkeypatch.setattr(
        "backtest.loaders.registry.FALLBACK_CHAINS",
        {"a_share": []},
    )
    q = market_quote(
        "000001.SZ",
        universe=("000001.SZ",),
        price_source="auto",
        market="a_share",
        allow_dynamic_symbols=False,
    )
    assert q.symbol == "000001.SZ"
    assert q.source == "price_unavailable"
    assert q.last == 0.0
    assert q.bid == 0.0
    assert q.ask == 0.0


def test_quote_a_share_out_of_universe() -> None:
    """不在 universe 且 allow_dynamic_symbols=False 时拒绝."""
    with pytest.raises(ValueError, match="not in the configured universe"):
        market_quote(
            "000001.SZ",
            universe=DEFAULT_SYMBOLS,
            market="us_equity",
            allow_dynamic_symbols=False,
        )


def test_quote_a_share_dynamic_symbols(monkeypatch) -> None:
    """allow_dynamic_symbols=True 时通过（market 匹配，使用 base_prices）."""
    # Stub out the fallback chain so all loaders are skipped.
    monkeypatch.setattr(
        "backtest.loaders.registry.FALLBACK_CHAINS",
        {"a_share": []},
    )
    q = market_quote(
        "000001.SZ",
        universe=(),  # empty universe
        price_source="fallback",
        market="a_share",
        allow_dynamic_symbols=True,
        base_prices={"000001.SZ": 15.0},
    )
    assert q.symbol == "000001.SZ"
    assert q.source == "base_prices"


def test_quote_us_equity_unchanged(monkeypatch) -> None:
    """美股路径不变（fallback quote 正常返回）."""
    monkeypatch.setattr(
        "src.trading.connectors.virtual.market_sim._try_yfinance_quote",
        lambda sym: None,
    )
    q = market_quote(
        "AAPL",
        universe=DEFAULT_SYMBOLS,
        price_source="fallback",
        market="us_equity",
        allow_dynamic_symbols=False,
    )
    assert q.symbol == "AAPL"
    assert q.last > 0
    assert q.source == "fallback"


# ---------------------------------------------------------------------------
# Universe check
# ---------------------------------------------------------------------------


def test_symbol_in_universe_dynamic() -> None:
    """_symbol_in_universe 动态检查."""
    from src.trading.connectors.virtual.market_sim import _symbol_in_universe

    # In explicit universe
    assert _symbol_in_universe("AAPL", universe=DEFAULT_SYMBOLS) is True
    # Not in universe, no dynamic
    assert _symbol_in_universe("TSM", universe=DEFAULT_SYMBOLS) is False
    # Dynamic A-share
    assert _symbol_in_universe(
        "000001.SZ", universe=(), market="a_share", allow_dynamic_symbols=True
    ) is True
    # Dynamic but market mismatch
    assert _symbol_in_universe(
        "000001.SZ", universe=(), market="us_equity", allow_dynamic_symbols=True
    ) is False


# ---------------------------------------------------------------------------
# Historical bars — A-share fallback
# ---------------------------------------------------------------------------


def test_historical_bars_a_share_fallback(monkeypatch) -> None:
    """A 股 fallback bars（loader 不可用时走 synthetic bars，使用 base_prices）."""
    # Stub out the fallback chain so all loaders are skipped.
    monkeypatch.setattr(
        "backtest.loaders.registry.FALLBACK_CHAINS",
        {"a_share": []},
    )
    result = historical_bars(
        "000001.SZ",
        period="1d",
        limit=5,
        universe=("000001.SZ",),
        price_source="auto",
        market="a_share",
        allow_dynamic_symbols=False,
        base_prices={"000001.SZ": 15.0},
    )
    assert result["status"] == "ok"
    assert result["symbol"] == "000001.SZ"
    assert result["source"] == "base_prices"
    assert len(result["bars"]) == 5
    assert "open" in result["bars"][0]
    assert "close" in result["bars"][0]


def test_historical_bars_a_share_no_base_prices(monkeypatch) -> None:
    """A 股无 base_prices 时 historical_bars 返回 error."""
    # Stub out the fallback chain so all loaders are skipped.
    monkeypatch.setattr(
        "backtest.loaders.registry.FALLBACK_CHAINS",
        {"a_share": []},
    )
    result = historical_bars(
        "000001.SZ",
        period="1d",
        limit=5,
        universe=("000001.SZ",),
        price_source="auto",
        market="a_share",
        allow_dynamic_symbols=False,
    )
    assert result["status"] == "error"
    assert result["source"] == "price_unavailable"
    assert len(result["bars"]) == 0
