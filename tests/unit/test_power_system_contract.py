"""P-1 (xianxia benchmark): power_system structured-output contract.

Root cause chain being fixed:
1. ``_world_spec_prompts`` never told the model that ``power_system`` is a
   structured object, so production artifacts stored it as free text and
   ``WorldSpecInput`` coerced the whole paragraph into ``name`` with
   ``tiers=[]`` — progression tracking and tier-consistency checks were
   starved for every book (zhaoshen-hr-v4 and shilouyan-bench-v1 both hit
   this).
2. ``plan_judge._check_power_tier_escalation`` assumed a dict and raised
   ``AttributeError`` on the free-text shape, silently disabling the genre
   escalation check (observed in the shilouyan-bench-v1 planning run).
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from bestseller.infra.db.models import ProjectModel
from bestseller.services import planner as planner_services
from bestseller.services.plan_judge import validate_plan


def _build_project(genre: str = "仙侠") -> ProjectModel:
    project = ProjectModel(
        slug="power-system-contract",
        title="蚀漏砚",
        genre=genre,
        target_word_count=1_100_000,
        target_chapters=500,
        audience="男频",
        metadata_json={},
    )
    project.id = uuid4()
    return project


def _minimal_book_spec() -> dict[str, Any]:
    return {"title": "蚀漏砚", "logline": "凡人少年以寿数为价修行问衡之道。"}


def _make_volume_plan(count: int) -> list[dict[str, Any]]:
    return [
        {
            "volume_number": i + 1,
            "volume_goal": f"目标{i + 1}",
            "volume_theme": f"主题{i + 1}",
            "conflict_phase": ["survival", "political_intrigue", "betrayal", "faction_war"][i % 4],
            "reader_hook_to_next": f"钩子{i + 1}",
        }
        for i in range(count)
    ]


def test_world_spec_prompt_specifies_power_system_structure() -> None:
    """The zh world-spec prompt must spell out power_system's object shape."""
    project = _build_project()
    premise = "凡人少年捡到吞噬寿数的古砚，踏入七境修行路。"
    book_spec = planner_services._fallback_book_spec(project, premise)

    _, user_prompt = planner_services._world_spec_prompts(project, premise, book_spec)

    assert "tiers" in user_prompt
    assert "protagonist_starting_tier" in user_prompt
    assert "acquisition_method" in user_prompt
    assert "hard_limits" in user_prompt


def test_world_spec_prompt_specifies_power_system_structure_en() -> None:
    project = _build_project()
    project.metadata_json = {"language": "en-US"}
    premise = "A mortal youth finds an inkstone that trades lifespan for cultivation."
    book_spec = planner_services._fallback_book_spec(project, premise)

    _, user_prompt = planner_services._world_spec_prompts(project, premise, book_spec)

    assert "tiers" in user_prompt
    assert "protagonist_starting_tier" in user_prompt


def test_world_spec_prompt_requires_per_tier_cost_and_bottleneck() -> None:
    """凡人对标 A2: every 境界 must carry a breakthrough cost + bottleneck.

    A bare ordered name list (tiers=["引气","通脉",...]) clears tier-depth but
    leaves the cultivation ladder mechanically empty — no 突破代价/天槛 per tier.
    The prompt must demand a structured tier_progression so the model emits the
    resource-cost mechanism that the《凡人修仙传》structural bar (A2) requires.
    """
    project = _build_project()
    premise = "凡人少年捡到吞噬寿数的古砚，踏入七境修行路。"
    book_spec = planner_services._fallback_book_spec(project, premise)

    _, user_prompt = planner_services._world_spec_prompts(project, premise, book_spec)

    assert "tier_progression" in user_prompt
    assert "突破代价" in user_prompt or "代价" in user_prompt
    assert "瓶颈" in user_prompt or "天槛" in user_prompt


def test_power_system_input_preserves_tier_progression() -> None:
    """The schema must keep per-tier cost/bottleneck, not flatten them away."""
    from bestseller.domain.story_bible import PowerSystemInput

    ps = PowerSystemInput.model_validate(
        {
            "name": "蚀漏砚体系",
            "tiers": ["引气", "通脉", "凝府"],
            "tier_progression": [
                {"tier": "引气", "breakthrough_cost": "三月寿数", "bottleneck": "灵窍未开的浊脉天槛"},
                {"tier": "通脉", "breakthrough_cost": "一年寿数+一枚通脉丹", "bottleneck": "脉络逆冲之险"},
            ],
            "acquisition_method": "以寿数换修为",
            "hard_limits": "问衡之境三千年无人在世",
            "protagonist_starting_tier": "引气",
        }
    )
    assert len(ps.tier_progression) == 2
    assert ps.tier_progression[0].tier == "引气"
    assert "寿数" in (ps.tier_progression[0].breakthrough_cost or "")
    assert "天槛" in (ps.tier_progression[0].bottleneck or "")


def test_power_tier_escalation_tolerates_free_text_power_system() -> None:
    """A free-text power_system must yield a real finding, not a crash-skip."""
    world_spec: dict[str, Any] = {
        "rules": [{"rule_id": "R001", "description": "规则"}],
        "locations": [{"name": "盐泽镇", "description": "边郡小镇"}],
        # The production failure shape: whole tier ladder as one string.
        "power_system": "七境递进：引气→通脉→凝府→丹枢→洗象→照神→问衡。",
    }

    result = validate_plan(
        genre="action-progression",
        sub_genre=None,
        book_spec=_minimal_book_spec(),
        world_spec=world_spec,
        cast_spec={"protagonist": {"name": "谢迟"}},
        volume_plan=_make_volume_plan(4),
    )

    assert result.rubric_checks.get("power_tier_escalation") is False
    matching = [f for f in result.findings if f.category == "power_tier_escalation"]
    assert matching, "free-text power_system must produce a power_tier_escalation finding"
    # The finding must be the real tier-depth diagnosis, not the generic
    # "check raised an exception" fallback.
    assert all("异常" not in f.message and "exception" not in f.message.lower() for f in matching)
