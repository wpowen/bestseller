"""Pure, closed-target proof of a historical fallback clone.

Inputs are caller-trusted, JSON-serialized original database rows. Physical
files use {reference, raw_json}; this module never reads storage or the DB.
Only the exact known wrapper plus an unchanged parent body qualifies. Heading
rewrites, broad sanitizer equivalence and merely similar bodies do not.
"""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
import re
from uuid import UUID

SCHEMA = "rewrite-fallback-clone-retirement.v1"


def _require(value, code):
    if not value:
        raise ValueError(f"fallback_history:{code}")


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def _hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sha(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _uuid(value):
    _require(isinstance(value, str) and str(UUID(value)) == value, "uuid")
    return value


def _unique(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result, "duplicate_json_key")
        result[key] = value
    return result


def _constant(_value):
    raise ValueError("fallback_history:nonfinite_json")


def _file(value, reference, kind):
    _require(isinstance(value, Mapping) and set(value) == {"reference", "raw_json"}, "physical_shape")
    _require(isinstance(reference, str) and reference and value["reference"] == reference,
             "physical_reference")
    raw = value["raw_json"]
    _require(isinstance(raw, str) and raw.strip(), "physical_bytes")
    data = json.loads(raw, object_pairs_hook=_unique, parse_constant=_constant)
    _json(data)  # Includes exponent overflow, not caught by parse_constant.
    _require(isinstance(data, dict) and data.get("schema_version") == "llm-payload.v1"
             and data.get("kind") == kind and data.get("replayable") is True, "physical_protocol")
    return data, _hash(raw)


def _text(wrapper):
    _require(isinstance(wrapper, Mapping) and wrapper.get("truncated") is False, "physical_truncation")
    text = wrapper.get("text")
    _require(isinstance(text, str) and type(wrapper.get("original_chars")) is int
             and wrapper["original_chars"] == len(text), "physical_text_length")
    return text


def build_fallback_clone_retirement_receipt(
    *, draft, parent, task, llm_run, physical_request, physical_response,
    expected_current_draft_id, target_executor_policy,
) -> dict:
    """Prove selection-only retirement; never authorize pointer/grade/credit changes.

    Row field names are the ORM's serialized names, including metadata_json.
    target_executor_policy is {policy_id: nonempty str, source_sha256: SHA256}.
    expected_current_draft_id is only an operator CAS expectation: this pure
    function does not verify the actual current pointer or formal adoption.
    """
    try:
        return _build(
            draft, parent, task, llm_run, physical_request, physical_response,
            expected_current_draft_id, target_executor_policy,
        )
    except (TypeError, KeyError, AttributeError, OverflowError, RecursionError, UnicodeError):
        raise ValueError("fallback_history:invalid_serialized_input") from None


def _build(draft, parent, task, run, request_file, response_file, expected_current, policy):
    rows = {"draft": draft, "parent": parent, "task": task, "llm_run": run}
    _require(all(isinstance(row, Mapping) for row in rows.values()), "row_shape")
    row_hashes = {key: _hash(_json(row)) for key, row in rows.items()}
    for row in rows.values():
        _uuid(row["id"])
    _uuid(expected_current)
    _require(isinstance(policy, Mapping) and set(policy) == {"policy_id", "source_sha256"}
             and isinstance(policy["policy_id"], str) and policy["policy_id"].strip()
             and _sha(policy["source_sha256"]), "target_policy")
    project, chapter = _uuid(draft["project_id"]), _uuid(draft["chapter_id"])
    _require(parent["project_id"] == task["project_id"] == run["project_id"] == project,
             "project_binding")
    _require(parent["chapter_id"] == task["trigger_source_id"] == chapter
             and task["trigger_type"] == "chapter_review", "chapter_binding")
    _require(draft["id"] != parent["id"] and type(draft["version_no"]) is int
             and type(parent["version_no"]) is int
             and draft["version_no"] > parent["version_no"] >= 1, "version_binding")
    _require(draft.get("promotion_state") in {"candidate", "under_review", "rejected", "superseded"}
             and draft.get("promoted_at") is None, "draft_selection_state")
    _require(task["status"] == "completed" and type(task["attempts"]) is int
             and task["attempts"] >= 1, "task_not_completed")
    body, original = draft["content_md"], parent["content_md"]
    _require(isinstance(body, str) and isinstance(original, str) and original.strip(), "body")
    meta, run_meta = task["metadata_json"], run["metadata_json"]
    _require(isinstance(meta, Mapping) and isinstance(run_meta, Mapping), "metadata")
    _require(meta.get("draft_id") == meta.get("parent_draft_id") == parent["id"]
             and meta.get("parent_draft_hash") == _hash(original), "task_parent_binding")
    _require(meta.get("candidate_chapter_draft_id") == meta.get("rewritten_chapter_draft_id") == draft["id"]
             and meta.get("candidate_body_sha256") == _hash(body), "task_result_binding")
    _require(draft["llm_run_id"] == meta.get("candidate_llm_run_id") == run["id"]
             and run_meta.get("rewrite_task_id") == task["id"], "run_task_binding")
    _require(run["logical_role"] == "editor" and run["prompt_template"] == "chapter_rewrite"
             and isinstance(run["prompt_version"], str) and run["prompt_version"], "run_route")
    model, h = run["model_name"], run["prompt_hash"]
    _require(isinstance(model, str) and model and _sha(h), "run_input")
    effective, execution, origin = (run_meta.get(k) for k in (
        "effective_input_receipt", "execution_receipt", "paid_call_origin"
    ))
    _require(isinstance(effective, Mapping) and effective.get("schema") == "effective-input.v1"
             and effective.get("hash") == h and effective.get("model") == model, "effective_binding")
    _require(isinstance(execution, Mapping) and isinstance(execution.get("scope_id"), str)
             and execution["scope_id"] and _sha(execution.get("operation_key")), "operation_binding")
    attempts = execution.get("attempts")
    _require(isinstance(attempts, list) and attempts, "declared_attempts")
    seen = set()
    for attempt in attempts:
        _require(isinstance(attempt, Mapping) and isinstance(attempt.get("attempt_id"), str)
                 and attempt["attempt_id"] and attempt["attempt_id"] not in seen,
                 "declared_attempt_identity")
        seen.add(attempt["attempt_id"])
        _require(attempt.get("model") == model and attempt.get("effective_input_hash") == h,
                 "declared_attempt_binding")
    _require(isinstance(origin, Mapping) and origin.get("schema") == "paid-call-origin.v1"
             and origin.get("status") == "recorded" and origin.get("prompt_hash") == h,
             "recorded_origin")
    for key in ("project_id", "workflow_run_id", "logical_role", "prompt_template", "prompt_version"):
        _require(origin.get(key) == run.get(key), "origin_binding")
    req, req_hash = _file(request_file, run["request_payload_ref"], "request")
    resp, resp_hash = _file(response_file, run["response_payload_ref"], "response")
    for physical in (req, resp):
        _require(physical.get("llm_run_id") == run["id"]
                 and physical.get("project_id") == project
                 and physical.get("workflow_run_id") == run["workflow_run_id"]
                 and physical.get("logical_role") == "editor", "physical_run_binding")
    _require(req.get("prompt_template") == run["prompt_template"]
             and req.get("prompt_version") == run["prompt_version"], "physical_route")
    _require(isinstance(req.get("effective"), Mapping) and req["effective"].get("model") == model
             and resp.get("model") == model and resp.get("provider") == run["provider"], "physical_model")
    _require(resp.get("fallback_used") is False and resp.get("fallback_source") is None
             and resp.get("finish_reason") == run.get("finish_reason") == "stop"
             and resp.get("tool_call_count") == 0, "physical_paid_return")
    messages = req.get("messages")
    _require(isinstance(messages, Mapping) and messages.get("replayable") is True
             and messages.get("source") == "system_user", "physical_messages")
    system, user = _text(messages.get("system")), _text(messages.get("user"))
    raw = _text(resp.get("content"))
    _require(raw.strip() and raw != body and raw != original and len(raw) < len(original), "raw_not_short_distinct")

    # Validate the exact renderer's quoted task and draft sections, not arbitrary
    # substring coincidences inside the adopted source JSON.
    sections = (
        ("引用的 review_task JSON：\n", "当前完整草稿（引用的来源正文）：\n",
         "\n\n请输出修订后的整章正文，包含全部未改动段落。"),
        ("Quoted review_task JSON:\n", "Current complete draft (quoted source text):\n",
         "\n\nReturn the entire revised chapter, including unchanged paragraphs."),
    )
    matches = [item for item in sections if user.count(item[0]) == user.count(item[1]) == 1
               and user.endswith(item[2])]
    _require(len(matches) == 1, "request_sections")
    task_marker, body_marker, suffix = matches[0]
    quoted, end = json.JSONDecoder(object_pairs_hook=_unique, parse_constant=_constant).raw_decode(
        user.split(task_marker, 1)[1]
    )
    _require(isinstance(quoted, dict) and set(quoted) == {"instructions", "rewrite_strategy"}
             and quoted["instructions"] == task["instructions"]
             and quoted["rewrite_strategy"] == task["rewrite_strategy"], "physical_task")
    request_body = user.split(body_marker, 1)[1][:-len(suffix)]
    _require(request_body == original, "physical_parent_body")
    source, end = json.JSONDecoder(object_pairs_hook=_unique, parse_constant=_constant).raw_decode(user)
    source_receipt = run_meta.get("review_source_receipt")
    _require(isinstance(source_receipt, Mapping) and source_receipt.get("source_complete") is True
             and source_receipt.get("source_sha256") == _hash(_json(source)), "physical_source_hash")
    slug, number = run_meta.get("project_slug"), run_meta.get("chapter_number")
    _require(isinstance(slug, str) and slug and not any(c in slug for c in '\n\r"“”<>')
             and type(number) is int and number >= 1, "wrapper_context")
    marker = f'<!-- rewrite-chapter-fallback project="{slug}" chapter={number} reason="rewriter-llm-unavailable" -->'
    curved_marker = marker.replace(f'"{slug}"', f'“{slug}”').replace(
        '"rewriter-llm-unavailable"', '“rewriter-llm-unavailable”')
    wrappers = [prefix for prefix in (marker, curved_marker) if body == prefix + "\n\n" + original]
    _require(len(wrappers) == 1, "not_exact_fallback_clone")
    receipt = {
        "schema": SCHEMA, "status": "proven", "scope": "selection_only",
        "project_id": project, "chapter_id": chapter, "draft_id": draft["id"],
        "parent_draft_id": parent["id"], "task_id": task["id"], "llm_run_id": run["id"],
        "expected_current_draft_id": expected_current, "target_executor_policy": dict(policy),
        "source_row_sha256": row_hashes, "draft_body_sha256": _hash(body),
        "parent_body_sha256": _hash(original), "raw_body_sha256": _hash(raw),
        "raw_characters": len(raw), "parent_characters": len(original),
        "shortness_basis": "fewer_unicode_characters_than_exact_parent; not_a_length_grade",
        "wrapper": wrappers[0], "request_ref": request_file["reference"],
        "response_ref": response_file["reference"], "request_file_sha256": req_hash,
        "response_file_sha256": resp_hash, "system_sha256": _hash(system), "user_sha256": _hash(user),
        "execution_scope_id": execution["scope_id"], "operation_key": execution["operation_key"],
        "effective_input_hash": h, "model_name": model,
        "declared_attempt_ids": sorted(seen), "adoption_allowed": False,
        "pointer_change_allowed": False, "grade_reuse_allowed": False,
        "new_credit_allowed": False, "attempt_reset_allowed": False,
        "workflow_retirement_allowed": False, "provider_dispatch_allowed": False,
        "limitations": [
            "Caller authenticates original rows and physical bytes; no storage lookup performed.",
            "Effective hash is recorded-origin/run/declared-attempt binding, not recomputed wire input.",
            "All actual sends, financial settlement and formal adoption require external verification.",
            "Expected current is an operator CAS expectation, not a verified current-pointer observation.",
            "No renderer-heading or general-sanitizer equivalence is accepted.",
        ],
    }
    receipt["receipt_id"] = _hash(_json(receipt))
    return receipt
