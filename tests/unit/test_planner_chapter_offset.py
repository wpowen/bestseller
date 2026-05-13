"""Regression tests for `_fallback_chapter_outline_batch` chapter_number_offset.

When re-planning a tail volume after some chapters have already been written,
the fallback must renumber chapters starting at ``max_written + 1`` — not
globally from 1. This prevents the 200-chapter gap seen in
``xianxia-upgrade-1776137730`` where vol 4 got chapter_number 351+ after only
150 chapters had been drafted.
"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from bestseller.infra.db.models import ProjectModel
from bestseller.services import planner as planner_services
from bestseller.settings import load_settings


pytestmark = pytest.mark.unit


def _project(target_chapters: int = 100) -> ProjectModel:
    project = ProjectModel(
        slug="offset-regression",
        title="offset-regression",
        genre="action-progression",
        target_word_count=target_chapters * 3000,
        target_chapters=target_chapters,
        audience="web-serial",
        metadata_json={},
    )
    project.id = uuid4()
    return project


def _load_fallback_inputs(project: ProjectModel):
    book_spec = planner_services._fallback_book_spec(project, "主角逆境崛起")
    world_spec = planner_services._fallback_world_spec(project, "主角逆境崛起", book_spec)
    cast_spec = planner_services._fallback_cast_spec(project, "主角逆境崛起", book_spec, world_spec)
    return book_spec, world_spec, cast_spec


def test_default_offset_starts_at_one() -> None:
    # load_settings cached for performance
    load_settings(
        config_path=Path("config/default.yaml"),
        local_config_path=Path("config/does-not-exist.yaml"),
        env={},
    )
    project = _project(target_chapters=10)
    book_spec, world_spec, cast_spec = _load_fallback_inputs(project)
    volume_plan = [
        {"volume_number": 1, "chapter_count_target": 10, "volume_goal": "g"},
    ]

    batch = planner_services._fallback_chapter_outline_batch(
        project, book_spec, cast_spec, volume_plan
    )
    numbers = [ch["chapter_number"] for ch in batch["chapters"]]
    assert numbers == list(range(1, 11))


def test_volume_plan_normalization_derives_count_from_chapter_range() -> None:
    normalized = planner_services._normalize_volume_plan_payload(
        [
            {
                "volume_number": 2,
                "title": "旧城阴宅门",
                "chapter_range": [51, 100],
                "arc_ranges": [[51, 62], [63, 75], [76, 88], [89, 100]],
            }
        ]
    )

    assert normalized[0]["chapter_count_target"] == 50


def test_volume_plan_normalization_derives_count_from_arc_ranges() -> None:
    normalized = planner_services._normalize_volume_plan_payload(
        [
            {
                "volume_number": 2,
                "title": "旧城阴宅门",
                "arc_ranges": [[51, 62], [63, 75], [76, 88], [89, 100]],
            }
        ]
    )

    assert normalized[0]["chapter_count_target"] == 50


def test_outline_repair_directive_prioritizes_count_mismatch() -> None:
    directives = planner_services._outline_repair_directives_from_error(
        planner_services.PlannerFallbackError(
            "Planner artifact 'volume_2_chapter_outline' returned 30/50 chapters for volume 2."
        ),
        language="zh-CN",
    )

    assert directives[0].startswith("上一版只生成了 30/50 章")
    assert "chapters 数组必须恰好包含 50 项" in directives[0]


def test_outline_repair_directive_distinguishes_overcount_with_volume_bounds() -> None:
    directives = planner_services._outline_repair_directives_from_error(
        planner_services.PlannerFallbackError(
            "Planner artifact 'volume_2_chapter_outline' returned 63/50 chapters for volume 2."
        ),
        language="zh-CN",
        volume_number=2,
        chapter_number_offset=51,
        expected_count=50,
    )

    assert directives[0].startswith("上一版多生成了 63/50 章")
    assert "只允许规划第2卷" in directives[0]
    assert "全局章节号必须限定在第51-100章" in directives[0]
    assert "不得延伸到第101章及以后" in directives[0]


def test_volume_outline_prompt_includes_global_chapter_bounds_from_arc_ranges() -> None:
    project = _project(target_chapters=500)
    book_spec, _, cast_spec = _load_fallback_inputs(project)
    volume_entry = {
        "volume_number": 2,
        "chapter_count_target": 50,
        "arc_ranges": [[51, 62], [63, 74], [75, 86], [87, 98], [99, 100]],
        "volume_goal": "柳如是进入津门，沈青崖追查母亲失踪线索。",
    }

    _, user_prompt = planner_services._volume_outline_prompts(
        project,
        book_spec,
        cast_spec,
        [volume_entry],
        volume_entry,
    )

    assert "全局章节号必须落在第51-100章" in user_prompt
    assert "不能生成第101章及以后的内容" in user_prompt


def test_offset_shifts_numbering_past_written_frontier() -> None:
    project = _project(target_chapters=50)
    book_spec, _, cast_spec = _load_fallback_inputs(project)
    # Replanning vol 4 after 150 chapters are already written
    single_volume_plan = [
        {"volume_number": 4, "chapter_count_target": 50, "volume_goal": "g"},
    ]

    batch = planner_services._fallback_chapter_outline_batch(
        project, book_spec, cast_spec, single_volume_plan,
        chapter_number_offset=151,
    )
    numbers = [ch["chapter_number"] for ch in batch["chapters"]]
    assert numbers == list(range(151, 201))
    # volume_number tag should still be 4 even though only one volume was passed
    assert all(ch["volume_number"] == 4 for ch in batch["chapters"])


def test_offset_below_one_is_clamped() -> None:
    project = _project(target_chapters=5)
    book_spec, _, cast_spec = _load_fallback_inputs(project)
    volume_plan = [{"volume_number": 1, "chapter_count_target": 5, "volume_goal": "g"}]

    # Defensive: negative/zero offset is nonsense — must still produce ch 1..5
    batch = planner_services._fallback_chapter_outline_batch(
        project, book_spec, cast_spec, volume_plan,
        chapter_number_offset=0,
    )
    numbers = [ch["chapter_number"] for ch in batch["chapters"]]
    assert numbers[0] == 1
    assert numbers == list(range(1, 6))
