"""Unit tests for ``quality_levers.critic_personas``."""

from __future__ import annotations

import pytest

from bestseller.services.quality_levers.critic_personas import (
    AggregationPolicy,
    PersonaIssue,
    PersonaResult,
    aggregate_persona_results,
    get_persona,
    load_critic_personas,
    render_persona_system_prompt,
)


pytestmark = pytest.mark.unit


def test_load_critic_personas_returns_four_personas() -> None:
    config = load_critic_personas()

    assert set(config.personas.keys()) == {
        "platform_editor",
        "new_reader",
        "loyal_reader",
        "peer_author",
    }
    editor = config.personas["platform_editor"]
    assert editor.scoring_dimensions
    weights = [dim.weight for dim in editor.scoring_dimensions]
    assert all(w > 0 for w in weights)
    assert editor.must_rewrite_triggers


def test_aggregation_policy_defaults() -> None:
    policy = load_critic_personas().aggregation
    assert policy.hard_floor == pytest.approx(0.65)
    assert policy.soft_floor == pytest.approx(0.75)
    assert policy.soft_floor_min_count >= 2
    assert policy.max_issues_per_rewrite >= 1


def test_render_persona_system_prompt_returns_relevant_content() -> None:
    block = render_persona_system_prompt("peer_author")
    assert "peer_author" in block
    assert "JSON" in block
    assert "扮演" in block


def test_render_persona_system_prompt_returns_empty_for_unknown() -> None:
    assert render_persona_system_prompt("ghost_persona") == ""


def _persona(
    persona_id: str, score: float, *, must_rewrite: bool = False, issues=()
) -> PersonaResult:
    return PersonaResult(
        persona_id=persona_id,
        overall_score=score,
        must_rewrite=must_rewrite,
        issues=tuple(issues),
        verdict="rewrite" if must_rewrite else "accept",
    )


def test_aggregate_persona_results_no_rewrite_when_all_pass() -> None:
    results = [
        _persona("platform_editor", 0.85),
        _persona("new_reader", 0.80),
        _persona("loyal_reader", 0.78),
        _persona("peer_author", 0.79),
    ]
    out = aggregate_persona_results(results)
    assert out.must_rewrite is False
    assert out.min_score == pytest.approx(0.78)
    assert out.avg_score == pytest.approx(0.805)
    assert out.consensus_issues == ()


def test_aggregate_persona_results_triggers_hard_floor() -> None:
    results = [
        _persona("platform_editor", 0.85),
        _persona("new_reader", 0.80),
        _persona("loyal_reader", 0.78),
        _persona("peer_author", 0.60),  # below hard floor 0.65
    ]
    out = aggregate_persona_results(results)
    assert out.must_rewrite is True
    assert out.rewrite_reason == "hard_floor_breached"


def test_aggregate_persona_results_triggers_soft_floor_consensus() -> None:
    # All above hard floor but 3 below soft floor — triggers rewrite
    results = [
        _persona("platform_editor", 0.72),
        _persona("new_reader", 0.74),
        _persona("loyal_reader", 0.85),
        _persona("peer_author", 0.70),
    ]
    out = aggregate_persona_results(results)
    assert out.must_rewrite is True
    assert out.rewrite_reason == "soft_floor_consensus"


def test_aggregate_persona_results_collects_consensus_issues() -> None:
    issue_a = PersonaIssue(
        issue="主角对白通用化",
        severity="high",
        suggested_cause_id="weak_character_hook",
    )
    issue_b = PersonaIssue(
        issue="主角对白通用化",
        severity="critical",
        suggested_cause_id="weak_character_hook",
    )
    issue_solo = PersonaIssue(
        issue="第三人称视角飘移",
        severity="medium",
    )
    results = [
        _persona("platform_editor", 0.78, issues=(issue_a, issue_solo)),
        _persona("peer_author", 0.68, issues=(issue_b,)),
        _persona("new_reader", 0.74),
        _persona("loyal_reader", 0.80),
    ]
    out = aggregate_persona_results(results)
    assert any("对白" in c.issue for c in out.consensus_issues)
    consensus = next(c for c in out.consensus_issues if "对白" in c.issue)
    # Highest severity preserved
    assert consensus.severity == "critical"
    assert set(consensus.votes) == {"platform_editor", "peer_author"}
    assert "weak_character_hook" in consensus.suggested_cause_ids
    assert "weak_character_hook" in out.merged_cause_ids
    # Solo issue must NOT show up in consensus
    assert all("视角" not in c.issue for c in out.consensus_issues)


def test_aggregate_persona_results_handles_empty_input() -> None:
    out = aggregate_persona_results([])
    assert out.must_rewrite is False
    assert out.min_score == 0.0
    assert out.consensus_issues == ()


def test_aggregate_persona_results_respects_custom_policy() -> None:
    strict = AggregationPolicy(
        hard_floor=0.80,
        soft_floor=0.90,
        soft_floor_min_count=2,
        consensus_threshold=2,
        max_issues_per_rewrite=5,
    )
    results = [
        _persona("platform_editor", 0.85),
        _persona("new_reader", 0.85),
        _persona("loyal_reader", 0.85),
        _persona("peer_author", 0.85),
    ]
    out = aggregate_persona_results(results, policy=strict)
    # All below 0.90 soft floor → triggers rewrite under the strict policy
    assert out.must_rewrite is True
