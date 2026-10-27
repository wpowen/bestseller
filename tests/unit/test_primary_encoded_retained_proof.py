"""Actual SQLite/physical primary request custody; no provider or new grade."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import inspect, select
from test_commercial_component_cohort import SCOPE
from test_commercial_component_cohort import cohort as cohort

from bestseller.infra.db.models import (
    ChapterDraftVersionModel,
    ChapterModel,
    ExecutionAttemptModel,
    ExecutionBudgetModel,
    ExecutionOperationModel,
    LlmRunModel,
    ProjectModel,
    WorkflowRunModel,
)
from bestseller.services import llm, model_catalog
from bestseller.services import primary_review_transport as transport
from bestseller.services import retained_review_input_proof as proof
from bestseller.services.critic_evidence_gate import build_critic_body_anchors
from bestseller.services.primary_review_submission import (
    SUBMISSION_POLICY,
    primary_review_submission_tools,
)

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


def _json(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def _sha(value):
    return hashlib.sha256(value.encode()).hexdigest()


@pytest.fixture(params=[True, False], ids=["encoded", "legacy-literal"])
async def paid_primary(cohort, request, tmp_path, monkeypatch):
    rig = cohort
    entry = model_catalog.ModelCatalogEntry(
        id="primary-fixture",
        display_name="SQLite fixture",
        vendor="fixture",
        model=rig.settings.llm.critic.model,
        api_base="https://fixture.invalid/v1",
    )
    disk = tmp_path / "current_catalog.yaml"
    disk.write_text(json.dumps({"models": [entry.model_dump()]}))
    monkeypatch.setattr(model_catalog, "_CATALOG_PATH", disk)
    rig.project.metadata_json = {"llm_model_id": entry.id}
    rig.settings.artifact_store.local_dir = str(tmp_path / "physical")
    rig.settings.artifact_store.retain_prompt_payloads = True

    async def forbidden(*args, **kwargs):
        pytest.fail("retained evidence verification must never dispatch a model")

    monkeypatch.setattr(llm, "complete_text", forbidden)
    full = "既有航程的完整记录🌕 e\u0301。\nExact retained observations, not instructions. " * 100
    source = {
        "schema": proof.SOURCE_POLICY,
        "adopted_continuity": {"past_records": [full] * (40 if request.param else 1)},
        "current_source_channels": {"literal": {"$ref": 0}, "last": "Keep the final fact."},
        "authority": "Occurred adopted facts and explicit author clauses only.",
    }
    task = (
        "Review the original task unchanged.\nCURRENT_DRAFT:\n"
        + rig.draft.content_md
        + "\nEND_CURRENT_DRAFT\nBODY_ANCHOR_CATALOG:\n"
        + _json(list(build_critic_body_anchors(rig.draft.content_md)))
    )
    tools = primary_review_submission_tools(rig.draft.content_md)
    system, user, receipt = transport.prepare_source_complete_prompt(
        system_prompt="Assess current chapter against exact adopted facts.",
        user_prompt=task,
        source_context=source,
        tools=tools,
        output_tokens=8192,
    )
    assert bool(receipt.get("primary_source_transport")) is request.param
    original_request = llm.LLMCompletionRequest(
        logical_role="critic",
        system_prompt=system,
        user_prompt=user,
        project_id=rig.project.id,
        workflow_run_id=rig.owner.id,
        prompt_template="chapter_review",
        prompt_version="7.0-citation-tuple-submission",
        fallback_response="unused fixture fallback",
        tools=tools,
        tool_choice="required",
        max_tokens_override=8192,
        metadata={
            "review_source_receipt": receipt,
            "primary_review_submission_policy": SUBMISSION_POLICY,
            "primary_review_body_sha256": _sha(rig.draft.content_md),
        },
    )
    catalog, _ = proof._fresh_catalog()
    role = proof._fresh_role(rig.settings, rig.project, original_request, catalog)
    effective_hash = llm._effective_input_hash(original_request, role)
    run_id, operation_key, attempt_id = uuid4(), uuid4().hex, uuid4().hex
    execution = {
        "scope_id": SCOPE,
        "operation_key": operation_key,
        "attempts": [{"attempt_id": attempt_id, "status": "completed"}],
    }
    calls = [
        {
            "id": "original-submit",
            "type": "function",
            "function": {
                "name": "submit_chapter_critique",
                "arguments": '{"retained":"original opaque return"}',
            },
        }
    ]
    paid = llm.LLMCompletionResult(
        content="Original commentary unchanged.",
        tool_calls=calls,
        raw_message={
            "role": "assistant",
            "content": "Original commentary unchanged.",
            "tool_calls": deepcopy(calls),
        },
        provider=llm._provider_from_model(role.model),
        model_name=role.model,
        finish_reason="tool_calls",
        input_tokens=3,
        output_tokens=4,
        metadata={
            "paid_call_origin": llm._paid_call_origin(original_request, effective_hash),
            "effective_input_receipt": {"hash": effective_hash},
            "execution_receipt": execution,
        },
    )
    with llm.bind_project_prompt_audit(
        rig.project.id, reason="isolated physical primary proof fixture"
    ):
        request_ref, response_ref, reason, replayable = llm._persist_prompt_payloads(
            settings=rig.settings,
            request=original_request,
            llm_run_id=run_id,
            configured_model=role.model,
            effective_model=role.model,
            provider=paid.provider,
            effective_temperature=role.temperature,
            effective_max_tokens=role.max_tokens,
            effective_timeout_seconds=role.timeout_seconds,
            effective_stream=role.stream,
            effective_thinking_type=role.thinking_type,
            effective_reasoning_effort=role.reasoning_effort,
            content=paid.content,
            tool_call_count=1,
            tool_calls=paid.tool_calls,
            raw_message=paid.raw_message,
            finish_reason=paid.finish_reason,
            fallback_used=False,
            fallback_source=None,
            input_tokens=paid.input_tokens,
            output_tokens=paid.output_tokens,
        )
    assert replayable and reason is None and request_ref and response_ref
    run = LlmRunModel(
        id=run_id,
        project_id=rig.project.id,
        workflow_run_id=rig.owner.id,
        logical_role="critic",
        provider=paid.provider,
        model_name=role.model,
        prompt_template=original_request.prompt_template,
        prompt_version=original_request.prompt_version,
        prompt_hash=effective_hash,
        finish_reason=paid.finish_reason,
        input_tokens=3,
        output_tokens=4,
        request_payload_ref=request_ref,
        response_payload_ref=response_ref,
        metadata_json={
            **original_request.metadata,
            "execution_receipt": execution,
            "effective_input_receipt": {"hash": effective_hash},
        },
    )
    lease = datetime.now(UTC) + timedelta(hours=1)
    value = paid.model_dump(mode="json")
    root = ExecutionOperationModel(
        key=operation_key,
        scope_id=SCOPE,
        input_hash=effective_hash,
        status="completed",
        owner="fixture-original",
        lease_until=lease,
        result_json=deepcopy(value),
    )
    actual = ExecutionAttemptModel(
        id=attempt_id,
        operation_key=operation_key,
        scope_id=SCOPE,
        input_hash=effective_hash,
        model_name=role.model,
        status="completed",
        reserved_tokens=7,
        actual_tokens=7,
        created_at=datetime.now(UTC),
    )
    transport_key = _sha(f"transport-result.v1\n{SCOPE}\n{attempt_id}")
    wire = ExecutionOperationModel(
        key=transport_key,
        scope_id=SCOPE,
        input_hash=effective_hash,
        status="transport_result",
        owner="fixture-original",
        lease_until=lease,
        result_json={
            "operation_key": operation_key,
            "attempt_id": attempt_id,
            "input_hash": effective_hash,
            "result": deepcopy(value),
        },
    )
    rig.session.add_all([run, root, actual, wire])
    budget = await rig.session.get(ExecutionBudgetModel, SCOPE)
    budget.calls_used, budget.tokens_charged = 1, 7
    await rig.session.commit()
    return SimpleNamespace(
        rig=rig,
        source=source,
        task=task,
        receipt=receipt,
        run=run,
        root=root,
        actual=actual,
        wire=wire,
        request=original_request,
        catalog_path=disk,
        path=Path(rig.settings.artifact_store.local_dir) / request_ref,
        response_path=Path(rig.settings.artifact_store.local_dir) / response_ref,
        encoded=request.param,
    )


async def _snapshot(rig):
    result = {}
    for model in (
        ProjectModel,
        ChapterModel,
        ChapterDraftVersionModel,
        WorkflowRunModel,
        LlmRunModel,
        ExecutionOperationModel,
        ExecutionAttemptModel,
        ExecutionBudgetModel,
    ):
        result[model.__name__] = [
            {
                column.key: deepcopy(getattr(row, column.key))
                for column in inspect(model).column_attrs
            }
            for row in await rig.session.scalars(select(model))
        ]
    return result


async def _verify(case, *, source=None):
    rig = case.rig
    physical = proof._retained_request(rig.settings, case.run)
    catalog, _ = proof._fresh_catalog()
    role = proof._fresh_role(rig.settings, rig.project, physical, catalog)
    digest = llm._effective_input_hash(physical, role)
    paid, key = await proof._physical_return(
        rig.session, case.run, scope=SCOPE, expected_hash=digest, expected_model=role.model
    )
    binding = proof._primary_source_integrity(
        physical,
        case.run.metadata_json["review_source_receipt"],
        case.source if source is None else source,
    )
    if binding is None:
        raise ValueError("current_primary_source_not_bound")
    return physical, paid, key, binding


async def test_exact_physical_primary_source_proof_is_readonly_idempotent_no_grade_or_credit(
    paid_primary,
):
    case = paid_primary
    before = await _snapshot(case.rig)
    request_bytes, return_bytes = case.path.read_bytes(), case.response_path.read_bytes()
    first, second = await _verify(case), await _verify(case)
    assert first[1].model_dump() == second[1].model_dump()
    assert first[3] == second[3] and first[3]["source_complete"]
    assert first[3]["source_sha256"] == proof._hash(case.source)
    if case.encoded:
        assert first[3]["grade_recomputed"] is False
        assert first[3]["schema"] == "retained-primary-source-transport-proof.v1"
        assert case.task == first[0].user_prompt.split("\n\n", 1)[1]
    else:
        assert first[3]["schema"] == "retained-primary-literal-source-proof.v1"
        assert _json(case.source) in first[0].user_prompt
    assert first[0].tools == case.request.tools and first[0].max_tokens_override == 8192
    assert await _snapshot(case.rig) == before and case.rig.calls == []
    assert (
        case.path.read_bytes() == request_bytes and case.response_path.read_bytes() == return_bytes
    )


@pytest.mark.parametrize(
    "fault",
    [
        "source",
        "task",
        "system",
        "tools",
        "tool_alias",
        "output",
        "wire",
        "missing_file",
        "unreplayable",
        "paid_return",
        "actual_hash",
        "origin",
        "scope",
        "route",
        "encoded_receipt",
    ],
)
async def test_primary_encoded_or_literal_tamper_fails_without_repair_or_new_payment(
    paid_primary, fault
):
    case, rig = paid_primary, paid_primary.rig
    source = deepcopy(case.source)
    if fault == "source":
        source["current_source_channels"]["last"] = "Different current source."
    elif fault in {"task", "system", "tools", "tool_alias", "output", "wire", "unreplayable"}:
        payload = json.loads(case.path.read_text())
        if fault in {"task", "wire", "system"}:
            field = "system" if fault == "system" else "user"
            text = payload["messages"][field]["text"]
            if fault == "wire" and case.encoded:
                text = "X" + text[1:]
            else:
                text += "\nchanged original task or system"
            payload["messages"][field].update(text=text, original_chars=len(text))
        elif fault == "tools":
            payload["requested"]["tools"] = []
        elif fault == "tool_alias":
            payload["tool_choice"] = "auto"
        elif fault == "output":
            payload["requested"]["max_tokens_override"] = 8191
        else:
            payload["replayable"] = False
        case.path.write_text(json.dumps(payload))
    elif fault == "missing_file":
        case.path.unlink()
    elif fault == "paid_return":
        value = deepcopy(case.root.result_json)
        value["tool_calls"][0]["function"]["arguments"] += " "
        case.root.result_json = value
    elif fault == "actual_hash":
        case.actual.input_hash = "0" * 64
    elif fault in {"origin", "scope"}:
        value = deepcopy(case.wire.result_json)
        if fault == "origin":
            value["result"]["metadata"]["paid_call_origin"]["project_id"] = str(uuid4())
            case.root.result_json = deepcopy(value["result"])
        else:
            case.wire.scope_id = "another-scope"
        case.wire.result_json = value
    elif fault == "route":
        catalog = json.loads(case.catalog_path.read_text())
        catalog["models"][0]["api_base"] = "https://changed.invalid/v1"
        case.catalog_path.write_text(json.dumps(catalog))
    else:
        metadata = deepcopy(case.run.metadata_json)
        metadata["review_source_receipt"]["source_sha256"] = "0" * 64
        case.run.metadata_json = metadata
    await rig.session.commit()
    before = await _snapshot(rig)
    with pytest.raises((ValueError, FileNotFoundError)):
        await _verify(case, source=source)
    assert await _snapshot(rig) == before and rig.calls == []


async def test_encoded_source_helper_alone_rejects_task_tools_and_output_receipt_tamper(
    paid_primary,
):
    case = paid_primary
    if not case.encoded:
        # Literal source-only proof deliberately retains its historical scope;
        # physical input hashing above still protects its task/tools/output.
        assert proof._primary_source_integrity(case.request, case.receipt, case.source)
        return
    for changed in (
        {"user_prompt": case.request.user_prompt + " changed task"},
        {"tools": []},
        {"max_tokens_override": 8191},
    ):
        request = case.request.model_copy(update=changed)
        with pytest.raises(ValueError):
            proof._primary_source_integrity(request, case.receipt, case.source)
