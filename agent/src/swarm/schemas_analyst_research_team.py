"""
Analyst Research Team 结构化输出 Schemas — Pydantic 模型

这些 Schema 定义了 Analyst Research Team 工作流中关键 Agent 的结构化输出格式。
当 Vibe-Trading 未来支持结构化输出模式时，可直接使用这些模型。

使用方法参考:
    from src.swarm.schemas_analyst_research_team import (
        SentimentReport, ResearchPlan, TraderProposal, PortfolioDecision
    )
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, field_validator

# LLMs sometimes write a placeholder string ("None", "N/A") into an optional
# numeric field. Coerce those to None so validation passes.
_NULLISH_FLOAT = {"", "none", "n/a", "na", "null", "nil", "-", "tbd", "unknown"}


def _coerce_optional_float(value):
    if isinstance(value, str) and value.strip().lower() in _NULLISH_FLOAT:
        return None
    return value


# =============================================================================
# Shared Rating Types
# =============================================================================


class PortfolioRating(str, Enum):
    """5-tier rating used by Research Manager and Portfolio Manager."""

    BUY = "Buy"
    OVERWEIGHT = "Overweight"
    HOLD = "Hold"
    UNDERWEIGHT = "Underweight"
    SELL = "Sell"


class TraderAction(str, Enum):
    """3-tier action used by the Trader."""

    BUY = "Buy"
    HOLD = "Hold"
    SELL = "Sell"


class SentimentBand(str, Enum):
    """6-tier sentiment direction."""

    BULLISH = "Bullish"
    MILDLY_BULLISH = "Mildly Bullish"
    NEUTRAL = "Neutral"
    MIXED = "Mixed"
    MILDLY_BEARISH = "Mildly Bearish"
    BEARISH = "Bearish"


class TechnicalBias(str, Enum):
    """Technical analysis bias."""

    BULLISH = "Bullish"
    NEUTRAL = "Neutral"
    BEARISH = "Bearish"


class Confidence(str, Enum):
    """Confidence level."""

    HIGH = "High"
    MEDIUM = "Medium"
    LOW = "Low"


# =============================================================================
# Stage 1: Sentiment Analyst
# =============================================================================


class SentimentReport(BaseModel):
    """Structured sentiment report."""

    overall_band: SentimentBand = Field(
        description="Overall sentiment direction. Bullish / Mildly Bullish / Neutral / Mixed / Mildly Bearish / Bearish."
    )
    overall_score: float = Field(
        ge=0.0,
        le=10.0,
        description="Numeric sentiment intensity on 0–10 scale. 0 = most bearish, 10 = most bullish.",
    )
    confidence: Literal["low", "medium", "high"] = Field(
        description="Confidence based on data quality and sample size."
    )
    institutional_summary: str = Field(
        default="",
        description="Fund flow direction, 13F trends, notable institutional moves.",
    )
    retail_summary: str = Field(
        default="",
        description="Fear/greed indicators, social media buzz, retail flow.",
    )
    analyst_consensus: str = Field(
        default="",
        description="Buy/Hold/Sell ratio, target price revision direction.",
    )
    narrative: str = Field(
        description="Full narrative: source-by-source breakdown, cross-source divergences, dominant themes."
    )


# =============================================================================
# Stage 2: Research Manager
# =============================================================================


class ResearchPlan(BaseModel):
    """Structured investment plan from Research Manager."""

    recommendation: PortfolioRating = Field(
        description="Investment recommendation. Exactly one of Buy / Overweight / Hold / Underweight / Sell."
    )
    rationale: str = Field(
        description="Detailed reasoning for the recommendation, anchored in the bull/bear debate evidence."
    )
    strategic_actions: str = Field(
        description="Concrete steps for the trader: direction, sizing guidance, timing considerations."
    )


def render_research_plan(plan: ResearchPlan) -> str:
    """Render a ResearchPlan to markdown."""
    return "\n".join(
        [
            f"**Recommendation**: {plan.recommendation.value}",
            "",
            f"**Rationale**: {plan.rationale}",
            "",
            f"**Strategic Actions**: {plan.strategic_actions}",
        ]
    )


# =============================================================================
# Stage 3: Trader
# =============================================================================


class TraderProposal(BaseModel):
    """Structured trade proposal from Trader."""

    action: TraderAction = Field(
        description="Transaction direction. Exactly one of Buy / Hold / Sell."
    )
    reasoning: str = Field(
        description="The case for this action anchored in the research plan. 2-4 sentences."
    )
    entry_price: float | None = Field(
        default=None,
        description="Optional entry price target.",
    )
    stop_loss: float | None = Field(
        default=None,
        description="Optional stop-loss price.",
    )
    position_sizing: str | None = Field(
        default=None,
        description="Optional sizing guidance, e.g. '5% of portfolio'.",
    )
    execution_notes: str | None = Field(
        default=None,
        description="Optional execution instructions: limit vs market, time of day, VWAP, etc.",
    )

    @field_validator("entry_price", "stop_loss", mode="before")
    @classmethod
    def _nullish_float_to_none(cls, v):
        return _coerce_optional_float(v)


def render_trader_proposal(proposal: TraderProposal) -> str:
    """Render a TraderProposal to markdown."""
    parts = [
        f"**Action**: {proposal.action.value}",
        f"**Reasoning**: {proposal.reasoning}",
    ]
    if proposal.entry_price is not None:
        parts.append(f"**Entry Price**: {proposal.entry_price}")
    if proposal.stop_loss is not None:
        parts.append(f"**Stop Loss**: {proposal.stop_loss}")
    if proposal.position_sizing:
        parts.append(f"**Position Sizing**: {proposal.position_sizing}")
    if proposal.execution_notes:
        parts.append(f"**Execution Notes**: {proposal.execution_notes}")
    parts.append(f"FINAL TRANSACTION PROPOSAL: **{proposal.action.value.upper()}**")
    return "\n\n".join(parts)


# =============================================================================
# Stage 4: Risk Analysts
# =============================================================================


class RiskView(BaseModel):
    """Risk analyst perspective (aggressive/conservative/neutral)."""

    stance: str = Field(
        description="Clear position statement: larger/smaller/balanced sizing recommendation."
    )
    key_arguments: list[str] = Field(
        description="List of key arguments supporting this risk position."
    )
    sizing_recommendation: str = Field(
        description="Specific sizing suggestion as % of portfolio."
    )
    stop_recommendation: str | None = Field(
        default=None,
        description="Specific stop-loss recommendation.",
    )
    hedge_recommendation: str | None = Field(
        default=None,
        description="Specific hedge recommendation, if any.",
    )


# =============================================================================
# Stage 5: Portfolio Manager (Final Decision)
# =============================================================================


class PortfolioDecision(BaseModel):
    """Structured final investment decision from Portfolio Manager."""

    rating: PortfolioRating = Field(
        description="Final position rating. Exactly one of Buy / Overweight / Hold / Underweight / Sell."
    )
    executive_summary: str = Field(
        description="Concise action plan: entry strategy, position sizing, key risk levels, time horizon. 2-4 sentences."
    )
    investment_thesis: str = Field(
        description="Detailed reasoning anchored in specific evidence from the full debate."
    )
    price_target: float | None = Field(
        default=None,
        description="Optional target price.",
    )
    time_horizon: str | None = Field(
        default=None,
        description="Optional recommended holding period, e.g. '3-6 months'.",
    )
    key_risks: list[str] = Field(
        default_factory=list,
        description="Top 2-3 risks that could invalidate this thesis.",
    )

    @field_validator("price_target", mode="before")
    @classmethod
    def _nullish_float_to_none(cls, v):
        return _coerce_optional_float(v)


def render_pm_decision(decision: PortfolioDecision) -> str:
    """Render a PortfolioDecision to markdown."""
    parts = [
        f"**Rating**: {decision.rating.value}",
        f"**Executive Summary**: {decision.executive_summary}",
        f"**Investment Thesis**: {decision.investment_thesis}",
    ]
    if decision.price_target is not None:
        parts.append(f"**Price Target**: {decision.price_target}")
    if decision.time_horizon:
        parts.append(f"**Time Horizon**: {decision.time_horizon}")
    if decision.key_risks:
        risks = "\n".join(f"- {r}" for r in decision.key_risks)
        parts.append(f"**Key Risks**:\n{risks}")
    parts.append(f"FINAL DECISION: **{decision.rating.value.upper()}**")
    return "\n\n".join(parts)
