"""Synthetic completed rows/paid bytes in the actual native operator schema.

Historical story/contracts are read-only fixtures. No real new title adoption,
provider receipt or literary verdict is asserted. Only pure native helpers run.
"""
from copy import deepcopy
import base64
import hashlib
import importlib.util
import json
import socket
import unittest
from unittest.mock import patch
from uuid import uuid4

from test_fanqie_short_semantic_review import HERE, stage, finding


def synthetic_native_rows():
    """Use the other stage's fixture factories, never its runtime or test suite."""
    path = HERE.parent / "title-operator-stage/test_title_operator.py"
    spec = importlib.util.spec_from_file_location("stage4_native_schema_fixture", path)
    fixture = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixture)
    consumer = stage._native_title_consumer()
    op = consumer.op
    snapshot = fixture.fixture_snapshot()
    plan = op.prepare(snapshot, snapshot["manifest"], fixture.native_messages)
    # Match the historical factory's explicitly synthetic effective receipt,
    # while deriving source identity/code hash from the ACTUAL runtime producer.
    plan["effective_input_hash"] = op.fingerprint({"synthetic_effective_request": plan["request"]})
    plan["effective_max_tokens"] = 4096  # Fixture cap, never a live M3.1 claim.
    plan["request"]["metadata"]["prepared_effective_input"] = {"schema": "effective-input.v1", "hash": plan["effective_input_hash"]}
    plan["plan_sha256"] = op.fingerprint({k: v for k, v in plan.items() if k != "plan_sha256"})
    completion, docs, ledger = fixture.paid_fixture(plan)
    selection = op.choose_title(plan, completion, consumer.finalize_revised_title, consumer.build_platform_title_workflow)
    title, record, workflow = selection["title"], selection["record"], selection["workflow"]
    audit = {"schema": op.SCHEMA, "plan": plan, "completion": completion, "retained_payloads": docs, "ledger": ledger}
    audit["audit_sha256"] = op.fingerprint(audit)
    before = plan["binding"]["manifest"]
    old = plan["literal_source"]["old_native_title_metadata"]
    wf, run = plan["workflow_run_id"], completion["llm_run_id"]
    L = {"schema": op.SCHEMA, "project_id": op.PROJECT_ID, "original_task_id": op.TASK_ID,
         "workflow_run_id": wf, "llm_run_id": run, "previous_title": plan["binding"]["old_title"], "adopted_title": title,
         "source_manifest": deepcopy(before), "canonical_id": before["canonical_id"], "canonical_hash": before["canonical_hash"],
         "book_design_snapshot_hash": old["book_design_snapshot_hash"], "operation_key": plan["operation_key"],
         "effective_input_hash": plan["effective_input_hash"], "plan_sha256": plan["plan_sha256"],
         "paid_audit_sha256": audit["audit_sha256"], "literal_offset": selection["literal_offset"],
         "title_evaluation": selection["title_evaluation"], "packaging_created": False, "historical_promise_fulfillment_granted": False}
    metadata = deepcopy(old)
    history = deepcopy(old.get("title_revision_history", []))
    history.append({"schema": op.SCHEMA, "workflow_run_id": wf, "previous_title": before["project"]["title"],
                    "previous_native_metadata": {k: deepcopy(old[k]) for k in (
                        "title_revision_status", "title_workflow_primary", "title_workflow", "title_candidates", "writing_profile", "title_repair_lineage") if k in old},
                    "audit_sha256": audit["audit_sha256"]})
    metadata.update(title_repair_lineage=deepcopy(L), title_revision_history=history,
                    title_workflow_primary=deepcopy(record), title_workflow=deepcopy(workflow), title_candidates=deepcopy(workflow["candidates"]))
    market = metadata["writing_profile"]["market"]
    market["title_workflow_primary"] = deepcopy(record)
    market["title_workflow_summary"] = {"schema_version": workflow["schema_version"], "candidate_policy": workflow["candidate_policy"],
        "candidate_count": len(workflow["candidates"]), "platform_count": workflow["platform_count"], "recommended_primary_title": deepcopy(record)}
    metadata["title_revision_status"] = {"previous_title": before["project"]["title"], "adopted_title": title, "was_revised": True,
        "llm_run_id": run, "quality_status": "unchanged_or_revised", "retryable": False, "readability_issue": "",
        "title_evaluation": deepcopy(selection["title_evaluation"]), "native_title_lineage": deepcopy(L)}
    evidence = {"schema": consumer.SCHEMA, "project": {"id": op.PROJECT_ID, "title": title, "metadata_json": metadata},
        "workflow": {"id": wf, "project_id": op.PROJECT_ID, "workflow_type": "title_repair_operator", "status": "completed",
            "scope_type": "project", "scope_id": op.PROJECT_ID, "current_step": "title_repair_adopted", "error_message": None,
            "metadata_json": {"schema": op.SCHEMA, "plan_sha256": plan["plan_sha256"], "execution_scope_id": op.SCOPE,
                "original_task_id": op.TASK_ID, "witness": fixture.witness(plan), "literal_source": deepcopy(plan["literal_source"]), "title_repair_lineage": deepcopy(L)}},
        "step": {"id": str(uuid4()), "workflow_run_id": wf, "step_name": "story_grounded_title_revision", "step_order": 1,
            "status": "completed", "error_message": None, "input_ref": {"plan_sha256": plan["plan_sha256"], "source_manifest": deepcopy(before),
                "previous_title_revision_status": deepcopy(old.get("title_revision_status"))},
            "output_ref": {**deepcopy(L), "title_record": deepcopy(record), "rejected_candidates": deepcopy(selection["rejected_candidates"])}},
        "llm_run": {"id": run, "project_id": op.PROJECT_ID, "workflow_run_id": wf, "logical_role": "editor",
            "prompt_template": "title_story_grounded_revision", "prompt_version": "1.0", "prompt_hash": plan["effective_input_hash"],
            **{k: completion[k] for k in ("provider", "model_name", "finish_reason", "input_tokens", "output_tokens")},
            "request_payload_ref": completion["metadata"]["prompt_payload_receipt"]["request_ref"],
            "response_payload_ref": completion["metadata"]["prompt_payload_receipt"]["response_ref"], "metadata_json": deepcopy(completion["metadata"])},
        "paid_return": audit, "ledger": deepcopy(ledger), "physical_files": {}}
    encode_documents(evidence)
    native = json.loads((HERE.parent / "original-native-creative-approval-readback.json").read_text())
    contract = {**native["selected_contracts"], "approval_evidence": native["native_approval_evidence"]}
    current = deepcopy(before)
    current["project"]["title"] = title
    context = {"project_id": op.PROJECT_ID, "title": title, "full_text": snapshot["full_text"], "manifest": current, "approved_contract": contract}
    return evidence, context


def encode_documents(evidence):
    for kind, doc in evidence["paid_return"]["retained_payloads"].items():
        raw = (json.dumps(doc, ensure_ascii=False, indent=2) + "\n").encode()
        evidence["physical_files"][kind] = {"ref": evidence["paid_return"]["completion"]["metadata"]["prompt_payload_receipt"][kind + "_ref"],
            "sha256": hashlib.sha256(raw).hexdigest(), "bytes_b64": base64.b64encode(raw).decode()}


def rehash_audit(evidence):
    audit = evidence["paid_return"]
    audit["audit_sha256"] = stage.fingerprint({k: v for k, v in audit.items() if k != "audit_sha256"})


def fixture_lineages(e):
    return (e["project"]["metadata_json"]["title_repair_lineage"],
            e["project"]["metadata_json"]["title_revision_status"]["native_title_lineage"],
            e["workflow"]["metadata_json"]["title_repair_lineage"], e["step"]["output_ref"])


class NativePublicationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.guards = [patch.object(owner, name, side_effect=AssertionError("offline only"))
                      for owner, name in ((socket.socket, "connect"), (socket.socket, "connect_ex"), (socket, "create_connection"))]
        for guard in cls.guards:
            guard.start()
        cls.evidence, cls.context = synthetic_native_rows()

    @classmethod
    def tearDownClass(cls):
        for guard in cls.guards:
            guard.stop()

    def fresh(self):
        return deepcopy(self.evidence), deepcopy(self.context)

    def mapping(self, evidence, context):
        return stage.map_native_publishing_title_evidence(evidence, **context)

    def test_actual_native_shapes_map_without_rewriting_inputs_or_granting_semantics(self):
        e, c = self.fresh()
        before = stage.fingerprint([e, c])
        receipt = self.mapping(e, c)
        self.assertEqual(receipt["route"], "native_publishing_title_revision")
        self.assertFalse(receipt["semantic_verdict_granted"])
        self.assertFalse(receipt["historical_promise_fulfillment_granted"])
        self.assertEqual(stage.fingerprint([e, c]), before)

    def test_native_pretelemetry_null_run_ids_require_unflushed_cache_not_rebinding(self):
        e, c = self.fresh()
        for result in (e["ledger"]["root"]["result_json"], e["ledger"]["transports"][0]["result"]):
            result["llm_run_id"] = None
            result["metadata"]["llm_run_flushed"] = False
        e["paid_return"]["ledger"] = deepcopy(e["ledger"])
        rehash_audit(e)
        # Rebind only the synthetic fixture's downstream audit reference.
        new_hash = e["paid_return"]["audit_sha256"]
        for L in (e["project"]["metadata_json"]["title_repair_lineage"],
                  e["project"]["metadata_json"]["title_revision_status"]["native_title_lineage"],
                  e["workflow"]["metadata_json"]["title_repair_lineage"], e["step"]["output_ref"]):
            L["paid_audit_sha256"] = new_hash
        e["project"]["metadata_json"]["title_revision_history"][-1]["audit_sha256"] = new_hash
        self.mapping(e, c)
        e["ledger"]["root"]["result_json"]["metadata"]["llm_run_flushed"] = True
        e["paid_return"]["ledger"] = deepcopy(e["ledger"])
        rehash_audit(e)
        with self.assertRaisesRegex(stage.ReviewInputError, "root_transport_run_conflict"):
            self.mapping(e, c)

    def test_native_mapping_integrates_literal_title_only_while_full_evidence_stays_bound_locally(self):
        e, c = self.fresh()
        c["approved_contract"]["approval_evidence"]["native_publishing_title_evidence"] = e
        f = finding("title_click_contract_missing", c["full_text"])
        p = stage.prepare_review(**c, protagonist="我", workflow_run_id=str(uuid4()),
            current_scope="synthetic-title-lineage-only", execution_scope_id="synthetic-no-dispatch",
            operation_namespace="synthetic-publication-mapping", original_report={
                "passed": False, "ranking_report": {"findings": [f]}, "short_v2_report": {"findings": []}})
        wire = stage._user_prompt(p)
        doc = json.loads(wire)
        self.assertTrue(p["title_provenance_verified"])
        self.assertEqual(doc["title_provenance_route"], "native_publishing_title_revision")
        self.assertEqual(doc["selected_literary_contracts"][p["title_source_path"]], c["title"])
        for token in ('"bytes_b64"', '"paid_return"', '"physical_files"', '"native_publishing_title_receipt"'):
            self.assertNotIn(token, wire)
        selected = doc["selected_literary_contracts"]
        self.assertEqual([key for key in selected if "native_publishing_title_evidence" in key], [p["title_source_path"]])
        self.assertEqual(p["binding"]["approved_contract_sha256"], stage.fingerprint(c["approved_contract"]))
        self.assertEqual(p["title_revision_status"]["blocked"], "title_revision_call_failed")
        self.assertNotEqual(c["approved_contract"]["approval_evidence"]["canonical_snapshot"]["content"]["title"], p["title"])
        result = stage.validate_response(p, {}, current_binding=p["binding"], retained_payloads={})
        self.assertTrue(all(r["verdict"] == "uncertain" for r in result["reviews"]))

    def test_completed_workflow_step_and_actual_project_are_required_not_approved_flags(self):
        for field, value in (("status", "running"), ("workflow_type", "conception_initial"), ("scope_id", str(uuid4())), ("current_step", "pending")):
            e, c = self.fresh()
            e["workflow"][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(stage.ReviewInputError, "completed_title_workflow"):
                self.mapping(e, c)
        for section, key, value in (("project", "title", "任意项目标题不能当采用名"), ("step", "status", "failed"), ("step", "step_order", True)):
            e, c = self.fresh()
            e[section][key] = value
            with self.subTest(section=section, key=key), self.assertRaises(stage.ReviewInputError):
                self.mapping(e, c)
        with self.assertRaisesRegex(stage.ReviewInputError, "native_evidence_schema"):
            self.mapping({"approved": True}, self.context)

    def test_selected_title_uses_unicode_literal_offset_not_bytes_or_metadata_claims(self):
        e, c = self.fresh()
        raw = "1. 《" + c["title"] + "》"
        audit = e["paid_return"]
        audit["completion"]["content"] = raw
        audit["retained_payloads"]["response"]["content"] = {"text": raw, "truncated": False, "original_chars": len(raw)}
        for result in (audit["ledger"]["root"]["result_json"], audit["ledger"]["transports"][0]["result"]):
            result["content"] = raw
        e["ledger"] = deepcopy(audit["ledger"])
        encode_documents(e)
        rehash_audit(e)
        for L in fixture_lineages(e):
            L["paid_audit_sha256"] = audit["audit_sha256"]
            L["literal_offset"] = raw.index(c["title"])
        e["project"]["metadata_json"]["title_revision_history"][-1]["audit_sha256"] = audit["audit_sha256"]
        self.mapping(e, c)
        for L in fixture_lineages(e):
            L["literal_offset"] = len("1. 《".encode())
        with self.assertRaisesRegex(stage.ReviewInputError, "literal_adopted_title"):
            self.mapping(e, c)

    def test_physical_original_bytes_sha_duplicate_keys_and_parsed_values_are_strict(self):
        for kind in ("request", "response"):
            for mutation in ("missing", "base64", "sha", "json", "duplicate"):
                e, c = self.fresh()
                file = e["physical_files"][kind]
                if mutation == "missing":
                    del e["physical_files"][kind]
                elif mutation == "base64":
                    file["bytes_b64"] = "not base64!"
                elif mutation == "sha":
                    file["sha256"] = "0" * 64
                else:
                    raw = b'{"kind":"other"}' if mutation == "json" else b'{"kind":"request","kind":"response"}'
                    file.update(sha256=hashlib.sha256(raw).hexdigest(), bytes_b64=base64.b64encode(raw).decode())
                with self.subTest(kind=kind, mutation=mutation), self.assertRaises(stage.ReviewInputError):
                    self.mapping(e, c)

    def test_paid_fallback_length_unsettled_extra_attempt_and_transport_conflicts_stop_even_rehashed(self):
        for mutation in ("fallback", "length", "root", "attempt", "extra_attempt", "extra_transport", "missing_transport", "tokens", "content", "origin"):
            e, c = self.fresh()
            audit = e["paid_return"]
            if mutation in {"fallback", "length"}:
                audit["completion"]["fallback_used" if mutation == "fallback" else "finish_reason"] = True if mutation == "fallback" else "length"
            elif mutation == "root":
                audit["ledger"]["root"]["status"] = "unknown"
            elif mutation == "attempt":
                audit["ledger"]["attempts"][0]["status"] = "reserved"
            elif mutation in {"extra_attempt", "extra_transport"}:
                key = "attempts" if mutation == "extra_attempt" else "transports"
                audit["ledger"][key].append(deepcopy(audit["ledger"][key][0]))
            elif mutation == "missing_transport":
                audit["ledger"]["transports"].clear()
            elif mutation == "tokens":
                audit["ledger"]["attempts"][0]["actual_tokens"] += 1
            elif mutation == "content":
                audit["ledger"]["transports"][0]["result"]["content"] += "伪造"
            else:
                audit["completion"]["metadata"]["paid_call_origin"]["workflow_run_id"] = str(uuid4())
            e["ledger"] = deepcopy(audit["ledger"])
            rehash_audit(e)
            with self.subTest(mutation=mutation), self.assertRaisesRegex(stage.ReviewInputError, "native_operator:"):
                self.mapping(e, c)

    def test_retained_untruncated_route_model_input_and_original_llm_run_must_match(self):
        for mutation in ("truncated", "text", "model", "max_tokens", "run_role", "run_hash", "run_tokens", "run_origin", "run_ref", "run_id"):
            e, c = self.fresh()
            doc = e["paid_return"]["retained_payloads"]["request"]
            if mutation == "truncated":
                doc["messages"]["user"]["truncated"] = True
            elif mutation == "text":
                doc["messages"]["user"]["text"] = doc["messages"]["user"]["text"][:-1]
            elif mutation in {"model", "max_tokens"}:
                doc["effective"][mutation] = "different" if mutation == "model" else 1
            elif mutation == "run_role":
                e["llm_run"]["logical_role"] = "critic"
            elif mutation == "run_hash":
                e["llm_run"]["prompt_hash"] = "0" * 64
            elif mutation == "run_tokens":
                e["llm_run"]["output_tokens"] += 1
            elif mutation == "run_origin":
                e["llm_run"]["metadata_json"]["paid_call_origin"]["project_id"] = str(uuid4())
            elif mutation == "run_ref":
                e["llm_run"]["request_payload_ref"] = "other"
            else:
                e["llm_run"]["id"] = str(uuid4())
            encode_documents(e)
            rehash_audit(e)
            with self.subTest(mutation=mutation), self.assertRaises(stage.ReviewInputError):
                self.mapping(e, c)

    def test_current_title_status_workflow_primaries_history_and_literal_offset_cannot_drift(self):
        for mutation in ("blocked", "retryable", "provisional", "top_primary", "market_primary", "recommendation", "evaluation", "history", "offset", "deadline_grant"):
            e, c = self.fresh()
            m = e["project"]["metadata_json"]
            if mutation in {"blocked", "retryable"}:
                m["title_revision_status"][mutation] = True
            elif mutation == "provisional":
                m["title_workflow_primary"]["provisional"] = True
            elif mutation == "top_primary":
                m["title_workflow_primary"]["title"] = "另一个标题"
            elif mutation == "market_primary":
                m["writing_profile"]["market"]["title_workflow_primary"]["title"] = "另一个标题"
            elif mutation == "recommendation":
                m["title_workflow"]["recommended_primary_title"]["title"] = "另一个标题"
            elif mutation == "evaluation":
                m["title_workflow"]["candidate_evaluations"][c["title"]]["decision"] = "revise"
            elif mutation == "history":
                m["title_revision_history"][-1]["previous_native_metadata"]["title_revision_status"] = {"approved": True}
            elif mutation == "offset":
                m["title_repair_lineage"]["literal_offset"] = True
            else:
                m["title_repair_lineage"]["historical_promise_fulfillment_granted"] = True
            with self.subTest(mutation=mutation), self.assertRaises(stage.ReviewInputError):
                self.mapping(e, c)

    def test_current_assembly_sources_canonical_and_design_must_remain_exact(self):
        for mutation in ("body", "order", "source", "canon", "design", "project_setting"):
            e, c = self.fresh()
            if mutation == "body":
                c["full_text"] += "新增正文"
            elif mutation == "order":
                c["manifest"]["sources"].reverse()
            elif mutation == "source":
                c["manifest"]["sources"][1]["draft_id"] = str(uuid4())
            elif mutation == "canon":
                c["approved_contract"]["approval_evidence"]["canonical_snapshot"]["content"]["title"] = c["title"]
            elif mutation == "design":
                c["approved_contract"]["book_design_snapshot"]["tone"] += "篡改"
            else:
                c["manifest"]["project"]["target_word_count"] += 1
            with self.subTest(mutation=mutation), self.assertRaises(stage.ReviewInputError):
                self.mapping(e, c)


if __name__ == "__main__":
    unittest.main()
