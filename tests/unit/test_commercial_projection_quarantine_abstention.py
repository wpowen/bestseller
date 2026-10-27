"""Pure consumer: authenticated charged quarantines abstain, never supply grades."""

# ruff: noqa: RUF001

from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
import hashlib
import json
from pathlib import Path

import pytest
import test_commercial_action_projection as fixtures

from bestseller.domain.llm_quality_judge import (
    LLMQualityJudgeResult,
    quality_judge_result_from_mapping,
)
from bestseller.services.commercial_action_projection import _sha, project_commercial_actions
from bestseller.services.commercial_judge_recovery import bind_commercial_slot_result
from bestseller.services.commercial_review_tool import submitted_review_payload
from bestseller.services.commercial_review_wire import parse_commercial_wire
from bestseller.services.commercial_source_evidence import (
    CommercialSources,
    resolve_commercial_source_pointer,
)
from bestseller.services.llm import LLMCompletionResult
from bestseller.services.write_gate import GateConfig

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]
stable = fixtures.stable
SLOT = "chapter-quality.independent-v2:2"


def passing_slot(src, number, score):
    retained = fixtures.slot(src, number, score=score, issues=False)
    envelope = json.loads(retained.raw_excerpt)
    raw = json.loads(envelope["raw_report"])
    raw["pass"] = True
    text = json.dumps(raw)
    parsed, adapter, errors = parse_commercial_wire(text, src)
    assert not errors
    completion = LLMCompletionResult(
        content="",
        provider="fixture",
        model_name="fixture",
        tool_calls=[
            {
                "id": "retained-call",
                "function": {"name": "submit_chapter_review", "arguments": text},
            }
        ],
    )
    _, submission, _ = submitted_review_payload(completion)
    adapter["submission"] = submission
    result = quality_judge_result_from_mapping(
        parsed,
        scope="chapter",
        min_overall=0.82,
        min_dimensions={"readability": 0.8},
        llm_run_id=retained.llm_run_id,
    )
    return bind_commercial_slot_result(
        result,
        raw_report=text,
        validation_errors=errors,
        source_receipt=src.receipt,
        sample_slot=f"chapter-quality.independent-v2:{number}",
        attempt=1,
        adapter_receipt=adapter,
        evaluation_contract=envelope["evaluation_contract"],
    )


def quarantine(src, contract, number=2, *, round_no=1, policy_namespace=False):
    """Immutable JSON-shaped proof, with the producer's actual neutral report."""
    slot = f"chapter-quality.independent-v2:{number}"
    scope, cohort, input_hash = "test:quarantined-action", "b" * 64, "c" * 64
    namespace = f"commercial-component:{cohort}:round:{round_no}"
    if policy_namespace:
        namespace = f"commercial-component:{cohort}:policy:{_sha(contract)}:round:{round_no}"
    subject = f"critic:chapter_commercial_quality_judge:{src.receipt['prompt_version']}"
    operation = "semantic.v1:" + _sha([namespace, subject, input_hash])
    key = hashlib.sha256(f"{scope}\n{operation}:sample:{slot}".encode()).hexdigest()
    aid, identifier = f"physical-{number}", f"operator-exit-{number}"
    now = datetime.now(UTC)
    root = {
        "key": key,
        "scope_id": scope,
        "input_hash": input_hash,
        "owner": "physical-owner",
        "status": "stopped",
        "result_json": None,
        "lease_until": now.isoformat(),
    }
    attempt = {
        "id": aid,
        "operation_key": key,
        "scope_id": scope,
        "input_hash": input_hash,
        "model_name": "fixture-critic",
        "status": "reconciled_charged",
        "reserved_tokens": 1000,
        "actual_tokens": None,
        "created_at": (now - timedelta(seconds=1)).isoformat(),
    }
    failure = {
        **root,
        "key": hashlib.sha256(f"transport-failure.v1\n{scope}\n{aid}".encode()).hexdigest(),
        "status": "transport_failure_receipt",
        "result_json": {
            "schema": "transport-failure.v1",
            "attempt_id": aid,
            "operation_key": key,
            "project_id": "unrelated-observatory-project",
            "workflow_run_id": "stopped-cohort-owner",
            "physical_sends": 1,
            "logical_role": "critic",
            "prompt_template": "chapter_commercial_quality_judge",
            "partial_stream_observed": False,
            "exception_type": "CancelledError",
        },
    }
    evidence = {
        "reconciliation_id": identifier,
        "attempt_id": aid,
        "expected_input_hash": input_hash,
        "decision": "quarantine_charged",
        "evidence_kind": "dispatcher_exit",
        "evidence_sha256": "e" * 64,
        "actor": "offline authorized operator",
        "remote_operation_closed": False,
        "local_dispatcher_stopped": True,
        "result": None,
    }
    record = {
        k: evidence[k]
        for k in (
            "reconciliation_id",
            "evidence_kind",
            "evidence_sha256",
            "actor",
            "remote_operation_closed",
            "local_dispatcher_stopped",
        )
    }
    record.update(
        request_hash=_sha(evidence),
        at=now.isoformat(),
        receipt={
            "scope_id": scope,
            "reconciliation_id": identifier,
            "attempt_id": aid,
            "decision": "quarantine_charged",
            "status": "active",
            "remaining_unknown": 0,
            "calls_used": 3,
            "tokens_charged": 3000,
            "automatic_resume": False,
            "credit_released": False,
            "remote_outcome": "unknown_quarantined",
            "operation_replay_allowed": False,
        },
    )
    saved = {
        **root,
        "key": hashlib.sha256(f"reconciliation.v1\n{scope}\n{identifier}".encode()).hexdigest(),
        "input_hash": _sha(evidence),
        "owner": "manual-reconciliation",
        "status": "reconciliation_receipt",
        "result_json": record,
    }
    proof = {
        "schema": "commercial-slot-quarantine.v1",
        "scope_id": scope,
        "cohort_key": cohort,
        "slot": slot,
        "round": round_no,
        "namespace": namespace,
        "input_hash": input_hash,
        "request_sha256": "d" * 64,
        "source_receipt_sha256": _sha(dict(src.receipt)),
        "effective_critic": {"model": "fixture-critic"},
        "root": root,
        "attempts": [attempt],
        "failures": [failure],
        "reconciliations": [saved],
    }
    report = LLMQualityJudgeResult.model_validate(
        {
            "scope": "chapter",
            "passed": False,
            "overall_score": 0,
            "dimension_scores": {},
            "llm_run_id": None,
            "blocking_issues": [
                {
                    "code": "CHAPTER_JUDGE_UNAVAILABLE",
                    "severity": "critical",
                    "evidence": "Paid slot quarantined with unknown remote outcome; no grade.",
                    "required_fix": (
                        "Retain the charged slot; no replay, replacement sample or prose rewrite."
                    ),
                }
            ],
            "rewrite_plan": {
                "scope": "judge",
                "instructions": "No retry or chapter rewrite is authorized.",
            },
            "raw_excerpt": json.dumps(
                {
                    "schema": "commercial-slot-quarantine.v1",
                    "sample_slot": slot,
                    "operation_key": key,
                    "attempt": round_no,
                    "proof_sha256": _sha(proof),
                },
                sort_keys=True,
            ),
        }
    )
    return report, {
        "schema": "verified-quarantined-slot-action.v1",
        "proof": proof,
        "source_binding": {
            k: src.receipt[k]
            for k in (
                "body_sha256",
                "semantic_source_sha256",
                "previous_sha256",
                "source_protocol_sha256",
            )
        },
        "evaluation_contract": deepcopy(contract),
        "original_report_sha256": _sha(report.model_dump(mode="json")),
    }


async def cohort_case(stable, *, hard=False, round_no=1, policy_namespace=False):
    src = fixtures.sources()
    first = passing_slot(src, 0, 0.855)
    second = (
        fixtures.slot(src, 1, score=0.86, kind="current_fact_conflict")
        if hard
        else passing_slot(src, 1, 0.86)
    )
    contract = json.loads(first.raw_excerpt)["evaluation_contract"]
    unavailable, verified = quarantine(
        src, contract, round_no=round_no, policy_namespace=policy_namespace
    )
    aggregate = await stable([first, second, unavailable])
    return src, aggregate, {SLOT: verified}


def project(src, aggregate, mapping=None):
    return project_commercial_actions(
        aggregate,
        sources=src,
        gate_config=GateConfig({}),
        chapter_number=2,
        verified_quarantine_slots=mapping,
    )


@pytest.mark.parametrize("round_no,policy_namespace", [(1, False), (1, True)])
async def test_two_valid_plus_quarantine_preserves_real_majority_and_compact_abstention(
    stable, round_no, policy_namespace
):
    src, original, mapping = await cohort_case(
        stable, round_no=round_no, policy_namespace=policy_namespace
    )
    before, source_before, proof_before = (
        original.model_dump(mode="json"),
        deepcopy(src),
        deepcopy(mapping),
    )
    view, receipt = project(src, original, mapping)
    assert receipt["status"] == "projected" and view is not None
    assert view["overall_score"] == original.overall_score == (0.855 + 0.86) / 2
    assert view["dimension_scores"] == dict(original.dimension_scores)
    assert view["pass"] is original.passed is True
    assert not view["blocking_issues"] and not view["audit_issues"]
    assert not receipt["issue_actions"] and receipt["unknown_sample_slots"] == [SLOT]
    abstention = receipt["slots"][2]
    assert abstention["abstains"] is True
    assert (
        abstention["domain_issues_contributed"]
        == abstention["actions_contributed"]
        == abstention["scores_contributed"]
        == 0
    )
    assert abstention["quarantine_proof_sha256"] == _sha(mapping[SLOT]["proof"])
    assert abstention["original_report_sha256"] == mapping[SLOT]["original_report_sha256"]
    assert not {
        "proof",
        "root",
        "attempts",
        "reconciliations",
        "original_report",
        "llm_run_id",
    } & set(abstention)
    assert "offline authorized operator" not in json.dumps(receipt)
    assert (
        original.model_dump(mode="json") == before
        and src == source_before
        and mapping == proof_before
    )


async def test_default_missing_partial_or_extra_proof_mapping_stays_closed(stable):
    src, original, mapping = await cohort_case(stable)
    for supplied in (
        None,
        {},
        {**mapping, "chapter-quality.independent-v2:0": mapping[SLOT]},
        {SLOT: None},
    ):
        view, receipt = project(src, original, supplied)
        assert view is None and receipt["status"] in {"blocked", "unsupported"}
    original_bytes = original.raw_excerpt
    valid = await stable([fixtures.slot(src, i, issues=False) for i in range(3)])
    assert project(src, valid, mapping)[0] is None
    assert original.raw_excerpt == original_bytes


async def test_full_source_alias_receipt_is_not_replaced_by_four_matching_hashes(stable):
    src, original, mapping = await cohort_case(stable)
    changed = replace(src, receipt={**src.receipt, "structural_aliases": [{"path": "changed"}]})
    view, receipt = project(changed, original, mapping)
    assert view is None and receipt["reason"] == "quarantine_source_receipt_changed"


async def test_old_round2_proof_without_authenticated_input_binding_stays_closed(stable):
    src, original, mapping = await cohort_case(stable, round_no=2)
    view, receipt = project(src, original, mapping)
    assert view is None and receipt["reason"] == "quarantine_proof_shape_changed"


@pytest.mark.parametrize(
    "fault",
    [
        "proof_hash",
        "report_hash",
        "source_body",
        "source_semantic",
        "source_previous",
        "source_protocol",
        "full_receipt",
        "contract",
        "round",
        "slot",
        "cohort",
        "scope",
        "root",
        "root_result",
        "root_status",
        "model",
        "attempt_status",
        "attempt_actual",
        "attempt_input",
        "failure_missing",
        "failure_sends",
        "failure_owner",
        "receipt_missing",
        "receipt_hash",
        "receipt_credit",
        "receipt_replay",
        "receipt_remote",
        "receipt_actor",
        "duplicate_attempt",
        "invented_paid_run",
        "pass",
        "score",
        "dimensions",
        "audit_issue",
        "domain_issue",
        "rewrite",
        "valid_flag",
        "alias",
        "extra_report_field",
        "valid_wire_source",
    ],
)
async def test_tampered_proof_or_typed_report_never_grants_projection(stable, fault):
    src, original, mapping = await cohort_case(stable)
    evidence = json.loads(original.raw_excerpt)
    wrapper = mapping[SLOT]
    proof = wrapper["proof"]
    report = evidence["samples"][2]["report"]
    if fault == "proof_hash":
        proof["request_sha256"] = "f" * 64
    if fault == "report_hash":
        wrapper["original_report_sha256"] = "f" * 64
    binding_faults = {
        "source_body": "body_sha256",
        "source_semantic": "semantic_source_sha256",
        "source_previous": "previous_sha256",
        "source_protocol": "source_protocol_sha256",
    }
    if fault in binding_faults:
        wrapper["source_binding"][binding_faults[fault]] = "f" * 64
    if fault == "full_receipt":
        proof["source_receipt_sha256"] = "f" * 64
    if fault == "contract":
        wrapper["evaluation_contract"]["min_overall"] = 0.1
    if fault == "round":
        proof["round"] = 2
    if fault == "slot":
        proof["slot"] = "chapter-quality.independent-v2:1"
    if fault == "cohort":
        proof["cohort_key"] = "f" * 64
    if fault == "scope":
        proof["scope_id"] = "test:other"
    if fault == "root":
        proof["root"]["key"] = "f" * 64
    if fault == "root_result":
        proof["root"]["result_json"] = {"content": "invented grade"}
    if fault == "root_status":
        proof["root"]["status"] = "completed"
    if fault == "model":
        proof["effective_critic"]["model"] = "changed-model"
    if fault == "attempt_status":
        proof["attempts"][0]["status"] = "completed"
    if fault == "attempt_actual":
        proof["attempts"][0]["actual_tokens"] = 1
    if fault == "attempt_input":
        proof["attempts"][0]["input_hash"] = "f" * 64
    if fault == "failure_missing":
        proof["failures"] = []
    if fault == "failure_sends":
        proof["failures"][0]["result_json"]["physical_sends"] = 0
    if fault == "failure_owner":
        proof["failures"][0]["owner"] = "different"
    if fault == "receipt_missing":
        proof["reconciliations"] = []
    if fault == "receipt_hash":
        proof["reconciliations"][0]["input_hash"] = "f" * 64
    record = proof["reconciliations"][0]["result_json"] if proof["reconciliations"] else {}
    if fault == "receipt_credit":
        record["receipt"]["credit_released"] = True
    if fault == "receipt_replay":
        record["receipt"]["operation_replay_allowed"] = True
    if fault == "receipt_remote":
        record["remote_operation_closed"] = True
    if fault == "receipt_actor":
        record["actor"] = "changed actor"
    if fault == "duplicate_attempt":
        proof["attempts"].append(deepcopy(proof["attempts"][0]))
    if fault == "invented_paid_run":
        report["llm_run_id"] = "invented"
    if fault == "pass":
        report["passed"] = True
    if fault == "score":
        report["overall_score"] = 1
    if fault == "dimensions":
        report["dimension_scores"] = {"readability": 1}
    if fault == "audit_issue":
        report["audit_issues"] = deepcopy(report["blocking_issues"])
    if fault == "domain_issue":
        report["blocking_issues"].append(
            {
                "code": "BODY_BAD",
                "severity": "critical",
                "evidence": "",
                "required_fix": "rewrite",
                "path": "",
            }
        )
    if fault == "rewrite":
        report["rewrite_plan"]["change"] = ["Rewrite the prose"]
    if fault == "valid_flag":
        evidence["samples"][2]["valid"] = True
    if fault == "alias":
        report["pass"] = True
    if fault == "extra_report_field":
        report["infrastructure_failure"] = True
    if fault == "valid_wire_source":
        paid = evidence["samples"][0]["report"]
        raw = json.loads(paid["raw_excerpt"])
        raw["source_binding"]["body_sha256"] = "f" * 64
        paid["raw_excerpt"] = json.dumps(raw)
    raw = json.loads(report["raw_excerpt"])
    if fault != "proof_hash":
        raw["proof_sha256"] = _sha(proof)
        report["raw_excerpt"] = json.dumps(raw, sort_keys=True)
    if fault != "report_hash":
        wrapper["original_report_sha256"] = _sha(report)
    altered = original.model_copy(update={"raw_excerpt": json.dumps(evidence)})
    before, mapping_before = altered.model_dump(mode="json"), deepcopy(mapping)
    view, receipt = project(src, altered, mapping)
    assert view is None and receipt["status"] in {"blocked", "unsupported"}
    assert altered.model_dump(mode="json") == before and mapping == mapping_before


async def test_quarantine_does_not_drop_hard_issues_or_supply_a_vote(stable):
    src, original, mapping = await cohort_case(stable, hard=True)
    view, receipt = project(src, original, mapping)
    assert receipt["status"] == "projected" and view["pass"] is False
    assert any(issue["severity"] == "critical" for issue in view["blocking_issues"])
    assert all(issue["code"] != "CHAPTER_JUDGE_UNAVAILABLE" for issue in view["blocking_issues"])
    first = fixtures.slot(src, 0, issues=False)
    contract = mapping[SLOT]["evaluation_contract"]
    second, verified = quarantine(src, contract, 1)
    third, third_verified = quarantine(src, contract)
    low = await stable([first, second, third])
    view, receipt = project(
        src, low, {SLOT: third_verified, "chapter-quality.independent-v2:1": verified}
    )
    assert view is None and receipt["reason"] == "insufficient_verified_quorum"


async def test_actual_retained_two_valid_and_one_quarantine_projects_without_changing_bytes():
    """RO production evidence replay; the independent async bridge tests DB authentication."""
    path = Path(
        "reports/framework-recovery-2026-10-21/phase8n-original-body-review-unknown-readback.json"
    )
    if not path.exists():
        pytest.skip("optional local retained production readback is unavailable")
    before = path.read_bytes()
    retained = json.loads(before)
    journals = [
        j
        for wf in retained["workflows"]
        for j in (wf.get("metadata_json") or {}).get("commercial_component_cohorts", {}).values()
    ]
    journal = next(
        j for j in journals if any(e.get("state") == "quarantined" for e in j["slots"].values())
    )
    slot, entry = next(
        (s, e) for s, e in journal["slots"].items() if e.get("state") == "quarantined"
    )
    user = entry["request"]["user_prompt"]
    marker = "完整来源包（JSON 数据，含本章全文、已提供前章和类型化引用目录）：\n"
    start = user.index(marker) + len(marker)
    payload, length = json.JSONDecoder().raw_decode(user[start:])
    text = user[start : start + length]
    catalog = {}
    for row in payload["citation_catalog"]:
        value = resolve_commercial_source_pointer(payload, row["source_ref"])
        if row["kind"] in {"current", "prior"}:
            quote = value[row["start"] : row["end"]]
        elif row["kind"] == "observation":
            quote = next(
                (
                    value[k]
                    for k in ("text", "evidence", "value")
                    if isinstance(value, dict)
                    and isinstance(value.get(k), str)
                    and value[k].strip()
                ),
                json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                if isinstance(value, dict)
                else str(value),
            )
        else:
            quote = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        catalog[row["id"]] = {**row, "text": quote}
    src = CommercialSources(text=text, catalog=catalog, receipt=entry["source_receipt"])
    original = journal["aggregate"]["report"]
    snapshot = deepcopy(original)
    mapping = {
        slot: {
            "schema": "verified-quarantined-slot-action.v1",
            "proof": entry["quarantine_history"][-1]["proof"],
            "source_binding": journal["binding"]["source_binding"],
            "evaluation_contract": journal["binding"]["evaluation_contract"],
            "original_report_sha256": _sha(entry["report"]),
        }
    }
    view, receipt = project_commercial_actions(
        original,
        sources=src,
        gate_config=GateConfig({}),
        chapter_number=payload["chapter_number"],
        verified_quarantine_slots=mapping,
    )
    assert receipt["status"] == "projected" and view is not None, receipt
    assert view["overall_score"] == original["overall_score"] == (0.855 + 0.86) / 2
    assert (
        view["dimension_scores"] == original["dimension_scores"]
        and view["pass"] is original["passed"]
    )
    assert receipt["slots"][2]["abstains"] is True
    assert original == snapshot and path.read_bytes() == before
