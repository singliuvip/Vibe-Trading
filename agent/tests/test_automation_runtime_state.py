"""Tests for AutomationRuntimeState and read_runtime_state."""

from __future__ import annotations

from typing import Any, Mapping

import pytest

from src.live.automation.runtime_state import (
    AutomationRuntimeState,
    read_runtime_state,
)


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


def _positions_response(positions: list[dict]) -> Mapping[str, Any]:
    return {"positions": positions}


def _account_response(equity: float = 10000.0) -> Mapping[str, Any]:
    return {"equity": equity}


def _orders_response(orders: list[dict]) -> Mapping[str, Any]:
    return {"orders": orders}


# --------------------------------------------------------------------------- #
# Normal parsing                                                              #
# --------------------------------------------------------------------------- #


def test_normal_positions_account_orders_parsed():
    """Positions, account equity, and open orders are correctly parsed."""
    state = read_runtime_state(
        read_positions=lambda: _positions_response([
            {"symbol": "AAPL", "quantity": 100, "price": 150.0, "side": "long"},
            {"symbol": "TSLA", "market_value": 5000.0, "quantity": -10, "side": "short"},
        ]),
        read_account=lambda: _account_response(equity=25000.0),
        read_open_orders=lambda: _orders_response([{"id": "o1"}, {"id": "o2"}]),
        daily_turnover_usd=1000.0,
        daily_realized_loss_usd=50.0,
        peak_equity_usd=30000.0,
    )

    assert state.available is True
    assert state.open_position_count == 2
    assert state.open_order_count == 2
    assert state.current_equity_usd == 25000.0
    assert state.daily_turnover_usd == 1000.0
    assert state.daily_realized_loss_usd == 50.0
    assert state.peak_equity_usd == 30000.0
    # AAPL: 100 * 150 = 15000
    assert state.symbol_exposure("AAPL") == 15000.0
    # TSLA: market_value = 5000
    assert state.symbol_exposure("TSLA") == 5000.0
    # Long detection
    assert state.has_long("AAPL") is True
    assert state.has_long("TSLA") is False


def test_empty_positions():
    """Empty positions → open_position_count=0, no long symbols."""
    state = read_runtime_state(
        read_positions=lambda: _positions_response([]),
        read_account=lambda: _account_response(),
        read_open_orders=lambda: _orders_response([]),
    )

    assert state.available is True
    assert state.open_position_count == 0
    assert state.open_order_count == 0
    assert state.has_long("AAPL") is False


def test_data_key_fallback():
    """Positions/orders under 'data' key are also parsed."""
    state = read_runtime_state(
        read_positions=lambda: {"data": [{"symbol": "MSFT", "quantity": 50, "price": 300.0}]},
        read_account=lambda: {"total_equity": 50000.0},
        read_open_orders=lambda: {"data": [{"id": "o1"}]},
    )

    assert state.available is True
    assert state.open_position_count == 1
    assert state.symbol_exposure("MSFT") == 15000.0
    assert state.open_order_count == 1
    assert state.current_equity_usd == 50000.0


def test_list_response_shape():
    """Raw list responses (not wrapped in dict) are handled."""
    state = read_runtime_state(
        read_positions=lambda: [{"symbol": "GOOG", "qty": 10, "last_price": 200.0, "side": "long"}],
        read_account=lambda: {"net_liquidation": 8000.0},
        read_open_orders=lambda: [],
    )

    assert state.available is True
    assert state.symbol_exposure("GOOG") == 2000.0
    assert state.has_long("GOOG") is True
    assert state.current_equity_usd == 8000.0


# --------------------------------------------------------------------------- #
# Drawdown calculation                                                        #
# --------------------------------------------------------------------------- #


def test_drawdown_pct_calculation():
    """peak=10000, current=9000 → drawdown=0.1."""
    state = AutomationRuntimeState(
        available=True,
        peak_equity_usd=10000.0,
        current_equity_usd=9000.0,
    )
    assert state.drawdown_pct == pytest.approx(0.1)


def test_drawdown_pct_zero_peak():
    """No peak equity → drawdown=0."""
    state = AutomationRuntimeState(available=True, peak_equity_usd=0.0, current_equity_usd=5000.0)
    assert state.drawdown_pct == 0.0


def test_drawdown_pct_current_above_peak():
    """Current above peak → drawdown=0 (clamped)."""
    state = AutomationRuntimeState(
        available=True, peak_equity_usd=10000.0, current_equity_usd=12000.0
    )
    assert state.drawdown_pct == 0.0


# --------------------------------------------------------------------------- #
# Fail-closed: read exceptions                                                #
# --------------------------------------------------------------------------- #


def test_read_exception_returns_unavailable():
    """Any read callable raising → available=False."""
    state = read_runtime_state(
        read_positions=lambda: (_ for _ in ()).throw(ConnectionError("broker down")),
        read_account=lambda: _account_response(),
        read_open_orders=lambda: _orders_response([]),
    )

    assert state.available is False
    assert "broker down" in state.error


def test_account_read_exception_returns_unavailable():
    """Account read raising → available=False."""
    state = read_runtime_state(
        read_positions=lambda: _positions_response([]),
        read_account=lambda: (_ for _ in ()).throw(TimeoutError("timeout")),
        read_open_orders=lambda: _orders_response([]),
    )

    assert state.available is False
    assert "timeout" in state.error


# --------------------------------------------------------------------------- #
# Fail-closed: parse exceptions                                               #
# --------------------------------------------------------------------------- #


def test_parse_exception_returns_unavailable():
    """Completely unrecognized structure → available=False."""
    # A string is not a valid Mapping — will raise on .get()
    state = read_runtime_state(
        read_positions=lambda: "not-a-dict",  # type: ignore[return-value]
        read_account=lambda: _account_response(),
        read_open_orders=lambda: _orders_response([]),
    )

    assert state.available is False
    assert state.error != ""


# --------------------------------------------------------------------------- #
# has_long / symbol_exposure helpers                                          #
# --------------------------------------------------------------------------- #


def test_has_long_and_symbol_exposure():
    state = AutomationRuntimeState(
        available=True,
        symbol_exposures={"AAPL": 15000.0, "TSLA": 5000.0},
        long_symbols=frozenset({"AAPL"}),
    )
    assert state.has_long("AAPL") is True
    assert state.has_long("TSLA") is False
    assert state.has_long("UNKNOWN") is False
    assert state.symbol_exposure("AAPL") == 15000.0
    assert state.symbol_exposure("UNKNOWN") == 0.0
