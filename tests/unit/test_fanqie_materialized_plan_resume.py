"""Actual SQLite source selection and actual hard-receipt verifier, offline wire."""

import asyncio
from copy import deepcopy
import json
from pathlib import Path
import socket
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import JSON, ForeignKeyConstraint, MetaData, select
from sqlalchemy.dialects.postgresql import JSONB
from test_execution_budget_core import gateway_settings
from test_foundation_selection import store as store
from test_materialized_outline_source_recovery import case as materialized_case  # noqa: F401
from test_planning_draft_admission import grade_payload

from bestseller.domain.enums import ProjectType
from bestseller.domain.project import ProjectCreate
from bestseller.infra.db import models as db
from bestseller.services import foundation_selection, llm, planner, planning_draft_admission
from bestseller.services.budget import bind_execution_scope
from bestseller.services.materialized_outline_source_recovery import _read_sources
from bestseller.services.prompt_source_policy import content_hash

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]
REAL_ASSESS = planning_draft_admission.assess_planning_draft_admission


@pytest.fixture
def integrated_modules():
    from bestseller.services import (
        fanqie_short_finalizer,
        fanqie_short_pipeline,
        fanqie_short_source_export,
    )

    return fanqie_short_source_export, fanqie_short_pipeline, fanqie_short_finalizer


@pytest.fixture
async def plan_case(integrated_modules, materialized_case, tmp_path, monkeypatch):  # noqa: F811
    c = materialized_case
    from bestseller.services import fanqie_short_plan_resume as helper

    engine = c.factory.kw["bind"]
    assert engine.dialect.name == "sqlite"
    for model in (db.CanonFactModel, db.TimelineEventModel, db.ChapterDraftVersionModel):
        table = model.__table__.to_metadata(MetaData())
        for col in table.c:
            if isinstance(col.type, JSONB):
                col.type = JSON()
            if col.server_default is not None and "::" in str(col.server_default.arg):
                col.server_default = None
        for constraint in list(table.constraints):
            if isinstance(constraint, ForeignKeyConstraint):
                table.constraints.remove(constraint)
        async with engine.begin() as connection:
            await connection.run_sync(table.create)
    settings = gateway_settings(calls=100)
    settings.llm.critic = settings.llm.planner.model_copy(
        update={
            "model": "openai/MiniMax-M3",
            "api_base": "https://api.minimaxi.com/v1",
        }
    )
    settings.output.base_dir = str(tmp_path / "output")
    settings.artifact_store.mode = "local"
    settings.artifact_store.local_dir = str(tmp_path / "physical")
    settings.artifact_store.retain_prompt_payloads = True
    sent = []

    async def transport(request, role):
        sent.append(request)
        grade = grade_payload(c.outline.content)
        calls = [
            {
                "id": "offline-submission",
                "type": "function",
                "function": {
                    "name": "submit_planning_admission",
                    "arguments": json.dumps(grade),
                },
            }
        ]
        return (
            "Retained offline structured submission.",
            10,
            5,
            "tool_calls",
            calls,
            {
                "role": "assistant",
                "content": "Retained offline structured submission.",
                "tool_calls": calls,
            },
        )

    async def no_override(*args):
        return None

    monkeypatch.setattr(llm, "_call_litellm", transport)
    monkeypatch.setattr(llm, "_resolve_project_model_override", no_override)
    async with c.factory() as session:
        project = await session.get(db.ProjectModel, c.project.id)
        project.project_type = "fanqie_short"
        project.target_chapters = 4
        project.target_word_count = 8000
        project.metadata_json = {
            **project.metadata_json,
            "pov": "first_person",
            "content_mode": "fanqie_short_story",
            "segment_count": 4,
            "unlock_line_ratio": 0.3,
        }
        volume = await session.get(db.PlanningArtifactVersionModel, c.assets["volume_plan"].id)
        detached = SimpleNamespace(
            **{
                key: deepcopy(getattr(project, key))
                for key in (
                    "id",
                    "target_chapters",
                    "target_word_count",
                    "metadata_json",
                    "language",
                )
            }
        )
        detached._source_bound_planning_canonical = {
            "id": str(c.canonical.id),
            "content_hash": content_hash(c.canonical.content),
            "content": deepcopy(c.canonical.content),
        }
        projection = planner._compile_source_bound_volume_plan(
            detached,
            project.metadata_json["premise"],
            *(c.assets[kind].content for kind in foundation_selection.KINDS[:3]),
        )
        volume.content = [
            {
                "volume_number": 1,
                "_meta": {
                    "source_projection_sha256": planner._approved_payload_hash(projection),
                },
            }
        ]
        await session.commit()
        await foundation_selection.pin_completed_foundation(
            session,
            project=project,
            workflow_id=UUID(project.metadata_json[foundation_selection.POINTER]["workflow_id"]),
            settings=settings,
        )
        await session.commit()
        _fresh, outline, candidate, sources, binding = await _read_sources(
            session,
            settings,
            project_slug=project.slug,
            artifact_id=c.outline.id,
            materialization_workflow_id=c.owner.id,
        )
        from bestseller.services.materialized_outline_admission_read import admission_workflow_id

        stable_id = admission_workflow_id(project.id, outline.id)
        workflow = db.WorkflowRunModel(
            id=stable_id,
            project_id=project.id,
            workflow_type="materialized_outline_source_admission",
            scope_type="planning_artifact",
            scope_id=outline.id,
            status="running",
            metadata_json={
                "schema": "materialized-outline-source-admission.v1",
                **binding,
            },
        )
        session.add(workflow)
        await session.commit()
        with bind_execution_scope(c.scope):
            receipt = await REAL_ASSESS(
                session,
                settings,
                project=project,
                workflow_run_id=stable_id,
                candidate_payload=candidate,
                book_spec=sources["book_spec"].content,
                world_spec=sources["world_spec"].content,
                cast_spec=sources["cast_spec"].content,
                commercial_report={"historical": deepcopy(c.reports)},
            )
        workflow.status = "completed"
        workflow.metadata_json = {
            **workflow.metadata_json,
            "source_admission": receipt,
            "grade_granted_to_old_editorial_reports": False,
        }
        draft = db.ChapterDraftVersionModel(
            project_id=project.id,
            chapter_id=c.chapters[0].id,
            version_no=1,
            content_md="The original observer records a faint winter signal.",
            is_current=True,
            promotion_state="candidate",
            promotion_metadata={"old_grade": {"passed": False, "raw": None}},
        )
        session.add(draft)
        await session.commit()
        pid, draft_id = project.id, draft.id
    assert len(sent) == 1

    async def deny(*args, **kwargs):
        raise AssertionError("resume must not dispatch any planner/Beat/materializer/provider")

    def deny_sync(*args, **kwargs):
        raise AssertionError("resume must not rebuild/import a plan")

    monkeypatch.setattr(llm, "_call_litellm", deny)
    monkeypatch.setattr(llm, "complete_text", deny)
    pipeline = integrated_modules[1]
    for name in (
        "generate_foundation_plan",
        "derive_ideology_kernel",
        "generate_fanqie_beat_sheet",
        "persist_fanqie_chapter_outline",
        "materialize_latest_story_bible",
        "materialize_latest_chapter_outline_batch",
        "materialize_latest_narrative_graph",
        "_ensure_project_invariants",
    ):
        monkeypatch.setattr(pipeline, name, deny)
    monkeypatch.setattr(pipeline, "build_fanqie_segment_outline_batch", deny_sync)
    monkeypatch.setattr(pipeline, "_write_fanqie_source_artifacts", deny_sync)
    monkeypatch.setattr(socket.socket, "connect", deny_sync)
    monkeypatch.setattr(socket, "create_connection", deny_sync)
    return SimpleNamespace(
        original=c,
        helper=helper,
        pipeline=pipeline,
        settings=settings,
        pid=pid,
        factory=c.factory,
        stable_id=stable_id,
        draft_id=draft_id,
        sent=sent,
    )


async def read(case, session):
    project = await session.get(db.ProjectModel, case.pid)
    return await case.helper.require_or_read_existing_complete_fanqie_plan(
        session,
        case.settings,
        project=project,
    )


async def evidence(case):
    async with case.factory() as session:
        rows = {}
        for model in (
            db.PlanningArtifactVersionModel,
            db.WorkflowRunModel,
            db.ChapterModel,
            db.SceneCardModel,
            db.ChapterDraftVersionModel,
            db.ExecutionBudgetModel,
            db.ExecutionOperationModel,
            db.ExecutionAttemptModel,
            db.LlmRunModel,
        ):
            rows[model.__tablename__] = deepcopy(
                list((await session.execute(select(*model.__table__.c))).mappings())
            )
    physical = {
        str(p): p.read_bytes()
        for p in Path(case.settings.artifact_store.local_dir).rglob("*")
        if p.is_file()
    }
    return rows, physical


async def test_real_complete_source_retries_keep_original_ids_and_paid_evidence(plan_case):
    c = plan_case
    before = await evidence(c)
    async with c.factory() as session:
        first = await read(c, session)
    async with c.factory() as session:
        second = await read(c, session)
    assert first == second
    assert first["planning_workflow_run_id"] == UUID(
        c.original.project.metadata_json[foundation_selection.POINTER]["workflow_id"]
    )
    assert first["outline_workflow_run_id"] == c.original.owner.id
    assert first["hard_admission_workflow_run_id"] == c.stable_id
    assert first["chapters_created"] == first["scenes_created"] == 0
    assert first["planning_reused"] is first["materialization_reused"] is True
    assert first["story_bible_workflow_run_id"] is first["narrative_graph_workflow_run_id"] is None
    assert len(c.sent) == 1 and await evidence(c) == before


@pytest.mark.parametrize(
    "fault",
    [
        "missing_admission",
        "admission_running",
        "missing_receipt",
        "rejected_receipt",
        "paid_model",
        "paid_root",
        "chapter",
        "scene",
        "projection_binding",
        "world_meta",
        "volume",
        "canonical",
        "missing_chapter",
        "extra_chapter",
        "orphan_scene",
        "materializer_cancelled",
        "second_complete_outline",
        "foundation_pin_missing",
    ],
)
async def test_missing_incomplete_or_changed_hard_proof_never_becomes_generation_miss(
    plan_case, fault
):
    c = plan_case
    async with c.factory() as session:
        workflow = await session.get(db.WorkflowRunModel, c.stable_id)
        if fault == "missing_admission":
            await session.delete(workflow)
        elif fault == "admission_running":
            workflow.status = "running"
        elif fault in {"missing_receipt", "rejected_receipt", "projection_binding"}:
            metadata = deepcopy(workflow.metadata_json)
            if fault == "missing_receipt":
                metadata.pop("source_admission")
            elif fault == "rejected_receipt":
                metadata["source_admission"]["draft_eligible"] = False
            else:
                metadata["chapter_projection_hashes"][str(c.original.chapters[0].id)] = "f" * 64
            workflow.metadata_json = metadata
        elif fault in {"paid_model", "paid_root"}:
            run_id = UUID(workflow.metadata_json["source_admission"]["llm_run_id"])
            run = await session.get(db.LlmRunModel, run_id)
            if fault == "paid_model":
                run.model_name = "contradictory-original-model"
            else:
                operations = list(await session.scalars(select(db.ExecutionOperationModel)))
                roots = [row for row in operations if (row.result_json or {}).get("content")]
                assert roots
                for row in roots:
                    row.result_json = {**row.result_json, "content": "changed paid raw"}
        elif fault in {"chapter", "missing_chapter"}:
            row = await session.get(db.ChapterModel, c.original.chapters[-1].id)
            if fault == "missing_chapter":
                await session.delete(row)
            else:
                row.chapter_goal += " Different adopted planning intent."
        elif fault == "scene":
            row = await session.get(db.SceneCardModel, c.original.scenes[0].id)
            row.exit_state = {"new": "contradictory state"}
        elif fault in {"world_meta", "volume", "canonical", "second_complete_outline"}:
            identity = {
                "world_meta": c.original.assets["world_spec"].id,
                "volume": c.original.assets["volume_plan"].id,
                "canonical": c.original.canonical.id,
                "second_complete_outline": c.original.outline.id,
            }[fault]
            row = await session.get(db.PlanningArtifactVersionModel, identity)
            if fault == "second_complete_outline":
                session.add(
                    db.PlanningArtifactVersionModel(
                        project_id=c.pid,
                        artifact_type="chapter_outline_batch",
                        version_no=2,
                        schema_version="1.0",
                        status="approved",
                        content=deepcopy(row.content),
                    )
                )
            elif fault == "volume":
                row.content = [*deepcopy(row.content), {"volume_number": 2}]
            else:
                row.content = {**row.content, "unknown_changed_source": {"raw": None}}
        elif fault == "extra_chapter":
            session.add(
                db.ChapterModel(
                    project_id=c.pid,
                    chapter_number=5,
                    title="Extra plan",
                    chapter_goal="Different",
                    metadata_json={},
                )
            )
        elif fault == "orphan_scene":
            session.add(
                db.SceneCardModel(
                    project_id=c.pid,
                    chapter_id=uuid4(),
                    scene_number=1,
                    scene_type="investigation",
                    purpose={},
                    metadata_json={},
                )
            )
        elif fault == "materializer_cancelled":
            row = await session.get(db.WorkflowRunModel, c.original.owner.id)
            row.status = "cancelled"
        else:
            row = await session.get(db.ProjectModel, c.pid)
            metadata = deepcopy(row.metadata_json)
            metadata.pop(foundation_selection.POINTER)
            row.metadata_json = metadata
        await session.commit()
    before = await evidence(c)
    async with c.factory() as session:
        with pytest.raises(
            c.helper.MaterializedFanqiePlanPendingError, match="source_repair_pending"
        ):
            await read(c, session)
    assert len(c.sent) == 1 and await evidence(c) == before


async def test_stale_two_session_project_and_pending_source_are_not_refreshed_or_flushed(plan_case):
    c = plan_case
    async with c.factory() as first:
        project = await first.get(db.ProjectModel, c.pid)
        async with c.factory() as other:
            current = await other.get(db.ProjectModel, c.pid)
            current.metadata_json = {**current.metadata_json, "execution_scope_id": "another-scope"}
            await other.commit()
        original = deepcopy(project.metadata_json)
        with pytest.raises(
            c.helper.MaterializedFanqiePlanPendingError, match="caller_project_stale"
        ):
            await c.helper.require_or_read_existing_complete_fanqie_plan(
                first,
                c.settings,
                project=project,
            )
        assert project.metadata_json == original
        project.title += " pending caller change"
        pending = project.title
        with pytest.raises(
            c.helper.MaterializedFanqiePlanPendingError, match="pending_source_state"
        ):
            await c.helper.require_or_read_existing_complete_fanqie_plan(
                first,
                c.settings,
                project=project,
            )
        assert project.title == pending and first.is_modified(project)
    assert len(c.sent) == 1


async def test_unrelated_empty_project_does_not_borrow_this_plan_or_grade(plan_case):
    c = plan_case
    async with c.factory() as session:
        other = db.ProjectModel(
            slug="unrelated-" + uuid4().hex,
            title="The harbour inventory",
            genre="historical",
            language="en",
            project_type="fanqie_short",
            target_chapters=4,
            target_word_count=8000,
            metadata_json={},
        )
        session.add(other)
        await session.commit()
        assert (
            await c.helper.require_or_read_existing_complete_fanqie_plan(
                session,
                c.settings,
                project=other,
            )
            is None
        )
        assert (await read(c, session))["source_artifact_id"] == str(c.original.outline.id)
    assert len(c.sent) == 1


async def test_public_pipeline_retries_skip_all_planning_and_enter_existing_segments_and_export(
    plan_case, integrated_modules, monkeypatch
):
    c = plan_case
    export_helper, pipeline, _ = integrated_modules
    entered = []

    async def segments(session, settings, project, **kwargs):
        entered.append(("segments", project.id))
        draft = await session.get(db.ChapterDraftVersionModel, c.draft_id)
        assert draft.content_md == "The original observer records a faint winter signal."

    async def read_assembly(session, project_id):
        entered.append(("adopted_assembly_boundary", project_id))
        draft = await session.get(db.ChapterDraftVersionModel, c.draft_id)
        # Isolate only the outer planning dispatch. Actual knowledge/source export
        # is covered by test_fanqie_source_export_integrity, not granted by this fixture.
        return SimpleNamespace(full_text=draft.content_md, manifest={"sources": [1, 2, 3, 4]})

    async def no_deslop(session, settings, full_text, **kwargs):
        return full_text

    async def advisory(*args, **kwargs):
        return SimpleNamespace(final_score=None, level="not-assessed")

    async def rejected_export(session, **kwargs):
        entered.append(("source_export_boundary", kwargs["project_id"]))
        body = kwargs["expected"].full_text
        review = pipeline.review_whole_fanqie_short_story(body, title="Observatory")
        assert not review.passed  # This fixture cannot confer a green whole grade.
        return {
            "full_text": body,
            "review": review,
            "status": "needs_revision",
            "whole_book_complete": False,
            "export_paths": {},
            "rejected_paths": {},
            "candidate_paths": {},
            "revision_task_counts": {"ranking": 0, "short_v2": 0},
        }

    monkeypatch.setattr(pipeline, "_run_fanqie_segments", segments)
    monkeypatch.setattr(export_helper, "read_adopted_assembly", read_assembly)
    monkeypatch.setattr(pipeline, "_deslop_whole_short_story", no_deslop)
    monkeypatch.setattr(pipeline, "judge_chapter_litstyle_stable", advisory)
    monkeypatch.setattr(export_helper, "publish_adopted_assembly", rejected_export)
    before = await evidence(c)
    for _ in range(2):
        async with c.factory() as session:
            project = await session.get(db.ProjectModel, c.pid)
            payload = ProjectCreate(
                slug=project.slug,
                title=project.title,
                genre=project.genre,
                language=project.language,
                project_type=ProjectType.FANQIE_SHORT,
                target_chapters=4,
                target_word_count=8000,
                metadata=deepcopy(project.metadata_json),
            )
            with bind_execution_scope(c.original.scope):
                result = await pipeline.run_fanqie_short_pipeline(
                    session,
                    c.settings,
                    project_payload=payload,
                    premise=project.metadata_json["premise"],
                )
            assert result.planning_workflow_run_id == UUID(
                project.metadata_json[foundation_selection.POINTER]["workflow_id"]
            )
            assert result.outline_workflow_run_id == c.original.owner.id
            assert result.chapters_created == result.scenes_created == 0
            assert result.planning_reused is result.materialization_reused is True
            assert result.plan_reuse_receipt["hard_admission_workflow_run_id"] == c.stable_id
            assert result.final_verdict == "needs_revision" and not result.whole_book_complete
    assert [name for name, _ in entered] == [
        "segments",
        "adopted_assembly_boundary",
        "source_export_boundary",
    ] * 2
    assert len(c.sent) == 1 and await evidence(c) == before


@pytest.mark.parametrize("stop", ["missing_hard_admission", "pause", "wrong_scope", "cancel"])
async def test_public_resume_keeps_source_control_scope_and_cancellation_fences(
    plan_case, monkeypatch, stop
):
    c = plan_case
    cancellation = asyncio.CancelledError("caller cancellation remains cancellation")
    async with c.factory() as session:
        if stop == "missing_hard_admission":
            await session.delete(await session.get(db.WorkflowRunModel, c.stable_id))
        elif stop == "pause":
            control = await session.get(db.BookProductionControlModel, c.pid)
            control.desired_state = "pause"
        await session.commit()
    if stop == "cancel":
        from bestseller.services import materialized_outline_source_recovery

        async def cancelled(*args, **kwargs):
            raise cancellation

        monkeypatch.setattr(materialized_outline_source_recovery, "_read_sources", cancelled)

    async def segments_must_not_start(*args, **kwargs):
        pytest.fail("stopped outer recovery must not enter segment generation")

    monkeypatch.setattr(c.pipeline, "_run_fanqie_segments", segments_must_not_start)
    before = await evidence(c)
    async with c.factory() as session:
        project = await session.get(db.ProjectModel, c.pid)
        payload = ProjectCreate(
            slug=project.slug,
            title=project.title,
            genre=project.genre,
            language=project.language,
            project_type=ProjectType.FANQIE_SHORT,
            target_chapters=4,
            target_word_count=8000,
            metadata=deepcopy(project.metadata_json),
        )
        error_type = asyncio.CancelledError if stop == "cancel" else ValueError
        with bind_execution_scope("foreign-scope" if stop == "wrong_scope" else c.original.scope):
            with pytest.raises(error_type) as caught:
                await c.pipeline.run_fanqie_short_pipeline(
                    session,
                    c.settings,
                    project_payload=payload,
                    premise=project.metadata_json["premise"],
                )
        if stop == "cancel":
            assert caught.value is cancellation
        elif stop == "missing_hard_admission":
            assert str(caught.value).startswith("source_repair_pending:")
    assert len(c.sent) == 1 and await evidence(c) == before


async def test_legacy_beat_is_only_an_exact_typed_ui_match_never_latest_or_approval(plan_case):
    from bestseller.domain.fanqie_short import FanqieShortBeatSheet
    from bestseller.services.fanqie_short_planner import build_fanqie_segment_outline_batch

    c = plan_case
    async with c.factory() as session:
        project = await session.get(db.ProjectModel, c.pid)
        sheet = FanqieShortBeatSheet(
            title="An unrelated harbour inventory",
            unlock_milestone_segment=2,
            beats=[
                {
                    "segment_number": number,
                    "beat_role": "observation",
                    "purpose": f"Compare signal {number}",
                    "payoff": f"Record result {number}",
                }
                for number in range(1, 5)
            ],
        )
        expected = build_fanqie_segment_outline_batch(
            project,
            sheet,
            book_spec=c.original.assets["book_spec"].content,
            cast_spec=c.original.assets["cast_spec"].content,
        )
        exact = SimpleNamespace(
            id=uuid4(),
            project_id=c.pid,
            artifact_type="fanqie_beat_sheet",
            status="approved",
            scope_ref_id=None,
            content=sheet.model_dump(mode="json"),
            version_no=1,
        )
        later = SimpleNamespace(
            **{
                **vars(exact),
                "id": uuid4(),
                "version_no": 99,
                "content": {**deepcopy(exact.content), "unlock_milestone_segment": 4},
            }
        )
        foreign = SimpleNamespace(**{**vars(exact), "id": uuid4(), "project_id": uuid4()})
        result = c.helper._legacy_unlock(
            project,
            [later, foreign, exact],
            expected,
            c.original.assets,
        )
        assert result["legacy_beat_artifact_ids"] == [str(exact.id)]
        assert result["unlock_segment"] == 2 and result["legacy_beat_bound"] is True
        assert result["beat_paid_or_source_approval_asserted"] is False
        unbound = c.helper._legacy_unlock(project, [later, foreign], expected, c.original.assets)
        assert unbound["legacy_beat_artifact_ids"] == [] and not unbound["legacy_beat_bound"]
        assert unbound["unbound_unlock_routing"] == "all_existing_chapters"
        assert unbound["unlock_segment"] == project.target_chapters
