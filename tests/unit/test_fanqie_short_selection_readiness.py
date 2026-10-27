"""Actual retained text + synthetic native rows/tools; no production/provider IO."""

from contextlib import contextmanager
from copy import deepcopy
from datetime import UTC, datetime
import json
from pathlib import Path
import socket
from types import SimpleNamespace
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import pytest
from sqlalchemy import event, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.schema import CreateTable

import test_fanqie_short_semantic_adapter as db_types  # SQLite JSONB/UUID compilers.
import test_fanqie_short_semantic_evidence_selection as body_fixture
import test_fanqie_short_title_semantic_judge as title_fixture
from bestseller.services import fanqie_short_semantic_adapter as native
from bestseller.services import fanqie_short_semantic_consumer as legacy
from bestseller.services import fanqie_short_semantic_evidence_selection as selection
from bestseller.services import fanqie_short_semantic_selection_adapter as adapter
from bestseller.services import fanqie_short_selection_readiness as formal
from bestseller.services import fanqie_short_semantic_readiness as common
from bestseller.services import fanqie_short_title_semantic_judge as judge


BASE = Path(__file__).resolve().parents[2] / "reports/project-reliability-review-2026-10-24/implementation"


@pytest.fixture(autouse=True)
def no_provider_network(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("No provider/network authorized")
    for owner, name in ((socket.socket, "connect"), (socket.socket, "connect_ex"), (socket, "create_connection")):
        monkeypatch.setattr(owner, name, forbidden)
    from bestseller.services import llm
    monkeypatch.setattr(llm, "complete_text", forbidden)


def original_native(saved):
    p, c = saved["request_payload"], deepcopy(saved["provider_receipt"])
    docs = deepcopy(saved["retained_payloads"])
    c.update(content=saved["raw_response"], tool_calls=None,
             input_tokens=docs["response"]["input_tokens"], output_tokens=docs["response"]["output_tokens"])
    return native_bundle(p, c, docs, role="critic", template="fanqie_short_semantic_review",
                         version=docs["request"]["prompt_version"])


def native_bundle(p, c, docs, *, role, template, version):
    meta = c["metadata"]
    execution = meta["execution_receipt"]
    sent = execution["attempts"][0]
    run = {k: c[k] for k in ("provider", "model_name", "finish_reason", "input_tokens", "output_tokens")}
    run.update(id=c["llm_run_id"], project_id=p["project_id"], workflow_run_id=p["workflow_run_id"],
        logical_role=role, prompt_template=template, prompt_version=version,
        prompt_hash=meta["effective_input_receipt"]["hash"], metadata_json=deepcopy(meta))
    scope, key, h = p["execution_scope_id"], execution["operation_key"], run["prompt_hash"]
    root = dict(key=key, scope_id=scope, input_hash=h, status="completed", result_json=deepcopy(c))
    attempt = dict(id=sent["attempt_id"], operation_key=key, scope_id=scope, input_hash=h,
                   model_name=c["model_name"], status="completed", actual_tokens=c["input_tokens"] + c["output_tokens"],
                   reserved_tokens=sent["reserved_tokens"])
    transport = dict(key=legacy.sha(("transport-result.v1\n" + scope + "\n" + attempt["id"]).encode()),
        scope_id=scope, input_hash=h, status="transport_result", result_json=dict(schema="transport-result.v1",
            operation_key=key, attempt_id=attempt["id"], input_hash=h, result=deepcopy(c)))
    files = {}
    for kind in ("request", "response"):
        ref = meta["prompt_payload_receipt"][kind + "_ref"]
        run[kind + "_payload_ref"] = ref
        files[ref] = json.dumps(docs[kind], ensure_ascii=False).encode()
    return dict(request_payload=deepcopy(p), completion=c, retained_payloads=docs, physical_files=files,
                native=dict(llm_run=run, operation=root, attempts=[attempt], transport_results=[transport]))


def tool_bundle(plan, args, module, wf, inputs):
    fields = module.native_request_fields(plan, workflow_run_id=wf)
    run_id, aid = str(uuid4()), str(uuid4())
    scope, h = inputs["execution_scope_id"], legacy.digest({"synthetic_request": fields["user_prompt"], "wf": wf})
    key = legacy.sha((scope + "\n" + fields["operation_id"] + ":sample:0").encode())
    calls = [dict(id="synthetic-original-call", type="function", function=dict(name=module.TOOL_NAME, arguments=selection.encoded(args)))]
    meta = {**deepcopy(fields["metadata"]), "llm_run_flushed": True,
        "effective_input_receipt": dict(schema="effective-input.v1", hash=h, model="critic-model", tools_included=True,
            messages_source="system_user", legacy_prompt_hash=legacy.sha((fields["system_prompt"] + "\n\n" + fields["user_prompt"]).encode())),
        "paid_call_origin": dict(schema="paid-call-origin.v1", status="recorded", project_id=inputs["project_id"],
            workflow_run_id=wf, logical_role="critic", prompt_template=fields["prompt_template"], prompt_version=fields["prompt_version"], prompt_hash=h),
        "execution_receipt": dict(scope_id=scope, operation_key=key, policy="execution-budget.v1", attempts=[dict(
            attempt_id=aid, status="completed", effective_input_hash=h, model="critic-model", reserved_tokens=12000, wire_body_hash="a" * 64)]),
        "prompt_payload_receipt": dict(enabled=True, status="stored", replayable=True,
            **{k + "_ref": f"runtime/llm_payloads/{run_id}.{k}.json" for k in ("request", "response")})}
    c = dict(llm_run_id=run_id, provider="openai", model_name="critic-model", content="", tool_calls=calls,
             finish_reason="tool_calls", input_tokens=30, output_tokens=25, fallback_used=False, fallback_source=None, metadata=meta)
    def block(text): return dict(text=text, truncated=False, original_chars=len(text))
    base = dict(schema_version="llm-payload.v1", replayable=True, replayability_reasons=[], llm_run_id=run_id,
                project_id=inputs["project_id"], workflow_run_id=wf)
    docs = dict(request={**base, "kind": "request", "logical_role": "critic", "prompt_template": fields["prompt_template"],
        "prompt_version": fields["prompt_version"], "requested": {k: fields[k] for k in ("tools", "tool_choice", "max_tokens_override", "model_tier", "temperature_override")},
        "messages": dict(system=block(fields["system_prompt"]), user=block(fields["user_prompt"])),
        "effective": dict(provider="openai", model="critic-model")},
        response={**base, "kind": "response", "content": block(""), "tool_calls": deepcopy(calls), "tool_call_count": 1,
                  "logical_role": "critic", "input_tokens": 30, "output_tokens": 25,
                  "provider": "openai", "model": "critic-model", "finish_reason": "tool_calls", "fallback_used": False})
    p = dict(project_id=inputs["project_id"], workflow_run_id=wf, execution_scope_id=scope)
    bundle = native_bundle(p, c, docs, role="critic", template=fields["prompt_template"], version=fields["prompt_version"])
    bundle["request_payload"] = deepcopy(plan)
    return bundle


def wf_record(identity, p, artifact_id, error, *, implementation=None, recovery=None):
    meta = dict(artifact_id=artifact_id, validation_error=error, source_manifest=deepcopy(p["manifest"]),
                semantic_binding_sha256=p["binding_sha256"], execution_scope_id=p["execution_scope_id"])
    if implementation is not None: meta["implementation"] = implementation
    if recovery is not None: meta["protocol_recovery"] = recovery
    return dict(id=identity, project_id=p["project_id"], workflow_type="original_four_whole_semantic_review",
                scope_type="project", scope_id=p["project_id"], status="machine_blocked", metadata_json=meta)


@pytest.fixture(scope="module")
def complete_context():
    r4 = json.loads((BASE / "original-four-whole-v4-execution.json").read_text())
    r5 = json.loads((BASE / "original-four-whole-v5-protocol-execution.json").read_text())
    a4 = dict(id=r4["artifact_id"], project_id=r4["result"]["request_payload"]["project_id"], artifact_type=native.ARTIFACT_TYPE,
              status="failed", content=r4["result"], source_run_id=r4["result"]["request_payload"]["workflow_run_id"], version_no=1, schema_version=r4["result"]["schema"])
    a5 = {**deepcopy(a4), "id": r5["artifact_id"], "content": r5["result"], "source_run_id": r5["result"]["request_payload"]["workflow_run_id"], "version_no": 2, "schema_version": r5["result"]["schema"]}
    w4 = wf_record(a4["source_run_id"], a4["content"]["request_payload"], a4["id"], a4["content"]["error"], implementation=a4["content"]["request_payload"]["implementation_source_hashes"])
    w5 = wf_record(a5["source_run_id"], a5["content"]["request_payload"], a5["id"], a5["content"]["error"], recovery=a5["content"]["protocol_recovery"])
    inputs, descriptor = title_fixture.fixture_inputs()
    title_wid, body_wid = str(uuid4()), str(uuid4())
    inputs["workflow_run_id"] = title_wid
    body_plan = selection.prepare_selection(a5["content"])
    body_args, _ = body_fixture.editor_diagnostic_arguments(body_plan)
    title_plan = judge.prepare_title_review(inputs, parent_descriptor=descriptor)
    body_e = tool_bundle(body_plan, body_args, selection, body_wid, inputs)
    title_e = tool_bundle(title_plan, title_fixture.answer(title_plan), judge, title_wid, inputs)
    artifacts, workflows, bundles = [], [], {}
    for kind, plan, module, wid, bundle in ((adapter.BODY_ARTIFACT_TYPE, body_plan, selection, body_wid, body_e),
                                          (adapter.TITLE_ARTIFACT_TYPE, title_plan, judge, title_wid, title_e)):
        manifest = deepcopy(inputs["manifest"])
        content = dict(schema=module.SCHEMA, request_payload=plan, semantic_selection_parent=descriptor, source_manifest=manifest,
            raw_response=bundle["completion"]["content"], retained_payloads=bundle["retained_payloads"], provider_receipt={k: deepcopy(bundle["completion"][k]) for k in
                ("llm_run_id", "provider", "model_name", "finish_reason", "fallback_used", "fallback_source", "metadata")})
        a = dict(id=str(uuid4()), project_id=inputs["project_id"], artifact_type=kind, status="validated", source_run_id=wid,
                 content=content, version_no=1, schema_version="selection.1")
        w = dict(id=wid, project_id=inputs["project_id"], workflow_type=kind, scope_type="project", scope_id=inputs["project_id"],
                 status="completed", metadata_json=dict(semantic_selection_parent=descriptor, source_manifest=manifest,
                     plan_binding_sha256=plan["binding_sha256"], execution_scope_id=inputs["execution_scope_id"]))
        artifacts.append(a); workflows.append(w)
        bundles[kind] = dict(artifact=a, workflow=w, evidence=bundle)
    parent = dict(parent_artifact=a5, parent_workflow=w5, parent_report=a5["content"], parent_evidence=original_native(a5["content"]),
                  original_v4_artifact=a4, original_v4_workflow=w4, original_v4_evidence=original_native(a4["content"]))
    return adapter.SelectionContext(inputs, dict(descriptor=descriptor, parent=parent, leaves=bundles,
        artifact_inventory=artifacts, workflow_inventory=workflows, original_artifact_inventory=[a4, a5]))


def test_leaf_short_tag_fits_actual_mapped_column_and_retains_full_content_schema(complete_context):
    width = native.PlanningArtifactVersionModel.__table__.c.schema_version.type.length
    assert width == 32
    assert len(adapter.LEAF_SCHEMA_VERSION) <= width
    leaves = adapter.verify_once_census(complete_context.evidence, descriptor=complete_context.evidence["descriptor"],
                                       current_manifest=complete_context.current_inputs["manifest"])
    for kind, module in ((adapter.BODY_ARTIFACT_TYPE, selection), (adapter.TITLE_ARTIFACT_TYPE, judge)):
        artifact, _ = leaves[kind]
        assert artifact["schema_version"] == "selection.1"
        assert artifact["content"]["schema"] == module.SCHEMA
        assert len(module.SCHEMA) > width


@pytest.mark.parametrize("kind", [adapter.BODY_ARTIFACT_TYPE, adapter.TITLE_ARTIFACT_TYPE])
@pytest.mark.parametrize("tag", [selection.SCHEMA, judge.SCHEMA, "selection.2", "stage.5", ""])
def test_leaf_long_or_mismatched_column_tag_blocks(complete_context, kind, tag):
    context = deepcopy(complete_context)
    context.evidence["leaves"][kind]["artifact"]["schema_version"] = tag
    with pytest.raises(ValueError, match="new_leaf_schema_version"):
        adapter.verify_once_census(context.evidence, descriptor=context.evidence["descriptor"],
                                  current_manifest=context.current_inputs["manifest"])


@pytest.fixture(scope="module")
def restored_physical_tool():
    """Actual saved paid return with SYNTHETIC restored SQL rows/byte encoding.

    No physical production read, row restoration, provider send or literary
    acceptance occurs. This solely tests native identity/receipt consistency.
    """
    probe=json.loads((BASE/"original-four-body-paid-return-recovery-probe.json").read_text())
    parent=json.loads((BASE/"original-four-whole-v5-protocol-execution.json").read_text())["result"]
    plan=selection.prepare_selection(parent)
    wid=probe["physical"]["request"]["workflow_run_id"]
    fields=selection.native_request_fields(plan,workflow_run_id=wid)
    assert plan["binding_sha256"]==probe["plan_binding_sha256"]
    root_result=deepcopy(probe["paid_result"])
    c=deepcopy(root_result);c["llm_run_id"]=probe["physical"]["original_llm_run_id"]
    c["metadata"]={**deepcopy(fields["metadata"]),**deepcopy(root_result["metadata"])}
    c["metadata"].update(llm_run_flushed=True,prompt_payload_receipt=dict(enabled=True,status="stored",replayable=True,
        request_ref=probe["physical"]["request_ref"],response_ref=probe["physical"]["response_ref"]))
    docs={k:deepcopy(probe["physical"][k]) for k in ("request","response")}
    inputs=dict(project_id=docs["request"]["project_id"],workflow_run_id=wid,
                execution_scope_id=probe["source_manifest"]["execution_scope_id"])
    evidence=native_bundle(inputs,c,docs,role="critic",template=fields["prompt_template"],version=fields["prompt_version"])
    n=evidence["native"]
    n["operation"]["result_json"]=deepcopy(root_result)
    n["transport_results"][0]["result_json"]["result"]=deepcopy(root_result)
    n["llm_run"]["created_at"]="2026-10-25T01:00:00Z"  # synthetic restoration-time fixture
    n["attempts"][0]["created_at"]="2026-10-25T00:00:00Z"  # independent synthetic original attempt
    failure=deepcopy(probe["wf_metadata"]["native_save_failure"])
    wf=dict(id=wid,project_id=inputs["project_id"],scope_type="project",scope_id=inputs["project_id"],status="completed",
            metadata_json=dict(native_save_failure=failure,no_resend=True))
    receipt=dict(schema="original-physical-telemetry-restoration.v1",reason="caller_transaction_rollback_sqlstate_22001",
        original_llm_run_id=c["llm_run_id"],original_workflow_run_id=wid,operation_key=n["operation"]["key"],
        attempt_id=n["attempts"][0]["id"],paid_result_sha256=legacy.digest(root_result),
        physical_sha256={ref:legacy.sha(raw) for ref,raw in evidence["physical_files"].items()},
        original_failure_sha256=legacy.digest(failure),provider_calls=0,original_paid_identity_preserved=True,
        reconstructed_request_metadata_sha256=legacy.digest(fields["metadata"]))
    c["metadata"][adapter.RESTORATION_KEY]=receipt
    n["llm_run"]["metadata_json"]=deepcopy(c["metadata"])
    return fields,evidence,wf,inputs


def test_restored_original_physical_identity_is_explicit_in_native_trace(restored_physical_tool):
    fields,e,wf,inputs=restored_physical_tool
    before=deepcopy((fields,e,wf))
    proof=adapter.verify_forced_native_call(fields,e,workflow=wf,project_id=inputs["project_id"],scope_id=inputs["execution_scope_id"])
    trace=proof[adapter.RESTORATION_KEY]
    assert trace["receipt"]==e["completion"]["metadata"][adapter.RESTORATION_KEY]
    assert trace["llm_run_created_at"] != trace["original_attempt_created_at"]
    assert trace["telemetry_status"]=="row_restored_from_original_physical_pair_not_continuously_persisted"
    assert e["native"]["operation"]["result_json"]["llm_run_id"] is None
    assert e["native"]["operation"]["result_json"]["metadata"]["llm_run_flushed"] is False
    assert (fields,e,wf)==before
    assert "passed" not in proof and "ready_for_upload" not in proof


@pytest.mark.parametrize("fault", ["missing", "schema", "reason", "run", "workflow", "key", "attempt", "paid_hash",
    "physical_hash", "failure_hash", "request_metadata_hash", "send", "bool_send", "identity_false", "extra_field", "execution_recovery", "missing_failure"])
def test_restoration_forgery_or_replay_fails_closed(restored_physical_tool,fault):
    fields,e,wf,inputs=deepcopy(restored_physical_tool)
    meta=e["completion"]["metadata"];r=meta[adapter.RESTORATION_KEY]
    mutation={"schema":("schema","wrong"),"reason":("reason","wrong"),"run":("original_llm_run_id",str(uuid4())),
        "workflow":("original_workflow_run_id",str(uuid4())),"key":("operation_key","0"*64),"attempt":("attempt_id","wrong"),
        "paid_hash":("paid_result_sha256","0"*64),"failure_hash":("original_failure_sha256","0"*64),
        "request_metadata_hash":("reconstructed_request_metadata_sha256","0"*64),"send":("provider_calls",1),
        "bool_send":("provider_calls",False),"identity_false":("original_paid_identity_preserved",False)}
    if fault in mutation:
        k,v=mutation[fault];r[k]=v
    elif fault=="missing":meta.pop(adapter.RESTORATION_KEY)
    elif fault=="physical_hash":r["physical_sha256"][next(iter(r["physical_sha256"]))]="0"*64
    elif fault=="extra_field":r["trusted_pass"]=True
    elif fault=="execution_recovery":meta["execution_recovery"]={"status":"replayed"}
    elif fault=="missing_failure":wf["metadata_json"].pop("native_save_failure")
    e["native"]["llm_run"]["metadata_json"]=deepcopy(meta)
    with pytest.raises(ValueError):
        adapter.verify_forced_native_call(fields,e,workflow=wf,project_id=inputs["project_id"],scope_id=inputs["execution_scope_id"])


def test_formal_dual_native_tools_close_exact_findings_preserve_history_and_debt(complete_context):
    before = deepcopy(complete_context)
    view = formal.derive_selection_readiness(complete_context)
    assert complete_context == before
    derived = view["semantic_derived_review"]
    assert derived["remaining_blockers"] == []
    assert all(view[k] for k in ("opening_gate_passed", "ranking_gate_passed", "short_v2_gate_passed"))
    assert derived["adjudication_trace"]["historical_title_refusals"][0]["resolved_row"]["verdict"] == "confirmed"
    assert derived["open_debt"][0]["status"] == "open"
    assert derived["human_attraction_acceptance"] == "not_performed"
    assert derived["original_report"] == complete_context.current_inputs["original_report"]
    assert derived["original_report"]["passed"] is False
    selected = derived["adjudication_trace"]["native_leaves"][adapter.BODY_ARTIFACT_TYPE]["validated"]["selected_rows"]
    by_id = {r["id"]: r for r in selected}
    for decision in derived["adjudicated_nonblocking"]:
        evidence = decision["evidence"]
        if evidence["id"] in by_id:
            record = by_id[evidence["id"]]
            assert evidence["reason"] == evidence["repaired_reason"] == record["repair_rationale"]
            assert evidence["original_reason"] == record["resolved_row"]["reason"] == record["original_reason"]
            assert evidence["original_reason_sha256"] == legacy.sha(record["original_reason"].encode())


@pytest.mark.parametrize("fault", ["title_manifest", "parent_scope"])
def test_wrong_title_manifest_or_parent_scope_blocks(complete_context,fault):
    context=deepcopy(complete_context)
    if fault=="title_manifest":
        manifest=context.evidence["leaves"][adapter.TITLE_ARTIFACT_TYPE]["artifact"]["content"]["source_manifest"]
        manifest["project"]["title"]="旧标题"
    else: context.evidence["parent"]["parent_workflow"]["scope_id"]=str(uuid4())
    with pytest.raises(ValueError): formal.derive_selection_readiness(context)


def test_common_builder_and_readback_keep_10902_target8000_false(complete_context):
    from bestseller.services.fanqie_short_export import build_signing_readiness_report
    p = complete_context.current_inputs
    args = dict(full_text=p["full_text"], title=p["title"], unlock_line_ratio=p["unlock_line_ratio"],
                protagonist_name=p["protagonist"], target_word_count=8000, semantic_context=complete_context)
    generated = build_signing_readiness_report(**args)
    assert generated == build_signing_readiness_report(**args)
    assert generated["total_words"] == 10902 and generated["target_word_count"] == 8000
    assert generated["ready_for_upload"] is False
    assert generated["semantic_adjudication_trace"]["open_debt"][0]["status"] == "open"


@pytest.mark.parametrize("kind", [adapter.BODY_ARTIFACT_TYPE, adapter.TITLE_ARTIFACT_TYPE])
def test_genuine_typed_abstention_keeps_formal_blockers(complete_context, kind):
    context=deepcopy(complete_context);bundle=context.evidence["leaves"][kind]
    plan=bundle["artifact"]["content"]["request_payload"]
    if kind==adapter.BODY_ARTIFACT_TYPE:
        module=selection;args=body_fixture.abstentions(plan)
    else:
        module=judge;args=title_fixture.answer(plan);args["title_assessment"]["verdict"]="uncertain"
    evidence=tool_bundle(plan,args,module,bundle["workflow"]["id"],context.current_inputs)
    bundle["evidence"]=evidence
    content=bundle["artifact"]["content"]
    content.update(raw_response=evidence["completion"]["content"],retained_payloads=evidence["retained_payloads"],
        provider_receipt={k:deepcopy(evidence["completion"][k]) for k in content["provider_receipt"]})
    view=formal.derive_selection_readiness(context)
    assert view["semantic_derived_review"]["remaining_blockers"]
    if kind==adapter.TITLE_ARTIFACT_TYPE:
        assert any(r["code"]=="current_title_assessment_unaccepted" for r in view["semantic_derived_review"]["remaining_blockers"])


@pytest.mark.parametrize("fault", ["failed_body", "pending_title", "extra_failed_artifact", "extra_unknown_workflow",
    "missing_title", "missing_parent4", "foreign_wf", "unexplained_parent", "stale_body", "raw_parent",
    "tool_arguments", "tool_choice", "extra_attempt", "missing_physical", "duplicate_keys", "fake_stored_pass"])
def test_formal_faults_never_grant_readiness(complete_context, fault):
    context = deepcopy(complete_context); e = context.evidence
    body = e["leaves"][adapter.BODY_ARTIFACT_TYPE]
    if fault == "failed_body": body["artifact"]["status"] = "failed"
    elif fault == "pending_title": e["leaves"][adapter.TITLE_ARTIFACT_TYPE]["workflow"]["status"] = "pending"
    elif fault == "extra_failed_artifact":
        a=deepcopy(body["artifact"]);a.update(id=str(uuid4()),status="failed");e["artifact_inventory"].append(a)
    elif fault == "extra_unknown_workflow":
        w=deepcopy(body["workflow"]);w.update(id=str(uuid4()),status="unknown");e["workflow_inventory"].append(w)
    elif fault == "missing_title": e["artifact_inventory"] = [e["artifact_inventory"][0]]
    elif fault == "missing_parent4": e["original_artifact_inventory"] = [e["parent"]["parent_artifact"]]
    elif fault == "foreign_wf": body["workflow"]["project_id"] = str(uuid4())
    elif fault == "unexplained_parent": body["artifact"]["content"][adapter.SELECTION_PARENT_KEY] = {}
    elif fault == "stale_body": context.current_inputs["full_text"] += "改"
    elif fault == "raw_parent": e["parent"]["parent_report"]["raw_response"] += " "
    elif fault == "tool_arguments": body["evidence"]["completion"]["tool_calls"][0]["function"]["arguments"] = "{}"
    elif fault == "tool_choice": body["evidence"]["retained_payloads"]["request"]["requested"]["tool_choice"] = "auto"
    elif fault == "extra_attempt": body["evidence"]["native"]["attempts"].append(deepcopy(body["evidence"]["native"]["attempts"][0]))
    elif fault == "missing_physical": body["evidence"]["physical_files"].clear()
    elif fault == "duplicate_keys": body["evidence"]["physical_files"][next(iter(body["evidence"]["physical_files"]))] = b'{"a":1,"a":2}'
    elif fault == "fake_stored_pass":
        body["artifact"]["content"]["selection_proof"] = {"passed": True}
        body["evidence"]["native"]["operation"]["status"] = "unknown"
    with pytest.raises((ValueError, KeyError)):
        formal.derive_selection_readiness(context)


@pytest.fixture
async def native_store(tmp_path, complete_context):
    async for store in _seed_native_store(tmp_path, complete_context):
        yield store


async def _seed_native_store(tmp_path, complete_context):
    target = tmp_path / "readiness_test.sqlite3"
    assert target.stem.endswith("_test")
    engine = create_async_engine("sqlite+aiosqlite:///" + str(target))
    models = (native.ProjectModel, native.WorkflowRunModel, native.PlanningArtifactVersionModel,
              native.LlmRunModel, native.ExecutionBudgetModel, native.ExecutionOperationModel, native.ExecutionAttemptModel)
    async with engine.begin() as connection:
        for model in models:
            ddl = str(CreateTable(model.__table__).compile(dialect=connection.dialect))
            await connection.exec_driver_sql(ddl.replace("::jsonb", ""))
    session = async_sessionmaker(engine, expire_on_commit=False)()
    context = deepcopy(complete_context)
    p, e = context.current_inputs, context.evidence
    project = native.ProjectModel(id=UUID(p["project_id"]), slug="formal-readiness-test", title=p["title"], genre="现实",
                                  target_chapters=4, target_word_count=8000, metadata_json={"fixture_authoritative_inputs": p})
    session.add(project)
    now = datetime.now(UTC)
    session.add(native.ExecutionBudgetModel(scope_id=p["execution_scope_id"], max_calls=300, calls_used=290,
        max_tokens=100000000, tokens_charged=20000000, status="active", deadline_at=now, metadata_json={}))
    parent = e["parent"]
    artifacts = [parent["original_v4_artifact"], parent["parent_artifact"], *e["artifact_inventory"]]
    workflows = [parent["original_v4_workflow"], parent["parent_workflow"], *e["workflow_inventory"]]
    bundles = [parent["original_v4_evidence"], parent["parent_evidence"], *[b["evidence"] for b in e["leaves"].values()]]
    for source in artifacts:
        row=deepcopy(source)
        for key in ("id", "project_id", "source_run_id"): row[key]=UUID(row[key])
        session.add(native.PlanningArtifactVersionModel(**row))
    for source in workflows:
        row=deepcopy(source)
        for key in ("id", "project_id", "scope_id"): row[key]=UUID(row[key])
        session.add(native.WorkflowRunModel(**row))
    for bundle in bundles:
        n=bundle["native"];run=deepcopy(n["llm_run"])
        for key in ("id", "project_id", "workflow_run_id"): run[key]=UUID(run[key])
        session.add(native.LlmRunModel(**run))
        for source in (n["operation"], *n["transport_results"]):
            session.add(native.ExecutionOperationModel(**deepcopy(source), owner="synthetic-fixture", lease_until=now))
        session.add(native.ExecutionAttemptModel(**deepcopy(n["attempts"][0]), created_at=now))
        for ref, data in bundle["physical_files"].items():
            path=tmp_path/ref;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
    await session.commit()
    assembly=SimpleNamespace(full_text=p["full_text"],manifest=deepcopy(p["manifest"]))
    settings=SimpleNamespace(artifact_store=SimpleNamespace(local_dir=str(tmp_path)))

    async def factory(session, fresh_project, fresh_assembly, source_run_id):
        # Test DB represents independent authoritative canonical/source rows;
        # never reads candidate artifact.request_payload as current inputs.
        metadata=await session.scalar(select(native.ProjectModel.metadata_json).where(native.ProjectModel.id==fresh_project.id))
        values=deepcopy(metadata["fixture_authoritative_inputs"])
        values.update(full_text=fresh_assembly.full_text, manifest=fresh_assembly.manifest,
                      title=fresh_project.title, workflow_run_id=str(source_run_id))
        return values

    @contextmanager
    def select_only():
        def check(conn,cursor,statement,parameters,ctx,many):
            assert statement.lstrip().upper().startswith("SELECT"), statement[:80]
        event.listen(engine.sync_engine,"before_cursor_execute",check)
        from unittest.mock import AsyncMock, patch
        try:
            with patch.object(session,"flush",new=AsyncMock(side_effect=AssertionError("flush forbidden"))), \
                 patch.object(session,"commit",new=AsyncMock(side_effect=AssertionError("commit forbidden"))):
                yield
        finally: event.remove(engine.sync_engine,"before_cursor_execute",check)
    try: yield SimpleNamespace(session=session,project=project,assembly=assembly,settings=settings,factory=factory,
                               context=context,select_only=select_only)
    finally:
        await session.close();await engine.dispose()


async def test_sqlite_formal_loader_all_native_rows_physical_and_common_dispatch(native_store, monkeypatch):
    store=native_store
    with store.select_only():
        loaded=await formal.load_current_selection_context(store.session,store.settings,project=store.project,
            assembly=store.assembly,original_report=store.context.current_inputs["original_report"],current_inputs_factory=store.factory)
    assert formal.derive_selection_readiness(loaded)["semantic_derived_review"]["remaining_blockers"]==[]
    async def fresh(session, project, assembly, source_run_id, *, settings):
        return await store.factory(session,project,assembly,source_run_id)
    monkeypatch.setattr(common,"_fresh_inputs",fresh)
    with store.select_only():
        context=await common.load_upload_semantic_context(store.session,settings=store.settings,project=store.project,
                    assembly=store.assembly,original_report=store.context.current_inputs["original_report"])
    assert isinstance(context,adapter.SelectionContext)
    assert common.semantic_readiness_view(context,full_text=store.assembly.full_text,title=store.project.title,
        unlock_line_ratio=.30,protagonist_name=store.context.current_inputs["protagonist"])["opening_gate_passed"]


async def test_present_pending_or_corrupt_new_evidence_cannot_fall_back(native_store, monkeypatch):
    store=native_store
    async def legacy_forbidden(*args,**kwargs): raise AssertionError("legacy fallback forbidden")
    monkeypatch.setattr(native,"load_current_semantic_context",legacy_forbidden)
    title=store.context.evidence["leaves"][adapter.TITLE_ARTIFACT_TYPE]["artifact"]
    await store.session.execute(update(native.PlanningArtifactVersionModel).where(native.PlanningArtifactVersionModel.id==UUID(title["id"])).values(status="failed"))
    await store.session.commit()
    with store.select_only(),pytest.raises(ValueError,match="incomplete"):
        await common.load_upload_semantic_context(store.session,settings=store.settings,project=store.project,
            assembly=store.assembly,original_report=store.context.current_inputs["original_report"])
    await store.session.execute(update(native.PlanningArtifactVersionModel).where(native.PlanningArtifactVersionModel.id==UUID(title["id"])).values(content={}))
    await store.session.commit()
    with store.select_only(),pytest.raises(ValueError,match="source_manifest"):
        await common.load_upload_semantic_context(store.session,settings=store.settings,project=store.project,
            assembly=store.assembly,original_report=store.context.current_inputs["original_report"])


@pytest.fixture(scope="module")
def failed_title_context(complete_context):
    context=deepcopy(complete_context)
    actual=json.loads((BASE/"original-four-title-tool-execution.json").read_text())
    plan=actual["request_payload"];wf_id=actual["identity"]["workflow_run_id"]
    values=deepcopy(plan["current_inputs"]);values["workflow_run_id"]=wf_id
    p=dict(project_id=values["project_id"],workflow_run_id=wf_id,execution_scope_id=values["execution_scope_id"])
    evidence=native_bundle(p,deepcopy(actual["original_completion"]),deepcopy(actual["result"]["retained_payloads"]),
        role="critic",template=judge.TEMPLATE,version=judge.VERSION)
    evidence["request_payload"]=deepcopy(plan)
    a=dict(id=actual["artifact_id"],project_id=values["project_id"],artifact_type=adapter.TITLE_ARTIFACT_TYPE,
        status="failed",source_run_id=wf_id,content=deepcopy(actual["result"]),version_no=1,schema_version="selection.1")
    w=dict(id=wf_id,project_id=values["project_id"],workflow_type=adapter.TITLE_ARTIFACT_TYPE,
        status="machine_blocked",scope_type="project",scope_id=values["project_id"],metadata_json=dict(
        semantic_selection_parent=actual["result"]["semantic_selection_parent"],source_manifest=deepcopy(values["manifest"]),
        plan_binding_sha256=plan["binding_sha256"],execution_scope_id=values["execution_scope_id"]))
    e=context.evidence
    e["leaves"][adapter.TITLE_ARTIFACT_TYPE]=dict(artifact=a,workflow=w,evidence=evidence)
    e["artifact_inventory"]=[e["leaves"][adapter.BODY_ARTIFACT_TYPE]["artifact"],a]
    e["workflow_inventory"]=[e["leaves"][adapter.BODY_ARTIFACT_TYPE]["workflow"],w]
    return adapter.SelectionContext(values,e)


@pytest.fixture(scope="module")
def linked_title_context(failed_title_context):
    from bestseller.services import fanqie_short_title_contract_linkage as linkage
    context=deepcopy(failed_title_context);e=context.evidence;b=e["leaves"][adapter.TITLE_ARTIFACT_TYPE]
    plan=b["artifact"]["content"]["request_payload"]
    proof=linkage.prepare_title_contract_linkage(plan,b["evidence"]["completion"])
    title_ref=adapter._title_reference(b["artifact"],b["workflow"],b["evidence"]["completion"],proof["original_failure"])
    content=dict(schema=linkage.SCHEMA,title_artifact=title_ref,semantic_selection_parent=e["descriptor"],
        source_manifest=deepcopy(context.current_inputs["manifest"]),execution_scope_id=context.current_inputs["execution_scope_id"],
        linkage_proof=proof,called=False,provider_calls=0)
    tid=b["artifact"]["id"];wid=str(uuid5(NAMESPACE_URL,"title-contract-linkage:"+tid))
    aid=str(uuid5(NAMESPACE_URL,"title-contract-linkage-artifact:"+tid))
    metadata=dict(content=deepcopy(content),title_artifact=title_ref,semantic_selection_parent=e["descriptor"],
        source_manifest=deepcopy(context.current_inputs["manifest"]),execution_scope_id=context.current_inputs["execution_scope_id"],
        linkage_sha256=legacy.digest(proof))
    a=dict(id=aid,project_id=context.current_inputs["project_id"],artifact_type=adapter.LINK_ARTIFACT_TYPE,status="validated",
        schema_version="link.1",version_no=1,source_run_id=wid,content=content,idempotency_key="title-contract-linkage-artifact:"+tid)
    w=dict(id=wid,project_id=context.current_inputs["project_id"],workflow_type=adapter.LINK_ARTIFACT_TYPE,
        status="completed",scope_type="project",scope_id=context.current_inputs["project_id"],metadata_json=metadata,
        current_step="title_contract_references_linked",idempotency_key="title-contract-linkage:"+tid)
    e["artifact_inventory"].append(a);e["workflow_inventory"].append(w)
    e["title_contract_link"]=dict(artifact=a,workflow=w,nonpaid_llm_runs=[])
    return context


def test_linked_actual_failed_title_is_a_derived_reference_not_original_pass(linked_title_context):
    context=linked_title_context;before=deepcopy(context)
    view=formal.derive_selection_readiness(context)
    assert context==before
    derived=view["semantic_derived_review"];assert derived["remaining_blockers"]==[]
    native_proof=derived["adjudication_trace"]["native_leaves"][adapter.TITLE_ARTIFACT_TYPE]["native"]
    link=native_proof["title_contract_linkage"]
    assert link["original_paid_workflow_status"]=="machine_blocked"
    assert link["original_paid_artifact_status"]=="failed"
    assert link["proof"]["original_failure"]=="explicit_creator_evidence_missing"
    assert {r["contract_id"] for r in link["proof"]["borrowed_contract_refs"]}=={"k124","k298","k295"}
    assert link["proof"]["semantic_limitations"]["semantic_overclaims_not_repaired"] is True
    assert derived["open_debt"] and derived["human_attraction_acceptance"]=="not_performed"
    assert derived["model_grade_asserted"] is False


@pytest.mark.parametrize("fault",["missing_link","second_failed_link","second_pending_wf","link_failed","link_wf_unknown",
    "original_relabelled","foreign_link","stored_proof_changed","new_link_paid_run","source_drift"])
def test_link_once_and_original_failure_are_not_bypassable(linked_title_context,fault):
    context=deepcopy(linked_title_context);e=context.evidence;record=e["title_contract_link"]
    if fault=="missing_link":e["artifact_inventory"].remove(record["artifact"])
    elif fault=="second_failed_link":
        row=deepcopy(record["artifact"]);row.update(id=str(uuid4()),status="failed");e["artifact_inventory"].append(row)
    elif fault=="second_pending_wf":
        row=deepcopy(record["workflow"]);row.update(id=str(uuid4()),status="pending");e["workflow_inventory"].append(row)
    elif fault=="link_failed":record["artifact"]["status"]="failed"
    elif fault=="link_wf_unknown":record["workflow"]["status"]="unknown"
    elif fault=="original_relabelled":e["leaves"][adapter.TITLE_ARTIFACT_TYPE]["workflow"]["status"]="completed"
    elif fault=="foreign_link":record["workflow"]["project_id"]=str(uuid4())
    elif fault=="stored_proof_changed":record["artifact"]["content"]["linkage_proof"]["original_failure"]="pretend"
    elif fault=="new_link_paid_run":record["nonpaid_llm_runs"].append({"id":str(uuid4())})
    elif fault=="source_drift":record["artifact"]["content"]["source_manifest"]["project"]["title"]="另题"
    with pytest.raises((ValueError,KeyError)):formal.derive_selection_readiness(context)


def test_original_failed_title_cannot_be_consumed_without_link(failed_title_context):
    with pytest.raises(ValueError,match="incomplete"):formal.derive_selection_readiness(failed_title_context)


@pytest.fixture
async def failed_title_store(tmp_path,failed_title_context):
    async for store in _seed_native_store(tmp_path,failed_title_context):yield store


@pytest.fixture
async def linked_title_store(tmp_path,linked_title_context):
    async for store in _seed_native_store(tmp_path,linked_title_context):yield store


async def test_readonly_link_helper_matches_fixed_operator_four_keys_plus_proof(failed_title_store,monkeypatch):
    store=failed_title_store
    async def fresh(session,project,assembly,source_run_id,*,settings):return await store.factory(session,project,assembly,source_run_id)
    monkeypatch.setattr(common,"_fresh_inputs",fresh)
    with store.select_only():
        prepared=await adapter.prepare_current_title_contract_linkage(store.session,store.settings,
            project=store.project,assembly=store.assembly)
    assert set(prepared)=={"content","workflow_metadata","workflow_id","artifact_id","native_proof"}
    tid=store.context.evidence["leaves"][adapter.TITLE_ARTIFACT_TYPE]["artifact"]["id"]
    assert prepared["workflow_id"]==str(uuid5(NAMESPACE_URL,"title-contract-linkage:"+tid))
    assert prepared["artifact_id"]==str(uuid5(NAMESPACE_URL,"title-contract-linkage-artifact:"+tid))
    assert prepared["workflow_metadata"]["content"]==prepared["content"]
    assert prepared["content"]["provider_calls"]==0 and prepared["content"]["called"] is False
    assert prepared["content"]["title_artifact"]["error"]=="explicit_creator_evidence_missing"
    assert store.context.evidence["leaves"][adapter.TITLE_ARTIFACT_TYPE]["artifact"]["status"]=="failed"


async def test_link_common_native_readback_and_no_second_helper(linked_title_store,monkeypatch):
    store=linked_title_store
    async def fresh(session,project,assembly,source_run_id,*,settings):return await store.factory(session,project,assembly,source_run_id)
    monkeypatch.setattr(common,"_fresh_inputs",fresh)
    with store.select_only():
        context=await common.load_upload_semantic_context(store.session,settings=store.settings,project=store.project,
            assembly=store.assembly,original_report=store.context.current_inputs["original_report"])
    view=common.semantic_readiness_view(context,full_text=store.assembly.full_text,title=store.project.title,
        unlock_line_ratio=.30,protagonist_name=store.context.current_inputs["protagonist"])
    audit=view["semantic_derived_review"]["adjudication_trace"]["native_leaves"][adapter.TITLE_ARTIFACT_TYPE]["native"]["title_contract_linkage"]["nonpaid_audit"]
    assert audit["workflow_id"]==store.context.evidence["title_contract_link"]["workflow"]["id"]
    assert audit["artifact_id"]==store.context.evidence["title_contract_link"]["artifact"]["id"]
    with store.select_only(),pytest.raises(ValueError,match="already_exists_any_status"):
        await adapter.prepare_current_title_contract_linkage(store.session,store.settings,project=store.project,assembly=store.assembly)
