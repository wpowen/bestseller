"""Synthetic native rows/physical files only; no production or provider access."""

from copy import deepcopy
from datetime import UTC, datetime
import hashlib
import importlib.util
import json
import os
import socket
import tempfile
import unittest
from unittest.mock import AsyncMock, patch
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.schema import CreateTable

import test_fanqie_short_semantic_review as f
import test_fanqie_short_semantic_adapter as db_fixture
from bestseller.services import fanqie_short_semantic_adapter as adapter
from bestseller.services import fanqie_short_semantic_consumer as consumer
from bestseller.services import fanqie_short_semantic_protocol_recovery as recovery
from bestseller.services import fanqie_short_semantic_review as semantic


ROOT = Path(__file__).resolve().parents[2]
FROZEN = ROOT / "reports/project-reliability-review-2026-10-24/implementation/whole-v4-protocol-preimage"
spec = importlib.util.spec_from_file_location("witnessed_v4_test_dependency", FROZEN / "src/bestseller/services/fanqie_short_semantic_review.py")
v4 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(v4)


def paid(payload, rows, route):
    """A visibly synthetic gateway/ledger, without replacing any validator."""
    raw = route._json({"binding_sha256": payload["binding_sha256"], "reviews": rows})
    rid, aid = str(uuid4()), str(uuid4())
    h = "b" * 64
    key = route._sha(payload["execution_scope_id"] + "\n" + route.native_request_fields(payload)["operation_id"] + ":sample:0")
    meta = {
        "llm_run_flushed": True,
        "prompt_payload_receipt": {"enabled": True, "status": "stored", "replayable": True,
                                   **{k + "_ref": f"runtime/llm_payloads/{rid}.{k}.json" for k in ("request", "response")}},
        "effective_input_receipt": {"schema": "effective-input.v1", "hash": h, "model": "critic-model",
                                    "messages_source": "system_user", "tools_included": False,
                                    "legacy_prompt_hash": route._sha(route.SYSTEM_PROMPT + "\n\n" + route._user_prompt(payload))},
        "execution_receipt": {"scope_id": payload["execution_scope_id"], "operation_key": key,
                              "policy": "execution-budget.v1", "attempts": [{"attempt_id": aid, "status": "completed",
                                "reserved_tokens": 10000, "effective_input_hash": h, "model": "critic-model",
                                "wire_body_hash": hashlib.sha256(b"synthetic wire").hexdigest()}]},
        "paid_call_origin": {"schema": "paid-call-origin.v1", "status": "recorded", "project_id": payload["project_id"],
                             "workflow_run_id": payload["workflow_run_id"], "logical_role": "critic",
                             "prompt_template": "fanqie_short_semantic_review", "prompt_version": route.PROMPT_VERSION,
                             "prompt_hash": h},
    }
    completion = {"content": raw, "provider": "openai", "model_name": "critic-model", "llm_run_id": rid,
                  "fallback_used": False, "fallback_source": None, "finish_reason": "stop", "tool_calls": None,
                  "metadata": meta, "input_tokens": 30, "output_tokens": 25}
    common = {"schema_version": "llm-payload.v1", "replayable": True, "replayability_reasons": [], "llm_run_id": rid,
              "project_id": payload["project_id"], "workflow_run_id": payload["workflow_run_id"]}
    def block(text):
        return {"text": text, "truncated": False, "original_chars": len(text)}
    retained = {
        "request": {**common, "kind": "request", "logical_role": "critic", "prompt_template": "fanqie_short_semantic_review",
                    "prompt_version": route.PROMPT_VERSION, "messages": {"system": block(route.SYSTEM_PROMPT), "user": block(route._user_prompt(payload))},
                    "effective": {"provider": "openai", "model": "critic-model"},
                    "requested": {"max_tokens_override": route.POLICY["max_output_tokens"], "temperature_override": 0, "model_tier": "strong"}},
        "response": {**common, "kind": "response", "content": block(raw), "fallback_used": False,
                     "provider": "openai", "model": "critic-model", "finish_reason": "stop", "tool_call_count": 0},
    }
    run = {k: completion[k] for k in ("provider", "model_name", "finish_reason", "input_tokens", "output_tokens")}
    run.update(id=rid, project_id=payload["project_id"], workflow_run_id=payload["workflow_run_id"], logical_role="critic",
               prompt_template="fanqie_short_semantic_review", prompt_version=route.PROMPT_VERSION, prompt_hash=h, metadata_json=deepcopy(meta))
    files = {}
    for kind in ("request", "response"):
        ref = meta["prompt_payload_receipt"][kind + "_ref"]
        run[kind + "_payload_ref"] = ref
        files[ref] = json.dumps(retained[kind], ensure_ascii=False).encode()
    op = {"key": key, "scope_id": payload["execution_scope_id"], "input_hash": h, "status": "completed", "result_json": deepcopy(completion)}
    attempt = {"id": aid, "operation_key": key, "scope_id": op["scope_id"], "input_hash": h, "model_name": "critic-model",
               "status": "completed", "actual_tokens": 55, "reserved_tokens": 10000}
    transport = {"key": route._sha("transport-result.v1\n" + op["scope_id"] + "\n" + aid), "scope_id": op["scope_id"],
                 "input_hash": h, "status": "transport_result", "result_json": {"schema": "transport-result.v1", "operation_key": key,
                 "attempt_id": aid, "input_hash": h, "result": deepcopy(completion)}}
    return {"request_payload": payload, "completion": completion, "retained_payloads": retained, "physical_files": files,
            "native": {"llm_run": run, "operation": op, "attempts": [attempt], "transport_results": [transport]}}


def parent_fixture(valid_rejection=False):
    inputs = f.kwargs(("opening_pressure_missing",), text=f.SOURCE + "\n" + "旧物记录。" * 100)
    inputs["manifest"]["sources"][0]["promotion_state"] = "promoted"
    inputs["manifest"]["project"] = {"title": inputs["title"]}
    inputs["operation_namespace"] = "fanqie-whole-semantic-v4"
    inputs["original_report"].update(opening_passed=True, ranking_passed=False, short_v2_passed=False,
                                      notes=[], thresholds={"protected": 1})
    payload = v4.prepare_review(**inputs)
    row = f.row(payload, payload["reviews_requested"][0], verdict="confirmed" if valid_rejection else "not_supported")
    if not valid_rejection:
        row["evidence"] = [{"source": "full_text", "quote": "这句从未在原文出现。"}]
    evidence = paid(payload, [row], v4)
    saved = v4.validate_response(payload, evidence["completion"], current_binding=payload["binding"], retained_payloads=evidence["retained_payloads"])
    saved["called"] = True
    aid = str(uuid4())
    artifact = {"id": aid, "project_id": payload["project_id"], "source_run_id": payload["workflow_run_id"],
                "artifact_type": adapter.ARTIFACT_TYPE, "status": "failed", "content": saved,
                "schema_version": v4.SCHEMA, "version_no": 1}
    workflow = {"id": payload["workflow_run_id"], "project_id": payload["project_id"], "scope_type": "project",
                "scope_id": payload["project_id"], "workflow_type": "original_four_whole_semantic_review", "status": "machine_blocked",
                "metadata_json": {"execution_scope_id": payload["execution_scope_id"], "source_manifest": deepcopy(payload["manifest"]),
                    "semantic_binding_sha256": payload["binding_sha256"], "artifact_id": aid, "validation_error": saved["error"],
                    "implementation": deepcopy(recovery.V4_SOURCES)}}
    return inputs, artifact, workflow, evidence


def prove(artifact, workflow, evidence):
    return recovery.verify_parent_batch(artifact, workflow, evidence, consumer=consumer, semantic=semantic)


def child_fixture(codes=("opening_pressure_missing",), verdicts=None, opening_codes=(), opening_mutator=None):
    codes = tuple(dict.fromkeys(("opening_pressure_missing", *codes)))
    inputs, parent, pw, pe = parent_fixture()
    # Parent/child use exactly the same original ranking/v2 inventory. v5 only
    # adds the full opening report, including opaque failures that cannot clear.
    if codes != ("opening_pressure_missing",):
        rows = [f.finding(code, inputs["full_text"]) for code in codes]
        inputs["original_report"]["ranking_report"]["findings"] = rows
        inputs["original_report"]["short_v2_report"]["findings"] = deepcopy(rows)
        payload = v4.prepare_review(**inputs)
        parent_rows = []
        for item in payload["reviews_requested"]:
            row = f.row(payload, item)
            row["evidence"] = [{"source": "full_text", "quote": "这句从未在原文出现。"}]
            parent_rows.append(row)
        pe = paid(payload, parent_rows, v4)
        saved = v4.validate_response(payload, pe["completion"], current_binding=payload["binding"], retained_payloads=pe["retained_payloads"])
        saved["called"] = True
        parent["content"] = saved
        pw["metadata_json"].update(semantic_binding_sha256=payload["binding_sha256"], validation_error=saved["error"])
    child_inputs = deepcopy(inputs)
    child_inputs["workflow_run_id"] = str(uuid4())
    child_inputs["operation_namespace"] = "fanqie-whole-semantic-v5"
    native_findings = [{k: v for k, v in f.finding(code, inputs["full_text"]).items() if k in {"code", "severity", "message", "evidence"}}
                       for code in opening_codes]
    child_inputs["original_report"]["opening_passed"] = not native_findings
    # Do not change the old report grade. Real parents already have this grade.
    if native_findings:
        # Rebuild the parent from its unchanged native fixture inputs so its paid
        # binding remains coherent, rather than editing retained proof afterward.
        inputs["original_report"]["opening_passed"] = False
        payload = v4.prepare_review(**inputs)
        parent_rows = []
        for item in payload["reviews_requested"]:
            row = f.row(payload, item)
            row["evidence"] = [{"source": "full_text", "quote": "这句从未在原文出现。"}]
            parent_rows.append(row)
        pe = paid(payload, parent_rows, v4)
        parent["content"] = v4.validate_response(payload, pe["completion"], current_binding=payload["binding"], retained_payloads=pe["retained_payloads"])
        parent["content"]["called"] = True
        pw["metadata_json"].update(semantic_binding_sha256=payload["binding_sha256"], validation_error=parent["content"]["error"])
    child_inputs["original_report"]["opening_report"] = {"passed": not native_findings, "findings": native_findings,
        "unlock_zone_words": max(50, int(len(inputs["full_text"].strip()) * .3)), "total_words": len(inputs["full_text"].strip()), "unlock_ratio": .3}
    if opening_mutator is not None:
        opening_mutator(child_inputs["original_report"]["opening_report"])
    child_inputs["recovery_parent"] = recovery.descriptor(parent)
    payload = semantic.prepare_review(**child_inputs)
    answer_rows = []
    for item in payload["reviews_requested"]:
        verdict = (verdicts or {}).get(item["code"], "not_supported")
        row = f.title_row(payload, item) if item["target"] == "title" and verdict == "not_supported" else f.row(payload, item, verdict=verdict)
        if item["code"] == "first_10k_loop_missing" and verdict == "not_supported":
            row["events"] = f.events(payload)[:1]
        answer_rows.append(row)
    evidence = paid(payload, answer_rows, semantic)
    saved = semantic.validate_response(payload, evidence["completion"], current_binding=payload["binding"], retained_payloads=evidence["retained_payloads"])
    assert saved["error"] is None, saved["error"]
    desc = recovery.descriptor(parent)
    saved["protocol_recovery"] = desc
    child = {**deepcopy(parent), "id": str(uuid4()), "version_no": 2, "source_run_id": payload["workflow_run_id"],
             "status": "validated", "content": saved, "schema_version": semantic.SCHEMA}
    cw = {**deepcopy(pw), "id": payload["workflow_run_id"], "status": "completed", "metadata_json": {
        "execution_scope_id": payload["execution_scope_id"], "source_manifest": deepcopy(payload["manifest"]),
        "semantic_binding_sha256": payload["binding_sha256"], "protocol_recovery": desc}}
    evidence["protocol_recovery"] = {"descriptor": desc, "parent_artifact": parent, "parent_workflow": pw,
        "parent_evidence": pe, "child_artifact": child, "child_workflow": cw,
        "artifact_inventory": [deepcopy(parent), deepcopy(child)], "workflow_inventory": [deepcopy(pw), deepcopy(cw)]}
    return child_inputs, evidence


def test_two_original_paid_runs_and_protocol_chain_reverified():
    inputs, evidence = child_fixture()
    before = deepcopy((inputs, evidence))
    result = consumer.consume_semantic_evidence(current_inputs=inputs, evidence=evidence, semantic=semantic)
    assert result["adjudication_trace"]["status"] == "verified", result
    assert result["remaining_blockers"] == []
    assert result["adjudication_trace"]["protocol_recovery"]["rows_reused"] is False
    assert (inputs, evidence) == before


@pytest.mark.parametrize("fault", ["parent_content", "parent_omitted", "second_artifact", "second_workflow", "foreign_child", "foreign_parent", "descriptor", "new_wf_pointer", "old_run", "old_schema"])
def test_child_chain_faults_never_remove_a_blocker(fault):
    inputs, e = child_fixture()
    c = e["protocol_recovery"]
    if fault == "parent_content": c["parent_artifact"]["content"]["raw_response"] += " "
    elif fault == "parent_omitted": c["artifact_inventory"] = [c["child_artifact"]]
    elif fault == "second_artifact":
        a = deepcopy(c["child_artifact"]); a["id"] = str(uuid4()); c["artifact_inventory"].append(a)
    elif fault == "second_workflow":
        w = deepcopy(c["child_workflow"]); w["id"] = str(uuid4()); c["workflow_inventory"].append(w)
    elif fault == "foreign_child": c["child_workflow"]["project_id"] = str(uuid4())
    elif fault == "foreign_parent": c["parent_workflow"]["project_id"] = str(uuid4())
    elif fault == "descriptor": c["descriptor"]["content_sha256"] = "0" * 64
    elif fault == "new_wf_pointer": c["child_workflow"]["metadata_json"]["protocol_recovery"] = {}
    elif fault == "old_run": e["completion"]["llm_run_id"] = c["parent_evidence"]["completion"]["llm_run_id"]
    elif fault == "old_schema": c["child_artifact"]["content"]["schema"] = v4.SCHEMA
    result = consumer.consume_semantic_evidence(current_inputs=inputs, evidence=e, semantic=semantic)
    assert result["adjudication_trace"]["status"] == "unverified"
    assert result["adjudicated_nonblocking"] == []
    assert result["remaining_blockers"]


def test_complete_opening_native_criticals_closed_individually():
    inputs, e = child_fixture(codes=("weak_hook", "first_10k_loop_missing"), opening_codes=("weak_hook", "first_10k_loop_missing"))
    view = consumer.semantic_readiness_gates(current_inputs=inputs, evidence=e, semantic=semantic)
    assert view["opening_gate_passed"] is True
    assert view["semantic_derived_review"]["original_report"]["opening_passed"] is False
    assert len([r for r in view["semantic_derived_review"]["adjudicated_nonblocking"] if r["gate"] == "opening"]) == 2


@pytest.mark.parametrize("fault", ["confirmed", "uncertain", "missing_ranking", "changed_native_evidence", "extra_native_critical", "missing_inventory", "inconsistent_pass"])
def test_opening_never_inferred_from_ranking_absence_or_raw_flags(fault):
    verdicts = {"weak_hook": fault} if fault in {"confirmed", "uncertain"} else None
    inputs, e = child_fixture(codes=("weak_hook",), opening_codes=("weak_hook",), verdicts=verdicts)
    if fault not in {"confirmed", "uncertain"}:
        report = inputs["original_report"]
        if fault == "missing_ranking":
            report["ranking_report"] = {"passed": True, "findings": []}
            report["short_v2_report"] = {"passed": True, "findings": []}
            report["ranking_passed"] = report["short_v2_passed"] = True
        elif fault == "changed_native_evidence": report["opening_report"]["findings"][0]["evidence"] = "another original span"
        elif fault == "extra_native_critical": report["opening_report"]["findings"].append({"code": "unreviewed_native_failure", "severity": "critical", "message": "new", "evidence": "原文"})
        elif fault == "missing_inventory": report.pop("opening_report")
        elif fault == "inconsistent_pass": report["opening_report"]["passed"] = True
    try:
        result = consumer.consume_semantic_evidence(current_inputs=inputs, evidence=e, semantic=semantic)
    except ValueError:
        return  # Malformed current report/payload is itself a hard block.
    assert any(b["gate"] == "opening" for b in result["remaining_blockers"]), result


def test_title_abstract_eligible_but_confirmed_retains_original_rejection():
    for verdict in ("not_supported", "confirmed"):
        inputs, e = child_fixture(codes=("title_abstract_setting_only",), verdicts={"title_abstract_setting_only": verdict})
        result = consumer.consume_semantic_evidence(current_inputs=inputs, evidence=e, semantic=semantic)
        assert result["adjudication_trace"]["status"] == "verified", result
        assert bool(result["remaining_blockers"]) == (verdict == "confirmed")


@pytest.mark.parametrize("mutation", ["unknown", "different_evidence", "different_weak_hook_location"])
def test_valid_batch_still_retains_every_unclosed_native_opening_finding(mutation):
    def change(report):
        if mutation == "unknown":
            report["findings"].append({"code": "native_unreviewed_critical", "severity": "critical", "message": "hard failure", "evidence": "exact source"})
        elif mutation == "different_evidence":
            report["findings"][0]["evidence"] = "another exact native location"
        else:
            report["findings"].append({**report["findings"][0], "evidence": "another exact native location"})
    inputs, evidence = child_fixture(codes=("weak_hook",), opening_codes=("weak_hook",), opening_mutator=change)
    result = consumer.consume_semantic_evidence(current_inputs=inputs, evidence=evidence, semantic=semantic)
    assert result["adjudication_trace"]["status"] == "verified", result
    assert any(row["gate"] == "opening" for row in result["remaining_blockers"])
    view = consumer.semantic_readiness_gates(current_inputs=inputs, evidence=evidence, semantic=semantic)
    assert view["opening_gate_passed"] is False


def test_real_batch_error_does_not_authorize_rejudging_an_individually_valid_rejection():
    inputs, artifact, workflow, _ = parent_fixture()
    inputs["original_report"]["ranking_report"]["findings"].append(f.finding("first_screen_pressure_missing"))
    inputs["original_report"]["short_v2_report"]["findings"] = deepcopy(inputs["original_report"]["ranking_report"]["findings"])
    payload = v4.prepare_review(**inputs)
    rows = [f.row(payload, i, verdict="confirmed") for i in payload["reviews_requested"]]
    rows[0]["evidence"] = [{"source": "full_text", "quote": "这句从未在原文出现。"}]
    evidence = paid(payload, rows, v4)
    saved = v4.validate_response(payload, evidence["completion"], current_binding=payload["binding"], retained_payloads=evidence["retained_payloads"])
    assert saved["error"] == "quote_not_in_scope"
    saved["called"] = True
    artifact["content"] = saved
    workflow["metadata_json"]["semantic_binding_sha256"] = payload["binding_sha256"]
    with pytest.raises(ValueError, match="valid_content_rejection"):
        prove(artifact, workflow, evidence)


def test_frozen_seven_bytes_match_witness_and_route_only():
    manifest = json.loads((FROZEN / "manifest.json").read_text())
    assert manifest == recovery.V4_SOURCES
    for key, expected in manifest.items():
        assert hashlib.sha256((FROZEN / "src/bestseller/services" / key).read_bytes()).hexdigest() == expected
    assert hashlib.sha256(v4.SYSTEM_PROMPT.encode()).hexdigest() == recovery.V4_SYSTEM_SHA256


def test_parent_real_protocol_error_reproduced_and_no_rows_reused():
    _, a, w, e = parent_fixture()
    before = deepcopy((a, w, e))
    proof = prove(a, w, e)
    assert set(proof["descriptor"]) == recovery.DESCRIPTOR_KEYS
    assert proof["row_errors"][0]["error"] == "quote_not_in_scope"
    assert proof["rows_reused"] is False
    assert (a, w, e) == before


@pytest.mark.parametrize("fault", ["raw", "error", "parent_wf", "parent_project", "body", "source_hash", "reviews", "physical", "extra_attempt", "valid_rejection", "nested"])
def test_parent_mutation_or_valid_rejection_fails_closed(fault):
    _, a, w, e = parent_fixture(valid_rejection=fault == "valid_rejection")
    if fault == "raw": a["content"]["raw_response"] += " "
    elif fault == "error": a["content"]["error"] = "pretend"
    elif fault == "parent_wf": w["id"] = str(uuid4())
    elif fault == "parent_project": w["project_id"] = str(uuid4())
    elif fault == "body": e["request_payload"]["full_text"] += "变"
    elif fault == "source_hash": e["request_payload"]["implementation_source_hashes"]["fanqie_short_semantic_review.py"] = "0" * 64
    elif fault == "reviews": a["content"]["reviews"][0]["verdict"] = "not_supported"
    elif fault == "physical": e["physical_files"][next(iter(e["physical_files"]))] = b'{"a":1,"a":2}'
    elif fault == "extra_attempt": e["native"]["attempts"].append(deepcopy(e["native"]["attempts"][0]))
    elif fault == "nested": a["content"]["protocol_recovery"] = {}
    with pytest.raises((ValueError, KeyError)):
        prove(a, w, e)


class TestParentReadOnly(unittest.IsolatedAsyncioTestCase):
    """Existing hermetic SQLite tables plus actual SELECT/files proof of a parent."""

    only_select = db_fixture.NativeAdapterTests.only_select
    asyncTearDown = db_fixture.NativeAdapterTests.asyncTearDown

    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="semantic-protocol-test-")
        self.base = Path(self.directory.name).resolve()
        self.env = patch.dict(os.environ, {"BESTSELLER__DATABASE__URL": f"sqlite+aiosqlite:///{self.base}/protocol_test.sqlite3"})
        self.env.start()
        url = db_fixture.selected_database_url()
        assert url.get_backend_name() == "sqlite" and Path(url.database).name == "protocol_test.sqlite3"
        self.remove_db_guard = lambda: None  # pytest conftest owns the connection guard.
        self.guards = [patch.object(socket.socket, "connect", side_effect=AssertionError("network forbidden")),
                       patch.object(socket.socket, "connect_ex", side_effect=AssertionError("network forbidden")),
                       patch.object(socket, "create_connection", side_effect=AssertionError("network forbidden")),
                       patch.object(semantic, "_native_complete_text", new=AsyncMock(side_effect=AssertionError("provider forbidden")))]
        for guard in self.guards: guard.start()
        self.engine = create_async_engine(url)
        models = (db_fixture.ProjectModel, db_fixture.WorkflowRunModel, db_fixture.PlanningArtifactVersionModel,
                  db_fixture.LlmRunModel, db_fixture.ExecutionBudgetModel, db_fixture.ExecutionOperationModel,
                  db_fixture.ExecutionAttemptModel)
        async with self.engine.begin() as connection:
            for model in models:
                ddl = str(CreateTable(model.__table__).compile(dialect=connection.dialect))
                await connection.exec_driver_sql(ddl.replace("::jsonb", ""))
        self.session = async_sessionmaker(self.engine, expire_on_commit=False)()
        self.settings = SimpleNamespace(artifact_store=SimpleNamespace(local_dir=str(self.base)))
        self.parent_inputs, self.parent, self.parent_wf, self.parent_evidence = parent_fixture()
        # Replace only the synthetic test DB; never uses project settings/PG.
        for model in (db_fixture.PlanningArtifactVersionModel, db_fixture.ExecutionAttemptModel,
                      db_fixture.ExecutionOperationModel, db_fixture.LlmRunModel, db_fixture.WorkflowRunModel,
                      db_fixture.ProjectModel):
            await self.session.execute(delete(model))
        self.project_id = UUID(self.parent["project_id"])
        self.project = db_fixture.ProjectModel(id=self.project_id, slug="protocol-test", title=self.parent_inputs["title"],
                                               genre="fixture", target_word_count=8000, target_chapters=1, metadata_json={})
        self.session.add(self.project)
        self.session.add(db_fixture.ExecutionBudgetModel(scope_id=self.parent_inputs["execution_scope_id"], max_calls=10,
                         max_tokens=100000, calls_used=1, tokens_charged=55, deadline_at=datetime.now(UTC), status="active", metadata_json={}))
        self.assembly = SimpleNamespace(full_text=self.parent_inputs["full_text"], manifest=self.parent_inputs["manifest"])
        w = deepcopy(self.parent_wf)
        for key in ("id", "project_id", "scope_id"): w[key] = UUID(w[key])
        self.session.add(db_fixture.WorkflowRunModel(**w))
        a = deepcopy(self.parent)
        for key in ("id", "project_id", "source_run_id"): a[key] = UUID(a[key])
        self.session.add(db_fixture.PlanningArtifactVersionModel(**a))
        native = self.parent_evidence["native"]
        run = deepcopy(native["llm_run"])
        for key in ("id", "project_id", "workflow_run_id"): run[key] = UUID(run[key])
        self.session.add(db_fixture.LlmRunModel(**run))
        now = datetime.now(UTC)
        for row in (native["operation"], *native["transport_results"]):
            self.session.add(db_fixture.ExecutionOperationModel(**row, owner="fixture", lease_until=now))
        self.session.add(db_fixture.ExecutionAttemptModel(**native["attempts"][0], created_at=now))
        for ref, data in self.parent_evidence["physical_files"].items():
            path = self.base / ref
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        await self.session.commit()

    # The base tests target its own unrelated fixture. This class only inherits
    # setup/SELECT guard helpers, not those test methods (removed below).
    async def test_parent_public_helper_select_only(self):
        with self.only_select():
            desc = await adapter.build_protocol_recovery_parent(self.session, self.settings, self.project,
                                                               self.assembly, self.parent["id"])
        assert desc == recovery.descriptor(self.parent)

    async def test_second_workflow_even_without_child_artifact_blocked(self):
        desc = recovery.descriptor(self.parent)
        self.session.add(db_fixture.WorkflowRunModel(id=uuid4(), project_id=self.project_id,
            workflow_type="fanqie_whole_semantic_review", status="running", metadata_json={"protocol_recovery": desc}))
        await self.session.commit()
        with self.only_select(), pytest.raises(ValueError, match="second_recovery_workflow"):
            await adapter.build_protocol_recovery_parent(self.session, self.settings, self.project,
                                                        self.assembly, self.parent["id"])

    async def test_unexplained_current_failed_artifact_blocked(self):
        a = deepcopy(self.parent)
        a.update(id=uuid4(), version_no=2)
        a["project_id"] = self.project_id
        a["source_run_id"] = UUID(a["source_run_id"])
        self.session.add(db_fixture.PlanningArtifactVersionModel(**a))
        await self.session.commit()
        with self.only_select(), pytest.raises(ValueError, match="extra_current_artifact"):
            await adapter.build_protocol_recovery_parent(self.session, self.settings, self.project,
                                                        self.assembly, self.parent["id"])

    async def install_child(self):
        self.child_inputs, evidence = child_fixture()
        chain = evidence["protocol_recovery"]
        for model in (db_fixture.PlanningArtifactVersionModel, db_fixture.ExecutionAttemptModel,
                      db_fixture.ExecutionOperationModel, db_fixture.LlmRunModel, db_fixture.WorkflowRunModel,
                      db_fixture.ProjectModel):
            await self.session.execute(delete(model))
        self.project_id = UUID(self.child_inputs["project_id"])
        self.project = db_fixture.ProjectModel(id=self.project_id, slug="child-test", title=self.child_inputs["title"],
            genre="fixture", target_word_count=8000, target_chapters=1,
            metadata_json={"contracts": deepcopy(self.child_inputs["approved_contract"])})
        self.session.add(self.project)
        self.assembly = SimpleNamespace(full_text=self.child_inputs["full_text"], manifest=deepcopy(self.child_inputs["manifest"]))
        for source in (chain["parent_artifact"], chain["child_artifact"]):
            row = deepcopy(source)
            for key in ("id", "project_id", "source_run_id"): row[key] = UUID(row[key])
            self.session.add(db_fixture.PlanningArtifactVersionModel(**row))
        for source in (chain["parent_workflow"], chain["child_workflow"]):
            row = deepcopy(source)
            for key in ("id", "project_id", "scope_id"): row[key] = UUID(row[key])
            self.session.add(db_fixture.WorkflowRunModel(**row))
        now = datetime.now(UTC)
        for source in (chain["parent_evidence"], evidence):
            native = source["native"]
            row = deepcopy(native["llm_run"])
            for key in ("id", "project_id", "workflow_run_id"): row[key] = UUID(row[key])
            self.session.add(db_fixture.LlmRunModel(**row))
            for operation in (native["operation"], *native["transport_results"]):
                self.session.add(db_fixture.ExecutionOperationModel(**operation, owner="fixture", lease_until=now))
            self.session.add(db_fixture.ExecutionAttemptModel(**native["attempts"][0], created_at=now))
            for ref, data in source["physical_files"].items():
                path = self.base / ref
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
        await self.session.execute(update(db_fixture.ExecutionBudgetModel).values(calls_used=2, tokens_charged=110))
        await self.session.commit()
        return evidence

    async def current_factory(self, session, project, assembly, source_run_id):
        from sqlalchemy import select
        metadata = await session.scalar(select(db_fixture.ProjectModel.metadata_json).where(db_fixture.ProjectModel.id == project.id))
        values = deepcopy(self.child_inputs)
        # The factory independently reads contracts and current source/grade;
        # adapter must supply the actual parent, not a stored candidate payload.
        values.update(approved_contract=metadata["contracts"], full_text=assembly.full_text,
                      manifest=assembly.manifest, title=project.title, workflow_run_id=str(source_run_id))
        values.pop("recovery_parent")
        return values

    async def load_child(self):
        with self.only_select():
            return await adapter.load_current_semantic_context(self.session, self.settings, project=self.project,
                assembly=self.assembly, title=self.project.title, original_report=self.child_inputs["original_report"],
                current_inputs_factory=self.current_factory, semantic=semantic, consumer=consumer)

    async def test_adapter_both_paid_roots_readback_and_builder_reconsume(self):
        evidence = await self.install_child()
        loaded = await self.load_child()
        assert loaded["derived_trace"]["status"] == "verified"
        assert loaded["derived_trace"]["protocol_recovery"]["descriptor"] == evidence["protocol_recovery"]["descriptor"]
        assert loaded["current_inputs"]["recovery_parent"] == evidence["protocol_recovery"]["descriptor"]
        assert consumer.semantic_readiness_gates(current_inputs=loaded["current_inputs"], evidence=loaded["evidence"], semantic=semantic)["opening_gate_passed"]
        with self.only_select(), pytest.raises(ValueError, match="second_recovery"):
            await adapter.build_protocol_recovery_parent(self.session, self.settings, self.project,
                self.assembly, evidence["protocol_recovery"]["parent_artifact"]["id"])

    async def test_adapter_missing_parent_and_extra_failed_never_fall_back(self):
        evidence = await self.install_child()
        pid = UUID(evidence["protocol_recovery"]["parent_artifact"]["id"])
        await self.session.execute(delete(db_fixture.PlanningArtifactVersionModel).where(db_fixture.PlanningArtifactVersionModel.id == pid))
        await self.session.commit()
        with pytest.raises(ValueError, match="protocol_parent_inventory"):
            await self.load_child()

    async def test_adapter_parent_physical_missing_not_recovered_by_new_success(self):
        evidence = await self.install_child()
        ref = next(iter(evidence["protocol_recovery"]["parent_evidence"]["physical_files"]))
        (self.base / ref).unlink()
        with pytest.raises(OSError):
            await self.load_child()


