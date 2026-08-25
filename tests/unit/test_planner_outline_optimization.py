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
    # Heavy structured stages use the full planner completion budget so the
    # model does not truncate mid-JSON (finish_reason="length") and fall into a
    # validation-failure retry loop. Lighter stages keep their smaller caps.
    assert planner._planner_stage_max_tokens("volume_1_chapter_outline") == 16384
    assert planner._planner_stage_max_tokens("volume_1_chapter_outline_batch_1_10") == 16384
    assert planner._planner_stage_max_tokens("story_design_kernel") == 16384
    assert planner._planner_stage_max_tokens("emotion_driven_kernel") == 16384
    # 2026-08-24：结构化 spec 帽 8192→12288（book/cast_spec 生产截断定罪）。
    assert planner._planner_stage_max_tokens("volume_plan") == 12288
    assert planner._planner_stage_max_tokens("book_spec") == 12288


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


def _outline_batch_with_empty_executable_fields():
    from bestseller.domain.workflow import ChapterOutlineBatchInput

    return ChapterOutlineBatchInput.model_validate(
        {
            "batch_name": "v1",
            "chapters": [
                {
                    "chapter_number": 1,
                    "title": "签字",
                    "protagonist_flaw": None,
                    "scenes": [
                        {
                            "scene_number": 1,
                            "title": "站点对峙",
                            "protagonist_state": "被HR堵在站点，只想保住工作。",
                            "cut_point": "签字瞬间异能觉醒。",
                            "participants": ["纪渊"],
                            "entry_state": {},
                            "exit_state": {},
                        }
                    ],
                }
            ],
        }
    )


def test_repair_backfills_entry_exit_state_and_protagonist_flaw() -> None:
    batch = _outline_batch_with_empty_executable_fields()
    repaired = planner._repair_generated_volume_outline_contract_inputs(
        batch,
        identity_manifest=[{"name": "纪渊", "role": "protagonist"}],
        protagonist_flaw_default="习惯把压力全部扛在自己身上。",
    )
    assert repaired >= 3
    chapter = batch.chapters[0]
    scene = chapter.scenes[0]
    assert scene.entry_state and scene.entry_state.get("summary")
    assert scene.exit_state and scene.exit_state.get("summary")
    assert chapter.protagonist_flaw == "习惯把压力全部扛在自己身上。"


def test_repair_passes_planning_readiness_gate() -> None:
    from bestseller.services.planning_readiness_gate import (
        evaluate_chapter_outline_batch_planning_readiness,
    )

    batch = _outline_batch_with_empty_executable_fields()
    before = evaluate_chapter_outline_batch_planning_readiness(batch)
    assert not before.passed  # empty executable fields block

    planner._repair_generated_volume_outline_contract_inputs(
        batch,
        identity_manifest=[{"name": "纪渊", "role": "protagonist"}],
        protagonist_flaw_default="习惯把压力全部扛在自己身上。",
    )
    after = evaluate_chapter_outline_batch_planning_readiness(batch)
    # The repair specifically resolves the entry_state / exit_state / protagonist_flaw
    # findings (other sparse-fixture fields may still flag, which is expected).
    resolved_paths = {"entry_state", "exit_state", "protagonist_flaw"}
    remaining_targeted = [
        f
        for f in after.blocking_findings
        if any(p in (f.path or "") for p in resolved_paths)
    ]
    assert remaining_targeted == [], [f.path for f in remaining_targeted]


def test_validate_volume_outline_backfills_flaw_via_caller_path() -> None:
    # Regression: the caller must extract the multi-candidate protagonist flaw
    # with _first_non_empty_text, not _non_empty_string (which takes 2 args).
    # This exercises _validate_generated_volume_outline_or_raise, which the
    # repair-loop runs on every attempt — a TypeError here fails all attempts.
    project = _project(target_chapters=20, strict=True)
    cast_spec = {
        "protagonist": {"name": "纪渊", "flaw": "习惯把压力全部扛在自己身上。"}
    }
    chapters = [
        {
            "chapter_number": i,
            "title": f"第{i}章 站点{i}",
            "protagonist_flaw": None,
            "scenes": [
                {
                    "scene_number": 1,
                    "title": f"场景{i}",
                    "participants": ["纪渊"],
                    "protagonist_state": "被HR堵在站点。",
                    "cut_point": "签字瞬间异能觉醒。",
                    "entry_state": {},
                    "exit_state": {},
                }
            ],
        }
        for i in range(1, 6)
    ]
    payload = {"batch_name": "v1", "chapters": chapters}
    try:
        planner._validate_generated_volume_outline_or_raise(
            payload,
            project=project,
            logical_name="volume_1_chapter_outline_batch_1_5",
            volume_number=1,
            expected_count=5,
            chapter_number_offset=1,
            cast_spec=cast_spec,
        )
    except TypeError as exc:  # the specific regression
        raise AssertionError(f"caller flaw extraction crashed: {exc}") from exc
    except Exception:
        # Other downstream contract validations may still reject this minimal
        # fixture — that is fine; this test only guards the flaw-extraction crash.
        pass
