"""The preview reuses the existing extractor's paid repair, without sending."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
import hashlib
import json
from uuid import uuid4

import pytest
from sqlalchemy import JSON, MetaData, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.schema import CreateTable
from test_adopted_knowledge_resume_control_integrity import configured as configured

from bestseller.infra.db import models as db
from bestseller.services import chapter_continuity_admission as admission
from bestseller.services import continuity, llm
from bestseller.services.budget import bind_execution_scope

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


def wire_bytes(request, role):
    return json.dumps(llm._effective_input_payload(request, role), ensure_ascii=False,
                      sort_keys=True, separators=(",", ":")).encode("utf-8")


async def financial_rows(session):
    result = {}
    for model in (db.ExecutionBudgetModel, db.ExecutionOperationModel,
                  db.ExecutionAttemptModel, db.LlmRunModel):
        rows = (await session.execute(select(model.__table__))).mappings().all()
        result[model.__tablename__] = sorted(
            json.dumps(dict(row), sort_keys=True, default=str) for row in rows
        )
    return result


async def test_original_paid_malformed_and_repair_replay_through_native_cache(
    configured, monkeypatch,
):
    session, settings, project, chapter, draft = configured
    assert session.bind.dialect.name == "sqlite"
    assert session.bind.url.database.endswith("_test.sqlite3")
    connection = await session.connection()
    for model in (db.ExecutionBudgetModel, db.ExecutionOperationModel,
                  db.ExecutionAttemptModel, db.LlmRunModel):
        table = model.__table__.to_metadata(MetaData())
        for column in table.c:
            if isinstance(column.type, JSONB):
                column.type = JSON()
            if column.server_default and "::jsonb" in str(column.server_default.arg):
                column.server_default = None
        await connection.execute(CreateTable(table, include_foreign_key_constraints=[]))
    settings = settings.model_copy(deep=True)
    settings.llm.mock = False
    settings.llm.editor = settings.llm.editor.model_copy(update={
        "model": "openai/synthetic", "api_base": "https://offline.invalid/v1",
        "stream": False, "max_tokens": 1024, "api_key_env": None,
    })
    workflow = db.WorkflowRunModel(
        project_id=project.id, workflow_type="chapter_pipeline", scope_type="chapter",
        scope_id=chapter.id, status="completed", metadata_json={},
    )
    session.add(workflow)
    await session.commit()
    original_requests, original_results = [], []

    async def historical_completion(_session, _settings, request):
        original_requests.append(request)
        result = llm.LLMCompletionResult(
            content="not json" if len(original_requests) == 1 else '{"facts": []}',
            provider="openai", model_name=settings.llm.editor.model,
            input_tokens=3, output_tokens=4, finish_reason="stop", llm_run_id=uuid4(),
        )
        original_results.append(result)
        return result

    # Capture the old request constructors themselves, rather than duplicating
    # their prompt, repair diagnostics, operation identity, or parser error.
    with monkeypatch.context() as patch:
        patch.setattr(continuity, "complete_text", historical_completion)
        snapshot = await continuity.extract_chapter_state_snapshot(
            session, settings, project_id=project.id, chapter=chapter,
            chapter_md=draft.content_md, source_chapter_draft_version_id=draft.id,
            workflow_run_id=workflow.id,
        )
    assert snapshot.extraction_status == "ok_promoted"
    assert len(original_requests) == 2
    assert admission.PREVIEW_KEY not in draft.promotion_metadata
    scope = project.metadata_json["execution_scope_id"]
    now = datetime.now(UTC)
    session.add(db.ExecutionBudgetModel(
        scope_id=scope, status="active", calls_used=2, tokens_charged=14,
        max_calls=2, max_tokens=14, deadline_at=now + timedelta(hours=1),
        metadata_json={"historical_paid_repair": "preserved"},
    ))
    keys = []
    for request, result in zip(original_requests, original_results, strict=True):
        input_hash = llm._effective_input_hash(request, settings.llm.editor)
        key = hashlib.sha256(f"{scope}\n{request.operation_id}".encode()).hexdigest()
        keys.append(key)
        attempt_id = uuid4().hex
        origin = {
            "schema": "paid-call-origin.v1", "status": "recorded",
            "project_id": str(project.id), "workflow_run_id": str(workflow.id),
            "step_run_id": None, "logical_role": "editor",
            "prompt_template": request.prompt_template,
            "prompt_version": request.prompt_version, "prompt_hash": input_hash,
        }
        result.metadata = {
            "paid_call_origin": origin,
            "effective_input_receipt": {"schema": "effective-input.v1",
                                        "hash": input_hash, "model": result.model_name},
            "execution_receipt": {
                "policy": "execution-budget.v1", "scope_id": scope, "operation_key": key,
                "attempts": [{"attempt_id": attempt_id, "status": "completed",
                              "model": result.model_name, "effective_input_hash": input_hash,
                              "reserved_tokens": 1024, "wire_body_hash": "a" * 64}],
            },
        }
        saved = result.model_dump(mode="json")
        session.add_all([
            db.ExecutionOperationModel(
                key=key, scope_id=scope, input_hash=input_hash, status="completed",
                owner="original-dispatcher", lease_until=now, result_json=deepcopy(saved),
            ),
            db.ExecutionAttemptModel(
                id=attempt_id, operation_key=key, scope_id=scope, model_name=result.model_name,
                input_hash=input_hash, reserved_tokens=1024, actual_tokens=7,
                status="completed", created_at=now,
            ),
            db.ExecutionOperationModel(
                key=hashlib.sha256(attempt_id.encode()).hexdigest(), scope_id=scope,
                input_hash=input_hash, status="transport_result", owner="original-dispatcher",
                lease_until=now, result_json={"schema": "transport-result.v1",
                    "operation_key": key, "input_hash": input_hash, "attempt_id": attempt_id,
                    "result": deepcopy(saved)},
            ),
            db.LlmRunModel(
                id=result.llm_run_id, project_id=project.id, workflow_run_id=workflow.id,
                logical_role="editor", provider=result.provider, model_name=result.model_name,
                prompt_template=request.prompt_template, prompt_version=request.prompt_version,
                prompt_hash=input_hash, input_tokens=3, output_tokens=4, finish_reason="stop",
                metadata_json=deepcopy(result.metadata),
            ),
        ])
    await session.commit()
    before = await financial_rows(session)
    body = draft.content_md
    new_requests, restored_keys, provider_calls = [], [], []

    async def forbidden_provider(*args, **kwargs):
        provider_calls.append(True)
        raise AssertionError("Paid repair replay must never send or reserve again")

    async def native_cached_completion(current_session, current_settings, request):
        new_requests.append(request)
        result = await llm.complete_text(current_session, current_settings, request)
        restored_keys.append(result.metadata["reused_operation_key"])
        return result

    monkeypatch.setattr(llm, "_call_litellm_with_retry", forbidden_provider)
    monkeypatch.setattr(admission, "complete_text", native_cached_completion)
    with bind_execution_scope(scope), llm.bind_execution_operation("original-preview-phase"):
        for _ in range(2):
            preview = await admission.extract_continuity_admission_preview(
                session, settings, project_id=project.id, chapter=chapter,
                draft=draft, workflow_run_id=workflow.id,
            )
            assert preview.raw_extraction == original_results[1].content
    assert provider_calls == []
    assert restored_keys == keys and len(new_requests) == 2
    for old, new in zip(original_requests, new_requests, strict=True):
        assert old.operation_id == new.operation_id
        assert old.model_dump(exclude={"metadata"}) == new.model_dump(exclude={"metadata"})
        assert wire_bytes(old, settings.llm.editor) == wire_bytes(new, settings.llm.editor)
        assert llm._effective_input_hash(old, settings.llm.editor) == llm._effective_input_hash(
            new, settings.llm.editor,
        )
    assert await financial_rows(session) == before
    assert draft.content_md == body and draft.promotion_state == "promoted"
    assert draft.promotion_metadata[admission.PREVIEW_KEY]["llm_run_id"] == str(
        original_results[1].llm_run_id,
    )
