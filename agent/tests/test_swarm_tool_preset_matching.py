"""Regression coverage for SwarmTool natural-language preset routing."""

from __future__ import annotations

import json

import src.tools.swarm_tool as swarm_tool


def test_explicit_preset_name_wins_over_keyword_scoring() -> None:
    prompt = (
        "[Swarm Team Mode] Use the investment_committee preset to evaluate "
        "whether to go long or short on NVDA given current market conditions"
    )

    assert swarm_tool._match_preset(prompt) == "investment_committee"


def test_plain_given_does_not_trigger_iv_derivatives_match() -> None:
    prompt = "Evaluate whether to go long or short on NVDA given current market conditions"

    assert swarm_tool._match_preset(prompt) != "derivatives_strategy_desk"


def test_explicit_preset_name_accepts_spaces() -> None:
    prompt = "Use the investment committee preset for NVDA"

    assert swarm_tool._match_preset(prompt) == "investment_committee"


def test_explicit_preset_parameter_is_normalized() -> None:
    preset, error = swarm_tool._resolve_preset(
        "Continue and finish the report.",
        explicit_preset="Investment Committee",
    )

    assert error is None
    assert preset == "investment_committee"


def test_ambiguous_continuation_does_not_fallback_to_equity_team() -> None:
    preset, error = swarm_tool._resolve_preset(
        "Continue and finish report. Continue from 'Trim 25% of position if price r'."
    )

    assert preset is None
    assert error is not None
    assert "equity_research_team" in error


def test_swarm_tool_rejects_ambiguous_continuation_before_starting_run() -> None:
    payload = json.loads(
        swarm_tool.SwarmTool().execute(
            prompt="Continue and finish report. Continue from 'Trim 25% of position if price r'."
        )
    )

    assert payload["status"] == "error"
    assert "Ambiguous continuation" in payload["error"]


def test_analyst_research_team_english_keyword_match() -> None:
    """English keywords 'analyst research team' should route to analyst_research_team."""
    prompt = "Use the analyst research team to evaluate TSLA given current market conditions"
    assert swarm_tool._match_preset(prompt) == "analyst_research_team"


def test_analyst_research_team_chinese_keyword_match() -> None:
    """Chinese keywords '分析师研究团队' should route to analyst_research_team."""
    prompt = "用分析师研究团队评估一下茅台的投资价值"
    assert swarm_tool._match_preset(prompt) == "analyst_research_team"


def test_analyst_research_team_explicit_preset_name() -> None:
    """Explicit preset name should match analyst_research_team."""
    preset, error = swarm_tool._resolve_preset(
        "Analyze NVDA",
        explicit_preset="analyst_research_team",
    )
    assert error is None
    assert preset == "analyst_research_team"
