"""Ordinary source selection: native SQLite, real paid grades, inert wire/Redis IO.

No source readers, grade verifiers, history checks or hard validators are mocked.
"""

from contextlib import asynccontextmanager
from copy import deepcopy
import json
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from test_fanqie_beat_paid_resume import LeaseIO
from test_fanqie_materialized_plan_resume import (
    REAL_ASSESS,
    evidence,
    integrated_modules,  # noqa: F401 -- imported fixture dependency
    materialized_case,  # noqa: F401 -- imported fixture dependency
    plan_case,  # noqa: F401 -- real SQLite/paid grade fixture
    store,  # noqa: F401 -- imported fixture dependency
)
from test_planning_draft_admission import FAIL, PASS, grade_payload

from bestseller.infra.db import models as db
from bestseller.services import fanqie_outline_source_selection as selection
from bestseller.services import llm, materialized_outline_source_recovery, planning_draft_admission
from bestseller.services.budget import bind_execution_scope
from bestseller.services.chapter_outline_readiness import outline_prefix_projection_hash
from bestseller.services.materialized_outline_admission_read import (
    require_materialized_outline_source_admission,
)
from bestseller.services.planner import _without_planning_meta
from bestseller.services.prompt_source_policy import content_hash
from bestseller.worker import tasks
from bestseller.worker.tasks import TaskCancelledError

NATIVE_COMPLETE = llm.complete_text
pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@asynccontextmanager
async def owner(c):
    """Use native acquisition and the real live lease callback, not proof flags."""
    token = uuid4().hex
    with bind_execution_scope(c.original.scope):
        state = await tasks._acquire_pipeline_live(c.redis, c.original.project.slug, token)
        assert state == "acquired"
        async with tasks._pipeline_live_guard(c.redis, c.original.project.slug, token, state):
            yield token


@pytest.fixture
async def ordinary(plan_case, monkeypatch, request):  # noqa: F811
    c = plan_case
    c.redis = LeaseIO()
    monkeypatch.setattr("redis.asyncio.Redis.from_url", lambda *args, **kwargs: c.redis)
    # Parent fixture is already native and paid; restore the actual assessor and
    # gateway after its final no-new-provider tripwire. Only wire IO is replaced.
    monkeypatch.setattr(planning_draft_admission, "assess_planning_draft_admission", REAL_ASSESS)
    monkeypatch.setattr(llm, "complete_text", NATIVE_COMPLETE)
    c.selection_calls = []
    verdict = deepcopy(FAIL if getattr(request, "param", None) == "rejected" else PASS)
    for violation in verdict["hard_violations"]:
        violation["source_refs"] = ["/approved_world_spec/label"]

    async with c.factory() as session:
        # No drafted content is discarded by the service. This is test setup for
        # an ordinary, not-yet-drafted four-chapter plan; draft protection is
        # exercised independently below.
        draft = await session.get(db.ChapterDraftVersionModel, c.draft_id)
        await session.delete(draft)
        content = deepcopy(c.original.outline.content)
        source = db.PlanningArtifactVersionModel(
            project_id=c.pid,
            artifact_type="chapter_outline_batch",
            status="approved",
            version_no=2,
            schema_version="1.0",
            content=content,
            source_run_id=None,
        )
        session.add(source)
        await session.flush()
        materializer = db.WorkflowRunModel(
            project_id=c.pid,
            workflow_type="materialize_chapter_outline_batch",
            scope_type="planning_artifact",
            scope_id=source.id,
            status="completed",
            metadata_json={
                "source_artifact_id": str(source.id),
                "outline_llm_commercial_judge": {
                    "pass": True,
                    "overall_score": 0.9,
                    "blocking_issues": [],
                },
                "outline_reader_experience_judge": {
                    "pass": True,
                    "overall_score": 0.9,
                    "blocking_issues": [],
                },
            },
        )
        session.add(materializer)
        await session.commit()
        c.source, c.materializer = source, materializer

    async def transport(req, role):
        c.selection_calls.append(req.model_copy(deep=True))
        grade = grade_payload(_without_planning_meta(c.source.content), verdict=verdict)
        calls = [
            {
                "id": "ordinary-native-grade",
                "type": "function",
                "function": {
                    "name": "submit_planning_admission",
                    "arguments": json.dumps(grade),
                },
            }
        ]
        message = {"role": "assistant", "content": "", "tool_calls": calls}
        return "", 13, 7, "tool_calls", calls, message

    monkeypatch.setattr(llm, "_call_litellm", transport)
    async with owner(c):
        async with c.factory() as session:
            try:
                c.grade = (
                    await materialized_outline_source_recovery.assess_materialized_outline_source(
                        session,
                        c.settings,
                        project_slug=c.original.project.slug,
                        artifact_id=c.source.id,
                        materialization_workflow_id=c.materializer.id,
                        requested_by="ordinary-selection-sqlite",
                    )
                )
            except planning_draft_admission.PlanningDraftAdmissionProtocolError as error:
                raise AssertionError(
                    [part.get("error") for part in error.receipt["attempts"]]
                ) from error
            await session.commit()
    assert len(c.selection_calls) == 1
    assert c.grade["draft_eligible"] is (getattr(request, "param", None) != "rejected")
    c.admission_id = materialized_outline_source_recovery.admission_workflow_id(c.pid, c.source.id)
    c.before = await evidence(c)
    return c


async def select_source(c):
    async with owner(c):
        async with c.factory() as session:
            result = await selection.admit_and_select_outline_source(
                session,
                c.settings,
                project_id=c.pid,
                artifact_id=c.source.id,
                materialization_workflow_id=c.materializer.id,
                requested_by="sqlite-selection",
            )
            await session.commit()
            return result


async def common_read(c, *, whole=False):
    async with c.factory() as session:
        project = await session.get(db.ProjectModel, c.pid)
        source = await session.get(db.PlanningArtifactVersionModel, c.source.id)
        chapters = list(
            await session.scalars(
                select(db.ChapterModel)
                .where(db.ChapterModel.project_id == c.pid)
                .order_by(db.ChapterModel.chapter_number)
            )
        )
        scenes = list(
            await session.scalars(
                select(db.SceneCardModel).where(db.SceneCardModel.project_id == c.pid)
            )
        )
        return await require_materialized_outline_source_admission(
            session,
            c.settings,
            project=project,
            artifact=source,
            **(
                {}
                if whole
                else {
                    "chapter": chapters[0],
                    "scenes": [s for s in scenes if s.chapter_id == chapters[0].id],
                }
            ),
        )


async def test_positive_ordinary_grade_selects_all_four_and_common_reader_verifies(ordinary):
    c = ordinary
    result = await select_source(c)
    assert len(result["selection_markers"]) == 4
    assert result["workflow_run_id"] == str(c.admission_id)
    assert result["dispatch_authorized"] is result["adoption_authorized"] is False
    assert result["credit_released"] is False
    async with c.factory() as session:
        chapters = list(
            await session.scalars(
                select(db.ChapterModel).where(db.ChapterModel.project_id == c.pid)
            )
        )
        for chapter in chapters:
            scenes = list(
                await session.scalars(
                    select(db.SceneCardModel)
                    .where(db.SceneCardModel.chapter_id == chapter.id)
                    .order_by(db.SceneCardModel.scene_number)
                )
            )
            marker = chapter.metadata_json[selection.SELECTION_KEY]
            assert marker == {
                "artifact_id": str(c.source.id),
                "workflow_run_id": str(c.admission_id),
                "projection_hash": outline_prefix_projection_hash(chapter, scenes),
                "source_content_sha256": content_hash(c.source.content),
            }
            assert chapter.metadata_json[selection.HISTORY_KEY] == []
            assert "outline_source_admission" not in chapter.metadata_json
    for whole in (False, True):
        verified = await common_read(c, whole=whole)
        assert isinstance(verified, dict) and verified["workflow_run_id"] == str(c.admission_id)
        assert verified["source_admission"]["draft_eligible"] is True
    async with c.factory() as session:
        project = await session.get(db.ProjectModel, c.pid)
        reused = await c.helper.require_or_read_existing_complete_fanqie_plan(
            session,
            c.settings,
            project=project,
        )
        assert reused["source_artifact_id"] == str(c.source.id)
        assert reused["hard_admission_workflow_run_id"] == c.admission_id
    assert len(c.selection_calls) == 1


async def test_reuse_is_idempotent_and_buys_no_grade(ordinary):
    c = ordinary
    first = await select_source(c)
    before = await evidence(c)
    assert await select_source(c) == first
    after = await evidence(c)
    for table, rows in before[0].items():
        actual = after[0][table]
        if table == "workflow_runs":
            # The native assessor checkpoints a verified replay, updating its
            # activity timestamp. Identity, state, receipts and all paid rows
            # remain exact; selection markers/history are unchanged.
            rows = [
                {key: value for key, value in row.items() if key != "updated_at"} for row in rows
            ]
            actual = [
                {key: value for key, value in row.items() if key != "updated_at"} for row in actual
            ]
        assert actual == rows, table
    assert after[1] == before[1]
    assert len(c.selection_calls) == 1


@pytest.mark.parametrize("change", ["stop", "source", "foundation", "physical_grade"])
async def test_native_stop_source_drift_and_malformed_grade_fail_without_selection(
    ordinary, change
):
    c = ordinary
    async with c.factory() as session:
        if change == "stop":
            row = await session.get(db.BookProductionControlModel, c.pid)
            row.desired_state = "stop"
        elif change == "source":
            row = await session.get(db.PlanningArtifactVersionModel, c.source.id)
            row.content = {**row.content, "manual_fact": "Different current source"}
        elif change == "foundation":
            row = await session.get(
                db.PlanningArtifactVersionModel, c.original.assets["book_spec"].id
            )
            row.content = {**row.content, "new_fact": "Current source drift"}
        else:
            row = await session.get(db.LlmRunModel, UUID(c.grade["llm_run_id"]))
            operation_key = row.metadata_json["execution_receipt"]["operation_key"]
            root = await session.get(db.ExecutionOperationModel, operation_key)
            # This native grade is physically retained in the settled operation
            # and transport result, not necessarily in a prompt payload file.
            body = deepcopy(root.result_json)
            body["tool_calls"][0]["function"]["arguments"] = '{"draft_eligible":"true"}'
            root.result_json = body
        await session.commit()
    before = await evidence(c)
    with pytest.raises(ValueError):
        await select_source(c)
    after = await evidence(c)
    assert after[0]["chapters"] == before[0]["chapters"]
    assert len(c.selection_calls) == 1


async def test_no_actual_native_owner_context_denies_even_with_valid_paid_grade(ordinary):
    c = ordinary
    before = await evidence(c)
    with bind_execution_scope(c.original.scope):
        async with c.factory() as session:
            with pytest.raises(ValueError, match="acquired_native_owner_required"):
                await selection.admit_and_select_outline_source(
                    session,
                    c.settings,
                    project_id=c.pid,
                    artifact_id=c.source.id,
                    materialization_workflow_id=c.materializer.id,
                )
    assert await evidence(c) == before and len(c.selection_calls) == 1


async def test_real_bound_owner_callback_tripwire_observes_lost_lease(ordinary):
    c = ordinary
    before = await evidence(c)
    with pytest.raises(TaskCancelledError):
        async with owner(c):
            c.redis.values[tasks._pipeline_live_key(c.original.project.slug)] = uuid4().hex
            async with c.factory() as session:
                await selection.admit_and_select_outline_source(
                    session,
                    c.settings,
                    project_id=c.pid,
                    artifact_id=c.source.id,
                    materialization_workflow_id=c.materializer.id,
                )
    assert await evidence(c) == before and len(c.selection_calls) == 1


async def test_existing_drafts_forbid_reselection_and_preserve_history(ordinary):
    c = ordinary
    async with c.factory() as session:
        chapters = list(
            await session.scalars(
                select(db.ChapterModel).where(db.ChapterModel.project_id == c.pid)
            )
        )
        for chapter in chapters:
            scenes = list(
                await session.scalars(
                    select(db.SceneCardModel)
                    .where(db.SceneCardModel.chapter_id == chapter.id)
                    .order_by(db.SceneCardModel.scene_number)
                )
            )
            previous = {
                "artifact_id": str(c.original.outline.id),
                "workflow_run_id": str(c.stable_id),
                "projection_hash": outline_prefix_projection_hash(chapter, scenes),
                "source_content_sha256": content_hash(c.original.outline.content),
            }
            chapter.metadata_json = {
                **chapter.metadata_json,
                selection.SELECTION_KEY: previous,
                selection.HISTORY_KEY: [{"historical": "preserve"}],
            }
        session.add(
            db.ChapterDraftVersionModel(
                project_id=c.pid,
                chapter_id=chapters[0].id,
                version_no=1,
                content_md="An existing native draft is preserved.",
                is_current=True,
                promotion_state="candidate",
                promotion_metadata={},
            )
        )
        await session.commit()
    before = await evidence(c)
    with pytest.raises(
        ValueError, match=r"existing_draft_source_cannot_be_reselected|current_marker_changed"
    ):
        await select_source(c)
    after = await evidence(c)
    for table in ("chapters", "chapter_draft_versions"):
        assert after[0][table] == before[0][table]
    assert len(c.selection_calls) == 1


@pytest.mark.parametrize("ordinary", ["rejected"], indirect=True)
async def test_stable_failed_ordinary_grade_cannot_become_reader_none_fallback(ordinary):
    c = ordinary
    before = await evidence(c)
    for whole in (False, True):
        with pytest.raises(ValueError, match=r"current_hard_admission|hard_rejected"):
            await common_read(c, whole=whole)
    with pytest.raises(ValueError, match="current_hard_admission_rejected"):
        await select_source(c)
    assert len(c.selection_calls) == 1
    after = await evidence(c)
    assert after[0]["chapters"] == before[0]["chapters"]
