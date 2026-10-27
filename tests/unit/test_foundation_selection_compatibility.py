"""Real SQLite/file custody for approval-only compatibility; no provider or PG."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import JSON, ForeignKeyConstraint, MetaData
from sqlalchemy.dialects.postgresql import JSONB
from test_foundation_selection import seed_objects
from test_foundation_selection import store as store

from bestseller.infra.db.models import (
    ExecutionAttemptModel,
    ExecutionBudgetModel,
    ExecutionOperationModel,
    LlmRunModel,
    PlanningArtifactVersionModel,
    ProjectModel,
)
from bestseller.services import foundation_selection as selection
from bestseller.services import llm, planner
from bestseller.services import retained_review_input_proof as proof
from bestseller.services import source_bound_planning_content as content
from bestseller.services.prompt_source_policy import content_hash
from bestseller.services.source_bound_planning_submission import (
    VOLUME_NATIVE_POLICY,
    planning_submission_tool,
)
from bestseller.settings import load_settings

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.fixture
async def compat(store, tmp_path, monkeypatch):
    factory, engine = store
    for model in (
        ExecutionBudgetModel,
        ExecutionOperationModel,
        ExecutionAttemptModel,
        LlmRunModel,
    ):
        table = model.__table__.to_metadata(MetaData())
        for column in table.c:
            if isinstance(column.type, JSONB):
                column.type = JSON()
            if column.server_default is not None and "::" in str(column.server_default.arg):
                column.server_default = None
        for constraint in list(table.constraints):
            if isinstance(constraint, ForeignKeyConstraint):
                table.constraints.remove(constraint)
        async with engine.begin() as connection:
            await connection.run_sync(table.create)
    settings = load_settings(env={}).model_copy(deep=True)
    settings.llm.mock = False
    settings.llm.planner.model = "openai/Configured-offline-route"
    settings.llm.planner.model_override = None
    settings.artifact_store.mode = "local"
    settings.artifact_store.local_dir = tmp_path / "physical"
    settings.artifact_store.retain_prompt_payloads = True
    monkeypatch.setattr(proof, "apply_runtime_llm_profile", lambda value: value)
    p, canonical, w, failed, assets = seed_objects()
    scope = "project:" + str(p.id)
    p.metadata_json = {**p.metadata_json, "execution_scope_id": scope}
    detached = SimpleNamespace(
        id=p.id,
        target_chapters=p.target_chapters,
        target_word_count=p.target_word_count,
        metadata_json=deepcopy(p.metadata_json),
        language=p.language,
    )
    detached._source_bound_planning_canonical = {
        "id": str(canonical.id),
        "content_hash": content_hash(canonical.content),
        "content": deepcopy(canonical.content),
    }
    old_assets = {kind: deepcopy(assets[kind].content) for kind in selection.KINDS[:3]}
    old_assets["cast_spec"]["_meta"].update(
        source_bound_expansion="derived_pending_review",
        source_bound_expansion_review="independent_semantic_review_required",
    )
    assets["cast_spec"].content = deepcopy(old_assets["cast_spec"])
    report_id = str(uuid4())
    response_hash = "a" * 64
    candidate_hash = planner._approved_payload_hash(
        {
            kind: planner._without_planning_meta(assets[kind].content)
            for kind in ("world_spec", "cast_spec")
        }
    )
    approval = {
        "decision": "pass",
        "reason": "reviewed",
        "core_preserved": True,
        "core_conflicts": [],
        "draft_eligible": True,
        "evidence_verified": True,
        "candidate_hash": candidate_hash,
        "candidate_hash_echo": candidate_hash,
        "authority_snapshot_hash": content_hash(canonical.content),
        "authority_snapshot_hash_echo": content_hash(canonical.content),
        "changed_fields": ["cast.protagonist"],
        "source_evidence": [{"quote": "fixture source"}],
        "candidate_evidence": [{"quote": "fixture candidate"}],
        "llm_run_id": report_id,
        "provider": "openai",
        "finish_reason": "stop",
        "raw_response_hash": response_hash,
        "review_attempts": [
            {
                "llm_run_id": report_id,
                "provider": "openai",
                "finish_reason": "stop",
                "raw_response_hash": response_hash,
                "fallback_used": False,
            }
        ],
    }
    assets["cast_spec"].content = planner._mark_source_bound_expansion_reviewed(
        assets["cast_spec"].content, approval
    )
    original = planner._compile_source_bound_volume_plan(
        detached, p.metadata_json["premise"], *(old_assets[k] for k in selection.KINDS[:3])
    )
    dependencies = {k: content._dependency_projection(v) for k, v in old_assets.items()}
    logical = {
        "protocol": content.SOURCE_BOUND_PLANNING_PROTOCOL,
        "kind": "volume_plan",
        "approved_premise": p.metadata_json["premise"],
        "approved_sources": {"canonical_snapshot": detached._source_bound_planning_canonical},
        "projection": original,
        "existing_planning_assets": dependencies,
    }
    rid = uuid4()
    attempt = uuid4().hex
    body = [{"volume_number": 1, "volume_title": "An observatory reopened"}]
    assets["volume_plan"].content = [
        {
            **body[0],
            "_meta": {
                "generation_llm_run_id": str(rid),
                "source_projection_sha256": content_hash(original),
            },
        }
    ]
    tool = planning_submission_tool("volume_plan", version=VOLUME_NATIVE_POLICY)
    request = llm.LLMCompletionRequest(
        logical_role="planner",
        project_id=p.id,
        workflow_run_id=w.id,
        prompt_template="planner_volume_plan",
        prompt_version="v4-volume-native",
        system_prompt="Use the complete retained source as data.",
        user_prompt=json.dumps(logical, ensure_ascii=False),
        fallback_response="no dispatch",
        max_tokens_override=8192,
        tools=[tool],
        tool_choice={"type": "function", "function": {"name": "submit_volume_plan"}},
    )
    role = settings.llm.planner
    digest = llm._effective_input_hash(request, role)
    key = hashlib.sha256((scope + "fixture-operation").encode()).hexdigest()
    transport_key = hashlib.sha256(f"transport-result.v1\n{scope}\n{attempt}".encode()).hexdigest()
    origin = {
        "project_id": str(p.id),
        "workflow_run_id": str(w.id),
        "logical_role": "planner",
        "prompt_template": request.prompt_template,
        "prompt_version": request.prompt_version,
        "prompt_hash": digest,
    }
    metadata = {"paid_call_origin": origin, "effective_input_receipt": {"hash": digest}}
    paid = llm.LLMCompletionResult(
        content="",
        provider="openai",
        model_name=role.model,
        finish_reason="tool_calls",
        input_tokens=123,
        output_tokens=45,
        llm_run_id=rid,
        metadata=metadata,
        tool_calls=[
            {
                "id": "retained-volume",
                "type": "function",
                "function": {
                    "name": "submit_volume_plan",
                    "arguments": json.dumps({"volumes": body}),
                },
            }
        ],
        raw_message={"role": "assistant", "content": ""},
    )
    paid.raw_message["tool_calls"] = deepcopy(paid.tool_calls)
    with llm.bind_project_prompt_audit(p.id, reason="offline-foundation-custody-fixture"):
        request_ref, response_ref, reason, replayable = llm._persist_prompt_payloads(
            settings=settings,
            request=request,
            llm_run_id=rid,
            configured_model=role.model,
            effective_model=role.model,
            provider="openai",
            effective_temperature=role.temperature,
            effective_max_tokens=8192,
            effective_timeout_seconds=role.timeout_seconds,
            effective_stream=False,
            effective_thinking_type=llm._effective_thinking_type(role),
            effective_reasoning_effort=role.reasoning_effort,
            content="",
            tool_call_count=1,
            finish_reason="tool_calls",
            fallback_used=False,
            fallback_source=None,
            input_tokens=123,
            output_tokens=45,
            tool_calls=paid.tool_calls,
            raw_message=paid.raw_message,
        )
    assert replayable, reason
    until = datetime.now(UTC) + timedelta(days=1)
    raw = paid.model_dump(mode="json")
    root = deepcopy(raw)
    root["metadata"]["runtime_provenance"] = "root-only telemetry"
    root["latency_ms"] = 888
    rows = [
        ExecutionBudgetModel(
            scope_id=scope,
            max_calls=1000,
            max_tokens=900000,
            calls_used=147,
            tokens_charged=100000,
            deadline_at=until,
            status="active",
            metadata_json={"old_calls": "preserved"},
        ),
        ExecutionOperationModel(
            key=key,
            scope_id=scope,
            input_hash=digest,
            status="completed",
            owner="fixture",
            lease_until=until,
            result_json=root,
        ),
        ExecutionOperationModel(
            key=transport_key,
            scope_id=scope,
            input_hash=digest,
            status="transport_result",
            owner="fixture",
            lease_until=until,
            result_json={
                "operation_key": key,
                "attempt_id": attempt,
                "input_hash": digest,
                "result": raw,
            },
        ),
        ExecutionAttemptModel(
            id=attempt,
            operation_key=key,
            scope_id=scope,
            model_name=role.model,
            input_hash=digest,
            reserved_tokens=8192,
            actual_tokens=168,
            status="completed",
            created_at=datetime.now(UTC),
        ),
        LlmRunModel(
            id=rid,
            project_id=p.id,
            workflow_run_id=w.id,
            logical_role="planner",
            provider="openai",
            model_name=role.model,
            prompt_template=request.prompt_template,
            prompt_version=request.prompt_version,
            prompt_hash=digest,
            finish_reason="tool_calls",
            input_tokens=123,
            output_tokens=45,
            request_payload_ref=request_ref,
            response_payload_ref=response_ref,
            metadata_json={
                **metadata,
                "execution_receipt": {
                    "scope_id": scope,
                    "operation_key": key,
                    "attempts": [{"attempt_id": attempt, "status": "completed"}],
                },
            },
        ),
    ]
    async with factory() as session:
        session.add_all([p, canonical, w, failed, *assets.values(), *rows])
        await session.commit()
    return SimpleNamespace(
        factory=factory,
        settings=settings,
        pid=p.id,
        wid=w.id,
        assets=assets,
        canonical=canonical,
        scope=scope,
        run_id=rid,
        key=key,
        transport_key=transport_key,
        attempt=attempt,
        request_ref=request_ref,
        response_ref=response_ref,
        logical=logical,
        original=original,
        approval=approval,
    )


async def test_full_paid_compatibility_pins_and_reloads_no_credit_or_financial_changes(compat):
    c = compat
    async with c.factory() as s:
        p = await s.get(ProjectModel, c.pid)
        budget = await s.get(ExecutionBudgetModel, c.scope)
        before = (budget.calls_used, budget.tokens_charged, deepcopy(budget.metadata_json))
        receipt = await selection.pin_completed_foundation(
            s, project=p, workflow_id=c.wid, settings=c.settings
        )
        proof_receipt = receipt["derived_compatibility"]
        assert proof_receipt["original_projection_sha256"] == content_hash(c.original)
        assert proof_receipt["current_projection_sha256"] != content_hash(c.original)
        assert proof_receipt["changed_projection_paths"] == ["/0/_meta/cast_spec_hash"]
        assert (
            proof_receipt["paid_proof"]["root_row_sha256"]
            != proof_receipt["paid_proof"]["transport_row_sha256"]
        )
        assert (
            not receipt["author_authority"]
            and not receipt["credit_granted"]
            and not receipt["grade_asserted"]
        )
        assert set(s.dirty) == {p} and not s.new and not s.deleted
        assert before == (budget.calls_used, budget.tokens_charged, budget.metadata_json)
        await s.commit()
    async with c.factory() as s:
        p = await s.get(ProjectModel, c.pid)
        selected = await selection.load_selected_foundation_artifacts(
            s, project=p, settings=c.settings
        )
        assert {k: v.id for k, v in selected.items()} == {k: v.id for k, v in c.assets.items()}
        assert (
            await selection.pin_completed_foundation(
                s, project=p, workflow_id=c.wid, settings=c.settings
            )
            == receipt
        )
        assert len(p.metadata_json[selection.HISTORY]) == 1
        assert not s.dirty


@pytest.mark.parametrize(
    "fault",
    [
        "missing_settings",
        "body",
        "unknown_meta",
        "nested_meta",
        "approval_false",
        "approval_hash",
        "old_projection",
        "canonical",
        "request_duplicate",
        "request_nonfinite",
        "request_truncated",
        "request_wrong_identity",
        "response_tool",
        "response_raw",
        "response_content",
        "root",
        "transport",
        "extra_attempt",
        "attempt_model",
        "origin",
        "scope",
        "route",
        "missing_request",
        "missing_response",
    ],
)
async def test_forged_or_changed_compatibility_fails_closed(compat, fault):
    c = compat
    async with c.factory() as s:
        p = await s.get(ProjectModel, c.pid)
        settings = c.settings
        cast = await s.get(PlanningArtifactVersionModel, c.assets["cast_spec"].id)
        if fault == "missing_settings":
            settings = None
        elif fault in ("body", "unknown_meta", "nested_meta", "approval_false", "approval_hash"):
            value = deepcopy(cast.content)
            if fault == "body":
                value["label"] = "different story"
            elif fault == "unknown_meta":
                value["_meta"]["unknown"] = "new value"
            elif fault == "nested_meta":
                value["nested"] = {"_meta": {"source_bound_expansion": "reviewed"}}
            elif fault == "approval_false":
                value["_meta"]["source_bound_expansion_draft_eligible"] = False
            else:
                value["_meta"]["source_bound_expansion_review_receipt"]["candidate_hash"] = "0" * 64
            cast.content = value
        elif fault == "old_projection":
            v = await s.get(PlanningArtifactVersionModel, c.assets["volume_plan"].id)
            value = deepcopy(v.content)
            value[0]["_meta"]["source_projection_sha256"] = "0" * 64
            v.content = value
        elif fault == "canonical":
            row = await s.get(PlanningArtifactVersionModel, c.canonical.id)
            row.content = {**row.content, "changed": True}
        elif fault.startswith("request_") or fault == "missing_request":
            path = Path(c.settings.artifact_store.local_dir) / c.request_ref
            if fault == "missing_request":
                path.unlink()
            elif fault == "request_duplicate":
                path.write_text('{"kind":"request","kind":"request"}')
            elif fault == "request_nonfinite":
                path.write_text('{"kind":NaN}')
            else:
                value = json.loads(path.read_text())
                if fault == "request_truncated":
                    value["messages"]["user"]["truncated"] = True
                else:
                    value["project_id"] = str(uuid4())
                path.write_text(json.dumps(value))
        elif fault.startswith("response_") or fault == "missing_response":
            path = Path(c.settings.artifact_store.local_dir) / c.response_ref
            if fault == "missing_response":
                path.unlink()
            else:
                value = json.loads(path.read_text())
                if fault == "response_tool":
                    value["tool_calls"][0]["function"]["arguments"] = "{}"
                elif fault == "response_raw":
                    value["raw_message"] = {"role": "assistant", "content": "changed"}
                else:
                    value["content"]["text"] = "changed"
                path.write_text(json.dumps(value))
        elif fault in ("root", "transport", "origin"):
            row = await s.get(
                ExecutionOperationModel, c.transport_key if fault == "transport" else c.key
            )
            value = deepcopy(row.result_json)
            if fault == "origin":
                value["metadata"]["paid_call_origin"]["workflow_run_id"] = str(uuid4())
            elif fault == "transport":
                value["result"]["tool_calls"] = []
            else:
                value["content"] = "changed"
            row.result_json = value
        elif fault == "extra_attempt":
            s.add(
                ExecutionAttemptModel(
                    id=uuid4().hex,
                    operation_key=c.key,
                    scope_id=c.scope,
                    model_name=c.settings.llm.planner.model,
                    input_hash="0" * 64,
                    reserved_tokens=1,
                    actual_tokens=1,
                    status="completed",
                    created_at=datetime.now(UTC),
                )
            )
            await s.flush()
        elif fault == "attempt_model":
            row = await s.get(ExecutionAttemptModel, c.attempt)
            row.model_name = "changed"
        elif fault == "scope":
            p.metadata_json = {**p.metadata_json, "execution_scope_id": "different-scope"}
        elif fault == "route":
            settings = settings.model_copy(deep=True)
            settings.llm.planner.temperature += 0.12
        before = deepcopy(p.metadata_json)
        with pytest.raises((ValueError, FileNotFoundError)):
            await selection.pin_completed_foundation(
                s, project=p, workflow_id=c.wid, settings=settings
            )
        assert p.metadata_json == before and selection.POINTER not in p.metadata_json


async def test_real_retained_document_dependency_comparison_is_exact():
    """Actual archived source, not a claim of actual ledger validation."""
    path = (
        Path(__file__).resolve().parents[2]
        / "reports/continuous-recovery-2026-10-16"
        / "20261017T222220843645Z-foundation-projection-binding-diagnostic.json"
    )
    d = json.loads(path.read_text())
    logical = d["original_logical_document"]
    old = selection._expand_projection(logical["projection"], logical)
    assert content_hash(old) == d["recorded_projection_sha256"] == d["physical_projection_sha256"]
    assert d["projection_diff_paths"] == ["/0/_meta/cast_spec_hash"]
    for kind in selection.KINDS[:3]:
        left = deepcopy(logical["existing_planning_assets"][kind])
        right = content._dependency_projection(d["selected_asset_contents"][kind])
        if kind == "cast_spec":
            for key in selection.CAST_APPROVAL_FIELDS:
                left["_meta"].pop(key, None)
                right["_meta"].pop(key, None)
        assert left == right
    selection._cast_approval(
        d["selected_asset_contents"]["cast_spec"], d["selected_asset_contents"]["world_spec"]
    )


@pytest.mark.parametrize("envelope", ["root", "transport"])
async def test_late_full_row_telemetry_mutation_invalidates_persisted_proof(compat, envelope):
    c = compat
    async with c.factory() as s:
        p = await s.get(ProjectModel, c.pid)
        await selection.pin_completed_foundation(
            s, project=p, workflow_id=c.wid, settings=c.settings
        )
        await s.commit()
    async with c.factory() as s:
        row = await s.get(ExecutionOperationModel, c.key if envelope == "root" else c.transport_key)
        value = deepcopy(row.result_json)
        value["late_telemetry"] = "changed after receipt"
        row.result_json = value
        await s.commit()
    async with c.factory() as s:
        p = await s.get(ProjectModel, c.pid)
        with pytest.raises(ValueError, match="foundation_selection_pointer_changed"):
            await selection.load_selected_foundation_artifacts(s, project=p, settings=c.settings)


async def test_strict_decoding_rejects_overflow_and_projection_reference_cycles():
    with pytest.raises(ValueError, match="nonfinite"):
        selection._strict_decoder().decode('{"value":1e999}')
    for document in (
        {"projection": {"$ref": "#/projection"}},
        {"projection": {"$ref": "#/missing"}},
        {"projection": {"$ref": "#/bad~2name"}},
        {"projection": {"$ref": "#/values/01"}, "values": [1, 2]},
    ):
        with pytest.raises(ValueError, match="foundation_selection_projection_"):
            selection._expand_projection(document["projection"], document)
