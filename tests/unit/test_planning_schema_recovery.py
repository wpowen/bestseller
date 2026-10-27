"""Native V4 recovery with actual SQLite and full retained physical receipts.

Only the new completion boundary is replaced. Readers, schema validators,
source comparison, file custody, execution rows and cache reads are real.
"""

from copy import deepcopy
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import socket
from uuid import uuid4

import pytest
from sqlalchemy import JSON, MetaData, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.schema import CreateTable
from test_retained_planning_generation import MODELS
from test_retained_planning_generation import case as case

from bestseller.infra.db.models import (
    BookProductionControlModel,
    ExecutionAttemptModel,
    PlanningArtifactVersionModel,
)
from bestseller.services import llm, planner
from bestseller.services import planning_schema_recovery as recovery
from bestseller.services import retained_review_input_proof as proof
from bestseller.services.budget import bind_execution_scope, current_execution_scope
from bestseller.services.lossless_source_encoding import decode_lossless_source
from bestseller.services.planning_field_corrections import correction_contract
from bestseller.services.source_bound_planning_content import (
    VOLUME_PLANNING_PROMPT_VERSION,
    decode_source_bound_planning_document,
)
from bestseller.services.source_bound_planning_submission import (
    VOLUME_NATIVE_POLICY,
    PlanningSubmissionError,
    _validate_wire,
    parse_planning_submission,
    planning_submission_contract,
    planning_submission_tool,
    wire_diagnostics,
)

pytestmark = [
    pytest.mark.unit,
    pytest.mark.asyncio,
    pytest.mark.parametrize("case", ["volume_plan"], indirect=True),
]

PATCH = {
    "$/volumes/0/volume_number": 1,
    "$/volumes/0/chapter_count_target": 30,
    "$/volumes/0/volume_resolution/goal_achieved": True,
    "$/volumes/0/key_reveals": ["The signal can be reproduced", "The error is public"],
    "$/volumes/0/foreshadowing_planted": ["A second instrument records the same signal"],
    "$/volumes/0/foreshadowing_paid_off": ["Both observations agree"],
}


def canonical(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def sha(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("schema recovery tests forbid network/PG/providers")

    monkeypatch.setenv("BESTSELLER_LINEAGE_PG", "0")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)


@pytest.fixture
async def native(case, request):
    """Upgrade only this fixture's old envelope to a rejected native V4 return."""
    assert case.session.bind.dialect.name == "sqlite"
    include_meta = getattr(request, "param", True)
    table = BookProductionControlModel.__table__.to_metadata(MetaData())
    for column in table.c:
        if isinstance(column.type, JSONB):
            column.type = JSON()
    conn = await case.session.connection()
    await conn.run_sync(lambda c: c.execute(CreateTable(table, include_foreign_key_constraints=[])))
    request = case.args["current_request"].model_copy(
        deep=True,
        update={
            "workflow_run_id": case.original.id,
            "prompt_version": VOLUME_PLANNING_PROMPT_VERSION,
            "max_tokens_override": 8192,
            "tools": [planning_submission_tool("volume_plan", version=VOLUME_NATIVE_POLICY)],
        },
    )
    document, suffix = recovery._document(request.user_prompt)
    logical = decode_source_bound_planning_document(document)
    assert suffix == planner._STRUCTURED_FUNCTION_SUFFIX
    assert (
        logical["current_output_contract"]["native_submission"]["contract_version"]
        == VOLUME_NATIVE_POLICY
    )
    # Retain unknown authored objects and terminal source text as data.
    logical["existing_planning_assets"]["opaque_archive"] = {
        "unknown": [{"$ref": "#/authored-literal", "unset": None}, "§0", "end-of-source🌕"]
    }
    request.user_prompt = canonical(logical) + suffix
    request.metadata["input_hash"] = planner._planner_artifact_input_hash(
        logical_name="volume_plan",
        system_prompt=request.system_prompt,
        user_prompt=request.user_prompt,
        fallback_payload=[{"volume_number": 1, "chapter_count_target": 30}],
    )
    catalog, _ = proof._fresh_catalog()
    role = llm._apply_conception_phase_settings(
        request, proof._fresh_role(case.settings, case.project, request, catalog)
    )
    effective = llm._effective_input_hash(request, role)
    value = {
        "volumes": [
            {
                **deepcopy(case.payload[0]),
                "volume_number": "1",
                "chapter_count_target": "30",
                "word_count_target": "78000",
                "volume_resolution": {"goal_achieved": "true", "cost_paid": "Time"},
                "key_reveals": {"item": deepcopy(PATCH["$/volumes/0/key_reveals"])},
                "foreshadowing_planted": {
                    "item": deepcopy(PATCH["$/volumes/0/foreshadowing_planted"])
                },
                "foreshadowing_paid_off": {
                    "item": deepcopy(PATCH["$/volumes/0/foreshadowing_paid_off"])
                },
                "unknown_authored": {
                    "literal": "§0 and $ref remain story data\n terminal line",
                    "unset": None,
                },
                "_meta": {"archival_note": "not an approval"},
            }
        ]
    }
    if not include_meta:
        value["volumes"][0].pop("_meta")
    arguments = json.dumps(value, ensure_ascii=False, indent=2)
    calls = [
        {
            "id": "native-v4-rejected",
            "type": "function",
            "function": {"name": "submit_volume_plan", "arguments": arguments},
        }
    ]
    execution = deepcopy(case.run.metadata_json["execution_receipt"])
    execution["attempts"][0]["effective_input_hash"] = effective
    # Same credential-free receipt fields emitted by the real gateway.
    receipt = {
        "schema": "effective-input.v1",
        "hash": effective,
        "model": role.model,
        "messages_source": "system_user",
        "tools_included": True,
        "legacy_prompt_hash": llm._hash_prompt(request.system_prompt, request.user_prompt),
    }
    paid = case.paid.model_copy(
        deep=True,
        update={
            "tool_calls": calls,
            "raw_message": {"role": "assistant", "content": "", "tool_calls": deepcopy(calls)},
            "metadata": {
                "paid_call_origin": llm._paid_call_origin(request, effective),
                "execution_receipt": execution,
                "effective_input_receipt": receipt,
            },
        },
    )
    with llm.bind_project_prompt_audit(case.project.id, reason="native V4 SQLite fixture"):
        request_ref, response_ref, reason, replayable = llm._persist_prompt_payloads(
            settings=case.settings,
            request=request,
            llm_run_id=case.run.id,
            configured_model=role.model,
            effective_model=role.model,
            provider=paid.provider,
            effective_temperature=role.temperature,
            effective_max_tokens=8192,
            effective_timeout_seconds=role.timeout_seconds,
            effective_stream=role.stream,
            effective_thinking_type=llm._effective_thinking_type(role),
            effective_reasoning_effort=role.reasoning_effort,
            content=paid.content,
            tool_call_count=1,
            tool_calls=paid.tool_calls,
            raw_message=paid.raw_message,
            finish_reason=paid.finish_reason,
            fallback_used=False,
            fallback_source=None,
            input_tokens=13,
            output_tokens=17,
        )
    assert replayable and reason is None
    case.run.prompt_version = VOLUME_PLANNING_PROMPT_VERSION
    case.run.prompt_hash = case.operation.input_hash = case.attempt.input_hash = effective
    case.run.request_payload_ref, case.run.response_payload_ref = request_ref, response_ref
    case.run.metadata_json = {
        "execution_receipt": execution,
        "effective_input_receipt": receipt,
        "input_hash": request.metadata["input_hash"],
    }
    case.operation.result_json = paid.model_dump(mode="json")
    case.transport.input_hash = effective
    case.transport.result_json = {
        **case.transport.result_json,
        "input_hash": effective,
        "result": paid.model_dump(mode="json"),
    }
    with pytest.raises(PlanningSubmissionError, match="planning_submission:schema_type:") as caught:
        parse_planning_submission(paid, kind="volume_plan", version=VOLUME_NATIVE_POLICY)
    case.original.metadata_json = {
        **case.original.metadata_json,
        "planning_submissions": {str(case.run.id): deepcopy(caught.value.receipt)},
    }
    await case.session.commit()
    case.schema = planning_submission_contract("volume_plan", version=VOLUME_NATIVE_POLICY)[
        "json_schema"
    ]
    errors = wire_diagnostics(value, case.schema, case.schema)
    assert len(errors) == 6 and all(e.startswith("schema_type:") for e in errors)
    assert correction_contract(value, case.schema)["required"] == sorted(PATCH)
    case.value, case.paid, case.request, case.role = value, paid, request, role
    case.user, case.system, case.logical = canonical(logical), request.system_prompt, logical
    case.projection = [{"volume_number": 1, "chapter_count_target": 30}]
    case.paths = {
        "request": Path(case.settings.artifact_store.local_dir) / request_ref,
        "response": Path(case.settings.artifact_store.local_dir) / response_ref,
    }
    case.dispatch_checks, case.patch_calls = [], []
    return case


async def rows(case):
    """Column snapshots cannot share the mutable ORM objects under test."""
    result = {}
    with case.session.no_autoflush:
        for model in (*MODELS, BookProductionControlModel):
            values = await case.session.execute(select(*model.__table__.c))
            result[model.__tablename__] = deepcopy([dict(row) for row in values.mappings()])
    return result


def files(case):
    return {key: path.read_bytes() for key, path in case.paths.items()}


async def find(case, *, user=None):
    with bind_execution_scope(case.budget.scope_id):
        return await recovery.find_retained_source(
            case.session,
            case.settings,
            project=case.project,
            user=case.user if user is None else user,
        )


def install_patch_gateway(case, monkeypatch, patches):
    async def complete(session, settings, request):
        assert session is case.session and not session.in_nested_transaction()
        assert current_execution_scope() == case.budget.scope_id
        assert request.prompt_template == recovery.TEMPLATE
        assert request.prompt_version == recovery.PROMPT_VERSION
        assert request.project_id == case.project.id and request.workflow_run_id == case.consumer.id
        assert not request.tools and request.max_tokens_override == 8192
        assert settings.artifact_store.retain_prompt_payloads
        assert len(case.patch_calls) < len(patches), "unexpected new model/reroll request"
        case.patch_calls.append(request.model_copy(deep=True))
        patch = patches[len(case.patch_calls) - 1]
        return llm.LLMCompletionResult(
            content=canonical(patch),
            provider="openai",
            model_name=case.role.model,
            finish_reason="stop",
            llm_run_id=uuid4(),
            input_tokens=7,
            output_tokens=11,
        )

    monkeypatch.setattr(llm, "complete_text", complete)


async def recover(case):
    async def before_dispatch():
        assert current_execution_scope() == case.budget.scope_id
        owner = await case.session.get(type(case.consumer), case.consumer.id)
        assert owner.project_id == case.project.id and owner.status == "running"
        case.dispatch_checks.append(True)

    with bind_execution_scope(case.budget.scope_id):
        return await recovery.recover(
            case.session,
            case.settings,
            project=case.project,
            system=case.system,
            user=case.user,
            projection=case.projection,
            workflow_run_id=case.consumer.id,
            validate=case.args["validator"],
            before_dispatch=before_dispatch,
        )


async def test_find_is_read_only_and_binds_full_v4_source_and_physical_custody(native):
    case = native
    before, physical, writes = await rows(case), files(case), len(case.writes)
    result = await find(case)
    assert result.value == case.value and result.completion.raw_message == case.paid.raw_message
    assert result.schema == case.schema
    assert result.proof["corrected_paths"] == sorted(PATCH)
    assert (
        result.proof["original_request_file_sha256"]
        == hashlib.sha256(physical["request"]).hexdigest()
    )
    assert (
        result.proof["original_response_file_sha256"]
        == hashlib.sha256(physical["response"]).hexdigest()
    )
    assert result.proof["original_operation_key"] == case.operation.key
    assert result.proof["original_llm_run_id"] == str(case.run.id)
    assert result.proof["original_effective_input_hash"] == case.run.prompt_hash
    assert (
        result.proof["original_rejected_submission"]["raw_arguments_sha256"]
        == hashlib.sha256(case.paid.tool_calls[0]["function"]["arguments"].encode()).hexdigest()
    )
    assert result.proof["quality_approval_granted"] is False
    assert await rows(case) == before and files(case) == physical
    assert len(case.writes) == writes and not case.denied
    result.value["volumes"][0]["volume_title"] = "caller copy"
    assert (await find(case)).value == case.value


async def test_find_preserves_dirty_caller_state_without_autoflush(native):
    case = native
    before, writes = await rows(case), len(case.writes)
    case.consumer.metadata_json = {**case.consumer.metadata_json, "pending_caller_edit": True}
    assert await find(case)
    assert case.consumer in case.session.dirty
    assert await rows(case) == before and len(case.writes) == writes


@pytest.mark.parametrize(
    "field", ["world_spec", "cast_spec", "canonical_snapshot", "opaque_archive"]
)
async def test_entire_source_change_never_reuses_old_return_or_dispatches(native, field):
    case = native
    logical = deepcopy(case.logical)
    source = (
        logical["approved_sources"]
        if field == "canonical_snapshot"
        else logical["existing_planning_assets"]
    )
    source[field] = {"unrelated": "changed at the terminal source field"}
    before, physical, writes = await rows(case), files(case), len(case.writes)
    assert await find(case, user=canonical(logical)) is None
    assert await rows(case) == before and files(case) == physical
    assert len(case.writes) == writes and not case.denied


async def test_only_top_level_runtime_capacity_leaf_may_differ(native):
    case = native
    logical = deepcopy(case.logical)
    logical["prompt_capacity"]["user_chars"] += 1
    assert (await find(case, user=canonical(logical))).value == case.value


@pytest.mark.parametrize(
    "fault",
    [
        "request_missing",
        "request_truncated",
        "request_identity",
        "request_tool_receipt",
        "response_missing",
        "response_truncated",
        "response_raw",
        "response_tool_receipt",
        "role_route",
        "effective_hash",
        "owner_project",
        "owner_scope",
        "budget_unknown",
        "root_unknown",
        "attempt_unknown",
        "attempt_usage",
        "extra_attempt",
        "transport_missing",
        "transport_attempt",
        "transport_input_hash",
        "return_mismatch",
        "effective_receipt_schema",
    ],
)
async def test_incomplete_or_foreign_physical_financial_evidence_is_rejected(native, fault):
    case = native
    if fault.endswith("missing"):
        if fault == "transport_missing":
            await case.session.delete(case.transport)
        else:
            case.paths[fault.split("_")[0]].unlink()
    elif fault.startswith(("request_", "response_")):
        kind = fault.split("_")[0]
        doc = json.loads(case.paths[kind].read_text())
        if fault == "request_truncated":
            doc["messages"]["user"]["truncated"] = True
        elif fault == "request_identity":
            doc["workflow_run_id"] = str(uuid4())
        elif fault == "request_tool_receipt":
            doc["structured_payload_receipts"]["tools"]["sha256"] = "0" * 64
        elif fault == "response_truncated":
            doc["content"]["truncated"] = True
        elif fault == "response_raw":
            doc["raw_message"]["role"] = "user"
        else:
            doc["structured_payload_receipts"]["tool_calls"]["sha256"] = "0" * 64
        case.paths[kind].write_text(json.dumps(doc))
    elif fault == "role_route":
        catalog = json.loads(case.catalog_path.read_text())
        catalog["models"][0]["api_base"] = "https://unrelated.invalid/v1"
        case.catalog_path.write_text(json.dumps(catalog))
    elif fault == "effective_hash":
        case.run.prompt_hash = "0" * 64
    elif fault == "owner_project":
        case.original.project_id = uuid4()
    elif fault == "owner_scope":
        case.original.metadata_json = {
            **case.original.metadata_json,
            "execution_scope_id": "foreign",
        }
    elif fault == "budget_unknown":
        case.budget.status = "unknown"
    elif fault == "root_unknown":
        case.operation.status = "unknown"
    elif fault == "attempt_unknown":
        case.attempt.status = "unknown"
    elif fault == "attempt_usage":
        case.attempt.actual_tokens += 1
    elif fault == "extra_attempt":
        case.session.add(
            ExecutionAttemptModel(
                id=uuid4().hex,
                scope_id=case.budget.scope_id,
                operation_key=case.operation.key,
                input_hash=case.run.prompt_hash,
                model_name=case.run.model_name,
                status="completed",
                reserved_tokens=30,
                actual_tokens=30,
                created_at=datetime.now(UTC),
            )
        )
    elif fault == "transport_attempt":
        case.transport.result_json = {**case.transport.result_json, "attempt_id": uuid4().hex}
    elif fault == "transport_input_hash":
        case.transport.input_hash = "0" * 64
    elif fault == "return_mismatch":
        doc = deepcopy(case.transport.result_json)
        doc["result"]["content"] = "different paid return"
        case.transport.result_json = doc
    else:
        case.run.metadata_json = {
            **case.run.metadata_json,
            "effective_input_receipt": {"schema": "unknown", "hash": case.run.prompt_hash},
        }
    await case.session.commit()
    before, physical, writes = (
        await rows(case),
        {key: path.read_bytes() for key, path in case.paths.items() if path.exists()},
        len(case.writes),
    )
    with pytest.raises((ValueError, FileNotFoundError)):
        await find(case)
    assert await rows(case) == before
    assert physical == {key: path.read_bytes() for key, path in case.paths.items() if path.exists()}
    assert len(case.writes) == writes and not case.denied


@pytest.mark.parametrize("native", [True, False], indirect=True, ids=["original-meta", "no-meta"])
async def test_model_corrects_only_failed_fields_and_approved_cache_replay_is_zero(
    native, monkeypatch
):
    case = native
    before, physical = await rows(case), files(case)
    install_patch_gateway(case, monkeypatch, [PATCH])
    result, run_id = await recover(case)
    assert run_id is None and len(case.patch_calls) == 1 and len(case.dispatch_checks) == 2
    clean = [{k: deepcopy(v) for k, v in row.items() if k != "_meta"} for row in result]
    expected = deepcopy(case.value)
    for path, replacement in PATCH.items():
        parent = expected
        tokens = path[2:].split("/")
        for token in tokens[:-1]:
            parent = parent[int(token)] if isinstance(parent, list) else parent[token]
        parent[tokens[-1]] = deepcopy(replacement)
    assert clean == [{k: v for k, v in row.items() if k != "_meta"} for row in expected["volumes"]]
    if "_meta" in case.value["volumes"][0]:
        assert result[0]["_meta"]["archival_note"] == "not an approval"
    assert result[0]["word_count_target"] == "78000"
    _validate_wire(expected, case.schema, case.schema)
    receipt = result[0]["_meta"]["field_recovery_receipt"]
    assert receipt["result_payload_sha256"] == sha(expected)
    assert receipt["corrected_paths"] == sorted(PATCH)
    assert receipt["original_volume_metadata"] == [
        {
            "present": "_meta" in case.value["volumes"][0],
            "value": deepcopy(case.value["volumes"][0].get("_meta")),
        }
    ]
    assert receipt["representation_only_verified"] and receipt["patch_origin_verified"]
    assert (
        not receipt["single_provider_payload_claimed"] and not receipt["quality_approval_granted"]
    )
    assert result[0]["_meta"]["approved_complete"] is False
    request = case.patch_calls[0]
    wire = json.loads(request.user_prompt[len(recovery.WIRE_INSTRUCTION) :])
    assert wire["planning_document"]["schema"] == "lossless-source-encoding.v4"
    restored = decode_lossless_source(wire["planning_document"])
    assert restored["complete_current_source"] == case.logical
    assert restored["retained_candidate"] == case.value
    assert restored["original_native_schema"] == case.schema
    assert wire["current_correction_contract"]["required"] == sorted(PATCH)
    after = await rows(case)
    for table in before.keys() - {"workflow_runs"}:
        assert after[table] == before[table], table
    original = next(row for row in after["workflow_runs"] if row["id"] == case.original.id)
    assert original == next(row for row in before["workflow_runs"] if row["id"] == case.original.id)
    assert files(case) == physical
    # The caller's separately approved import is a real immutable SQLite row.
    artifact = PlanningArtifactVersionModel(
        project_id=case.project.id,
        artifact_type="volume_plan",
        version_no=1,
        status="approved",
        schema_version="test-composite.v1",
        content=deepcopy(result),
    )
    case.session.add(artifact)
    await case.session.commit()
    frozen, writes = await rows(case), len(case.writes)
    replay = await recover(case)
    assert replay == (result, None) and len(case.patch_calls) == 1
    assert await rows(case) == frozen and len(case.writes) == writes and files(case) == physical
    replay[0][0]["volume_title"] = "caller mutation"
    assert (await recover(case))[0] == result and not case.denied


@pytest.mark.parametrize(
    "fault",
    [
        "semantic_number",
        "semantic_boolean",
        "semantic_items",
        "extra_field",
        "missing_field",
        "wrong_type",
    ],
)
async def test_invalid_patch_cannot_change_meaning_or_original_raw_and_ledger(
    native, monkeypatch, fault
):
    case = native
    patch = deepcopy(PATCH)
    if fault == "semantic_number":
        patch["$/volumes/0/chapter_count_target"] = 31
    elif fault == "semantic_boolean":
        patch["$/volumes/0/volume_resolution/goal_achieved"] = False
    elif fault == "semantic_items":
        patch["$/volumes/0/key_reveals"].reverse()
    elif fault == "extra_field":
        patch["$/volumes/0/volume_title"] = "Rewrite the story"
    elif fault == "missing_field":
        patch.pop("$/volumes/0/key_reveals")
    else:
        patch["$/volumes/0/volume_number"] = "1"
    install_patch_gateway(case, monkeypatch, [patch, patch])
    before, physical = await rows(case), files(case)
    with pytest.raises((ValueError, TypeError)):
        await recover(case)
    assert len(case.patch_calls) == 2
    assert await rows(case) == before and files(case) == physical
    retry = json.loads(case.patch_calls[1].user_prompt[len(recovery.WIRE_INSTRUCTION) :])
    restored = decode_lossless_source(retry["planning_document"])
    assert restored["retained_candidate"] == case.value
    assert restored["rejected_patch_for_correction_only"]["raw_json"] == canonical(patch)
    assert not case.denied


@pytest.mark.parametrize("returns_none", [False, True])
async def test_factory_schema_type_callback_never_dispatches_a_second_native(
    native, monkeypatch, tmp_path, returns_none
):
    case = native
    monkeypatch.chdir(tmp_path)
    case.settings.pipeline.planning_artifact_reuse_enabled = False
    native_calls, callbacks = [], []
    answer = (
        [{"volume_number": 1, "volume_title": "callback result", "volume_goal": "same goal"}],
        None,
    )

    async def native_return(session, settings, request):
        native_calls.append(request)
        assert len(native_calls) == 1, "whole native proposal must not reroll"
        return case.paid.model_copy(deep=True, update={"llm_run_id": uuid4()})

    async def callback():
        callbacks.append(True)
        return None if returns_none else answer

    monkeypatch.setattr(planner, "complete_text", native_return)
    kwargs = {
        "project": case.project,
        "logical_name": "volume_plan",
        "system_prompt": case.system,
        "user_prompt": case.user,
        "fallback_payload": case.projection,
        "workflow_run_id": case.consumer.id,
        "validator": case.args["validator"],
        "abort_on_fallback": True,
        "merge_fallback": False,
        "prompt_version": VOLUME_PLANNING_PROMPT_VERSION,
        "max_attempts": 4,
        "attach_default_methodology": False,
        "allow_legacy_reuse": False,
        "output_tool": planning_submission_tool("volume_plan", version=VOLUME_NATIVE_POLICY),
        "output_parser": lambda result: parse_planning_submission(
            result, kind="volume_plan", version=VOLUME_NATIVE_POLICY
        )[:2],
        "schema_error_recovery": callback,
    }
    with bind_execution_scope(case.budget.scope_id):
        if returns_none:
            with pytest.raises(planner.PlannerFallbackError, match="field recovery unavailable"):
                await planner._generate_structured_artifact(case.session, case.settings, **kwargs)
        else:
            assert (
                await planner._generate_structured_artifact(case.session, case.settings, **kwargs)
                is answer
            )
    assert len(native_calls) == len(callbacks) == 1 and not case.denied
