"""Pure, exact fallback-clone history proof; no database or provider."""

from copy import deepcopy
import hashlib
import json
from uuid import uuid4

import pytest

from bestseller.services.rewrite_fallback_history import build_fallback_clone_retirement_receipt

pytestmark = pytest.mark.unit


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def wrap(text):
    return {"text": text, "original_chars": len(text), "truncated": False}


@pytest.fixture
def case():
    project, chapter, parent_id, draft_id, task_id, run_id, workflow = [str(uuid4()) for _ in range(7)]
    original = "海洋调查员留下完整观测记录。潮汐并未停止。\n\n另一位研究员接走了样本，记录留在桌上。"
    clone = '<!-- rewrite-chapter-fallback project="ocean-observations" chapter=7 reason="rewriter-llm-unavailable" -->\n\n' + original
    task = {
        "id": task_id, "project_id": project, "trigger_source_id": chapter,
        "trigger_type": "chapter_review", "status": "completed", "attempts": 2,
        "instructions": "Repair the issue supported by the quoted report.",
        "rewrite_strategy": "chapter_coherence_bridge_rewrite",
        "metadata_json": {
            "draft_id": parent_id, "parent_draft_id": parent_id, "parent_draft_hash": sha(original),
            "candidate_chapter_draft_id": draft_id, "rewritten_chapter_draft_id": draft_id,
            "candidate_body_sha256": sha(clone), "candidate_llm_run_id": run_id,
        },
    }
    source = {"schema": "review-rewrite-adopted-source.v1", "prior": "original retained sources"}
    quoted_task = {k: task[k] for k in ("instructions", "rewrite_strategy")}
    user = dump(source) + "\n\n引用的 review_task JSON：\n" + dump(quoted_task) + (
        "\n\n当前完整草稿（引用的来源正文）：\n" + original
        + "\n\n请输出修订后的整章正文，包含全部未改动段落。"
    )
    model, h = "openai/fixture-editor", sha("original effective input")
    run = {
        "id": run_id, "project_id": project, "workflow_run_id": workflow,
        "logical_role": "editor", "prompt_template": "chapter_rewrite",
        "prompt_version": "3.1-lossless-source-time", "model_name": model,
        "provider": "openai", "prompt_hash": h, "finish_reason": "stop",
        "request_payload_ref": f"runtime/llm_payloads/{run_id}.request.json",
        "response_payload_ref": f"runtime/llm_payloads/{run_id}.response.json",
        "metadata_json": {
            "rewrite_task_id": task_id, "project_slug": "ocean-observations", "chapter_number": 7,
            "effective_input_receipt": {"schema": "effective-input.v1", "hash": h, "model": model},
            "execution_receipt": {
                "scope_id": "original-budget-scope", "operation_key": sha("original-operation"),
                "attempts": [{"attempt_id": "original-send", "model": model, "effective_input_hash": h}],
            },
            "review_source_receipt": {"source_complete": True, "source_sha256": sha(dump(source))},
            "paid_call_origin": {
                "schema": "paid-call-origin.v1", "status": "recorded", "project_id": project,
                "workflow_run_id": workflow, "logical_role": "editor", "prompt_hash": h,
                "prompt_template": "chapter_rewrite", "prompt_version": "3.1-lossless-source-time",
            },
        },
    }
    common = {"schema_version": "llm-payload.v1", "replayable": True,
              "llm_run_id": run_id, "project_id": project, "workflow_run_id": workflow,
              "logical_role": "editor"}
    request = {**common, "kind": "request", "prompt_template": run["prompt_template"],
               "prompt_version": run["prompt_version"], "effective": {"model": model},
               "messages": {"source": "system_user", "replayable": True,
                            "system": wrap("Preserve adopted facts."), "user": wrap(user)}}
    response = {**common, "kind": "response", "model": model, "provider": "openai",
                "finish_reason": "stop", "fallback_used": False, "fallback_source": None,
                "tool_call_count": 0, "content": wrap("样本已带走。")}
    return {
        "draft": {"id": draft_id, "project_id": project, "chapter_id": chapter,
                  "version_no": 9, "content_md": clone, "llm_run_id": run_id,
                  "promotion_state": "candidate", "promoted_at": None},
        "parent": {"id": parent_id, "project_id": project, "chapter_id": chapter,
                   "version_no": 8, "content_md": original},
        "task": task, "llm_run": run,
        "physical_request": {"reference": run["request_payload_ref"], "raw_json": dump(request)},
        "physical_response": {"reference": run["response_payload_ref"], "raw_json": dump(response)},
        # A later genuine working draft is a CAS expectation, not a retirement target.
        "expected_current_draft_id": str(uuid4()),
        "target_executor_policy": {"policy_id": "reject-short-without-cloning", "source_sha256": sha("new executor")},
    }


def alter_physical(case, kind, mutate):
    field = case[f"physical_{kind}"]
    value = json.loads(field["raw_json"])
    mutate(value)
    field["raw_json"] = dump(value)


def update_clone(case, body):
    case["draft"]["content_md"] = body
    case["task"]["metadata_json"]["candidate_body_sha256"] = sha(body)


def test_exact_proof_is_idempotent_and_never_changes_current_grade_spend_or_rows(case):
    before = deepcopy(case)
    result = build_fallback_clone_retirement_receipt(**case)
    assert result == build_fallback_clone_retirement_receipt(**case)
    assert case == before and result["scope"] == "selection_only"
    assert result["draft_id"] == case["draft"]["id"]
    assert result["expected_current_draft_id"] != result["draft_id"]
    for key in ("adoption_allowed", "pointer_change_allowed", "grade_reuse_allowed",
                "new_credit_allowed", "attempt_reset_allowed", "workflow_retirement_allowed",
                "provider_dispatch_allowed"):
        assert result[key] is False
    assert result["request_file_sha256"] == sha(case["physical_request"]["raw_json"])
    assert result["response_file_sha256"] == sha(case["physical_response"]["raw_json"])
    assert result["parent_body_sha256"] == sha(case["parent"]["content_md"])
    assert result["raw_body_sha256"] == sha("样本已带走。")
    assert result["declared_attempt_ids"] == ["original-send"]
    assert any("not recomputed" in item for item in result["limitations"])
    assert any("external verification" in item for item in result["limitations"])
    json.dumps(result, allow_nan=False)


def test_only_exact_existing_quote_normalization_is_accepted(case):
    body = case["draft"]["content_md"].replace('"ocean-observations"', '“ocean-observations”').replace(
        '"rewriter-llm-unavailable"', '“rewriter-llm-unavailable”')
    update_clone(case, body)
    assert build_fallback_clone_retirement_receipt(**case)["status"] == "proven"


@pytest.mark.parametrize("mode", ["changed_body", "no_wrapper", "wrong_slug", "wrong_number",
                                  "wrong_reason", "nested_wrapper", "heading_change", "leading_space"])
def test_real_rewrites_and_nonexact_wrappers_never_qualify(case, mode):
    body = case["draft"]["content_md"]
    if mode == "changed_body":
        body += "\n\n真实新增行动，保留这份新工作稿。"
    elif mode == "no_wrapper":
        body = case["parent"]["content_md"]
    elif mode == "wrong_slug":
        body = body.replace("ocean-observations", "different-project")
    elif mode == "wrong_number":
        body = body.replace("chapter=7", "chapter=8")
    elif mode == "wrong_reason":
        body = body.replace("rewriter-llm-unavailable", "user-authored-note")
    elif mode == "nested_wrapper":
        body = body.split("\n\n", 1)[0] + "\n\n" + body
    elif mode == "heading_change":
        body = body.replace("\n\n海洋", "\n\n# 第7章 新标题\n\n海洋")
    else:
        body = " " + body
    update_clone(case, body)
    with pytest.raises(ValueError, match="not_exact_fallback_clone"):
        build_fallback_clone_retirement_receipt(**case)


@pytest.mark.parametrize("row,key,value", [
    ("draft", "project_id", str(uuid4())), ("parent", "chapter_id", str(uuid4())),
    ("task", "trigger_source_id", str(uuid4())), ("draft", "llm_run_id", str(uuid4())),
    ("llm_run", "project_id", str(uuid4())), ("llm_run", "prompt_hash", sha("other input")),
    ("draft", "version_no", 7), ("task", "status", "pending"), ("task", "attempts", 0),
    ("draft", "promotion_state", "promoted"), ("draft", "promoted_at", "original adoption time"),
])
def test_exact_scope_history_and_adoption_evidence_fails_closed(case, row, key, value):
    case[row][key] = value
    with pytest.raises(ValueError):
        build_fallback_clone_retirement_receipt(**case)


@pytest.mark.parametrize("key", ["draft_id", "parent_draft_id", "parent_draft_hash",
                                  "candidate_chapter_draft_id", "rewritten_chapter_draft_id",
                                  "candidate_body_sha256", "candidate_llm_run_id"])
def test_task_cannot_be_rebound_to_current_or_another_result(case, key):
    case["task"]["metadata_json"][key] = str(uuid4())
    with pytest.raises(ValueError):
        build_fallback_clone_retirement_receipt(**case)


@pytest.mark.parametrize("kind,field,value", [
    ("request", "project_id", str(uuid4())), ("request", "llm_run_id", str(uuid4())),
    ("response", "workflow_run_id", str(uuid4())), ("response", "model", "another-model"),
    ("request", "prompt_template", "chapter_review"), ("request", "prompt_version", "new input"),
    ("response", "fallback_used", True), ("response", "finish_reason", "length"),
    ("response", "tool_call_count", 1), ("response", "replayable", False),
])
def test_physical_original_binding_cannot_borrow_unrelated_paid_return(case, kind, field, value):
    alter_physical(case, kind, lambda v: v.update({field: value}))
    with pytest.raises(ValueError):
        build_fallback_clone_retirement_receipt(**case)


def test_immutable_file_bytes_are_hashed_not_reserialized(case):
    case["physical_request"]["raw_json"] = "\n" + case["physical_request"]["raw_json"]
    result = build_fallback_clone_retirement_receipt(**case)
    assert result["request_file_sha256"] == sha(case["physical_request"]["raw_json"])


@pytest.mark.parametrize("mode", ["ref", "duplicate_key", "truncated", "wrong_length", "empty", "not_short"])
def test_missing_or_partial_physical_proof_fails(case, mode):
    if mode == "ref":
        case["physical_response"]["reference"] = "unrelated.response.json"
    elif mode == "duplicate_key":
        raw = case["physical_response"]["raw_json"]
        case["physical_response"]["raw_json"] = raw[:-1] + ',"kind":"response"}'
    elif mode == "truncated":
        alter_physical(case, "response", lambda v: v["content"].update(truncated=True))
    elif mode == "wrong_length":
        alter_physical(case, "response", lambda v: v["content"].update(original_chars=999))
    elif mode == "empty":
        alter_physical(case, "response", lambda v: v.update(content=wrap("")))
    else:
        alter_physical(case, "response", lambda v: v.update(content=wrap(case["parent"]["content_md"] + "新行动。")))
    with pytest.raises(ValueError):
        build_fallback_clone_retirement_receipt(**case)


@pytest.mark.parametrize("mode", ["parent", "instructions", "strategy", "source_hash", "attempt_hash", "operation", "origin"])
def test_request_and_recorded_operation_receipts_must_match(case, mode):
    if mode == "parent":
        case["parent"]["content_md"] += "用户新改动。"
        case["task"]["metadata_json"]["parent_draft_hash"] = sha(case["parent"]["content_md"])
    elif mode in ("instructions", "strategy"):
        case["task"]["instructions" if mode == "instructions" else "rewrite_strategy"] += "changed"
    else:
        meta = case["llm_run"]["metadata_json"]
        if mode == "source_hash":
            meta["review_source_receipt"]["source_sha256"] = sha("different source")
        elif mode == "attempt_hash":
            meta["execution_receipt"]["attempts"][0]["effective_input_hash"] = sha("different input")
        elif mode == "operation":
            meta["execution_receipt"]["operation_key"] = "unknown"
        else:
            meta["paid_call_origin"]["status"] = "unknown"
    with pytest.raises(ValueError):
        build_fallback_clone_retirement_receipt(**case)
