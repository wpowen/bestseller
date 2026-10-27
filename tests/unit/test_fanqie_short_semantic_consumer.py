"""Synthetic consistency tests only; never real literary/production acceptance."""

from copy import deepcopy
import hashlib
import json
import socket
import unittest
from unittest.mock import patch

import test_fanqie_short_semantic_review as f
from bestseller.services.fanqie_short_semantic_consumer import consume_semantic_evidence, semantic_readiness_gates


def build(
    codes=("opening_pressure_missing",), verdicts=None, extra=None, report_overrides=None, text=None
):
    # Native shape fixtures, with enough body length and explicit whole flags.
    inputs = f.kwargs(codes, text=text or f.SOURCE + "\n" + "旧物记录。" * 100)
    inputs["manifest"]["sources"][0]["promotion_state"] = "promoted"
    report = inputs["original_report"]
    if extra:
        report["ranking_report"]["findings"].append(deepcopy(extra))
    report.update(
        opening_passed=True,
        ranking_passed=False,
        short_v2_passed=False,
        notes=["原审查不变"],
        thresholds={"protected_threshold": 3},
    )
    report.update(report_overrides or {})
    # stage.5 requires the full native opening inventory, independently of
    # ranking/v2. These synthetic flags never stand in for real chapter review.
    report["opening_report"] = {
        "passed": report["opening_passed"],
        "unlock_zone_words": max(50, int(len(inputs["full_text"].strip()) * .30)),
        "total_words": len(inputs["full_text"].strip()),
        "unlock_ratio": .30,
        "findings": [] if report["opening_passed"] else [{
            "code": "synthetic_opaque_opening_failure", "severity": "critical",
            "message": "Synthetic native opening failure remains blocking", "evidence": "original synthetic source",
        }],
    }
    payload = f.stage.prepare_review(**inputs)
    rows = []
    for item in payload["reviews_requested"]:
        row = f.title_row(payload, item) if item["target"] == "title" else f.row(payload, item)
        if item["code"] in {
            "opening_fast_payoff_missing",
            "first_screen_feedback_missing",
            "unlock_payoff_missing",
            "payoff_density_too_low",
        }:
            row["events"] = f.events(payload)[: item.get("expected_payoff_count", 1)]
        verdict = (verdicts or {}).get(item["code"], "not_supported")
        if verdict != "not_supported":
            row = f.row(payload, item, verdict=verdict)
        rows.append(row)
    completion, retained = f.response(payload, rows)
    completion.update(input_tokens=30, output_tokens=25)
    aid = "physical-original-send"
    completion["metadata"]["execution_receipt"]["attempts"][0].update(
        attempt_id=aid,
        status="completed",
        reserved_tokens=10000,
        wire_body_hash=hashlib.sha256(b"synthetic physical request bytes").hexdigest(),
    )
    meta = completion["metadata"]
    receipt = meta["execution_receipt"]
    h = meta["effective_input_receipt"]["hash"]
    op = dict(
        key=receipt["operation_key"],
        scope_id=payload["execution_scope_id"],
        input_hash=h,
        status="completed",
        result_json=deepcopy(completion),
    )
    attempt = dict(
        id=aid,
        operation_key=op["key"],
        scope_id=op["scope_id"],
        input_hash=h,
        model_name=completion["model_name"],
        status="completed",
        actual_tokens=55,
        reserved_tokens=10000,
    )
    run = {
        k: completion[k]
        for k in ("provider", "model_name", "finish_reason", "input_tokens", "output_tokens")
    }
    run.update(
        id=completion["llm_run_id"],
        project_id=payload["project_id"],
        workflow_run_id=payload["workflow_run_id"],
        logical_role="critic",
        prompt_template="fanqie_short_semantic_review",
        prompt_version=f.stage.PROMPT_VERSION,
        prompt_hash=h,
        metadata_json=deepcopy(meta),
    )
    files = {}
    for kind in ("request", "response"):
        ref = meta["prompt_payload_receipt"][kind + "_ref"]
        run[kind + "_payload_ref"] = ref
        files[ref] = json.dumps(retained[kind], ensure_ascii=False).encode()
    transport = dict(
        key=hashlib.sha256(
            ("transport-result.v1\n" + op["scope_id"] + "\n" + aid).encode()
        ).hexdigest(),
        scope_id=op["scope_id"],
        input_hash=h,
        status="transport_result",
        result_json=dict(
            schema="transport-result.v1",
            operation_key=op["key"],
            attempt_id=aid,
            input_hash=h,
            result=deepcopy(completion),
        ),
    )
    return inputs, dict(
        request_payload=payload,
        completion=completion,
        retained_payloads=retained,
        physical_files=files,
        native=dict(operation=op, attempts=[attempt], llm_run=run, transport_results=[transport]),
    )


def consume(inputs, evidence):
    return consume_semantic_evidence(current_inputs=inputs, evidence=evidence, semantic=f.stage)


class ConsumerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from sqlalchemy.engine import Engine

        cls.guards = [
            patch.object(socket.socket, "connect", side_effect=AssertionError("network forbidden")),
            patch.object(
                socket.socket, "connect_ex", side_effect=AssertionError("network forbidden")
            ),
            patch.object(
                socket, "create_connection", side_effect=AssertionError("network forbidden")
            ),
            patch.object(Engine, "raw_connection", side_effect=AssertionError("DB forbidden")),
            patch.object(
                f.stage, "_native_complete_text", side_effect=AssertionError("provider forbidden")
            ),
        ]
        for guard in cls.guards:
            guard.start()

    @classmethod
    def tearDownClass(cls):
        for guard in reversed(cls.guards):
            guard.stop()

    def test_exact_evidence_only_derivation_preserves_original_everywhere(self):
        inputs, evidence = build()
        before = deepcopy((inputs, evidence))
        result = consume(inputs, evidence)
        self.assertEqual(result["adjudication_trace"]["status"], "verified", result)
        self.assertEqual(result["remaining_blockers"], [])
        self.assertEqual(
            {r["gate"] for r in result["adjudicated_nonblocking"]},
            {"ranking_report", "short_v2_report"},
        )
        self.assertEqual(result["original_report"], inputs["original_report"])
        self.assertFalse(result["original_report"]["passed"])
        self.assertFalse(result["model_grade_asserted"])
        self.assertNotIn("passed", result)
        self.assertNotIn("ready_for_upload", result)
        self.assertEqual((inputs, evidence), before)
        self.assertEqual(consume(inputs, evidence), result)

    def test_all_seven_exact_codes_with_real_v4_validator(self):
        for code in (
            "opening_pressure_missing",
            "opening_fast_payoff_missing",
            "unlock_payoff_missing",
            "title_click_contract_missing",
            "first_screen_pressure_missing",
            "first_screen_feedback_missing",
            "payoff_density_too_low",
        ):
            with self.subTest(code=code):
                inputs, evidence = build((code,))
                result = consume(inputs, evidence)
                self.assertEqual(result["adjudication_trace"]["status"], "verified", result)
                self.assertEqual(result["remaining_blockers"], [])

    def test_confirmed_uncertain_and_excluded_remain_gate_exact(self):
        extra = f.finding("continuity_mismatch")
        inputs, evidence = build(
            ("opening_pressure_missing", "first_screen_pressure_missing", "unlock_payoff_missing"),
            {"first_screen_pressure_missing": "confirmed", "unlock_payoff_missing": "uncertain"},
            extra,
        )
        evidence.update(verified=True, reviews=[{"verdict": "not_supported"}])
        result = consume(inputs, evidence)
        self.assertEqual(result["adjudication_trace"]["status"], "verified", result)
        self.assertEqual(
            {b["code"] for b in result["remaining_blockers"]},
            {"first_screen_pressure_missing", "unlock_payoff_missing", "continuity_mismatch"},
        )

    def test_nonseven_code_even_not_supported_never_removed(self):
        inputs, evidence = build(("opening_action_missing",))
        result = consume(inputs, evidence)
        self.assertEqual(result["adjudication_trace"]["status"], "verified", result)
        self.assertEqual(len(result["remaining_blockers"]), 2)

    def test_excluded_taboo_length_and_opening_remain(self):
        inputs, evidence = build(
            report_overrides={
                "taboo_signals": ["contains_review_begging"],
                "opening_passed": False,
            },
            text=f.SOURCE,
        )
        result = consume(inputs, evidence)
        self.assertEqual(result["adjudication_trace"]["status"], "verified", result)
        self.assertEqual(
            {b["gate"] for b in result["remaining_blockers"]}, {"taboo", "length", "opening"}
        )
        inputs, evidence = build(extra=f.finding("word_count_too_low"))
        result = consume(inputs, evidence)
        self.assertEqual([b["code"] for b in result["remaining_blockers"]], ["word_count_too_low"])

    def test_stale_any_bound_input_rejects_batch(self):
        mutations = {
            "title": lambda x: x.update(title=x["title"] + "新"),
            "body": lambda x: x.update(full_text=x["full_text"] + "新"),
            "manifest": lambda x: x["manifest"]["sources"][0].update(draft_id=str(f.uuid4())),
            "canonical": lambda x: x["approved_contract"]["approval_evidence"].update(
                creation_canonical_snapshot_hash="0" * 64
            ),
            "design": lambda x: x["approved_contract"]["book_design_snapshot"].update(
                reader_promise="changed"
            ),
            "scope": lambda x: x.update(current_scope="new-scope"),
            "execution_scope": lambda x: x.update(execution_scope_id="new-paid-scope"),
            "workflow": lambda x: x.update(workflow_run_id=str(f.uuid4())),
            "original_report": lambda x: x["original_report"]["thresholds"].update(
                protected_threshold=4
            ),
        }
        for name, change in mutations.items():
            with self.subTest(name=name):
                inputs, evidence = build()
                change(inputs)
                try:
                    result = consume(inputs, evidence)
                except ValueError:
                    continue  # Malformed current readback is a hard block too.
                self.assertEqual(result["adjudication_trace"]["status"], "unverified")
                self.assertFalse(result["adjudicated_nonblocking"])

    def test_readiness_builder_and_readback_share_reverified_view(self):
        inputs, evidence = build()
        args = dict(current_inputs=inputs, evidence=evidence, semantic=f.stage)
        publisher = semantic_readiness_gates(**args)
        readback = semantic_readiness_gates(**args)
        self.assertEqual(publisher, readback)
        self.assertTrue(publisher["ranking_gate_passed"])
        self.assertFalse(publisher["semantic_derived_review"]["original_report"]["ranking_passed"])
        evidence["stored_verified_derived_review"] = publisher
        evidence["native"]["operation"]["status"] = "unknown"
        with self.assertRaisesRegex(ValueError, "semantic_evidence_unverified"):
            semantic_readiness_gates(**args)

    def test_native_physical_counterexamples(self):
        for fault in (
            "missing_native",
            "unknown_root",
            "stopped_root",
            "unknown_send",
            "unpaid",
            "extra_send",
            "wire_missing",
            "owner",
            "role",
            "version",
            "hash",
            "run_id",
            "restored_run",
            "paid_content",
            "transport",
            "missing_file",
            "file_tamper",
            "duplicate_file_key",
            "fake_stored_verified",
            "candidate_source",
        ):
            with self.subTest(fault=fault):
                inputs, evidence = build()
                n = evidence["native"]
                if fault == "missing_native":
                    evidence.pop("native")
                elif fault in {"unknown_root", "stopped_root"}:
                    n["operation"]["status"] = fault.split("_")[0]
                elif fault == "unknown_send":
                    n["attempts"][0]["status"] = "unknown"
                elif fault == "unpaid":
                    n["attempts"][0]["actual_tokens"] = None
                elif fault == "extra_send":
                    n["attempts"].append(deepcopy(n["attempts"][0]))
                elif fault == "wire_missing":
                    evidence["completion"]["metadata"]["execution_receipt"]["attempts"][0].pop(
                        "wire_body_hash"
                    )
                elif fault == "owner":
                    n["llm_run"]["project_id"] = str(f.uuid4())
                elif fault == "role":
                    n["llm_run"]["logical_role"] = "planner"
                elif fault == "version":
                    n["llm_run"]["prompt_version"] = "stage.3"
                elif fault == "hash":
                    n["operation"]["input_hash"] = "0" * 64
                elif fault == "run_id":
                    n["llm_run"]["id"] = str(f.uuid4())
                elif fault == "restored_run":
                    n["llm_run"]["metadata_json"]["execution_recovery"] = {}
                elif fault == "paid_content":
                    n["operation"]["result_json"]["content"] = "different paid result"
                elif fault == "transport":
                    n["transport_results"][0]["key"] = "0" * 64
                elif fault == "missing_file":
                    evidence["physical_files"].clear()
                elif fault == "file_tamper":
                    evidence["physical_files"][next(iter(evidence["physical_files"]))] = b"{}"
                elif fault == "duplicate_file_key":
                    evidence["physical_files"][next(iter(evidence["physical_files"]))] = (
                        b'{"x":1,"x":2}'
                    )
                elif fault == "fake_stored_verified":
                    evidence.update(verified=True, reviews=[{"verdict": "not_supported"}])
                    evidence["native"] = {}
                elif fault == "candidate_source":
                    inputs["manifest"]["sources"][0]["promotion_state"] = "candidate"
                result = consume(inputs, evidence)
                self.assertEqual(result["adjudication_trace"]["status"], "unverified")
                self.assertFalse(result["adjudicated_nonblocking"])

    def test_raw_response_missing_quotes_wrong_window_density_and_truncation(self):
        for fault in ("missing_quote", "outside_window", "density_N", "length", "missing_row"):
            with self.subTest(fault=fault):
                inputs, evidence = build(
                    ("payoff_density_too_low",)
                    if fault == "density_N"
                    else ("opening_pressure_missing", "first_screen_pressure_missing")
                )
                completion = evidence["completion"]
                answer = json.loads(completion["content"])
                if fault == "missing_quote":
                    answer["reviews"][0]["evidence"][0]["quote"] = "这是原文没有的一句话"
                elif fault == "outside_window":
                    answer["reviews"][0]["evidence"][0] = dict(
                        source="full_text", quote="旧物记录。" * 50
                    )
                elif fault == "density_N":
                    answer["reviews"][0]["events"] = []
                elif fault == "length":
                    completion["finish_reason"] = "length"
                else:
                    answer["reviews"] = []
                completion["content"] = json.dumps(answer, ensure_ascii=False)
                # Keep all native rows/files consistent so the real v4 validator
                # (rather than an earlier mismatched file check) sees the defect.
                for target in (
                    evidence["native"]["operation"]["result_json"],
                    evidence["native"]["transport_results"][0]["result_json"]["result"],
                ):
                    target.update(
                        content=completion["content"], finish_reason=completion["finish_reason"]
                    )
                evidence["native"]["llm_run"]["finish_reason"] = completion["finish_reason"]
                response = evidence["retained_payloads"]["response"]
                response["content"].update(
                    text=completion["content"], original_chars=len(completion["content"])
                )
                response["finish_reason"] = completion["finish_reason"]
                ref = evidence["native"]["llm_run"]["response_payload_ref"]
                evidence["physical_files"][ref] = json.dumps(response, ensure_ascii=False).encode()
                result = consume(inputs, evidence)
                self.assertEqual(result["adjudication_trace"]["status"], "unverified")
                self.assertTrue(
                    result["adjudication_trace"]["error"].startswith("v4_validation:"), result
                )
                self.assertFalse(result["adjudicated_nonblocking"])


if __name__ == "__main__":
    unittest.main()
