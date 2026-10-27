"""Isolated SQLite failed-origin proof; never dispatch to a provider."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
import json
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import event, inspect, text
from sqlalchemy.schema import CreateTable
from test_chapter_cancelled_review_recovery import db as db
from test_chapter_cancelled_review_recovery import retained as retained

from bestseller.infra.db.models import (
    ChapterContractModel,
    ExecutionAttemptModel,
    ExecutionOperationModel,
    LlmRunModel,
    QualityScoreModel,
    ReviewReportModel,
    RewriteTaskModel,
    SceneCardModel,
    WorkflowRunModel,
)
from bestseller.services import pipelines
from bestseller.services.budget import bind_execution_scope
from bestseller.services.chapter_failed_review_recovery import validate_failed_review_policy_source
from bestseller.settings import load_settings

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]
REAL_SEMANTIC_DEPENDENCIES = pipelines._chapter_critic_semantic_dependencies


@pytest.fixture
async def failed(retained, monkeypatch):
    session, project, chapter, draft, previous, control, budget, _scope = retained
    await session.execute(
        text(
            str(
                CreateTable(LlmRunModel.__table__, if_not_exists=True).compile(
                    dialect=session.bind.dialect
                )
            ).replace("::jsonb", "")
        )
    )
    scope = "conception-continuation:" + str(uuid4()) + ":" + str(uuid4())
    budget.scope_id = scope
    project.metadata_json = {"execution_scope_id": scope}
    previous.status, previous.current_step, previous.error_message = (
        "failed",
        "review_chapter_v3",
        "workflow_failed",
    )
    cycle = deepcopy(next(iter(chapter.metadata_json["chapter_critic_cycles"].values())))
    fingerprint = pipelines.chapter_review_body_fingerprint(draft.content_md)
    report = ReviewReportModel(
        id=uuid4(),
        project_id=project.id,
        target_type="chapter",
        target_id=chapter.id,
        reviewer_type="rule-based-critic",
        verdict="unknown",
        structured_output={
            "draft_id": str(draft.id),
            "reviewed_body_fingerprint": fingerprint,
            "critic_evidence": {"status": "unavailable", "passed": False},
            "primary_submission_original": None,
            "primary_submission_receipt": None,
            "rewrite_instructions": None,
        },
    )
    quality = QualityScoreModel(
        id=uuid4(),
        project_id=project.id,
        target_type="chapter",
        target_id=chapter.id,
        chapter_draft_version_id=draft.id,
        review_report_id=report.id,
        score_overall=0.0,
        is_current=True,
    )
    entry = {
        "attempt": 1,
        "state": "completed",
        "outcome": "unknown",
        "draft_id": str(draft.id),
        "body_fingerprint": fingerprint,
        "review_report_id": str(report.id),
        "quality_score_id": str(quality.id),
        "rewrite_task_id": None,
        "operation_namespace": f"chapter-critic:{cycle['cycle_id']}:1",
    }
    cycle["attempts"] = [
        entry,
        {
            "attempt": 2,
            "state": "running",
            "operation_namespace": f"chapter-critic:{cycle['cycle_id']}:2",
        },
    ]
    chapter.metadata_json = {
        **chapter.metadata_json,
        "chapter_critic_cycles": {cycle["cycle_id"]: cycle},
        "chapter_review_evidence_status": "unknown",
        "chapter_review_evidence_report_id": str(report.id),
    }
    previous.metadata_json = {
        "execution_scope_id": scope,
        "critic_cycle_id": cycle["cycle_id"],
        "paid_history": ["preserve-v5"],
        "critic_evidence_attempts": [
            {"draft_id": str(uuid4()), "outcome": "available"},
            deepcopy(entry),
        ],
        "execution_failure_receipts": [
            {
                "code": "workflow_failed",
                "recoverable": True,
                "error_type": "ValueError",
                "step": previous.current_step,
                "workflow_id": str(previous.id),
                "project_id": str(project.id),
                "workflow_type": "chapter_pipeline",
            }
        ],
    }
    operation = ExecutionOperationModel(
        key="5" * 64,
        scope_id=scope,
        input_hash="6" * 64,
        status="stopped",
        owner="retired",
        lease_until=datetime.now(UTC) - timedelta(minutes=1),
    )
    attempt = ExecutionAttemptModel(
        id="physical-stopped",
        operation_key=operation.key,
        scope_id=scope,
        input_hash=operation.input_hash,
        model_name="retained-model",
        reserved_tokens=1000,
        status="reconciled_charged",
        created_at=datetime.now(UTC),
    )
    transport = ExecutionOperationModel(
        key="7" * 64,
        scope_id=scope,
        input_hash="8" * 64,
        status="transport_failure_receipt",
        owner="receipt",
        lease_until=operation.lease_until,
        result_json={
            "schema": "transport-failure.v1",
            "workflow_run_id": str(previous.id),
            "project_id": str(project.id),
            "logical_role": "critic",
            "attempt_id": attempt.id,
            "operation_key": operation.key,
        },
    )
    stop = ExecutionOperationModel(
        key="9" * 64,
        scope_id=scope,
        input_hash="a" * 64,
        status="reconciliation_receipt",
        owner="manual-reconciliation",
        lease_until=operation.lease_until,
        result_json={
            "evidence_kind": "dispatcher_exit",
            "local_dispatcher_stopped": True,
            "remote_operation_closed": False,
            "evidence_sha256": "b" * 64,
            "actor": "operator",
            "receipt": {
                "scope_id": scope,
                "attempt_id": attempt.id,
                "decision": "quarantine_charged",
                "remaining_unknown": 0,
                "credit_released": False,
                "automatic_resume": False,
                "remote_outcome": "unknown_quarantined",
                "operation_replay_allowed": False,
                "calls_used": budget.calls_used,
                "tokens_charged": budget.tokens_charged,
            },
        },
    )
    session.add_all([report, quality, operation, attempt, transport, stop])
    await session.commit()
    settings = load_settings(env={})
    semantic = AsyncMock(return_value="2" * 64)
    monkeypatch.setattr(pipelines, "_chapter_critic_semantic_dependencies", semantic)
    return {
        "session": session,
        "project": project,
        "chapter": chapter,
        "draft": draft,
        "previous": previous,
        "control": control,
        "budget": budget,
        "scope": scope,
        "report": report,
        "quality": quality,
        "operation": operation,
        "attempt": attempt,
        "transport": transport,
        "stop": stop,
        "settings": settings,
        "semantic": semantic,
    }


async def checked(rows, policy="3" * 64):
    session = rows["session"]
    for key in (
        "project",
        "chapter",
        "draft",
        "previous",
        "control",
        "budget",
        "report",
        "quality",
        "operation",
        "attempt",
        "transport",
        "stop",
    ):
        row = rows[key]
        if inspect(row).persistent and row not in session.dirty:
            await session.refresh(row)
    state = (set(session.new), set(session.dirty), set(session.deleted))
    writes = []

    def track(_conn, _cursor, statement, _params, _context, _many):
        if not statement.lstrip().upper().startswith("SELECT"):
            writes.append(statement)

    event.listen(session.bind.sync_connection, "before_cursor_execute", track)
    try:
        proof = await validate_failed_review_policy_source(
            session,
            settings=rows["settings"],
            project=rows["project"],
            chapter=rows["chapter"],
            draft=rows["draft"],
            previous=rows["previous"],
            execution_scope_id=rows["scope"],
            current_policy_hash=policy,
        )
        json.dumps(proof, allow_nan=False)
        return proof
    finally:
        event.remove(session.bind.sync_connection, "before_cursor_execute", track)
        assert not writes
        assert state == (set(session.new), set(session.dirty), set(session.deleted))


async def test_current_failed_cycle_with_prior_available_other_body_is_origin_only(failed):
    before = deepcopy(failed["previous"].metadata_json), deepcopy(failed["chapter"].metadata_json)
    proof = await checked(failed)
    assert proof == await checked(failed)
    assert proof["previous_status"] == failed["previous"].status == "failed"
    assert proof["source_cycle"]["attempts"][-1]["state"] == "running"
    assert proof["budget_usage"] == {"calls_used": 13, "tokens_charged": 42000}
    assert failed["semantic"].call_args.args[1] is failed["settings"]
    assert before == (failed["previous"].metadata_json, failed["chapter"].metadata_json)
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


async def test_partial_automatic_cycle_ignores_historical_active_pointer_and_project_label(failed):
    r = failed
    original = deepcopy(next(iter(r["chapter"].metadata_json["chapter_critic_cycles"].values())))
    r["chapter"].metadata_json = {
        **r["chapter"].metadata_json,
        "chapter_review_active_cycle": {
            "cycle_id": "historical-v3",
            "state": "completed",
            "source_draft_id": str(uuid4()),
        },
    }
    r["project"].status = "paused"
    r["control"].command_serial = 3
    await r["session"].commit()
    proof = await checked(r)
    assert proof["source_cycle"] == original
    assert proof["source_cycle"]["attempts"][0]["outcome"] == "unknown"
    assert proof["source_cycle"]["attempts"][1]["state"] == "running"
    assert proof["control_command_serial"] == 3
    assert r["project"].status == "paused"


@pytest.mark.parametrize("bound_to_current", [True, False])
@pytest.mark.parametrize("conflicting_report", [True, False])
async def test_cached_current_or_unattributed_primary_cannot_be_ignored(
    failed, bound_to_current, conflicting_report
):
    import hashlib

    r = failed
    metadata = {
        "primary_review_body_sha256": hashlib.sha256(r["draft"].content_md.encode()).hexdigest()
    }
    run = LlmRunModel(
        id=uuid4(),
        project_id=r["project"].id,
        workflow_run_id=r["previous"].id,
        logical_role="critic",
        provider="retained-provider",
        model_name="retained-model",
        prompt_template="chapter_review",
        metadata_json=metadata if bound_to_current else {},
    )
    r["session"].add(run)
    if conflicting_report:
        r["session"].add(
            ReviewReportModel(
                project_id=r["project"].id,
                target_type="chapter",
                target_id=r["chapter"].id,
                reviewer_type="retained-provider",
                verdict="rewrite",
                llm_run_id=run.id,
                structured_output={"draft_id": str(uuid4())} if bound_to_current else {},
            )
        )
    await r["session"].commit()
    with pytest.raises(ValueError, match="primary_result_available_or_unattributed"):
        await checked(r)


async def test_available_primary_for_earlier_body_in_same_workflow_is_retained(failed):
    r = failed
    run = LlmRunModel(
        id=uuid4(),
        project_id=r["project"].id,
        workflow_run_id=r["previous"].id,
        logical_role="critic",
        provider="retained-provider",
        model_name="retained-model",
        prompt_template="chapter_review",
        metadata_json={},
    )
    prior = ReviewReportModel(
        project_id=r["project"].id,
        target_type="chapter",
        target_id=r["chapter"].id,
        reviewer_type="retained-provider",
        verdict="rewrite",
        llm_run_id=run.id,
        structured_output={"draft_id": str(uuid4())},
    )
    r["session"].add_all([run, prior])
    await r["session"].commit()
    proof = await checked(r)
    assert proof["draft_id"] == str(r["draft"].id)
    assert prior.verdict == "rewrite" and run.metadata_json == {}


@pytest.mark.parametrize("change", [None, "model", "threshold", "runtime_model", "scene"])
async def test_native_effective_semantic_config_must_match_old_cycle(failed, monkeypatch, change):
    r, session = failed, failed["session"]
    for model in (SceneCardModel, ChapterContractModel):
        await session.execute(
            text(
                str(
                    CreateTable(model.__table__, if_not_exists=True).compile(
                        dialect=session.bind.dialect
                    )
                ).replace("::jsonb", "")
            )
        )
    scene = SceneCardModel(
        project_id=r["project"].id,
        chapter_id=r["chapter"].id,
        scene_number=1,
        scene_type="development",
        purpose={"story": "Inspect the instrument"},
        participants=[],
        entry_state={"valve": "closed"},
        exit_state={"valve": "open"},
        metadata_json={},
    )
    session.add(scene)
    await session.flush()
    semantic = await REAL_SEMANTIC_DEPENDENCIES(session, r["settings"], r["chapter"])
    m = deepcopy(r["chapter"].metadata_json)
    next(iter(m["chapter_critic_cycles"].values()))["semantic_dependencies_sha256"] = semantic
    r["chapter"].metadata_json = m
    if change == "model":
        r["settings"].llm.critic.model = "changed-semantic-fixture-model"
    elif change == "threshold":
        r["settings"].quality.thresholds.scene_min_score += 0.01
    elif change == "runtime_model":
        monkeypatch.setenv("BESTSELLER__LLM__COMMERCIAL_JUDGE_MODEL_KEY", "changed-fixture-critic")
    elif change == "scene":
        scene.exit_state = {"valve": "closed"}
    await session.commit()
    monkeypatch.setattr(
        pipelines, "_chapter_critic_semantic_dependencies", REAL_SEMANTIC_DEPENDENCIES
    )
    if change is None:
        assert (await checked(r))["semantic_dependencies_sha256"] == semantic
    else:
        with pytest.raises(ValueError, match="semantic_dependencies_changed"):
            await checked(r)


@pytest.mark.parametrize(
    "change",
    [
        "wrong_workflow",
        "wrong_step",
        "missing_failure",
        "bad_failure",
        "wrong_body",
        "same_policy",
        "stale_wf_cycle",
        "current_available",
        "primary_returned",
        "wrong_quality",
        "unknown_operation",
        "reserved_attempt",
        "missing_exit",
        "live_dispatcher",
        "released_credit",
        "wrong_exit_attempt",
        "pause",
        "missing_auth",
        "semantic_change",
        "active_wf",
    ],
)
async def test_failed_origin_rejects_missing_or_unsafe_evidence_without_writes(failed, change):
    r, session = failed, failed["session"]
    policy = "3" * 64
    if change == "wrong_workflow":
        other = WorkflowRunModel(
            id=uuid4(),
            project_id=r["project"].id,
            scope_type="chapter",
            scope_id=r["chapter"].id,
            workflow_type="chapter_pipeline",
            status="failed",
            current_step="review_chapter_v3",
            error_message="workflow_failed",
            metadata_json=deepcopy(r["previous"].metadata_json),
        )
        session.add(other)
        r["previous"] = other
    elif change == "wrong_step":
        r["previous"].current_step = "draft_chapter"
    elif change in {"missing_failure", "bad_failure", "stale_wf_cycle"}:
        m = deepcopy(r["previous"].metadata_json)
        if change == "missing_failure":
            m.pop("execution_failure_receipts")
        elif change == "bad_failure":
            m["execution_failure_receipts"][-1]["recoverable"] = False
        else:
            m["critic_cycle_id"] = "0" * 64
        r["previous"].metadata_json = m
    elif change == "wrong_body":
        r["draft"].content_md += " A changed instrument."
    elif change == "same_policy":
        policy = "1" * 64
    elif change == "current_available":
        r["report"].verdict = "rewrite"
    elif change == "primary_returned":
        r["report"].structured_output = {
            **r["report"].structured_output,
            "primary_submission_receipt": {"status": "verified"},
        }
    elif change == "wrong_quality":
        r["quality"].chapter_draft_version_id = uuid4()
    elif change == "unknown_operation":
        r["operation"].status = "unknown"
    elif change == "reserved_attempt":
        r["attempt"].status = "reserved"
    elif change == "missing_exit":
        await session.delete(r["stop"])
    elif change in {"live_dispatcher", "released_credit", "wrong_exit_attempt"}:
        m = deepcopy(r["stop"].result_json)
        if change == "live_dispatcher":
            m["local_dispatcher_stopped"] = False
        elif change == "released_credit":
            m["receipt"]["credit_released"] = True
        else:
            m["receipt"]["attempt_id"] = "other"
        r["stop"].result_json = m
    elif change == "pause":
        r["control"].desired_state = "pause"
    elif change == "missing_auth":
        r["budget"].metadata_json = {}
    elif change == "semantic_change":
        r["semantic"].return_value = "4" * 64
    else:
        session.add(
            WorkflowRunModel(
                project_id=r["project"].id,
                scope_type="chapter",
                scope_id=r["chapter"].id,
                workflow_type="chapter_pipeline",
                status="running",
            )
        )
    await session.commit()
    with pytest.raises(ValueError, match="chapter_failed_review_recovery_"):
        await checked(r, policy)


@pytest.fixture
async def entry(failed, monkeypatch):
    session = failed["session"]
    await session.execute(
        text(
            str(
                CreateTable(RewriteTaskModel.__table__).compile(dialect=session.bind.dialect)
            ).replace("::jsonb", "")
        )
    )
    await session.commit()
    dispatch = AsyncMock(return_value="stopped_before_dispatch")
    monkeypatch.setattr(pipelines, "_run_prepared_chapter_review_cycle", dispatch)

    def forbidden(**kwargs):
        raise AssertionError("failed source must retain its terminal state")

    monkeypatch.setattr(pipelines, "_record_chapter_adoption_outcome", forbidden)
    return failed, dispatch


async def invoke(r):
    with bind_execution_scope(r["scope"]):
        return await pipelines.resume_chapter_review_after_policy_change(
            r["session"],
            r["settings"],
            r["project"].slug,
            r["chapter"].chapter_number,
            previous_workflow_run_id=r["previous"].id,
            expected_draft_id=r["draft"].id,
            expected_body_hash=pipelines.chapter_review_body_fingerprint(r["draft"].content_md)[
                "digest"
            ],
            policy_revision="explicit-source-policy-change",
            policy_source_hash=pipelines.chapter_review_recovery_source_hash(),
            reason="Keep failed source and charged history; re-review current body only.",
        )


async def test_actual_entry_preserves_failed_source_and_existing_allowance(entry):
    r, dispatch = entry
    old_cycle = deepcopy(r["chapter"].metadata_json["chapter_critic_cycles"])
    paid = r["budget"].calls_used, r["budget"].tokens_charged
    assert await invoke(r) == "stopped_before_dispatch"
    await r["session"].refresh(r["previous"])
    await r["session"].refresh(r["chapter"])
    cycle = r["chapter"].metadata_json["chapter_review_recovery_cycles"][-1]
    assert cycle["attempts_base"] == 9
    assert cycle["failed_origin_proof"]["previous_workflow_run_id"] == str(r["previous"].id)
    assert r["chapter"].metadata_json["chapter_critic_cycles"] == old_cycle
    assert r["previous"].status == "failed" and r["previous"].error_message == "workflow_failed"
    assert r["previous"].current_step == "review_chapter_v3"
    assert (r["budget"].calls_used, r["budget"].tokens_charged) == paid
    assert dispatch.await_count == 1
    m = deepcopy(r["chapter"].metadata_json)
    m["chapter_review_recovery_cycles"][-1]["state"] = "completed"
    r["chapter"].metadata_json = m
    await r["session"].commit()
    with pytest.raises(ValueError, match="policy_already_used"):
        await invoke(r)
    assert dispatch.await_count == 1


async def test_actual_entry_invalid_origin_stops_before_checkpoint(entry):
    r, dispatch = entry
    r["control"].desired_state = "pause"
    await r["session"].commit()
    with pytest.raises(ValueError, match="explicit_project_run_required"):
        await invoke(r)
    assert not r["session"].new and not r["session"].dirty
    dispatch.assert_not_awaited()


async def test_earlier_machine_blocked_workflow_cannot_authorize_failed_current_body(entry):
    r, dispatch = entry
    session = r["session"]
    older = WorkflowRunModel(
        project_id=r["project"].id,
        scope_type="chapter",
        scope_id=r["chapter"].id,
        workflow_type="chapter_pipeline",
        status="machine_blocked",
    )
    session.add(older)
    await session.commit()
    r["previous"] = older
    with pytest.raises(ValueError, match="requires_failed_origin"):
        await invoke(r)
    dispatch.assert_not_awaited()
    assert not session.new and not session.dirty
