"""Exact whole-story semantic purpose; synthetic paid proof, no DB/provider."""

from copy import deepcopy

import pytest
from test_commercial_request_purpose import add_run, add_transport, paid, sha
from test_commercial_request_purpose import case as case

from bestseller.services.commercial_request_purpose import prove_known_noncommercial_request

pytestmark = pytest.mark.unit
ROUTE = {
    "logical_role": "critic",
    "prompt_template": "fanqie_short_semantic_review",
    "prompt_version": "stage.3",
}


@pytest.fixture
def semantic(case):
    result = paid(case)
    result["metadata"]["paid_call_origin"].update(ROUTE)
    # A paid, truncated response remains purpose evidence without a grade.
    result.update(content='{"partial_review":', finish_reason="length")
    add_run(case, origin=True).update(ROUTE)
    add_transport(case)
    return case


def route_sources(value):
    run = value["original_runs"][0]
    return {
        "paid_origin": paid(value)["metadata"]["paid_call_origin"],
        "run": run,
        "run_origin": run["metadata_json"]["paid_call_origin"],
        "transport_origin": value["transport_results"][0]["result_json"]["result"]["metadata"]["paid_call_origin"],
    }


def test_exact_semantic_route_classifies_truncated_paid_result_without_mutation(semantic):
    before = deepcopy(semantic)
    assert prove_known_noncommercial_request(**semantic) is True
    assert prove_known_noncommercial_request(**semantic) is True
    assert semantic == before
    assert paid(semantic)["finish_reason"] == "length"
    assert "grade" not in paid(semantic)
    assert semantic["actual_attempts"][0]["actual_tokens"] == 55
    assert semantic["actual_attempts"][0]["status"] == "completed"
    assert all(source[key] is None for source in route_sources(semantic).values()
               for key in ("project_id", "workflow_run_id", "step_run_id"))


@pytest.mark.parametrize("field,value", [
    ("logical_role", "planner"), ("logical_role", "editor"),
    ("logical_role", "writer"), ("prompt_version", None),
    ("prompt_version", "stage.2"), ("prompt_version", "stage.30"),
    ("prompt_version", "stage.3-unknown"), ("prompt_version", " stage.3"),
    ("prompt_template", "fanqie_short_semantic_review_extra"),
    ("prompt_template", "fanqie_short_semantic_review_candidate"),
    ("prompt_template", "chapter_commercial_quality_judge"),
])
def test_even_consistent_wrong_route_is_not_registered(semantic, field, value):
    for source in route_sources(semantic).values():
        source[field] = value
    before = deepcopy(semantic)
    assert prove_known_noncommercial_request(**semantic) is False
    assert semantic == before


@pytest.mark.parametrize("source", ["paid_origin", "run", "run_origin", "transport_origin"])
@pytest.mark.parametrize("field,value", [
    ("prompt_template", "hook_pull_judge"),  # Known, but conflicting purpose.
    ("prompt_version", "stage.2"), ("logical_role", "planner"),
])
def test_one_polluted_route_cannot_borrow_other_witnesses(semantic, source, field, value):
    route_sources(semantic)[source][field] = value
    assert prove_known_noncommercial_request(**semantic) is False


@pytest.mark.parametrize("source", ["paid", "transport", "run"])
@pytest.mark.parametrize("fault", [
    "effective_hash", "execution_scope", "execution_root", "missing_attempt",
    "extra_attempt", "attempt_model", "attempt_hash", "commercial_marker",
])
def test_exact_route_preserves_each_witness_physical_guards(semantic, source, fault):
    target = {
        "paid": paid(semantic),
        "transport": semantic["transport_results"][0]["result_json"]["result"],
        "run": semantic["original_runs"][0],
    }[source]
    meta = target.get("metadata", target.get("metadata_json"))
    receipt = meta["execution_receipt"]
    if fault == "effective_hash":
        meta["effective_input_receipt"]["hash"] = sha("other input")
    elif fault == "execution_scope":
        receipt["scope_id"] = "foreign-scope"
    elif fault == "execution_root":
        receipt["operation_key"] = sha("foreign-root")
    elif fault == "missing_attempt":
        receipt["attempts"].clear()
    elif fault == "extra_attempt":
        receipt["attempts"].append({**receipt["attempts"][0], "attempt_id": "extra"})
    elif fault == "attempt_model":
        receipt["attempts"][0]["model"] = "other-model"
    elif fault == "attempt_hash":
        receipt["attempts"][0]["effective_input_hash"] = sha("other wire")
    else:
        meta["commercial_grade"] = "unearned"
    assert prove_known_noncommercial_request(**semantic) is False


@pytest.mark.parametrize("fault", [
    "unknown_attempt", "reserved_attempt", "extra_send", "missing_send",
    "attempt_scope", "attempt_root", "attempt_hash", "attempt_model",
    "transport_key", "transport_scope", "transport_hash", "transport_attempt",
    "commercial_tool", "unknown_root",
])
def test_exact_route_preserves_actual_send_and_transport_guards(semantic, fault):
    attempt = semantic["actual_attempts"][0]
    row = semantic["transport_results"][0]
    if fault in {"unknown_attempt", "reserved_attempt"}:
        attempt["status"] = fault.removesuffix("_attempt")
    elif fault == "extra_send":
        semantic["actual_attempts"].append({**attempt, "id": "unreceipted-send"})
    elif fault == "missing_send":
        semantic["actual_attempts"].clear()
    elif fault.startswith("attempt_"):
        field = {"attempt_scope": "scope_id", "attempt_root": "operation_key",
                 "attempt_hash": "input_hash", "attempt_model": "model_name"}[fault]
        attempt[field] = sha("foreign")
    elif fault.startswith("transport_"):
        if fault == "transport_attempt":
            row["result_json"]["attempt_id"] = "foreign-send"
        else:
            field = {"transport_key": "key", "transport_scope": "scope_id",
                     "transport_hash": "input_hash"}[fault]
            row[field] = sha("foreign")
    elif fault == "commercial_tool":
        row["result_json"]["result"]["raw_message"] = {
            "tool_calls": [{"function": {"name": "submit_chapter_review"}}],
        }
    else:
        semantic["operation"]["status"] = "unknown"
    before = deepcopy(semantic)
    assert prove_known_noncommercial_request(**semantic) is False
    assert semantic == before
