from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from bestseller.domain.enums import ArtifactType
from bestseller.infra.db.models import ProjectModel
from bestseller.services import planner

pytestmark = pytest.mark.unit


def _project(*, target_chapters: int, strict: bool = False) -> ProjectModel:
    project = ProjectModel(
        slug=f"book-{target_chapters}",
        title="Batch Book",
        genre="urban",
        target_word_count=target_chapters * 2200,
        target_chapters=target_chapters,
        language="zh-CN",
        metadata_json=(
            {"quality_profile": "commercial_strict_prewrite"}
            if strict
            else {}
        ),
    )
    project.id = uuid4()
    return project


def test_planner_stage_token_policy_caps_chapter_outline() -> None:
    assert planner._planner_stage_max_tokens("volume_1_chapter_outline") == 9000
    assert planner._planner_stage_max_tokens("volume_1_chapter_outline_batch_1_10") == 9000
    assert planner._planner_stage_max_tokens("volume_plan") == 8192
    assert planner._planner_stage_max_tokens("story_design_kernel") == 12288


def test_planner_artifact_type_maps_outline_batches_to_volume_outline() -> None:
    assert (
        planner._planner_artifact_type_for_logical_name("volume_1_chapter_outline_batch_1_10")
        == ArtifactType.VOLUME_CHAPTER_OUTLINE
    )
    assert (
        planner._planner_artifact_type_for_logical_name("volume_2_world_disclosure")
        == ArtifactType.VOLUME_WORLD_DISCLOSURE
    )


def test_chapter_outline_batch_ranges_split_500_chapter_volume() -> None:
    ranges = planner._chapter_outline_batch_ranges(
        chapter_number_offset=1,
        expected_count=500,
        batch_size=10,
    )

    assert len(ranges) == 50
    assert ranges[0] == (1, 10, 10)
    assert ranges[-1] == (491, 500, 10)


def test_chapter_outline_batch_size_clamps_long_and_short_projects() -> None:
    settings = SimpleNamespace(pipeline=SimpleNamespace(chapter_outline_batch_size=12))

    assert planner._chapter_outline_batch_size(settings, _project(target_chapters=500)) == 10
    assert planner._chapter_outline_batch_size(settings, _project(target_chapters=32)) == 12
    assert planner._chapter_outline_batch_size(settings, _project(target_chapters=6)) == 3


def test_strict_chapter_outline_batch_size_uses_five_chapter_batches() -> None:
    settings = SimpleNamespace(
        pipeline=SimpleNamespace(
            chapter_outline_batch_size=12,
            commercial_strict_prewrite_chapter_outline_batch_size=5,
        )
    )

    assert (
        planner._chapter_outline_batch_size(
            settings, _project(target_chapters=500, strict=True)
        )
        == 5
    )
    assert (
        planner._chapter_outline_batch_size(
            settings, _project(target_chapters=20, strict=True)
        )
        == 5
    )


def test_planning_meta_round_trip_preserves_payload_without_meta() -> None:
    payload = {"chapters": [{"chapter_number": 1, "title": "开局"}]}

    with_meta = planner._with_planning_meta(
        payload,
        logical_name="volume_1_chapter_outline_batch_1_10",
        input_hash="hash-1",
        workflow_run_id=uuid4(),
        reused_artifact_id=None,
    )

    assert with_meta["_meta"]["input_hash"] == "hash-1"
    assert with_meta["_meta"]["source_step"] == "volume_1_chapter_outline_batch_1_10"
    assert with_meta["_meta"]["reused"] is False
    assert planner._without_planning_meta(with_meta) == payload


def test_planning_meta_records_strict_profile_and_finish_reason() -> None:
    project = _project(target_chapters=20, strict=True)

    with_meta = planner._with_planning_meta(
        {"chapters": []},
        logical_name="volume_1_chapter_outline_batch_1_5",
        input_hash="hash-2",
        workflow_run_id=uuid4(),
        project=project,
        finish_reason="length",
    )

    assert with_meta["_meta"]["quality_profile"] == "commercial_strict_prewrite"
    assert with_meta["_meta"]["methodology_lineage"]["methodology_contract_mode"] == "strict"
    assert with_meta["_meta"]["finish_reason"] == "length"


def test_outline_count_contract_error_is_detected_for_batch_shrink() -> None:
    assert planner._is_outline_count_contract_error(
        ValueError("volume 1 returned 4/5 chapters for volume outline")
    )


def test_missing_book_spec_narrative_lines_are_filled_deterministically() -> None:
    from bestseller.services.narrative_lines import scan_narrative_lines

    project = _project(target_chapters=500)
    payload = {
        "title": project.title,
        "logline": "主角在黑科技风暴中夺回主动权。",
        "protagonist": {"name": "林澈"},
    }

    repaired = planner._fill_missing_book_spec_narrative_lines(
        project=project,
        premise="林澈用黑科技产品撬动城市旧秩序。",
        book_spec_payload=payload,
        volume_count=10,
    )
    report = scan_narrative_lines(
        repaired.get("narrative_lines"),
        total_chapters=500,
        volume_count=10,
        language="zh-CN",
    )

    assert report.is_critical is False
    assert report.has_overt is True
    assert report.has_undercurrent is True
    assert report.has_hidden_thread is True
    assert report.has_core_axis is True
