"""One v4 protocol failure -> v5 recovery. Pure proof, no dispatch or IO.

The frozen route identifies the seven witnessed v4 implementation bytes only
for parent evidence. It never authorizes current code or a new paid request.
"""

from copy import deepcopy
import hashlib
import json
import re


V4_SCHEMA = "fanqie-short-semantic-stage.v4"
V5_SCHEMA = "fanqie-short-semantic-stage.v5"
V4_SOURCES = {
    "fanqie_short_semantic_review.py": "f80c3580dc89dcbb8e2fb65fb6462ed032809b4dbca2dc720bfa472b22023bb2",
    "native_publication_title.py": "418ee2f3dc0fe41a58c9e01f490ae7ca17a6ea119f28323df090e52abad17c08",
    "native_title_operator/__init__.py": "981272dcf911e2c8b438770012e0abbac70dfffdafbcd5c94e5cf6636adab151",
    "native_title_operator/title_operator.py": "d06f7463820cb42447060cb9160ea381a89b7a2b9fb3c730b6f29142027f61a9",
    "native_title_operator/title_operator_native.py": "7e1e6e50902f33d8923e51661217aa1b2c1f78674b9ca0269ba9c9882a830bed",
    "commercial_request_purpose.py": "fbbc5c471412f363a2f9352f1b2284628798d9bf56fc3db6bc7319de8e0a3d25",
    "platform_title_workflow.py": "a4480b43659e8eecf566c3f100830e8430a8314e073ed00bb7cc168b8a975098",
}
V4_SYSTEM_SHA256 = "f9f3ef37e4be37503b9d4cc47b418f5f4acde19336ea48c978db905d9cfa695d"
DESCRIPTOR_KEYS = {"artifact_id", "workflow_id", "content_sha256", "error", "raw_response_sha256"}


def need(ok, reason):
    if not ok:
        raise ValueError("protocol_recovery:" + reason)


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return sha(encoded(value))


def sha(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class FrozenV4Route:
    SCHEMA = V4_SCHEMA
    PROMPT_VERSION = "stage.4"

    @staticmethod
    def native_request_fields(payload):
        binding = {k: v for k, v in payload["binding"].items() if k != "workflow_run_id_sha256"}
        return {"operation_id": payload["operation_namespace"] + ":" + digest(binding)}


def descriptor(artifact):
    content = artifact["content"]
    return {
        "artifact_id": str(artifact["id"]),
        "workflow_id": str(artifact["source_run_id"]),
        "content_sha256": digest(content),
        "error": content["error"],
        "raw_response_sha256": sha(content["raw_response"]),
    }


def verify_parent_batch(artifact, workflow, evidence, *, consumer, semantic):
    """Reproduce the actual batch failure; never reuse even individually good rows.

    Deliberately only quote_not_in_scope is recoverable here. Financial failures,
    provider truncation, valid rejection and other errors need separate policy.
    """
    saved, payload = artifact["content"], evidence["request_payload"]
    meta = workflow["metadata_json"]
    need(artifact["status"] == "failed" and saved["schema"] == V4_SCHEMA, "parent_not_failed_v4")
    need("protocol_recovery" not in saved and "protocol_recovery" not in meta, "nested_recovery")
    need(workflow["status"] == "machine_blocked", "parent_workflow_not_blocked")
    need(str(workflow["id"]) == str(artifact["source_run_id"]) == payload["workflow_run_id"], "parent_workflow_binding")
    need(str(workflow["project_id"]) == str(artifact["project_id"]) == payload["project_id"], "parent_project_binding")
    need(workflow["workflow_type"] in {"original_four_whole_semantic_review", "fanqie_whole_semantic_review"}, "parent_workflow_type")
    need(workflow["scope_type"] == "project" and str(workflow["scope_id"]) == payload["project_id"], "parent_workflow_scope")
    need(meta["execution_scope_id"] == payload["execution_scope_id"]
         and meta["source_manifest"] == payload["manifest"]
         and meta["semantic_binding_sha256"] == payload["binding_sha256"]
         and meta["artifact_id"] == str(artifact["id"])
         and meta["validation_error"] == saved["error"], "parent_workflow_metadata")
    need(saved["error"] == "quote_not_in_scope", "unsupported_parent_error")
    need(saved.get("called") is True, "parent_not_dispatched")
    need(payload["schema"] == V4_SCHEMA and payload["implementation_source_hashes"] == V4_SOURCES
         and meta["implementation"] == V4_SOURCES, "parent_frozen_implementation")
    binding = {k + "_sha256": digest(v) for k, v in payload.items() if k not in {"binding", "binding_sha256"}}
    binding["system_prompt_sha256"] = V4_SYSTEM_SHA256
    need(payload["binding"] == binding and payload["binding_sha256"] == digest(binding)
         and saved["binding_sha256"] == payload["binding_sha256"], "parent_payload_hash")
    need(saved["original_report"] == payload["original_report"]
         and saved["quote_location_receipt"] is None, "parent_batch_not_invalid")
    completion, retained = evidence["completion"], evidence["retained_payloads"]
    proof = consumer._native(payload, completion, evidence["native"], retained,
                             evidence["physical_files"], FrozenV4Route())
    need(completion["finish_reason"] in {"stop", "end_turn", "completed"}
         and completion.get("fallback_used") is False and not completion.get("fallback_source")
         and not completion.get("tool_calls"), "parent_not_original_complete")
    need(all(isinstance(completion.get(k), str) and completion[k].strip()
             and not re.search(r"mock|fixture|unknown|synthetic|offline|stub|fake|fallback|test", completion[k], re.I)
             for k in ("provider", "model_name")), "parent_nonreal_provider")
    need(saved["raw_response"] == completion["content"], "parent_raw_changed")
    request, response = retained["request"], retained["response"]
    need(set(retained) == {"request", "response"}, "parent_retained_inventory")
    pm = completion["metadata"]["prompt_payload_receipt"]
    need(completion["metadata"].get("llm_run_flushed") is True and pm.get("enabled") is True
         and pm.get("status") == "stored" and pm.get("replayable") is True, "parent_retention_receipt")
    for kind in ("request", "response"):
        need(pm[kind + "_ref"] == evidence["native"]["llm_run"][kind + "_payload_ref"], "parent_retention_ref")
    for kind, doc in retained.items():
        need(kind in {"request", "response"} and doc["schema_version"] == "llm-payload.v1"
             and doc["replayable"] is True and doc["replayability_reasons"] == []
             and doc["kind"] == kind and doc["llm_run_id"] == completion["llm_run_id"]
             and doc["project_id"] == payload["project_id"]
             and doc["workflow_run_id"] == payload["workflow_run_id"], "parent_retention_identity")
    need(request["prompt_version"] == "stage.4" and request["logical_role"] == "critic"
         and request["prompt_template"] == "fanqie_short_semantic_review", "parent_request_route")
    need(request["requested"]["max_tokens_override"] == 12000 and request["requested"]["temperature_override"] == 0
         and request["requested"]["model_tier"] == "strong"
         and request["effective"]["provider"] == completion["provider"]
         and request["effective"]["model"] == completion["model_name"], "parent_request_effective")
    need(response["fallback_used"] is False and response["provider"] == completion["provider"]
         and response["model"] == completion["model_name"]
         and response["finish_reason"] == completion["finish_reason"]
         and not response.get("tool_calls") and response.get("tool_call_count", 0) == 0, "parent_response_provider")
    wire = {k: deepcopy(payload[k]) for k in (
        "schema", "binding_sha256", "full_text", "title", "protagonist", "unlock_line_ratio",
        "methodology_contract_mode", "title_revision_status", "default_title_contract",
        "reviews_requested", "approval_verified", "title_provenance_verified",
        "title_provenance_route", "title_source_path",
    )}
    wire.update(deepcopy(payload["literary_projection"]))
    wire["density_thresholds"] = deepcopy(payload["original_report"].get("thresholds", {}))
    wire["review_limits"] = {k: deepcopy(payload["policy"][k]) for k in (
        "decisions", "max_review_rows", "max_density_events", "density", "authority")}
    messages = request["messages"]
    for kind, expected in (("user", encoded(wire)), ("system", messages["system"]["text"])):
        block = messages[kind]
        need(block["text"] == expected and block["truncated"] is False
             and block["original_chars"] == len(expected), "parent_request_text")
    need(sha(messages["system"]["text"]) == V4_SYSTEM_SHA256, "parent_system_text")
    need(completion["metadata"]["effective_input_receipt"]["legacy_prompt_hash"]
         == sha(messages["system"]["text"] + "\n\n" + messages["user"]["text"]), "parent_prompt_hash")
    block = response["content"]
    need(block["text"] == saved["raw_response"] and block["truncated"] is False
         and block["original_chars"] == len(block["text"]), "parent_response_text")
    origin = completion["metadata"]["paid_call_origin"]
    need(origin["status"] == "recorded" and origin["schema"] == "paid-call-origin.v1"
         and origin["prompt_version"] == "stage.4" and origin["logical_role"] == "critic"
         and origin["prompt_template"] == "fanqie_short_semantic_review"
         and origin["project_id"] == payload["project_id"] and origin["workflow_run_id"] == payload["workflow_run_id"]
         and origin["prompt_hash"] == completion["metadata"]["effective_input_receipt"]["hash"], "parent_paid_origin")
    answer = consumer.strict_json(saved["raw_response"])
    need(set(answer) == {"binding_sha256", "reviews"} and answer["binding_sha256"] == payload["binding_sha256"], "parent_answer_binding")
    items, rows = payload["reviews_requested"], answer["reviews"]
    need(len(rows) == len(items) and len({r["id"] for r in rows}) == len(rows)
         and {r["id"] for r in rows} == {i["id"] for i in items}, "parent_answer_coverage")
    by_id = {r["id"]: r for r in rows}
    errors = []
    for item in items:
        try:
            row, _ = semantic._resolve_response_row(by_id[item["id"]], item, payload)
            semantic._validate_row(row, item, payload)
        except semantic.ReviewInputError as exc:
            errors.append({"id": item["id"], "error": str(exc)})
        else:
            need(row["verdict"] != "confirmed", "valid_content_rejection")
    need(len(errors) == len(items) and errors and errors[0]["error"] == saved["error"], "parent_not_all_rows_protocol_invalid")
    expected = [{"id": i["id"], "code": i["code"], "target": i["target"],
                 "verdict": "uncertain", "reason": "无法判断：批次来源、回执或证据校验失败。"} for i in items]
    need(saved["reviews"] == expected, "parent_not_wholly_invalid")
    return {"descriptor": descriptor(artifact), "native_proof": proof, "row_errors": errors,
            "rows_reused": False}


def verify_recovery(chain, current, evidence, *, consumer, semantic):
    """Recheck both native sends and their unique source-bound parentage in memory."""
    parent, child = chain["parent_artifact"], chain["child_artifact"]
    old_wf, new_wf = chain["parent_workflow"], chain["child_workflow"]
    desc = chain["descriptor"]
    need(set(desc) == DESCRIPTOR_KEYS and desc == descriptor(parent), "descriptor_mismatch")
    need(child["content"]["protocol_recovery"] == new_wf["metadata_json"]["protocol_recovery"] == desc,
         "child_pointer_mismatch")
    need(current.get("recovery_parent") == desc, "paid_payload_pointer_mismatch")
    need(child["status"] == "validated" and child["content"]["error"] is None
         and child["content"]["schema"] == current["schema"] == V5_SCHEMA
         and semantic.SCHEMA == V5_SCHEMA and semantic.PROMPT_VERSION == "stage.5", "child_not_native_v5")
    need(child["id"] != parent["id"] and new_wf["id"] != old_wf["id"]
         and child["source_run_id"] == new_wf["id"] == current["workflow_run_id"]
         and child["project_id"] == parent["project_id"] == new_wf["project_id"] == current["project_id"], "child_owner")
    need(new_wf["status"] == "completed" and new_wf["scope_type"] == "project"
         and new_wf["scope_id"] == current["project_id"]
         and new_wf["workflow_type"] in {"original_four_whole_semantic_review", "fanqie_whole_semantic_review"}, "child_workflow")
    nm = new_wf["metadata_json"]
    need(nm["execution_scope_id"] == current["execution_scope_id"]
         and nm["source_manifest"] == current["manifest"]
         and nm["semantic_binding_sha256"] == current["binding_sha256"], "child_workflow_binding")
    need(child["content"]["request_payload"] == current
         and child["content"]["raw_response"] == evidence["completion"]["content"], "child_result_binding")
    old = parent["content"]["request_payload"]
    for key in ("project_id", "current_scope", "full_text", "title", "protagonist", "manifest",
                "approved_contract", "execution_scope_id", "unlock_line_ratio", "methodology_contract_mode"):
        need(old[key] == current[key], "changed_parent_binding:" + key)
    # v5 adds the complete opening report; no old grade/critical/threshold changes.
    for key, value in old["original_report"].items():
        need(current["original_report"].get(key) == value, "changed_original_report:" + key)
    need(old["operation_namespace"] == "fanqie-whole-semantic-v4"
         and current["operation_namespace"] == "fanqie-whole-semantic-v5", "invalid_v4_v5_operation_route")
    need(chain["parent_evidence"]["completion"]["llm_run_id"] != evidence["completion"]["llm_run_id"], "reused_parent_paid_run")
    allowed = {parent["id"], child["id"]}
    seen = []
    for row in chain["artifact_inventory"]:
        if row["project_id"] != current["project_id"]:
            continue
        content = row["content"]
        payload = content["request_payload"]
        if payload["full_text"] == current["full_text"] and payload["title"] == current["title"]:
            need(row["id"] in allowed, "extra_current_artifact")
            seen.append(row["id"])
            need(row == (parent if row["id"] == parent["id"] else child), "inventory_row_changed")
        recovery = content.get("protocol_recovery")
        if recovery is not None and recovery.get("artifact_id") == parent["id"]:
            need(row["id"] == child["id"], "second_recovery")
    need(len(seen) == 2 and set(seen) == allowed, "missing_parent_or_child_inventory")
    seen_wf = []
    for row in chain["workflow_inventory"]:
        meta = row["metadata_json"] or {}
        if row["id"] in {old_wf["id"], new_wf["id"]}:
            need(row == (old_wf if row["id"] == old_wf["id"] else new_wf), "workflow_inventory_changed")
            seen_wf.append(row["id"])
        rec = meta.get("protocol_recovery")
        if rec is not None and rec.get("artifact_id") == parent["id"]:
            need(row["id"] == new_wf["id"], "second_recovery_workflow")
        manifest = meta.get("source_manifest") or {}
        if (row["project_id"] == current["project_id"]
                and row["workflow_type"] in {"original_four_whole_semantic_review", "fanqie_whole_semantic_review"}
                and manifest.get("assembly_sha256") == current["manifest"]["assembly_sha256"]
                and (manifest.get("project") or {}).get("title") == current["title"]):
            need(row["id"] in {old_wf["id"], new_wf["id"]}, "extra_current_workflow")
    need(len(seen_wf) == 2 and set(seen_wf) == {old_wf["id"], new_wf["id"]}, "workflow_inventory_missing")
    proof = verify_parent_batch(parent, old_wf, chain["parent_evidence"], consumer=consumer, semantic=semantic)
    return {"descriptor": deepcopy(desc), "parent_native_proof": proof["native_proof"],
            "parent_row_errors": proof["row_errors"], "rows_reused": False}
