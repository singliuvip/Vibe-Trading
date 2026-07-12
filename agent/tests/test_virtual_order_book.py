"""Tests for virtual order book A-share matching rules (order_book.py)."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from src.trading.connectors.virtual.market_sim import Quote
from src.trading.connectors.virtual.order_book import (
    VirtualAccount,
    VirtualExecution,
    VirtualOrder,
    VirtualPosition,
    VirtualState,
    _now_iso,
    load_or_initialize,
    mark_to_market,
    match_open_orders,
    submit_order,
)

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _quote_stub(
    symbol: str,
    last: float = 100.0,
    bid: float | None = None,
    ask: float | None = None,
    source: str = "stub",
) -> Quote:
    """Build a stub Quote for monkeypatching market_quote."""
    if bid is None:
        bid = round(last * 0.999, 4)
    if ask is None:
        ask = round(last * 1.001, 4)
    return Quote(
        symbol=symbol,
        bid=bid,
        ask=ask,
        last=last,
        time="2026-07-12T00:00:00.000Z",
        source=source,
    )


def _make_state(
    account_id: str = "test",
    currency: str = "CNY",
    cash: float = 1_000_000.0,
    positions: dict | None = None,
    orders: dict | None = None,
    prices: dict | None = None,
) -> VirtualState:
    """Construct a fresh VirtualState for testing."""
    now = _now_iso()
    return VirtualState(
        version=2,
        account=VirtualAccount(
            account_id=account_id,
            currency=currency,
            cash=cash,
            realized_pnl=0.0,
            created_at=now,
            updated_at=now,
        ),
        positions=positions or {},
        orders=orders or {},
        executions=[],
        prices=prices or {},
    )


def _setup_state_dir(monkeypatch, tmp_path: Path, account_id: str = "test") -> Path:
    """Monkeypatch _state_dir to point to tmp_path."""
    state_dir = tmp_path / "virtual" / account_id
    monkeypatch.setattr(
        "src.trading.connectors.virtual.order_book._state_dir",
        lambda aid: state_dir if aid == account_id else tmp_path / "virtual" / aid,
    )
    return state_dir


def _mock_market_quote(monkeypatch, symbol_to_quote: dict[str, Quote]):
    """Replace order_book.market_quote with a stub that returns configured quotes."""

    def _stub(symbol, **kwargs):
        if symbol in symbol_to_quote:
            return symbol_to_quote[symbol]
        # Default fallback quote for any symbol
        return _quote_stub(symbol)

    monkeypatch.setattr(
        "src.trading.connectors.virtual.order_book.market_quote",
        _stub,
    )


# ---------------------------------------------------------------------------
# A-share buy order
# ---------------------------------------------------------------------------


def test_submit_order_a_share_buy_valid(monkeypatch, tmp_path: Path) -> None:
    """A 股 100 股买入通过."""
    _setup_state_dir(monkeypatch, tmp_path)
    q = _quote_stub("000001.SZ", last=15.0)
    _mock_market_quote(monkeypatch, {"000001.SZ": q})

    state = _make_state(currency="CNY")
    result = submit_order(
        state,
        symbol="000001.SZ",
        side="buy",
        quantity=100,
        notional=0,
        order_type="market",
        limit_price=None,
        time_in_force="day",
        slippage_bps=10.0,
        fee_bps=5.0,
        allow_short=False,
        max_leverage=1.0,
        universe=(),
        price_source="fallback",
        market="a_share",
        allow_dynamic_symbols=True,
    )

    assert result["status"] == "ok"
    assert result["order_status"] == "filled"
    assert result["filled_quantity"] > 0
    assert result["filled_price"] > 0
    # Position should exist
    assert "000001.SZ" in state.positions
    assert state.positions["000001.SZ"].quantity == 100


def test_submit_order_a_share_buy_invalid_qty(monkeypatch, tmp_path: Path) -> None:
    """A 股非 100 整数倍拒绝."""
    _setup_state_dir(monkeypatch, tmp_path)
    q = _quote_stub("000001.SZ", last=15.0)
    _mock_market_quote(monkeypatch, {"000001.SZ": q})

    state = _make_state(currency="CNY")
    result = submit_order(
        state,
        symbol="000001.SZ",
        side="buy",
        quantity=150,
        notional=0,
        order_type="market",
        limit_price=None,
        time_in_force="day",
        slippage_bps=10.0,
        fee_bps=5.0,
        allow_short=False,
        max_leverage=1.0,
        universe=(),
        price_source="fallback",
        market="a_share",
        allow_dynamic_symbols=True,
    )

    assert result["status"] == "error"
    assert "multiple" in result["error"] or "at least" in result["error"]
    assert result["order_status"] == "rejected"


# ---------------------------------------------------------------------------
# A-share T+1 sell rules
# ---------------------------------------------------------------------------


def test_submit_order_a_share_sell_same_day(monkeypatch, tmp_path: Path) -> None:
    """A 股同日卖出被拒（T+1）."""
    _setup_state_dir(monkeypatch, tmp_path)
    today = "2026-07-12"
    monkeypatch.setattr(
        "src.trading.connectors.virtual.rules.today_str",
        lambda: today,
    )

    q = _quote_stub("000001.SZ", last=15.0)
    _mock_market_quote(monkeypatch, {"000001.SZ": q})

    state = _make_state(
        currency="CNY",
        positions={
            "000001.SZ": VirtualPosition(
                symbol="000001.SZ",
                quantity=100,
                avg_price=14.0,
                buy_lots=[{"trade_date": today, "quantity": 100, "price": 14.0}],
            )
        },
        prices={"000001.SZ": 15.0},
    )

    result = submit_order(
        state,
        symbol="000001.SZ",
        side="sell",
        quantity=100,
        notional=0,
        order_type="market",
        limit_price=None,
        time_in_force="day",
        slippage_bps=10.0,
        fee_bps=5.0,
        allow_short=False,
        max_leverage=1.0,
        universe=(),
        price_source="fallback",
        market="a_share",
        allow_dynamic_symbols=True,
    )

    assert result["status"] == "error"
    assert "T+1" in result["error"]


def test_submit_order_a_share_sell_next_day(monkeypatch, tmp_path: Path) -> None:
    """A 股次日卖出通过."""
    _setup_state_dir(monkeypatch, tmp_path)
    today = "2026-07-13"
    monkeypatch.setattr(
        "src.trading.connectors.virtual.rules.today_str",
        lambda: today,
    )

    q = _quote_stub("000001.SZ", last=15.0)
    _mock_market_quote(monkeypatch, {"000001.SZ": q})

    state = _make_state(
        currency="CNY",
        positions={
            "000001.SZ": VirtualPosition(
                symbol="000001.SZ",
                quantity=100,
                avg_price=14.0,
                buy_lots=[{"trade_date": "2026-07-12", "quantity": 100, "price": 14.0}],
            )
        },
        prices={"000001.SZ": 15.0},
    )

    result = submit_order(
        state,
        symbol="000001.SZ",
        side="sell",
        quantity=100,
        notional=0,
        order_type="market",
        limit_price=None,
        time_in_force="day",
        slippage_bps=10.0,
        fee_bps=5.0,
        allow_short=False,
        max_leverage=1.0,
        universe=(),
        price_source="fallback",
        market="a_share",
        allow_dynamic_symbols=True,
    )

    assert result["status"] == "ok"
    assert result["order_status"] == "filled"


# ---------------------------------------------------------------------------
# Short selling rules
# ---------------------------------------------------------------------------


def test_submit_order_a_share_short_forbidden(monkeypatch, tmp_path: Path) -> None:
    """A 股做空被拒（即使 allow_short=True）."""
    _setup_state_dir(monkeypatch, tmp_path)
    q = _quote_stub("000001.SZ", last=15.0)
    _mock_market_quote(monkeypatch, {"000001.SZ": q})

    state = _make_state(currency="CNY")  # No position

    result = submit_order(
        state,
        symbol="000001.SZ",
        side="sell",
        quantity=100,
        notional=0,
        order_type="market",
        limit_price=None,
        time_in_force="day",
        slippage_bps=10.0,
        fee_bps=5.0,
        allow_short=True,  # allow_short is True, but A-share overrides it
        max_leverage=1.0,
        universe=(),
        price_source="fallback",
        market="a_share",
        allow_dynamic_symbols=True,
    )

    assert result["status"] == "error"
    assert "short" in result["error"].lower()


def test_submit_order_us_equity_short_allowed(monkeypatch, tmp_path: Path) -> None:
    """美股做空可通过."""
    _setup_state_dir(monkeypatch, tmp_path)
    q = _quote_stub("AAPL", last=210.0)
    _mock_market_quote(monkeypatch, {"AAPL": q})

    state = _make_state(currency="USD", cash=500_000.0)  # No position

    result = submit_order(
        state,
        symbol="AAPL",
        side="sell",
        quantity=10,
        notional=0,
        order_type="market",
        limit_price=None,
        time_in_force="day",
        slippage_bps=10.0,
        fee_bps=5.0,
        allow_short=True,
        max_leverage=2.0,
        universe=("AAPL",),
        price_source="fallback",
        market="us_equity",
        allow_dynamic_symbols=False,
    )

    assert result["status"] == "ok"
    assert result["order_status"] == "filled"
    # Short position should be negative
    assert "AAPL" in state.positions
    assert state.positions["AAPL"].quantity == -10


def test_submit_order_us_equity_unchanged(monkeypatch, tmp_path: Path) -> None:
    """美股下单路径不变."""
    _setup_state_dir(monkeypatch, tmp_path)
    q = _quote_stub("AAPL", last=210.0)
    _mock_market_quote(monkeypatch, {"AAPL": q})

    state = _make_state(currency="USD")
    result = submit_order(
        state,
        symbol="AAPL",
        side="buy",
        quantity=10,
        notional=0,
        order_type="market",
        limit_price=None,
        time_in_force="day",
        slippage_bps=10.0,
        fee_bps=5.0,
        allow_short=True,
        max_leverage=2.0,
        universe=("AAPL",),
        price_source="fallback",
        market="us_equity",
        allow_dynamic_symbols=False,
    )

    assert result["status"] == "ok"
    assert result["order_status"] == "filled"
    assert state.positions["AAPL"].quantity == 10


# ---------------------------------------------------------------------------
# A-share fee calculation
# ---------------------------------------------------------------------------


def test_a_share_fee_calculation(monkeypatch, tmp_path: Path) -> None:
    """A 股费用使用 rules.calculate_fees（含佣金、印花税、过户费）."""
    _setup_state_dir(monkeypatch, tmp_path)
    q = _quote_stub("000001.SZ", last=15.0)
    _mock_market_quote(monkeypatch, {"000001.SZ": q})

    state = _make_state(currency="CNY")
    result = submit_order(
        state,
        symbol="000001.SZ",
        side="buy",
        quantity=100,
        notional=0,
        order_type="market",
        limit_price=None,
        time_in_force="day",
        slippage_bps=10.0,
        fee_bps=5.0,
        allow_short=False,
        max_leverage=1.0,
        universe=(),
        price_source="fallback",
        market="a_share",
        allow_dynamic_symbols=True,
    )

    # Fee should reflect A-share commission (min ¥5) + transfer fee
    assert result["fee"] > 0
    # Verify the execution record has the correct fee
    assert len(state.executions) == 1
    execution = state.executions[0]
    assert execution.fee > 0
    # Cash reduction includes fee
    gross = 100 * q.ask * (1.0 + 10.0 / 10000.0)
    expected_cash = 1_000_000.0 - gross - execution.fee
    assert state.account.cash == pytest.approx(expected_cash, rel=1e-6)


# ---------------------------------------------------------------------------
# State v1 compatibility
# ---------------------------------------------------------------------------


def test_state_v1_compatibility(monkeypatch, tmp_path: Path) -> None:
    """v1 state.json 可读取（无 buy_lots）."""
    state_dir = _setup_state_dir(monkeypatch, tmp_path, account_id="v1-test")
    state_dir.mkdir(parents=True, exist_ok=True)

    # Write a v1-style state without buy_lots in positions
    v1_state = {
        "version": 1,
        "account": {
            "account_id": "v1-test",
            "currency": "CNY",
            "cash": 500000.0,
            "realized_pnl": 0.0,
            "created_at": "2026-01-01T00:00:00.000Z",
            "updated_at": "2026-01-01T00:00:00.000Z",
        },
        "positions": {
            "000001.SZ": {
                "symbol": "000001.SZ",
                "quantity": 200,
                "avg_price": 12.0,
                "market_value": 0.0,
                "unrealized_pnl": 0.0,
                # No "buy_lots" key (v1 format)
            }
        },
        "orders": {},
        "executions": [],
        "prices": {},
    }
    state_file = state_dir / "state.json"
    state_file.write_text(json.dumps(v1_state), encoding="utf-8")

    state = load_or_initialize(account_id="v1-test", initial_cash=1_000_000.0, currency="CNY")

    assert state.version == 1
    assert state.account.account_id == "v1-test"
    assert state.account.cash == 500000.0
    assert "000001.SZ" in state.positions
    pos = state.positions["000001.SZ"]
    assert pos.quantity == 200
    assert pos.avg_price == 12.0
    # v1 had no buy_lots — should default to empty list
    assert pos.buy_lots == []


# ---------------------------------------------------------------------------
# Mark-to-market
# ---------------------------------------------------------------------------


def test_mark_to_market_a_share(monkeypatch, tmp_path: Path) -> None:
    """A 股持仓市值更新."""
    _setup_state_dir(monkeypatch, tmp_path)
    q = _quote_stub("000001.SZ", last=16.0)
    _mock_market_quote(monkeypatch, {"000001.SZ": q})

    state = _make_state(
        currency="CNY",
        positions={
            "000001.SZ": VirtualPosition(
                symbol="000001.SZ",
                quantity=200,
                avg_price=14.0,
                market_value=0.0,
                unrealized_pnl=0.0,
            )
        },
    )

    mark_to_market(
        state,
        universe=(),
        price_source="fallback",
        market="a_share",
        allow_dynamic_symbols=True,
    )

    pos = state.positions["000001.SZ"]
    assert pos.market_value == pytest.approx(200 * 16.0)
    assert pos.unrealized_pnl == pytest.approx((16.0 - 14.0) * 200)


# ---------------------------------------------------------------------------
# Limit order matching
# ---------------------------------------------------------------------------


def test_match_open_orders_a_share(monkeypatch, tmp_path: Path) -> None:
    """A 股限价单延迟撮合."""
    _setup_state_dir(monkeypatch, tmp_path)
    # First, place a limit buy order that won't fill immediately (bid too low).
    # Use a quote with ask=15.0 and place limit at 14.0 → won't fill yet.
    q_low = _quote_stub("000001.SZ", last=15.0, ask=15.0)
    _mock_market_quote(monkeypatch, {"000001.SZ": q_low})

    state = _make_state(currency="CNY")
    # Submit a limit buy order at 14.0 — with ask=15.0, this shouldn't fill.
    result = submit_order(
        state,
        symbol="000001.SZ",
        side="buy",
        quantity=100,
        notional=0,
        order_type="limit",
        limit_price=14.0,
        time_in_force="day",
        slippage_bps=10.0,
        fee_bps=5.0,
        allow_short=False,
        max_leverage=1.0,
        universe=(),
        price_source="fallback",
        market="a_share",
        allow_dynamic_symbols=True,
    )

    assert result["order_status"] == "open"
    order_id = result["order_id"]

    # Now update quote to make the limit fillable — ask drops to 14.0.
    q_now = _quote_stub("000001.SZ", last=14.0, ask=14.0)
    _mock_market_quote(monkeypatch, {"000001.SZ": q_now})

    fills = match_open_orders(
        state,
        slippage_bps=10.0,
        fee_bps=5.0,
        allow_short=False,
        max_leverage=1.0,
        universe=(),
        price_source="fallback",
        market="a_share",
        allow_dynamic_symbols=True,
    )

    assert len(fills) == 1
    assert fills[0]["status"] == "filled"
    assert fills[0]["order_id"] == order_id

    # Position should exist
    assert "000001.SZ" in state.positions
    assert state.positions["000001.SZ"].quantity == 100
