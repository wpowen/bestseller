"""SQLite retained-result validation; no gateway, PG or new grade is involved."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.schema import CreateTable
from test_chapter_review_reconciliation import migration  # noqa: F401

from bestseller.infra.db.models import (
    ChapterDraftVersionModel,
    ChapterModel,
    PlanningArtifactVersionModel,
    QualityScoreModel,
    ReviewReportModel,
    RewriteTaskModel,
    SceneCardModel,
    WorkflowRunModel,
)
from bestseller.services import pipelines
from bestseller.services.chapter_review_preflight import validate_retained_review_before_processing
from bestseller.services.rewrite_patch import text_hash
from bestseller.settings import load_settings

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.fixture
async def proof_case(migration):  # noqa: F811
    rig = migration
    rig.wrong.is_current = False
    await rig.session.flush()
    rig.source.is_current = True
    task = RewriteTaskModel(
        project_id=rig.project.id,
        trigger_type="chapter_review",
        trigger_source_id=rig.chapter.id,
        rewrite_strategy="chapter_coherence_bridge_rewrite",
        status="pending",
        instructions="Proposal",
        metadata_json={
            "chapter_id": str(rig.chapter.id),
            "review_report_id": str(rig.report.id),
            "draft_id": str(rig.source.id),
            "parent_draft_id": str(rig.source.id),
            "parent_draft_hash": text_hash(rig.source.content_md),
        },
    )
    rig.session.add(task)
    await rig.session.flush()
    review_owner = WorkflowRunModel(
        project_id=rig.project.id,
        scope_type="chapter",
        scope_id=rig.chapter.id,
        workflow_type="chapter_pipeline",
        status="completed",
        metadata_json={},
    )
    rig.session.add(review_owner)
    await rig.session.flush()
    cycle = deepcopy(rig.original_cycle)
    cycle_id = pipelines._chapter_critic_cycle_id(
        rig.project.id, rig.chapter.id, cycle["source_body_hash"]
    )
    cycle.update(
        cycle_id=cycle_id,
        policy_source_hash="current-policy",
        review_workflow_run_id=str(review_owner.id),
    )
    cycle["attempts"][0].update(
        attempt=1,
        operation_namespace=f"chapter-critic:{cycle_id}:1",
        draft_id=str(rig.source.id),
        body_fingerprint=pipelines.chapter_review_body_fingerprint(rig.source.content_md),
    )
    cycle["attempts"][0]["rewrite_task_id"] = str(task.id)
    old = deepcopy(rig.original_cycle)
    old.update(
        cycle_id="old-recovery",
        source_draft_id=str(rig.wrong.id),
        source_body_hash=pipelines.chapter_review_body_fingerprint(rig.wrong.content_md)["digest"],
    )
    rig.chapter.metadata_json = {
        "chapter_review_active_cycle": old,
        "chapter_critic_cycles": {cycle_id: cycle, old["cycle_id"]: deepcopy(old)},
        "chapter_first_resume_existing_draft_id": str(rig.wrong.id),
    }
    await rig.session.commit()
    rig.task, rig.cycle_id, rig.review_owner = task, cycle_id, review_owner
    return rig


def change_cycle(rig, **updates):
    metadata = deepcopy(rig.chapter.metadata_json)
    metadata["chapter_critic_cycles"][rig.cycle_id].update(updates)
    rig.chapter.metadata_json = metadata


async def check(rig):
    return await validate_retained_review_before_processing(
        rig.session, SimpleNamespace(), rig.chapter
    )


async def snapshot(session):
    rows = {}
    for model in (
        ChapterModel,
        ChapterDraftVersionModel,
        ReviewReportModel,
        QualityScoreModel,
        RewriteTaskModel,
        WorkflowRunModel,
    ):
        rows[model.__tablename__] = {
            str(row.id): deepcopy(
                {column.key: getattr(row, column.key) for column in inspect(model).column_attrs}
            )
            for row in await session.scalars(
                select(model).execution_options(populate_existing=True)
            )
        }
    return rows


async def test_current_automatic_proof_preserves_stale_active_and_all_persisted_rows(proof_case):
    rig = proof_case
    before = await snapshot(rig.session)
    proof = await check(rig)
    assert proof == {
        "schema": "chapter-review-first-reuse.v1",
        "project_id": str(rig.project.id),
        "chapter_id": str(rig.chapter.id),
        "draft_id": str(rig.source.id),
        "body_sha256": pipelines.chapter_review_body_fingerprint(rig.source.content_md)["digest"],
        "cycle_id": rig.cycle_id,
        "policy_source_hash": "current-policy",
        "semantic_dependencies_sha256": "same-data",
        "report_id": str(rig.report.id),
        "quality_score_id": str(rig.quality.id),
        "task_id": str(rig.task.id),
        "verdict": "rewrite",
        "reuse_kind": "available_review",
    }
    assert await check(rig) == proof
    assert not rig.session.new and not rig.session.dirty and not rig.session.deleted
    assert rig.session.in_transaction()
    factory = async_sessionmaker(rig.session.bind, expire_on_commit=False)
    async with factory() as observer:
        assert await snapshot(observer) == before


@pytest.mark.parametrize("state", ["prepared", "running"])
async def test_incomplete_cycles_never_authorize_skipping_or_reset(proof_case, state):
    rig = proof_case
    change_cycle(rig, state=state, attempts=[])
    await rig.session.commit()
    before = await snapshot(rig.session)
    proof = await check(rig)
    assert proof["reuse_kind"] == "resume_pending_review"
    assert all(
        proof[key] is None for key in ("verdict", "report_id", "quality_score_id", "task_id")
    )
    assert await snapshot(rig.session) == before


@pytest.mark.parametrize("unknown", ["attempt", "result", "report", "evidence"])
async def test_unknown_is_not_reuse_authority(proof_case, unknown):
    rig = proof_case
    cycle = deepcopy(rig.chapter.metadata_json["chapter_critic_cycles"][rig.cycle_id])
    if unknown == "attempt":
        cycle["attempts"][-1]["outcome"] = "unknown"
    elif unknown == "result":
        cycle["attempts"][-1]["review_result"]["verdict"] = "unknown"
    elif unknown == "report":
        rig.report.verdict = "unknown"
    else:
        rig.report.structured_output = {
            **rig.report.structured_output,
            "critic_evidence": {"status": "evidence_invalid"},
        }
    change_cycle(rig, **cycle)
    await rig.session.commit()
    with pytest.raises(ValueError, match="chapter_critic_cycle_result_binding_mismatch"):
        await check(rig)


@pytest.mark.parametrize(
    "fault",
    [
        "cycle_id",
        "draft_id",
        "body",
        "policy",
        "semantic",
        "semantic_latch",
        "state",
        "attempts_type",
        "attempts_empty",
        "attempts_excess",
        "attempt_state",
        "outcome",
        "missing_report_id",
        "report_body",
        "report_target",
        "report_verdict",
        "quality_report",
        "quality_draft",
        "missing_task",
        "task_report",
        "task_chapter",
        "task_parent",
        "task_hash",
        "task_trigger",
        "cycles_type",
    ],
)
async def test_malformed_or_obsolete_evidence_fails_closed(proof_case, fault):
    rig = proof_case
    cycle = deepcopy(rig.chapter.metadata_json["chapter_critic_cycles"][rig.cycle_id])
    replacements = {
        "cycle_id": {"cycle_id": "forged"},
        "draft_id": {"source_draft_id": str(uuid4())},
        "body": {"source_body_hash": "0" * 64},
        "policy": {"policy_source_hash": "obsolete"},
        "semantic": {"semantic_dependencies_sha256": "changed"},
        "semantic_latch": {"semantic_dependency_change_detected": True},
        "state": {"state": "other"},
        "attempts_type": {"attempts": {}},
        "attempts_empty": {"attempts": []},
        "attempts_excess": {"attempts": cycle["attempts"] * 3},
    }
    cycle.update(replacements.get(fault, {}))
    if fault == "attempt_state":
        cycle["attempts"][-1]["state"] = "running"
    if fault == "outcome":
        cycle["attempts"][-1]["outcome"] = "anything"
    if fault == "missing_report_id":
        del cycle["attempts"][-1]["review_report_id"]
    if fault == "report_body":
        rig.report.structured_output = {**rig.report.structured_output, "draft_id": str(uuid4())}
    if fault == "report_target":
        rig.report.target_id = uuid4()
    if fault == "report_verdict":
        rig.report.verdict = "pass"
    if fault == "quality_report":
        rig.quality.review_report_id = uuid4()
    if fault == "quality_draft":
        rig.quality.chapter_draft_version_id = uuid4()
    if fault == "missing_task":
        cycle["attempts"][-1]["rewrite_task_id"] = str(uuid4())
    task_fields = {
        "task_report": "review_report_id",
        "task_chapter": "chapter_id",
        "task_parent": "parent_draft_id",
        "task_hash": "parent_draft_hash",
    }
    if fault in task_fields:
        rig.task.metadata_json = {**rig.task.metadata_json, task_fields[fault]: "changed"}
    if fault == "task_trigger":
        rig.task.trigger_source_id = uuid4()
    change_cycle(rig, **cycle)
    if fault == "cycles_type":
        rig.chapter.metadata_json = {
            **rig.chapter.metadata_json,
            "chapter_critic_cycles": ["wrong"],
        }
    await rig.session.commit()
    before = await snapshot(rig.session)
    with pytest.raises(ValueError):
        await check(rig)
    assert await snapshot(rig.session) == before


async def test_no_autoflush_or_commit_of_unrelated_caller_work(proof_case):
    rig = proof_case
    rig.task.instructions = "Uncommitted caller work"
    assert await check(rig)
    assert rig.task in rig.session.dirty
    factory = async_sessionmaker(rig.session.bind, expire_on_commit=False)
    async with factory() as observer:
        assert (await observer.get(RewriteTaskModel, rig.task.id)).instructions == "Proposal"
    await rig.session.rollback()


async def test_unflushed_target_body_is_rejected_without_overwriting_caller_work(proof_case):
    rig = proof_case
    changed = rig.source.content_md + " Not yet committed."
    rig.source.content_md = changed
    with pytest.raises(ValueError, match="chapter_critic_cycle_uncommitted_source"):
        await check(rig)
    assert rig.source.content_md == changed and rig.source in rig.session.dirty


async def test_ambiguous_current_draft_is_rejected(proof_case):
    rig = proof_case
    rig.wrong.is_current = True
    await rig.session.commit()
    with pytest.raises(ValueError, match="chapter_critic_cycle_source_mismatch"):
        await check(rig)


async def test_two_completed_rounds_reuse_final_available_result_without_new_attempt(proof_case):
    rig = proof_case
    cycle = deepcopy(rig.chapter.metadata_json["chapter_critic_cycles"][rig.cycle_id])
    first = deepcopy(cycle["attempts"][-1])
    first.update(outcome="unknown", rewrite_task_id=None)
    first["review_result"]["verdict"] = "unknown"
    cycle["attempts"] = [
        first,
        {
            **deepcopy(cycle["attempts"][-1]),
            "attempt": 2,
            "operation_namespace": f"chapter-critic:{rig.cycle_id}:2",
        },
    ]
    change_cycle(rig, **cycle)
    await rig.session.commit()
    before = deepcopy(rig.chapter.metadata_json)
    assert (await check(rig))["report_id"] == str(rig.report.id)
    assert rig.chapter.metadata_json == before


async def test_new_body_with_no_cycle_has_no_reuse_proof(proof_case):
    rig = proof_case
    rig.source.content_md += " The tide changes."
    await rig.session.commit()
    assert await check(rig) is None


async def test_explicit_same_body_recovery_does_not_authorize_another_automatic_cycle(proof_case):
    rig = proof_case
    active = deepcopy(rig.chapter.metadata_json["chapter_critic_cycles"][rig.cycle_id])
    active["attempts"][-1].update(
        attempt=1,
        operation_namespace=f"chapter-critic:{rig.cycle_id}:1",
    )
    active["cycle_id"] = "explicit-recovery"
    metadata = deepcopy(rig.chapter.metadata_json)
    metadata["chapter_review_active_cycle"] = active
    metadata["chapter_critic_cycles"][active["cycle_id"]] = deepcopy(active)
    rig.chapter.metadata_json = metadata
    await rig.session.commit()
    with pytest.raises(ValueError, match="chapter_critic_cycle_attempt_binding_mismatch"):
        await check(rig)


@pytest.fixture
async def active_recovery(proof_case, monkeypatch):
    """Create the custom attempt through the actual durable cycle producer."""
    from bestseller.domain.review import ChapterReviewResult

    rig = proof_case
    original_cycle = deepcopy(rig.chapter.metadata_json["chapter_critic_cycles"][rig.cycle_id])
    cycle_id = pipelines._chapter_critic_cycle_id(
        rig.project.id,
        rig.chapter.id,
        original_cycle["source_body_hash"],
        "current-policy",
    )
    prepared = {
        "cycle_id": cycle_id,
        "state": "prepared",
        "source_draft_id": str(rig.source.id),
        "source_body_hash": original_cycle["source_body_hash"],
        "policy_source_hash": "current-policy",
        "previous_workflow_run_id": str(uuid4()),
    }
    rig.chapter.metadata_json = {
        "chapter_critic_cycles": {},
        "chapter_review_recovery_cycles": [deepcopy(prepared)],
    }
    pipelines._store_chapter_review_recovery_cycle(rig.chapter, prepared)
    workflow = WorkflowRunModel(
        project_id=rig.project.id,
        scope_type="chapter",
        scope_id=rig.chapter.id,
        workflow_type="chapter_pipeline",
        status="running",
        metadata_json={},
    )
    rig.session.add(workflow)
    await rig.session.commit()
    local_reviewer = AsyncMock(
        return_value=(
            ChapterReviewResult.model_validate(original_cycle["attempts"][-1]["review_result"]),
            rig.report,
            rig.quality,
            rig.task,
        )
    )
    monkeypatch.setattr(pipelines, "review_chapter_draft", local_reviewer)
    await pipelines._review_chapter_with_evidence_retry(
        rig.session,
        SimpleNamespace(),
        rig.project.slug,
        rig.chapter.chapter_number,
        workflow=workflow,
        draft=rig.source,
        cycle_id=cycle_id,
    )
    produced = deepcopy(rig.chapter.metadata_json["chapter_critic_cycles"][cycle_id])
    assert produced["attempts"][-1]["operation_namespace"] == f"chapter-critic:{cycle_id}:1"
    assert produced["review_workflow_run_id"] == str(workflow.id)
    assert workflow.metadata_json["critic_cycle_id"] == cycle_id
    assert workflow.metadata_json["critic_evidence_attempts"] == produced["attempts"]
    pipelines._store_chapter_review_recovery_cycle(rig.chapter, produced)
    await rig.session.commit()
    rig.cycle_id, rig.workflow, rig.local_reviewer = cycle_id, workflow, local_reviewer
    return rig


async def test_producer_completed_matching_active_recovery_returns_readonly_proof_and_replays(
    active_recovery,
):
    rig = active_recovery
    before = await snapshot(rig.session)
    first = await check(rig)
    assert first["cycle_id"] == rig.cycle_id and first["task_id"] == str(rig.task.id)
    assert first["draft_id"] == str(rig.source.id)
    assert await check(rig) == first
    result, report, quality, task = await pipelines._review_chapter_with_evidence_retry(
        rig.session,
        SimpleNamespace(),
        rig.project.slug,
        rig.chapter.chapter_number,
        workflow=SimpleNamespace(id=uuid4(), metadata_json={}),
        draft=rig.source,
    )
    assert result.verdict == "rewrite" and report.id == rig.report.id
    assert quality.id == rig.quality.id and task.id == rig.task.id
    assert rig.local_reviewer.await_count == 1  # Only the fixture producer, never reuse.
    assert await snapshot(rig.session) == before


@pytest.mark.parametrize("fault", ["namespace", "number", "draft", "body", "cycle_id"])
async def test_active_recovery_wrong_cycle_bound_attempt_is_rejected(active_recovery, fault):
    rig = active_recovery
    cycle = deepcopy(rig.chapter.metadata_json["chapter_critic_cycles"][rig.cycle_id])
    item = cycle["attempts"][-1]
    if fault == "namespace":
        automatic = pipelines._chapter_critic_cycle_id(
            rig.project.id,
            rig.chapter.id,
            cycle["source_body_hash"],
        )
        item["operation_namespace"] = f"chapter-critic:{automatic}:1"
    elif fault == "number":
        item["attempt"] = 2
    elif fault == "draft":
        item["draft_id"] = str(uuid4())
    elif fault == "body":
        item["body_fingerprint"] = pipelines.chapter_review_body_fingerprint("Other full body")
    else:
        item["cycle_id"] = "foreign-cycle"
    change_cycle(rig, **cycle)
    await rig.session.commit()
    before = await snapshot(rig.session)
    with pytest.raises(ValueError, match="chapter_critic_cycle_attempt_binding_mismatch"):
        await check(rig)
    assert await snapshot(rig.session) == before


@pytest.mark.parametrize("status", ["failed", "completed", "running", "queued"])
async def test_non_pending_task_never_authorizes_a_new_rewrite(proof_case, status):
    rig = proof_case
    rig.task.status = status
    await rig.session.commit()
    with pytest.raises(ValueError, match="chapter_critic_cycle_rewrite_not_pending"):
        await check(rig)
    assert rig.task.status == status and rig.task.attempts == 0


async def test_pass_result_needs_no_rewrite_task(proof_case):
    rig = proof_case
    cycle = deepcopy(rig.chapter.metadata_json["chapter_critic_cycles"][rig.cycle_id])
    cycle["attempts"][-1].pop("rewrite_task_id")
    cycle["attempts"][-1]["review_result"]["verdict"] = "pass"
    rig.report.verdict = "pass"
    change_cycle(rig, **cycle)
    await rig.session.commit()
    proof = await check(rig)
    assert proof["verdict"] == "pass" and proof["task_id"] is None


async def test_available_rewrite_without_task_cannot_authorize_preprocessing_skip(proof_case):
    rig = proof_case
    cycle = deepcopy(rig.chapter.metadata_json["chapter_critic_cycles"][rig.cycle_id])
    cycle["attempts"][-1].pop("rewrite_task_id")
    change_cycle(rig, **cycle)
    await rig.session.commit()
    with pytest.raises(ValueError, match="chapter_critic_cycle_rewrite_not_pending"):
        await check(rig)


@pytest.fixture
async def entry_case(proof_case, monkeypatch):
    """Use the real entry/preflight and DB; stop before opening a new workflow."""
    rig = proof_case
    from bestseller.services.invariants import invariants_to_dict, seed_invariants

    # The common source entry now performs its own fresh source lookup. This
    # fixture has no authored plan, represented by a real empty physical table.
    connection = await rig.session.connection()
    assert connection.dialect.name == "sqlite"
    ddl = str(CreateTable(PlanningArtifactVersionModel.__table__).compile(
        dialect=connection.dialect))
    await connection.exec_driver_sql(ddl.replace("::jsonb", ""))
    assert await rig.session.scalar(select(PlanningArtifactVersionModel.id)) is None

    # This fixture starts after technical initialization, before body work.
    # Missing-contract initialization has its own real DB regression suite.
    rig.project.invariants_json = invariants_to_dict(seed_invariants(
        project_id=rig.project.id, language=rig.project.language, words_per_chapter=2600))
    rig.session.add(
        SceneCardModel(
            project_id=rig.project.id,
            chapter_id=rig.chapter.id,
            scene_number=1,
            scene_type="plot",
            purpose={"goal": "Return the sealed chart"},
            title="Harbour",
        )
    )
    await rig.session.commit()
    monkeypatch.setattr(pipelines, "get_project_by_slug", AsyncMock(return_value=rig.project))
    monkeypatch.setattr(pipelines, "_enforce_truth_version_guard", AsyncMock())
    # This orchestration fixture starts at chapter 2 without a chapter-1 row.
    # Exact prefix knowledge recovery is covered with real SQLite separately.
    from bestseller.services import adopted_knowledge_resume
    monkeypatch.setattr(adopted_knowledge_resume, "resume_project_adopted_knowledge",
        AsyncMock(return_value={}))
    # This fixture deliberately uses "current-policy" and a stubbed paid-input
    # verifier. Native historical-policy admission is a separate boundary,
    # covered by test_native_chapter_review_resume with actual source/ledger
    # fixtures; this test continues through the real retained-result preflight.
    from bestseller.services import chapter_review_reconciliation
    rig.native_admission = AsyncMock(return_value={"status": "unchanged"})
    monkeypatch.setattr(chapter_review_reconciliation, "prepare_native_review_resume",
        rig.native_admission)
    return rig


class _EntryBoundary(BaseException):
    """Stop orchestration before any new workflow or paid work is created."""


async def test_actual_entry_skips_optional_backfills_and_carries_exact_proof(
    entry_case, monkeypatch
):
    rig = entry_case
    expected = await check(rig)
    backfills = []
    for name in (
        "_ensure_emotion_kernel_backfill_for_pipeline",
        "_ensure_public_emotion_kernel_backfill_for_pipeline",
        "_ensure_entry_system_backfill_for_pipeline",
    ):
        spy = AsyncMock(side_effect=AssertionError("retained review reached paid backfill"))
        monkeypatch.setattr(pipelines, name, spy)
        backfills.append(spy)
    captured = []

    async def workflow_boundary(session, **kwargs):
        captured.append(kwargs)
        raise _EntryBoundary

    monkeypatch.setattr(pipelines, "create_workflow_run", workflow_boundary)
    before = await snapshot(rig.session)
    with pytest.raises(_EntryBoundary):
        await pipelines.run_chapter_pipeline.__wrapped__(
            rig.session,
            load_settings(env={}),
            rig.project.slug,
            rig.chapter.chapter_number,
            chapter_first=True,
            supersede_pending_rewrites=False,
            resume_existing_draft_only=False,
        )
    assert captured[0]["metadata"]["retained_review_first_reuse"] == expected
    rig.native_admission.assert_awaited_once()
    assert "review_recovery_source" not in captured[0]["metadata"]
    assert all(spy.await_count == 0 for spy in backfills)
    assert await snapshot(rig.session) == before


async def test_actual_entry_new_body_has_no_proof_and_keeps_normal_backfill(
    entry_case, monkeypatch
):
    rig = entry_case
    rig.source.content_md += " The navigator opens a different sealed chart."
    await rig.session.commit()
    backfill = AsyncMock(side_effect=_EntryBoundary)
    monkeypatch.setattr(pipelines, "_ensure_emotion_kernel_backfill_for_pipeline", backfill)
    workflow = AsyncMock(side_effect=AssertionError("must stop before workflow creation"))
    monkeypatch.setattr(pipelines, "create_workflow_run", workflow)
    with pytest.raises(_EntryBoundary):
        await pipelines.run_chapter_pipeline.__wrapped__(
            rig.session,
            load_settings(env={}),
            rig.project.slug,
            rig.chapter.chapter_number,
            chapter_first=True,
            supersede_pending_rewrites=False,
        )
    backfill.assert_awaited_once()
    rig.native_admission.assert_awaited_once()
    workflow.assert_not_awaited()


async def test_actual_durable_consumer_returns_same_pending_task_without_paid_review(
    proof_case,
    monkeypatch,
):
    rig = proof_case
    before = await snapshot(rig.session)
    paid_review = AsyncMock(side_effect=AssertionError("retained cycle must not buy a critic"))
    monkeypatch.setattr(pipelines, "review_chapter_draft", paid_review)
    result, report, quality, task = await pipelines._review_chapter_with_evidence_retry(
        rig.session,
        SimpleNamespace(),
        rig.project.slug,
        rig.chapter.chapter_number,
        workflow=SimpleNamespace(id=uuid4(), metadata_json={}),
        draft=rig.source,
    )
    assert result.verdict == "rewrite" and report.id == rig.report.id
    assert quality.id == rig.quality.id and task.id == rig.task.id and task.status == "pending"
    paid_review.assert_not_awaited()
    assert await snapshot(rig.session) == before


def unknown_attempt(rig, number):
    item = deepcopy(
        rig.chapter.metadata_json["chapter_critic_cycles"][rig.cycle_id]["attempts"][-1]
    )
    item.update(
        attempt=number,
        operation_namespace=f"chapter-critic:{rig.cycle_id}:{number}",
        outcome="unknown",
        rewrite_task_id=None,
    )
    item["review_result"]["verdict"] = "unknown"
    return item


async def test_terminal_unknown_proof_consumes_original_receipts_without_credit_or_prose(
    proof_case,
    monkeypatch,
):
    rig = proof_case
    attempts = [unknown_attempt(rig, 1), unknown_attempt(rig, 2)]
    change_cycle(rig, attempts=attempts)
    await rig.session.commit()
    before = await snapshot(rig.session)
    proof = await check(rig)
    assert proof["reuse_kind"] == "consume_retained_unknown"
    assert proof["verdict"] == "unknown" and proof["task_id"] is None
    assert proof["report_id"] == str(rig.report.id)
    assert proof["quality_score_id"] == str(rig.quality.id)
    paid = AsyncMock(side_effect=AssertionError("terminal unknown cannot buy a review"))
    monkeypatch.setattr(pipelines, "review_chapter_draft", paid)
    result, report, quality, task = await pipelines._review_chapter_with_evidence_retry(
        rig.session,
        SimpleNamespace(),
        rig.project.slug,
        rig.chapter.chapter_number,
        workflow=SimpleNamespace(id=uuid4(), metadata_json={}),
        draft=rig.source,
    )
    assert result.verdict == "unknown" and task is None
    assert report.id == rig.report.id and quality.id == rig.quality.id
    paid.assert_not_awaited()
    assert await snapshot(rig.session) == before


@pytest.mark.parametrize("state", ["prepared", "running"])
@pytest.mark.parametrize("cursor", ["empty", "first_prepared", "first_running", "second_running"])
async def test_pending_proof_resumes_original_workflow_namespace_and_finite_cursor(
    proof_case,
    monkeypatch,
    state,
    cursor,
):
    from bestseller.domain.review import ChapterReviewResult
    from bestseller.services import llm

    rig = proof_case
    existing = deepcopy(
        rig.chapter.metadata_json["chapter_critic_cycles"][rig.cycle_id]["attempts"][-1]
    )
    number = 2 if cursor == "second_running" else 1
    pending = {
        "attempt": number,
        "state": "prepared" if cursor == "first_prepared" else "running",
        "operation_namespace": f"chapter-critic:{rig.cycle_id}:{number}",
    }
    attempts = [] if cursor == "empty" else [pending]
    if cursor == "second_running":
        attempts = [unknown_attempt(rig, 1), pending]
    change_cycle(rig, state=state, attempts=attempts)
    await rig.session.commit()
    before = await snapshot(rig.session)
    proof = await check(rig)
    assert proof["reuse_kind"] == "resume_pending_review"
    assert all(
        proof[key] is None for key in ("verdict", "report_id", "quality_score_id", "task_id")
    )
    assert await snapshot(rig.session) == before
    dispatched = []

    async def local_reviewer(*args, **kwargs):
        dispatched.append((kwargs["workflow_run_id"], llm._semantic_operation_namespace.get()[0]))
        return (
            ChapterReviewResult.model_validate(existing["review_result"]),
            rig.report,
            rig.quality,
            rig.task,
        )

    monkeypatch.setattr(pipelines, "review_chapter_draft", local_reviewer)
    caller = WorkflowRunModel(
        project_id=rig.project.id,
        scope_type="chapter",
        scope_id=rig.chapter.id,
        workflow_type="chapter_pipeline",
        status="running",
        metadata_json={},
    )
    rig.session.add(caller)
    await rig.session.commit()
    result = await pipelines._review_chapter_with_evidence_retry(
        rig.session,
        SimpleNamespace(),
        rig.project.slug,
        rig.chapter.chapter_number,
        workflow=caller,
        draft=rig.source,
    )
    assert result[3].id == rig.task.id and result[0].verdict == "rewrite"
    assert dispatched == [(rig.review_owner.id, f"chapter-critic:{rig.cycle_id}:{number}")]
    saved = rig.chapter.metadata_json["chapter_critic_cycles"][rig.cycle_id]
    assert saved["review_workflow_run_id"] == str(rig.review_owner.id)
    assert saved["state"] == "completed" and len(saved["attempts"]) == number <= 2
    assert saved["attempts"][-1]["operation_namespace"] == pending["operation_namespace"]
    assert saved["attempts"][:-1] == attempts[:-1]
    assert rig.source.is_current and rig.source.promotion_state == "candidate"


@pytest.mark.parametrize(
    "fault",
    [
        "missing_id",
        "missing_row",
        "foreign_project",
        "foreign_chapter",
        "wrong_scope",
        "wrong_type",
        "missing_namespace",
        "wrong_namespace",
        "wrong_number",
        "nonfinal_pending",
        "pending_claims_grade",
    ],
)
@pytest.mark.parametrize("state", ["prepared", "running"])
async def test_partial_cycle_missing_or_wrong_owner_and_namespace_fails_before_dispatch(
    proof_case,
    fault,
    state,
):
    rig = proof_case
    pending = {
        "attempt": 1,
        "state": "running",
        "operation_namespace": f"chapter-critic:{rig.cycle_id}:1",
    }
    updates = {"state": state, "attempts": [pending]}
    if fault == "missing_id":
        updates["review_workflow_run_id"] = None
    if fault == "missing_row":
        updates["review_workflow_run_id"] = str(uuid4())
    if fault == "foreign_project":
        rig.review_owner.project_id = uuid4()
    if fault == "foreign_chapter":
        rig.review_owner.scope_id = uuid4()
    if fault == "wrong_scope":
        rig.review_owner.scope_type = "project"
    if fault == "wrong_type":
        rig.review_owner.workflow_type = "planning"
    if fault == "missing_namespace":
        del pending["operation_namespace"]
    if fault == "wrong_namespace":
        pending["operation_namespace"] = "chapter-critic:other:1"
    if fault == "wrong_number":
        pending["attempt"] = 2
    if fault == "nonfinal_pending":
        updates["attempts"] = [pending, deepcopy(pending)]
    if fault == "pending_claims_grade":
        pending["review_report_id"] = str(rig.report.id)
    change_cycle(rig, **updates)
    await rig.session.commit()
    before = await snapshot(rig.session)
    with pytest.raises(ValueError):
        await check(rig)
    assert await snapshot(rig.session) == before


@pytest.mark.parametrize(
    "fault", ["missing_report", "wrong_quality", "wrong_body", "has_task", "one_round"]
)
async def test_terminal_unknown_needs_full_retained_proof(proof_case, fault):
    rig = proof_case
    attempts = [unknown_attempt(rig, 1), unknown_attempt(rig, 2)]
    if fault == "missing_report":
        del attempts[-1]["review_report_id"]
    if fault == "wrong_quality":
        attempts[-1]["quality_score_id"] = str(uuid4())
    if fault == "wrong_body":
        attempts[-1]["body_fingerprint"] = {}
    if fault == "has_task":
        attempts[-1]["rewrite_task_id"] = str(rig.task.id)
    if fault == "one_round":
        attempts.pop()
    change_cycle(rig, attempts=attempts)
    await rig.session.commit()
    before = await snapshot(rig.session)
    with pytest.raises(ValueError):
        await check(rig)
    assert await snapshot(rig.session) == before
