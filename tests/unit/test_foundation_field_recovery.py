"""Composite foundation custody through real SQLite and retained physical rows.

Selected approved assets are fixture inputs, not approvals issued by recovery.
Only completion is inert: source matching, patch preparation/application,
domain validation, physical files and financial readers all execute normally.
"""

from copy import deepcopy
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select
from test_planning_schema_recovery import PATCH, canonical, files, rows
from test_planning_schema_recovery import case as case
from test_planning_schema_recovery import native as native
from test_planning_schema_recovery import offline as offline

from bestseller.infra.db.models import (
    ExecutionAttemptModel,
    ExecutionOperationModel,
    LlmRunModel,
    PlanningArtifactVersionModel,
)
from bestseller.services import foundation_field_recovery as foundation
from bestseller.services import llm, planner
from bestseller.services import planning_schema_recovery as recovery
from bestseller.services import retained_review_input_proof as proof
from bestseller.services.budget import bind_execution_scope
from bestseller.services.conception_snapshots import snapshot_hash
from bestseller.services.model_context_capacity import bind_selected_model_capacity
from bestseller.services.source_bound_planning_content import build_source_bound_planning_prompts
from bestseller.services.source_bound_planning_submission import (
    VOLUME_NATIVE_POLICY,
    PlanningSubmissionError,
    parse_planning_submission,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.asyncio,
    pytest.mark.parametrize("case", ["volume_plan"], indirect=True),
]


async def record_completion(case, request, *, content, calls=None, run=None):
    """Seed a complete settled fixture return, never a reader/pass-proof stub."""
    catalog, _ = proof._fresh_catalog()
    role = llm._apply_conception_phase_settings(
        request, proof._fresh_role(case.settings, case.project, request, catalog)
    )
    effective = llm._effective_input_hash(request, role)
    scope = case.budget.scope_id
    if run is None:
        run = LlmRunModel(id=uuid4())
        operation_key, attempt_id = uuid4().hex, uuid4().hex
        operation = ExecutionOperationModel(key=operation_key)
        attempt = ExecutionAttemptModel(id=attempt_id, created_at=datetime.now(UTC))
        transport = ExecutionOperationModel(
            key=hashlib.sha256(f"transport-result.v1\n{scope}\n{attempt_id}".encode()).hexdigest()
        )
        case.session.add_all([run, operation, attempt, transport])
    else:
        operation_key = run.metadata_json["execution_receipt"]["operation_key"]
        operation = await case.session.get(ExecutionOperationModel, operation_key)
        attempt_id = run.metadata_json["execution_receipt"]["attempts"][0]["attempt_id"]
        attempt = await case.session.get(ExecutionAttemptModel, attempt_id)
        transport = await case.session.get(
            ExecutionOperationModel,
            hashlib.sha256(f"transport-result.v1\n{scope}\n{attempt_id}".encode()).hexdigest(),
        )
    execution = {
        "scope_id": scope,
        "operation_key": operation_key,
        "attempts": [
            {"attempt_id": attempt_id, "status": "completed", "effective_input_hash": effective}
        ],
    }
    effective_receipt = {
        "schema": "effective-input.v1",
        "hash": effective,
        "model": role.model,
        "messages_source": "system_user",
        "tools_included": bool(request.tools),
        "legacy_prompt_hash": llm._hash_prompt(request.system_prompt, request.user_prompt),
    }
    input_tokens, output_tokens = (13, 17) if calls else (7, 11)
    raw_message = {"role": "assistant", "content": content}
    if calls:
        raw_message["tool_calls"] = deepcopy(calls)
    paid = llm.LLMCompletionResult(
        llm_run_id=run.id,
        content=content,
        tool_calls=deepcopy(calls),
        raw_message=raw_message,
        provider=llm._provider_from_model(role.model),
        model_name=role.model,
        finish_reason="tool_calls" if calls else "stop",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        metadata={
            "execution_receipt": execution,
            "effective_input_receipt": effective_receipt,
            "paid_call_origin": llm._paid_call_origin(request, effective),
        },
    )
    with llm.bind_project_prompt_audit(case.project.id, reason="SQLite composite custody fixture"):
        request_ref, response_ref, reason, replayable = llm._persist_prompt_payloads(
            settings=case.settings,
            request=request,
            llm_run_id=run.id,
            configured_model=role.model,
            effective_model=role.model,
            provider=paid.provider,
            effective_temperature=role.temperature,
            effective_max_tokens=request.max_tokens_override,
            effective_timeout_seconds=role.timeout_seconds,
            effective_stream=role.stream,
            effective_thinking_type=llm._effective_thinking_type(role),
            effective_reasoning_effort=role.reasoning_effort,
            content=content,
            tool_call_count=len(calls or []),
            tool_calls=paid.tool_calls,
            raw_message=raw_message,
            finish_reason=paid.finish_reason,
            fallback_used=False,
            fallback_source=None,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
    assert replayable and reason is None
    for key, value in {
        "project_id": request.project_id,
        "workflow_run_id": request.workflow_run_id,
        "logical_role": request.logical_role,
        "prompt_template": request.prompt_template,
        "prompt_version": request.prompt_version,
        "provider": paid.provider,
        "model_name": role.model,
        "prompt_hash": effective,
        "finish_reason": paid.finish_reason,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "request_payload_ref": request_ref,
        "response_payload_ref": response_ref,
        "metadata_json": {
            "execution_receipt": execution,
            "effective_input_receipt": effective_receipt,
            "input_hash": request.metadata.get("input_hash"),
        },
    }.items():
        setattr(run, key, value)
    for item in (operation, transport):
        item.scope_id, item.input_hash = scope, effective
        item.owner, item.lease_until = "offline-fixture", case.budget.deadline_at
    operation.status, operation.result_json = "completed", paid.model_dump(mode="json")
    transport.status = "transport_result"
    transport.result_json = {
        "schema": "transport-result.v1",
        "attempt_id": attempt_id,
        "operation_key": operation_key,
        "input_hash": effective,
        "result": paid.model_dump(mode="json"),
    }
    attempt.scope_id, attempt.operation_key, attempt.input_hash = scope, operation_key, effective
    attempt.model_name, attempt.status = role.model, "completed"
    attempt.reserved_tokens = attempt.actual_tokens = input_tokens + output_tokens
    for kind, ref in (("request", request_ref), ("response", response_ref)):
        case.paths[f"{run.id}:{kind}"] = Path(case.settings.artifact_store.local_dir) / ref
    await case.session.flush()
    return paid, run


@pytest.fixture
async def recovered(native, monkeypatch, request):
    case = native
    case.project.metadata_json = {
        **case.project.metadata_json,
        "premise": case.canonical.content["premise"],
    }
    assets = deepcopy(case.logical["existing_planning_assets"])
    # Unknown data belongs to an actual selected asset, not an invented fourth source.
    assets["book_spec"]["_meta"] = {"opaque_archive": assets.pop("opaque_archive")}
    case.artifacts = {}
    for kind in ("book_spec", "world_spec", "cast_spec"):
        row = PlanningArtifactVersionModel(
            id=uuid4(),
            project_id=case.project.id,
            artifact_type=kind,
            status="approved",
            version_no=1,
            schema_version="v1",
            content=assets[kind],
            source_run_id=case.original.id,
        )
        case.artifacts[kind] = row
        case.session.add(row)
    case.projection = planner._compile_source_bound_volume_plan(
        case.project,
        case.project.metadata_json["premise"],
        *(deepcopy(case.artifacts[k].content) for k in ("book_spec", "world_spec", "cast_spec")),
    )
    with bind_selected_model_capacity(case.project.metadata_json):
        case.system, case.user = build_source_bound_planning_prompts(
            kind="volume_plan",
            project=case.project,
            premise=case.project.metadata_json["premise"],
            projection=case.projection,
            **{k: row.content for k, row in case.artifacts.items()},
        )
    case.request = case.request.model_copy(deep=True)
    case.request.system_prompt = case.system
    case.request.user_prompt = case.user.rstrip() + planner._STRUCTURED_FUNCTION_SUFFIX
    case.request.metadata["input_hash"] = planner._planner_artifact_input_hash(
        logical_name="volume_plan",
        system_prompt=case.system,
        user_prompt=case.request.user_prompt,
        fallback_payload=case.projection,
    )
    case.paid, _ = await record_completion(
        case,
        case.request,
        content="",
        calls=case.paid.tool_calls,
        run=case.run,
    )
    with pytest.raises(PlanningSubmissionError) as caught:
        parse_planning_submission(case.paid, kind="volume_plan", version=VOLUME_NATIVE_POLICY)
    case.original.metadata_json = {
        **case.original.metadata_json,
        "planning_submissions": {str(case.run.id): deepcopy(caught.value.receipt)},
    }
    await case.session.commit()
    case.original_rows, case.original_files = await rows(case), files(case)
    patches = [deepcopy(PATCH)]
    if getattr(request, "param", None) == "retry":
        patches.insert(0, {**deepcopy(PATCH), "$/volumes/0/volume_number": 2})
    case.patch_runs, case.patch_requests = [], []

    async def inert_completion(session, settings, patch_request):
        assert session is case.session and settings.artifact_store.retain_prompt_payloads
        assert len(case.patch_runs) < len(patches), "unexpected paid retry"
        paid, run = await record_completion(
            case,
            patch_request,
            content=canonical(patches[len(case.patch_runs)]),
        )
        case.patch_runs.append(run)
        case.patch_requests.append(patch_request.model_copy(deep=True))
        case.budget.calls_used += 1
        case.budget.tokens_charged += paid.input_tokens + paid.output_tokens
        return paid

    async def canonical_guard():
        current = await case.session.get(
            type(case.canonical), case.canonical.id, populate_existing=True
        )
        assert (
            snapshot_hash(current.content)
            == case.project.metadata_json["creation_canonical_snapshot_hash"]
        )

    denied = llm.complete_text
    monkeypatch.setattr(llm, "complete_text", inert_completion)
    with bind_execution_scope(case.budget.scope_id):
        payload, _ = await recovery.recover(
            case.session,
            case.settings,
            project=case.project,
            system=case.system,
            user=case.user,
            projection=case.projection,
            workflow_run_id=case.consumer.id,
            validate=lambda value: foundation._validate(
                value, project=case.project, projection=case.projection
            ),
            before_dispatch=canonical_guard,
        )
    monkeypatch.setattr(llm, "complete_text", denied)
    case.artifacts["volume_plan"] = PlanningArtifactVersionModel(
        id=uuid4(),
        project_id=case.project.id,
        artifact_type="volume_plan",
        status="approved",
        version_no=1,
        schema_version="v1",
        content=payload,
        source_run_id=case.consumer.id,
    )
    case.session.add(case.artifacts["volume_plan"])
    await case.session.commit()
    return case


async def verify(case):
    return await foundation.verify_recovered_volume(
        case.session,
        case.settings,
        project=case.project,
        artifacts=case.artifacts,
        projection=case.projection,
    )


async def verify_without_writes(case, *, error=None):
    before, physical, writes = await rows(case), files(case), len(case.writes)
    if error is None:
        result = await verify(case)
    else:
        with pytest.raises(ValueError, match=error):
            await verify(case)
        result = None
    assert await rows(case) == before and files(case) == physical
    assert len(case.writes) == writes and not case.denied
    return result


async def test_exact_composite_has_two_real_paid_origins_without_regrading(recovered):
    case = recovered
    receipt = await verify_without_writes(case)
    assert receipt == await verify_without_writes(case)
    assert receipt["schema"] == foundation.SCHEMA
    assert receipt["original_paid"]["llm_run_id"] == str(case.run.id)
    assert receipt["correction_paid"]["llm_run_id"] == str(case.patch_runs[0].id)
    assert receipt["retry_paid"] is None
    assert receipt["verified_producer_metadata_paths"] == []
    assert (
        receipt["author_authority"]
        is receipt["grade_asserted"]
        is receipt["credit_granted"]
        is False
    )
    assert receipt["single_provider_payload_claimed"] is False
    assert case.budget.calls_used == 2 and case.budget.tokens_charged == 48
    for key, original in case.original_files.items():
        assert files(case)[key] == original
    current = await rows(case)
    for table in ("llm_runs", "execution_attempts", "execution_operations", "workflow_runs"):
        old_by_id = {str(row.get("id", row.get("key"))): row for row in case.original_rows[table]}
        for row in current[table]:
            identity = str(row.get("id", row.get("key")))
            if identity in old_by_id and identity != str(case.consumer.id):
                assert row == old_by_id[identity]
    volume = case.artifacts["volume_plan"].content[0]
    assert volume["unknown_authored"] == case.value["volumes"][0]["unknown_authored"]
    assert volume["_meta"]["field_recovery_receipt"]["original_volume_metadata"] == [
        {"present": True, "value": {"archival_note": "not an approval"}}
    ]


@pytest.mark.parametrize("recovered", ["retry"], indirect=True)
async def test_second_patch_proves_exact_first_physical_rejection(recovered):
    case = recovered
    receipt = await verify_without_writes(case)
    assert len(case.patch_runs) == 2 and case.budget.calls_used == 3
    assert case.budget.tokens_charged == 66
    assert receipt["retry_paid"]["llm_run_id"] == str(case.patch_runs[0].id)
    assert receipt["correction_paid"]["llm_run_id"] == str(case.patch_runs[1].id)


@pytest.mark.parametrize("source", ["book_spec", "world_spec", "world_meta", "cast_spec", "premise"])
async def test_changed_selected_source_rejected_without_laundering_old_input(recovered, source):
    case = recovered
    if source == "premise":
        case.project.metadata_json = {
            **case.project.metadata_json,
            "premise": "A different authored premise",
        }
    elif source == "world_meta":
        world = deepcopy(case.artifacts["world_spec"].content)
        world["_meta"] = {**world.get("_meta", {}), "unknown_source_witness": None}
        case.artifacts["world_spec"].content = world
    else:
        case.artifacts[source].content = {
            **case.artifacts[source].content,
            "unknown_new_fact": None,
        }
    await case.session.commit()
    await verify_without_writes(case, error="world_source_changed|source_binding")


@pytest.mark.parametrize(
    "which", ["base_request", "base_response", "patch_request", "patch_response"]
)
async def test_physical_raw_conflicts_rejected(recovered, which):
    case = recovered
    run = case.run if which.startswith("base") else case.patch_runs[-1]
    kind = which.split("_")[1]
    path = case.paths[f"{run.id}:{kind}"]
    document = json.loads(path.read_text())
    if kind == "request":
        document["messages"]["user"]["text"] += "\nchanged physical input"
        document["messages"]["user"]["original_chars"] = len(document["messages"]["user"]["text"])
    else:
        document["raw_message"] = {"role": "assistant", "content": "conflicting raw evidence"}
    path.write_text(canonical(document))
    await verify_without_writes(case, error="retained_")


@pytest.mark.parametrize("fault", ["operation", "attempt", "scope", "origin", "effective_receipt"])
async def test_patch_financial_or_scope_evidence_cannot_be_replaced_by_receipt(recovered, fault):
    case = recovered
    run = case.patch_runs[-1]
    execution = run.metadata_json["execution_receipt"]
    operation = await case.session.get(ExecutionOperationModel, execution["operation_key"])
    attempt = await case.session.get(ExecutionAttemptModel, execution["attempts"][0]["attempt_id"])
    if fault == "operation":
        operation.input_hash = "0" * 64
    elif fault == "attempt":
        attempt.actual_tokens += 1
    elif fault == "scope":
        case.consumer.metadata_json = {
            **case.consumer.metadata_json,
            "execution_scope_id": "other-scope",
        }
    elif fault == "origin":
        value = deepcopy(operation.result_json)
        value["metadata"]["paid_call_origin"]["workflow_run_id"] = str(uuid4())
        operation.result_json = value
        transport_key = hashlib.sha256(
            f"transport-result.v1\n{case.budget.scope_id}\n{attempt.id}".encode()
        ).hexdigest()
        transport = await case.session.get(ExecutionOperationModel, transport_key)
        transport.result_json = {**transport.result_json, "result": deepcopy(value)}
    else:
        value = deepcopy(run.metadata_json)
        value["effective_input_receipt"]["model"] = "other-model"
        run.metadata_json = value
    await case.session.commit()
    await verify_without_writes(case, error="retained_|foundation_field_recovery:")


@pytest.mark.parametrize(
    "fault",
    [
        "body",
        "body_boolean_number",
        "raw_digest",
        "patch_digest",
        "original_meta",
        "input_hash",
        "projection_hash",
        "single_provider",
        "grade_flag",
    ],
)
async def test_composite_body_and_receipt_are_not_self_authenticating(recovered, fault):
    case = recovered
    content = deepcopy(case.artifacts["volume_plan"].content)
    meta = content[0]["_meta"]
    receipt = meta["field_recovery_receipt"]
    if fault == "body":
        content[0]["unknown_authored"]["unset"] = "invented"
    elif fault == "body_boolean_number":
        assert type(content[0]["volume_number"]) is int and content[0]["volume_number"] == 1
        content[0]["volume_number"] = True
    elif fault in {"raw_digest", "patch_digest"}:
        receipt[
            {"raw_digest": "raw_payload_sha256", "patch_digest": "patch_payload_sha256"}[fault]
        ] = "0" * 64
    elif fault == "original_meta":
        receipt["original_volume_metadata"][0]["value"]["archival_note"] = "approved"
    elif fault == "input_hash":
        meta["input_hash"] = "0" * 64
    elif fault == "projection_hash":
        meta["source_projection_sha256"] = "0" * 64
    elif fault == "single_provider":
        meta["single_provider_payload_claimed"] = True
    else:
        receipt["quality_approval_granted"] = True
    case.artifacts["volume_plan"].content = content
    await case.session.commit()
    await verify_without_writes(case, error="foundation_field_recovery:")


async def test_coherently_settled_patch_cannot_change_narrative_meaning(recovered):
    case = recovered
    patch = {**deepcopy(PATCH), "$/volumes/0/key_reveals": ["Invented discovery"]}
    await record_completion(
        case,
        case.patch_requests[-1],
        content=canonical(patch),
        run=case.patch_runs[-1],
    )
    await case.session.commit()
    await verify_without_writes(case, error="patch_changed_items")


async def test_pending_caller_changes_are_not_autoflushed(recovered):
    case = recovered
    before = await rows(case)
    case.artifacts["book_spec"].content = {
        **case.artifacts["book_spec"].content,
        "pending_source_edit": True,
    }
    case.consumer.metadata_json = {**case.consumer.metadata_json, "pending_caller_edit": True}
    await verify_without_writes(case, error="source_binding")
    assert case.artifacts["book_spec"] in case.session.dirty
    assert case.consumer in case.session.dirty
    assert await rows(case) == before


async def test_fixture_contains_actual_patch_rows_not_just_producer_metadata(recovered):
    case = recovered
    run_ids = list(await case.session.scalars(select(LlmRunModel.id)))
    assert set(run_ids) == {case.run.id, case.patch_runs[-1].id}
    for run in (case.run, case.patch_runs[-1]):
        assert case.paths[f"{run.id}:request"].is_file()
        assert case.paths[f"{run.id}:response"].is_file()
    await verify_without_writes(case)
