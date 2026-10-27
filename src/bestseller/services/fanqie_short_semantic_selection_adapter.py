"""Read-only native selection/title custody for the formal readiness branch.

No dispatch or ledger mutation. Existing v4->v5 adapters/consumers stay unchanged.
The original seven v5 files are trusted historical dependencies, not current
title authority. No dispatch, source mutation or stored-pass authority is provided.
"""

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import re
from pathlib import Path
from types import ModuleType
from uuid import NAMESPACE_URL, UUID, uuid5

from bestseller.services import fanqie_short_semantic_adapter as native
from bestseller.services import fanqie_short_semantic_consumer as consumer
from bestseller.services import fanqie_short_semantic_protocol_recovery as recovery


V5_SOURCES = {**recovery.V4_SOURCES,
    "fanqie_short_semantic_review.py": "4079a7e4e8ab6efecae926d5627c03b96466651beddb0db48a426d945778bb24",
    "commercial_request_purpose.py": "a94992d390465244c022f213f3164b9b808917665dcc315ed7f57b396c7d5055",
}
TOOL_NAME = "select_whole_semantic_evidence"
TOOL_TEMPLATE = "fanqie_short_semantic_evidence_selection"
SELECTION_PARENT_KEY = "semantic_selection_parent"  # Proposed WF metadata seam.
BODY_ARTIFACT_TYPE = "fanqie_whole_semantic_evidence_selection"
TITLE_ARTIFACT_TYPE = "fanqie_whole_title_semantic_review"
NEW_ARTIFACT_TYPES = (BODY_ARTIFACT_TYPE, TITLE_ARTIFACT_TYPE)
LINK_ARTIFACT_TYPE = "fanqie_whole_title_contract_link"
ALL_SELECTION_TYPES = (*NEW_ARTIFACT_TYPES, LINK_ARTIFACT_TYPE)
LEAF_SCHEMA_VERSION = "selection.1"  # Native prompt version; fits artifact String(32).
RESTORATION_KEY = "original_physical_telemetry_restoration"


def _verify_physical_telemetry_restoration(fields, evidence, workflow):
    """A missing telemetry row may be restored; the paid send is never replayed."""
    c, n = evidence["completion"], evidence["native"]
    meta, wm = c["metadata"], workflow["metadata_json"]
    failure = wm.get("native_save_failure")
    rollback = isinstance(failure, list) and any(isinstance(r, dict) and r.get("sqlstate") == "22001" for r in failure)
    receipt = meta.get(RESTORATION_KEY)
    if receipt is None:
        need(not rollback, "sql22001_restoration_receipt_missing")
        return None
    keys = {"schema", "reason", "original_llm_run_id", "original_workflow_run_id", "operation_key", "attempt_id",
            "paid_result_sha256", "physical_sha256", "original_failure_sha256", "provider_calls",
            "original_paid_identity_preserved", "reconstructed_request_metadata_sha256"}
    need(isinstance(receipt, dict) and set(receipt) == keys, "restoration_receipt_schema")
    run, root, attempt = n["llm_run"], n["operation"], n["attempts"][0]
    need(receipt["schema"] == "original-physical-telemetry-restoration.v1"
         and receipt["reason"] == "caller_transaction_rollback_sqlstate_22001"
         and rollback and wm.get("no_resend") is True, "restoration_failure_origin")
    need(receipt["original_llm_run_id"] == run["id"] == c["llm_run_id"]
         and receipt["original_workflow_run_id"] == workflow["id"] == run["workflow_run_id"]
         and receipt["operation_key"] == root["key"] == attempt["operation_key"]
         and receipt["attempt_id"] == attempt["id"], "restoration_original_identity")
    need(receipt["paid_result_sha256"] == consumer.digest(root["result_json"])
         and receipt["original_failure_sha256"] == consumer.digest(failure)
         and receipt["reconstructed_request_metadata_sha256"] == consumer.digest(fields["metadata"]), "restoration_original_hashes")
    hashes = {run[k + "_payload_ref"]: consumer.sha(evidence["physical_files"][run[k + "_payload_ref"]])
              for k in ("request", "response")}
    need(receipt["physical_sha256"] == hashes, "restoration_original_physical_hashes")
    need(type(receipt["provider_calls"]) is int and receipt["provider_calls"] == 0
         and receipt["original_paid_identity_preserved"] is True, "restoration_not_zero_send")
    for result in (root["result_json"], n["transport_results"][0]["result_json"]["result"]):
        need(result.get("llm_run_id") in (None, run["id"])
             and RESTORATION_KEY not in result["metadata"]
             and "execution_recovery" not in result["metadata"], "restoration_paid_history_changed")
    return {"receipt": deepcopy(receipt), "llm_run_created_at": run.get("created_at"),
            "original_attempt_created_at": attempt.get("created_at"),
            "telemetry_status": "row_restored_from_original_physical_pair_not_continuously_persisted"}


def need(ok, reason):
    if not ok:
        raise ValueError("semantic_selection:" + reason)


def frozen_v5_validator():
    """Read only a fixed, hash-pinned archive. No artifact chooses executable code."""
    root = Path(__file__).resolve().parent / "whole_semantic_history_v5"
    need(not root.is_symlink(), "historical_dependency_symlink")
    sources = {}
    for name, expected in V5_SOURCES.items():
        path = root / name
        need(not path.is_symlink() and not path.parent.is_symlink() and path.is_file(), "missing_frozen_v5_source")
        sources[name] = path.read_bytes()
        need(hashlib.sha256(sources[name]).hexdigest() == expected, "frozen_v5_source_changed:" + name)
    module = ModuleType("witnessed_whole_v5_selection_dependency")
    module.__file__ = str(root / "fanqie_short_semantic_review.py")
    exec(compile(sources["fanqie_short_semantic_review.py"], module.__file__, "exec"), module.__dict__)
    return module


def audit_v5_rows(report, *, semantic=None):
    """Pure row consistency audit. Native custody is established separately.

    Batch failure is retained. Individually valid body verdicts/reasons and valid
    title refusals become immutable inputs, not a new accepted batch.
    """
    semantic = semantic or frozen_v5_validator()
    p = report["request_payload"]
    need(report["schema"] == p["schema"] == "fanqie-short-semantic-stage.v5"
         and p["implementation_source_hashes"] == V5_SOURCES
         and report.get("called") is True and bool(report.get("error")), "original_failed_v5_required")
    binding = {k + "_sha256": consumer.digest(v) for k, v in p.items() if k not in {"binding", "binding_sha256"}}
    binding["system_prompt_sha256"] = consumer.sha(semantic.SYSTEM_PROMPT.encode())
    need(binding == p["binding"] and consumer.digest(binding) == p["binding_sha256"] == report["binding_sha256"], "parent_payload_binding")
    need(report["original_report"] == p["original_report"], "parent_original_report_changed")
    completion = {**deepcopy(report["provider_receipt"]), "content": report["raw_response"], "tool_calls": None}
    # Original route/physical text consistency; deliberately never calls _fresh.
    semantic._retention(p, completion, report["retained_payloads"])
    answer = consumer.strict_json(report["raw_response"])
    need(set(answer) == {"binding_sha256", "reviews"} and answer["binding_sha256"] == p["binding_sha256"], "parent_answer_binding")
    rows, items = answer["reviews"], p["reviews_requested"]
    need(len(rows) == len(items) and len({r["id"] for r in rows}) == len(rows)
         and {r["id"] for r in rows} == {i["id"] for i in items}, "parent_row_inventory")
    by_id = {r["id"]: r for r in rows}
    body, title, invalid = [], [], []
    for item in items:
        raw = by_id[item["id"]]
        record = {"id": item["id"], "raw_row": deepcopy(raw)}
        try:
            resolved, locations = semantic._resolve_response_row(raw, item, p)
            semantic._validate_row(resolved, item, p)
        except semantic.ReviewInputError as exc:
            invalid.append({**record, "item": deepcopy(item), "error": str(exc)})
        else:
            record.update(resolved_row=resolved, locations=locations)
            (title if item["target"] == "title" else body).append(record)
    need(invalid and invalid[0]["error"] == report["error"], "batch_error_not_reproduced")
    need(all(r["verdict"] == "uncertain" for r in report["reviews"])
         and len(report["reviews"]) == len(items), "stored_batch_state_changed")
    return {"parent_report_sha256": consumer.digest(report),
            "parent_raw_response_sha256": consumer.sha(report["raw_response"].encode()),
            "parent_payload_sha256": consumer.digest(p), "frozen_body_rows": body,
            "frozen_title_rows": title, "invalid_rows": invalid,
            "batch_error": report["error"], "authority": "historical_row_evidence_only"}


def _body_manifest(manifest):
    value = deepcopy(manifest)
    value.get("project", {}).pop("title", None)
    return value


async def load_selection_parent(session, settings, *, project, assembly, parent_artifact_id):
    """SELECT/files only; returns both original paid roots and immutable v5 rows.

    Does not authorize a tool call or create/consume a recovery. A changed title
    requires separate native current-title proof; only the body may be reused.
    """
    semantic = frozen_v5_validator()
    with session.no_autoflush:
        parent = await native._one(session, native.PlanningArtifactVersionModel,
                                   native.PlanningArtifactVersionModel.id == UUID(str(parent_artifact_id)))
        need(parent["project_id"] == project.id and parent["artifact_type"] == native.ARTIFACT_TYPE
             and parent["status"] == "failed", "parent_not_failed_original_owner")
        saved = parent["content"]
        p = saved["request_payload"]
        need(p["full_text"] == assembly.full_text and _body_manifest(p["manifest"]) == _body_manifest(assembly.manifest), "body_source_changed")
        wf = await native._one(session, native.WorkflowRunModel, native.WorkflowRunModel.id == parent["source_run_id"])
        need(wf["project_id"] == project.id and wf["status"] == "machine_blocked"
             and wf["workflow_type"] == "original_four_whole_semantic_review"
             and wf["scope_type"] == "project" and wf["scope_id"] == project.id, "parent_workflow_owner")
        meta = wf["metadata_json"]
        need(meta["artifact_id"] == str(parent["id"]) and meta["validation_error"] == saved["error"]
             and meta["source_manifest"] == p["manifest"] and meta["execution_scope_id"] == p["execution_scope_id"]
             and meta["semantic_binding_sha256"] == p["binding_sha256"], "parent_workflow_binding")
        evidence, _ = await native._load_artifact_native(session, settings, parent, wf, consumer)
        proof = consumer._native(p, evidence["completion"], evidence["native"], evidence["retained_payloads"], evidence["physical_files"], semantic)
        audit = audit_v5_rows(saved, semantic=semantic)
        desc = saved["protocol_recovery"]
        need(desc == p["recovery_parent"] == meta["protocol_recovery"], "original_v4_v5_chain_pointer")
        old = await native._one(session, native.PlanningArtifactVersionModel, native.PlanningArtifactVersionModel.id == UUID(desc["artifact_id"]))
        old_wf = await native._one(session, native.WorkflowRunModel, native.WorkflowRunModel.id == old["source_run_id"])
        old_evidence, _ = await native._load_artifact_native(session, settings, old, old_wf, consumer)
        old_proof = recovery.verify_parent_batch(native.json_safe(old), native.json_safe(old_wf), old_evidence, consumer=consumer, semantic=semantic)
        need(old_proof["descriptor"] == desc, "original_v4_parent_changed")
        return {"parent_artifact": native.json_safe(parent), "parent_workflow": native.json_safe(wf),
                "parent_report": deepcopy(saved), "parent_evidence": evidence, "parent_native_proof": proof,
                "original_v4_artifact": native.json_safe(old), "original_v4_workflow": native.json_safe(old_wf),
                "original_v4_evidence": old_evidence, "original_v4_proof": old_proof, "row_audit": audit,
                "authority": "source_evidence_only_no_dispatch"}


def verify_forced_native_call(fields, evidence, *, workflow, project_id, scope_id,
                              expected_workflow_status="completed"):
    """Reprove the actual single paid tool send and exact retained request/return."""
    c, n, retained = evidence["completion"], evidence["native"], evidence["retained_payloads"]
    need(all(isinstance(c.get(k), str) and c[k].strip()
             and not re.search(r"mock|fixture|unknown|synthetic|offline|stub|fake|fallback|test", c[k], re.I)
             for k in ("provider", "model_name")), "nonreal_tool_provider")
    need(expected_workflow_status in {"completed", "machine_blocked"}, "invalid_expected_workflow_status")
    need(workflow["project_id"] == project_id and workflow["status"] == expected_workflow_status
         and workflow["scope_type"] == "project" and workflow["scope_id"] == project_id,
         "tool_workflow_owner")
    run, root, attempts = n["llm_run"], n["operation"], n["attempts"]
    key = consumer.sha((scope_id + "\n" + fields["operation_id"] + ":sample:0").encode())
    need(root["key"] == key and root["status"] == "completed" and len(attempts) == 1, "single_original_tool_root")
    attempt = attempts[0]
    need(attempt["operation_key"] == key and attempt["status"] == "completed"
         and attempt["scope_id"] == root["scope_id"] == scope_id, "tool_attempt_binding")
    need(run["id"] == c["llm_run_id"] and run["workflow_run_id"] == workflow["id"]
         and run["project_id"] == project_id and run["prompt_template"] == fields["prompt_template"]
         and run["prompt_version"] == fields["prompt_version"]
         and run["logical_role"] == fields["logical_role"] == "critic", "tool_original_run_route")
    meta = c["metadata"]
    need(run["metadata_json"] == meta and "execution_recovery" not in meta and meta.get("llm_run_flushed") is True, "tool_original_metadata")
    need(all(meta.get(k) == v for k, v in fields["metadata"].items()), "tool_request_metadata_changed")
    effective = meta["effective_input_receipt"]
    need(effective["hash"] == root["input_hash"] == attempt["input_hash"] == run["prompt_hash"]
         and effective["tools_included"] is True and effective["model"] == c["model_name"], "tool_effective_input")
    origin, execution = meta["paid_call_origin"], meta["execution_receipt"]
    need(origin["schema"] == "paid-call-origin.v1" and origin["status"] == "recorded"
         and origin["project_id"] == project_id and origin["workflow_run_id"] == workflow["id"]
         and origin["logical_role"] == "critic" and origin["prompt_template"] == fields["prompt_template"]
         and origin["prompt_version"] == fields["prompt_version"] and origin["prompt_hash"] == effective["hash"], "tool_paid_origin")
    need(execution["scope_id"] == root["scope_id"] and execution["operation_key"] == key
         and execution["policy"] == "execution-budget.v1" and len(execution["attempts"]) == 1, "tool_execution_receipt")
    sent = execution["attempts"][0]
    need(sent["attempt_id"] == attempt["id"] and sent["status"] == "completed"
         and sent["effective_input_hash"] == effective["hash"] and sent["model"] == c["model_name"]
         and sent["reserved_tokens"] == attempt["reserved_tokens"]
         and isinstance(sent.get("wire_body_hash"), str) and len(sent["wire_body_hash"]) == 64, "tool_attempt_receipt")
    need(all(type(c.get(k)) is int and c[k] >= 0 and run[k] == c[k] for k in ("input_tokens", "output_tokens"))
         and c["input_tokens"] + c["output_tokens"] == attempt["actual_tokens"] > 0
         and type(attempt["reserved_tokens"]) is int and attempt["reserved_tokens"] > 0, "tool_charge")
    transports = n["transport_results"]
    need(len(transports) == 1 and transports[0]["status"] == "transport_result", "tool_transport_inventory")
    transport = transports[0]
    need(transport["key"] == consumer.sha(("transport-result.v1\n" + root["scope_id"] + "\n" + attempt["id"]).encode())
         and transport["scope_id"] == root["scope_id"] and transport["input_hash"] == root["input_hash"], "tool_transport_key")
    envelope = transport["result_json"]
    need(envelope["schema"] == "transport-result.v1" and envelope["operation_key"] == key
         and envelope["attempt_id"] == attempt["id"] and envelope["input_hash"] == root["input_hash"], "tool_transport_envelope")
    for result in (root["result_json"], envelope["result"]):
        need(all(result[k] == c[k] for k in ("content", "tool_calls", "provider", "model_name", "finish_reason", "input_tokens", "output_tokens"))
             and result.get("fallback_used") is False and not result.get("fallback_source"), "tool_native_result_changed")
        pm = result["metadata"]
        need(pm["paid_call_origin"] == origin and pm["execution_receipt"] == execution
             and pm["effective_input_receipt"]["hash"] == effective["hash"]
             and pm["effective_input_receipt"]["model"] == c["model_name"], "tool_native_receipts_changed")
    need(c["provider"] == run["provider"] and c["model_name"] == run["model_name"]
         and c["finish_reason"] == run["finish_reason"] and attempt["model_name"] == c["model_name"], "tool_run_result")
    for kind in ("request", "response"):
        ref = f"runtime/llm_payloads/{run['id']}.{kind}.json"
        need(run[kind + "_payload_ref"] == ref and consumer.strict_json(evidence["physical_files"][ref]) == retained[kind], "tool_physical_mismatch")
        doc = retained[kind]
        need(doc["llm_run_id"] == run["id"] and doc["project_id"] == project_id
             and doc["workflow_run_id"] == workflow["id"] and doc["kind"] == kind
             and doc["schema_version"] == "llm-payload.v1" and doc["replayable"] is True
             and doc["replayability_reasons"] == [], "tool_physical_owner")
    request, response = retained["request"], retained["response"]
    need(request["prompt_template"] == fields["prompt_template"] and request["prompt_version"] == fields["prompt_version"]
         and request["requested"]["tools"] == fields["tools"]
         and request["requested"]["tool_choice"] == fields["tool_choice"], "forced_tool_schema_changed")
    need(all(request["requested"].get(k) == fields[k] for k in ("max_tokens_override", "temperature_override", "model_tier"))
         and not request["requested"].get("response_format"), "tool_requested_parameters_changed")
    receipt = meta["prompt_payload_receipt"]
    need(receipt["enabled"] is True and receipt["status"] == "stored" and receipt["replayable"] is True
         and all(receipt[k + "_ref"] == run[k + "_payload_ref"] for k in ("request", "response")), "tool_retention_receipt")
    for role in ("system", "user"):
        block = request["messages"][role]
        need(block["text"] == fields[role + "_prompt"] and block["truncated"] is False
             and block["original_chars"] == len(block["text"]), "tool_prompt_changed")
    need(response["tool_calls"] == c["tool_calls"] and response["tool_call_count"] == 1
         and response["content"]["text"] == (c["content"] or "") and response["content"]["truncated"] is False, "physical_tool_calls_changed")
    need(effective["legacy_prompt_hash"] == consumer.sha((fields["system_prompt"] + "\n\n" + fields["user_prompt"]).encode())
         and response["fallback_used"] is False and response["provider"] == c["provider"]
         and response["model"] == c["model_name"] and response["finish_reason"] == c["finish_reason"], "tool_physical_route")
    need(request["logical_role"] == "critic" and response["logical_role"] == "critic"
         and effective["messages_source"] == "system_user"
         and all(response[k] == c[k] for k in ("input_tokens", "output_tokens")), "tool_physical_telemetry")
    proof = {"native_rows_sha256": consumer.digest(n),
             "physical_sha256": {ref: consumer.sha(data) for ref, data in evidence["physical_files"].items()}}
    restoration = _verify_physical_telemetry_restoration(fields, evidence, workflow)
    if restoration is not None:
        proof[RESTORATION_KEY] = restoration
    return proof


def verify_selection_tool_native(plan, evidence, *, workflow, selection):
    p = plan["parent_report"]["request_payload"]
    need(workflow["id"] != p["workflow_run_id"], "selection_needs_distinct_workflow")
    fields = selection.native_request_fields(plan, workflow_run_id=workflow["id"])
    proof = verify_forced_native_call(fields, evidence, workflow=workflow,
                                     project_id=p["project_id"], scope_id=p["execution_scope_id"])
    proof["selection_proof"] = selection.validate_selection(plan, evidence["completion"])
    return proof


@dataclass(frozen=True)
class SelectionContext:
    current_inputs: dict
    evidence: dict


def _record_manifest(row, *, workflow=False):
    document = row["metadata_json"] if workflow else row["content"]
    need(isinstance(document, dict), "unclassifiable_new_evidence")
    manifest = document.get("source_manifest")
    need(isinstance(manifest, dict) and isinstance(manifest.get("assembly_sha256"), str),
         "new_evidence_source_manifest_required")
    return manifest


async def current_selection_presence(session, *, project, assembly):
    """All statuses, including a WF with no artifact. Malformation cannot fall back."""
    with session.no_autoflush:
        for row in (*session.new, *session.dirty):
            if (isinstance(row, native.PlanningArtifactVersionModel) and row.project_id == project.id
                    and row.artifact_type in ALL_SELECTION_TYPES):
                raise ValueError("semantic_selection:pending_artifact")
            if (isinstance(row, native.WorkflowRunModel) and row.project_id == project.id
                    and row.workflow_type in ALL_SELECTION_TYPES):
                raise ValueError("semantic_selection:pending_workflow")
        artifacts = await native._rows(session, native.PlanningArtifactVersionModel,
            native.PlanningArtifactVersionModel.project_id == project.id,
            native.PlanningArtifactVersionModel.artifact_type.in_(ALL_SELECTION_TYPES))
        workflows = await native._rows(session, native.WorkflowRunModel,
            native.WorkflowRunModel.project_id == project.id,
            native.WorkflowRunModel.workflow_type.in_(ALL_SELECTION_TYPES))
    for records, is_workflow in ((artifacts, False), (workflows, True)):
        for row in records:
            # Missing/malformed manifest is itself relevant, never absent success.
            manifest = _record_manifest(row, workflow=is_workflow)
            if manifest["assembly_sha256"] == assembly.manifest["assembly_sha256"]:
                return True
    return False


def verify_once_census(evidence, *, descriptor, current_manifest, allow_failed_title_for_link=False):
    """Each leaf has exactly one WF and one artifact across ALL statuses."""
    result = {}
    for kind in NEW_ARTIFACT_TYPES:
        found = []
        for key, workflow in (("artifact_inventory", False), ("workflow_inventory", True)):
            matches = []
            for row in evidence[key]:
                if row["workflow_type" if workflow else "artifact_type"] != kind:
                    continue
                manifest = _record_manifest(row, workflow=workflow)
                doc = row["metadata_json"] if workflow else row["content"]
                parent = doc.get(SELECTION_PARENT_KEY)
                need(isinstance(parent, dict) and set(parent) == recovery.DESCRIPTOR_KEYS,
                     "new_evidence_parent_required")
                if parent["artifact_id"] != descriptor["artifact_id"]:
                    need(manifest["assembly_sha256"] != current_manifest["assembly_sha256"],
                         "unexplained_current_parent")
                    continue
                need(parent == descriptor and _body_manifest(manifest) == _body_manifest(current_manifest),
                     "new_evidence_parent_or_body_changed")
                matches.append(row)
            need(len(matches) == 1, "once_missing_or_multiple:" + kind + ":" + key)
            found.append(matches[0])
        artifact, wf = found
        need(artifact["schema_version"] == LEAF_SCHEMA_VERSION, "new_leaf_schema_version")
        statuses = ("failed", "machine_blocked") if kind == TITLE_ARTIFACT_TYPE and allow_failed_title_for_link else ("validated", "completed")
        need((artifact["status"], wf["status"]) == statuses
             and artifact["source_run_id"] == wf["id"], "new_evidence_incomplete:" + kind)
        need(artifact["project_id"] == wf["project_id"] == current_manifest["project_id"]
             and wf["scope_type"] == "project" and wf["scope_id"] == wf["project_id"],
             "new_evidence_owner:" + kind)
        need(artifact["content"]["source_manifest"] == wf["metadata_json"]["source_manifest"],
             "new_evidence_workflow_manifest")
        result[kind] = (artifact, wf)
    need(result[BODY_ARTIFACT_TYPE][1]["id"] != result[TITLE_ARTIFACT_TYPE][1]["id"], "shared_new_workflow")
    return result


def verify_parent_context(context, *, current_inputs):
    """Repeat historical native and physical proof, not cached proof/pass fields."""
    semantic = frozen_v5_validator()
    artifact, wf = context["parent_artifact"], context["parent_workflow"]
    saved, evidence = context["parent_report"], context["parent_evidence"]
    p = saved["request_payload"]
    need(saved == artifact["content"] and artifact["status"] == "failed"
         and wf["status"] == "machine_blocked" and artifact["source_run_id"] == wf["id"] == p["workflow_run_id"]
         and artifact["project_id"] == wf["project_id"] == str(current_inputs["project_id"])
         and wf["workflow_type"] == "original_four_whole_semantic_review"
         and wf["scope_type"] == "project" and wf["scope_id"] == wf["project_id"], "historical_parent_owner")
    need(evidence["request_payload"] == p and evidence["completion"]["content"] == saved["raw_response"], "historical_parent_payload")
    meta = wf["metadata_json"]
    need(meta["artifact_id"] == artifact["id"] and meta["validation_error"] == saved["error"]
         and meta["semantic_binding_sha256"] == p["binding_sha256"] and meta["source_manifest"] == p["manifest"]
         and meta["execution_scope_id"] == p["execution_scope_id"], "historical_parent_workflow_binding")
    need(current_inputs["full_text"] == p["full_text"]
         and _body_manifest(current_inputs["manifest"]) == _body_manifest(p["manifest"]), "current_body_changed")
    need(current_inputs["title"] != p["title"], "independent_title_must_be_new")
    for key in ("protagonist", "current_scope", "execution_scope_id", "unlock_line_ratio", "methodology_contract_mode"):
        need(current_inputs[key] == p[key], "body_contract_changed:" + key)
    old_contract, current_contract = deepcopy(p["approved_contract"]), deepcopy(current_inputs["approved_contract"])
    old_approval, current_approval = old_contract.pop("approval_evidence"), current_contract.pop("approval_evidence")
    need(old_contract == current_contract, "current_locked_contract_changed")
    for value in (old_approval, current_approval):
        value.pop("native_publishing_title_evidence", None)
        value.pop("title_revision_status", None)
    need(old_approval == current_approval, "current_canonical_authority_changed")
    parent_proof = consumer._native(p, evidence["completion"], evidence["native"],
                                    evidence["retained_payloads"], evidence["physical_files"], semantic)
    audit = audit_v5_rows(saved, semantic=semantic)
    old, old_wf, old_evidence = (context[k] for k in ("original_v4_artifact", "original_v4_workflow", "original_v4_evidence"))
    old_proof = recovery.verify_parent_batch(old, old_wf, old_evidence, consumer=consumer, semantic=semantic)
    need(saved["protocol_recovery"] == p["recovery_parent"] == meta["protocol_recovery"] == old_proof["descriptor"],
         "historical_v4_v5_pointer_changed")
    return audit, {"v4": old_proof, "v5": parent_proof}


async def load_current_selection_context(session, settings, *, project, assembly,
                                         original_report, current_inputs_factory):
    """Fresh native SELECT/physical evidence for both once-only typed leaves."""
    from bestseller.services import fanqie_short_semantic_evidence_selection as selection
    from bestseller.services import fanqie_short_title_semantic_judge as title_judge

    need(callable(current_inputs_factory), "fresh_inputs_factory_required")
    need(await current_selection_presence(session, project=project, assembly=assembly), "current_selection_evidence_missing")
    with session.no_autoflush:
        need(not any(isinstance(r, native.ProjectModel) and r.id == project.id
                     for r in (*session.new, *session.dirty)), "pending_current_project")
        stored = await native._one(session, native.ProjectModel, native.ProjectModel.id == project.id)
        fresh_project = native.ProjectModel(**stored)
        need(fresh_project.title == project.title == assembly.manifest["project"]["title"], "current_project_title_changed")
        artifacts = await native._rows(session, native.PlanningArtifactVersionModel,
            native.PlanningArtifactVersionModel.project_id == project.id,
            native.PlanningArtifactVersionModel.artifact_type.in_(ALL_SELECTION_TYPES))
        workflows = await native._rows(session, native.WorkflowRunModel,
            native.WorkflowRunModel.project_id == project.id,
            native.WorkflowRunModel.workflow_type.in_(ALL_SELECTION_TYPES))
        candidates = [a for a in artifacts if _record_manifest(a)["assembly_sha256"] == assembly.manifest["assembly_sha256"]]
        need(candidates, "selection_artifacts_missing")
        parents = [a["content"].get(SELECTION_PARENT_KEY) for a in candidates]
        need(all(isinstance(p, dict) and set(p) == recovery.DESCRIPTOR_KEYS for p in parents), "selection_parent_descriptor_required")
        descriptor = parents[0]
        need(all(p == descriptor for p in parents), "multiple_selection_parents")
        census = {"artifact_inventory": native.json_safe(artifacts), "workflow_inventory": native.json_safe(workflows)}
        has_link = any(a["artifact_type"] == LINK_ARTIFACT_TYPE for a in artifacts)
        leaves = verify_once_census(census, descriptor=descriptor, current_manifest=assembly.manifest,
                                   allow_failed_title_for_link=has_link)
        parent = await load_selection_parent(session, settings, project=fresh_project, assembly=assembly,
                                              parent_artifact_id=descriptor["artifact_id"])
        need(recovery.descriptor(parent["parent_artifact"]) == descriptor, "selection_original_parent_mismatch")
        title_artifact, title_wf = leaves[TITLE_ARTIFACT_TYPE]
        inputs = deepcopy(await current_inputs_factory(session, fresh_project, assembly, UUID(title_wf["id"])))
        report = original_report.to_dict() if hasattr(original_report, "to_dict") else original_report
        need(inputs["original_report"] == report and inputs["full_text"] == assembly.full_text
             and inputs["title"] == fresh_project.title and inputs["manifest"] == assembly.manifest
             and str(inputs["workflow_run_id"]) == title_wf["id"]
             and str(inputs["project_id"]) == str(project.id), "fresh_selection_inputs_mismatch")
        body_artifact, body_wf = leaves[BODY_ARTIFACT_TYPE]
        plan = selection.prepare_selection(parent["parent_report"])
        title_plan = title_judge.prepare_title_review(inputs, parent_descriptor=descriptor)
        bundles = {}
        for kind, expected, schema in ((BODY_ARTIFACT_TYPE, plan, selection.SCHEMA),
                                      (TITLE_ARTIFACT_TYPE, title_plan, title_judge.SCHEMA)):
            artifact, wf = leaves[kind]
            content = artifact["content"]
            need(content["schema"] == schema and content["request_payload"] == expected
                 and wf["metadata_json"]["plan_binding_sha256"] == expected["binding_sha256"], "current_selection_plan_mismatch")
            native_evidence, _ = await native._load_artifact_native(session, settings,
                {**artifact, "id": UUID(artifact["id"]), "project_id": UUID(artifact["project_id"])},
                {**wf, "id": UUID(wf["id"])}, consumer)
            bundles[kind] = {"artifact": artifact, "workflow": wf, "evidence": native_evidence}
        originals = await native._rows(session, native.PlanningArtifactVersionModel,
            native.PlanningArtifactVersionModel.project_id == project.id,
            native.PlanningArtifactVersionModel.artifact_type == native.ARTIFACT_TYPE)
        evidence = {**census, "descriptor": descriptor, "parent": parent, "leaves": bundles,
                    "original_artifact_inventory": native.json_safe(originals)}
        if has_link:
            links = [a for a in artifacts if a["artifact_type"] == LINK_ARTIFACT_TYPE]
            link_wfs = [w for w in workflows if w["workflow_type"] == LINK_ARTIFACT_TYPE]
            need(len(links) == len(link_wfs) == 1, "link_once_missing_or_multiple")
            evidence["title_contract_link"] = {"artifact": native.json_safe(links[0]),
                "workflow": native.json_safe(link_wfs[0]),
                "nonpaid_llm_runs": native.json_safe(await native._rows(session, native.LlmRunModel,
                    native.LlmRunModel.workflow_run_id == link_wfs[0]["id"]))}
        return SelectionContext(current_inputs=inputs, evidence=evidence)


def _title_reference(artifact, workflow, completion, error):
    return {"id": str(artifact["id"]), "workflow_id": str(workflow["id"]),
            "content_sha256": consumer.digest(artifact["content"]),
            "raw_tool_calls_sha256": consumer.digest(completion["tool_calls"]), "error": error}


async def prepare_current_title_contract_linkage(session, settings, *, project, assembly):
    """Read-only pre-write builder for exactly one independent nonpaid link."""
    from bestseller.services import fanqie_short_title_contract_linkage as linkage
    from bestseller.services import fanqie_short_title_semantic_judge as judge
    from bestseller.services.fanqie_short_semantic_readiness import _fresh_inputs

    with session.no_autoflush:
        need(not session.new and not session.dirty, "link_pending_changes")
        stored = await native._one(session, native.ProjectModel, native.ProjectModel.id == project.id)
        current = native.ProjectModel(**stored)
        need(current.title == project.title == assembly.manifest["project"]["title"], "link_current_title_changed")
        for model, kind_field in ((native.PlanningArtifactVersionModel, "artifact_type"), (native.WorkflowRunModel, "workflow_type")):
            need(not await native._rows(session, model, model.project_id == project.id,
                getattr(model, kind_field) == LINK_ARTIFACT_TYPE), "link_already_exists_any_status")
        titles = await native._rows(session, native.PlanningArtifactVersionModel,
            native.PlanningArtifactVersionModel.project_id == project.id,
            native.PlanningArtifactVersionModel.artifact_type == TITLE_ARTIFACT_TYPE)
        need(len(titles) == 1, "original_title_leaf_missing_or_ambiguous")
        artifact = titles[0]
        wf = await native._one(session, native.WorkflowRunModel, native.WorkflowRunModel.id == artifact["source_run_id"])
        need(artifact["status"] == "failed" and artifact["schema_version"] == LEAF_SCHEMA_VERSION
             and wf["workflow_type"] == TITLE_ARTIFACT_TYPE and wf["status"] == "machine_blocked"
             and artifact["project_id"] == wf["project_id"] == project.id, "link_original_failed_leaf_required")
        descriptor = artifact["content"][SELECTION_PARENT_KEY]
        need(descriptor == wf["metadata_json"][SELECTION_PARENT_KEY]
             and artifact["content"]["source_manifest"] == wf["metadata_json"]["source_manifest"] == assembly.manifest,
             "link_original_source_binding")
        parent = await load_selection_parent(session, settings, project=current, assembly=assembly,
            parent_artifact_id=descriptor["artifact_id"])
        need(recovery.descriptor(parent["parent_artifact"]) == descriptor, "link_original_parent_binding")
        inputs = await _fresh_inputs(session, current, assembly, wf["id"], settings=settings)
        plan = judge.prepare_title_review(inputs, parent_descriptor=descriptor)
        need(artifact["content"]["schema"] == judge.SCHEMA and artifact["content"]["request_payload"] == plan
             and wf["metadata_json"]["plan_binding_sha256"] == plan["binding_sha256"], "link_current_title_plan_mismatch")
        evidence, _ = await native._load_artifact_native(session, settings, artifact, wf, consumer)
        proof = linkage.prepare_title_contract_linkage(plan, evidence["completion"])
        fields = judge.native_request_fields(plan, workflow_run_id=str(wf["id"]))
        native_proof = verify_forced_native_call(fields, evidence, workflow=native.json_safe(wf),
            project_id=str(project.id), scope_id=inputs["execution_scope_id"], expected_workflow_status="machine_blocked")
        title_ref = _title_reference(artifact, wf, evidence["completion"], proof["original_failure"])
        content = {"schema": linkage.SCHEMA, "title_artifact": title_ref, SELECTION_PARENT_KEY: descriptor,
            "source_manifest": deepcopy(assembly.manifest), "execution_scope_id": inputs["execution_scope_id"],
            "linkage_proof": proof, "called": False, "provider_calls": 0}
        metadata = {"content": deepcopy(content), "title_artifact": deepcopy(title_ref),
            SELECTION_PARENT_KEY: deepcopy(descriptor), "source_manifest": deepcopy(assembly.manifest),
            "execution_scope_id": inputs["execution_scope_id"], "linkage_sha256": consumer.digest(proof)}
        return {"content": content, "workflow_metadata": metadata,
            "native_proof": native_proof,
            "workflow_id": str(uuid5(NAMESPACE_URL, "title-contract-linkage:" + str(artifact["id"]))),
            "artifact_id": str(uuid5(NAMESPACE_URL, "title-contract-linkage-artifact:" + str(artifact["id"])))}


def verify_title_contract_link(evidence, *, original_artifact, original_workflow, original_completion,
                               original_plan, current_inputs):
    from bestseller.services import fanqie_short_title_contract_linkage as linkage

    records = evidence["title_contract_link"]
    artifacts = [a for a in evidence["artifact_inventory"] if a["artifact_type"] == LINK_ARTIFACT_TYPE]
    workflows = [w for w in evidence["workflow_inventory"] if w["workflow_type"] == LINK_ARTIFACT_TYPE]
    need(len(artifacts) == len(workflows) == 1, "link_once_missing_or_multiple")
    a, w = artifacts[0], workflows[0]
    need(records["artifact"] == a and records["workflow"] == w and not records["nonpaid_llm_runs"], "link_nonpaid_inventory_changed")
    need(a["status"] == "validated" and a["schema_version"] == "link.1" and w["status"] == "completed"
         and w["current_step"] == "title_contract_references_linked" and a["source_run_id"] == w["id"]
         and a["project_id"] == w["project_id"] == str(current_inputs["project_id"])
         and w["scope_type"] == "project" and w["scope_id"] == w["project_id"], "link_native_owner_or_status")
    need(original_artifact["status"] == "failed" and original_workflow["status"] == "machine_blocked", "link_paid_failure_was_relabelled")
    original_id = str(original_artifact["id"])
    need(w["id"] == str(uuid5(NAMESPACE_URL, "title-contract-linkage:" + original_id))
         and a["id"] == str(uuid5(NAMESPACE_URL, "title-contract-linkage-artifact:" + original_id)), "link_once_identity")
    proof = linkage.prepare_title_contract_linkage(original_plan, original_completion)
    reference = _title_reference(original_artifact, original_workflow, original_completion, proof["original_failure"])
    content, meta = a["content"], w["metadata_json"]
    need(content["schema"] == linkage.SCHEMA and content["title_artifact"] == reference
         and content["linkage_proof"] == proof and content[SELECTION_PARENT_KEY] == evidence["descriptor"]
         and content["source_manifest"] == current_inputs["manifest"]
         and content["execution_scope_id"] == current_inputs["execution_scope_id"]
         and content["called"] is False and type(content["provider_calls"]) is int and content["provider_calls"] == 0,
         "link_current_source_proof_changed")
    need(meta["content"] == content and meta["title_artifact"] == reference
         and meta[SELECTION_PARENT_KEY] == evidence["descriptor"]
         and meta["source_manifest"] == current_inputs["manifest"]
         and meta["execution_scope_id"] == current_inputs["execution_scope_id"]
         and meta["linkage_sha256"] == consumer.digest(proof), "link_workflow_audit_changed")
    return proof, {"workflow_id": w["id"], "artifact_id": a["id"],
                   "content_sha256": consumer.digest(content), "authority": proof["authority"]}
