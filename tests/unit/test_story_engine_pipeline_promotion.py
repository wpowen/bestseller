from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from bestseller.domain.story_engine import canonical_json_hash
from bestseller.infra.db.models import QualityScoreModel
from bestseller.services import pipelines
from bestseller.services.story_engine_review import (
    StoryEngineReceiptRejected,
    StoryEngineReceiptVerdict,
)


def _creative_core() -> dict[str, object]:
    pre_state = {"exposure": {"category": "exposure", "value": 0}}
    post_state = {"exposure": {"category": "exposure", "value": 1}}
    post_hash = canonical_json_hash(post_state)
    return {
        "engine_artifact_id": str(uuid4()),
        "engine_version": 2,
        "window_artifact_id": str(uuid4()),
        "chapter_number": 7,
        "choice_id": "publish",
        "pre_state": pre_state,
        "pre_state_hash": canonical_json_hash(pre_state),
        "known_facts": ["档案将在午夜销毁"],
        "pressure": "管理层正在销毁档案",
        "options": [
            {
                "choice_id": "publish",
                "label": "公开证据",
                "reachable_state_hash": post_hash,
            },
            {
                "choice_id": "hide",
                "label": "隐藏证据",
                "reachable_state_hash": "other-state",
            },
        ],
        "chosen_path": "当众公开档案并承担暴露代价",
        "alternative_costs": ["隐藏会错过窗口"],
        "opponent_strategy": "冻结权限并追查证人",
        "due_obligations": ["保护证人"],
        "required_state_changes": [
            {
                "key": "exposure",
                "category": "exposure",
                "before": 0,
                "operator": "set",
                "after": 1,
                "evidence": "公开档案",
                "monotonic": "non_decreasing",
            }
        ],
        "expected_post_state_hash": post_hash,
        "projection_hash": "projection-7",
        "can_drive_generation": True,
    }


def _inputs() -> tuple[object, object, object, QualityScoreModel, object]:
    project = SimpleNamespace(id=uuid4(), genre="悬疑", metadata_json={})
    chapter = SimpleNamespace(
        id=uuid4(),
        chapter_number=7,
        metadata_json={"story_engine_projection": _creative_core()},
    )
    draft = SimpleNamespace(
        id=uuid4(),
        assembled_from_scene_draft_ids=["chapter_first_scene:1"],
        content_md=(
            "林澈当众公开档案。审计主管随即冻结权限,并派人追查证人。"
        ),
        promotion_state="candidate",
        promotion_metadata={},
    )
    quality = QualityScoreModel(id=uuid4(), judge_key="chapter-quality-v2")
    settings = SimpleNamespace(
        pipeline=SimpleNamespace(
            story_engine_mode="canary",
            story_engine_canary_project_ids=[str(project.id)],
            story_engine_canary_genres=[project.genre],
        )
    )
    return project, chapter, draft, quality, settings


@pytest.mark.asyncio
async def test_canary_promotion_runs_receipt_review_before_atomic_promotion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, chapter, draft, quality, settings = _inputs()
    workflow_run_id = uuid4()
    observation = {"observed_action": "林澈当众公开档案"}
    review = SimpleNamespace(
        verdict=StoryEngineReceiptVerdict.MATCHED,
        blocking_codes=(),
        content={},
    )
    extract = AsyncMock(return_value=observation)
    review_transition = Mock(return_value=review)
    promote = AsyncMock(return_value=SimpleNamespace())
    monkeypatch.setattr(
        pipelines,
        "extract_story_engine_receipt_observation",
        extract,
    )
    monkeypatch.setattr(
        pipelines,
        "review_story_engine_transition",
        review_transition,
    )
    monkeypatch.setattr(
        pipelines,
        "promote_chapter_draft_with_story_engine_receipt",
        promote,
    )

    result = await pipelines._promote_reviewed_chapter_draft(
        AsyncMock(),
        settings=settings,
        project=project,
        chapter=chapter,
        draft=draft,
        quality=quality,
        workflow_run_id=workflow_run_id,
    )

    assert result == (True, "chapter_first")
    extract.assert_awaited_once()
    review_transition.assert_called_once()
    promote.assert_awaited_once()
    assert promote.await_args.kwargs["review"] is review
    assert promote.await_args.kwargs["workflow_run_id"] == workflow_run_id


@pytest.mark.asyncio
async def test_canary_promotion_fails_closed_without_current_chapter_projection() -> None:
    project, chapter, draft, quality, settings = _inputs()
    chapter.metadata_json = {}

    with pytest.raises(
        StoryEngineReceiptRejected,
        match="current-chapter creative projection",
    ) as exc_info:
        await pipelines._promote_reviewed_chapter_draft(
            AsyncMock(),
            settings=settings,
            project=project,
            chapter=chapter,
            draft=draft,
            quality=quality,
            workflow_run_id=uuid4(),
        )

    assert "STORY_ENGINE_RECEIPT_PROJECTION_MISSING" in exc_info.value.blocking_codes


@pytest.mark.asyncio
async def test_dual_write_records_shadow_divergence_but_legacy_promotion_continues(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project, chapter, draft, quality, settings = _inputs()
    project.metadata_json = {"story_engine_mode": "dual_write"}
    shadow_core = _creative_core()
    shadow_core["can_drive_generation"] = False
    chapter.metadata_json = {"story_engine_shadow_projection": shadow_core}
    workflow_run_id = uuid4()
    extract = AsyncMock(return_value={"observed_action": "局势发生了变化"})
    shadow_review = SimpleNamespace(
        verdict=StoryEngineReceiptVerdict.DIVERGED,
        blocking_codes=("STORY_ENGINE_RECEIPT_TRANSITION_DIVERGED",),
        replay_passed=False,
        content={
            "post_state_hash": "",
            "_meta": {"projection_hash": "projection-7"},
        },
    )
    monkeypatch.setattr(
        pipelines,
        "extract_story_engine_receipt_observation",
        extract,
    )
    monkeypatch.setattr(
        pipelines,
        "review_story_engine_transition",
        Mock(return_value=shadow_review),
    )
    monkeypatch.setattr(pipelines, "mark_candidate_under_review", AsyncMock())
    monkeypatch.setattr(pipelines, "mark_draft_eligible", AsyncMock())
    legacy_promote = AsyncMock(
        return_value=SimpleNamespace(promoted_draft_id=draft.id)
    )
    monkeypatch.setattr(pipelines, "promote_chapter_draft", legacy_promote)
    atomic_promote = AsyncMock(
        side_effect=AssertionError("dual-write must not append canonical receipt")
    )
    monkeypatch.setattr(
        pipelines,
        "promote_chapter_draft_with_story_engine_receipt",
        atomic_promote,
    )

    result = await pipelines._promote_reviewed_chapter_draft(
        AsyncMock(),
        settings=settings,
        project=project,
        chapter=chapter,
        draft=draft,
        quality=quality,
        workflow_run_id=workflow_run_id,
    )

    assert result == (True, "chapter_first")
    legacy_promote.assert_awaited_once()
    atomic_promote.assert_not_awaited()
    shadow = draft.promotion_metadata["story_engine_shadow_review"]
    assert shadow["verdict"] == "diverged"
    assert shadow["blocking_codes"] == [
        "STORY_ENGINE_RECEIPT_TRANSITION_DIVERGED"
    ]


def test_story_engine_receipt_defects_force_full_chapter_regeneration() -> None:
    project, chapter, draft, _, _ = _inputs()
    project.metadata_json = {"chapter_first_full_regeneration_max_attempts": 2}
    chapter.metadata_json = {"chapter_first_full_regeneration_count": 0}

    reason = pipelines._chapter_first_full_regeneration_reason(
        project,
        chapter,
        draft,
        ("STORY_ENGINE_RECEIPT_TRANSITION_DIVERGED",),
        attempt_number=1,
    )

    assert reason == (
        "story_engine_receipt_block:"
        "STORY_ENGINE_RECEIPT_TRANSITION_DIVERGED"
    )
