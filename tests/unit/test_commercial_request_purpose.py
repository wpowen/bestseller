"""Pure original-request purpose proof; no provider, database or owner backfill."""

from copy import deepcopy
import hashlib
from uuid import uuid4

import pytest

from bestseller.services.commercial_request_purpose import prove_known_noncommercial_request

pytestmark = pytest.mark.unit


def sha(value):
    return hashlib.sha256(value.encode()).hexdigest()


@pytest.fixture
def case():
    scope, key, h, aid, model = (
        "original-scope", sha("original-operation"), sha("actual effective input"),
        "original-send", "openai/unrelated-model",
    )
    origin = {
        "schema": "paid-call-origin.v1", "status": "recorded", "project_id": None,
        "workflow_run_id": None, "step_run_id": None, "logical_role": "critic",
        "prompt_template": "world_law_consistency", "prompt_version": "v1", "prompt_hash": h,
    }
    meta = {
        "paid_call_origin": origin,
        "effective_input_receipt": {"schema": "effective-input.v1", "hash": h, "model": model},
        "execution_receipt": {
            "scope_id": scope, "operation_key": key,
            "attempts": [{"attempt_id": aid, "model": model, "effective_input_hash": h}],
        },
    }
    result = {"content": "海洋探测：潮汐符合既定世界规则。", "model_name": model, "metadata": meta,
              "tool_calls": None, "raw_message": None}
    return {
        "operation": {"scope_id": scope, "key": key, "input_hash": h,
                      "status": "completed", "result_json": result},
        "actual_attempts": [{"id": aid, "scope_id": scope, "operation_key": key,
                             "input_hash": h, "model_name": model, "status": "completed",
                             "actual_tokens": 55}],
        "original_runs": [], "transport_results": [], "failure_receipts": [],
    }


def paid(case):
    return case["operation"]["result_json"]


def add_run(case, *, origin=False):
    meta = deepcopy(paid(case)["metadata"])
    if not origin:
        meta.pop("paid_call_origin", None)
    run = {
        "id": str(uuid4()), "project_id": None, "workflow_run_id": None, "step_run_id": None,
        "logical_role": "critic", "prompt_template": "world_law_consistency",
        "prompt_version": "v1", "prompt_hash": case["operation"]["input_hash"],
        "model_name": paid(case)["model_name"], "metadata_json": meta,
    }
    case["original_runs"].append(run)
    return run


def add_transport(case):
    op, attempt = case["operation"], case["actual_attempts"][0]
    row = {
        "scope_id": op["scope_id"], "key": sha(f"transport-result.v1\n{op['scope_id']}\n{attempt['id']}"),
        "input_hash": op["input_hash"], "status": "transport_result",
        "result_json": {"schema": "transport-result.v1", "operation_key": op["key"],
                        "attempt_id": attempt["id"], "input_hash": op["input_hash"],
                        "result": deepcopy(paid(case))},
    }
    case["transport_results"].append(row)
    return row


def test_null_owner_world_law_proves_purpose_without_attribution_or_mutation(case):
    add_transport(case)
    before = deepcopy(case)
    assert prove_known_noncommercial_request(**case) is True
    assert case == before
    assert paid(case)["metadata"]["paid_call_origin"]["project_id"] is None


@pytest.mark.parametrize("prose", [
    "无关海洋探险小说", "A different book about orbital gardening",
    "人物读到 chapter_commercial_quality_judge 与 submit_chapter_review 的字样。",
])
def test_book_and_quoted_marker_prose_is_not_request_purpose(case, prose):
    paid(case)["content"] = prose
    paid(case)["raw_message"] = {"content": [{"text": prose}]}
    add_transport(case)
    assert prove_known_noncommercial_request(**case) is True


@pytest.mark.parametrize("legacy_marker", [False, True])
def test_exact_original_run_can_prove_missing_origin(case, legacy_marker):
    del paid(case)["metadata"]["paid_call_origin"]
    if legacy_marker:
        paid(case)["metadata"]["paid_call_origin"] = {
            "schema": "paid-call-origin.v1", "status": "unknown", "legacy": True,
            "reason": "missing_or_invalid_paid_call_origin", "project_id": None,
            "workflow_run_id": None, "step_run_id": None,
        }
    add_run(case)
    add_transport(case)
    assert prove_known_noncommercial_request(**case) is True


def test_unknown_origin_without_original_run_has_no_proof(case):
    del paid(case)["metadata"]["paid_call_origin"]
    assert prove_known_noncommercial_request(**case) is False


def test_nullable_version_stays_unknown_not_inferred(case):
    origin = paid(case)["metadata"]["paid_call_origin"]
    origin["prompt_version"] = None
    origin["prompt_template"] = "conception_intent_alignment"
    run = add_run(case)
    run.update(prompt_template="conception_intent_alignment", prompt_version=None)
    add_transport(case)
    before = deepcopy(case)
    assert prove_known_noncommercial_request(**case) is True
    assert case == before


def test_known_version_may_coexist_with_unknown_but_not_another_known_version(case):
    run = add_run(case)
    run["prompt_version"] = None
    assert prove_known_noncommercial_request(**case) is True
    run["prompt_version"] = "v9"
    assert prove_known_noncommercial_request(**case) is False


@pytest.mark.parametrize("field,value", [
    ("schema", "paid-call-origin.v2"), ("status", "recovered"),
    ("prompt_hash", sha("different")), ("logical_role", "planner"),
    ("prompt_template", "planner_unregistered_route"),
    ("prompt_template", "chapter_commercial_quality_judge"),
    ("prompt_version", 3), ("prompt_version", ""), ("project_id", "made-up-owner"),
])
def test_bad_or_commercial_origin_cannot_fall_back_to_run(case, field, value):
    add_run(case)
    paid(case)["metadata"]["paid_call_origin"][field] = value
    assert prove_known_noncommercial_request(**case) is False


@pytest.mark.parametrize("field,value", [
    ("scope_id", "foreign-scope"), ("operation_key", sha("other-operation")),
    ("input_hash", sha("other-input")), ("model_name", "other-model"),
    ("status", "unknown"), ("status", "reserved"),
])
def test_every_actual_attempt_is_bound(case, field, value):
    case["actual_attempts"][0][field] = value
    assert prove_known_noncommercial_request(**case) is False


@pytest.mark.parametrize("mode", ["empty", "duplicate", "extra", "declared_missing", "declared_extra"])
def test_attempt_inventory_cannot_be_silently_filtered(case, mode):
    attempts = case["actual_attempts"]
    declared = paid(case)["metadata"]["execution_receipt"]["attempts"]
    if mode == "empty":
        attempts.clear()
    elif mode == "duplicate":
        attempts.append(deepcopy(attempts[0]))
    elif mode == "extra":
        attempts.append({**attempts[0], "id": "another-physical-send"})
    elif mode == "declared_missing":
        declared.clear()
    else:
        declared.append({**declared[0], "attempt_id": "another-physical-send"})
    assert prove_known_noncommercial_request(**case) is False


@pytest.mark.parametrize("where,field,value", [
    ("row", "key", sha("wrong-domain-key")), ("row", "scope_id", "other-scope"),
    ("row", "input_hash", sha("wrong-input")), ("row", "status", "completed"),
    ("envelope", "schema", "transport-result.v2"),
    ("envelope", "operation_key", sha("other-root")),
    ("envelope", "attempt_id", "unlisted-send"),
    ("envelope", "input_hash", sha("different-effective-input")),
    ("paid", "model_name", "other-model"),
])
def test_transport_cannot_borrow_root_purpose_proof(case, where, field, value):
    row = add_transport(case)
    target = row if where == "row" else row["result_json"]
    if where == "paid":
        target = target["result"]
    target[field] = value
    assert prove_known_noncommercial_request(**case) is False


def test_all_transports_checked_including_bad_late_item_and_duplicate(case):
    row = add_transport(case)
    case["transport_results"].append(deepcopy(row))
    assert prove_known_noncommercial_request(**case) is False


@pytest.mark.parametrize("field,value", [
    ("prompt_template", "hook_pull_judge"),
    ("prompt_version", "v2"),
])
def test_conflicting_recorded_origin_on_transport_rejects_whole_root(case, field, value):
    row = add_transport(case)
    row["result_json"]["result"]["metadata"]["paid_call_origin"][field] = value
    assert prove_known_noncommercial_request(**case) is False


@pytest.mark.parametrize("marker", [
    {"tool_calls": [{"function": {"name": "submit_chapter_review", "arguments": "{}"}}]},
    {"raw_message": {"tool_calls": [{"function": {"name": "submit_chapter_review"}}]}},
    {"metadata": {"commercial_source_input_receipt": {"body_sha256": sha("body")}}},
])
def test_commercial_tool_or_current_source_markers_reject(case, marker):
    if "metadata" in marker:
        paid(case)["metadata"].update(marker["metadata"])
    else:
        paid(case).update(marker)
    assert prove_known_noncommercial_request(**case) is False


@pytest.mark.parametrize("target,field,value", [
    ("run", "prompt_hash", sha("wronghash")), ("run", "model_name", "another-model"),
    ("effective", "schema", "legacy-input.v0"), ("effective", "hash", sha("legacyhash")),
    ("effective", "model", "another-model"), ("execution", "scope_id", "another-scope"),
    ("execution", "operation_key", sha("another-root")),
])
def test_original_run_must_have_exact_v1_scope_operation_hash_model(case, target, field, value):
    del paid(case)["metadata"]["paid_call_origin"]
    run = add_run(case)
    dest = run if target == "run" else run["metadata_json"][
        "effective_input_receipt" if target == "effective" else "execution_receipt"
    ]
    dest[field] = value
    assert prove_known_noncommercial_request(**case) is False


def test_recovery_telemetry_without_original_origin_does_not_invent_route(case):
    del paid(case)["metadata"]["paid_call_origin"]
    run = add_run(case)
    run["metadata_json"]["execution_recovery"] = {"schema": "execution-recovery.v1"}
    assert prove_known_noncommercial_request(**case) is False


def test_restored_run_owners_do_not_override_original_paid_purpose_or_null_owners(case):
    run = add_run(case)
    run.update(project_id=str(uuid4()), workflow_run_id=str(uuid4()))
    run["metadata_json"]["execution_recovery"] = {"schema": "execution-recovery.v1"}
    row = add_transport(case)
    row["result_json"]["result"]["metadata"]["paid_call_origin"]["project_id"] = str(uuid4())
    before = deepcopy(case)
    assert prove_known_noncommercial_request(**case) is True
    assert case == before
    # Removing original paid route evidence makes recovery telemetry insufficient.
    del paid(case)["metadata"]["paid_call_origin"]
    del row["result_json"]["result"]["metadata"]["paid_call_origin"]
    assert prove_known_noncommercial_request(**case) is False


def test_metadata_alias_conflict_cannot_hide_commercial_marker(case):
    run = add_run(case)
    run["metadata"] = {"commercial_cohort": {}}
    assert prove_known_noncommercial_request(**case) is False


@pytest.mark.parametrize("template,role", [
    ("planner_volume_7_chapter_outline_batch_11_14", "planner"),
    ("planner_volume_3_chapter_outline_batch_2_9_story_enhancer_route_repair", "planner"),
    ("planner_volume_2_chapter_outline_commercial_surgical_repair", "planner"),
    ("outline_commercial_judge", "critic"),
    ("title_story_grounded_revision", "editor"),
    ("ideology_kernel", "planner"),
])
def test_known_routes_are_generic_across_volumes_and_stories(case, template, role):
    paid(case)["metadata"]["paid_call_origin"].update(prompt_template=template, logical_role=role)
    assert prove_known_noncommercial_request(**case) is True


@pytest.fixture
def rejected_case(case):
    op, attempt = case["operation"], case["actual_attempts"][0]
    op.update(status="stopped", result_json=None)
    attempt.update(status="rejected", actual_tokens=0)
    case["failure_receipts"] = [{
        "key": sha(f"transport-failure.v1\n{op['scope_id']}\n{attempt['id']}"),
        "scope_id": op["scope_id"], "input_hash": op["input_hash"],
        "status": "transport_failure_receipt",
        "result_json": {
            "schema": "transport-failure.v1", "attempt_id": attempt["id"],
            "operation_key": op["key"], "project_id": None, "workflow_run_id": None,
            "http_status": 422, "logical_role": "editor", "exception_type": "BadRequestError",
            "physical_sends": 1, "prompt_template": "chapter_rewrite_repair",
            "operator_interruption": False,
        },
    }]
    return case


def test_physical_422_proves_noncommercial_failure_without_free_send_claim(rejected_case):
    before = deepcopy(rejected_case)
    assert prove_known_noncommercial_request(**rejected_case) is True
    assert rejected_case == before
    assert rejected_case["failure_receipts"][0]["result_json"]["physical_sends"] == 1


@pytest.mark.parametrize("target,field,value", [
    ("row", "key", sha("wrong-key-domain")), ("row", "scope_id", "foreign-scope"),
    ("row", "input_hash", sha("wrong-hash")), ("receipt", "schema", "transport-failure.v0"),
    ("receipt", "operation_key", sha("foreign-root")), ("receipt", "attempt_id", "unknown"),
    ("receipt", "physical_sends", 0), ("receipt", "physical_sends", 2),
    ("receipt", "http_status", 400), ("receipt", "http_status", 500),
    ("receipt", "operator_interruption", True), ("receipt", "logical_role", "critic"),
    ("receipt", "prompt_template", "chapter_commercial_quality_judge"),
    ("receipt", "prompt_template", "unknown_repair"),
    ("attempt", "actual_tokens", None), ("attempt", "actual_tokens", True),
    ("attempt", "status", "reconciled_charged"), ("attempt", "input_hash", sha("other")),
])
def test_failure_proof_is_exact_and_unknown_or_commercial_stays_blocked(rejected_case, target, field, value):
    row = rejected_case["failure_receipts"][0]
    dest = row if target == "row" else row["result_json"]
    if target == "attempt":
        dest = rejected_case["actual_attempts"][0]
    dest[field] = value
    assert prove_known_noncommercial_request(**rejected_case) is False


def test_stopped_without_complete_failure_inventory_is_not_proof(rejected_case):
    rejected_case["failure_receipts"] = []
    assert prove_known_noncommercial_request(**rejected_case) is False


def test_stopped_charged_quarantine_is_not_route_proof(case):
    case["operation"].update(status="stopped", result_json=None)
    case["actual_attempts"][0].update(status="reconciled_charged", actual_tokens=None)
    assert prove_known_noncommercial_request(**case) is False


@pytest.fixture
def retried_case(case):
    attempt = case["actual_attempts"][0]
    rejected = {**attempt, "id": "prior-rejected-send", "status": "rejected", "actual_tokens": 0}
    case["actual_attempts"].insert(0, rejected)
    declared = paid(case)["metadata"]["execution_receipt"]["attempts"]
    declared.insert(0, {
        "attempt_id": rejected["id"], "model": rejected["model_name"],
        "effective_input_hash": rejected["input_hash"], "status": "rejected",
    })
    del paid(case)["metadata"]["paid_call_origin"]
    run = add_run(case)
    run.update(logical_role="planner", prompt_template="planner_volume_4_chapter_outline_batch_10_12")
    # The retained transport belongs to the successful send, not the rejection.
    case["actual_attempts"].reverse()
    add_transport(case)
    case["actual_attempts"].reverse()
    return case


def test_original_success_proves_same_request_with_prior_zero_token_rejection(retried_case):
    before = deepcopy(retried_case)
    assert prove_known_noncommercial_request(**retried_case) is True
    assert retried_case == before
    assert len(retried_case["actual_attempts"]) == 2
    assert retried_case["actual_attempts"][0]["status"] == "rejected"


@pytest.mark.parametrize("field,value", [
    ("input_hash", sha("different-input")), ("model_name", "another-model"),
    ("status", "unknown"), ("status", "reserved"),
    ("actual_tokens", None), ("actual_tokens", 5), ("actual_tokens", False),
])
def test_prior_rejected_send_must_remain_terminal_same_input_model(retried_case, field, value):
    retried_case["actual_attempts"][0][field] = value
    assert prove_known_noncommercial_request(**retried_case) is False


@pytest.mark.parametrize("source", ["root", "transport", "run"])
def test_omitting_prior_attempt_in_any_receipt_does_not_prove_full_request(retried_case, source):
    meta = paid(retried_case)["metadata"]
    if source == "transport":
        meta = retried_case["transport_results"][0]["result_json"]["result"]["metadata"]
    elif source == "run":
        meta = retried_case["original_runs"][0]["metadata_json"]
    meta["execution_receipt"]["attempts"].pop(0)
    assert prove_known_noncommercial_request(**retried_case) is False


def test_rejected_send_cannot_supply_a_completed_transport_result(retried_case):
    row = retried_case["transport_results"][0]
    aid = retried_case["actual_attempts"][0]["id"]
    row["key"] = sha(f"transport-result.v1\n{row['scope_id']}\n{aid}")
    row["result_json"]["attempt_id"] = aid
    assert prove_known_noncommercial_request(**retried_case) is False


@pytest.mark.parametrize("key,value", [("operation", None), ("actual_attempts", "bad"),
                                      ("transport_results", [None]), ("original_runs", [{}])])
def test_malformed_inputs_fail_closed(case, key, value):
    case[key] = value
    assert prove_known_noncommercial_request(**case) is False


@pytest.fixture
def charged_failure_case(rejected_case):
    value = deepcopy(rejected_case)
    value['actual_attempts'][0].update(status='reconciled_charged', actual_tokens=None)
    value['failure_receipts'][0]['result_json'].update(
        logical_role='planner', prompt_template='planner_cast_spec',
        http_status=None, exception_type='ValueError')
    return value


def test_original_charged_failure_proves_only_known_purpose_without_rewriting(charged_failure_case):
    before = deepcopy(charged_failure_case)
    assert prove_known_noncommercial_request(**charged_failure_case)
    assert charged_failure_case == before
    assert charged_failure_case['actual_attempts'][0]['actual_tokens'] is None
    assert charged_failure_case['operation']['result_json'] is None


@pytest.mark.parametrize('target,field,value', [
    ('receipt', 'logical_role', 'critic'),
    ('receipt', 'prompt_template', 'chapter_commercial_quality_judge'),
    ('receipt', 'prompt_template', 'unknown_planner_route'),
    ('receipt', 'operation_key', sha('different-root')),
    ('receipt', 'attempt_id', 'different-attempt'),
    ('receipt', 'physical_sends', 0),
    ('receipt', 'physical_sends', 2),
    ('receipt', 'operator_interruption', True),
    ('receipt', 'tool_calls', [{'function': {'name': 'submit_chapter_review'}}]),
    ('row', 'input_hash', sha('different-body')),
    ('row', 'key', sha('different-control')),
    ('attempt', 'status', 'unknown'),
    ('attempt', 'actual_tokens', -1),
])
def test_charged_failure_conflicting_or_unbound_purpose_stays_unknown(charged_failure_case, target, field, value):
    row = charged_failure_case['failure_receipts'][0]
    obj = row if target == 'row' else row['result_json'] if target == 'receipt' else charged_failure_case['actual_attempts'][0]
    obj[field] = value
    assert not prove_known_noncommercial_request(**charged_failure_case)
