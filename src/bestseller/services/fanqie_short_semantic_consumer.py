"""Offline evidence consumer proposal. No IO, DB, dispatch or upload authority.

The caller must freshly read authoritative inputs/rows/files, not deserialize a
stored `verified=True`. A pure function proves consistency, not data provenance.
The reviewed v4 module is an explicit code dependency; no validator is persisted.
"""

from copy import deepcopy
import hashlib
import json
import sys


SCHEMA = "whole-semantic-consumer.stage.1"
# Deliberately narrower than v4's review vocabulary. Never a prefix exemption.
ELIGIBLE = {
    "opening_pressure_missing": "opening_300",
    "opening_fast_payoff_missing": "opening_300",
    "unlock_payoff_missing": "unlock_30_percent",
    "title_click_contract_missing": "title",
    "first_screen_pressure_missing": "opening_80",
    "first_screen_feedback_missing": "opening_300",
    "payoff_density_too_low": "whole_story",
    "title_abstract_setting_only": "title",
    "weak_hook": "opening_30_percent",
    "first_10k_loop_missing": "opening_30_percent",
}
GATES = {"ranking_report": "ranking_passed", "short_v2_report": "short_v2_passed"}


def need(condition, reason):
    if not condition:
        raise ValueError(reason)


def digest(value):
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def strict_json(data):
    def pairs(items):
        result = {}
        for key, value in items:
            need(key not in result, "duplicate_json_key")
            result[key] = value
        return result

    def constant(_):
        raise ValueError("nonfinite_json")

    result = json.loads(data, object_pairs_hook=pairs, parse_constant=constant)
    digest(result)  # Also reject overflowing exponents.
    return result


def _inventory(report, full_text, *, require_opening=False):
    """Retain opaque failures; a missing report cannot silently become empty."""
    blockers = []
    for field in ("passed", "opening_passed", "ranking_passed", "short_v2_passed"):
        need(type(report.get(field)) is bool, "incomplete_current_report:" + field)
    need(type(report.get("thresholds")) is dict, "missing_current_thresholds")
    need(type(report.get("taboo_signals")) is list, "missing_taboo_inventory")
    for gate, flag in GATES.items():
        part = report.get(gate)
        need(
            type(part) is dict
            and type(part.get("findings")) is list
            and type(part.get("passed")) is bool,
            "missing_gate_inventory:" + gate,
        )
        criticals = [f for f in part["findings"] if f.get("severity") == "critical"]
        seen = set()
        for finding in criticals:
            key = (finding.get("code"), finding.get("target"))
            need(
                all(isinstance(x, str) and x for x in key) and key not in seen,
                "invalid_or_duplicate_finding",
            )
            seen.add(key)
            blockers.append(
                {"gate": gate, "code": key[0], "target": key[1], "finding": deepcopy(finding)}
            )
        # Unexplained gate failure is independently blocking, never waived.
        if part["passed"] != (not criticals) or report[flag] != part["passed"]:
            blockers.append({"gate": gate, "code": "unexplained_gate_state", "target": gate})
    opening = report.get("opening_report")
    if opening is None:
        need(not require_opening, "missing_complete_opening_report")
        if not report["opening_passed"]:
            blockers.append({"gate": "opening", "code": "opening_gate_failed", "target": "opening"})
    else:
        need(type(opening) is dict and type(opening.get("passed")) is bool
             and type(opening.get("findings")) is list, "missing_complete_opening_report")
        seen = set()
        criticals = []
        for finding in opening["findings"]:
            need(type(finding) is dict and isinstance(finding.get("code"), str)
                 and finding.get("severity") in {"critical", "warning", "info"}, "invalid_opening_finding")
            if finding["severity"] != "critical":
                continue
            code = finding["code"]
            target = "opening_30_percent" if code in {"weak_hook", "first_10k_loop_missing"} else "opening_native"
            # Same code may occur at distinct native locations. Each exact
            # finding remains an independent blocker; never collapse by code.
            identity = digest(finding)
            need(identity not in seen, "duplicate_opening_finding")
            seen.add(identity)
            criticals.append(finding)
            blockers.append({"gate": "opening", "code": code, "target": target,
                             "finding": deepcopy(finding)})
        if opening["passed"] != (not criticals) or report["opening_passed"] != opening["passed"]:
            blockers.append({"gate": "opening", "code": "unexplained_opening_state", "target": "opening"})
    for signal in report["taboo_signals"]:
        blockers.append({"gate": "taboo", "code": str(signal), "target": "whole_story"})
    if len(full_text.strip()) < 500:
        blockers.append({"gate": "length", "code": "whole_text_too_short", "target": "whole_story"})
    if not report["passed"] and not blockers:
        blockers.append(
            {"gate": "whole", "code": "unexplained_whole_failure", "target": "whole_story"}
        )
    if report["passed"] and blockers:
        blockers.append(
            {"gate": "whole", "code": "inconsistent_whole_state", "target": "whole_story"}
        )
    return blockers


def _native(payload, completion, native, retained, files, semantic):
    """Single original completed send only. Restored telemetry/replays fail closed.

    Rows are JSON snapshots of actual native columns, not a new DB schema.
    No `LlmRun.status` is invented: native completion is established by its
    finish_reason, durable operation/attempt/transport and physical payloads.
    """
    run, op, attempts = native["llm_run"], native["operation"], native["attempts"]
    meta = completion["metadata"]
    need("execution_recovery" not in run["metadata_json"], "restored_run_not_original")
    need(len(attempts) == 1, "single_original_send_required")
    attempt = attempts[0]
    effective = meta["effective_input_receipt"]["hash"]
    key = sha(
        (
            payload["execution_scope_id"]
            + "\n"
            + semantic.native_request_fields(payload)["operation_id"]
            + ":sample:0"
        ).encode()
    )
    need(op["status"] == "completed" and attempt["status"] == "completed", "native_not_completed")
    need(op["key"] == attempt["operation_key"] == key, "native_root_mismatch")
    need(
        op["scope_id"] == attempt["scope_id"] == payload["execution_scope_id"],
        "native_scope_mismatch",
    )
    need(op["input_hash"] == attempt["input_hash"] == effective, "native_input_mismatch")
    need(attempt["model_name"] == completion["model_name"], "native_model_mismatch")
    need(
        type(attempt["actual_tokens"]) is int
        and attempt["actual_tokens"] > 0
        and type(attempt["reserved_tokens"]) is int
        and attempt["reserved_tokens"] > 0,
        "native_charge_missing",
    )
    for field in ("input_tokens", "output_tokens"):
        need(type(completion.get(field)) is int and completion[field] >= 0, "native_tokens_missing")
        need(run[field] == completion[field], "native_run_tokens_mismatch")
    need(
        sum(completion[f] for f in ("input_tokens", "output_tokens")) == attempt["actual_tokens"],
        "native_charge_mismatch",
    )
    need(run["id"] == completion["llm_run_id"], "native_run_id_mismatch")
    for field in ("project_id", "workflow_run_id"):
        need(run[field] == payload[field], "native_run_owner_mismatch")
    for field, expected in (
        ("logical_role", "critic"),
        ("prompt_template", "fanqie_short_semantic_review"),
        ("prompt_version", semantic.PROMPT_VERSION),
        ("prompt_hash", effective),
    ):
        need(run[field] == expected, "native_run_route_mismatch")
    for field in ("provider", "model_name", "finish_reason"):
        need(run[field] == completion[field], "native_run_result_mismatch")
    for field in (
        "paid_call_origin",
        "effective_input_receipt",
        "execution_receipt",
        "prompt_payload_receipt",
    ):
        need(run["metadata_json"][field] == meta[field], "native_run_receipt_mismatch")
    receipt = meta["execution_receipt"]
    need(len(receipt["attempts"]) == 1, "native_attempt_inventory_mismatch")
    sent = receipt["attempts"][0]
    need(
        sent["attempt_id"] == attempt["id"]
        and sent["status"] == "completed"
        and sent["reserved_tokens"] == attempt["reserved_tokens"],
        "native_attempt_receipt_mismatch",
    )
    wire_hash = sent.get("wire_body_hash")
    need(
        isinstance(wire_hash, str)
        and len(wire_hash) == 64
        and all(c in "0123456789abcdef" for c in wire_hash),
        "physical_wire_hash_missing",
    )
    transports = native["transport_results"]
    need(len(transports) == 1, "native_transport_inventory_mismatch")
    transport = transports[0]
    need(
        transport["key"]
        == sha(("transport-result.v1\n" + op["scope_id"] + "\n" + attempt["id"]).encode())
        and transport["scope_id"] == op["scope_id"]
        and transport["input_hash"] == effective
        and transport["status"] == "transport_result",
        "native_transport_identity_mismatch",
    )
    envelope = transport["result_json"]
    need(
        envelope["schema"] == "transport-result.v1"
        and envelope["operation_key"] == key
        and envelope["attempt_id"] == attempt["id"]
        and envelope["input_hash"] == effective,
        "native_transport_binding_mismatch",
    )
    for result in (op["result_json"], envelope["result"]):
        for field in (
            "content",
            "provider",
            "model_name",
            "finish_reason",
            "input_tokens",
            "output_tokens",
        ):
            need(result[field] == completion[field], "native_paid_result_mismatch")
        need(
            result.get("fallback_used") is False and not result.get("tool_calls"),
            "native_paid_fallback_or_tool",
        )
        pm = result["metadata"]
        need(
            pm["paid_call_origin"] == meta["paid_call_origin"]
            and pm["effective_input_receipt"]["hash"] == effective
            and pm["effective_input_receipt"]["model"] == completion["model_name"]
            and pm["execution_receipt"] == receipt,
            "native_paid_receipt_mismatch",
        )
    hashes = {}
    for kind in ("request", "response"):
        ref = f"runtime/llm_payloads/{run['id']}.{kind}.json"
        need(run[kind + "_payload_ref"] == ref, "native_physical_ref_mismatch")
        need(type(files[ref]) is bytes and bool(files[ref]), "physical_file_bytes_required")
        need(strict_json(files[ref]) == retained[kind], "physical_payload_mismatch")
        hashes[ref] = sha(files[ref])
    return {
        "run_id": run["id"],
        "operation_key": key,
        "attempt_id": attempt["id"],
        "native_rows_sha256": digest(native),
        "physical_sha256": hashes,
    }


def consume_semantic_evidence(*, current_inputs, evidence, semantic):
    """Return a separate derived blocker inventory, NEVER overwrite passed/grade.

    `current_inputs`: fresh authoritative kwargs for v4.prepare_review.
    `evidence`: request_payload, completion, retained_payloads, physical_files
    (relative native refs -> original bytes), native original row snapshots.
    Any stale/malformed evidence leaves ALL current blockers in place. Invalid
    current input/report raises; consumers must block publication on exception.
    """
    current = semantic.prepare_review(**deepcopy(current_inputs))
    report = current["original_report"]
    remaining = _inventory(report, current["full_text"], require_opening=semantic.PROMPT_VERSION == "stage.5")
    result = {
        "schema": SCHEMA,
        "original_report": deepcopy(report),
        "remaining_blockers": remaining,
        "adjudicated_nonblocking": [],
        "model_grade_asserted": False,
        "adjudication_trace": {
            "status": "unverified",
            "error": None,
            "binding_sha256": current["binding_sha256"],
            "decisions": [],
        },
    }
    trace = result["adjudication_trace"]
    try:
        need(
            (semantic.SCHEMA, semantic.PROMPT_VERSION) in {
                ("fanqie-short-semantic-stage.v4", "stage.4"),
                ("fanqie-short-semantic-stage.v5", "stage.5"),
            },
            "unsupported_validator_interface",
        )
        need(evidence["request_payload"] == current, "current_payload_mismatch")
        need(current["title_provenance_verified"] is True, "current_title_unverified")
        for source in current["manifest"]["sources"]:
            # Native publication manifests stay byte-for-byte unchanged. Their
            # already source-bound lineage carries the actual adoption states;
            # never add a fabricated simplified promotion_state alias.
            lineage = source.get("lineage") or {}
            states = [value for value in (
                source.get("promotion_state"), lineage.get("adoption_state"),
                lineage.get("source_promotion_state"),
            ) if value is not None]
            need(bool(states) and all(value == "promoted" for value in states),
                 "current_sources_not_adopted")
        proof = _native(
            current,
            evidence["completion"],
            evidence["native"],
            evidence["retained_payloads"],
            evidence["physical_files"],
            semantic,
        )
        recovery = None
        if "protocol_recovery" in evidence:
            from bestseller.services.fanqie_short_semantic_protocol_recovery import verify_recovery
            recovery = verify_recovery(evidence["protocol_recovery"], current, evidence,
                                       consumer=sys.modules[__name__], semantic=semantic)
        # Re-run the real source-bound validator, never consume stored reviews.
        validated = semantic.validate_response(
            deepcopy(current),
            deepcopy(evidence["completion"]),
            current_binding=deepcopy(current["binding"]),
            retained_payloads=deepcopy(evidence["retained_payloads"]),
        )
        need(validated["error"] is None, "v4_validation:" + str(validated["error"]))
        need(
            validated["original_report"] == report and validated["request_payload"] == current,
            "validated_source_mismatch",
        )
        requested = {r["id"]: r for r in current["reviews_requested"]}
        reviews = validated["reviews"]
        need(
            len(reviews) == len(requested) and {r["id"] for r in reviews} == set(requested),
            "validated_coverage_mismatch",
        )
        decisions = []
        for row in reviews:
            item = requested[row["id"]]
            for gate in item["origins"]:
                if gate == "opening_report":
                    continue  # The complete native inventory is checked below.
                identity = {"gate": gate, "code": item["code"], "target": item["target"]}
                need(
                    row["code"] == item["code"] and row["target"] == item["target"],
                    "validated_identity_mismatch",
                )
                if (
                    ELIGIBLE.get(item["code"]) == item["target"]
                    and row["verdict"] == "not_supported"
                ):
                    matches = [b for b in remaining if all(b[k] == v for k, v in identity.items())]
                    need(
                        len(matches) == 1 and matches[0]["finding"] == item["detector_finding"],
                        "gate_finding_mismatch",
                    )
                    decisions.append(
                        {
                            **identity,
                            "verdict": row["verdict"],
                            "finding": deepcopy(matches[0]["finding"]),
                            "evidence": deepcopy(row),
                        }
                    )
            if ELIGIBLE.get(item["code"]) == item["target"] and row["verdict"] == "not_supported":
                for blocker in remaining:
                    if (blocker["gate"] == "opening" and blocker["code"] == item["code"]
                            and blocker["target"] == item["target"]
                            and all(item["detector_finding"].get(k) == v
                                    for k, v in blocker["finding"].items())):
                        decisions.append({**deepcopy(blocker), "verdict": row["verdict"],
                                          "evidence": deepcopy(row)})
        # Atomic: no removal until the entire native proof / batch has passed.
        removed = {(r["gate"], r["code"], r["target"], digest(r["finding"])) for r in decisions}
        result["remaining_blockers"] = [
            b for b in remaining if (b["gate"], b["code"], b["target"], digest(b.get("finding"))) not in removed
        ]
        result["adjudicated_nonblocking"] = decisions
        trace.update(
            status="verified",
            native_proof=proof,
            validation_sha256=digest(validated),
            raw_response_sha256=sha(evidence["completion"]["content"].encode()),
            decisions=deepcopy(reviews),
        )
        if recovery is not None:
            trace["protocol_recovery"] = recovery
    except (
        KeyError,
        ValueError,
        TypeError,
        AttributeError,
        OverflowError,
        RecursionError,
    ) as error:
        trace["error"] = str(error) if isinstance(error, ValueError) else type(error).__name__
    return result


def semantic_readiness_gates(*, current_inputs, evidence, semantic):
    """Small builder/verifier seam: recompute proof on EVERY invocation.

    No serialized derived review or ready flag is accepted as authority. The
    readiness builder still evaluates its length/upload requirements; delivery
    completion, pending rewrites and knowledge closure remain external gates.
    """
    derived = consume_semantic_evidence(
        current_inputs=current_inputs, evidence=evidence, semantic=semantic
    )
    need(derived["adjudication_trace"]["status"] == "verified", "semantic_evidence_unverified")
    blocked = {b["gate"] for b in derived["remaining_blockers"]}
    return {
        "opening_gate_passed": "opening" not in blocked,
        "ranking_gate_passed": "ranking_report" not in blocked,
        "short_v2_gate_passed": "short_v2_report" not in blocked,
        "semantic_derived_review": derived,
    }
