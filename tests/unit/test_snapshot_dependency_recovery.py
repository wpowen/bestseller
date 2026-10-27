"""Retained evidence recovery on real SQLite, without provider or network access."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
import hashlib
import json
import socket
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import event, select
from sqlalchemy.schema import CreateTable
from test_adopted_knowledge_lineage import db as db

from bestseller.infra.db.models import (
    CanonFactModel,
    ChapterDraftVersionModel,
    ChapterModel,
    ChapterStateSnapshotModel,
    ExecutionAttemptModel,
    ExecutionBudgetModel,
    ExecutionOperationModel,
    LlmRunModel,
    ProjectModel,
    QualityScoreModel,
    ReviewReportModel,
    TimelineEventModel,
    WorkflowRunModel,
)
from bestseller.services import continuity, llm
from bestseller.services.feedback import validate_adopted_knowledge_source
from bestseller.settings import LLMRoleSettings

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]
PROTECTED = (
    ProjectModel, ChapterModel, ChapterDraftVersionModel, ReviewReportModel,
    QualityScoreModel, CanonFactModel, TimelineEventModel, LlmRunModel,
    WorkflowRunModel, ExecutionBudgetModel, ExecutionOperationModel, ExecutionAttemptModel,
)


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def digest(value):
    return sha(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str))


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv("BESTSELLER_LINEAGE_PG", "0")

    def forbidden(*_args, **_kwargs):
        raise AssertionError("Recovery tests prohibit network and model calls")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(llm, "complete_text", forbidden)
    monkeypatch.setattr(llm, "_call_litellm", forbidden)
    monkeypatch.setattr(continuity, "complete_text", forbidden)


@pytest.fixture
async def retained(db, tmp_path):
    session, project, chapter, draft = db
    assert session.bind.dialect.name == "sqlite"
    assert "test" in session.bind.engine.url.database
    conn = await session.connection()
    for model in (WorkflowRunModel, LlmRunModel, ExecutionBudgetModel,
                  ExecutionOperationModel, ExecutionAttemptModel):
        ddl = str(CreateTable(model.__table__).compile(dialect=conn.dialect))
        await conn.exec_driver_sql(ddl.replace("::jsonb", ""))
    root = tmp_path / "canonical-artifacts"
    root.mkdir()
    settings = SimpleNamespace(artifact_store=SimpleNamespace(
        mode="local", local_dir=root, retain_prompt_payloads=True,
    ), llm=SimpleNamespace(editor=LLMRoleSettings(model='test/model', api_base='https://test.invalid/v1',
        temperature=.1, max_tokens=400, timeout_seconds=60, stream=False)))
    scope = f"snapshot-test:{uuid4()}"
    now = datetime.now(UTC)
    budget = ExecutionBudgetModel(
        scope_id=scope, max_calls=3, max_tokens=600, calls_used=3,
        tokens_charged=600, deadline_at=now + timedelta(days=1), status="stopped",
        metadata_json={"authorization": "unchanged", "credit": 0},
    )
    workflow = WorkflowRunModel(
        project_id=project.id, workflow_type="chapter_pipeline", status="completed",
    )
    session.add_all([budget, workflow])
    await session.flush()
    rows, runs, operations, attempts, chapters, drafts = [], [], [], [], [], []
    for number in range(1, 4):
        if number > 1:
            chapter = ChapterModel(
                project_id=project.id, chapter_number=number, chapter_goal="离线依赖恢复",
                metadata_json={},
            )
            session.add(chapter)
            await session.flush()
            draft = ChapterDraftVersionModel(
                project_id=project.id, chapter_id=chapter.id, version_no=1,
                content_md=f"第{number}次观测结束。", promotion_state="promoted", is_current=True,
            )
            session.add(draft)
            await session.flush()
        lineage, rejection = await validate_adopted_knowledge_source(
            session, project_id=project.id, chapter_id=chapter.id,
            source_chapter_draft_version_id=draft.id, chapter_md=draft.content_md, lock=False,
        )
        assert rejection is None
        system = continuity._EXTRACTION_SYSTEM_PROMPT
        user = continuity._build_extraction_user_prompt(
            chapter=chapter, chapter_md=draft.content_md,
            previous_snapshot=continuity._snapshot_context(rows[-1]) if rows else None,
        )
        facts = [{"name": "位置", "value": "观测室", "kind": "location", "unit": None,
                  "notes": None, "source_quote": None, "unknown": {"future": [None, 1, True]}},
                 None, {"future_only": None}]
        raw = json.dumps({"facts": facts}, ensure_ascii=False)
        run_id = uuid4()
        operation_id = (
            f"knowledge:snapshot:v1:{chapter.id}:{draft.id}:{lineage['source_body_sha256']}"
        )
        key = sha(scope + "\n" + operation_id)
        input_hash = sha(f"retained-effective-parameters-{number}")
        attempt_id = uuid4().hex
        execution = {
            "scope_id": scope, "operation_key": key, "policy": "execution-budget.v1",
            "attempts": [{"attempt_id": attempt_id, "model": "test/model",
                          "effective_input_hash": input_hash, "reserved_tokens": 300,
                          "status": "completed"}],
        }
        request = llm.LLMCompletionRequest(
            logical_role="editor", system_prompt=system, user_prompt=user,
            fallback_response='{"facts": []}',
            prompt_template="chapter_state_snapshot", prompt_version="2.0",
            project_id=project.id, workflow_run_id=workflow.id, operation_id=operation_id,
        )
        input_hash = llm._effective_input_hash(request, settings.llm.editor)
        execution['attempts'][0]['effective_input_hash'] = input_hash
        effective_receipt = {'schema':'effective-input.v1', 'hash':input_hash, 'model':'test/model'}
        with llm.bind_project_prompt_audit(project.id, reason="offline retained fixture"):
            req, resp, error, replayable = llm._persist_prompt_payloads(
                settings=settings, request=request, llm_run_id=run_id,
                configured_model="test/model", effective_model="test/model", provider="test",
                effective_temperature=0.1, effective_max_tokens=400,
                effective_timeout_seconds=60, effective_stream=False,
                effective_thinking_type=None, effective_reasoning_effort=None,
                content=raw, tool_call_count=0, finish_reason="stop", fallback_used=False,
                fallback_source=None, input_tokens=120, output_tokens=80,
            )
        assert error is None and replayable
        operation = {
            "operation_id": operation_id, "llm_run_id": str(run_id),
            "workflow_run_id": str(workflow.id), "step_run_id": None,
            "prompt_template": "chapter_state_snapshot", "prompt_version": "2.0",
            "prompt_sha256": sha(system + "\n" + user), "model": "test/model",
            "provider": "test", "fallback_used": False,
        }
        contract = {**lineage, **operation, "llm_operations": [deepcopy(operation)],
                    "is_usable": True, "rejection_reason": None, "unknown_contract": None}
        row = ChapterStateSnapshotModel(
            project_id=project.id, chapter_id=chapter.id, chapter_number=number,
            facts={"facts": facts, "snapshot_contract": contract,
                   "unknown_root": {"keep": None}, "story_engine": {"canonical": None},
                   "snapshot_history": [{"unchanged_historical_entry": [None, {"x": True}]}]},
            raw_extraction=raw, extraction_model="test/model", extraction_status="ok_promoted",
            time_anchor=None, chapter_time_span="未知",
        )
        run = LlmRunModel(
            id=run_id, project_id=project.id, workflow_run_id=workflow.id,
            logical_role="editor", provider="test", model_name="test/model",
            prompt_template="chapter_state_snapshot", prompt_version="2.0",
            prompt_hash=input_hash, input_tokens=120, output_tokens=80, finish_reason="stop",
            request_payload_ref=req, response_payload_ref=resp,
            metadata_json={"execution_receipt": execution,'effective_input_receipt':effective_receipt},
        )
        op = ExecutionOperationModel(
            key=key, scope_id=scope, input_hash=input_hash, status="completed", owner="original",
            lease_until=now, result_json={"content": raw, "model_name": "test/model",
                "provider": "test", "input_tokens": 120, "output_tokens": 80,
                "finish_reason": "stop", "fallback_used": False,
                "metadata": {"execution_receipt": execution,'effective_input_receipt':effective_receipt}},
        )
        attempt = ExecutionAttemptModel(
            id=attempt_id, operation_key=key, scope_id=scope, model_name="test/model",
            input_hash=input_hash, reserved_tokens=300, actual_tokens=200,
            status="completed", created_at=now,
        )
        session.add_all([row, run, op, attempt])
        await session.flush()
        rows.append(row)
        runs.append(run)
        operations.append(op)
        attempts.append(attempt)
        chapters.append(chapter)
        drafts.append(draft)
    data = SimpleNamespace(session=session, project=project, settings=settings, root=root,
        rows=rows, runs=runs, operations=operations, attempts=attempts,
        chapters=chapters, drafts=drafts, budget=budget, workflow=workflow)
    # Root has no dependency migration, but consumers still require its real
    # admitted view. Keep later legacy rows and their paid inputs untouched.
    await admit_snapshot_view(data, 1)
    return data


async def admit_snapshot_view(data, number):
    """Publish only after the old dependency migration, in predecessor order."""
    chapter, draft = data.chapters[number - 1], data.drafts[number - 1]
    kwargs = {
        "project_id": data.project.id,
        "chapter_id": chapter.id,
        "source_chapter_draft_version_id": draft.id,
        "chapter_md": draft.content_md,
    }
    proof = await continuity.validate_snapshot_epistemic_view(data.session, **kwargs)
    return await continuity.apply_snapshot_epistemic_view(
        data.session, **kwargs,
        expected_snapshot_sha256=proof["snapshot_sha256"],
        admission_receipt=proof["admission_receipt"],
        actor="offline-fixture-operator",
        reason="Publish same-source view after retained dependency recovery",
    )


async def recover(data, number=2, *, dry_run=True, actor="operator", reason="retained proof"):
    from bestseller.services.snapshot_dependency_recovery import (
        reconcile_retained_snapshot_dependencies,
    )

    result = await reconcile_retained_snapshot_dependencies(
        data.session, data.settings, snapshot_id=data.rows[number - 1].id,
        actor=actor, reason=reason, dry_run=dry_run,
    )
    if not dry_run and result["status"] == "reconciled":
        await admit_snapshot_view(data, number)
    return result


async def database_image(data):
    tables = (*PROTECTED, ChapterStateSnapshotModel)
    return {model.__tablename__: digest([
        dict(row) for row in (await data.session.execute(select(model.__table__))).mappings()
    ]) for model in tables}


async def test_dry_run_then_execute_preserves_exact_tree_history_and_is_idempotent(retained):
    data = retained
    old = deepcopy(data.rows[1].facts)
    before = await database_image(data)
    root = await recover(data, 1, dry_run=False)
    assert root["status"] == "already_current"
    preview = await recover(data)
    assert preview["status"] == "proof_only"
    assert await database_image(data) == before
    result = await recover(data, dry_run=False)
    assert result["status"] == "reconciled"
    current = data.rows[1].facts
    assert current["facts"] == old["facts"]
    assert current["unknown_root"] == old["unknown_root"]
    assert current["snapshot_history"][:-1] == old["snapshot_history"]
    assert current["snapshot_history"][-1]["facts"] == {
        k: v for k, v in old.items() if k != "snapshot_history"
    }
    receipt = current["snapshot_contract"]["snapshot_dependency_recovery"]
    assert receipt["schema"] == "retained-snapshot-dependencies.v1"
    assert receipt["actor"] == "operator" and receipt["reason"] == "retained proof"
    assert receipt["provider_calls"] == receipt["new_grade"] == receipt["new_adoption"] == 0
    assert receipt["credit_released"] == 0
    after = await database_image(data)
    assert all(after[k] == v for k, v in before.items() if k != "chapter_state_snapshots")
    assert (await recover(data, dry_run=False))["status"] == "already_current"
    assert (await recover(data))["status"] == "already_current"
    assert await database_image(data) == after


async def test_three_chapters_require_legal_predecessor_migration_in_order(retained):
    before = await database_image(retained)
    with pytest.raises(ValueError, match="predecessor_snapshot_not_current"):
        await recover(retained, 3, dry_run=False)
    assert await database_image(retained) == before
    assert (await recover(retained, 1))["status"] == "already_current"
    await recover(retained, 2, dry_run=False)
    assert (await recover(retained, 3))["status"] == "proof_only"
    await recover(retained, 3, dry_run=False)
    for number in range(1, 4):
        assert await continuity.snapshot_evidence_current(
            retained.session, project_id=retained.project.id, chapter_number=number,
        )


async def test_missing_optional_raw_witness_is_recorded_without_inventing_it(retained):
    operation = retained.operations[1]
    operation.result_json = {**operation.result_json, 'raw_message': {
        'role':'assistant', 'content':operation.result_json['content'], 'tool_calls':None,
    }}
    await retained.session.flush()
    before = await database_image(retained)
    result = await recover(retained)
    assert result['status'] == 'proof_only'
    assert 'raw_message' in result['proof']['physical_structured_fields_missing']
    assert await database_image(retained) == before


async def test_dry_run_has_no_flush_or_for_update_and_execute_locks_in_order(retained, monkeypatch):
    statements = []

    def record(state):
        statements.append(state.statement)

    async def forbidden_flush(*_args, **_kwargs):
        raise AssertionError("dry_run called flush")

    event.listen(retained.session.sync_session, "do_orm_execute", record)
    try:
        with monkeypatch.context() as patch:
            patch.setattr(retained.session, "flush", forbidden_flush)
            await recover(retained)
        assert statements and all(s._for_update_arg is None for s in statements)
        statements.clear()
        await recover(retained, dry_run=False)
        locked = [s.column_descriptions[0]["entity"].__tablename__
                  for s in statements if s._for_update_arg is not None]
        assert locked[:3] == ["chapters", "chapter_draft_versions", "chapter_state_snapshots"]
    finally:
        event.remove(retained.session.sync_session, "do_orm_execute", record)


def rewrite_payload(data, response=False, change=None):
    run = data.runs[1]
    path = data.root / (run.response_payload_ref if response else run.request_payload_ref)
    payload = json.loads(path.read_bytes())
    change(payload)
    path.write_text(json.dumps(payload, ensure_ascii=False))


@pytest.mark.parametrize("defect", [
    "input", "request_owner", "response_owner", "body", "version", "raw", "unknown",
    "missing_file", "prompt_hash", "truncated", "not_replayable", "length",
    "run_owner", "workflow_owner", "role", "model", "tokens", "operation_scope",
    "operation_content", "operation_input_hash", "unsettled_tokens", "missing_attempt", "unknown_extra_attempt",
    "parameter_missing", "parameter_temperature", "temperature_override_conflict", "max_tokens_override_conflict", "outside_store", "source_version", "source_owner", "unusable",
    "status", "response_hash", "response_truncated", "request_version", "response_hidden_tools", "response_raw_message", "paid_raw_tools",
])
async def test_bad_evidence_refuses_both_modes_without_writes(retained, defect, tmp_path):
    d = retained
    row, run, op, attempt = d.rows[1], d.runs[1], d.operations[1], d.attempts[1]
    if defect == "input":
        rewrite_payload(d, change=lambda p: p["messages"]["user"].update(
            text="changed", original_chars=7))
    elif defect in {"request_owner", "response_owner"}:
        rewrite_payload(d, response=defect == "response_owner",
                        change=lambda p: p.update(project_id=str(uuid4())))
    elif defect == "body":
        d.drafts[1].content_md += "另一个版本。"
    elif defect == "version":
        run.prompt_version = "unknown"
    elif defect == "raw":
        row.raw_extraction += "changed"
    elif defect == "unknown":
        attempt.status = "unknown"
    elif defect == "missing_file":
        (d.root / run.request_payload_ref).unlink()
    elif defect in {"prompt_hash", "source_version", "source_owner", "unusable"}:
        facts = deepcopy(row.facts)
        key, value = {"prompt_hash": ("prompt_sha256", "0" * 64),
                      "source_version": ("source_version_no", 2),
                      "source_owner": ("project_id", str(uuid4())),
                      "unusable": ("is_usable", False)}[defect]
        facts["snapshot_contract"][key] = value
        row.facts = facts
    elif defect == "truncated":
        rewrite_payload(d, change=lambda p: p["messages"]["system"].update(truncated=True))
    elif defect == "not_replayable":
        rewrite_payload(d, response=True, change=lambda p: p.update(replayable=False))
    elif defect == "length":
        rewrite_payload(d, change=lambda p: p["messages"]["user"].update(original_chars=1))
    elif defect == "run_owner":
        run.project_id = uuid4()
    elif defect == "workflow_owner":
        d.workflow.project_id = uuid4()
    elif defect == "role":
        run.logical_role = "critic"
    elif defect == "model":
        op.result_json = {**op.result_json, "model_name": "other/model"}
    elif defect == "tokens":
        op.result_json = {**op.result_json, "input_tokens": 121}
    elif defect == "operation_scope":
        op.scope_id = "other-scope"
    elif defect == "operation_content":
        op.result_json = {**op.result_json, "content": "different raw"}
    elif defect == "operation_input_hash":
        op.input_hash = "different-effective-input"
    elif defect == "unsettled_tokens":
        attempt.actual_tokens = None
    elif defect == "missing_attempt":
        await d.session.delete(attempt)
    elif defect == "unknown_extra_attempt":
        d.session.add(ExecutionAttemptModel(
            id=uuid4().hex, operation_key=op.key, scope_id=op.scope_id,
            model_name=attempt.model_name, input_hash=attempt.input_hash, reserved_tokens=99,
            status="unknown", created_at=datetime.now(UTC),
        ))
    elif defect == "parameter_missing":
        rewrite_payload(d, change=lambda p: p["effective"].pop("temperature"))
    elif defect == "parameter_temperature":
        rewrite_payload(d, change=lambda p: p['effective'].update(temperature=.8))
    elif defect == "temperature_override_conflict":
        def conflict(payload):
            payload['requested']['temperature_override'] = .1
            payload['effective']['temperature'] = .9
        rewrite_payload(d, change=conflict)
    elif defect == "max_tokens_override_conflict":
        def conflict(payload):
            payload['requested']['max_tokens_override'] = 400
            payload['effective']['max_tokens'] = 500
        rewrite_payload(d, change=conflict)
    elif defect == "outside_store":
        outside = tmp_path / "alias.request.json"
        outside.write_bytes((d.root / run.request_payload_ref).read_bytes())
        run.request_payload_ref = str(outside)
    elif defect == "status":
        row.extraction_status = "ok_accepted_with_debt"
    elif defect == "response_hash":
        rewrite_payload(d, response=True, change=lambda p: p["content"].update(text="changed"))
    elif defect == "response_truncated":
        rewrite_payload(d, response=True, change=lambda p: p["content"].update(truncated=True))
    elif defect == "request_version":
        rewrite_payload(d, change=lambda p: p.update(prompt_version="unknown"))
    elif defect == "response_hidden_tools":
        rewrite_payload(d, response=True, change=lambda p: p.update(tool_calls=[{'id':'unbound'}]))
    elif defect == "response_raw_message":
        rewrite_payload(d, response=True, change=lambda p: p.update(raw_message={'content':'unbound'}))
    elif defect == "paid_raw_tools":
        op.result_json = {**op.result_json, 'raw_message': {'tool_calls':[{'id':'hidden'}]}}
    await d.session.flush()
    before = await database_image(d)
    for dry_run in (True, False):
        with pytest.raises(ValueError):
            await recover(d, dry_run=dry_run)
        assert await database_image(d) == before


@pytest.mark.parametrize("actor,reason", [("", "reason"), ("actor", " ")])
async def test_execute_requires_actor_and_reason(retained, actor, reason):
    before = await database_image(retained)
    with pytest.raises(ValueError):
        await recover(retained, dry_run=False, actor=actor, reason=reason)
    assert await database_image(retained) == before


@pytest.mark.parametrize("late", ["file", "operation", "source", "projection"])
async def test_late_bad_evidence_rolls_back_every_snapshot_write(retained, monkeypatch, late):
    original = continuity._publish_epistemic_view
    before = await database_image(retained)

    def intervene(*args, **kwargs):
        result = original(*args, **kwargs)
        if late == "file":
            rewrite_payload(retained, response=True, change=lambda p: p.update(replayable=False))
        elif late == "operation":
            retained.operations[1].status = "unknown"
        elif late == "source":
            retained.drafts[1].content_md += "late change"
        else:
            args[0].raw_extraction += "late change"
        return result

    monkeypatch.setattr(continuity, "_publish_epistemic_view", intervene)
    with pytest.raises(ValueError):
        await recover(retained, dry_run=False)
    assert await database_image(retained) == before
