"""Unit tests for analyst_research_team schemas."""

from __future__ import annotations

import pytest

from src.swarm.schemas_analyst_research_team import (
    PortfolioRating,
    TraderAction,
    SentimentBand,
    TechnicalBias,
    Confidence,
    SentimentReport,
    ResearchPlan,
    TraderProposal,
    RiskView,
    PortfolioDecision,
    render_research_plan,
    render_trader_proposal,
    render_pm_decision,
)


# =============================================================================
# Enum tests
# =============================================================================


class TestPortfolioRating:
    def test_all_values(self):
        assert PortfolioRating.BUY.value == "Buy"
        assert PortfolioRating.OVERWEIGHT.value == "Overweight"
        assert PortfolioRating.HOLD.value == "Hold"
        assert PortfolioRating.UNDERWEIGHT.value == "Underweight"
        assert PortfolioRating.SELL.value == "Sell"
        assert len(PortfolioRating) == 5

    def test_from_string(self):
        assert PortfolioRating("Buy") == PortfolioRating.BUY
        assert PortfolioRating("Sell") == PortfolioRating.SELL

    def test_invalid_rating_raises(self):
        with pytest.raises(ValueError):
            PortfolioRating("Short")


class TestTraderAction:
    def test_all_values(self):
        assert TraderAction.BUY.value == "Buy"
        assert TraderAction.HOLD.value == "Hold"
        assert TraderAction.SELL.value == "Sell"
        assert len(TraderAction) == 3


class TestSentimentBand:
    def test_all_values(self):
        assert SentimentBand.BULLISH.value == "Bullish"
        assert SentimentBand.BEARISH.value == "Bearish"
        assert len(SentimentBand) == 6

    def test_neutral_and_mixed_distinct(self):
        assert SentimentBand.NEUTRAL != SentimentBand.MIXED


class TestTechnicalBias:
    def test_all_values(self):
        assert TechnicalBias.BULLISH.value == "Bullish"
        assert TechnicalBias.NEUTRAL.value == "Neutral"
        assert TechnicalBias.BEARISH.value == "Bearish"


class TestConfidence:
    def test_all_values(self):
        assert Confidence.HIGH.value == "High"
        assert Confidence.MEDIUM.value == "Medium"
        assert Confidence.LOW.value == "Low"


# =============================================================================
# Model creation tests
# =============================================================================


class TestSentimentReport:
    def test_full_fields(self):
        report = SentimentReport(
            overall_band=SentimentBand.BULLISH,
            overall_score=7.5,
            confidence="high",
            institutional_summary="Funds are net buying",
            retail_summary="Fear & Greed Index at 65",
            analyst_consensus="12 Buy / 3 Hold / 0 Sell",
            narrative="Strong bullish consensus across all sources.",
        )
        assert report.overall_band == SentimentBand.BULLISH
        assert report.overall_score == 7.5
        assert report.confidence == "high"

    def test_minimal_fields(self):
        report = SentimentReport(
            overall_band=SentimentBand.NEUTRAL,
            overall_score=5.0,
            confidence="low",
            narrative="Insufficient data.",
        )
        assert report.overall_band == SentimentBand.NEUTRAL
        assert report.institutional_summary == ""
        assert report.retail_summary == ""

    def test_overall_score_out_of_range_raises(self):
        with pytest.raises(ValueError):
            SentimentReport(
                overall_band=SentimentBand.BULLISH,
                overall_score=10.5,
                confidence="high",
                narrative="Test",
            )

    def test_overall_score_negative_raises(self):
        with pytest.raises(ValueError):
            SentimentReport(
                overall_band=SentimentBand.BEARISH,
                overall_score=-0.1,
                confidence="low",
                narrative="Test",
            )

    def test_invalid_confidence_raises(self):
        with pytest.raises(ValueError):
            SentimentReport(
                overall_band=SentimentBand.NEUTRAL,
                overall_score=5.0,
                confidence="very_high",
                narrative="Test",
            )


class TestResearchPlan:
    def test_full_fields(self):
        plan = ResearchPlan(
            recommendation=PortfolioRating.BUY,
            rationale="Strong fundamentals and positive momentum.",
            strategic_actions="Initiate 5% position at current levels.",
        )
        assert plan.recommendation == PortfolioRating.BUY

    def test_render(self):
        plan = ResearchPlan(
            recommendation=PortfolioRating.OVERWEIGHT,
            rationale="Valuation attractive relative to peers.",
            strategic_actions="Add 2% on dips.",
        )
        output = render_research_plan(plan)
        assert "**Recommendation**: Overweight" in output
        assert "**Rationale**:" in output
        assert "**Strategic Actions**:" in output
        assert "Valuation attractive" in output


class TestTraderProposal:
    def test_full_fields(self):
        proposal = TraderProposal(
            action=TraderAction.BUY,
            reasoning="Upside potential exceeds downside risk.",
            entry_price=150.0,
            stop_loss=135.0,
            position_sizing="5% of portfolio",
            execution_notes="Limit order at VWAP.",
        )
        assert proposal.action == TraderAction.BUY
        assert proposal.entry_price == 150.0
        assert proposal.stop_loss == 135.0
        assert proposal.position_sizing == "5% of portfolio"

    def test_minimal_fields(self):
        proposal = TraderProposal(
            action=TraderAction.HOLD,
            reasoning="No clear catalyst; maintain current position.",
        )
        assert proposal.entry_price is None
        assert proposal.stop_loss is None
        assert proposal.position_sizing is None
        assert proposal.execution_notes is None

    def test_nullish_entry_price_coerced_to_none(self):
        proposal = TraderProposal(
            action=TraderAction.SELL,
            reasoning="Profit-taking.",
            entry_price="N/A",
        )
        assert proposal.entry_price is None

    def test_nullish_stop_loss_coerced_to_none(self):
        proposal = TraderProposal(
            action=TraderAction.BUY,
            reasoning="Dip buy.",
            stop_loss="None",
        )
        assert proposal.stop_loss is None

    def test_empty_string_coerced_to_none(self):
        proposal = TraderProposal(
            action=TraderAction.BUY,
            reasoning="Test.",
            entry_price="",
            stop_loss="",
        )
        assert proposal.entry_price is None
        assert proposal.stop_loss is None

    def test_render(self):
        proposal = TraderProposal(
            action=TraderAction.BUY,
            reasoning="Strong catalyst ahead.",
            entry_price=100.0,
            stop_loss=90.0,
            position_sizing="10%",
        )
        output = render_trader_proposal(proposal)
        assert "**Action**: Buy" in output
        assert "**Entry Price**: 100.0" in output
        assert "**Stop Loss**: 90.0" in output
        assert "FINAL TRANSACTION PROPOSAL: **BUY**" in output


class TestRiskView:
    def test_full_fields(self):
        view = RiskView(
            stance="Larger position size, higher conviction.",
            key_arguments=["Upside is under-priced", "Catalyst in 2 weeks"],
            sizing_recommendation="8% of portfolio",
            stop_recommendation="15% trailing stop",
            hedge_recommendation="Buy protective puts at 10% OTM",
        )
        assert len(view.key_arguments) == 2
        assert view.stance is not None

    def test_minimal_fields(self):
        view = RiskView(
            stance="Balanced approach.",
            key_arguments=["Risk/reward is symmetric"],
            sizing_recommendation="3% of portfolio",
        )
        assert view.stop_recommendation is None
        assert view.hedge_recommendation is None


class TestPortfolioDecision:
    def test_full_fields(self):
        decision = PortfolioDecision(
            rating=PortfolioRating.BUY,
            executive_summary="Initiate 5% position at market open.",
            investment_thesis="Strong earnings growth and expanding margins.",
            price_target=200.0,
            time_horizon="6-12 months",
            key_risks=["Regulatory risk", "Competition intensifying"],
        )
        assert decision.rating == PortfolioRating.BUY
        assert decision.price_target == 200.0
        assert len(decision.key_risks) == 2

    def test_minimal_fields(self):
        decision = PortfolioDecision(
            rating=PortfolioRating.HOLD,
            executive_summary="Maintain current position, no action needed.",
            investment_thesis="Fairly valued at current levels.",
        )
        assert decision.price_target is None
        assert decision.time_horizon is None
        assert decision.key_risks == []

    def test_nullish_price_target_coerced_to_none(self):
        decision = PortfolioDecision(
            rating=PortfolioRating.SELL,
            executive_summary="Exit position.",
            investment_thesis="Deteriorating fundamentals.",
            price_target="N/A",
        )
        assert decision.price_target is None

    def test_invalid_overall_score_raises(self):
        """overall_score > 10 should fail on SentimentReport."""
        with pytest.raises(ValueError):
            SentimentReport(
                overall_band=SentimentBand.BULLISH,
                overall_score=11.0,
                confidence="high",
                narrative="Test",
            )

    def test_render(self):
        decision = PortfolioDecision(
            rating=PortfolioRating.OVERWEIGHT,
            executive_summary="Add 3% to existing position.",
            investment_thesis="Valuation dislocation creates opportunity.",
            price_target=180.0,
            time_horizon="3-6 months",
            key_risks=["Macro headwinds", "Sector rotation"],
        )
        output = render_pm_decision(decision)
        assert "**Rating**: Overweight" in output
        assert "**Executive Summary**:" in output
        assert "**Investment Thesis**:" in output
        assert "**Price Target**: 180.0" in output
        assert "**Time Horizon**: 3-6 months" in output
        assert "**Key Risks**:" in output
        assert "Macro headwinds" in output
        assert "FINAL DECISION: **OVERWEIGHT**" in output
