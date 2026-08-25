"""P-6 (xianxia benchmark): volume-plan acceptance contract.

Production failure being fixed: a 500-chapter project (hierarchy → 10
volumes) accepted an LLM volume plan with 5 volumes × 50 chapters = 250
chapters — half the book unplanned — and all five ``reader_hook_to_next``
fields empty. Neither the prompt nor any validator stated/enforced the
required volume count, chapter coverage, or hook completeness.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from bestseller.infra.db.models import ProjectModel
from bestseller.services import planner as planner_services
from bestseller.services.story_bible import enforce_volume_plan_contract


def _volumes(count: int, *, chapters_each: int = 50, hooks: bool = True) -> list[dict[str, Any]]:
    return [
        {
            "volume_number": i + 1,
            "volume_title": f"卷名{i + 1}",
            "volume_theme": f"主题{i + 1}",
            "chapter_count_target": chapters_each,
            "reader_hook_to_next": (f"下一卷钩子{i + 1}" if hooks and i + 1 < count else ""),
        }
        for i in range(count)
    ]


def test_contract_passes_on_full_coverage() -> None:
    enforce_volume_plan_contract(
        _volumes(10),
        expected_volume_count=10,
        target_chapters=500,
    )


def test_contract_rejects_volume_count_mismatch() -> None:
    with pytest.raises(ValueError, match="VOLUME_COUNT_MISMATCH"):
        enforce_volume_plan_contract(
            _volumes(5),
            expected_volume_count=10,
            target_chapters=500,
        )


def test_contract_rejects_chapter_coverage_shortfall() -> None:
    with pytest.raises(ValueError, match="VOLUME_CHAPTER_COVERAGE_SHORT"):
        enforce_volume_plan_contract(
            _volumes(10, chapters_each=25),
            expected_volume_count=10,
            target_chapters=500,
        )


def test_contract_rejects_missing_next_volume_hooks() -> None:
    volumes = _volumes(10)
    volumes[3]["reader_hook_to_next"] = ""
    volumes[7]["reader_hook_to_next"] = None
    with pytest.raises(ValueError, match="VOLUME_HOOK_MISSING"):
        enforce_volume_plan_contract(
            volumes,
            expected_volume_count=10,
            target_chapters=500,
        )


def test_contract_final_volume_hook_optional() -> None:
    volumes = _volumes(10)
    volumes[-1]["reader_hook_to_next"] = ""
    enforce_volume_plan_contract(
        volumes,
        expected_volume_count=10,
        target_chapters=500,
    )


def test_volume_plan_stage_max_tokens_scales_with_volume_count() -> None:
    """8192 output tokens cannot physically hold a 10-volume plan (~18k chars
    in production). The stage cap must scale with the project's volume count
    so the coverage contract is satisfiable at all."""
    project = ProjectModel(
        slug="cap-scale",
        title="蚀漏砚",
        genre="仙侠",
        target_word_count=1_100_000,
        target_chapters=500,
        audience="男频",
        metadata_json={},
    )
    project.id = uuid4()

    scaled = planner_services._planner_stage_max_tokens("volume_plan", project=project)
    assert scaled is not None and scaled >= 20_000

    # Legacy behaviour without project context stays put.
    assert planner_services._planner_stage_max_tokens("volume_plan") == 12288
    # Non-volume-plan stages are unaffected by the project hint.
    assert planner_services._planner_stage_max_tokens("world_spec", project=project) == 12288


def test_volume_plan_prompt_states_count_and_coverage() -> None:
    """The prompt must tell the model how many volumes / chapters to plan."""
    project = ProjectModel(
        slug="volume-contract",
        title="蚀漏砚",
        genre="仙侠",
        target_word_count=1_100_000,
        target_chapters=500,
        audience="男频",
        metadata_json={},
    )
    project.id = uuid4()
    premise = "凡人少年捡到吞噬寿数的古砚。"
    book_spec = planner_services._fallback_book_spec(project, premise)
    world_spec = planner_services._fallback_world_spec(project, premise, book_spec)
    cast_spec = planner_services._fallback_cast_spec(project, premise, book_spec, world_spec)

    _, user_prompt = planner_services._volume_plan_prompts(
        project, book_spec, world_spec, cast_spec
    )

    assert "10" in user_prompt and "500" in user_prompt
    assert "reader_hook_to_next" in user_prompt
    assert "seriality_phase_ref" in user_prompt
    assert "seriality_phase_id" in user_prompt
    assert "unit_family_ref" in user_prompt
    assert "renewable_unit_variant" in user_prompt
    assert "accumulation_track_deltas" in user_prompt
