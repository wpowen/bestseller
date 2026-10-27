"""Explicit waiting policy migration with real SQLite and actual semantic hashes."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import event, inspect, text
from sqlalchemy.schema import CreateTable
from test_adopted_knowledge_lineage import db as db
from test_chapter_critic_semantic_dependencies import semantic_db as semantic_db

from bestseller.domain.review import ChapterReviewResult, ChapterReviewScores
from bestseller.infra.db.models import (
    BookProductionControlModel,
    ChapterDraftVersionModel,
    ExecutionAttemptModel,
    ExecutionBudgetModel,
    ExecutionOperationModel,
    LlmRunModel,
    QualityScoreModel,
    ReviewReportModel,
    RewriteTaskModel,
    WorkflowRunModel,
)
from bestseller.services import feedback, llm, pipelines
from bestseller.services.budget import bind_execution_scope
from bestseller.services.chapter_review_preflight import (
    validate_retained_review_before_processing,
    validate_waiting_review_policy_source,
)
from bestseller.services.chapter_review_prerequisite import (
    WAITING,
    prerequisite_wait_receipt,
    wait_binding,
)
from bestseller.settings import load_settings

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.fixture
async def waiting(semantic_db, monkeypatch, request):
    session, project, chapter, scene, design, prior = semantic_db
    for model in (
        BookProductionControlModel,
        ExecutionBudgetModel,
        ExecutionOperationModel,
        ExecutionAttemptModel,
        WorkflowRunModel,
        LlmRunModel,
        RewriteTaskModel,
    ):
        ddl = str(
            CreateTable(model.__table__, if_not_exists=True).compile(dialect=session.bind.dialect)
        )
        await session.execute(text(ddl.replace("::jsonb", "")))
    title, genre, body = getattr(
        request,
        "param",
        (
            "The Observatory Ledger",
            "science-fiction",
            "The surveyor seals the instrument and chooses the ground antenna.",
        ),
    )
    project.title, project.genre = title, genre
    scope = f"conception-continuation:{uuid4()}:{uuid4()}"
    project.metadata_json = {**(project.metadata_json or {}), "execution_scope_id": scope}
    old = ChapterDraftVersionModel(
        project_id=project.id,
        chapter_id=chapter.id,
        version_no=1,
        content_md="A distinct retained earlier body.",
        is_current=False,
        promotion_state="candidate",
    )
    draft = ChapterDraftVersionModel(
        project_id=project.id,
        chapter_id=chapter.id,
        version_no=2,
        content_md=body,
        is_current=True,
        promotion_state="candidate",
    )
    owner = WorkflowRunModel(
        project_id=project.id,
        scope_type="chapter",
        scope_id=chapter.id,
        workflow_type="chapter_pipeline",
        status="machine_blocked",
        current_step=WAITING,
    )
    old_owner = WorkflowRunModel(
        project_id=project.id,
        scope_type="chapter",
        scope_id=chapter.id,
        workflow_type="chapter_pipeline",
        status="failed",
        current_step="review_chapter",
        error_message="workflow_failed",
        metadata_json={"execution_scope_id": scope},
    )
    control = BookProductionControlModel(
        project_id=project.id, desired_state="run", command_serial=8
    )
    budget = ExecutionBudgetModel(
        scope_id=scope,
        status="active",
        max_calls=64,
        max_tokens=1000000,
        calls_used=13,
        tokens_charged=42000,
        deadline_at=datetime.now(UTC) - timedelta(days=1),
        metadata_json={
            "provider_authorization": {
                "mode": "completion_bound",
                "status": "active",
                "provider": "minimax",
                "authorization_ref": "original-human-completion",
                "objective": "Finish retained body",
                "models": ["openai/MiniMax-M3"],
                "endpoint_hosts": ["api.minimaxi.com"],
                "aggregate_max_calls": None,
                "aggregate_max_tokens": None,
                "aggregate_deadline_at": None,
            }
        },
    )
    operation = ExecutionOperationModel(
        key="5" * 64,
        scope_id=scope,
        input_hash="6" * 64,
        status="completed",
        owner="retained",
        lease_until=datetime.now(UTC) - timedelta(minutes=1),
    )
    physical = ExecutionAttemptModel(
        id="settled-prior",
        operation_key=operation.key,
        scope_id=scope,
        input_hash=operation.input_hash,
        status="completed",
        model_name="fixture",
        reserved_tokens=100,
        actual_tokens=50,
        created_at=datetime.now(UTC),
    )
    session.add_all([old, draft, owner, old_owner, control, budget, operation, physical])
    await session.flush()
    settings = load_settings(env={})
    semantic = await pipelines._chapter_critic_semantic_dependencies(session, settings, chapter)
    fingerprint = pipelines.chapter_review_body_fingerprint(body)
    key = pipelines._chapter_critic_cycle_id(project.id, chapter.id, fingerprint["digest"])
    result = ChapterReviewResult(
        verdict="unknown",
        severity_max="critical",
        scores=ChapterReviewScores(**dict.fromkeys(ChapterReviewScores.model_fields, 0.0)),
    )
    report = ReviewReportModel(
        project_id=project.id,
        target_type="chapter",
        target_id=chapter.id,
        reviewer_type="rule-based-critic",
        verdict="unknown",
        structured_output={
            **result.model_dump(mode="json"),
            "draft_id": str(draft.id),
            "reviewed_body_fingerprint": fingerprint,
            "critic_evidence": {"status": "unavailable", "passed": False},
            "primary_submission_original": None,
            "primary_submission_receipt": None,
            "evidence_summary": {
                "primary_review_not_dispatched": {
                    "provider_calls": 0,
                    "reason": "required_judge_evidence_unavailable",
                    "body_fingerprint": fingerprint,
                }
            },
        },
    )
    session.add(report)
    await session.flush()
    quality = QualityScoreModel(
        project_id=project.id,
        target_type="chapter",
        target_id=chapter.id,
        chapter_draft_version_id=draft.id,
        review_report_id=report.id,
        score_overall=0,
        is_current=True,
    )
    session.add(quality)
    await session.flush()
    entry = {"attempt": 1, "state": WAITING, "operation_namespace": f"chapter-critic:{key}:1"}
    cycle = {
        "schema_version": "chapter-critic-cycle.v1",
        "cycle_id": key,
        "state": WAITING,
        "source_draft_id": str(draft.id),
        "source_body_hash": fingerprint["digest"],
        "review_workflow_run_id": str(owner.id),
        "policy_source_hash": "1" * 64,
        "semantic_dependencies_sha256": semantic,
        "attempts": [entry],
    }
    binding = wait_binding(
        cycle, entry, project_id=project.id, chapter_id=chapter.id, body_fingerprint=fingerprint
    )
    entry["infrastructure_history"] = [
        {
            "attempt": 1,
            "outcome": "unknown",
            "draft_id": str(draft.id),
            "body_fingerprint": fingerprint,
            "review_report_id": str(report.id),
            "quality_score_id": str(quality.id),
            "rewrite_task_id": None,
            "review_result": result.model_dump(mode="json"),
            "waiting_receipt": prerequisite_wait_receipt(report, binding=binding),
        }
    ]
    old_hash = pipelines.chapter_review_body_fingerprint(old.content_md)["digest"]
    old_key = pipelines._chapter_critic_cycle_id(project.id, chapter.id, old_hash)
    old_cycle = {
        "cycle_id": old_key,
        "source_draft_id": str(old.id),
        "source_body_hash": old_hash,
        "state": "running",
        "review_workflow_run_id": str(old_owner.id),
    }
    chapter.metadata_json = {
        "chapter_critic_cycles": {old_key: old_cycle, key: cycle},
        "chapter_review_active_cycle": {
            "cycle_id": old_key,
            "source_draft_id": str(old.id),
            "source_body_hash": old_hash,
            "state": "completed",
        },
        "chapter_review_evidence_status": "unknown",
        "chapter_review_evidence_report_id": str(report.id),
        "chapter_review_attempts_active": 5,
        "chapter_review_recovery_cycles": [],
    }
    waiting_receipt = {
        "schema": "chapter-review-first-reuse.v1",
        "project_id": str(project.id),
        "chapter_id": str(chapter.id),
        "draft_id": str(draft.id),
        "body_sha256": fingerprint["digest"],
        "cycle_id": key,
        "policy_source_hash": "1" * 64,
        "semantic_dependencies_sha256": semantic,
        "report_id": str(report.id),
        "quality_score_id": str(quality.id),
        "task_id": None,
        "verdict": "unknown",
        "reuse_kind": WAITING,
        "review_workflow_run_id": str(owner.id),
        "requires_human_review": False,
        "dispatch_authorized": False,
        "credit_released": False,
    }
    owner.metadata_json = {
        "execution_scope_id": scope,
        "critic_cycle_id": key,
        "review_prerequisite_wait": waiting_receipt,
        "chapter_critic_evidence_unknown": True,
        "requires_human_review": False,
        "review_report_id": str(report.id),
        "quality_score_id": str(quality.id),
    }
    await session.commit()

    async def forbidden(*args, **kwargs):
        raise AssertionError("provider/pipeline dispatch forbidden in source preflight")

    monkeypatch.setattr(llm, "complete_text", forbidden)
    monkeypatch.setattr(pipelines, "run_chapter_pipeline", forbidden)
    return SimpleNamespace(**locals())


async def checked(r, policy=None):
    rows = list(r.session.identity_map.values())
    for row in rows:
        if row not in r.session.dirty:
            await r.session.refresh(row)
    before = {
        id(row): deepcopy({c.key: getattr(row, c.key) for c in inspect(type(row)).column_attrs})
        for row in rows
    }
    writes, locks = [], []

    def sql(_conn, _cursor, statement, *_args):
        if not statement.lstrip().upper().startswith("SELECT"):
            writes.append(statement)

    def orm(state):
        if getattr(state.statement, "_for_update_arg", None) is not None:
            locks.append(state.statement)

    event.listen(r.session.bind.sync_connection, "before_cursor_execute", sql)
    event.listen(r.session.sync_session, "do_orm_execute", orm)
    try:
        return await validate_waiting_review_policy_source(
            r.session,
            settings=r.settings,
            project=r.project,
            chapter=r.chapter,
            draft=r.draft,
            previous=r.owner,
            execution_scope_id=r.scope,
            current_policy_hash=(
                policy if policy is not None else pipelines.chapter_review_recovery_source_hash()
            ),
        )
    finally:
        event.remove(r.session.bind.sync_connection, "before_cursor_execute", sql)
        event.remove(r.session.sync_session, "do_orm_execute", orm)
        assert not writes and not locks
        assert not r.session.new and not r.session.dirty and not r.session.deleted
        assert before == {
            id(row): {c.key: getattr(row, c.key) for c in inspect(type(row)).column_attrs}
            for row in rows
        }


async def test_default_stays_strict_explicit_api_is_readonly_origin_only(waiting):
    r = waiting
    with pytest.raises(ValueError, match="policy_changed_requires_explicit_recovery"):
        await validate_retained_review_before_processing(r.session, r.settings, r.chapter)
    proof = await checked(r)
    assert proof == await checked(r)
    assert proof["source_cycle_id"] == r.key
    assert proof["previous_policy_source_hash"] == "1" * 64
    assert proof["current_policy_source_hash"] == pipelines.chapter_review_recovery_source_hash()
    assert proof["origin_only"] is True
    assert all(
        proof[k] is False
        for k in (
            "grade_reuse_allowed",
            "adoption_allowed",
            "attempt_reset_allowed",
            "new_credit_allowed",
            "dispatch_allowed",
        )
    )
    assert r.old_owner.status == "failed" and r.report.verdict == "unknown"


@pytest.mark.parametrize("policy", ["not-a-policy-hash", "1" * 64, "f" * 64])
async def test_caller_policy_hash_cannot_replace_actual_current_policy(waiting, policy):
    with pytest.raises(ValueError, match="current_policy_hash"):
        await checked(waiting, policy)


async def test_dirty_source_is_rejected_without_flush_or_rollback(waiting):
    r = waiting
    r.owner.metadata_json = {**r.owner.metadata_json, "uncommitted": True}
    dirty = set(r.session.dirty)
    with pytest.raises(ValueError, match="uncommitted_source"):
        await validate_waiting_review_policy_source(
            r.session,
            settings=r.settings,
            project=r.project,
            chapter=r.chapter,
            draft=r.draft,
            previous=r.owner,
            execution_scope_id=r.scope,
            current_policy_hash=pipelines.chapter_review_recovery_source_hash(),
        )
    assert set(r.session.dirty) == dirty
    assert r.owner.metadata_json["uncommitted"] is True


@pytest.mark.parametrize(
    "waiting",
    [
        (
            "The Orchard Promise",
            "romance",
            "Mira returns the lease and chooses the shared orchard.",
        ),
        (
            "Station Nine",
            "science-fiction",
            "The engineer seals the valve and chooses the lower airlock.",
        ),
    ],
    indirect=True,
)
async def test_native_waiting_migration_keeps_allowance_and_old_body_failed(waiting, monkeypatch):
    r = waiting
    dispatch = AsyncMock(return_value="stopped-before-review")
    monkeypatch.setattr(pipelines, "_run_prepared_chapter_review_cycle", dispatch)
    before = deepcopy(r.chapter.metadata_json)
    body, calls, tokens = r.draft.content_md, r.budget.calls_used, r.budget.tokens_charged
    with bind_execution_scope(r.scope):
        result = await pipelines.resume_chapter_review_after_policy_change(
            r.session,
            r.settings,
            r.project.slug,
            r.chapter.chapter_number,
            previous_workflow_run_id=r.owner.id,
            expected_draft_id=r.draft.id,
            expected_body_hash=r.fingerprint["digest"],
            policy_revision="explicit-generic-source-fix",
            policy_source_hash=pipelines.chapter_review_recovery_source_hash(),
            reason="Keep exact waiting body and prior paid history.",
        )
    assert result == "stopped-before-review"
    cycle = r.chapter.metadata_json["chapter_review_recovery_cycles"][-1]
    assert cycle["waiting_origin_proof"]["source_cycle_id"] == r.key
    assert cycle["attempts_base"] == before["chapter_review_attempts_active"]
    assert r.chapter.metadata_json["chapter_critic_cycles"] == before["chapter_critic_cycles"]
    assert r.old_owner.status == "failed" and r.report.verdict == "unknown"
    assert (r.draft.content_md, r.budget.calls_used, r.budget.tokens_charged) == (
        body,
        calls,
        tokens,
    )
    dispatch.assert_awaited_once()


@pytest.mark.parametrize(
    "change",
    [
        "body",
        "draft",
        "owner",
        "report",
        "quality",
        "receipt",
        "payload",
        "config",
        "scene",
        "design",
        "latch",
        "policy",
        "cycle_owner",
        "cycle_ambiguity",
        "pause",
        "auth",
        "operation",
        "attempt",
        "primary",
        "adopted",
        "human",
        "newer_report",
    ],
)
async def test_source_latest_policy_config_and_financial_tamper_rejected(waiting, change):
    r = waiting
    cm, wm = deepcopy(r.chapter.metadata_json), deepcopy(r.owner.metadata_json)
    cycle = cm["chapter_critic_cycles"][r.key]
    if change == "body":
        r.draft.content_md += " Changed bytes."
    elif change == "draft":
        r.draft.is_current = False
    elif change == "owner":
        r.owner.current_step = "chapter_adoption_required"
    elif change == "report":
        cm["chapter_review_evidence_report_id"] = str(uuid4())
    elif change == "quality":
        r.quality.chapter_draft_version_id = r.old.id
    elif change == "receipt":
        wm["review_prerequisite_wait"]["report_id"] = str(uuid4())
    elif change == "payload":
        r.report.structured_output = {**r.report.structured_output, "extra": "tamper"}
    elif change == "config":
        r.settings.quality.thresholds.scene_min_score += 0.01
    elif change == "scene":
        r.scene.exit_state = {"door": "changed"}
    elif change == "design":
        r.design.content = {"rules": ["Changed source"]}
    elif change == "latch":
        cycle["semantic_dependency_change_detected"] = True
    elif change == "policy":
        cycle["policy_source_hash"] = pipelines.chapter_review_recovery_source_hash()
    elif change == "cycle_owner":
        cycle["review_workflow_run_id"] = str(r.old_owner.id)
    elif change == "cycle_ambiguity":
        cm["chapter_critic_cycles"]["9" * 64] = deepcopy(cycle)
    elif change == "pause":
        r.control.desired_state = "pause"
    elif change == "auth":
        r.budget.metadata_json = {}
    elif change == "operation":
        r.operation.status = "unknown"
    elif change == "attempt":
        r.physical.status = "reserved"
    elif change == "primary":
        r.session.add(
            LlmRunModel(
                project_id=r.project.id,
                workflow_run_id=r.owner.id,
                logical_role="critic",
                provider="fixture",
                model_name="fixture",
                prompt_template="chapter_review",
                metadata_json={
                    "primary_review_body_sha256": hashlib.sha256(
                        r.draft.content_md.encode()
                    ).hexdigest()
                },
            )
        )
    elif change == "adopted":
        r.draft.promotion_state = "promoted"
    elif change == "human":
        wm["requires_human_review"] = True
    elif change == "newer_report":
        r.session.add(
            ReviewReportModel(
                project_id=r.project.id,
                target_type="chapter",
                target_id=r.chapter.id,
                reviewer_type="fixture",
                verdict="unknown",
                structured_output=deepcopy(r.report.structured_output),
                created_at=datetime.now(UTC) + timedelta(minutes=1),
            )
        )
    r.chapter.metadata_json, r.owner.metadata_json = cm, wm
    await r.session.commit()
    with pytest.raises(ValueError, match="chapter_waiting_review_policy_source_"):
        await checked(r)


async def test_readonly_actual_adoption_validator_uses_no_pg_lock(waiting, monkeypatch):
    actual = feedback.validate_adopted_knowledge_source
    locks = []

    async def checked_validator(*args, **kwargs):
        locks.append(kwargs["lock"])
        return await actual(*args, **kwargs)

    monkeypatch.setattr(feedback, "validate_adopted_knowledge_source", checked_validator)
    await checked(waiting)
    assert locks == [False]
