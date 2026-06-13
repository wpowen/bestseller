"""G1 (xianxia benchmark): supporting-cast motivation contract + roster scaling.

Two production failures being fixed:

1. The cast prompt's supporting-cast element structure listed only
   {name, role, active_volumes, relationship_to_protagonist, evolution_arc} —
   no motivation fields. Models fill exactly what the contract names:
   zhaoshen-hr-v4 shipped 18 supporting characters with goal=0/18, flaw=0/18;
   shilouyan-bench-v1 shipped the inverse (goal filled by luck, arcs empty).
2. The "compact output contract" hard-coded "supporting_cast 默认 3-5 名",
   contradicting the relationship-scaling block (10-volume floor = 15). The
   model obeyed 3-5 (v1 shipped 3 for a 500-chapter epic) and the scaling
   repair couldn't recover ("critical count 1 → 1; keeping original").
"""

from __future__ import annotations

from uuid import uuid4

from bestseller.infra.db.models import ProjectModel
from bestseller.services import planner as planner_services
from bestseller.services.relationship_scaling import compute_supporting_bounds


def _build_project(*, target_chapters: int, language: str | None = None) -> ProjectModel:
    project = ProjectModel(
        slug="cast-motivation-contract",
        title="蚀漏砚",
        genre="仙侠",
        target_word_count=target_chapters * 2200,
        target_chapters=target_chapters,
        audience="男频",
        metadata_json={"language": language} if language else {},
    )
    project.id = uuid4()
    return project


def _cast_prompt(project: ProjectModel) -> str:
    premise = "凡人少年捡到吞噬寿数的古砚。"
    book_spec = planner_services._fallback_book_spec(project, premise)
    world_spec = planner_services._fallback_world_spec(project, premise, book_spec)
    _, user_prompt = planner_services._cast_spec_prompts(project, book_spec, world_spec)
    return user_prompt


def test_supporting_cast_structure_includes_motivation_fields_zh() -> None:
    prompt = _cast_prompt(_build_project(target_chapters=500))
    assert "goal" in prompt
    assert "flaw" in prompt


def test_supporting_cast_structure_includes_motivation_fields_en() -> None:
    prompt = _cast_prompt(_build_project(target_chapters=500, language="en-US"))
    assert "goal" in prompt
    assert "flaw" in prompt


def test_roster_floor_scales_with_volume_count_zh() -> None:
    """A 500-chapter (10-volume) project must NOT be told '3-5 named characters'."""
    prompt = _cast_prompt(_build_project(target_chapters=500))
    bounds = compute_supporting_bounds(10)
    assert "默认 3-5 名" not in prompt
    assert str(bounds.floor) in prompt


def test_roster_floor_scales_with_volume_count_en() -> None:
    prompt = _cast_prompt(_build_project(target_chapters=500, language="en-US"))
    bounds = compute_supporting_bounds(10)
    assert "3-5 named characters" not in prompt
    assert str(bounds.floor) in prompt


def test_small_project_keeps_compact_default_zh() -> None:
    """Short books (single volume) keep a small roster target."""
    prompt = _cast_prompt(_build_project(target_chapters=12))
    bounds = compute_supporting_bounds(1)
    assert str(bounds.floor) in prompt or "3-5" in prompt


def test_cast_spec_stage_cap_scales_with_roster() -> None:
    """A 15-30 character roster (10-volume book) cannot fit in the legacy
    8192-token cap (a single verification run emitted 12615 tokens for 23
    characters). The cast_spec stage cap must scale with the roster so the
    G1 motivation fields don't get truncated away."""
    project = _build_project(target_chapters=500)
    scaled = planner_services._planner_stage_max_tokens("cast_spec", project=project)
    assert scaled is not None and scaled >= 16_000

    # Legacy behaviour without project context stays put.
    assert planner_services._planner_stage_max_tokens("cast_spec") == 8192
    # A short book keeps the compact cap.
    small = _build_project(target_chapters=12)
    assert planner_services._planner_stage_max_tokens("cast_spec", project=small) == 8192
