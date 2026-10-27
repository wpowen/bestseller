"""Real SQLite workflow creation/scope bootstrap, with a forbidden model boundary."""

from copy import deepcopy
from types import SimpleNamespace

import pytest
from sqlalchemy import JSON, ForeignKeyConstraint, MetaData, select
from sqlalchemy.dialects.postgresql import JSONB
from test_chapter_outline_readiness import rig as rig
from test_chapter_outline_readiness import sqlite_identity_store as sqlite_identity_store

from bestseller.domain.enums import WorkflowStatus
from bestseller.infra.db.models import ChapterModel, WorkflowRunModel
from bestseller.services import chapter_outline_readiness as service
from bestseller.services import planning_continuity_context, workflows
from bestseller.services.budget import bind_execution_scope
from bestseller.services.prompt_source_policy import content_hash
from bestseller.settings import load_settings

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


class ForbiddenProviderError(AssertionError):
    """Reached a model boundary after checkpointing, never dispatched."""


@pytest.fixture
async def bootstrap(sqlite_identity_store, rig, monkeypatch):
    factory, wrap = sqlite_identity_store
    async with factory() as session:
        engine = session.bind
    table = ChapterModel.__table__.to_metadata(MetaData())
    for column in table.c:
        if isinstance(column.type, JSONB):
            column.type = JSON()
    for constraint in list(table.constraints):
        if isinstance(constraint, ForeignKeyConstraint):
            table.constraints.remove(constraint)
    async with engine.begin() as connection:
        await connection.run_sync(table.create)
    # Keep the real identity selection, workflow producer and commit path.
    monkeypatch.setattr(service, "create_workflow_run", workflows.create_workflow_run)

    async def continuity(_session, *, project_id, before_chapter):
        assert project_id == rig.project.id and before_chapter == 3
        return {
            "context_hash": content_hash({"project_id": str(project_id), "before": 3}),
            "project_id": str(project_id),
            "before_chapter": 3,
            "recent_adopted_prose": [{"chapter_number": 2, "body": "The parcel was delivered."}],
        }

    monkeypatch.setattr(
        planning_continuity_context, "build_planning_continuity_context", continuity
    )
    boundaries = []

    async def forbidden(_session, _settings, request):
        # The write must be visible to another real SQLite session before any
        # provider dispatch, including when initial metadata was runtime-only.
        async with factory() as observer:
            saved = await observer.get(WorkflowRunModel, request.workflow_run_id)
            checkpoint = deepcopy(saved.metadata_json)
        assert checkpoint["execution_scope_id"] == f"project:{rig.project.id}"
        assert checkpoint["identity"]["from_chapter"] == 3
        assert checkpoint["chapter_numbers"] == [3]
        assert checkpoint["candidates"][0]["candidate"]["chapters"][0]["chapter_number"] == 3
        assert checkpoint["phase"] == "continuation_rebase"
        boundaries.append((request, checkpoint))
        raise ForbiddenProviderError("No provider may execute")

    monkeypatch.setattr(service, "complete_text", forbidden)
    monkeypatch.setattr(service, "judge_outline_commercial_readiness", forbidden)
    return SimpleNamespace(
        factory=factory,
        wrap=wrap,
        rig=rig,
        boundaries=boundaries,
        scope=f"project:{rig.project.id}",
    )


async def approve(case, session, **kw):
    with bind_execution_scope(case.scope):
        return await service.approve_chapter_outline_prefix(
            case.wrap(session),
            load_settings(env={}),
            project=case.rig.project,
            source_payload=case.rig.source,
            from_chapter=3,
            through_chapter=3,
            requested_by="offline-prefix-test",
            **kw,
        )


async def create(case, session, metadata=None):
    with bind_execution_scope(case.scope):
        workflow = await workflows.create_workflow_run(
            session,
            project_id=case.rig.project.id,
            workflow_type=service.WORKFLOW_TYPE,
            status=WorkflowStatus.RUNNING,
            scope_type="project",
            scope_id=case.rig.project.id,
            requested_by="offline-test",
            metadata=metadata,
        )
    await session.commit()
    return workflow


async def test_real_create_under_scope_fresh_approve_reaches_saved_initialization(bootstrap):
    case = bootstrap
    before = deepcopy(case.rig.source)
    async with case.factory() as session:
        with pytest.raises(ForbiddenProviderError):
            await approve(case, session)
    assert len(case.boundaries) == 1
    checkpoint = case.boundaries[0][1]
    assert checkpoint["source_payload"] == before == case.rig.source
    assert checkpoint["source_record"]["candidate"] == before
    assert checkpoint["patch_attempts"] == []
    assert len(checkpoint["candidates"]) == 1
    assert "completion" not in checkpoint["continuation_rebase"]
    async with case.factory() as observer:
        rows = list(await observer.scalars(select(WorkflowRunModel)))
        assert len(rows) == 1
        assert rows[0].idempotency_key == content_hash(checkpoint["identity"])


async def test_runtime_unknown_metadata_is_preserved_through_actual_approval(bootstrap):
    case = bootstrap
    runtime = {
        "opaque_runtime": {"trace": ["first", {"second": True}]},
        "operator_annotation": "keep",
    }
    async with case.factory() as session:
        workflow = await create(case, session, runtime)
        assert workflow.metadata_json == {**runtime, "execution_scope_id": case.scope}
        with pytest.raises(ForbiddenProviderError):
            await approve(case, session, workflow_run_id=workflow.id)
    checkpoint = case.boundaries[0][1]
    for key, value in runtime.items():
        assert checkpoint[key] == value
    assert checkpoint["execution_scope_id"] == case.scope


async def test_actual_resume_is_idempotent_and_retains_previous_source_candidates_and_counts(
    bootstrap,
):
    case = bootstrap
    async with case.factory() as session:
        with pytest.raises(ForbiddenProviderError):
            await approve(case, session)
        workflow = await session.scalar(select(WorkflowRunModel))
        metadata = deepcopy(workflow.metadata_json)
        metadata.update(paid_call_count=147, retained_paid_receipts=[{"sha": "original-paid-root"}])
        # These historical consumed slots must not disappear during resume.
        metadata["patch_attempts"] = [
            {
                "status": "policy_superseded",
                "consumed": True,
                "completion": {"content": "historical raw response", "finish_reason": "stop"},
            }
        ]
        workflow.metadata_json = metadata
        await session.commit()
        wid, key = workflow.id, workflow.idempotency_key
        with pytest.raises(ForbiddenProviderError):
            await approve(case, session)
        await session.refresh(workflow)
        after = workflow.metadata_json
        for field in (
            "identity",
            "source_payload",
            "source_record",
            "candidates",
            "patch_attempts",
            "paid_call_count",
            "retained_paid_receipts",
            "execution_scope_id",
        ):
            assert after[field] == metadata[field]
        assert workflow.id == wid and workflow.idempotency_key == key
        assert len(list(await session.scalars(select(WorkflowRunModel)))) == 1
    assert len(case.boundaries) == 2
    assert case.boundaries[0][0].workflow_run_id == case.boundaries[1][0].workflow_run_id


@pytest.mark.parametrize(
    "partial",
    [
        {"identity": None},
        {"identity": {}},
        {"identity": []},
        {"phase": "validate"},
        {"schema": service.SCHEMA},
        {"candidates": []},
        {"patch_attempts": []},
        {"source_payload": {"raw": "must not overwrite"}},
        {"binding": {}},
        {"continuation_rebase": {"completion": {"content": "retained raw"}}},
    ],
)
async def test_partial_protocol_is_rejected_without_reset_or_provider(bootstrap, partial):
    case = bootstrap
    async with case.factory() as session:
        workflow = await create(case, session, {"unknown_runtime": ["keep"], **deepcopy(partial)})
        original = deepcopy(workflow.metadata_json)
        with pytest.raises(
            service.OutlinePrefixReadinessError,
            match=r"prefix_protocol_(identity_invalid|state_incomplete)",
        ):
            await approve(case, session, workflow_run_id=workflow.id)
        await session.refresh(workflow)
        assert workflow.metadata_json == original
    assert not case.boundaries
