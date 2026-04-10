from __future__ import annotations

from typing import Any

import pytest

from bestseller.services.plan_judge import validate_plan


pytestmark = pytest.mark.unit


# ═══════════════════════════════════════════════════════════════════════
# Helpers / fixtures
# ═══════════════════════════════════════════════════════════════════════

_CONFLICT_PHASES = [
    "survival",
    "political_intrigue",
    "betrayal",
    "faction_war",
    "existential_threat",
    "pursuit",
    "revelation",
    "escalation",
]


def _make_volume_plan(
    n: int,
    *,
    distinct_goals: bool = True,
    varied_phases: bool = True,
    with_hooks: bool = True,
) -> list[dict[str, Any]]:
    """Generate *n* volume plan entries with controllable quality.

    Parameters
    ----------
    n:
        Number of volumes to generate.
    distinct_goals:
        When True each volume gets a unique ``volume_goal``; when False all
        volumes share the same goal text.
    varied_phases:
        When True each volume gets a different ``conflict_phase``; when False
        all volumes share the same phase.
    with_hooks:
        When True non-last volumes get a ``reader_hook_to_next`` value; when
        False the field is omitted.
    """
    volumes: list[dict[str, Any]] = []
    for i in range(n):
        vol: dict[str, Any] = {
            "volume_number": i + 1,
            "volume_goal": f"volume-{i + 1}-goal-{'unique' if distinct_goals else 'same'}"
            if distinct_goals
            else "same-goal-for-all",
            "volume_theme": f"theme-{i + 1}",
            "conflict_phase": _CONFLICT_PHASES[i % len(_CONFLICT_PHASES)]
            if varied_phases
            else "survival",
            "primary_force_name": f"force-{(i % 3) + 1}",
            "key_reveals": [f"reveal-{i + 1}-a"] if i % 2 == 0 else [],
            "foreshadowing_planted": [f"hint-{i + 1}"] if i < n // 2 else [],
            "foreshadowing_paid_off": [f"hint-{i - n // 2 + 1}"] if i >= n // 2 else [],
        }
        if with_hooks and i < n - 1:
            vol["reader_hook_to_next"] = f"hook-to-vol-{i + 2}"
        volumes.append(vol)
    return volumes


def _minimal_book_spec() -> dict[str, Any]:
    """Minimal valid book specification dict."""
    return {
        "title": "Test Novel",
        "logline": "A hero must survive.",
        "genre": "fantasy",
        "target_audience": "web-serial",
        "tone": ["dark", "suspenseful"],
        "themes": ["survival", "trust"],
        "protagonist": {
            "name": "Lin",
            "role": "navigator",
        },
        "stakes": "The empire collapses if the truth is buried.",
        "series_engine": {
            "core_engine": "escalating power tiers",
            "reader_promise": "continuous growth",
        },
    }


def _minimal_world_spec(*, power_tiers: int = 5) -> dict[str, Any]:
    """Minimal valid world specification dict.

    Parameters
    ----------
    power_tiers:
        Number of power system tiers to include.  Set to 0 to omit the
        power_system entirely.
    """
    spec: dict[str, Any] = {
        "rules": [
            {"rule_id": f"R{str(i + 1).zfill(3)}", "description": f"Rule {i + 1}"}
            for i in range(3)
        ],
        "locations": [
            {"name": "Capital City", "description": "The seat of power."},
            {"name": "Border Outpost", "description": "A frontier settlement."},
        ],
    }
    if power_tiers > 0:
        spec["power_system"] = {
            "tiers": [
                {"tier_id": f"T{i + 1}", "name": f"Tier {i + 1}"}
                for i in range(power_tiers)
            ],
        }
    return spec


def _minimal_cast_spec(*, with_forces: bool = True) -> dict[str, Any]:
    """Minimal valid cast specification dict."""
    spec: dict[str, Any] = {
        "protagonist": {
            "name": "Lin",
            "role": "navigator",
            "relationships": [{"character": "Gu", "type": "rival"}],
        },
    }
    if with_forces:
        spec["antagonist_forces"] = [
            {"name": "The Empire", "force_type": "faction"},
            {"name": "The Void", "force_type": "environment"},
        ]
    return spec


# ═══════════════════════════════════════════════════════════════════════
# Tests — universal checks
# ═══════════════════════════════════════════════════════════════════════


def test_valid_plan_passes() -> None:
    """A well-formed plan with distinct goals, varied phases, and hooks passes."""
    result = validate_plan(
        genre="action-progression",
        sub_genre=None,
        book_spec=_minimal_book_spec(),
        world_spec=_minimal_world_spec(),
        cast_spec=_minimal_cast_spec(),
        volume_plan=_make_volume_plan(4, distinct_goals=True, varied_phases=True, with_hooks=True),
    )
    assert result.overall_pass is True
    assert result.score > 0.5


def test_duplicate_volume_goals_fails() -> None:
    """Plan with identical volume_goal values fails the volume_goals_distinct check."""
    result = validate_plan(
        genre="action-progression",
        sub_genre=None,
        book_spec=_minimal_book_spec(),
        world_spec=_minimal_world_spec(),
        cast_spec=_minimal_cast_spec(),
        volume_plan=_make_volume_plan(4, distinct_goals=False, varied_phases=True, with_hooks=True),
    )
    assert result.rubric_checks.get("volume_goals_distinct") is False
    matching = [f for f in result.findings if f.category == "volume_goals"]
    assert len(matching) >= 1


def test_missing_hooks_fails() -> None:
    """Plan missing reader_hook_to_next for non-last volumes fails the hooks check."""
    result = validate_plan(
        genre="action-progression",
        sub_genre=None,
        book_spec=_minimal_book_spec(),
        world_spec=_minimal_world_spec(),
        cast_spec=_minimal_cast_spec(),
        volume_plan=_make_volume_plan(4, distinct_goals=True, varied_phases=True, with_hooks=False),
    )
    assert result.rubric_checks.get("hooks") is False
    matching = [f for f in result.findings if f.category == "hooks"]
    assert len(matching) >= 1


def test_no_challenge_evolution_fails() -> None:
    """Plan where all volumes have same conflict_phase fails challenge_evolution."""
    result = validate_plan(
        genre="action-progression",
        sub_genre=None,
        book_spec=_minimal_book_spec(),
        world_spec=_minimal_world_spec(),
        cast_spec=_minimal_cast_spec(),
        volume_plan=_make_volume_plan(4, distinct_goals=True, varied_phases=False, with_hooks=True),
    )
    assert result.rubric_checks.get("challenge_evolution") is False
    matching = [f for f in result.findings if f.category == "challenge_evolution"]
    assert len(matching) >= 1


# ═══════════════════════════════════════════════════════════════════════
# Tests — action-progression genre checks
# ═══════════════════════════════════════════════════════════════════════


def test_action_progression_no_power_tiers_fails() -> None:
    """Action-progression genre with no power system tiers fails the escalation check."""
    result = validate_plan(
        genre="action-progression",
        sub_genre=None,
        book_spec=_minimal_book_spec(),
        world_spec=_minimal_world_spec(power_tiers=0),
        cast_spec=_minimal_cast_spec(),
        volume_plan=_make_volume_plan(4, distinct_goals=True, varied_phases=True, with_hooks=True),
    )
    assert result.rubric_checks.get("power_tier_escalation") is False
    matching = [f for f in result.findings if f.category == "power_tier_escalation"]
    assert len(matching) >= 1


def test_action_progression_with_power_tiers_passes() -> None:
    """Action-progression with proper power tiers passes the escalation check."""
    volumes = _make_volume_plan(4, distinct_goals=True, varied_phases=True, with_hooks=True)
    # Add escalating power tiers across volumes
    for i, vol in enumerate(volumes):
        vol["opening_state"] = {"protagonist_power_tier": f"T{i + 1}"}
        vol["volume_resolution"] = {"protagonist_power_tier": f"T{i + 2}"}

    result = validate_plan(
        genre="action-progression",
        sub_genre=None,
        book_spec=_minimal_book_spec(),
        world_spec=_minimal_world_spec(power_tiers=5),
        cast_spec=_minimal_cast_spec(),
        volume_plan=volumes,
    )
    assert result.rubric_checks.get("power_tier_escalation") is True


# ═══════════════════════════════════════════════════════════════════════
# Tests — relationship-driven genre checks
# ═══════════════════════════════════════════════════════════════════════


def test_relationship_driven_no_milestones_fails() -> None:
    """Relationship-driven genre without relationship keywords in volume goals fails."""
    # Build volumes whose goals and themes have zero relationship keywords
    volumes = _make_volume_plan(4, distinct_goals=True, varied_phases=True, with_hooks=True)
    for vol in volumes:
        vol["volume_goal"] = f"explore-territory-{vol['volume_number']}"
        vol["volume_theme"] = f"survival-phase-{vol['volume_number']}"

    result = validate_plan(
        genre="女性成长",
        sub_genre=None,
        book_spec=_minimal_book_spec(),
        world_spec=_minimal_world_spec(),
        cast_spec=_minimal_cast_spec(),
        volume_plan=volumes,
    )
    assert result.rubric_checks.get("relationship_milestone_progression") is False
    matching = [f for f in result.findings if f.category == "relationship_milestones"]
    assert len(matching) >= 1


def test_relationship_driven_with_milestones_passes() -> None:
    """Relationship-driven with proper relationship milestones passes."""
    volumes = _make_volume_plan(4, distinct_goals=True, varied_phases=True, with_hooks=True)
    # Inject relationship keywords into goals / themes
    relationship_goals = [
        "建立初步信任，打破防备",
        "误解与考验中靠近彼此",
        "在选择面前直面关系质变",
        "并肩对抗外部威胁，情感升华",
    ]
    for i, vol in enumerate(volumes):
        vol["volume_goal"] = relationship_goals[i]
        vol["volume_theme"] = f"情感成长阶段{i + 1}"

    result = validate_plan(
        genre="女性成长",
        sub_genre=None,
        book_spec=_minimal_book_spec(),
        world_spec=_minimal_world_spec(),
        cast_spec=_minimal_cast_spec(),
        volume_plan=volumes,
    )
    assert result.rubric_checks.get("relationship_milestone_progression") is True


# ═══════════════════════════════════════════════════════════════════════
# Tests — suspense-mystery genre checks
# ═══════════════════════════════════════════════════════════════════════


def test_suspense_mystery_no_clue_chain_fails() -> None:
    """Suspense-mystery without distributed key_reveals fails the clue_chain check."""
    volumes = _make_volume_plan(4, distinct_goals=True, varied_phases=True, with_hooks=True)
    # Remove key_reveals from all but one volume
    for vol in volumes:
        vol["key_reveals"] = []
    volumes[0]["key_reveals"] = ["the-only-reveal"]

    result = validate_plan(
        genre="suspense-mystery",
        sub_genre=None,
        book_spec=_minimal_book_spec(),
        world_spec=_minimal_world_spec(),
        cast_spec=_minimal_cast_spec(),
        volume_plan=volumes,
    )
    assert result.rubric_checks.get("clue_chain_exists") is False
    matching = [f for f in result.findings if f.category == "clue_chain"]
    assert len(matching) >= 1


# ═══════════════════════════════════════════════════════════════════════
# Tests — edge cases
# ═══════════════════════════════════════════════════════════════════════


def test_empty_volume_plan_passes_gracefully() -> None:
    """An empty volume plan should not crash and should return a result."""
    result = validate_plan(
        genre="action-progression",
        sub_genre=None,
        book_spec=_minimal_book_spec(),
        world_spec=_minimal_world_spec(),
        cast_spec=_minimal_cast_spec(),
        volume_plan=[],
    )
    # Should not crash; score and findings should be sensible
    assert isinstance(result.score, float)
    assert isinstance(result.findings, list)
    assert isinstance(result.rubric_checks, dict)
