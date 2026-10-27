"""SQLite source/receipt tests; patch dispatcher is inert, no PG/provider."""

from copy import deepcopy
import json
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select
from test_foundation_selection import store  # noqa: F401 - fixture dependency
from test_materialized_outline_source_recovery import case as source_case  # noqa: F401
from test_outline_revision_proposal_v2 import proposal as proposal

from bestseller.infra.db import models as db
from bestseller.services import foundation_selection
from bestseller.services import outline_revision_proposal as service
from bestseller.services import outline_revision_proposal_v2 as current_service
from bestseller.services.budget import bind_execution_scope
from bestseller.services.prompt_source_policy import content_hash

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


# Shared actual 3.5 lifecycle fixture; all legacy intent assertions below remain.


async def call(c, **changes):
    async with c.factory() as session:
        kwargs = {
            "project_slug": c.project.slug,
            "parent_artifact_id": c.outline.id,
            "parent_materialization_workflow_id": c.owner.id,
            "beat_artifact_id": c.beat.id,
            "actor": "fixture operator",
            "authorization_ref": "explicit-offline-fixture",
            "explicit_user_approval": True,
        }
        kwargs.update(changes)
        with bind_execution_scope(c.scope):
            return await service.propose_full_outline_revision(session, c.settings, **kwargs)


async def protected(c):
    async with c.factory() as session:
        return [
            deepcopy(list((await session.execute(select(*model.__table__.columns))).mappings()))
            for model in (
                db.ProjectModel,
                db.PlanningArtifactVersionModel,
                db.ChapterModel,
                db.SceneCardModel,
                db.BookProductionControlModel,
                db.ExecutionBudgetModel,
            )
        ]


async def test_exact_legacy_base_compiler_patch_replays_without_grade_projection_or_budget_reset(
    proposal,
):
    c = proposal
    before = await protected(c)
    first = await call(c)
    assert len(c.patch_calls) == 1
    context = c.patch_calls[0]["source_context"]
    assert context["untrusted_retained_beat"]["source_run_id"] is None
    assert context["untrusted_retained_beat"]["content"] == c.beat.content
    assert context["untrusted_retained_beat"]["paid_source_proof_asserted"] is False
    assert set(context["approved_foundation"]) == set(foundation_selection.KINDS)
    for kind, source in c.assets.items():
        assert context["approved_foundation"][kind]["content"] == source.content
    assert context["current_canonical"]["content"] == c.canonical.content
    assert context["legacy_materialized_base"]["candidate_payload"]["unknown_author_tree"] == {
        "unset": None
    }
    receipt = first["proposal_receipt"]
    assert receipt["grade_asserted"] is False and receipt["adoption_granted"] is False
    assert receipt["legacy_beat_paid_full_source_asserted"] is False
    assert len(receipt["paid_patch_attempts"]) == 1
    assert receipt["native_patch"][0]["field"] == "opening_situation"
    assert await call(c) == first and len(c.patch_calls) == 1
    assert await protected(c) == before


@pytest.mark.parametrize("change", ["approval", "actor", "reason", "foreign_beat", "budget"])
async def test_entry_guards_never_dispatch(proposal, change):
    c = proposal
    changes = {
        "approval": {"explicit_user_approval": False},
        "actor": {"actor": ""},
        "reason": {"authorization_ref": ""},
        "foreign_beat": {"beat_artifact_id": uuid4()},
    }.get(change, {})
    if change == "budget":
        async with c.factory() as session:
            budget = await session.get(db.ExecutionBudgetModel, c.scope)
            budget.status = "unknown"
            await session.commit()
    with pytest.raises(ValueError):
        await call(c, **changes)
    assert c.patch_calls == []


@pytest.mark.parametrize("change", ["beat", "pause", "cancel_owner", "canonical"])
async def test_postreturn_drift_cannot_publish_and_original_paid_rows_survive(proposal, change):
    c = proposal

    async def after(session, identity):
        # Core row mutation emulates an external writer without touching stale ORM objects.
        from sqlalchemy import update

        if change == "beat":
            await session.execute(
                update(db.PlanningArtifactVersionModel)
                .where(db.PlanningArtifactVersionModel.id == c.beat.id)
                .values(content={**c.beat.content, "unknown": "changed"})
            )
        elif change == "pause":
            await session.execute(
                update(db.ProjectModel)
                .where(db.ProjectModel.id == c.project.id)
                .values(status="paused")
            )
        elif change == "cancel_owner":
            await session.execute(
                update(db.WorkflowRunModel)
                .where(db.WorkflowRunModel.id == identity)
                .values(status="cancelled")
            )
        else:
            await session.execute(
                update(db.PlanningArtifactVersionModel)
                .where(db.PlanningArtifactVersionModel.id == c.canonical.id)
                .values(content={"changed": True})
            )
        await session.commit()

    c.after_patch = after
    with pytest.raises(ValueError):
        await call(c)
    assert len(c.patch_calls) == 1
    async with c.factory() as session:
        assert len(list(await session.scalars(select(db.LlmRunModel)))) == 1
        owner = (
            await session.scalars(
                select(db.WorkflowRunModel).where(
                    db.WorkflowRunModel.workflow_type == service.WORKFLOW
                )
            )
        ).one()
        assert owner.status != "completed"
        if change == "cancel_owner":
            assert owner.status == "cancelled"


@pytest.mark.parametrize("error", [RuntimeError("paid invalid patch"), KeyboardInterrupt()])
async def test_terminal_failure_and_interrupt_never_buy_another_protocol(proposal, error):
    c = proposal
    c.patch_error = error
    with pytest.raises(type(error)):
        await call(c)
    with pytest.raises(ValueError, match="terminal_or_inflight"):
        await call(c)
    assert len(c.patch_calls) == 1


async def test_completed_physical_file_tamper_fails_closed_without_call(proposal):
    c = proposal
    await call(c)
    path = next(path for key, path in c.paths.items() if key.endswith(":request"))
    assert isinstance(path, Path)
    path.write_text("{}")
    with pytest.raises(ValueError):
        await call(c)
    assert len(c.patch_calls) == 1


async def test_all_32_findings_remain_addressable_in_at_most_four_chapter_directives():
    context = {
        "compiler_candidate": {"chapters": [{"chapter_number": n} for n in range(1, 5)]},
        "readiness_diagnostics": {
            "blocking_findings": [
                {
                    "path": f"chapter_outlines[{n // 8}].field",
                    "code": f"MISSING_{n}",
                    "unknown": None,
                }
                for n in range(32)
            ],
            "audit_findings": [],
        },
    }
    before = deepcopy(context)
    directives = [json.loads(item) for item in service._directives(context)]
    indices = [
        index
        for item in directives
        for group in item["chapter_findings"].values()
        for index in group
    ]
    assert len(directives) == 4 and sorted(indices) == list(range(32))
    assert context == before


async def test_two_patch_attempts_are_both_physically_bound_and_settled(proposal):
    c = proposal
    c.protocol_retry = True
    result = await call(c)
    assert len(result["proposal_receipt"]["paid_patch_attempts"]) == 2
    async with c.factory() as session:
        runs = list(await session.scalars(select(db.LlmRunModel)))
        attempts = list(await session.scalars(select(db.ExecutionAttemptModel)))
        assert len(runs) == len(attempts) == 2
        assert all(
            attempt.status == "completed" and attempt.actual_tokens == 18 for attempt in attempts
        )
    assert await call(c) == result and len(c.patch_calls) == 1


async def test_inflight_without_published_proposal_requires_recovery_without_dispatch(proposal):
    from sqlalchemy import update

    c = proposal
    result = await call(c)
    async with c.factory() as session:
        owner = (
            await session.scalars(
                select(db.WorkflowRunModel).where(
                    db.WorkflowRunModel.workflow_type == service.WORKFLOW
                )
            )
        ).one()
        state = deepcopy(owner.metadata_json)
        state["phase"] = "dispatching"
        state.pop("candidate_payload")
        state.pop("proposal_receipt")
        await session.execute(
            update(db.WorkflowRunModel)
            .where(db.WorkflowRunModel.id == owner.id)
            .values(status="running", metadata_json=state)
        )
        await session.commit()
    with pytest.raises(ValueError, match="terminal_or_inflight"):
        await call(c)
    assert len(c.patch_calls) == 1
    assert result["proposal_receipt"]["grade_asserted"] is False


@pytest.mark.parametrize("phase", ["prepared", "completed"])
async def test_existing_proposal_cannot_move_to_another_open_project_and_ambient_budget(
    proposal, monkeypatch, phase
):
    from sqlalchemy import update

    from bestseller.services import pipelines

    c = proposal
    if phase == "prepared":
        checkpoint = pipelines._checkpoint_commit

        async def stop_before_first_provider(session):
            await checkpoint(session)
            raise RuntimeError("prepared checkpoint, no provider yet")

        monkeypatch.setattr(pipelines, "_checkpoint_commit", stop_before_first_provider)
        with pytest.raises(RuntimeError, match="prepared checkpoint"):
            await call(c)
        monkeypatch.setattr(pipelines, "_checkpoint_commit", checkpoint)
        assert c.patch_calls == []
    else:
        await call(c)
        assert len(c.patch_calls) == 1

    scope_b = "project:alternate-open-budget:" + str(uuid4())
    async with c.factory() as session:
        owner = (
            await session.scalars(
                select(db.WorkflowRunModel).where(
                    db.WorkflowRunModel.workflow_type == service.WORKFLOW
                )
            )
        ).one()
        assert owner.status == ("running" if phase == "prepared" else "completed")
        assert owner.metadata_json["phase"] == (
            "prepared" if phase == "prepared" else "proposal_ready"
        )
        assert owner.metadata_json["execution_scope_id"] == c.scope
        original_context = deepcopy(owner.metadata_json["source_context"])
        project = await session.get(db.ProjectModel, c.project.id)
        await session.execute(
            update(db.ProjectModel)
            .where(db.ProjectModel.id == c.project.id)
            .values(metadata_json={**project.metadata_json, "execution_scope_id": scope_b})
        )
        session.add(
            db.ExecutionBudgetModel(
                scope_id=scope_b,
                max_calls=c.budget.max_calls,
                max_tokens=c.budget.max_tokens,
                calls_used=0,
                tokens_charged=0,
                status="active",
                deadline_at=c.budget.deadline_at,
                metadata_json={"separate_open_budget": True},
            )
        )
        await session.commit()

    # This is the dangerous reachable case: the source/candidate digest stays
    # unchanged while both the fresh project and ambient context point to B.
    async with c.factory() as session:
        with bind_execution_scope(scope_b):
            fresh, current_context, _ = await current_service._inputs(
                session,
                c.settings,
                project_slug=c.project.slug,
                parent_artifact_id=c.outline.id,
                parent_materialization_workflow_id=c.owner.id,
                beat_artifact_id=c.beat.id,
            )
            assert fresh.metadata_json["execution_scope_id"] == scope_b
            assert content_hash(current_context) == content_hash(original_context)
            from bestseller.services.budget import execution_scope_block_code

            assert (
                execution_scope_block_code(await session.get(db.ExecutionBudgetModel, scope_b))
                is None
            )

    async def frozen_rows():
        async with c.factory() as session:
            return [
                deepcopy(list((await session.execute(select(*model.__table__.columns))).mappings()))
                for model in (
                    db.ProjectModel,
                    db.PlanningArtifactVersionModel,
                    db.ChapterModel,
                    db.SceneCardModel,
                    db.BookProductionControlModel,
                    db.ExecutionBudgetModel,
                    db.WorkflowRunModel,
                    db.LlmRunModel,
                    db.ExecutionOperationModel,
                    db.ExecutionAttemptModel,
                )
            ]

    before = await frozen_rows()
    dispatches_before = len(c.patch_calls)
    async with c.factory() as session:
        with bind_execution_scope(scope_b):
            with pytest.raises(ValueError, match="original_scope_changed"):
                await service.propose_full_outline_revision(
                    session,
                    c.settings,
                    project_slug=c.project.slug,
                    parent_artifact_id=c.outline.id,
                    parent_materialization_workflow_id=c.owner.id,
                    beat_artifact_id=c.beat.id,
                    actor="fixture operator",
                    authorization_ref="explicit-offline-fixture",
                    explicit_user_approval=True,
                )
    assert len(c.patch_calls) == dispatches_before
    assert await frozen_rows() == before
