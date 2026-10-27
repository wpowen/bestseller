"""Real SQLite revision lifecycle; only the native gateway return is inert."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
import json
from uuid import UUID, uuid4

import pytest
from sqlalchemy import JSON, ForeignKeyConstraint, MetaData, inspect, select
from sqlalchemy.dialects.postgresql import JSONB
from test_fanqie_materialized_plan_resume import (
    REAL_ASSESS,
    integrated_modules,  # noqa: F401 - fixture dependencies
    store,  # noqa: F401
)
from test_fanqie_materialized_plan_resume import (
    plan_case as paid_plan_case,  # noqa: F401
)
from test_materialized_outline_source_recovery import case as raw_materialized_case  # noqa: F401
from test_planning_draft_admission import grade_payload

from bestseller.infra.db import models as db
from bestseller.services import llm, workflows
from bestseller.services import outline_revision_lifecycle as service
from bestseller.services import planning_draft_admission as admission
from bestseller.services.budget import bind_execution_scope
from bestseller.services.planner import _without_planning_meta

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.fixture
async def materialized_case(raw_materialized_case):  # noqa: F811
    c = raw_materialized_case
    async with c.factory() as session:
        cast = await session.get(db.PlanningArtifactVersionModel, c.assets["cast_spec"].id)
        cast.content = {
            **cast.content,
            "protagonist": {
                "name": "Mira",
                "role": "protagonist",
                "gender": "female",
                "pronouns": "she/her",
                "identity_locked": True,
            },
        }
        await session.commit()
        # The enclosing paid fixture grades these actual authored sources.
        c.assets["cast_spec"].content = deepcopy(cast.content)
    return c


@pytest.fixture
async def case(paid_plan_case, monkeypatch):  # noqa: F811
    c = paid_plan_case
    engine = c.factory.kw["bind"]
    assert engine.dialect.name == "sqlite" and "test" in engine.url.database
    for model in (
        db.WorkflowStepRunModel,
        db.RewriteTaskModel,
        db.CharacterModel,
        db.SceneDraftVersionModel,
        db.VolumeModel,
    ):
        table = model.__table__.to_metadata(MetaData())
        for column in table.c:
            if isinstance(column.type, JSONB):
                column.type = JSON()
            if column.server_default is not None and "::" in str(column.server_default.arg):
                column.server_default = None
        for constraint in list(table.constraints):
            if isinstance(constraint, ForeignKeyConstraint):
                table.constraints.remove(constraint)
        async with engine.begin() as connection:
            await connection.run_sync(table.create)
    c.candidate = deepcopy(_without_planning_meta(c.original.outline.content))
    c.candidate["chapters"][0]["chapter_goal"] = (
        "Measure sensor 1 against the retained calibration log"
    )
    for chapter in c.candidate["chapters"]:
        number = chapter["chapter_number"]
        chapter["scenes"][0]["time_label"] = f"Day {number}, before dusk"
        chapter.update(
            opening_situation=f"Mira reaches weather station {number} before dusk.",
            main_conflict=f"The storm will erase station {number}'s unrecorded calibration.",
            hook_description=f"The station {number} log reveals an unexplained signal.",
            causal_contract={
                "chapter_function": "action",
                "pressure": "The storm arrives before dusk.",
                "protagonist_desire": "Mira wants a reliable sensor reading.",
                "protagonist_choice": "Mira stays to check the calibration log.",
                "visible_action_or_reaction": "Mira compares the log against the instrument.",
                "resistance": "The storm interrupts the instrument connection.",
                "cost_or_tradeoff": "Mira loses her chance to leave before dark.",
                "gain_or_reveal": f"Mira records the station {number} calibration.",
                "state_change": f"Station {number} is documented rather than unmeasured.",
                "next_reader_desire": "The reader wants to trace the unexplained signal.",
            },
        )
    c.grade = grade_payload(c.candidate)
    c.boundaries = []
    c.before_return = None
    c.incomplete = False
    c.registered = None
    async with c.factory() as session:
        # An old retained draft is eligible for ordinary unadopted reconciliation.
        draft = await session.get(db.ChapterDraftVersionModel, c.draft_id)
        draft.created_at = datetime.now(UTC) - timedelta(days=1)
        await session.commit()
        c.historical = []
        for model in (db.ExecutionOperationModel, db.ExecutionAttemptModel, db.LlmRunModel):
            key = inspect(model).primary_key[0].key
            c.historical.append(
                (model, key, list(await session.scalars(select(getattr(model, key)))))
            )

    async def transport(request, role):
        c.sent.append(request)
        if request.prompt_template == "planning_draft_admission":
            async with c.factory() as other:
                owner = await other.get(db.WorkflowRunModel, UUID(c.registered["workflow_run_id"]))
                c.boundaries.append((owner.status, owner.current_step, owner.error_message))
            if c.before_return is not None:
                await c.before_return()
            if c.incomplete:
                return "", 10, 5, "length", None, {"role": "assistant", "content": ""}
            calls = [
                {
                    "id": "offline-revision",
                    "type": "function",
                    "function": {
                        "name": "submit_planning_admission",
                        "arguments": json.dumps(c.grade),
                    },
                }
            ]
            return (
                "",
                10,
                5,
                "tool_calls",
                calls,
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": calls,
                },
            )
        assert request.prompt_template in {
            "outline_commercial_judge",
            "outline_reader_experience_judge",
        }, request.prompt_template
        raw = json.dumps(
            {
                "pass": True,
                "overall_score": 0.95,
                "dimension_scores": {},
                "blocking_issues": [],
                "audit_issues": [],
                "rewrite_plan": {
                    "scope": "outline",
                    "preserve": [],
                    "change": [],
                    "instructions": "",
                },
            }
        )
        return raw, 10, 5, "stop", None, {"role": "assistant", "content": raw}

    monkeypatch.setattr(llm, "_call_litellm", transport)
    # Upstream resume fixtures deny the gateway wholesale. Exercise the real
    # normal materializer judges too, using the same inert native transport.
    from bestseller.services import outline_llm_judge, outline_reader_experience_judge

    monkeypatch.setattr(llm, "complete_text", admission.complete_text)
    monkeypatch.setattr(outline_llm_judge, "complete_text", admission.complete_text)
    monkeypatch.setattr(outline_reader_experience_judge, "complete_text", admission.complete_text)
    monkeypatch.setattr(admission, "assess_planning_draft_admission", REAL_ASSESS)
    monkeypatch.setattr(workflows, "load_settings", lambda: c.settings)
    return c


async def stage(c, *, scope=None, **overrides):
    kwargs = {
        "project_slug": c.original.project.slug,
        "parent_artifact_id": c.original.outline.id,
        "parent_materialization_workflow_id": c.original.owner.id,
        "candidate_payload": c.candidate,
        "proposal_receipt": {"schema": "offline-ungraded-proposal.v1", "raw": {"unknown": None}},
        "actor": "fixture-operator",
        "authorization_ref": "explicit-offline-test",
        "explicit_user_approval": True,
        **overrides,
    }
    async with c.factory() as session:
        with bind_execution_scope(scope or c.original.scope):
            result = await service.stage_full_outline_revision(session, c.settings, **kwargs)
        await session.commit()
    c.registered = result
    return result


async def apply(c, *, scope=None):
    async with c.factory() as session:
        with bind_execution_scope(scope or c.original.scope):
            result = await service.review_and_apply_full_outline_revision(
                session,
                c.settings,
                artifact_id=c.registered["artifact_id"],
                actor="fixture-operator",
            )
        await session.commit()
        return result


async def protected(c):
    """Full raw historical rows, rather than reconstructed assertion payloads."""
    async with c.factory() as session:
        result = {}
        for model, where in (
            (
                db.PlanningArtifactVersionModel,
                db.PlanningArtifactVersionModel.id == c.original.outline.id,
            ),
            (db.WorkflowRunModel, db.WorkflowRunModel.id == c.original.owner.id),
            (db.WorkflowRunModel, db.WorkflowRunModel.id == c.stable_id),
            (db.ChapterDraftVersionModel, db.ChapterDraftVersionModel.id == c.draft_id),
        ):
            rows = list(await session.scalars(select(model).where(where)))
            result.setdefault(model.__name__, []).extend(
                [
                    {
                        column.key: deepcopy(getattr(row, column.key))
                        for column in inspect(model).column_attrs
                    }
                    for row in rows
                ]
            )
        for model, key, ids in c.historical:
            rows = list(await session.scalars(select(model).where(getattr(model, key).in_(ids))))
            result[model.__name__] = [
                {
                    column.key: deepcopy(getattr(row, column.key))
                    for column in inspect(model).column_attrs
                }
                for row in rows
            ]
        return result


async def change_owner(c, **values):
    async with c.factory() as session:
        row = await session.get(db.WorkflowRunModel, UUID(c.registered["workflow_run_id"]))
        for key, value in values.items():
            setattr(row, key, value)
        await session.commit()


def hard_reject(c):
    grade = grade_payload(
        c.candidate,
        verdict={
            "draft_eligible": False,
            "hard_violations": [
                {
                    "code": "SCENE_NOT_EXECUTABLE",
                    "path": "chapters[0]",
                    "evidence": c.candidate["chapters"][0]["chapter_goal"],
                    "source_requirement": "Native fixture diagnosis, not an author requirement",
                    "source_refs": ["/candidate_payload/chapters/0/chapter_goal"],
                }
            ],
            "editorial_notes": ["Preserve the old commercial observations"],
        },
    )
    for check in grade["projection_checks"]:
        if check["path"] == "chapters[0]":
            check.update(status="conflict", related_violation_paths=["chapters[0]"])
    c.grade = grade


async def test_stage_is_ungraded_idempotent_and_preserves_all_old_raw(case):
    c = case
    before = await protected(c)
    count = len(c.sent)
    first = await stage(c)
    second = await stage(c)
    assert first == {**second, "reused": False}
    assert second["reused"] is True
    assert len(c.sent) == count
    async with c.factory() as session:
        artifact = await session.get(db.PlanningArtifactVersionModel, UUID(first["artifact_id"]))
        owner = await session.get(db.WorkflowRunModel, UUID(first["workflow_run_id"]))
        assert artifact.status == "draft" and artifact.source_run_id == owner.id
        assert _without_planning_meta(artifact.content) == c.candidate
        assert owner.status == "running" and owner.scope_id == artifact.id
        assert owner.metadata_json["protocol"] == {}
        revision = owner.metadata_json["full_outline_revision"]
        assert revision["grade_asserted"] is False and revision["write_authorized"] is False
        assert "source_admission" not in owner.metadata_json
    assert await protected(c) == before


@pytest.mark.parametrize("fault", ["owner", "foundation", "canonical"])
async def test_stage_replay_checks_current_saved_dependencies(case, fault):
    c = case
    await stage(c)
    before = await protected(c)
    async with c.factory() as session:
        owner = await session.get(db.WorkflowRunModel, UUID(c.registered["workflow_run_id"]))
        if fault == "owner":
            owner.scope_type = "chapter"
        else:
            state = deepcopy(owner.metadata_json)
            if fault == "foundation":
                state["foundation"]["volume_plan"]["content_hash"] = "f" * 64
            else:
                state["canonical_hash"] = "f" * 64
            owner.metadata_json = state
        await session.commit()
    with pytest.raises(ValueError, match=r"existing_proposal_(owner|dependencies)_changed"):
        await stage(c)
    assert len(c.sent) == 1 and await protected(c) == before


@pytest.mark.parametrize("entry", ["stage_replay", "first_review"])
async def test_empty_protocol_cannot_follow_project_and_ambient_to_an_open_new_budget(case, entry):
    c = case
    registered = await stage(c)
    protected_before = await protected(c)
    replacement = "project:replacement-before-first-grade"
    async with c.factory() as session:
        project = await session.get(db.ProjectModel, c.pid)
        project.metadata_json = {**project.metadata_json, "execution_scope_id": replacement}
        session.add(
            db.ExecutionBudgetModel(
                scope_id=replacement,
                max_calls=100,
                calls_used=17,
                max_tokens=1000000,
                tokens_charged=9381,
                status="active",
                deadline_at=datetime.now(UTC) + timedelta(hours=1),
                metadata_json={"unrelated_scope_receipt": {"unknown": None}},
            )
        )
        await session.commit()

    async def saved_rows():
        async with c.factory() as session:
            owner = await session.get(db.WorkflowRunModel, UUID(registered["workflow_run_id"]))
            assert owner.metadata_json["protocol"] == {}
            assert owner.metadata_json["execution_scope_id"] == c.original.scope
            return {
                "owner": {
                    field.key: deepcopy(getattr(owner, field.key))
                    for field in inspect(db.WorkflowRunModel).column_attrs
                },
                "budgets": [
                    dict(row)
                    for row in (
                        await session.execute(
                            select(db.ExecutionBudgetModel.__table__).order_by(
                                db.ExecutionBudgetModel.scope_id
                            )
                        )
                    ).mappings()
                ],
            }

    before = deepcopy(await saved_rows())
    with pytest.raises(ValueError, match="outline_revision:original_scope_changed"):
        if entry == "stage_replay":
            await stage(c, scope=replacement)
        else:
            await apply(c, scope=replacement)
    assert len(c.sent) == 1 and c.boundaries == []
    assert c.registered == registered
    assert await saved_rows() == before and await protected(c) == protected_before


@pytest.mark.parametrize("fault", ["paused", "control", "scope", "budget", "cancelled"])
async def test_fresh_stop_before_review_no_native_or_materializer(case, fault):
    c = case
    await stage(c)
    before = await protected(c)
    async with c.factory() as session:
        if fault == "paused":
            row = await session.get(db.ProjectModel, c.pid)
            row.status = "paused"
        elif fault == "control":
            row = await session.get(db.BookProductionControlModel, c.pid)
            row.desired_state = "stop"
        elif fault == "scope":
            row = await session.get(db.ProjectModel, c.pid)
            row.metadata_json = {
                **row.metadata_json,
                "execution_scope_id": "project:replacement-scope",
            }
        elif fault == "budget":
            row = await session.get(db.ExecutionBudgetModel, c.original.scope)
            row.status = "cancelled"
        else:
            row = await session.get(db.WorkflowRunModel, UUID(c.registered["workflow_run_id"]))
            row.status = "cancelled"
        await session.commit()
    expected = {
        "paused": "project_halted",
        "control": "control_halted",
        "scope": "original_scope_changed",
        "budget": "original_budget_closed",
        "cancelled": "workflow_halted",
    }[fault]
    with pytest.raises(ValueError, match=rf"^outline_revision:{expected}$"):
        await apply(c)
    assert len(c.sent) == 1 and await protected(c) == before


async def test_valid_v4_revises_all_projections_retains_body_and_replays_without_paid_gates(case):
    c = case
    await stage(c)
    before = await protected(c)
    result = await apply(c)
    assert result["materialized"] is True
    assert result["workflow_run_id"] == c.registered["workflow_run_id"]
    assert result["source_admission"]["schema"] == "planning-draft-admission.v4"
    assert result["source_admission"]["draft_eligible"] is True
    assert c.boundaries == [("running", "review_revision_before_materialization", None)]
    assert await protected(c) == before
    sent = len(c.sent)
    replay = await apply(c)
    assert replay is not None and len(c.sent) == sent
    async with c.factory() as session:
        owner = await session.get(db.WorkflowRunModel, UUID(c.registered["workflow_run_id"]))
        assert owner.status == "completed"
        revision = owner.metadata_json["full_outline_revision"]
        assert revision["phase"] == "materialized"
        assert len(revision["source_alignment_tasks"]) == 1
        tasks = list(await session.scalars(select(db.RewriteTaskModel)))
        assert (
            len(tasks) == 1
            and tasks[0].metadata_json["source_alignment"]["evidence_status"] == "UNGRADED"
        )
        assert tasks[0].metadata_json["parent_draft_id"] == str(c.draft_id)
        chapters = list(
            await session.scalars(select(db.ChapterModel).order_by(db.ChapterModel.chapter_number))
        )
        assert chapters[0].chapter_goal == c.candidate["chapters"][0]["chapter_goal"]
        for chapter in chapters:
            marker = chapter.metadata_json["outline_source_admission"]
            assert marker["artifact_id"] == c.registered["artifact_id"]
            assert marker["workflow_run_id"] == c.registered["workflow_run_id"]


async def test_protocol_exhaustion_is_durable_unknown_not_false_grade(case):
    c = case
    await stage(c)
    before = await protected(c)
    c.incomplete = True
    with pytest.raises(admission.PlanningDraftAdmissionProtocolError):
        await apply(c)
    assert len(c.sent) == 3  # One protected historical send + the actual two-slot chapter cap.
    async with c.factory() as session:
        owner = await session.get(db.WorkflowRunModel, UUID(c.registered["workflow_run_id"]))
        assert owner.status == "machine_blocked"
        assert owner.current_step == "admission_protocol_exhausted"
        assert owner.metadata_json["full_outline_revision"]["phase"] == "protocol_unknown"
        assert len(owner.metadata_json["protocol"]["attempts"]) == 2
        assert "source_admission" not in owner.metadata_json
    assert await protected(c) == before


async def test_actual_v4_hard_reject_retains_native_false_without_materializing(case):
    c = case
    await stage(c)
    before = await protected(c)
    hard_reject(c)
    result = await apply(c)
    assert result["materialized"] is False
    assert result["source_admission"]["draft_eligible"] is False
    assert result["source_admission"]["schema"] == service.ADMISSION_SCHEMA
    assert len(c.sent) == 2
    async with c.factory() as session:
        owner = await session.get(db.WorkflowRunModel, UUID(c.registered["workflow_run_id"]))
        assert owner.status == "machine_blocked" and owner.current_step == "source_repair_required"
        assert owner.metadata_json["protocol"]["status"] == "validated"
        artifact = await session.get(db.PlanningArtifactVersionModel, owner.scope_id)
        assert artifact.status == "draft"
        materializers = list(
            await session.scalars(
                select(db.WorkflowRunModel).where(
                    db.WorkflowRunModel.workflow_type == "materialize_chapter_outline_batch"
                )
            )
        )
        assert [w.id for w in materializers] == [c.original.owner.id]
    assert await protected(c) == before


async def test_initial_failed_owner_is_running_at_real_native_boundary_and_archives_failure(case):
    c = case
    await stage(c)
    old = {
        "status": "failed",
        "current_step": "prior_review_failure",
        "error_message": "old raw failure",
    }
    await change_owner(c, **old)
    hard_reject(c)
    result = await apply(c)
    assert result["materialized"] is False
    assert c.boundaries == [("running", "review_revision_before_materialization", None)]
    async with c.factory() as session:
        owner = await session.get(db.WorkflowRunModel, UUID(c.registered["workflow_run_id"]))
        assert owner.metadata_json["previous_revision_failures"] == [old]
        assert owner.status == "machine_blocked"


@pytest.mark.parametrize("when", ["before_protocol_checkpoint", "after_real_return"])
@pytest.mark.parametrize("field", ["status", "current_step", "error_message", "metadata_json"])
async def test_entire_observed_owner_cas_rejects_independent_change(case, monkeypatch, when, field):
    c = case
    await stage(c)
    before = await protected(c)
    async with c.factory() as session:
        owner = await session.get(db.WorkflowRunModel, UUID(c.registered["workflow_run_id"]))
        foreign = {
            "status": "failed",
            "current_step": "external_failure",
            "error_message": "independent durable failure",
            "metadata_json": {
                **deepcopy(owner.metadata_json),
                "external_failure": {"unknown": None},
            },
        }[field]

    async def racing_assess(session, settings, **kwargs):
        if when == "before_protocol_checkpoint":
            original_persist = kwargs["persist_protocol"]

            async def racing_persist():
                await change_owner(c, **{field: foreign})
                await original_persist()

            kwargs["persist_protocol"] = racing_persist
        receipt = await REAL_ASSESS(session, settings, **kwargs)
        if when == "after_real_return":
            await change_owner(c, **{field: foreign})
        return receipt

    monkeypatch.setattr(admission, "assess_planning_draft_admission", racing_assess)
    with pytest.raises(ValueError, match="outline_revision:workflow_changed_during_review"):
        await apply(c)
    assert len(c.sent) == (1 if when == "before_protocol_checkpoint" else 2)
    async with c.factory() as session:
        owner = await session.get(db.WorkflowRunModel, UUID(c.registered["workflow_run_id"]))
        assert getattr(owner, field) == foreign
        assert owner.status != "completed"
        assert "source_admission" not in owner.metadata_json
    assert await protected(c) == before


@pytest.mark.parametrize("fault", ["project", "control"])
async def test_fresh_stop_after_valid_paid_review_preserves_receipt_before_materialization(
    case,
    monkeypatch,
    fault,
):
    c = case
    await stage(c)
    before = await protected(c)

    async def stop_after_grade(session, settings, **kwargs):
        receipt = await REAL_ASSESS(session, settings, **kwargs)
        async with c.factory() as other:
            if fault == "project":
                project = await other.get(db.ProjectModel, c.pid)
                project.status = "paused"
            else:
                control = await other.get(db.BookProductionControlModel, c.pid)
                control.desired_state = "stop"
            await other.commit()
        return receipt

    monkeypatch.setattr(admission, "assess_planning_draft_admission", stop_after_grade)
    with pytest.raises(ValueError, match=r"outline_revision:(project|control)_halted"):
        await apply(c)
    assert len(c.sent) == 2
    async with c.factory() as session:
        owner = await session.get(db.WorkflowRunModel, UUID(c.registered["workflow_run_id"]))
        assert owner.metadata_json["source_admission"]["draft_eligible"] is True
        assert len(owner.metadata_json["protocol"]["attempts"]) == 1
        artifact = await session.get(db.PlanningArtifactVersionModel, owner.scope_id)
        assert artifact.status == "draft"
    assert await protected(c) == before


async def test_committed_materializer_replays_same_identity_with_real_readback_and_no_new_gates(
    case,
    monkeypatch,
):
    c = case
    await stage(c)
    before = await protected(c)
    actual_materialize = workflows.materialize_chapter_outline_batch

    class InterruptedAfterCommitError(Exception):
        pass

    async def committed_then_interrupted(session, *args, **kwargs):
        result = await actual_materialize(session, *args, **kwargs)
        await session.commit()
        c.materialized = result
        raise InterruptedAfterCommitError

    monkeypatch.setattr(workflows, "materialize_chapter_outline_batch", committed_then_interrupted)
    with pytest.raises(InterruptedAfterCommitError):
        await apply(c)
    count = len(c.sent)
    assert {r.prompt_template for r in c.sent} >= {
        "planning_draft_admission",
        "outline_commercial_judge",
        "outline_reader_experience_judge",
    }
    async with c.factory() as session:
        owner = await session.get(db.WorkflowRunModel, UUID(c.registered["workflow_run_id"]))
        assert owner.status == "running" and owner.current_step == service.READY
        materializer = await session.get(db.WorkflowRunModel, c.materialized.workflow_run_id)
        assert materializer.status == "completed"
        steps = list(
            await session.scalars(
                select(db.WorkflowStepRunModel.id).where(
                    db.WorkflowStepRunModel.workflow_run_id == materializer.id
                )
            )
        )
    monkeypatch.setattr(workflows, "materialize_chapter_outline_batch", actual_materialize)
    result = await apply(c)
    assert result["materialization_workflow_run_id"] == str(c.materialized.workflow_run_id)
    assert result["workflow_run_id"] == c.registered["workflow_run_id"]
    assert result["materialized"] is True and len(c.sent) == count
    async with c.factory() as session:
        after_steps = list(
            await session.scalars(
                select(db.WorkflowStepRunModel.id).where(
                    db.WorkflowStepRunModel.workflow_run_id == c.materialized.workflow_run_id
                )
            )
        )
        assert after_steps == steps
        materializers = list(
            await session.scalars(
                select(db.WorkflowRunModel.id).where(
                    db.WorkflowRunModel.workflow_type == "materialize_chapter_outline_batch"
                )
            )
        )
        assert set(materializers) == {c.original.owner.id, c.materialized.workflow_run_id}
    assert await protected(c) == before


@pytest.mark.parametrize("fault", ["marker", "projection", "project"])
async def test_completed_read_requires_fresh_flat_marker_and_actual_projection_owner(case, fault):
    c = case
    await stage(c)
    await apply(c)
    count = len(c.sent)
    before = await protected(c)
    async with c.factory() as session:
        chapter = await session.get(db.ChapterModel, c.original.chapters[0].id)
        if fault == "marker":
            metadata = deepcopy(chapter.metadata_json)
            metadata["outline_source_admission"]["workflow_run_id"] = str(c.original.owner.id)
            chapter.metadata_json = metadata
        elif fault == "projection":
            chapter.chapter_goal = "An independent replacement without a new source proof"
        else:
            foreign = db.ProjectModel(
                id=uuid4(),
                slug="foreign-revision-owner",
                title="Foreign owner",
                genre="fantasy",
                language="en",
                target_chapters=4,
                target_word_count=8000,
                metadata_json={},
            )
            session.add(foreign)
            artifact = await session.get(
                db.PlanningArtifactVersionModel, UUID(c.registered["artifact_id"])
            )
            artifact.project_id = foreign.id
        await session.commit()
    with pytest.raises(ValueError, match=r"outline_(source_admission_required|revision):"):
        await apply(c)
    assert len(c.sent) == count and await protected(c) == before
