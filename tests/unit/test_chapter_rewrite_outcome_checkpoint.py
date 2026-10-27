"""Rejected candidates survive caller checkpoints without reopening paid work."""

from copy import deepcopy
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.schema import CreateTable
from test_adopted_knowledge_lineage import _jsonb  # noqa: F401

from bestseller.infra.db.models import (
    ChapterDraftVersionModel,
    ChapterModel,
    LlmRunModel,
    ProjectModel,
    RewriteTaskModel,
    WorkflowRunModel,
)
from bestseller.services import llm
from bestseller.services.chapter_rewrite_outcome import retain_failed_chapter_rewrite_outcome
from bestseller.services.rewrite_patch import text_hash

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.fixture
async def db(tmp_path, monkeypatch):
    path = tmp_path / "rewrite_outcome_checkpoint_test.sqlite3"
    assert "test" in path.name
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    models = (
        ProjectModel,
        ChapterModel,
        ChapterDraftVersionModel,
        WorkflowRunModel,
        RewriteTaskModel,
        LlmRunModel,
    )
    async with engine.begin() as conn:
        for model in models:
            ddl = str(CreateTable(model.__table__).compile(dialect=conn.dialect))
            await conn.exec_driver_sql(ddl.replace("::jsonb", ""))
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def forbidden(*args, **kwargs):
        pytest.fail("terminal outcome retention must not call a provider")

    monkeypatch.setattr(llm, "complete_text", forbidden)
    async with factory() as session:
        project = ProjectModel(
            slug=f"outcome-{uuid4().hex}",
            title="test",
            genre="test",
            target_word_count=10000,
            target_chapters=3,
        )
        session.add(project)
        await session.flush()
        chapter = ChapterModel(
            project_id=project.id,
            chapter_number=2,
            chapter_goal="test",
            status="revision",
            production_state="review",
            metadata_json={
                "chapter_review_attempts_active": 2,
                "chapter_review_active_cycle": {"attempts_base": 1},
            },
        )
        session.add(chapter)
        await session.flush()
        current = ChapterDraftVersionModel(
            project_id=project.id,
            chapter_id=chapter.id,
            version_no=9,
            content_md="The preserved current chapter.",
            word_count=5,
            is_current=True,
            promotion_state="candidate",
            promotion_metadata={"protected": True},
        )
        workflow = WorkflowRunModel(
            project_id=project.id,
            workflow_type="chapter_pipeline",
            scope_type="chapter",
            scope_id=chapter.id,
            status="running",
            current_step="rewrite_chapter_v1",
            metadata_json={"execution_scope_id": "original-scope", "protected": [1, 2]},
        )
        session.add_all([current, workflow])
        await session.flush()
        task = RewriteTaskModel(
            project_id=project.id,
            trigger_type="chapter_review",
            trigger_source_id=chapter.id,
            rewrite_strategy="chapter_coherence_bridge_rewrite",
            status="pending",
            attempts=0,
            instructions="Repair a supported issue.",
            metadata_json={
                "draft_id": str(current.id),
                "parent_draft_id": str(current.id),
                "parent_draft_hash": text_hash(current.content_md),
                "attempt_index": 2,
                "attempt_budget": 3,
                "review_report_id": str(uuid4()),
            },
        )
        session.add(task)
        await session.commit()
        # These result writes are deliberately uncommitted when the helper runs.
        paid = LlmRunModel(
            project_id=project.id,
            workflow_run_id=workflow.id,
            logical_role="editor",
            provider="fixture",
            model_name="fixture",
            prompt_template="chapter_rewrite",
            prompt_hash="a" * 64,
            metadata_json={"paid_receipt_fixture": "immutable"},
        )
        session.add(paid)
        await session.flush()
        candidate = ChapterDraftVersionModel(
            project_id=project.id,
            chapter_id=chapter.id,
            version_no=12,
            content_md="A complete rejected candidate.",
            word_count=5,
            is_current=False,
            promotion_state="candidate",
            llm_run_id=paid.id,
        )
        session.add(candidate)
        await session.flush()
        task.status = "failed"
        task.attempts = 1
        task.error_log = "candidate rejected; current preserved"
        task.metadata_json = {
            **task.metadata_json,
            "candidate_chapter_draft_id": str(candidate.id),
            "candidate_chapter_draft_version_no": 12,
            "candidate_llm_run_id": str(paid.id),
            "candidate_body_sha256": text_hash(candidate.content_md),
            "preserved_current_chapter_draft_version_no": current.version_no,
            "preserved_current_chapter_draft_id": str(current.id),
            "candidate_duplicate_gate_findings": [
                {"code": "duplicate", "payload": {"span": [25, 26]}}
            ],
            "quality_gate_rejected_current_promotion": True,
        }
        yield session, factory, project, chapter, current, workflow, task, candidate, paid
    await engine.dispose()


async def retain(db):
    session, _, _, chapter, current, workflow, task, _, _ = db
    return await retain_failed_chapter_rewrite_outcome(
        session,
        workflow=workflow,
        chapter=chapter,
        current_draft=current,
        rewrite_task=task,
    )


async def test_caller_checkpoint_retains_paid_candidate_failed_task_and_blocked_workflow(db):
    session, factory, project, chapter, current, workflow, task, candidate, paid = db
    task_before = deepcopy(task.metadata_json)
    chapter_before = deepcopy(chapter.metadata_json)
    receipt = await retain(db)
    assert receipt["automatic_retry_authorized"] is False
    assert receipt["candidate_body_proof"] == "retained_hash_matched"
    assert receipt["candidate_llm_reference_matched"] is True
    assert receipt["candidate_body_sha256"] == text_hash(candidate.content_md)
    assert task.status == "failed" and task.attempts == 1
    assert task.metadata_json == task_before
    assert chapter.metadata_json == chapter_before
    assert workflow.metadata_json["execution_scope_id"] == "original-scope"
    # A second real connection proves the helper did not commit candidate/result writes.
    async with factory() as observer:
        assert await observer.get(ChapterDraftVersionModel, candidate.id) is None
        assert await observer.get(LlmRunModel, paid.id) is None
        assert (await observer.get(RewriteTaskModel, task.id)).status == "pending"
        assert (await observer.get(WorkflowRunModel, workflow.id)).status == "running"
    await session.commit()  # The pipeline checkpoint is explicitly caller-owned.
    async with factory() as reopened:
        saved_workflow = await reopened.get(WorkflowRunModel, workflow.id)
        saved_task = await reopened.get(RewriteTaskModel, task.id)
        saved_current = await reopened.get(ChapterDraftVersionModel, current.id)
        saved_candidate = await reopened.get(ChapterDraftVersionModel, candidate.id)
        saved_chapter = await reopened.get(ChapterModel, chapter.id)
        assert saved_workflow.status == "machine_blocked"
        assert saved_workflow.current_step == "machine_repair_required"
        assert saved_workflow.metadata_json["chapter_rewrite_outcomes"][str(task.id)] == receipt
        assert saved_task.status == "failed" and saved_task.attempts == 1
        assert saved_task.metadata_json == task_before
        assert saved_candidate.content_md == candidate.content_md and not saved_candidate.is_current
        assert saved_candidate.llm_run_id == paid.id
        assert saved_current.content_md == current.content_md and saved_current.is_current
        assert saved_current.promotion_metadata == {"protected": True}
        assert saved_chapter.production_state == "blocked"
        assert saved_chapter.metadata_json == chapter_before
        assert await reopened.scalar(select(func.count()).select_from(RewriteTaskModel)) == 1
        assert await reopened.scalar(select(func.count()).select_from(LlmRunModel)) == 1
        saved_project = await reopened.get(ProjectModel, project.id)
        assert saved_project.title == "test"


async def test_caller_rollback_is_not_turned_into_hidden_checkpoint(db):
    session, factory, _, chapter, current, workflow, task, candidate, paid = db
    ids = chapter.id, current.id, workflow.id, task.id, candidate.id, paid.id
    await retain(db)
    await session.rollback()
    async with factory() as reopened:
        assert (await reopened.get(WorkflowRunModel, ids[2])).status == "running"
        assert (await reopened.get(RewriteTaskModel, ids[3])).status == "pending"
        assert await reopened.get(ChapterDraftVersionModel, ids[4]) is None
        assert await reopened.get(LlmRunModel, ids[5]) is None


async def test_repeat_after_checkpoint_is_idempotent_and_preserves_unrelated_pending_work(db):
    session, factory, project, _chapter, _current, workflow, _task, _candidate, _paid = db
    first = await retain(db)
    await session.commit()
    await session.refresh(workflow)  # Exercise receipt serialization roundtrip.
    before = deepcopy(workflow.metadata_json)
    project.title = "Caller pending edit"
    second = await retain(db)
    assert second == first
    assert workflow.metadata_json == before
    assert project in session.dirty
    async with factory() as observer:
        assert (await observer.get(ProjectModel, project.id)).title == "test"
    await session.commit()
    async with factory() as observer:
        assert (await observer.get(ProjectModel, project.id)).title == "Caller pending edit"
        assert await observer.scalar(select(func.count()).select_from(RewriteTaskModel)) == 1


async def test_completed_result_does_not_block_or_write_outcome(db):
    _, _, _, chapter, _, workflow, task, _, _ = db
    task.status = "completed"
    before = deepcopy(workflow.metadata_json)
    assert await retain(db) is None
    assert workflow.status == "running"
    assert chapter.production_state == "review"
    assert workflow.metadata_json == before


@pytest.mark.parametrize(
    "invalid",
    [
        "task_target",
        "workflow_scope",
        "body",
        "parent",
        "candidate_missing",
        "candidate_current",
        "candidate_chapter",
        "candidate_version",
        "terminal_workflow",
        "unknown_task_status",
        "draft_alias",
        "preserved_version",
        "candidate_body",
        "candidate_llm",
        "candidate_hash_null",
    ],
)
async def test_incompatible_result_fails_closed_without_mutating_receipt_or_task(db, invalid):
    _, _, _, chapter, current, workflow, task, candidate, _ = db
    if invalid == "task_target":
        task.trigger_source_id = uuid4()
    elif invalid == "workflow_scope":
        workflow.scope_id = uuid4()
    elif invalid == "body":
        current.content_md += " changed"
    elif invalid == "parent":
        task.metadata_json = {**task.metadata_json, "parent_draft_id": str(uuid4())}
    elif invalid == "candidate_missing":
        task.metadata_json = {**task.metadata_json, "candidate_chapter_draft_id": str(uuid4())}
    elif invalid == "candidate_current":
        candidate.is_current = True
    elif invalid == "candidate_chapter":
        candidate.chapter_id = uuid4()
    elif invalid == "candidate_version":
        task.metadata_json = {**task.metadata_json, "candidate_chapter_draft_version_no": 13}
    elif invalid == "draft_alias":
        task.metadata_json = {**task.metadata_json, "draft_id": str(uuid4())}
    elif invalid == "preserved_version":
        task.metadata_json = {**task.metadata_json, "preserved_current_chapter_draft_version_no": 8}
    elif invalid == "candidate_body":
        candidate.content_md += " altered before first checkpoint"
    elif invalid == "candidate_llm":
        task.metadata_json = {**task.metadata_json, "candidate_llm_run_id": str(uuid4())}
    elif invalid == "candidate_hash_null":
        task.metadata_json = {**task.metadata_json, "candidate_body_sha256": None}
    elif invalid == "terminal_workflow":
        workflow.status = "completed"
    else:
        task.status = "pending"
    before = deepcopy(workflow.metadata_json), task.status, task.attempts, chapter.production_state
    with pytest.raises(ValueError, match="chapter_rewrite_outcome_"):
        await retain(db)
    assert (workflow.metadata_json, task.status, task.attempts, chapter.production_state) == before


async def test_changed_immutable_outcome_receipt_cannot_be_overwritten(db):
    _, _, _, _, _, workflow, task, _, _ = db
    await retain(db)
    changed = deepcopy(workflow.metadata_json)
    changed["chapter_rewrite_outcomes"][str(task.id)]["candidate_body_sha256"] = "0" * 64
    workflow.metadata_json = changed
    before = deepcopy(workflow.metadata_json)
    with pytest.raises(ValueError, match="chapter_rewrite_outcome_receipt_changed"):
        await retain(db)
    assert workflow.metadata_json == before


@pytest.mark.parametrize("missing", ["body_hash", "llm_reference", "both"])
async def test_missing_historical_proof_is_observed_only_without_backfilling_or_credit(db, missing):
    session, _, _, chapter, _, workflow, task, _, _ = db
    metadata = deepcopy(task.metadata_json)
    if missing in {"body_hash", "both"}:
        metadata.pop("candidate_body_sha256")
    if missing in {"llm_reference", "both"}:
        metadata.pop("candidate_llm_run_id")
    task.metadata_json = metadata
    before = deepcopy(metadata), task.attempts, deepcopy(chapter.metadata_json)
    receipt = await retain(db)
    assert receipt["candidate_body_proof"] == (
        "checkpoint_observation_only"
        if missing in {"body_hash", "both"}
        else "retained_hash_matched"
    )
    assert receipt["candidate_llm_reference_matched"] == (missing == "body_hash")
    assert receipt["automatic_retry_authorized"] is False
    assert workflow.status == "machine_blocked" and task.status == "failed"
    assert (task.metadata_json, task.attempts, chapter.metadata_json) == before
    await session.commit()
