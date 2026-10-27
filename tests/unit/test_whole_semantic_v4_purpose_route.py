"""Historical stage.4 and current stage.5 routes; no fee relaxation.

Stage.4 remains a registered historical producer even after the runtime moves
to the bounded protocol recovery route. Neither tuple proves a literary grade.
"""
from copy import deepcopy

import pytest

from test_commercial_request_purpose import add_run, add_transport, paid
from test_commercial_request_purpose import case as case
from test_fanqie_short_semantic_review import kwargs
from test_whole_semantic_purpose_route import route_sources

from bestseller.services import fanqie_short_semantic_review as producer
from bestseller.services.commercial_request_purpose import prove_known_noncommercial_request

pytestmark = pytest.mark.unit


@pytest.fixture(params=["stage.4", "stage.5"])
def runtime_semantic(case, request):
    payload = producer.prepare_review(**kwargs())
    fields = producer.native_request_fields(payload)
    assert fields["prompt_version"] == producer.PROMPT_VERSION
    fields["prompt_version"] = request.param
    route = {k: fields[k] for k in ("logical_role", "prompt_template", "prompt_version")}
    assert tuple(route.values()) == ("critic", "fanqie_short_semantic_review", request.param)
    assert set(fields["metadata"]) == {
        "binding_sha256", "implementation_source_sha256", "current_scope", "policy_sha256", "review_evidence_only",
    }
    assert fields["metadata"]["review_evidence_only"] is True
    assert fields["metadata"]["implementation_source_sha256"] == producer.fingerprint(payload["implementation_source_hashes"])
    result = paid(case)
    result["metadata"].update(deepcopy(fields["metadata"]))
    origin = result["metadata"]["paid_call_origin"]
    origin.update(route, project_id=str(fields["project_id"]), workflow_run_id=str(fields["workflow_run_id"]))
    run = add_run(case, origin=True)
    run.update(route, project_id=str(fields["project_id"]), workflow_run_id=str(fields["workflow_run_id"]))
    # Real request tuple/metadata, synthetic physical proof. Truncation is not
    # a semantic grade and cannot turn UNKNOWN roots into completed results.
    result.update(content='{"partial_review":', finish_reason="length")
    add_transport(case)
    return case


def test_runtime_stage4_exact_tuple_metadata_proves_purpose_only(runtime_semantic):
    before = deepcopy(runtime_semantic)
    assert prove_known_noncommercial_request(**runtime_semantic) is True
    assert runtime_semantic == before
    assert paid(runtime_semantic)["finish_reason"] == "length"
    assert "grade" not in paid(runtime_semantic)


@pytest.mark.parametrize("field,value", [
    ("prompt_version", None), ("prompt_version", "stage.40"), ("prompt_version", "stage.4-unknown"),
    ("prompt_version", " stage.4"), ("prompt_version", "stage.6"),
    ("prompt_template", "fanqie_short_semantic_review_extra"),
    ("prompt_template", "fanqie_short_semantic_review_candidate"),
    ("logical_role", "planner"), ("logical_role", "editor"),
])
def test_even_consistent_unregistered_tuple_stays_closed(runtime_semantic, field, value):
    for source in route_sources(runtime_semantic).values():
        source[field] = value
    assert prove_known_noncommercial_request(**runtime_semantic) is False


@pytest.mark.parametrize("source", ["paid_origin", "run", "run_origin", "transport_origin"])
def test_stage3_and_stage4_cannot_be_mixed_as_one_original_request(runtime_semantic, source):
    route_sources(runtime_semantic)[source]["prompt_version"] = "stage.3"
    assert prove_known_noncommercial_request(**runtime_semantic) is False


def test_retained_original_run_can_prove_purpose_without_claiming_financial_closure(runtime_semantic):
    runtime_semantic["transport_results"].clear()
    before = deepcopy(runtime_semantic)
    assert prove_known_noncommercial_request(**runtime_semantic) is True
    assert runtime_semantic == before
    assert runtime_semantic["transport_results"] == []
    assert "grade" not in paid(runtime_semantic)


@pytest.mark.parametrize("fault", ["unknown", "reserved", "extra_attempt", "commercial_marker"])
def test_stage4_does_not_relax_physical_fee_or_commercial_guards(runtime_semantic, fault):
    if fault in {"unknown", "reserved"}:
        runtime_semantic["actual_attempts"][0]["status"] = fault
    elif fault == "extra_attempt":
        runtime_semantic["actual_attempts"].append({**runtime_semantic["actual_attempts"][0], "id": "extra-send"})
    else:
        paid(runtime_semantic)["metadata"]["commercial_grade"] = "unearned"
    before = deepcopy(runtime_semantic)
    assert prove_known_noncommercial_request(**runtime_semantic) is False
    assert runtime_semantic == before
