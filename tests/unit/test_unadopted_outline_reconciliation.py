"""Explicit outline migration with real SQLite rows and untouched draft versions.

The upstream prefix verifier is a scoped boundary stub here; projection validation,
formal adoption selection, activity guards, history and persistence use real code.
"""

from __future__ import annotations

import copy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.schema import CreateTable
from test_adopted_knowledge_lineage import db as db
from test_adopted_knowledge_lineage import make_accepted_debt
from test_workflow_approved_outline_source_guard import _batch

from bestseller.infra.db.models import (
    ChapterDraftVersionModel,
    ChapterModel,
    PlanningArtifactVersionModel,
    SceneCardModel,
    SceneDraftVersionModel,
    VolumeModel,
    WorkflowRunModel,
    WorkflowStepRunModel,
)
from bestseller.services import chapter_outline_readiness as readiness
from bestseller.services import chapter_scene_contract_materializer, llm
from bestseller.services import workflows as service
from bestseller.services.prompt_source_policy import content_hash
from bestseller.settings import load_settings

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


def _raw(row):
    return {a.key: copy.deepcopy(getattr(row, a.key)) for a in inspect(type(row)).column_attrs}


@pytest.fixture
async def migration(db, monkeypatch):
    session, project, prior, _prior_draft = db
    conn = await session.connection()
    for model in (
        VolumeModel,
        SceneCardModel,
        SceneDraftVersionModel,
        PlanningArtifactVersionModel,
        WorkflowRunModel,
        WorkflowStepRunModel,
    ):
        ddl = str(CreateTable(model.__table__, if_not_exists=True).compile(dialect=conn.dialect))
        await conn.exec_driver_sql(ddl.replace("::jsonb", ""))
    project.metadata_json = {
        "identity_manifest_status": "locked",
        "identity_manifest": [
            {"name": "Mara", "role": "protagonist", "gender": "female"},
            {"name": "Steward", "role": "supporting", "gender": "unknown"},
        ],
    }
    batch = _batch()
    outline = batch.chapters[0]
    outline.chapter_number = 2
    outline.title = None
    outline.target_word_count = 2371
    outline.target_emotion = None
    outline.key_reveals = []
    outline.chapter_information_introduced = []
    second = outline.scenes[0].model_copy(deep=True)
    second.scene_number = 2
    outline.scenes.append(second)
    for s, words in zip(outline.scenes, (1237, 1134), strict=True):
        s.hook_requirement = None
        s.target_word_count = words
        s.key_dialogue_beats = [f"Compare dated receipt {n}" for n in range(20)]
    chapter = ChapterModel(
        project_id=project.id,
        chapter_number=2,
        title="Old title",
        chapter_goal="Old goal",
        status="revision",
        production_state="blocked",
        primary_emotion="obsolete emotion",
        information_revealed=["obsolete reveal"],
        target_word_count=1234,
        metadata_json={
            "outline_prefix_readiness": {"artifact_id": str(uuid4()), "projection_hash": "old"},
            "unrelated": "retain",
        },
    )
    session.add(chapter)
    await session.flush()
    scenes = [
        SceneCardModel(
            project_id=project.id,
            chapter_id=chapter.id,
            scene_number=n,
            scene_type="setup",
            status="drafted",
            target_word_count=777,
            hook_requirement="Old cached hook",
            purpose={"story": f"Old scene {n}"},
            metadata_json={"unrelated": n},
        )
        for n in (1, 2)
    ]
    session.add_all(scenes)
    await session.flush()
    old = datetime.now(UTC) - timedelta(days=1)
    drafts = [
        ChapterDraftVersionModel(
            project_id=project.id,
            chapter_id=chapter.id,
            version_no=n,
            content_md=f"Retained body {n}",
            is_current=n == 16,
            promotion_state="under_review" if n == 16 else "rejected",
            created_at=old,
        )
        for n in range(1, 17)
    ]
    scene_drafts = [
        SceneDraftVersionModel(
            project_id=project.id,
            scene_card_id=s.id,
            version_no=n,
            content_md=f"Retained scene {s.scene_number}/{n}",
            is_current=n == 2,
            promotion_state="candidate",
            created_at=old,
        )
        for s in scenes
        for n in (1, 2)
    ]
    source = PlanningArtifactVersionModel(
        project_id=project.id,
        artifact_type="chapter_outline_batch",
        version_no=1,
        status="approved",
        schema_version="1.0",
        content={
            **batch.model_dump(mode="json"),
            "_meta": {readiness.RECEIPT_KEY: {"proof": "fixture"}},
        },
    )
    session.add_all([*drafts, *scene_drafts, source])
    await session.commit()
    for row in [chapter, *scenes, *drafts, *scene_drafts, prior]:
        await session.refresh(row)
    proof = {"workflow_run_id": str(uuid4()), "chapter_numbers": [2], "current": True}
    proof_calls = []

    async def verify(session_arg, *, project: object, artifact_id, **kwargs):
        assert session_arg is session and project.id == source.project_id
        assert artifact_id == source.id
        proof_calls.append(artifact_id)
        return copy.deepcopy(proof)

    async def no_model(*args, **kwargs):
        raise AssertionError("Outline migration must not call a model")

    settings = load_settings()
    settings.pipeline.enable_outline_llm_commercial_judge = False
    settings.pipeline.enable_outline_reader_experience_judge = False
    monkeypatch.setattr(service, "load_settings", lambda: settings)
    monkeypatch.setattr(readiness, "verify_outline_prefix_readiness", verify)
    monkeypatch.setattr(llm, "complete_text", no_model)
    return SimpleNamespace(
        session=session,
        project=project,
        prior=prior,
        batch=batch,
        chapter=chapter,
        scenes=scenes,
        source=source,
        drafts=drafts,
        scene_drafts=scene_drafts,
        proof=proof,
        proof_calls=proof_calls,
    )


async def _run(rig, **kwargs):
    return await service.materialize_chapter_outline_batch(
        rig.session,
        rig.project.slug,
        rig.batch,
        source_artifact_id=rig.source.id,
        requested_by="authorized-fixture-operator",
        reconcile_unadopted_chapter_numbers=[2],
        **kwargs,
    )


@pytest.mark.parametrize("hook", [None, ""])
async def test_reconcile_preserves_all_drafts_exact_source_and_raw_history(migration, hook):
    rig = migration
    for s in rig.batch.chapters[0].scenes:
        s.hook_requirement = hook
    rig.source.content = {**rig.source.content, **rig.batch.model_dump(mode="json")}
    await rig.session.flush()
    before_drafts = [_raw(d) for d in [*rig.drafts, *rig.scene_drafts]]
    before_prior = _raw(rig.prior)
    old_projection = readiness.outline_prefix_projection_hash(rig.chapter, rig.scenes)
    before_source, before_batch = (
        copy.deepcopy(rig.source.content),
        rig.batch.model_dump(mode="json"),
    )
    result = await _run(rig)
    await rig.session.commit()
    for row in [rig.chapter, *rig.scenes, *rig.drafts, *rig.scene_drafts, rig.prior]:
        await rig.session.refresh(row)
    assert [_raw(d) for d in [*rig.drafts, *rig.scene_drafts]] == before_drafts
    assert _raw(rig.prior) == before_prior
    assert rig.source.content == before_source and rig.batch.model_dump(mode="json") == before_batch
    assert [s.hook_requirement for s in rig.scenes] == [hook, hook]
    assert rig.chapter.title is None and rig.chapter.primary_emotion is None
    assert rig.chapter.information_revealed == []
    assert rig.chapter.status == "revision" and rig.chapter.production_state == "blocked"
    assert rig.chapter.metadata_json["unrelated"] == "retain"
    readiness._verify_materialized_content(rig.chapter, rig.scenes, before_source["chapters"][0])
    workflow = await rig.session.get(WorkflowRunModel, result.workflow_run_id)
    history = workflow.metadata_json["unadopted_outline_reconciliation_history"]
    assert "previous_raw" not in rig.chapter.metadata_json["outline_materialization_history"][0]
    assert len(history) == 1 and history[0]["previous_projection_hash"] == old_projection
    assert history[0]["previous_raw_hash"] == content_hash(history[0]["previous_raw"])
    assert history[0]["previous_raw"]["chapter"]["chapter_goal"] == "Old goal"
    assert [s["hook_requirement"] for s in history[0]["previous_raw"]["scenes"]] == [
        "Old cached hook"
    ] * 2
    assert history[0]["source_artifact_id"] == str(rig.source.id)
    assert history[0]["source_content_hash"] == content_hash(before_source)
    assert history[0]["prefix_receipt"] == rig.proof
    marker = rig.chapter.metadata_json["outline_prefix_readiness"]
    assert marker["artifact_id"] == str(rig.source.id)
    assert marker["projection_hash"] == readiness.outline_prefix_projection_hash(
        rig.chapter, rig.scenes
    )
    assert workflow.metadata_json["reconciled_unadopted_chapters"] == [2]
    assert workflow.metadata_json["scene_capacity_diagnostic_only"] is True
    assert workflow.metadata_json["scene_capacity_warnings"]
    await _run(rig)
    await rig.session.commit()
    assert len(rig.chapter.metadata_json["outline_materialization_history"]) == 1


@pytest.mark.parametrize("adoption", ["promoted", "numeric", "ambiguous"])
async def test_formal_adoption_and_ambiguity_block_before_materialization(migration, adoption):
    rig = migration
    if adoption == "promoted":
        # A formal older source remains adopted while a candidate is current.
        rig.drafts[0].promotion_state = "promoted"
    elif adoption == "numeric":
        await make_accepted_debt((rig.session, rig.project, rig.chapter, rig.drafts[-1]))
    else:
        rig.chapter.metadata_json = {**rig.chapter.metadata_json, "numeric_promotion_debt": {}}
    await rig.session.commit()
    for row in [rig.chapter, *rig.drafts]:
        await rig.session.refresh(row)
    before = [_raw(row) for row in [rig.chapter, *rig.scenes, *rig.drafts, *rig.scene_drafts]]
    with pytest.raises(ValueError, match=r"adopted_or_ambiguous|adoption_ambiguous"):
        await _run(rig)
    assert [
        _raw(row) for row in [rig.chapter, *rig.scenes, *rig.drafts, *rig.scene_drafts]
    ] == before
    assert not list(await rig.session.scalars(select(WorkflowRunModel)))


@pytest.mark.parametrize(
    "activity",
    ["chapter_draft", "scene_draft", "chapter_workflow", "scene_workflow", "project_workflow"],
)
async def test_recent_or_active_writing_blocks_before_changes(migration, activity):
    rig = migration
    if activity == "chapter_draft":
        rig.drafts[-1].created_at = datetime.now(UTC)
    elif activity == "scene_draft":
        rig.scene_drafts[-1].created_at = datetime.now(UTC)
    else:
        scope, identity, kind = {
            "chapter_workflow": ("chapter", rig.chapter.id, "chapter_pipeline"),
            "scene_workflow": ("scene_card", rig.scenes[0].id, "scene_pipeline"),
            "project_workflow": ("project", rig.project.id, "project_repair"),
        }[activity]
        rig.session.add(
            WorkflowRunModel(
                project_id=rig.project.id,
                workflow_type=kind,
                scope_type=scope,
                scope_id=identity,
                status="running",
                metadata_json={},
            )
        )
    await rig.session.commit()
    before = [_raw(r) for r in [rig.chapter, *rig.scenes, *rig.drafts, *rig.scene_drafts]]
    with pytest.raises(ValueError, match="active_writing"):
        await _run(rig)
    assert [_raw(r) for r in [rig.chapter, *rig.scenes, *rig.drafts, *rig.scene_drafts]] == before


@pytest.mark.parametrize(
    "fault",
    [
        "missing_receipt",
        "stale_proof",
        "wrong_source_project",
        "unapproved",
        "range",
        "prune",
        "force",
        "scene_retirement",
    ],
)
async def test_source_scope_proof_and_non_destructive_guards(migration, monkeypatch, fault):
    rig = migration
    kwargs = {}
    if fault == "missing_receipt":
        rig.source.content = rig.batch.model_dump(mode="json")
    elif fault == "stale_proof":

        async def stale(*args, **kwargs):
            raise ValueError("prefix_foundation_stale")

        monkeypatch.setattr(readiness, "verify_outline_prefix_readiness", stale)
    elif fault == "wrong_source_project":
        rig.source.project_id = uuid4()
    elif fault == "unapproved":
        rig.source.status = "needs_review"
    elif fault == "range":
        kwargs["reconcile_unadopted_chapter_numbers"] = [1]
    elif fault == "prune":
        kwargs["prune_missing_planned"] = True
    elif fault == "force":
        kwargs["force_chapter_numbers"] = [2]
    else:
        rig.batch.chapters[0].scenes.pop()
        rig.source.content = {**rig.source.content, **rig.batch.model_dump(mode="json")}
    await rig.session.commit()
    before = [_raw(r) for r in [rig.chapter, *rig.scenes, *rig.drafts, *rig.scene_drafts]]
    args = {
        "source_artifact_id": rig.source.id,
        "reconcile_unadopted_chapter_numbers": [2],
        **kwargs,
    }
    with pytest.raises(ValueError):
        await service.materialize_chapter_outline_batch(
            rig.session, rig.project.slug, rig.batch, **args
        )
    assert [_raw(r) for r in [rig.chapter, *rig.scenes, *rig.drafts, *rig.scene_drafts]] == before
    assert not list(await rig.session.scalars(select(WorkflowRunModel)))


async def test_non_migration_default_still_skips_unadopted_revision(migration):
    rig = migration
    before = [_raw(r) for r in [rig.chapter, *rig.scenes, *rig.drafts, *rig.scene_drafts]]
    await service.materialize_chapter_outline_batch(
        rig.session, rig.project.slug, rig.batch, source_artifact_id=rig.source.id
    )
    assert [_raw(r) for r in [rig.chapter, *rig.scenes, *rig.drafts, *rig.scene_drafts]] == before


async def test_non_boolean_adoption_result_fails_closed(migration, monkeypatch):
    async def unknown(*args, **kwargs):
        return None

    monkeypatch.setattr(
        chapter_scene_contract_materializer, "chapter_contract_has_adopted_source", unknown
    )
    with pytest.raises(ValueError, match="adopted_or_ambiguous"):
        await _run(migration)


async def test_explicit_reconciliation_changes_only_selected_chapters(migration):
    rig = migration
    other = ChapterModel(
        project_id=rig.project.id,
        chapter_number=3,
        chapter_goal="Keep untouched",
        status="planned",
        metadata_json={"manual": "keep"},
    )
    rig.session.add(other)
    outline = rig.batch.chapters[0].model_copy(deep=True)
    outline.chapter_number = 3
    rig.batch.chapters.append(outline)
    rig.source.content = {**rig.source.content, **rig.batch.model_dump(mode="json")}
    rig.proof["chapter_numbers"] = [2, 3]
    await rig.session.commit()
    await rig.session.refresh(other)
    before = _raw(other)
    await _run(rig)
    await rig.session.commit()
    await rig.session.refresh(other)
    assert _raw(other) == before


async def test_projection_failure_rolls_back_without_modifying_retained_drafts(
    migration, monkeypatch
):
    rig = migration
    before = [_raw(row) for row in [rig.chapter, *rig.scenes, *rig.drafts, *rig.scene_drafts]]
    original = service._sync_existing_scene_from_outline

    def corrupt(scene, outline, **kwargs):
        changed = original(scene, outline, **kwargs)
        scene.purpose = {"story": "Unapproved accidental post-processing"}
        return changed

    monkeypatch.setattr(service, "_sync_existing_scene_from_outline", corrupt)
    with pytest.raises(ValueError, match="prefix_materialized_scene_changed"):
        await _run(rig)
    await rig.session.rollback()
    for row in [rig.chapter, *rig.scenes, *rig.drafts, *rig.scene_drafts]:
        await rig.session.refresh(row)
    assert [
        _raw(row) for row in [rig.chapter, *rig.scenes, *rig.drafts, *rig.scene_drafts]
    ] == before
