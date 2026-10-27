"""Formal selection + independent current-title evidence, shared by export/readback.

No dispatch, grants or stored-pass authority. Each fresh native load repeats
history, once census, native/physical proof and both pure validators. Its exact
unchanged in-memory context may then reuse that computation within the call.
"""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import threading
import weakref

from bestseller.services import fanqie_short_semantic_consumer as legacy
from bestseller.services import fanqie_short_semantic_selection_adapter as adapter


SCHEMA = "fanqie-short-selection-readiness.v1"
_VALIDATED_CONTEXTS = {}
_CONTEXT_LOCK = threading.Lock()
_BYTES_MARKER = '\x00bestseller.native.physical.bytes'


def _validation_code_fence():
    """Physical runtime bytes; a hot source/config change invalidates a seal."""
    from bestseller.services.runtime_provenance import _KEY_FILES
    root = Path(__file__).resolve().parents[3]
    hashes = []
    for name in sorted(_KEY_FILES):
        path = root / name
        if not path.is_file():
            if name in {'config/local.yaml', 'migrations/versions/0044_character_state_binding.py'}:
                # The migration is host-only in this deployment; runtime
                # provenance likewise records it as absent in the process.
                hashes.append((name, None))
                continue
            raise ValueError('missing_validation_runtime_source')
        hashes.append((name, hashlib.sha256(path.read_bytes()).hexdigest()))
    return hashlib.sha256(json.dumps(hashes, separators=(',', ':')).encode()).hexdigest()


def _context_fingerprint(context):
    """JSON plus physical bytes, with no reserved-marker/type collisions."""
    value = {'inputs': context.current_inputs, 'evidence': context.evidence}
    pending, visited = [value], set()
    while pending:
        item = pending.pop()
        if type(item) in (dict, list):
            if id(item) in visited:
                continue
            visited.add(id(item))
            if type(item) is dict:
                if _BYTES_MARKER in item or any(type(k) is not str for k in item):
                    raise ValueError('unsealable_context_mapping')
                pending.extend(item.values())
            else:
                pending.extend(item)
        elif type(item) not in (str, int, float, bool, bytes, type(None)):
            raise ValueError('unsealable_context_type')
    def encode(item):
        if type(item) is bytes:
            return {_BYTES_MARKER: hashlib.sha256(item).hexdigest(), 'length': len(item)}
        raise TypeError('unsealable_context_type')
    encoded = json.dumps(value, default=encode, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def _forget_context(key, reference):
    with _CONTEXT_LOCK:
        entry = _VALIDATED_CONTEXTS.get(key)
        if entry is not None and entry[0] is reference:
            _VALIDATED_CONTEXTS.pop(key, None)


def _validate_loaded_context(context):
    """Only the native loader calls this; no stored JSON can mint this seal."""
    try:
        before = (_validation_code_fence(), _context_fingerprint(context))
    except (OSError, ValueError, TypeError, RecursionError):
        before = None
    view = derive_selection_readiness(context)
    if before is not None:
        try:
            after = (_validation_code_fence(), _context_fingerprint(context))
            if after == before:
                key = id(context)
                reference = weakref.ref(context, lambda ref: _forget_context(key, ref))
                # Store a private value, never a caller-owned mutable result.
                serialized = json.dumps(view, ensure_ascii=False, sort_keys=True,
                    separators=(',', ':'), allow_nan=False)
                with _CONTEXT_LOCK:
                    _VALIDATED_CONTEXTS[key] = (reference, after, serialized)
        except (OSError, ValueError, TypeError, RecursionError):
            pass  # Reuse is optional; the complete proof above still ran.
    return view


def selection_readiness_view(context):
    """Reuse only this freshly validated object with identical bytes and code.

    Every new native load gets a different object and runs the full proof.
    Mutation, caller-created lookalikes, source drift or missing runtime files
    always fall through to the original complete derivation.
    """
    with _CONTEXT_LOCK:
        entry = _VALIDATED_CONTEXTS.get(id(context))
    if entry is not None and entry[0]() is context:
        try:
            if entry[1] == (_validation_code_fence(), _context_fingerprint(context)):
                return json.loads(entry[2])
        except (OSError, ValueError, TypeError, RecursionError):
            pass
    return derive_selection_readiness(context)


def need(ok, reason):
    adapter.need(ok, reason)


def _verify_original_inventory(evidence, inputs):
    parent = evidence["parent"]
    expected = {row["id"]: row for row in (parent["parent_artifact"], parent["original_v4_artifact"])}
    titles = {parent["parent_report"]["request_payload"]["title"], inputs["title"]}
    found = []
    for row in evidence["original_artifact_inventory"]:
        payload = row["content"].get("request_payload")
        need(isinstance(payload, dict) and isinstance(payload.get("full_text"), str)
             and isinstance(payload.get("title"), str), "original_history_unclassifiable")
        if payload["full_text"] == inputs["full_text"] and payload["title"] in titles:
            need(row["id"] in expected and row == expected[row["id"]], "unexplained_current_original_artifact")
            found.append(row["id"])
    need(len(found) == 2 and set(found) == set(expected), "original_history_missing")


def _body_report_unchanged(old, current):
    for gate in legacy.GATES:
        before = [r for r in old[gate]["findings"] if r.get("target") != "title"]
        after = [r for r in current[gate]["findings"] if r.get("target") != "title"]
        need(before == after, "body_detector_changed:" + gate)
    for key in ("opening_report", "opening_passed", "taboo_signals", "thresholds"):
        need(old[key] == current[key], "body_gate_changed:" + key)


def _close_row(remaining, item, row, decisions, *, repair=None):
    """Exact source finding only. Unknown/opaque/native hard findings remain."""
    if legacy.ELIGIBLE.get(item["code"]) != item["target"] or row["verdict"] != "not_supported":
        return
    displayed = deepcopy(row)
    labels = {}
    if repair is not None:
        need(row["reason"] == repair["original_reason"], "repaired_original_reason_changed")
        labels = {"original_reason": repair["original_reason"],
                  "original_reason_sha256": legacy.sha(repair["original_reason"].encode()),
                  "original_row_sha256": repair["original_row_sha256"],
                  "original_reason_authority": repair["original_reason_authority"],
                  "repaired_reason": repair["repair_rationale"],
                  "explanation_authority": "provider_evidence_repair_only_no_new_grade"}
        # This is display evidence, not the immutable raw/resolved provider row.
        displayed.update(reason=repair["repair_rationale"], **deepcopy(labels))
    for gate in item["origins"]:
        if gate == "opening_report":
            continue
        matches = [b for b in remaining if b["gate"] == gate and b["code"] == item["code"]
                   and b["target"] == item["target"] and b.get("finding") == item["detector_finding"]]
        need(len(matches) == 1, "exact_current_finding_missing")
        decisions.append({**deepcopy(matches[0]), "verdict": row["verdict"], "evidence": deepcopy(displayed), **deepcopy(labels)})
    for blocker in remaining:
        if (blocker["gate"] == "opening" and blocker["code"] == item["code"]
                and blocker["target"] == item["target"]
                and all(item["detector_finding"].get(k) == v for k, v in blocker["finding"].items())):
            decisions.append({**deepcopy(blocker), "verdict": row["verdict"], "evidence": deepcopy(displayed), **deepcopy(labels)})


def derive_selection_readiness(context):
    from bestseller.services import fanqie_short_semantic_evidence_selection as selection
    from bestseller.services import fanqie_short_title_semantic_judge as title_judge
    from bestseller.services import fanqie_short_semantic_review as semantic

    inputs, evidence = context.current_inputs, context.evidence
    report = inputs["original_report"]
    remaining = legacy._inventory(report, inputs["full_text"], require_opening=True)
    descriptor = evidence["descriptor"]
    _verify_original_inventory(evidence, inputs)
    audit, history_proof = adapter.verify_parent_context(evidence["parent"], current_inputs=inputs)
    need(adapter.recovery.descriptor(evidence["parent"]["parent_artifact"]) == descriptor, "parent_descriptor_changed")
    _body_report_unchanged(evidence["parent"]["parent_report"]["original_report"], report)
    has_link = "title_contract_link" in evidence
    leaves = adapter.verify_once_census(evidence, descriptor=descriptor, current_manifest=inputs["manifest"],
                                       allow_failed_title_for_link=has_link)
    plan = selection.prepare_selection(evidence["parent"]["parent_report"])
    title_plan = title_judge.prepare_title_review(inputs, parent_descriptor=descriptor)
    current = semantic.prepare_review(**deepcopy(inputs))
    source = current["native_publishing_title_receipt"]
    need(isinstance(source, dict) and current["title_provenance_verified"] is True
         and source["current_manifest_sha256"] == legacy.digest(inputs["manifest"])
         and source["lineage_kind"] == "delegated_paid_candidate_selection", "current_title_source_unverified")
    need(source["human_attraction_acceptance"] == "not_performed", "invented_human_attraction_acceptance")
    open_debt = deepcopy(source["editorial_quality_debt"])
    proofs = {}
    for kind, expected, module in ((adapter.BODY_ARTIFACT_TYPE, plan, selection),
                                   (adapter.TITLE_ARTIFACT_TYPE, title_plan, title_judge)):
        artifact, wf = leaves[kind]
        bundle = evidence["leaves"][kind]
        if kind == adapter.TITLE_ARTIFACT_TYPE:
            need(artifact["content"]["source_manifest"] == inputs["manifest"], "current_title_manifest_changed")
        need(bundle["artifact"] == artifact and bundle["workflow"] == wf
             and artifact["content"]["request_payload"] == expected
             and artifact["content"]["schema"] == module.SCHEMA
             and artifact["schema_version"] == adapter.LEAF_SCHEMA_VERSION
             and wf["metadata_json"]["plan_binding_sha256"] == expected["binding_sha256"]
             and wf["metadata_json"]["execution_scope_id"] == inputs["execution_scope_id"], "current_leaf_plan_changed")
        need(bundle["evidence"]["request_payload"] == expected
             and artifact["content"]["raw_response"] == bundle["evidence"]["completion"]["content"], "leaf_raw_response_changed")
        need(artifact["content"]["retained_payloads"] == bundle["evidence"]["retained_payloads"]
             and all(bundle["evidence"]["completion"].get(k) == v
                     for k, v in artifact["content"]["provider_receipt"].items()), "leaf_saved_receipt_changed")
        fields = module.native_request_fields(expected, workflow_run_id=wf["id"])
        linked_title = kind == adapter.TITLE_ARTIFACT_TYPE and has_link
        native_proof = adapter.verify_forced_native_call(fields, bundle["evidence"], workflow=wf,
            project_id=str(inputs["project_id"]), scope_id=inputs["execution_scope_id"],
            expected_workflow_status="machine_blocked" if linked_title else "completed")
        if linked_title:
            link_proof, link_audit = adapter.verify_title_contract_link(evidence,
                original_artifact=artifact, original_workflow=wf, original_completion=bundle["evidence"]["completion"],
                original_plan=expected, current_inputs=inputs)
            proof = link_proof["strict_validation"]
            native_proof["title_contract_linkage"] = {"proof": link_proof, "nonpaid_audit": link_audit,
                "original_paid_workflow_status": wf["status"], "original_paid_artifact_status": artifact["status"]}
        else:
            proof = (selection.validate_selection(expected, bundle["evidence"]["completion"]) if kind == adapter.BODY_ARTIFACT_TYPE
                     else title_judge.validate_title_response(expected, bundle["evidence"]["completion"]))
        proofs[kind] = {"native": native_proof, "validated": proof}
    runs = [evidence["leaves"][k]["evidence"]["completion"]["llm_run_id"] for k in adapter.NEW_ARTIFACT_TYPES]
    old_runs = {evidence["parent"][k]["completion"]["llm_run_id"] for k in ("parent_evidence", "original_v4_evidence")}
    need(len(set(runs)) == 2 and not old_runs.intersection(runs), "new_paid_run_not_independent")
    body_proof = proofs[adapter.BODY_ARTIFACT_TYPE]["validated"]
    need(body_proof["frozen_body_rows"] == plan["frozen_body_rows"]
         and body_proof["frozen_title_rows"] == plan["frozen_title_rows"], "original_valid_rows_changed")
    frozen = {r["id"]: r["resolved_row"] for r in audit["frozen_body_rows"]}
    need(frozen == {r["id"]: r["resolved_row"] for r in plan["frozen_body_rows"]}, "frozen_body_inventory_changed")
    body_rows = {**frozen, **{r["id"]: r["resolved_row"] for r in body_proof["selected_rows"]}}
    original_items = {r["id"]: r for r in evidence["parent"]["parent_report"]["request_payload"]["reviews_requested"]}
    decisions = []
    repaired_records = {r["id"]: r for r in body_proof["selected_rows"]}
    for identity, row in body_rows.items():
        item = original_items[identity]
        need(item["target"] != "title", "body_selection_attempted_title_override")
        _close_row(remaining, item, row, decisions, repair=repaired_records.get(identity))
    title_proof = proofs[adapter.TITLE_ARTIFACT_TYPE]["validated"]
    # Independent title assessment is mandatory even with zero heuristic rows.
    assessment = title_proof["title_assessment"]
    if assessment["verdict"] != "supported":
        remaining.append({"gate": "title", "code": "current_title_assessment_unaccepted", "target": "title"})
    title_items = {r["id"]: r for r in current["reviews_requested"] if r["target"] == "title"}
    title_rows = title_proof["reviews"]
    need(len(title_rows) == len(title_items) and {r["id"] for r in title_rows} == set(title_items), "current_title_row_coverage")
    for row in title_rows:
        _close_row(remaining, title_items[row["id"]], row, decisions)
    removed = {(r["gate"], r["code"], r["target"], legacy.digest(r["finding"])) for r in decisions}
    remaining = [r for r in remaining if (r["gate"], r["code"], r["target"], legacy.digest(r.get("finding"))) not in removed]
    trace = {"status": "verified", "error": None, "binding_sha256": legacy.digest({"current": current, "descriptor": descriptor}),
             "historical_proof": history_proof, "native_leaves": proofs,
             "once_census_sha256": legacy.digest({k: evidence[k] for k in ("artifact_inventory", "workflow_inventory")}),
             "decisions": deepcopy(decisions), "historical_title_refusals": deepcopy(audit["frozen_title_rows"]),
             "open_debt": open_debt, "human_attraction_acceptance": "not_performed"}
    derived = {"schema": SCHEMA, "original_report": deepcopy(report), "remaining_blockers": remaining,
               "adjudicated_nonblocking": decisions, "model_grade_asserted": False,
               "adjudication_trace": trace, "open_debt": deepcopy(open_debt),
               "human_attraction_acceptance": "not_performed"}
    blocked = {r["gate"] for r in remaining}
    return {"opening_gate_passed": "opening" not in blocked,
            "ranking_gate_passed": "ranking_report" not in blocked,
            "short_v2_gate_passed": "short_v2_report" not in blocked,
            "semantic_derived_review": derived}


async def load_current_selection_context(session, settings, *, project, assembly,
                                         original_report, current_inputs_factory):
    context = await adapter.load_current_selection_context(session, settings, project=project, assembly=assembly,
        original_report=original_report, current_inputs_factory=current_inputs_factory)
    _validate_loaded_context(context)
    return context
