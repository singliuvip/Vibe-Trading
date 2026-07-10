"""Tests for swarm preset inspection and static validation."""

from __future__ import annotations

from pathlib import Path

from src.swarm import presets


def test_all_bundled_presets_inspect_without_errors() -> None:
    """Bundled presets should have valid agent/task references and DAGs."""
    for entry in presets.list_presets():
        report = presets.inspect_preset(entry["name"])
        assert report["valid"], f"{entry['name']} errors: {report['errors']}"
        assert report["layers"], f"{entry['name']} has no execution layers"


def test_inspect_preset_returns_dry_run_layers() -> None:
    report = presets.inspect_preset("investment_committee")

    assert report["valid"]
    assert report["variables"] == ["market", "target"]
    assert report["layers"][0] == [
        {"task_id": "task-bull", "agent_id": "bull_advocate"},
        {"task_id": "task-bear", "agent_id": "bear_advocate"},
    ]
    assert report["layers"][-1] == [
        {"task_id": "task-decision", "agent_id": "portfolio_manager"}
    ]


def test_inspect_preset_reports_invalid_references(
    tmp_path: Path,
    monkeypatch,
) -> None:
    preset_dir = tmp_path / "presets"
    preset_dir.mkdir()
    (preset_dir / "broken.yaml").write_text(
        """
name: broken
title: Broken Preset
agents:
  - id: analyst
    role: Analyst
    system_prompt: ""
tasks:
  - id: task-a
    agent_id: missing_agent
    prompt_template: "Analyze {target}"
    depends_on: [missing_task]
variables:
  - name: market
""",
        encoding="utf-8",
    )

    monkeypatch.setattr(presets, "PRESETS_DIR", preset_dir)

    report = presets.inspect_preset("broken")

    assert not report["valid"]
    assert "Task 'task-a' references unknown agent 'missing_agent'" in report["errors"]
    assert "Task 'task-a' depends on unknown task 'missing_task'" in report["errors"]
    assert "Prompt templates use undeclared variables: target" in report["warnings"]
    assert "Declared variables are not used by task prompt templates: market" in report["warnings"]


def test_analyst_research_team_inspect() -> None:
    """Static validation of analyst_research_team: DAG, agents, variables, tools."""
    report = presets.inspect_preset("analyst_research_team")

    assert report["valid"], f"analyst_research_team errors: {report['errors']}"
    assert report["variables"] == ["market", "target"]

    # 8-layer DAG (4 research → 2 bull/bear r1 → 2 bull/bear r2 → 1 research mgr → 1 trader → 3 risk r1 → 3 risk r2 → 1 PM)
    assert len(report["layers"]) == 8, f"expected 8 DAG layers, got {len(report['layers'])}"

    # 17 agents
    assert len(report["agents"]) == 17, f"expected 17 agents, got {len(report['agents'])}"

    # 17 tasks
    assert len(report["tasks"]) == 17, f"expected 17 tasks, got {len(report['tasks'])}"

    # Verify critical agents are present
    agent_ids = {a["id"] for a in report["agents"]}
    assert "market_analyst" in agent_ids
    assert "sentiment_analyst" in agent_ids
    assert "news_analyst" in agent_ids
    assert "fundamentals_analyst" in agent_ids
    assert "bull_researcher" in agent_ids
    assert "bear_researcher" in agent_ids
    assert "research_manager" in agent_ids
    assert "trader" in agent_ids
    assert "portfolio_manager" in agent_ids

    # Layer 0: 4 parallel research analysts
    assert len(report["layers"][0]) == 4
    layer0_tasks = {t["task_id"] for t in report["layers"][0]}
    assert "task-market-analysis" in layer0_tasks
    assert "task-sentiment-analysis" in layer0_tasks
    assert "task-news-analysis" in layer0_tasks
    assert "task-fundamentals-analysis" in layer0_tasks

    # Last layer: portfolio manager decision
    assert len(report["layers"][-1]) == 1
    assert report["layers"][-1][0]["task_id"] == "task-portfolio-manager"
