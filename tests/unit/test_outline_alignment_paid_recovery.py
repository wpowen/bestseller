"""Exact paid replay across real session checkpoints, without provider traffic."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
from uuid import uuid4

import pytest
from sqlalchemy import select

from bestseller.infra.db import models as db
from bestseller.services import llm
from bestseller.services import rewrite_outline_paid_recovery as paid
from bestseller.services.budget import execution_operation_id
from bestseller.services.prompt_source_policy import content_hash
from bestseller.services.rewrite_outline_dispatch_recovery import _row
from bestseller.settings import load_settings
from test_outline_alignment_dispatch import guard_dispatch_boundaries  # noqa: F401 -- Offline autouse boundary.
from test_outline_alignment_unstarted_recovery import (
    recovery_db, failed_case, financial_rows,  # noqa: F401 -- Real SQLite fixture.
)

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]
_COMPLETE = llm.complete_text


async def seed_paid(factory):
    case = await failed_case(factory)
    settings = load_settings(env={"BESTSELLER__LLM__MOCK": "false"})
    settings.llm.editor = settings.llm.editor.model_copy(update={
        "model": "openai/synthetic", "stream": False, "max_tokens": 1024,
        "api_base": "https://offline.invalid/v1", "api_key_env": None})
    request = llm.LLMCompletionRequest(logical_role="editor", system_prompt="Keep the map's facts.",
        user_prompt="Rewrite the source-bound route scene.", fallback_response="not a paid result",
        project_id=case.project.id, workflow_run_id=case.workflow_id,
        prompt_template="chapter_rewrite", prompt_version="3.2-source-selected-voice-editor")
    digest = llm._effective_input_hash(request, settings.llm.editor)
    namespace = "pipeline:run_chapter_pipeline:" + content_hash({
        "project_slug": case.project.slug, "chapter_number": case.chapter.chapter_number})
    operation_id = execution_operation_id(namespace,
        subject="editor:chapter_rewrite:3.2-source-selected-voice-editor", revision=digest)
    key = hashlib.sha256(f"{case.scope}\n{operation_id}:sample:0".encode()).hexdigest()
    actual_id = uuid4().hex
    origin = {"schema": "paid-call-origin.v1", "status": "recorded",
        "project_id": str(case.project.id), "workflow_run_id": str(case.workflow_id),
        "step_run_id": None, "logical_role": "editor", "prompt_template": "chapter_rewrite",
        "prompt_version": request.prompt_version, "prompt_hash": digest}
    result = llm.LLMCompletionResult(content="The cartographer returned the checked map to its owner.",
        provider="openai", model_name=settings.llm.editor.model, input_tokens=3, output_tokens=4,
        finish_reason="stop", raw_message={"role": "assistant",
            "content": "The cartographer returned the checked map to its owner."},
        metadata={"paid_call_origin": origin,
            "effective_input_receipt": {"schema": "effective-input.v1", "hash": digest,
                "model": settings.llm.editor.model},
            "execution_receipt": {"policy": "execution-budget.v1", "scope_id": case.scope,
                "operation_key": key, "attempts": [{"attempt_id": actual_id,
                    "status": "completed", "model": settings.llm.editor.model,
                    "effective_input_hash": digest, "reserved_tokens": 1000,
                    "wire_body_hash": "a" * 64}]}}).model_dump(mode="json")
    async with factory() as session:
        budget = await session.get(db.ExecutionBudgetModel, case.scope)
        budget.calls_used += 1
        budget.tokens_charged += 7
        session.add_all([
            db.ExecutionOperationModel(key=key, scope_id=case.scope, input_hash=digest,
                status="completed", owner="original-dispatcher", lease_until=datetime.now(timezone.utc),
                result_json=deepcopy(result)),
            db.ExecutionAttemptModel(id=actual_id, operation_key=key, scope_id=case.scope,
                model_name=settings.llm.editor.model, input_hash=digest, reserved_tokens=1000,
                actual_tokens=7, status="completed", created_at=datetime.now(timezone.utc)),
            db.ExecutionOperationModel(key=hashlib.sha256(actual_id.encode()).hexdigest(),
                scope_id=case.scope, input_hash=digest, status="transport_result", owner="original",
                lease_until=datetime.now(timezone.utc), result_json={"schema": "transport-result.v1",
                    "operation_key": key, "input_hash": digest, "attempt_id": actual_id,
                    "result": deepcopy(result)})])
        await session.commit()
    case.settings, case.request, case.key = settings, request, key
    case.result = result
    return case


def executor(case, restored):
    async def run(session, slug, number, *, rewrite_task_id, settings, workflow_run_id):
        result = await llm.complete_text(session, settings, case.request)
        assert result.metadata["reused_operation_key"] == case.key
        assert result.content == case.result["content"] and result.llm_run_id is not None
        restored.append(result.llm_run_id)
        task = await session.get(db.RewriteTaskModel, rewrite_task_id)
        old = await session.get(db.ChapterDraftVersionModel, case.draft.id)
        old.is_current = False
        await session.flush()
        candidate = db.ChapterDraftVersionModel(project_id=case.project.id,
            chapter_id=case.chapter.id, version_no=2, is_current=True,
            promotion_state="under_review", content_md=result.content)
        session.add(candidate)
        await session.flush()
        task.status = "completed"
        task.attempts += 1
        task.metadata_json = {**task.metadata_json, "rewritten_chapter_draft_id": str(candidate.id)}
        return candidate, task
    return run


def enable_real_cached_gateway(monkeypatch):
    monkeypatch.setattr(llm, "complete_text", _COMPLETE)

    async def forbidden(*args, **kwargs):
        raise AssertionError("No provider or retry is authorized in this fixture")
    monkeypatch.setattr(llm, "_call_litellm_with_retry", forbidden)


@pytest.mark.parametrize("historical_unknown", [False, True])
async def test_preparation_is_rollback_only_and_actual_cached_gateway_restores_body(
    recovery_db, monkeypatch, historical_unknown,  # noqa: F811 -- Imported pytest fixture.
):
    case = await seed_paid(recovery_db)
    enable_real_cached_gateway(monkeypatch)
    restored = []
    async with recovery_db() as session:
        if historical_unknown:
            session.add(db.ExecutionBudgetModel(scope_id="old-unknown-scope", max_calls=1,
                max_tokens=1000, calls_used=1, tokens_charged=1000, status="unknown",
                deadline_at=datetime.now(timezone.utc)))
            await session.flush()
            session.add(db.ExecutionOperationModel(key="b" * 64, scope_id="old-unknown-scope",
                input_hash="c" * 64, status="unknown", owner="old-dispatcher",
                lease_until=datetime.now(timezone.utc), result_json=None))
            session.add(db.ExecutionAttemptModel(id="old-unresolved-attempt", operation_key="b" * 64,
                scope_id="old-unknown-scope", model_name="openai/synthetic", input_hash="c" * 64,
                reserved_tokens=1000, actual_tokens=None, status="unknown",
                created_at=datetime.now(timezone.utc)))
            await session.commit()
        before = await financial_rows(session, case)
        old_workflow = _row(await session.get(db.WorkflowRunModel, case.workflow_id))
    prepared = await paid.prepare_paid_alignment_recovery(recovery_db, case.settings,
        workflow_run_id=case.workflow_id, expected_claim_id=case.claim_id,
        rewrite_executor=executor(case, restored))
    assert prepared["prepared_request"]["matched"] is True and restored == []
    async with recovery_db() as session:
        assert _row(await session.get(db.WorkflowRunModel, case.workflow_id)) == old_workflow
        assert before == await financial_rows(session, case)
        assert list(await session.scalars(select(db.LlmRunModel))) == []
    result = await paid.resume_paid_alignment_recovery(recovery_db, case.settings,
        workflow_run_id=case.workflow_id, expected_claim_id=case.claim_id,
        expected_witness_sha256=prepared["witness_sha256"], actor="test-operator",
        authorization_ref="preserve and recover original paid result", repair_ref="checkpoint-lifecycle",
        rewrite_executor=executor(case, restored))
    assert result.state == "review_ready" and len(restored) == 1
    async with recovery_db() as session:
        assert before == await financial_rows(session, case)
        workflow = await session.get(db.WorkflowRunModel, case.workflow_id)
        assert workflow.metadata_json["source_alignment_dispatch"]["claim_id"] == case.claim_id
        history = workflow.metadata_json[paid.HISTORY]
        assert len(history) == 1 and history[0]["prior_workflow"] == old_workflow
        assert history[0]["credit_released"] is False and history[0]["formal_adoption"] is False
        assert (await session.get(db.ChapterDraftVersionModel, case.draft.id)).is_current is False
        saved = await session.get(db.LlmRunModel, restored[0])
        assert saved.workflow_run_id == case.workflow_id
        assert saved.metadata_json["paid_call_origin"] == case.result["metadata"]["paid_call_origin"]
        task = await session.get(db.RewriteTaskModel, case.alignment_id)
        assert task.status == "completed" and task.attempts == 1
        if historical_unknown:
            old_hold = await session.get(db.ExecutionAttemptModel, "old-unresolved-attempt")
            old_budget = await session.get(db.ExecutionBudgetModel, "old-unknown-scope")
            assert old_hold.status == "unknown" and old_hold.actual_tokens is None
            assert old_hold.reserved_tokens == old_budget.tokens_charged == 1000
            assert old_budget.status == "unknown" and old_budget.calls_used == 1


@pytest.mark.parametrize("changed", ["body", "claim", "source", "paid_body", "physical_body",
    "actual_tokens", "actual_status", "budget", "prompt", "cross_scope_custody"])
async def test_changed_evidence_never_rearms_or_sends(recovery_db, monkeypatch, changed):  # noqa: F811
    case = await seed_paid(recovery_db)
    enable_real_cached_gateway(monkeypatch)
    async with recovery_db() as session:
        workflow = await session.get(db.WorkflowRunModel, case.workflow_id)
        if changed == "body":
            (await session.get(db.ChapterDraftVersionModel, case.draft.id)).content_md += " Changed."
        elif changed in {"claim", "source"}:
            metadata = deepcopy(workflow.metadata_json)
            metadata["source_alignment_dispatch"]["claim_id" if changed == "claim"
                else "input_body_sha256"] = str(uuid4()) if changed == "claim" else "0" * 64
            workflow.metadata_json = metadata
        elif changed in {"paid_body", "physical_body"}:
            rows = list(await session.scalars(select(db.ExecutionOperationModel).where(
                db.ExecutionOperationModel.scope_id == case.scope)))
            target = next(r for r in rows if r.key == case.key) if changed == "paid_body" else next(
                r for r in rows if r.status == "transport_result")
            payload = deepcopy(target.result_json)
            (payload if changed == "paid_body" else payload["result"])["content"] += " Different."
            target.result_json = payload
        elif changed.startswith("actual_"):
            actual = await session.scalar(select(db.ExecutionAttemptModel).where(
                db.ExecutionAttemptModel.operation_key == case.key))
            if changed == "actual_tokens":
                actual.actual_tokens += 1
            else:
                actual.status = "unknown"
        elif changed == "budget":
            (await session.get(db.ExecutionBudgetModel, case.scope)).calls_used += 1
        elif changed == "cross_scope_custody":
            other = "other-paid-scope"
            root_key = hashlib.sha256((other + "\nother-operation").encode()).hexdigest()
            attempt_id = uuid4().hex
            digest = case.result["metadata"]["effective_input_receipt"]["hash"]
            session.add(db.ExecutionBudgetModel(scope_id=other, max_calls=1,
                max_tokens=1000, calls_used=1, tokens_charged=7,
                status="active", deadline_at=datetime.now(timezone.utc)))
            await session.flush()
            root = deepcopy(case.result)
            root["metadata"]["execution_receipt"]["scope_id"] = other
            root["metadata"]["execution_receipt"]["operation_key"] = root_key
            root["metadata"]["execution_receipt"]["attempts"][0]["attempt_id"] = attempt_id
            session.add_all([db.ExecutionOperationModel(key=root_key, scope_id=other,
                input_hash=digest, status="completed", owner="other-original-owner",
                lease_until=datetime.now(timezone.utc), result_json=root),
                db.ExecutionAttemptModel(id=attempt_id, scope_id=other, operation_key=root_key,
                    model_name=case.settings.llm.editor.model, input_hash=digest,
                    reserved_tokens=1000, actual_tokens=7, status="completed",
                    created_at=datetime.now(timezone.utc)),
                db.ExecutionOperationModel(key=hashlib.sha256(attempt_id.encode()).hexdigest(),
                    scope_id=other, input_hash=digest, status="transport_result", owner="other",
                    lease_until=datetime.now(timezone.utc), result_json={"schema": "transport-result.v1",
                        "operation_key": root_key, "input_hash": digest, "attempt_id": attempt_id,
                        "result": deepcopy(root)})])
        else:
            case.request = case.request.model_copy(update={"user_prompt": "A different unpaid request"})
        await session.commit()
        before = await financial_rows(session, case)
        prior = _row(await session.get(db.WorkflowRunModel, case.workflow_id, populate_existing=True))
    from bestseller.services.retained_request_guard import RetainedRequestRejected
    with pytest.raises((ValueError, RetainedRequestRejected)) as rejected:
        await paid.prepare_paid_alignment_recovery(recovery_db, case.settings,
            workflow_run_id=case.workflow_id, expected_claim_id=case.claim_id,
            rewrite_executor=executor(case, []))
    if changed == "cross_scope_custody":
        assert "other_workflow_payment" in str(rejected.value)
    async with recovery_db() as session:
        assert _row(await session.get(db.WorkflowRunModel, case.workflow_id)) == prior
        assert before == await financial_rows(session, case)
