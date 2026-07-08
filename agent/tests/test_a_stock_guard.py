"""Unit tests for A-share order guard."""

from __future__ import annotations

import pytest

from src.live.a_stock_guard import (
    AStockOrderValidation,
    build_validation_from_quote,
    compute_price_limits,
    get_price_limit_ratio,
    validate_a_stock_order,
)


class TestGetPriceLimitRatio:
    def test_main_board_sh(self):
        assert get_price_limit_ratio("600036.SH") == 0.10

    def test_main_board_sz(self):
        assert get_price_limit_ratio("000001.SZ") == 0.10

    def test_star_board(self):
        assert get_price_limit_ratio("688001.SH") == 0.20

    def test_chinext(self):
        assert get_price_limit_ratio("300750.SZ") == 0.20
        assert get_price_limit_ratio("301000.SZ") == 0.20

    def test_bj(self):
        assert get_price_limit_ratio("830001.BJ") == 0.30


class TestComputePriceLimits:
    def test_main_board(self):
        lower, upper = compute_price_limits(10.00, 0.10)
        assert lower == 9.00
        assert upper == 11.00

    def test_zero_prev_close(self):
        lower, upper = compute_price_limits(0, 0.10)
        assert lower == 0.0
        assert upper == 0.0

    def test_negative_prev_close(self):
        lower, upper = compute_price_limits(-1.0, 0.10)
        assert lower == 0.0
        assert upper == 0.0


class TestValidateAStockOrder:
    def test_valid_order(self):
        order = AStockOrderValidation(
            symbol="000001.SZ", side="buy", quantity=100,
            limit_price=10.50, pre_close=10.00,
            upper_limit=11.00, lower_limit=9.00,
        )
        assert validate_a_stock_order(order) == []

    def test_invalid_symbol_format(self):
        order = AStockOrderValidation(symbol="AAPL", side="buy", quantity=100)
        violations = validate_a_stock_order(order)
        assert len(violations) == 1
        assert "symbol format" in violations[0].lower()

    def test_invalid_symbol_no_suffix(self):
        order = AStockOrderValidation(symbol="600036", side="buy", quantity=100)
        violations = validate_a_stock_order(order)
        assert len(violations) == 1
        assert "symbol format" in violations[0].lower()

    def test_quantity_not_multiple_of_100(self):
        order = AStockOrderValidation(
            symbol="000001.SZ", side="buy", quantity=150,
        )
        violations = validate_a_stock_order(order)
        assert any("100" in v for v in violations)

    def test_quantity_exact_lot(self):
        order = AStockOrderValidation(
            symbol="000001.SZ", side="buy", quantity=300,
        )
        violations = validate_a_stock_order(order)
        assert not any("100" in v for v in violations)

    def test_price_exceeds_upper_limit(self):
        order = AStockOrderValidation(
            symbol="000001.SZ", side="buy", quantity=100,
            limit_price=12.00, upper_limit=11.00, lower_limit=9.00,
        )
        violations = validate_a_stock_order(order)
        assert any("upper limit" in v for v in violations)

    def test_price_below_lower_limit(self):
        order = AStockOrderValidation(
            symbol="000001.SZ", side="buy", quantity=100,
            limit_price=8.00, upper_limit=11.00, lower_limit=9.00,
        )
        violations = validate_a_stock_order(order)
        assert any("lower limit" in v for v in violations)

    def test_price_precision(self):
        order = AStockOrderValidation(
            symbol="000001.SZ", side="buy", quantity=100,
            limit_price=10.123, upper_limit=11.00, lower_limit=9.00,
        )
        violations = validate_a_stock_order(order)
        assert any("precision" in v for v in violations)

    def test_price_precision_valid(self):
        order = AStockOrderValidation(
            symbol="000001.SZ", side="buy", quantity=100,
            limit_price=10.12, upper_limit=11.00, lower_limit=9.00,
        )
        violations = validate_a_stock_order(order)
        assert not any("precision" in v for v in violations)

    def test_market_order_no_price_check(self):
        """Market orders (limit_price=0) skip price checks."""
        order = AStockOrderValidation(
            symbol="000001.SZ", side="buy", quantity=100,
            limit_price=0.0, upper_limit=11.00, lower_limit=9.00,
        )
        violations = validate_a_stock_order(order)
        assert all("limit" not in v for v in violations)


class TestBuildValidationFromQuote:
    def test_from_quote_with_limits(self):
        quote = {
            "status": "ok",
            "pre_close": 10.00,
            "upper_limit": 11.00,
            "lower_limit": 9.00,
        }
        v = build_validation_from_quote("000001.SZ", "buy", 100, 10.50, quote)
        assert v.pre_close == 10.00
        assert v.upper_limit == 11.00
        assert v.lower_limit == 9.00

    def test_from_quote_without_limits_fallback(self):
        """If quote lacks limits, compute from ratio."""
        quote = {
            "status": "ok",
            "pre_close": 10.00,
        }
        v = build_validation_from_quote("000001.SZ", "buy", 100, 10.50, quote)
        assert v.upper_limit == 11.00
        assert v.lower_limit == 9.00
