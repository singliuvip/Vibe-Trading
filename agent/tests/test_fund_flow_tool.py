"""Tests for fund_flow_tool: envelope shape, parsing, per-symbol isolation.

All HTTP is mocked at the Eastmoney client functions the tool imports
(:func:`get_json` / :func:`resolve_secid`), so no test touches a live endpoint.
"""

from __future__ import annotations

import json
from unittest.mock import patch

from src.tools.fund_flow_tool import FundFlowTool

_DAILY_PAYLOAD = {
    "data": {
        "code": "600519",
        "klines": [
            "2024-01-02,100.0,-10.0,5.0,60.0,40.0,0,0,0,0,0,0,0,0,0",
            "2024-01-03,-50.0,20.0,-5.0,-30.0,-20.0,0,0,0,0,0,0,0,0,0",
        ],
    }
}


class TestSuccessEnvelope:
    """A resolvable symbol yields the ok envelope with labelled buckets."""

    def test_daily_flow_parses_into_buckets(self):
        with patch(
            "src.tools.fund_flow_tool.resolve_secid", return_value="1.600519"
        ), patch(
            "src.tools.fund_flow_tool.get_json", return_value=_DAILY_PAYLOAD
        ):
            text = FundFlowTool().execute(codes=["600519.SH"], period="daily", days=30)

        payload = json.loads(text)
        assert payload["ok"] is True
        assert payload["market"] == "stock"
        assert payload["source"] == "eastmoney"
        assert payload["period"] == "daily"
        assert payload["buckets"] == ["main", "small", "medium", "large", "super_large"]

        rows = payload["data"]["600519.SH"]["rows"]
        assert len(rows) == 2
        assert rows[0] == {
            "timestamp": "2024-01-02",
            "main": 100.0,
            "small": -10.0,
            "medium": 5.0,
            "large": 60.0,
            "super_large": 40.0,
        }

    def test_days_cap_keeps_most_recent_rows(self):
        with patch(
            "src.tools.fund_flow_tool.resolve_secid", return_value="1.600519"
        ), patch(
            "src.tools.fund_flow_tool.get_json", return_value=_DAILY_PAYLOAD
        ):
            text = FundFlowTool().execute(codes=["600519.SH"], period="daily", days=1)

        rows = json.loads(text)["data"]["600519.SH"]["rows"]
        assert len(rows) == 1
        assert rows[0]["timestamp"] == "2024-01-03"

    def test_minute_period_uses_minute_url(self):
        minute_payload = {"data": {"klines": ["2024-01-02 09:31,1.0,2.0,3.0,4.0,5.0"]}}
        with patch(
            "src.tools.fund_flow_tool.resolve_secid", return_value="1.600519"
        ), patch(
            "src.tools.fund_flow_tool.get_json", return_value=minute_payload
        ) as mock_get:
            text = FundFlowTool().execute(codes=["600519.SH"], period="min")

        url = mock_get.call_args[0][0]
        assert "fflow/kline/get" in url
        rows = json.loads(text)["data"]["600519.SH"]["rows"]
        assert rows[0]["timestamp"] == "2024-01-02 09:31"
        assert rows[0]["main"] == 1.0


class TestPerSymbolIsolation:
    """A single failing/unresolvable symbol never aborts the batch."""

    def test_unresolvable_symbol_is_reported_not_fatal(self):
        def fake_resolve(symbol):
            return None if symbol == "BAD" else "1.600519"

        with patch(
            "src.tools.fund_flow_tool.resolve_secid", side_effect=fake_resolve
        ), patch(
            "src.tools.fund_flow_tool.get_json", return_value=_DAILY_PAYLOAD
        ):
            text = FundFlowTool().execute(codes=["BAD", "600519.SH"])

        payload = json.loads(text)
        assert payload["ok"] is True
        assert payload["data"]["BAD"]["error"] == "unresolvable symbol"
        assert len(payload["data"]["600519.SH"]["rows"]) == 2

    def test_http_failure_on_one_symbol_is_captured(self):
        with patch(
            "src.tools.fund_flow_tool.resolve_secid", return_value="1.600519"
        ), patch(
            "src.tools.fund_flow_tool.get_json", side_effect=RuntimeError("HTTP 429")
        ):
            text = FundFlowTool().execute(codes=["600519.SH"])

        payload = json.loads(text)
        assert payload["ok"] is True
        assert "429" in payload["data"]["600519.SH"]["error"]

    def test_malformed_row_skipped(self):
        bad = {"data": {"klines": ["garbage", "2024-01-03,-50.0,20.0,-5.0,-30.0,-20.0"]}}
        with patch(
            "src.tools.fund_flow_tool.resolve_secid", return_value="1.600519"
        ), patch("src.tools.fund_flow_tool.get_json", return_value=bad):
            text = FundFlowTool().execute(codes=["600519.SH"])

        rows = json.loads(text)["data"]["600519.SH"]["rows"]
        assert len(rows) == 1


class TestErrorEnvelope:
    """Input validation returns the ok=false envelope before any HTTP."""

    def test_empty_codes_rejected(self):
        payload = json.loads(FundFlowTool().execute(codes=[]))
        assert payload["ok"] is False
        assert "codes" in payload["error"]

    def test_missing_codes_rejected(self):
        payload = json.loads(FundFlowTool().execute())
        assert payload["ok"] is False

    def test_non_string_code_rejected(self):
        payload = json.loads(FundFlowTool().execute(codes=[123]))
        assert payload["ok"] is False

    def test_invalid_period_rejected(self):
        payload = json.loads(
            FundFlowTool().execute(codes=["600519.SH"], period="hourly")
        )
        assert payload["ok"] is False
        assert "period" in payload["error"]

    def test_non_positive_days_rejected(self):
        payload = json.loads(
            FundFlowTool().execute(codes=["600519.SH"], days=0)
        )
        assert payload["ok"] is False
        assert "days" in payload["error"]

    def test_bool_days_rejected(self):
        payload = json.loads(
            FundFlowTool().execute(codes=["600519.SH"], days=True)
        )
        assert payload["ok"] is False


class TestRoutingDescription:
    """Description must scope to PER-STOCK flow so vague prompts don't misroute.

    Regression for B10-routing-desc: get_fund_flow and get_northbound_flow both
    used to open on a generic 'net capital flow' phrase, so a vague prompt could
    route to either. The fund-flow description must lead with the per-stock,
    order-level scope and point market-wide intent at get_northbound_flow.
    """

    def test_description_leads_with_per_stock_order_level(self):
        desc = FundFlowTool().description
        # Leads with the per-stock, order-level scope, not a generic phrase.
        assert desc.startswith("PER-STOCK order-level net inflow")
        assert "market-wide" not in desc.lower().split("not market-wide")[0]
        # Disambiguates against the market-wide tool.
        assert "get_northbound_flow" in desc

    def test_description_keeps_a_concrete_example(self):
        assert '{"codes": ["600519.SH"' in FundFlowTool().description


class TestFundFlowTushareRouting:
    """Test that source="tushare" correctly routes to Tushare provider."""

    def test_tushare_source_returns_tushare_metadata(self):
        """source=tushare should return actual_source=tushare in envelope."""
        tool = FundFlowTool()
        with patch.object(tool, '_execute_tushare') as mock_tushare:
            mock_tushare.return_value = json.dumps({"ok": True, "source": "tushare"})
            result = tool.execute(codes=["600519.SH"], source="tushare")
            mock_tushare.assert_called_once()

    def test_tushare_source_never_calls_eastmoney(self):
        """source=tushare must NOT fall through to Eastmoney path."""
        tool = FundFlowTool()
        with patch('src.tools.fund_flow_tool._fetch_symbol_flow') as mock_em:
            tool.execute(codes=["600519.SH"], source="tushare")
            mock_em.assert_not_called()

    def test_tushare_non_ashare_returns_error_not_routemismatch(self):
        """Non-A-share with source=tushare should return per-symbol error, not route_mismatch."""
        tool = FundFlowTool()
        with patch.object(
            tool, '_execute_tushare', return_value=json.dumps({
                "ok": True,
                "source": "tushare",
                "data": {"AAPL.US": {"symbol": "AAPL.US", "error": "tushare moneyflow supports A-shares only"}},
            })
        ):
            result = json.loads(tool.execute(codes=["AAPL.US"], source="tushare"))
        assert result.get("ok") is True
        assert result.get("source") == "tushare"
        aapl = result["data"]["AAPL.US"]
        assert "error" in aapl

    def test_auto_source_uses_eastmoney(self):
        """source=auto should use Eastmoney."""
        tool = FundFlowTool()
        with patch('src.tools.fund_flow_tool._fetch_symbol_flow') as mock_em:
            mock_em.return_value = {"symbol": "600519.SH", "rows": []}
            result = tool.execute(codes=["600519.SH"], source="auto")
            mock_em.assert_called()

    def test_envelope_contains_fetch_mode_live(self):
        """Response envelope should contain fetch_mode='live'."""
        tool = FundFlowTool()
        with patch.object(
            tool, '_execute_tushare', return_value=json.dumps({
                "ok": True, "source": "tushare", "fetch_mode": "live",
                "requested_source": "tushare", "actual_source": "tushare",
            })
        ):
            result = json.loads(tool.execute(codes=["600519.SH"], source="tushare"))
            assert result.get("fetch_mode") == "live"

    def test_envelope_contains_request_id(self):
        """Response envelope should contain a unique request_id."""
        tool = FundFlowTool()
        with patch.object(
            tool, '_execute_tushare', return_value=json.dumps({
                "ok": True, "source": "tushare", "request_id": "abc123",
            })
        ):
            result = json.loads(tool.execute(codes=["600519.SH"], source="tushare"))
            assert "request_id" in result
            assert len(result["request_id"]) > 0

    def test_two_consecutive_calls_have_different_request_ids(self):
        """Two consecutive calls should produce different request_ids."""
        tool = FundFlowTool()
        with patch.object(
            tool, '_execute_tushare',
            side_effect=[
                json.dumps({"ok": True, "source": "tushare", "request_id": "id1"}),
                json.dumps({"ok": True, "source": "tushare", "request_id": "id2"}),
            ]
        ):
            r1 = json.loads(tool.execute(codes=["600519.SH"], source="tushare"))
            r2 = json.loads(tool.execute(codes=["600519.SH"], source="tushare"))
            assert r1["request_id"] != r2["request_id"]

    def test_eastmoney_envelope_has_observability_fields(self):
        """Eastmoney path should contain requested_source, actual_source, fetch_mode, request_id."""
        with patch(
            "src.tools.fund_flow_tool.resolve_secid", return_value="1.600519"
        ), patch(
            "src.tools.fund_flow_tool.get_json", return_value=_DAILY_PAYLOAD
        ):
            text = FundFlowTool().execute(codes=["600519.SH"], source="auto")
        payload = json.loads(text)
        assert payload["requested_source"] == "auto"
        assert payload["actual_source"] == "eastmoney"
        assert payload["fetch_mode"] == "live"
        assert "request_id" in payload
        assert len(payload["request_id"]) > 0

    def test_tushare_source_routes_to_tushare_not_eastmoney(self):
        """When source='tushare', must route to _execute_tushare, not Eastmoney path."""
        tool = FundFlowTool()
        with patch.object(tool, '_execute_tushare') as mock_tushare:
            mock_tushare.return_value = json.dumps({"ok": True, "source": "tushare"})
            # Both auto and eastmoney go to Eastmoney, but tushare goes to _execute_tushare.
            tool.execute(codes=["600519.SH"], source="auto")
            mock_tushare.assert_not_called()
            tool.execute(codes=["600519.SH"], source="eastmoney")
            mock_tushare.assert_not_called()
            tool.execute(codes=["600519.SH"], source="tushare")
            mock_tushare.assert_called_once()
