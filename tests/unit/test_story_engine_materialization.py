from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from bestseller.domain.story_engine import StoryEngineMaturity, canonical_json_hash
from bestseller.infra.db.models import (
    StoryEngineCanaryCampaignModel,
    StoryEngineReaderStudyModel,
)
from bestseller.services import workflows
from bestseller.services.narrative import (
    _chapter_contract_metadata_from_chapter,
    _scene_contract_metadata_from_scene,
)
from bestseller.services.story_engine import (
    StoryEngineVerifiedRolloutEvidence,
    apply_story_engine_projection_to_chapter,
    build_story_engine_window_artifact_content,
    chapter_creative_core_from_window_content,
    chapter_observation_core_from_window_content,
    render_story_engine_creative_core_block,
    resolve_story_engine_mode,
    resolve_story_engine_rollout_decision,
    resolve_story_engine_rollout_decision_from_db,
)


def _window_payload() -> dict[str, object]:
    pre_state = {"pressure": {"category": "exposure", "value": 0}}
    post_state = {"pressure": {"category": "exposure", "value": 1}}
    post_hash = canonical_json_hash(post_state)
    return {
        "window_id": "window-1",
        "engine_id": "engine-1",
        "engine_version": 2,
        "engine_artifact_id": str(uuid4()),
        "source_engine_hash": "engine-hash",
        "start_chapter": 1,
        "end_chapter": 1,
        "projections": [
            {
                "chapter_number": 1,
                "choice_id": "publish",
                "pre_state": pre_state,
                "pre_state_hash": canonical_json_hash(pre_state),
                "known_facts": ["档案室今晚封存"],
                "pressure": "对手正在销毁证据",
                "options": [
                    {
                        "choice_id": "publish",
                        "label": "公开证据",
                        "reachable_state_hash": post_hash,
                    },
                    {
                        "choice_id": "hide",
                        "label": "隐藏证据",
                        "reachable_state_hash": "alternative-hash",
                    },
                ],
                "chosen_option_id": "publish",
                "chosen_path": "公开证据并保护证人",
                "alternative_costs": ["隐藏会让证人失去窗口"],
                "opponent_strategy": "冻结权限并追查证人",
                "due_obligations": ["保护证人"],
                "required_state_changes": [
                    {
                        "key": "pressure",
                        "category": "exposure",
                        "before": 0,
                        "operator": "set",
                        "after": 1,
                        "evidence": "主角公开了证据",
                        "monotonic": "any",
                    }
                ],
                "expected_post_state_hash": post_hash,
                "fingerprint": "publish|evidence|pressure",
                "projection_hash": "projection-hash",
            }
        ],
    }


def test_canary_window_projects_one_chapter_without_future_rows() -> None:
    content = build_story_engine_window_artifact_content(
        _window_payload(),
        maturity=StoryEngineMaturity.CANARY_VALIDATED,
        can_drive_generation=True,
    )

    core = chapter_creative_core_from_window_content(
        content,
        chapter_number=1,
        window_artifact_id=uuid4(),
    )

    assert core is not None
    assert core["choice_id"] == "publish"
    assert core["can_drive_generation"] is True
    assert "projections" not in core
    assert "future_facts" not in core


def test_shadow_window_cannot_project_into_writer_context() -> None:
    content = build_story_engine_window_artifact_content(
        _window_payload(),
        maturity=StoryEngineMaturity.SHADOW_VALIDATED,
        can_drive_generation=False,
    )

    assert chapter_creative_core_from_window_content(
        content,
        chapter_number=1,
        window_artifact_id=uuid4(),
    ) is None


def test_shadow_window_can_project_only_to_the_observation_lane() -> None:
    content = build_story_engine_window_artifact_content(
        _window_payload(),
        maturity=StoryEngineMaturity.SHADOW_VALIDATED,
        can_drive_generation=False,
    )

    core = chapter_observation_core_from_window_content(
        content,
        chapter_number=1,
        window_artifact_id=uuid4(),
    )

    assert core is not None
    assert core["choice_id"] == "publish"
    assert core["can_drive_generation"] is False


def test_unvalidated_window_cannot_claim_generation_authority() -> None:
    with pytest.raises(ValueError, match="only canary-validated"):
        build_story_engine_window_artifact_content(
            _window_payload(),
            maturity=StoryEngineMaturity.SHADOW_VALIDATED,
            can_drive_generation=True,
        )


def test_creative_core_renderer_ignores_missing_or_non_authoritative_payloads() -> None:
    assert render_story_engine_creative_core_block(None, language="zh-CN") == ""
    assert (
        render_story_engine_creative_core_block(
            {"can_drive_generation": False},
            language="zh-CN",
        )
        == ""
    )


def test_projection_attachment_preserves_unrelated_metadata() -> None:
    chapter = SimpleNamespace(metadata_json={"existing": {"keep": True}})
    scenes = [SimpleNamespace(metadata_json={"scene": 1})]
    core = {
        "chapter_number": 1,
        "choice_id": "publish",
        "projection_hash": "projection-hash",
        "can_drive_generation": True,
    }

    apply_story_engine_projection_to_chapter(chapter, scenes=scenes, creative_core=core)

    assert chapter.metadata_json["existing"] == {"keep": True}
    assert chapter.metadata_json["story_engine_projection"] == core
    assert scenes[0].metadata_json["scene"] == 1
    assert scenes[0].metadata_json["story_engine_projection_ref"] == {
        "chapter_number": 1,
        "choice_id": "publish",
        "projection_hash": "projection-hash",
    }


@pytest.mark.parametrize(
    "core",
    [
        {
            "chapter_number": 1,
            "choice_id": "publish",
            "projection_hash": "projection-hash",
            "can_drive_generation": False,
        },
        {
            "chapter_number": 0,
            "choice_id": "",
            "projection_hash": "",
            "can_drive_generation": True,
        },
    ],
)
def test_projection_attachment_rejects_non_authoritative_or_incomplete_core(
    core: dict[str, object],
) -> None:
    chapter = SimpleNamespace(metadata_json={})

    with pytest.raises(ValueError):
        apply_story_engine_projection_to_chapter(
            chapter,
            scenes=[],
            creative_core=core,
        )


def _rollout_settings(
    *,
    project_ids: list[str] | None = None,
    genres: list[str] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        pipeline=SimpleNamespace(
            story_engine_mode="shadow",
            story_engine_canary_project_ids=project_ids or [],
            story_engine_canary_genres=genres or [],
            story_engine_require_reader_validation_for_cutover=True,
        )
    )


def test_story_engine_canary_override_is_fail_closed_without_allowlist() -> None:
    project = SimpleNamespace(
        id=uuid4(),
        genre="悬疑",
        current_chapter_number=0,
        metadata_json={"story_engine_mode": "canary"},
    )

    decision = resolve_story_engine_rollout_decision(
        project,
        _rollout_settings(),
    )

    assert decision.requested_mode == "canary"
    assert decision.effective_mode == "shadow"
    assert decision.authorized is False
    assert "STORY_ENGINE_CANARY_PROJECT_NOT_ALLOWLISTED" in decision.blocking_codes
    assert resolve_story_engine_mode(project, _rollout_settings()) == "shadow"


def test_story_engine_canary_requires_project_and_genre_allowlist() -> None:
    project_id = uuid4()
    project = SimpleNamespace(
        id=project_id,
        genre="悬疑",
        current_chapter_number=0,
        metadata_json={"story_engine_mode": "canary"},
    )

    allowed = resolve_story_engine_rollout_decision(
        project,
        _rollout_settings(
            project_ids=[str(project_id)],
            genres=["悬疑"],
        ),
    )
    wrong_genre = resolve_story_engine_rollout_decision(
        project,
        _rollout_settings(
            project_ids=[str(project_id)],
            genres=["都市"],
        ),
    )

    assert allowed.effective_mode == "canary"
    assert allowed.authorized is True
    assert wrong_genre.effective_mode == "shadow"
    assert "STORY_ENGINE_CANARY_GENRE_NOT_ALLOWLISTED" in wrong_genre.blocking_codes


def test_story_engine_canonical_requires_live_canary_and_reader_artifacts() -> None:
    project_id = uuid4()
    settings = _rollout_settings(
        project_ids=[str(project_id)],
        genres=["悬疑"],
    )
    project = SimpleNamespace(
        id=project_id,
        genre="悬疑",
        current_chapter_number=10,
        metadata_json={"story_engine_mode": "canonical"},
    )

    blocked = resolve_story_engine_rollout_decision(project, settings)
    project.metadata_json = {
        "story_engine_mode": "canonical",
        "story_engine_canary_validation": {
            "status": "passed",
            "evidence_source": "live",
            "artifact_id": str(uuid4()),
        },
        "story_engine_reader_validation": {
            "status": "passed",
            "evidence_source": "live",
            "artifact_id": str(uuid4()),
        },
    }
    forged_metadata = resolve_story_engine_rollout_decision(project, settings)
    allowed = resolve_story_engine_rollout_decision(
        project,
        settings,
        verified_evidence=StoryEngineVerifiedRolloutEvidence(
            canary_campaign_id=UUID(
                project.metadata_json["story_engine_canary_validation"]["artifact_id"]
            ),
            reader_study_id=UUID(
                project.metadata_json["story_engine_reader_validation"]["artifact_id"]
            ),
        ),
    )

    assert blocked.effective_mode == "shadow"
    assert "STORY_ENGINE_LIVE_CANARY_EVIDENCE_REQUIRED" in blocked.blocking_codes
    assert "STORY_ENGINE_READER_EVIDENCE_REQUIRED" in blocked.blocking_codes
    assert forged_metadata.effective_mode == "shadow"
    assert forged_metadata.authorized is False
    assert allowed.effective_mode == "canonical"
    assert allowed.authorized is True


@pytest.mark.asyncio
async def test_story_engine_canonical_resolves_persisted_evidence_and_project_scope() -> None:
    project_id = uuid4()
    canary_id = uuid4()
    reader_id = uuid4()
    settings = _rollout_settings(
        project_ids=[str(project_id)],
        genres=["悬疑"],
    )
    project = SimpleNamespace(
        id=project_id,
        genre="悬疑",
        current_chapter_number=10,
        metadata_json={
            "story_engine_mode": "canonical",
            "story_engine_canary_validation": {
                "status": "passed",
                "evidence_source": "live",
                "artifact_id": str(canary_id),
            },
            "story_engine_reader_validation": {
                "status": "passed",
                "evidence_source": "live",
                "artifact_id": str(reader_id),
            },
        },
    )
    canary_manifest = {
        "cells": [{"engine_project_id": str(project_id)}],
    }
    canary_hash = canonical_json_hash(canary_manifest)
    campaign = StoryEngineCanaryCampaignModel(
        id=canary_id,
        campaign_key="e1-live",
        experiment="E1",
        status="canary_validated",
        evidence_source="live",
        manifest_hash=canary_hash,
        manifest_json=canary_manifest,
        report_json={
            "manifest_hash": canary_hash,
            "cohort": {"canary_ready": True},
        },
    )
    reader_manifest = {
        "canary_campaign_id": str(canary_id),
        "cells": [{"engine_project_id": str(project_id)}],
    }
    reader_hash = canonical_json_hash(reader_manifest)
    study = StoryEngineReaderStudyModel(
        id=reader_id,
        study_key="reader-live",
        canary_campaign_id=canary_id,
        status="reader_validated",
        evidence_source="live",
        manifest_hash=reader_hash,
        manifest_json=reader_manifest,
        report_json={
            "manifest_hash": reader_hash,
            "response_evidence_hash": "a" * 64,
            "report": {"reader_ready": True},
        },
    )
    session = AsyncMock()
    session.get = AsyncMock(side_effect=[campaign, study])

    decision = await resolve_story_engine_rollout_decision_from_db(
        session,
        project,
        settings,
        chapter_number=11,
    )

    assert decision.effective_mode == "canonical"
    assert decision.authorized is True
    assert session.get.await_count == 2


@pytest.mark.asyncio
async def test_story_engine_canonical_rejects_evidence_from_another_project() -> None:
    project_id = uuid4()
    canary_id = uuid4()
    settings = _rollout_settings(project_ids=[str(project_id)], genres=["悬疑"])
    project = SimpleNamespace(
        id=project_id,
        genre="悬疑",
        current_chapter_number=10,
        metadata_json={
            "story_engine_mode": "canonical",
            "story_engine_canary_validation": {
                "status": "passed",
                "evidence_source": "live",
                "artifact_id": str(canary_id),
            },
            "story_engine_reader_validation": {
                "status": "passed",
                "evidence_source": "live",
                "artifact_id": str(uuid4()),
            },
        },
    )
    manifest = {"cells": [{"engine_project_id": str(uuid4())}]}
    manifest_hash = canonical_json_hash(manifest)
    campaign = StoryEngineCanaryCampaignModel(
        id=canary_id,
        campaign_key="e1-other-project",
        experiment="E1",
        status="canary_validated",
        evidence_source="live",
        manifest_hash=manifest_hash,
        manifest_json=manifest,
        report_json={
            "manifest_hash": manifest_hash,
            "cohort": {"canary_ready": True},
        },
    )
    session = AsyncMock()
    session.get = AsyncMock(return_value=campaign)

    decision = await resolve_story_engine_rollout_decision_from_db(
        session,
        project,
        settings,
    )

    assert decision.effective_mode == "shadow"
    assert decision.authorized is False
    assert "STORY_ENGINE_LIVE_CANARY_EVIDENCE_REQUIRED" in decision.blocking_codes
    assert session.get.await_count == 1


def test_story_engine_rollout_lock_prevents_mid_chapter_mode_switch() -> None:
    project_id = uuid4()
    project = SimpleNamespace(
        id=project_id,
        genre="悬疑",
        current_chapter_number=3,
        metadata_json={
            "story_engine_mode": "canary",
            "story_engine_rollout_lock": {
                "chapter_number": 4,
                "mode": "dual_write",
            },
        },
    )

    decision = resolve_story_engine_rollout_decision(
        project,
        _rollout_settings(
            project_ids=[str(project_id)],
            genres=["悬疑"],
        ),
        chapter_number=4,
    )

    assert decision.effective_mode == "dual_write"
    assert decision.authorized is False
    assert "STORY_ENGINE_MODE_SWITCH_DURING_CHAPTER" in decision.blocking_codes


def test_story_engine_mode_keeps_shadow_default_compatible() -> None:
    settings = SimpleNamespace(
        pipeline=SimpleNamespace(story_engine_mode="shadow")
    )

    assert resolve_story_engine_mode(SimpleNamespace(metadata_json={}), settings) == "shadow"


@pytest.mark.asyncio
async def test_shadow_materialization_does_not_resolve_or_mutate_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolver = AsyncMock(side_effect=AssertionError("shadow must not resolve window"))
    monkeypatch.setattr(
        workflows,
        "resolve_latest_story_engine_window_artifact",
        resolver,
    )

    projections = await workflows._load_story_engine_materialization_projections(
        object(),
        project=SimpleNamespace(id=uuid4(), metadata_json={}),
        settings=SimpleNamespace(
            pipeline=SimpleNamespace(story_engine_mode="shadow")
        ),
        chapter_numbers={1},
    )

    assert projections == {}
    resolver.assert_not_awaited()


@pytest.mark.asyncio
async def test_dual_write_materialization_keeps_shadow_projection_out_of_writer_lane(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact = SimpleNamespace(
        id=uuid4(),
        content=build_story_engine_window_artifact_content(
            _window_payload(),
            maturity=StoryEngineMaturity.SHADOW_VALIDATED,
            can_drive_generation=False,
        ),
    )
    monkeypatch.setattr(
        workflows,
        "resolve_latest_story_engine_window_artifact",
        AsyncMock(return_value=artifact),
    )
    project = SimpleNamespace(
        id=uuid4(),
        metadata_json={"story_engine_mode": "dual_write"},
    )
    settings = SimpleNamespace(
        pipeline=SimpleNamespace(story_engine_mode="shadow")
    )

    active = await workflows._load_story_engine_materialization_projections(
        object(),
        project=project,
        settings=settings,
        chapter_numbers={1},
    )
    shadow = await workflows._load_story_engine_shadow_observation_projections(
        object(),
        project=project,
        settings=settings,
        chapter_numbers={1},
    )

    assert active == {}
    assert shadow[1]["can_drive_generation"] is False


@pytest.mark.asyncio
async def test_dual_write_generates_a_missing_shadow_window_from_real_outlines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine_artifact_id = uuid4()
    engine_artifact = SimpleNamespace(id=engine_artifact_id)
    generated = SimpleNamespace(id=uuid4())
    session = AsyncMock()
    session.get = AsyncMock(return_value=engine_artifact)
    monkeypatch.setattr(
        workflows,
        "resolve_latest_story_engine_window_artifact",
        AsyncMock(return_value=None),
    )
    generate = AsyncMock(return_value=generated)
    monkeypatch.setattr(
        workflows,
        "generate_story_engine_shadow_window",
        generate,
    )
    project = SimpleNamespace(
        id=uuid4(),
        metadata_json={
            "story_engine_mode": "dual_write",
            "story_engine_shadow": {"artifact_id": str(engine_artifact_id)},
        },
    )
    outlines = [SimpleNamespace(chapter_number=1)]

    result = await workflows._ensure_dual_write_story_engine_window(
        session,
        project=project,
        settings=SimpleNamespace(
            pipeline=SimpleNamespace(story_engine_mode="shadow")
        ),
        chapter_outlines=outlines,
        workflow_run_id=uuid4(),
    )

    assert result is generated
    generate.assert_awaited_once()
    assert generate.await_args.kwargs["engine_artifact"] is engine_artifact
    assert generate.await_args.kwargs["chapter_outlines"] is outlines


@pytest.mark.asyncio
async def test_canary_materialization_requires_every_requested_chapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id = uuid4()
    artifact = SimpleNamespace(id=uuid4(), content=build_story_engine_window_artifact_content(
        _window_payload(),
        maturity=StoryEngineMaturity.CANARY_VALIDATED,
        can_drive_generation=True,
    ))
    monkeypatch.setattr(
        workflows,
        "resolve_latest_story_engine_window_artifact",
        AsyncMock(return_value=artifact),
    )

    with pytest.raises(ValueError, match="missing chapters: 2"):
        await workflows._load_story_engine_materialization_projections(
            object(),
            project=SimpleNamespace(
                id=project_id,
                genre="悬疑",
                current_chapter_number=0,
                metadata_json={"story_engine_mode": "canary"},
            ),
            settings=_rollout_settings(
                project_ids=[str(project_id)],
                genres=["悬疑"],
            ),
            chapter_numbers={1, 2},
        )


@pytest.mark.asyncio
async def test_canary_materialization_returns_one_creative_core_per_chapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id = uuid4()
    artifact = SimpleNamespace(id=uuid4(), content=build_story_engine_window_artifact_content(
        _window_payload(),
        maturity=StoryEngineMaturity.CANARY_VALIDATED,
        can_drive_generation=True,
    ))
    monkeypatch.setattr(
        workflows,
        "resolve_latest_story_engine_window_artifact",
        AsyncMock(return_value=artifact),
    )

    projections = await workflows._load_story_engine_materialization_projections(
        object(),
        project=SimpleNamespace(
            id=project_id,
            genre="悬疑",
            current_chapter_number=0,
            metadata_json={"story_engine_mode": "canary"},
        ),
        settings=_rollout_settings(
            project_ids=[str(project_id)],
            genres=["悬疑"],
        ),
        chapter_numbers={1},
    )

    assert projections[1]["choice_id"] == "publish"
    assert projections[1]["can_drive_generation"] is True


def test_narrative_contract_metadata_preserves_story_engine_lineage() -> None:
    core = {
        "chapter_number": 1,
        "choice_id": "publish",
        "projection_hash": "projection-hash",
        "can_drive_generation": True,
    }
    chapter_metadata = _chapter_contract_metadata_from_chapter(
        SimpleNamespace(metadata_json={"story_engine_projection": core})
    )
    scene_metadata = _scene_contract_metadata_from_scene(
        SimpleNamespace(
            metadata_json={
                "story_engine_projection_ref": {
                    "chapter_number": 1,
                    "choice_id": "publish",
                    "projection_hash": "projection-hash",
                }
            }
        )
    )

    assert chapter_metadata["story_engine_projection"] == core
    assert scene_metadata["story_engine_projection_ref"]["choice_id"] == "publish"
