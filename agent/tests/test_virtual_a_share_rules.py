"""Tests for A-share virtual broker trading rules (rules.py)."""

from __future__ import annotations

import pytest

from src.trading.connectors.virtual.rules import (
    BuyLot,
    calculate_fees,
    can_sell_today,
    check_price_limit,
    consume_buy_lots,
    get_price_limit,
    infer_board,
    round_quantity,
    validate_quantity,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Board detection
# ---------------------------------------------------------------------------


def test_infer_board_main_board() -> None:
    """主板识别：000001.SZ → main_board, 600519.SH → main_board."""
    assert infer_board("000001.SZ") == "main_board"
    assert infer_board("600519.SH") == "main_board"


def test_infer_board_gem() -> None:
    """创业板：300750.SZ → gem."""
    assert infer_board("300750.SZ") == "gem"


def test_infer_board_star() -> None:
    """科创板：688981.SH → star."""
    assert infer_board("688981.SH") == "star"


def test_infer_board_bj() -> None:
    """北交所：833171.BJ → bj."""
    assert infer_board("833171.BJ") == "bj"


def test_infer_board_cn_suffix() -> None:
    """.CN 后缀识别（normalize 后传入，归入 main_board）."""
    assert infer_board("000001.SZ.CN") == "main_board"


# ---------------------------------------------------------------------------
# Price limits
# ---------------------------------------------------------------------------


def test_get_price_limit() -> None:
    """涨跌停比例：主板 0.10, 创业板 0.20, 科创板 0.20, 北交所 0.30."""
    assert get_price_limit("000001.SZ") == 0.10
    assert get_price_limit("600519.SH") == 0.10
    assert get_price_limit("300750.SZ") == 0.20
    assert get_price_limit("688981.SH") == 0.20
    assert get_price_limit("833171.BJ") == 0.30


# ---------------------------------------------------------------------------
# Quantity validation
# ---------------------------------------------------------------------------


def test_validate_quantity_valid() -> None:
    """100 股整数倍通过."""
    assert validate_quantity(100) is None
    assert validate_quantity(500) is None
    assert validate_quantity(10000) is None


def test_validate_quantity_invalid() -> None:
    """非 100 整数倍 / 不足 100 股拒绝."""
    assert validate_quantity(150) is not None
    assert validate_quantity(50) is not None
    assert validate_quantity(0) is not None
    assert validate_quantity(-100) is not None


def test_round_quantity() -> None:
    """向下取整到 100 倍数."""
    assert round_quantity(150) == 100.0
    assert round_quantity(199) == 100.0
    assert round_quantity(200) == 200.0
    assert round_quantity(50) == 0.0
    assert round_quantity(0) == 0.0
    assert round_quantity(1234) == 1200.0


# ---------------------------------------------------------------------------
# Fee calculation
# ---------------------------------------------------------------------------


def test_calculate_fees_buy() -> None:
    """买入只收佣金+过户费，无印花税."""
    fees = calculate_fees("buy", 100, 10.0)
    assert fees["commission"] > 0
    assert fees["stamp_tax"] == 0.0
    assert fees["transfer_fee"] > 0
    assert fees["total"] == pytest.approx(fees["commission"] + fees["stamp_tax"] + fees["transfer_fee"])


def test_calculate_fees_sell() -> None:
    """卖出收佣金+印花税+过户费."""
    fees = calculate_fees("sell", 100, 10.0)
    assert fees["commission"] > 0
    assert fees["stamp_tax"] > 0
    assert fees["transfer_fee"] > 0
    assert fees["total"] == pytest.approx(fees["commission"] + fees["stamp_tax"] + fees["transfer_fee"])


def test_calculate_fees_min_commission() -> None:
    """小额交易佣金不低于 ¥5."""
    fees = calculate_fees("buy", 100, 10.0)
    gross = 100 * 10.0
    min_from_rate = gross * 0.00025
    assert min_from_rate < 5.0
    assert fees["commission"] == 5.0


# ---------------------------------------------------------------------------
# T+1 rules
# ---------------------------------------------------------------------------


def test_can_sell_today_same_day() -> None:
    """同日买入不可卖出（T+1）."""
    lots = [BuyLot(trade_date="2026-07-12", quantity=100, price=10.0)]
    ok, err = can_sell_today(lots, 100, trade_date="2026-07-12")
    assert not ok
    assert err is not None
    assert "T+1" in err


def test_can_sell_today_next_day() -> None:
    """次日可卖出."""
    lots = [BuyLot(trade_date="2026-07-11", quantity=100, price=10.0)]
    ok, err = can_sell_today(lots, 100, trade_date="2026-07-12")
    assert ok
    assert err is None


def test_can_sell_today_insufficient() -> None:
    """可卖数量不足（只有 50 股已过 T+1，想卖 100 股）."""
    lots = [
        BuyLot(trade_date="2026-07-11", quantity=50, price=10.0),
        BuyLot(trade_date="2026-07-12", quantity=50, price=10.5),
    ]
    ok, err = can_sell_today(lots, 100, trade_date="2026-07-12")
    assert not ok
    assert err is not None


def test_can_sell_today_no_lots() -> None:
    """无买入记录拒绝卖出."""
    ok, err = can_sell_today([], 100)
    assert not ok


# ---------------------------------------------------------------------------
# Buy lot consumption (FIFO)
# ---------------------------------------------------------------------------


def test_consume_buy_lots_fifo() -> None:
    """FIFO 消费顺序."""
    lots = [
        BuyLot(trade_date="2026-07-11", quantity=100, price=10.0),
        BuyLot(trade_date="2026-07-10", quantity=200, price=9.0),
    ]
    remaining = consume_buy_lots(lots, 150)
    assert len(remaining) == 1
    # First lot (100) fully consumed, second lot (200) partially consumed
    assert remaining[0].quantity == 150
    assert remaining[0].price == 9.0


def test_consume_buy_lots_partial_first() -> None:
    """只消费第一个 lot 的一部分."""
    lots = [BuyLot(trade_date="2026-07-11", quantity=200, price=10.0)]
    remaining = consume_buy_lots(lots, 50)
    assert len(remaining) == 1
    assert remaining[0].quantity == 150


def test_consume_buy_lots_all() -> None:
    """全部消费."""
    lots = [BuyLot(trade_date="2026-07-11", quantity=100, price=10.0)]
    remaining = consume_buy_lots(lots, 100)
    assert remaining == []


# ---------------------------------------------------------------------------
# Price limit checks
# ---------------------------------------------------------------------------


def test_check_price_limit_buy_above() -> None:
    """买入价超过涨停价→拒绝."""
    err = check_price_limit("buy", 12.0, 10.0, "000001.SZ")
    assert err is not None
    assert "exceeds upper limit" in err


def test_check_price_limit_sell_below() -> None:
    """卖出价低于跌停价→拒绝."""
    err = check_price_limit("sell", 8.0, 10.0, "000001.SZ")
    assert err is not None
    assert "below lower limit" in err


def test_check_price_limit_no_prev_close() -> None:
    """无昨收→跳过检查."""
    assert check_price_limit("buy", 1000.0, None) is None
    assert check_price_limit("sell", 0.01, 0.0) is None


def test_check_price_limit_within_range() -> None:
    """价格在涨跌停范围内→通过."""
    assert check_price_limit("buy", 10.5, 10.0, "600519.SH") is None
    assert check_price_limit("sell", 9.5, 10.0, "600519.SH") is None
