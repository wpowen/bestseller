"""Read-only exact effective-input proof; retained flags are never rewritten.

SQLite paid rows are controlled physical-evidence fixtures, not live provider
proof. Request reconstruction never reparses a returned critique into a grade.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta
import hashlib
import json
from uuid import uuid4

import pytest
from sqlalchemy import inspect, select
from test_commercial_component_cohort import cohort  # noqa: F401
from test_retained_review_input_proof import retained, verify  # noqa: F401

from bestseller.infra.db.models import (
    ChapterDraftVersionModel,
    ChapterModel,
    ExecutionAttemptModel,
    ExecutionBudgetModel,
    ExecutionOperationModel,
    LlmRunModel,
    ReviewReportModel,
    WorkflowRunModel,
)
from bestseller.services import llm
from bestseller.services import retained_review_input_proof as proof
from bestseller.services.critic_evidence_gate import build_critic_body_anchors
from bestseller.services.primary_review_submission import (
    LEGACY_SUBMISSION_POLICY as SUBMISSION_POLICY,
    primary_review_submission_tools,
)

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


async def _configure(case, run, draft, path, *, physical_tools=False, representation="plain"):
    rig = case.rig
    payload = json.loads(case.paths["chapter_review"].read_text())
    source = json.dumps(case.source, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    body = draft.content_md
    catalog = list(build_critic_body_anchors(body))
    current = json.dumps(body, ensure_ascii=False) if representation == "json_string" else body
    task = (
        "CURRENT_DRAFT:\n"
        + current
        + "\nEND_CURRENT_DRAFT\nBODY_ANCHOR_CATALOG:\n"
        + json.dumps(catalog, ensure_ascii=False)
    )
    if representation == "json_object":
        task = json.dumps(
            {"CURRENT_DRAFT": body, "BODY_ANCHOR_CATALOG": catalog}, ensure_ascii=False
        )
    user = source + "\n" + task
    payload.update(
        llm_run_id=str(run.id),
        prompt_version="6.0-structured-body-submission",
        replayable=physical_tools,
        replayability_reasons=[] if physical_tools else ["tools_not_retained"],
    )
    payload["messages"].update(replayable=physical_tools, user=llm._bounded_text(user))
    tools = primary_review_submission_tools(body, submission_policy=SUBMISSION_POLICY)
    if physical_tools:
        payload["tools"], payload["tool_choice"] = tools, "required"
        payload["structured_payload_receipts"] = {}
        for key, value in (("tools", tools), ("tool_choice", "required")):
            encoded = json.dumps(
                value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
            )
            payload["structured_payload_receipts"][key] = {
                "encoding": "json-utf8",
                "limit_chars": 2_000_000,
                "truncated": False,
                "status": "retained",
                "original_chars": len(encoded),
                "sha256": hashlib.sha256(encoded.encode()).hexdigest(),
            }
    run.prompt_version = payload["prompt_version"]
    request = llm.LLMCompletionRequest(
        logical_role="critic",
        system_prompt=payload["messages"]["system"]["text"],
        user_prompt=user,
        fallback_response="never dispatched",
        project_id=rig.project.id,
        workflow_run_id=rig.owner.id,
        prompt_template="chapter_review",
        prompt_version=run.prompt_version,
        tools=tools,
        tool_choice="required",
        **payload["requested"],
    )
    catalog_entries, _ = proof._fresh_catalog()
    role = proof._fresh_role(rig.settings, rig.project, request, catalog_entries)
    effective_hash = llm._effective_input_hash(request, role)
    execution = deepcopy(run.metadata_json["execution_receipt"])
    metadata = deepcopy(run.metadata_json)
    metadata.update(
        primary_review_submission_policy=SUBMISSION_POLICY,
        primary_review_body_sha256=hashlib.sha256(body.encode()).hexdigest(),
        effective_input_receipt={"hash": effective_hash},
    )
    run.metadata_json = metadata
    run.prompt_hash, run.finish_reason = effective_hash, "tool_calls"
    run.request_payload_ref = path.name
    operation = await rig.session.get(ExecutionOperationModel, execution["operation_key"])
    attempt_id = execution["attempts"][0]["attempt_id"]
    attempt = await rig.session.get(ExecutionAttemptModel, attempt_id)
    transport = await rig.session.get(
        ExecutionOperationModel,
        hashlib.sha256(
            f"transport-result.v1\n{execution['scope_id']}\n{attempt_id}".encode()
        ).hexdigest(),
    )
    calls = [
        {
            "id": "physical-critique",
            "type": "function",
            "function": {
                "name": "submit_chapter_critique",
                "arguments": '{ "verdict": "rewrite", "retained": true }\n',
            },
        }
    ]
    paid = deepcopy(operation.result_json)
    paid.update(
        content="Auxiliary original content",
        tool_calls=calls,
        finish_reason="tool_calls",
        raw_message={
            "role": "assistant",
            "content": "Auxiliary original content",
            "tool_calls": deepcopy(calls),
        },
    )
    paid["metadata"] = {
        **paid["metadata"],
        "paid_call_origin": llm._paid_call_origin(request, effective_hash),
        "effective_input_receipt": {"hash": effective_hash},
    }
    operation.input_hash, operation.result_json = effective_hash, deepcopy(paid)
    attempt.input_hash = effective_hash
    transport.input_hash = effective_hash
    transport.result_json = {
        **transport.result_json,
        "input_hash": effective_hash,
        "result": deepcopy(paid),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False))
    await rig.session.commit()
    return request


async def _extra_primary(case, draft):
    rig = case.rig
    run_id, operation_key, aid = uuid4(), uuid4().hex, uuid4().hex
    execution = {
        "scope_id": case.primary.metadata_json["execution_receipt"]["scope_id"],
        "operation_key": operation_key,
        "attempts": [{"attempt_id": aid, "status": "completed"}],
    }
    run = LlmRunModel(
        id=run_id,
        project_id=rig.project.id,
        workflow_run_id=rig.owner.id,
        logical_role="critic",
        provider=case.primary.provider,
        model_name=case.primary.model_name,
        prompt_template="chapter_review",
        metadata_json={**case.primary.metadata_json, "execution_receipt": execution},
    )
    source = await rig.session.get(
        ExecutionOperationModel, case.primary.metadata_json["execution_receipt"]["operation_key"]
    )
    scope = execution["scope_id"]
    root = ExecutionOperationModel(
        key=operation_key,
        scope_id=scope,
        input_hash=source.input_hash,
        status="completed",
        owner="fixture",
        lease_until=datetime.now(UTC) + timedelta(hours=1),
        result_json=deepcopy(source.result_json),
    )
    transport = ExecutionOperationModel(
        key=hashlib.sha256(f"transport-result.v1\n{scope}\n{aid}".encode()).hexdigest(),
        scope_id=scope,
        input_hash=source.input_hash,
        status="transport_result",
        owner="fixture",
        lease_until=datetime.now(UTC) + timedelta(hours=1),
        result_json={
            "operation_key": operation_key,
            "attempt_id": aid,
            "input_hash": source.input_hash,
            "result": deepcopy(source.result_json),
        },
    )
    rig.session.add_all(
        [
            run,
            root,
            transport,
            ExecutionAttemptModel(
                id=aid,
                operation_key=operation_key,
                scope_id=scope,
                input_hash=source.input_hash,
                model_name=run.model_name,
                status="completed",
                reserved_tokens=7,
                actual_tokens=7,
                created_at=datetime.now(UTC),
            ),
        ]
    )
    await rig.session.commit()
    path = case.paths["chapter_review"].parent / f"{run.id}.request.json"
    await _configure(case, run, draft, path)
    return run, path


@pytest.fixture
async def primary6(retained):  # noqa: F811 - imported fixture
    case = retained
    case.rig.draft.version_no = 20
    await _configure(case, case.primary, case.rig.draft, case.paths["chapter_review"])
    return case


async def _snapshot(case):
    result = {}
    for model in (
        ChapterDraftVersionModel,
        ExecutionAttemptModel,
        ExecutionBudgetModel,
        ExecutionOperationModel,
        LlmRunModel,
        ChapterModel,
        ReviewReportModel,
        WorkflowRunModel,
    ):
        result[model.__tablename__] = [
            deepcopy({attr.key: getattr(row, attr.key) for attr in inspect(model).column_attrs})
            for row in await case.rig.session.scalars(
                select(model).execution_options(populate_existing=True)
            )
        ]
    return result


@pytest.mark.parametrize("representation", ["plain", "json_string", "json_object"])
async def test_old_tools_only_gap_is_proven_without_rewriting_retained_flags(
    primary6, representation
):
    case = primary6
    path = case.paths["chapter_review"]
    await _configure(case, case.primary, case.rig.draft, path, representation=representation)
    original = path.read_bytes()
    before = await _snapshot(case)
    first, second = await verify(case), await verify(case)
    assert first == second
    assert await _snapshot(case) == before and path.read_bytes() == original
    assert first["provider_calls"] == first["writes"] == 0
    row = next(item for item in first["paid_rows"] if item["llm_run_id"] == str(case.primary.id))
    receipt = row["request_reconstruction"]
    assert receipt["physical_request_file_sha256"] == hashlib.sha256(original).hexdigest()
    assert receipt["original_request_replayable"] is False
    assert receipt["physical_tool_schema_retained"] is False
    assert receipt["tool_schema_origin"] == "code-derived-required-tools"
    assert receipt["effective_input_sha256"] == case.primary.prompt_hash
    assert receipt["tool_schema_sha256"] == proof._hash(
        primary_review_submission_tools(case.rig.draft.content_md, submission_policy=SUBMISSION_POLICY)
    )
    assert not case.rig.session.dirty and not case.rig.session.new


async def test_each_primary_run_uses_its_own_unique_same_chapter_body(primary6):
    case = primary6
    old = ChapterDraftVersionModel(
        project_id=case.rig.project.id,
        chapter_id=case.rig.chapter.id,
        version_no=19,
        content_md="The navigator checked a different sealed chart.\n\nIt remained unopened.",
        word_count=15,
        is_current=False,
        promotion_state="candidate",
    )
    case.rig.session.add(old)
    await case.rig.session.commit()
    run, path = await _extra_primary(case, old)
    before = await _snapshot(case)
    result = await verify(case)
    rows = {row["llm_run_id"]: row for row in result["paid_rows"]}
    assert rows[str(run.id)]["request_reconstruction"]["source_draft_id"] == str(old.id)
    assert rows[str(case.primary.id)]["request_reconstruction"]["source_draft_id"] == str(
        case.rig.draft.id
    )
    assert rows[str(run.id)]["effective_input_sha256"] == run.prompt_hash
    assert await _snapshot(case) == before
    assert json.loads(path.read_text())["replayable"] is False


@pytest.mark.parametrize("location", ["top", "requested"])
async def test_future_physical_tools_are_read_exactly_not_regenerated(primary6, location):
    case = primary6
    path = case.paths["chapter_review"]
    request = await _configure(case, case.primary, case.rig.draft, path, physical_tools=True)
    payload = json.loads(path.read_text())
    if location == "requested":
        for key in ("tools", "tool_choice"):
            payload["requested"][key] = payload.pop(key)
        path.write_text(json.dumps(payload))
    before = path.read_bytes()
    recovered = proof._retained_request(
        case.rig.settings, case.primary, chapter_body=case.rig.draft.content_md
    )
    assert recovered.tools == request.tools and recovered.tool_choice == "required"
    result = await verify(case)
    row = next(row for row in result["paid_rows"] if row["llm_run_id"] == str(case.primary.id))
    assert "request_reconstruction" not in row and path.read_bytes() == before


async def test_actual_future_retention_writer_feeds_exact_physical_tools_reader(primary6):
    case = primary6
    rig = case.rig
    request = await _configure(case, case.primary, rig.draft, case.paths["chapter_review"])
    entries, _ = proof._fresh_catalog()
    role = proof._fresh_role(rig.settings, rig.project, request, entries)
    rig.settings.artifact_store.retain_prompt_payloads = True
    with llm.bind_project_prompt_audit(rig.project.id, reason="isolated protocol retention test"):
        request_ref, _, _, replayable = llm._persist_prompt_payloads(
            settings=rig.settings,
            request=request,
            llm_run_id=case.primary.id,
            configured_model=role.model,
            effective_model=role.model,
            provider=case.primary.provider,
            effective_temperature=role.temperature,
            effective_max_tokens=request.max_tokens_override,
            effective_timeout_seconds=role.timeout_seconds,
            effective_stream=False,
            effective_thinking_type=llm._effective_thinking_type(role),
            effective_reasoning_effort=role.reasoning_effort,
            content="original response",
            tool_call_count=0,
            finish_reason="stop",
            fallback_used=False,
            fallback_source=None,
            input_tokens=3,
            output_tokens=4,
        )
    assert replayable is True and request_ref
    case.primary.request_payload_ref = request_ref
    await rig.session.commit()
    result = await verify(case)
    row = next(row for row in result["paid_rows"] if row["llm_run_id"] == str(case.primary.id))
    assert "request_reconstruction" not in row
    path = case.paths["chapter_review"].parent / request_ref
    payload = json.loads(path.read_text())
    assert payload["requested"]["tools"] == request.tools
    assert payload["requested"]["tool_choice"] == "required"


@pytest.mark.parametrize(
    "fault", ["missing_receipt", "receipt_hash", "receipt_truncated", "alias_conflict"]
)
async def test_future_physical_tool_receipt_corruption_is_rejected(primary6, fault):
    case = primary6
    path = case.paths["chapter_review"]
    await _configure(case, case.primary, case.rig.draft, path, physical_tools=True)
    payload = json.loads(path.read_text())
    if fault == "missing_receipt":
        payload.pop("structured_payload_receipts")
    elif fault == "receipt_hash":
        payload["structured_payload_receipts"]["tools"]["sha256"] = "0" * 64
    elif fault == "receipt_truncated":
        payload["structured_payload_receipts"]["tools"]["truncated"] = True
    else:
        payload["requested"]["tools"] = []
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError):
        await verify(case)


@pytest.mark.parametrize(
    "fault",
    [
        "extra_reason",
        "missing_reason",
        "other_protocol",
        "missing_policy",
        "wrong_body_hash",
        "truncated",
        "changed_prompt",
        "history_body_only",
        "wrong_catalog",
        "different_body_argument",
        "duplicate_body",
        "foreign_chapter_body",
        "missing_body",
        "hash_mismatch",
        "transport_tools",
        "transport_raw_message",
        "root_hash",
        "attempt_hash",
        "paid_origin",
        "mixed_retained_tools",
    ],
)
async def test_any_missing_binding_or_physical_proof_stays_rejected(primary6, fault):
    case = primary6
    rig = case.rig
    path = case.paths["chapter_review"]
    payload = json.loads(path.read_text())
    metadata = deepcopy(case.primary.metadata_json)
    if fault == "extra_reason":
        payload["replayability_reasons"].append("messages_override_not_retained")
    elif fault == "missing_reason":
        payload.pop("replayability_reasons")
    elif fault == "other_protocol":
        case.primary.prompt_version = payload["prompt_version"] = "5.0-typed-source-time"
    elif fault == "missing_policy":
        metadata.pop("primary_review_submission_policy")
    elif fault == "wrong_body_hash":
        metadata["primary_review_body_sha256"] = "0" * 64
    elif fault == "truncated":
        payload["messages"]["user"]["truncated"] = True
    elif fault == "changed_prompt":
        payload["messages"]["system"] = llm._bounded_text("changed retained system")
    elif fault == "history_body_only":
        payload["messages"]["user"] = llm._bounded_text("PRIOR_PROSE:\n" + rig.draft.content_md)
    elif fault == "wrong_catalog":
        text = payload["messages"]["user"]["text"]
        payload["messages"]["user"] = llm._bounded_text(
            text.split("BODY_ANCHOR_CATALOG:")[0] + "BODY_ANCHOR_CATALOG:\n[]"
        )
    elif fault == "different_body_argument":
        with pytest.raises(ValueError, match="primary6_body_binding"):
            proof._retained_request(rig.settings, case.primary, chapter_body="Another story.")
        return
    elif fault == "duplicate_body":
        rig.session.add(
            ChapterDraftVersionModel(
                project_id=rig.project.id,
                chapter_id=rig.chapter.id,
                version_no=21,
                content_md=rig.draft.content_md,
                word_count=1,
                is_current=False,
                promotion_state="candidate",
            )
        )
    elif fault == "foreign_chapter_body":
        rig.draft.chapter_id = uuid4()
    elif fault == "missing_body":
        rig.draft.content_md += " changed"
    elif fault == "hash_mismatch":
        case.primary.prompt_hash = "0" * 64
    elif fault == "mixed_retained_tools":
        payload["tools"] = primary_review_submission_tools(rig.draft.content_md, submission_policy=SUBMISSION_POLICY)
    else:
        operation = await rig.session.get(
            ExecutionOperationModel, metadata["execution_receipt"]["operation_key"]
        )
        aid = metadata["execution_receipt"]["attempts"][0]["attempt_id"]
        scope = metadata["execution_receipt"]["scope_id"]
        if fault == "root_hash":
            operation.input_hash = "0" * 64
        elif fault == "attempt_hash":
            (await rig.session.get(ExecutionAttemptModel, aid)).input_hash = "0" * 64
        else:
            transport = await rig.session.get(
                ExecutionOperationModel,
                hashlib.sha256(f"transport-result.v1\n{scope}\n{aid}".encode()).hexdigest(),
            )
            value = deepcopy(transport.result_json)
            if fault == "transport_tools":
                value["result"]["tool_calls"][0]["function"]["arguments"] = "{}"
            elif fault == "transport_raw_message":
                value["result"]["raw_message"]["tool_calls"] = []
            else:
                value["result"]["metadata"]["paid_call_origin"]["prompt_hash"] = "0" * 64
            transport.result_json = value
    case.primary.metadata_json = metadata
    path.write_text(json.dumps(payload))
    await rig.session.commit()
    before = await _snapshot(case)
    calls = len(rig.calls)
    with pytest.raises(ValueError):
        await verify(case)
    assert await _snapshot(case) == before and len(rig.calls) == calls
    assert not rig.session.new and not rig.session.dirty
