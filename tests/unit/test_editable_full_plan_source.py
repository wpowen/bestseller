"""Shared editable full/prefix reads with actual SQLite and physical admission proof."""
# ruff: noqa: F811 -- Imported pytest fixture names are injected as parameters.

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, update
from test_editable_plan_source import prefix_rig as prefix_rig
from test_editable_plan_source import rig as prefix_case  # noqa: F401
from test_editable_plan_source import selected
from test_fanqie_materialized_plan_resume import evidence
from test_full_outline_source_selection import install_markers
from test_full_outline_source_selection import integrated_modules as integrated_modules
from test_full_outline_source_selection import materialized_case as materialized_case
from test_full_outline_source_selection import plan_case as plan_case
from test_full_outline_source_selection import store as store

from bestseller.infra.db import models as db
from bestseller.services import materialized_outline_admission_read as admission
from bestseller.services import reviews
from bestseller.services.editable_plan_source import (
    AUTHORITY,
    INTERPRETATION,
    SCHEMA,
    build_editable_plan_source,
    load_selected_plan_source,
)
from bestseller.services.prompt_source_policy import content_hash
from bestseller.services.review_source_context import load_review_source_context

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.fixture
async def full_case(plan_case):
    case = plan_case
    case.decoy_id = await install_markers(case)
    return case


async def current(case, session):
    project = await session.get(db.ProjectModel, case.pid)
    chapter = await session.get(db.ChapterModel, case.original.chapters[0].id)
    scenes = list(await session.scalars(select(db.SceneCardModel).where(
        db.SceneCardModel.chapter_id == chapter.id,
    ).order_by(db.SceneCardModel.scene_number)))
    return project, chapter, scenes


@pytest.mark.parametrize("project_type", ["linear", "fanqie_short"])
async def test_full_exact_shared_selection_context_and_immutable_readback(full_case, project_type):
    case = full_case
    async with case.factory() as session:
        project, _chapter, _scenes = await current(case, session)
        project.project_type = project_type
        await session.commit()
    before = await evidence(case)
    async with case.factory() as session:
        project, chapter, scenes = await current(case, session)
        artifact, plan, receipt = await load_selected_plan_source(
            session, case.settings, project=project, chapter=chapter, scenes=scenes,
        )
        assert artifact.id == case.original.outline.id and artifact.id != case.decoy_id
        assert plan == artifact.content["chapters"][0]
        assert receipt["schema"] == admission.SCHEMA
        assert receipt["workflow_run_id"] == str(case.stable_id)
        assert receipt["source_plan_projection"] == chapter.metadata_json[
            "outline_source_admission"
        ]["projection_hash"]
        result = await build_editable_plan_source(
            session, project=project, chapter=chapter, scenes=list(reversed(scenes)),
            settings=case.settings,
        )
        assert result["schema"] == SCHEMA and result["authority"] == AUTHORITY
        assert result["interpretation"] == INTERPRETATION
        assert result["status"] == "available" and result["plan"] == plan
        assert result["binding"]["readiness_receipt_sha256"] == content_hash(receipt)
        assert result["binding"]["artifact_content_sha256"] == content_hash(artifact.content)
        source = await load_review_source_context(
            session, project=project, chapter=chapter, scenes=scenes,
            context_packet=SimpleNamespace(), content_md="The observer compares the winter signal.",
            settings=case.settings,
        )
        assert source["editable_plan"] == result
        assert source["adopted_continuity"]["recent_adopted_prose"] == []
        assert "not proof of author authorization" in source["editable_plan"]["interpretation"]
        plan["chapter_goal"] = "Mutated returned intent"
        receipt["source_admission"].clear()
        again = await load_selected_plan_source(
            session, case.settings, project=project, chapter=chapter, scenes=scenes,
        )
        assert again[1] == result["plan"] and again[2]["source_admission"]
        assert not session.dirty and not session.new
    assert await evidence(case) == before
    assert len(case.sent) == 1  # Fixture-only offline transport; all later model boundaries deny.


@pytest.mark.parametrize("fault", [
    "missing_proof", "paid_model", "paid_owner", "marker_owner", "source_hash",
    "foreign_artifact", "unapproved_artifact", "goal", "scene_exit", "duplicate_chapter",
])
async def test_full_strong_admission_tamper_cannot_become_unavailable_or_latest(full_case, fault):
    case = full_case
    async with case.factory() as session:
        _project, chapter, scenes = await current(case, session)
        artifact = await session.get(db.PlanningArtifactVersionModel, case.original.outline.id)
        owner = await session.get(db.WorkflowRunModel, case.stable_id)
        metadata = deepcopy(chapter.metadata_json)
        marker = metadata["outline_source_admission"]
        if fault == "missing_proof":
            owner.metadata_json = {**owner.metadata_json, "source_admission": None}
        elif fault in {"paid_model", "paid_owner"}:
            run = await session.get(db.LlmRunModel, UUID(owner.metadata_json[
                "source_admission"
            ]["llm_run_id"]))
            if fault == "paid_model":
                run.model_name = "changed-after-proof"
            else:
                run.workflow_run_id = uuid4()
        elif fault == "marker_owner":
            marker["workflow_run_id"] = str(uuid4())
        elif fault == "source_hash":
            marker["source_content_sha256"] = "0" * 64
        elif fault == "foreign_artifact":
            artifact.project_id = uuid4()
        elif fault == "unapproved_artifact":
            artifact.status = "candidate"
        elif fault == "goal":
            chapter.chapter_goal = "An unselected replacement goal"
        elif fault == "scene_exit":
            scenes[0].exit_state = {"summary": "A different outcome"}
        else:
            content = deepcopy(artifact.content)
            content["chapters"].append(deepcopy(content["chapters"][0]))
            artifact.content = content
        chapter.metadata_json = metadata
        await session.commit()
    before = await evidence(case)
    async with case.factory() as session:
        project, chapter, scenes = await current(case, session)
        with pytest.raises(ValueError, match="outline_source_admission_required"):
            await load_selected_plan_source(
                session, case.settings, project=project, chapter=chapter, scenes=scenes,
            )
        assert not session.dirty and not session.new
    assert await evidence(case) == before
    assert len(case.sent) == 1


@pytest.mark.parametrize("matching", [True, False])
async def test_both_markers_consistent_or_rejected_without_skipping_full_guard(
    full_case, monkeypatch, matching
):
    case = full_case
    async with case.factory() as session:
        _project, chapter, _scenes = await current(case, session)
        metadata = deepcopy(chapter.metadata_json)
        marker = metadata["outline_source_admission"]
        prefix = {key: marker[key] for key in ("artifact_id", "workflow_run_id", "projection_hash")}
        if not matching:
            prefix["artifact_id"] = str(case.decoy_id)
        chapter.metadata_json = {**metadata, "outline_prefix_readiness": prefix}
        await session.commit()
    calls = []
    original = admission.require_materialized_outline_source_admission

    async def checked(session, settings, **kwargs):
        assert settings is case.settings
        calls.append(kwargs["artifact"].id)
        return await original(session, settings, **kwargs)

    monkeypatch.setattr(admission, "require_materialized_outline_source_admission", checked)
    async with case.factory() as session:
        project, chapter, scenes = await current(case, session)
        if matching:
            result = await load_selected_plan_source(
                session, case.settings, project=project, chapter=chapter, scenes=scenes,
            )
            assert result[2]["schema"] == admission.SCHEMA
            assert calls == [case.original.outline.id]
        else:
            with pytest.raises(ValueError, match="editable_plan_selection_ambiguous"):
                await load_selected_plan_source(
                    session, case.settings, project=project, chapter=chapter, scenes=scenes,
                )
            assert calls == []


@pytest.mark.parametrize("receipt", [None, {"schema": "chapter-outline-prefix-readiness.v1"}])
async def test_full_marker_never_accepts_empty_or_prefix_guard_receipt(
    full_case, monkeypatch, receipt
):
    case = full_case

    async def downgraded(*_args, **_kwargs):
        return deepcopy(receipt)

    monkeypatch.setattr(admission, "require_materialized_outline_source_admission", downgraded)
    async with case.factory() as session:
        project, chapter, scenes = await current(case, session)
        with pytest.raises(ValueError, match="editable_plan_full_admission_required"):
            await load_selected_plan_source(
                session, case.settings, project=project, chapter=chapter, scenes=scenes,
            )


async def test_full_stale_cached_marker_cannot_flush_or_rebind(full_case):
    case = full_case
    async with case.factory() as session:
        project, chapter, scenes = await current(case, session)
        chapter.metadata_json = {
            key: value for key, value in chapter.metadata_json.items()
            if key != "outline_source_admission"
        }
        with pytest.raises(ValueError, match="current_marker_changed"):
            await load_selected_plan_source(
                session, case.settings, project=project, chapter=chapter, scenes=scenes,
            )
        assert chapter in session.dirty
        await session.rollback()


async def test_changed_persisted_marker_cannot_refresh_cached_caller(full_case):
    case = full_case
    async with case.factory() as session:
        project, chapter, scenes = await current(case, session)
        cached = deepcopy(chapter.metadata_json)
        changed = deepcopy(cached)
        changed["outline_source_admission"]["workflow_run_id"] = str(uuid4())
        await session.execute(update(db.ChapterModel).where(db.ChapterModel.id == chapter.id)
            .values(metadata_json=changed).execution_options(synchronize_session=False))
        await session.commit()
        with pytest.raises(ValueError, match="current_marker_changed"):
            await load_selected_plan_source(
                session, case.settings, project=project, chapter=chapter, scenes=scenes,
            )
        assert chapter.metadata_json == cached


async def test_cached_prefix_cannot_hide_persisted_full_selection(full_case):
    case = full_case
    async with case.factory() as session:
        project, chapter, scenes = await current(case, session)
        marker = chapter.metadata_json["outline_source_admission"]
        chapter.metadata_json = {"outline_prefix_readiness": {
            key: marker[key] for key in ("artifact_id", "workflow_run_id", "projection_hash")
        }}
        with pytest.raises(ValueError, match="current_marker_changed"):
            await load_selected_plan_source(
                session, case.settings, project=project, chapter=chapter, scenes=scenes,
            )
        assert chapter in session.dirty
        await session.rollback()


@pytest.mark.parametrize("marker", [None, {}, "approved", {"artifact_id": "not-a-uuid"}])
async def test_malformed_full_selection_fails_without_database_or_model(marker):
    project = SimpleNamespace(id=uuid4())
    chapter = SimpleNamespace(id=uuid4(), project_id=project.id,
        metadata_json={"outline_source_admission": marker})
    with pytest.raises(ValueError, match="editable_plan_selection_invalid"):
        await load_selected_plan_source(None, project=project, chapter=chapter, scenes=[])


async def test_full_requires_actual_settings_before_database_or_model():
    project = SimpleNamespace(id=uuid4())
    chapter = SimpleNamespace(id=uuid4(), project_id=project.id, metadata_json={
        "outline_source_admission": {
            "artifact_id": str(uuid4()), "workflow_run_id": str(uuid4()),
            "projection_hash": "a" * 64, "source_content_sha256": "b" * 64,
        },
    })
    with pytest.raises(ValueError, match="editable_plan_full_settings_required"):
        await load_selected_plan_source(None, project=project, chapter=chapter, scenes=[])


@pytest.mark.parametrize("conflicting_prefix", [False, True])
async def test_actual_full_review_entry_validates_before_context_duplication_or_provider(
    full_case, monkeypatch, conflicting_prefix
):
    case = full_case
    async with case.factory() as session:
        project, chapter, scenes = await current(case, session)
        if conflicting_prefix:
            chapter.metadata_json = {**chapter.metadata_json, "outline_prefix_readiness": {}}
            await session.commit()
        draft = await session.get(db.ChapterDraftVersionModel, case.draft_id)
        monkeypatch.setattr(reviews, "_load_chapter_context", AsyncMock(
            return_value=(project, chapter, None, scenes, draft)
        ))
        reached = []

        class AfterAdmission(BaseException):
            pass

        async def context(*_args, **_kwargs):
            reached.append("context")
            raise AfterAdmission

        async def forbidden(*_args, **_kwargs):
            raise AssertionError("Duplication/provider reached before source admission fence")

        monkeypatch.setattr(reviews, "build_chapter_writer_context", context)
        monkeypatch.setattr(reviews, "_compute_chapter_duplication_signal", forbidden)
        monkeypatch.setattr(reviews, "complete_text", forbidden)
        if conflicting_prefix:
            with pytest.raises(ValueError, match="editable_plan_selection_ambiguous"):
                await reviews.review_chapter_draft(
                    session, case.settings, project.slug, chapter.chapter_number,
                )
            assert reached == []
        else:
            with pytest.raises(AfterAdmission):
                await reviews.review_chapter_draft(
                    session, case.settings, project.slug, chapter.chapter_number,
                )
            assert reached == ["context"]
    assert len(case.sent) == 1


async def test_prefix_shared_loader_keeps_existing_serialized_binding_exact(prefix_case):
    artifact, chapter, scenes = await selected(prefix_case)
    _artifact, plan, receipt = await load_selected_plan_source(
        prefix_case.store, project=prefix_case.project, chapter=chapter, scenes=scenes,
    )
    result = await build_editable_plan_source(
        prefix_case.store, project=prefix_case.project, chapter=chapter, scenes=scenes,
    )
    assert result == {
        "schema": SCHEMA, "authority": AUTHORITY, "interpretation": INTERPRETATION,
        "status": "available", "binding": {
            "project_id": str(prefix_case.project.id), "chapter_id": str(chapter.id),
            "chapter_number": chapter.chapter_number, "artifact_id": str(artifact.id),
            "artifact_version": artifact.version_no, "workflow_run_id": str(artifact.source_run_id),
            "artifact_content_sha256": content_hash(artifact.content),
            "readiness_receipt_sha256": content_hash(receipt),
            "projection_sha256": chapter.metadata_json[
                "outline_prefix_readiness"
            ]["projection_hash"],
            "plan_sha256": content_hash(plan),
            "scenes": [{"scene_id": str(scene.id), "scene_number": scene.scene_number}
                for scene in scenes],
        }, "plan": plan,
    }
