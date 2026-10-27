"""SQLite approved-cache identity precedes lossless physical dispatch transport."""
from copy import deepcopy
from datetime import UTC, datetime, timedelta
import hashlib
import json
from pathlib import Path
from uuid import uuid4
from unittest.mock import AsyncMock

import pytest
from test_retained_planning_generation import case as case  # noqa: F401
from test_retained_planning_generation import snapshot

from bestseller.infra.db.models import (
    ExecutionAttemptModel, ExecutionOperationModel, LlmRunModel, PlanningArtifactVersionModel,
)
from bestseller.services import llm, planner
from bestseller.services import retained_review_input_proof as proof
from bestseller.services import source_bound_planning_content as content
from bestseller.services.source_bound_planning_submission import parse_planning_submission

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def raw_document(user):
    document, end = json.JSONDecoder().raw_decode(user.lstrip())
    return content.decode_source_bound_planning_document(document), user.lstrip()[end:]


@pytest.fixture
async def cache_case(case, monkeypatch):  # noqa: F811
    request = case.args["current_request"]
    document, suffix = raw_document(request.user_prompt)
    assert suffix == planner._STRUCTURED_FUNCTION_SUFFIX
    retained = "封存的观测资料必须完整保留，末尾证据同样有效🌕。" * 150
    document["existing_planning_assets"]["additional_source_records"] = [
        {"sequence": index, "full_fact_record": retained} for index in range(22)
    ]
    document["existing_planning_assets"]["literal_author_object"] = {"$ref": 0}
    user = content._with_capacity_receipt(request.system_prompt, document)
    full_user = user + suffix
    assert content.assert_source_bound_planning_prompt_capacity(request.system_prompt, full_user)["combined_chars"] <= 120000
    with pytest.raises(content.SourceBoundPlanningCapacityError):
        content.assert_source_bound_planning_request_capacity(
            request.system_prompt, full_user, tools=request.tools, output_tokens=8192,
        )
    case.settings.pipeline.planning_artifact_reuse_enabled = True
    case.settings.pipeline.planning_artifact_reuse_allow_legacy = False
    monkeypatch.setattr(planner, "_heartbeat_planner_workflow", AsyncMock())
    monkeypatch.setattr(planner, "_planner_stage_max_tokens", lambda *args, **kwargs: 8192)
    case.system, case.user, case.logical_user = request.system_prompt, user, full_user
    case.logical_document = json.loads(user)
    case.base_hash = planner._planner_artifact_input_hash(
        logical_name="world_spec", system_prompt=case.system,
        user_prompt=full_user, fallback_payload={},
    )
    case.requests, case.new_runs, case.transport_calls = [], [], []
    return case


async def generate(case, *, max_attempts=2):
    return await planner._generate_structured_artifact(
        case.session, case.settings, project=case.project, logical_name="world_spec",
        system_prompt=case.system, user_prompt=case.user, fallback_payload={},
        workflow_run_id=case.consumer.id, validator=case.args["validator"],
        abort_on_fallback=True, merge_fallback=False, attach_default_methodology=False,
        allow_legacy_reuse=False, prompt_version=case.args["current_request"].prompt_version,
        output_tool=case.args["current_request"].tools[0],
        output_parser=lambda result: parse_planning_submission(result, kind="world_spec")[:2],
        before_dispatch=lambda: planner._assert_bound_planning_canonical(case.session, case.project),
        max_attempts=max_attempts,
    )


async def approve(case, payload):
    asset = PlanningArtifactVersionModel(
        project_id=case.project.id, artifact_type="world_spec", version_no=2,
        status="approved", schema_version="test-source-bound-asset.v1",
        content=deepcopy(payload), source_run_id=case.new_runs[-1].id if case.new_runs else None,
    )
    case.session.add(asset)
    await case.session.commit()
    return asset


def deny_provider(monkeypatch):
    async def forbidden(*args, **kwargs):
        pytest.fail("approved logical cache must precede transport and provider")
    monkeypatch.setattr(planner, "complete_text", forbidden)


async def test_approved_logical_asset_returns_before_transport_or_provider_even_when_raw_request_exceeds_budget(cache_case, monkeypatch):
    from bestseller.services import planning_dispatch_transport as transport
    case = cache_case
    payload = deepcopy(case.payload)
    payload["_meta"] = {"input_hash": case.base_hash, "approved_receipt": "must remain unchanged"}
    asset = await approve(case, payload)
    before = await snapshot(case)
    deny_provider(monkeypatch)

    def forbidden(*args, **kwargs):
        pytest.fail("approved cache hit must not even enter dispatch transport")
    monkeypatch.setattr(transport, "prepare_planning_dispatch", forbidden)
    result, run_id = await generate(case)
    assert run_id is None
    assert result["_meta"]["input_hash"] == case.base_hash
    assert result["_meta"]["reused_artifact_id"] == str(asset.id)
    assert {k: v for k, v in result.items() if k != "_meta"} == case.payload
    assert await snapshot(case) == before
    assert case.denied == []


def install_local_paid_return(case, monkeypatch, *, invalid_first=None):
    # This fixture constructs physical/SQL evidence locally; it never invokes
    # the real gateway, claims real credit, or asserts actual book acceptance.
    async def local_return(session, settings, request):
        index = len(case.requests)
        case.requests.append(request.model_copy(deep=True))
        payload = deepcopy(case.payload)
        if invalid_first == "parse" and index == 0:
            payload.pop("world_name")
        arguments = json.dumps(payload, ensure_ascii=False)
        tools = [{"id": f"local-native-{index}", "type": "function", "function": {
            "name": "submit_world_spec", "arguments": arguments,
        }}]
        raw = {"role": "assistant", "content": "", "tool_calls": deepcopy(tools)}
        catalog, _ = proof._fresh_catalog()
        role = proof._fresh_role(settings, case.project, request, catalog)
        wire_hash = llm._effective_input_hash(request, role)
        run_id, root_key, attempt_id = uuid4(), uuid4().hex, uuid4().hex
        scope = case.budget.scope_id
        execution = {"scope_id": scope, "operation_key": root_key,
                     "attempts": [{"attempt_id": attempt_id, "status": "completed"}]}
        effective = {"schema": "effective-input.v1", "hash": wire_hash}
        result = llm.LLMCompletionResult(
            content="", provider="openai", model_name=role.model, finish_reason=("length" if invalid_first == "length" and index == 0 else "tool_calls"),
            tool_calls=tools, raw_message=raw, input_tokens=13, output_tokens=17,
            metadata={"paid_call_origin": llm._paid_call_origin(request, wire_hash),
                      "execution_receipt": execution, "effective_input_receipt": effective},
        )
        with llm.bind_project_prompt_audit(case.project.id, reason="local cache/transport integration fixture"):
            request_ref, response_ref, reason, replayable = llm._persist_prompt_payloads(
                settings=settings, request=request, llm_run_id=run_id,
                configured_model=role.model, effective_model=role.model, provider=result.provider,
                effective_temperature=role.temperature, effective_max_tokens=8192,
                effective_timeout_seconds=role.timeout_seconds, effective_stream=role.stream,
                effective_thinking_type=llm._effective_thinking_type(role), effective_reasoning_effort=role.reasoning_effort,
                content=result.content, tool_call_count=1, tool_calls=tools, raw_message=raw,
                finish_reason=result.finish_reason, fallback_used=False, fallback_source=None,
                input_tokens=13, output_tokens=17,
            )
        assert reason is None and replayable
        run = LlmRunModel(
            id=run_id, project_id=case.project.id, workflow_run_id=case.consumer.id,
            logical_role="planner", provider=result.provider, model_name=role.model,
            prompt_template=request.prompt_template, prompt_version=request.prompt_version,
            prompt_hash=wire_hash, finish_reason=result.finish_reason, input_tokens=13, output_tokens=17,
            request_payload_ref=request_ref, response_payload_ref=response_ref,
            metadata_json={**request.metadata, "execution_receipt": execution, "effective_input_receipt": effective},
        )
        deadline = datetime.now(UTC) + timedelta(hours=1)
        paid = result.model_dump(mode="json")
        session.add_all([
            run,
            ExecutionOperationModel(key=root_key, scope_id=scope, input_hash=wire_hash, status="completed",
                owner="local-fixture", lease_until=deadline, result_json=paid),
            ExecutionAttemptModel(id=attempt_id, operation_key=root_key, scope_id=scope,
                input_hash=wire_hash, model_name=role.model, status="completed", reserved_tokens=30,
                actual_tokens=30, created_at=datetime.now(UTC)),
            ExecutionOperationModel(
                key=hashlib.sha256(f"transport-result.v1\n{scope}\n{attempt_id}".encode()).hexdigest(),
                scope_id=scope, input_hash=wire_hash, status="transport_result", owner="local-fixture",
                lease_until=deadline, result_json={"schema": "transport-result.v1", "operation_key": root_key,
                    "attempt_id": attempt_id, "input_hash": wire_hash, "result": paid}),
        ])
        case.budget.calls_used += 1
        case.budget.tokens_charged += 30
        await session.commit()
        case.new_runs.append(run)
        return result.model_copy(update={"llm_run_id": run_id})
    monkeypatch.setattr(planner, "complete_text", local_return)


async def assert_physical_binding(case, request, run):
    catalog, _ = proof._fresh_catalog()
    retained = proof._retained_request(case.settings, run)
    role = proof._fresh_role(case.settings, case.project, retained, catalog)
    assert llm._effective_input_hash(retained, role) == run.prompt_hash
    assert retained.user_prompt == request.user_prompt != case.logical_user
    assert run.metadata_json["input_hash"] == request.metadata["input_hash"] == case.base_hash
    unencoded = request.model_copy(update={"user_prompt": case.logical_user})
    assert llm._effective_input_hash(unencoded, role) != run.prompt_hash
    paid, operation = await proof._physical_return(
        case.session, run, scope=case.budget.scope_id,
        expected_hash=run.prompt_hash, expected_model=role.model,
    )
    assert operation == run.metadata_json["execution_receipt"]["operation_key"]
    assert paid.tool_calls and paid.raw_message["tool_calls"] == paid.tool_calls
    decoded, _ = raw_document(request.user_prompt)
    assert canonical(decoded) == canonical(case.logical_document)
    return retained


async def test_cache_miss_encodes_full_source_binds_logical_and_wire_identity_then_approved_reuse_is_zero(cache_case, monkeypatch):
    case = cache_case
    original_before = deepcopy(case.original.metadata_json)
    install_local_paid_return(case, monkeypatch)
    payload, run_id = await generate(case)
    assert len(case.requests) == 1 and run_id == case.new_runs[0].id
    assert payload["_meta"]["input_hash"] == case.base_hash
    assert case.requests[0].metadata["planning_dispatch_transport"]["encoding"] != "identity"
    await assert_physical_binding(case, case.requests[0], case.new_runs[0])
    asset = await approve(case, payload)
    before = await snapshot(case)
    files = {str(path): path.read_bytes() for path in Path(case.settings.artifact_store.local_dir).rglob("*.json")}
    deny_provider(monkeypatch)
    replay, second_id = await generate(case)
    assert second_id is None and replay["_meta"]["reused_artifact_id"] == str(asset.id)
    assert replay["_meta"]["input_hash"] == case.base_hash
    assert await snapshot(case) == before and len(case.requests) == 1
    assert files == {str(path): path.read_bytes() for path in Path(case.settings.artifact_store.local_dir).rglob("*.json")}
    assert case.original.metadata_json == original_before and case.denied == []


@pytest.mark.parametrize("failure", ["parse", "length"])
async def test_retry_keeps_entire_first_wire_source_and_appends_diagnostics_without_compact_renderer(cache_case, monkeypatch, failure):
    from bestseller.services import llm_closed_loop
    case = cache_case
    install_local_paid_return(case, monkeypatch, invalid_first=failure)

    def forbidden(*args, **kwargs):
        pytest.fail("source-bound retry must not compact or excerpt the original source")
    monkeypatch.setattr(llm_closed_loop, "build_repair_user_prompt", forbidden)
    monkeypatch.setattr(planner, "_planner_length_retry_prompt", forbidden)
    # Avoid filesystem failure exports; raw rejected response remains retained
    # physically and in SQL by the local-return fixture above.
    monkeypatch.setattr(planner, "_persist_failing_planner_output", lambda **kwargs: None)
    payload, run_id = await generate(case)
    assert len(case.requests) == 2 and run_id == case.new_runs[1].id
    first, second = case.requests
    assert second.user_prompt.startswith(first.user_prompt + "\n\n[SOURCE-BOUND SUBMISSION CORRECTION]\n")
    assert second.user_prompt.count(first.user_prompt) == 1
    assert "diagnostics" in second.user_prompt[len(first.user_prompt):]
    for request, run in zip(case.requests, case.new_runs, strict=True):
        await assert_physical_binding(case, request, run)
    assert case.new_runs[0].prompt_hash != case.new_runs[1].prompt_hash
    assert payload["_meta"]["input_hash"] == case.base_hash
    assert case.denied == []
