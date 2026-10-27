import copy
import json
from uuid import uuid4

import pytest

from bestseller.services import world_authority as authority
from bestseller.services.llm import LLMCompletionResult
from bestseller.settings import load_settings

pytestmark = pytest.mark.unit


def sources():
    return {
        "creation": {"cost_rule": "Using the compass never exhausts its owner."},
        "world": {"power_system": {
            "acquisition_method": "A purchased compass reveals direction within three hours.",
            "hard_limits": "Each use exhausts the pilot for two days.",
        }},
    }


def review(source, *, conflict=False):
    checks = []
    for axis in authority.AXES:
        citations = [{
            "path": ["world", "power_system", "acquisition_method"],
            "quote": source["world"]["power_system"]["acquisition_method"],
        }]
        status = "consistent"
        if axis == "cost_policy":
            status = "conflict" if conflict else "consistent"
            citations = [
                {"path": ["creation", "cost_rule"], "quote": source["creation"]["cost_rule"]},
                {"path": ["world", "power_system", "hard_limits"],
                 "quote": source["world"]["power_system"]["hard_limits"]},
            ]
        checks.append({"axis": axis, "status": status, "reason": "Review finding.",
                       "citations": citations})
    return {"complete": True, "source_hash": authority.world_authority_source_hash(source),
            "checks": checks}


def indexed_review(source, *, conflict=False):
    result = review(source, conflict=conflict)
    index = authority.world_authority_source_index(source)
    for check in result["checks"]:
        check["citations"] = [
            {"source_id": next(e["source_id"] for e in index
                               if e["path"] == c["path"] and e["quote"] == c["quote"])}
            for c in check["citations"]
        ]
    return result


def test_two_matching_conflicts_are_reported_without_mutating_sources():
    source = sources()
    before = copy.deepcopy(source)
    result = authority.reconcile_world_authority_reviews(
        [review(source, conflict=True), review(source, conflict=True)], sources=source,
    )
    assert result["status"] == "conflict"
    assert result["corroborated_axes"] == ["cost_policy"]
    assert source == before


def test_single_judge_disagreement_cannot_clear_or_corroborate_conflict():
    source = sources()
    result = authority.reconcile_world_authority_reviews(
        [review(source, conflict=True), review(source)], sources=source,
    )
    assert result["status"] == "review_required"
    assert result["corroborated_axes"] == []


@pytest.mark.parametrize(
    "fault", ["quote", "path", "hash", "complete", "axis", "duplicate", "one_source"],
)
def test_invalid_model_claims_fail_closed(fault):
    source = sources()
    payload = review(source, conflict=True)
    if fault == "quote":
        payload["checks"][0]["citations"][0]["quote"] = "after three hours"
    elif fault == "path":
        payload["checks"][0]["citations"][0]["path"] = ["world", "invented"]
    elif fault == "hash":
        payload["source_hash"] = "stale"
    elif fault == "complete":
        payload["complete"] = False
    elif fault == "axis":
        payload["checks"].pop()
    elif fault == "duplicate":
        payload["checks"][-1] = copy.deepcopy(payload["checks"][0])
    else:
        payload["checks"][3]["citations"] = [payload["checks"][3]["citations"][0]] * 2
    with pytest.raises(ValueError, match="WORLD_AUTHORITY"):
        authority.validate_world_authority_review(
            payload, sources=source, source_hash=authority.world_authority_source_hash(source),
        )


def test_stale_source_cannot_reuse_valid_old_review():
    source = sources()
    payload = review(source)
    old_hash = payload["source_hash"]
    source["world"]["power_system"]["hard_limits"] = "No exhaustion."
    with pytest.raises(ValueError, match="STALE_SOURCE"):
        authority.validate_world_authority_review(payload, sources=source, source_hash=old_hash)


def test_same_axis_different_source_pairs_are_not_corroboration():
    source = sources()
    left, right = review(source, conflict=True), review(source, conflict=True)
    right["checks"][3]["citations"][1] = right["checks"][0]["citations"][0]
    result = authority.reconcile_world_authority_reviews([left, right], sources=source)
    assert result["status"] == "review_required"


def test_uncertainty_never_turns_into_consistent_status():
    source = sources()
    uncertain = review(source)
    uncertain["checks"][0]["status"] = "uncertain"
    result = authority.reconcile_world_authority_reviews([uncertain, uncertain], sources=source)
    assert result["status"] == "review_required"


def test_consistent_control_requires_two_complete_reviews():
    source = sources()
    source["world"]["power_system"]["hard_limits"] = "The compass cannot reveal names."
    result = authority.reconcile_world_authority_reviews(
        [review(source), review(source)], sources=source,
    )
    assert result["status"] == "consistent"
    with pytest.raises(ValueError, match="TWO_REVIEWS"):
        authority.reconcile_world_authority_reviews([review(source)], sources=source)


@pytest.mark.asyncio
async def test_actual_requests_are_independent_bounded_and_traceable(monkeypatch):
    source = sources()
    requests = []

    async def complete(session, settings, request):
        requests.append(request)
        return LLMCompletionResult(
            content=json.dumps(indexed_review(source, conflict=True)), provider="fixture",
            model_name="fixture", llm_run_id=uuid4(),
        )

    monkeypatch.setattr(authority, "complete_text", complete)
    result = await authority.audit_world_authority(
        None, load_settings(env={}), sources=source, project_id=uuid4(),
    )
    assert result["status"] == "conflict"
    assert len(requests) == len(result["llm_run_ids"]) == 2
    assert requests[0].user_prompt != requests[1].user_prompt
    assert "Review finding." not in requests[1].user_prompt
    for request in requests:
        assert request.prompt_version == authority.AUDIT_VERSION
        assert source["world"]["power_system"]["acquisition_method"] in request.user_prompt
        assert request.max_tokens_override == 5000


@pytest.mark.asyncio
async def test_fallback_and_oversized_source_never_pass(monkeypatch):
    source = sources()
    calls = []

    async def complete(*args):
        calls.append(args)
        return LLMCompletionResult(
            content=json.dumps(indexed_review(source)), provider="fallback",
            model_name="fallback", llm_run_id=None,
        )

    monkeypatch.setattr(authority, "complete_text", complete)
    with pytest.raises(ValueError, match="PROVIDER_UNAVAILABLE"):
        await authority.audit_world_authority(
            None, load_settings(env={}), sources=source, project_id=uuid4(),
        )
    assert len(calls) == 1
    source["world"]["power_system"]["hard_limits"] = "rule" * 10000
    with pytest.raises(ValueError, match="BUDGET_EXCEEDED"):
        await authority.audit_world_authority(
            None, load_settings(env={}), sources=source, project_id=uuid4(),
        )
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["fallback_flag", "truncated", "malformed", "missing_axis"])
async def test_invalid_second_review_stops_without_retry(monkeypatch, fault):
    source = sources()
    calls = []

    async def complete(*args):
        calls.append(args)
        payload = indexed_review(source)
        kwargs = {}
        if len(calls) == 2:
            if fault == "fallback_flag":
                kwargs["fallback_used"] = True
            elif fault == "truncated":
                kwargs["finish_reason"] = "length"
            elif fault == "missing_axis":
                payload["checks"].pop()
        content = "not json" if len(calls) == 2 and fault == "malformed" else json.dumps(payload)
        return LLMCompletionResult(
            content=content, provider="fixture", model_name="fixture", llm_run_id=uuid4(),
            **kwargs,
        )

    monkeypatch.setattr(authority, "complete_text", complete)
    with pytest.raises(authority.WorldAuthorityAuditError, match="WORLD_AUTHORITY") as captured:
        await authority.audit_world_authority(
            None, load_settings(env={}), sources=source, project_id=uuid4(),
        )
    assert len(calls) == 2
    diagnostic = captured.value.diagnostic
    assert diagnostic["status"] == "review_required"
    assert diagnostic["failed_review_index"] == 2
    assert len(diagnostic["llm_run_ids"]) == 2
    assert len(diagnostic["raw_reviews_for_diagnosis_only"]) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", ["creation", "world"])
async def test_missing_source_does_not_call_model(monkeypatch, missing):
    source = sources()
    source.pop(missing)

    async def forbidden(*args):
        pytest.fail("No model call is allowed without both source groups")

    monkeypatch.setattr(authority, "complete_text", forbidden)
    with pytest.raises(ValueError, match="WORLD_AUTHORITY_MISSING"):
        await authority.audit_world_authority(
            None, load_settings(env={}), sources=source, project_id=uuid4(),
        )


@pytest.mark.parametrize("status", ["consistent", "conflict", "not_applicable"])
def test_cost_policy_cannot_ignore_user_source(status):
    source = sources()
    payload = review(source)
    payload["checks"][3]["status"] = status
    payload["checks"][3]["citations"][0] = payload["checks"][0]["citations"][0]
    with pytest.raises(ValueError, match="COST_REQUIRES"):
        authority.reconcile_world_authority_reviews([payload, payload], sources=source)


def test_world_internal_conflict_does_not_require_irrelevant_user_cost_citation():
    source = sources()
    payload = review(source)
    payload["checks"][-1]["status"] = "conflict"
    payload["checks"][-1]["citations"].append(payload["checks"][3]["citations"][1])
    result = authority.reconcile_world_authority_reviews([payload, payload], sources=source)
    assert result["corroborated_axes"] == ["internal_consistency"]
    assert result["semantic_review_required"] is True


def test_source_index_restores_exact_text_and_rejects_fabricated_ids():
    source = sources()
    indexed = indexed_review(source, conflict=True)
    assert authority.resolve_world_authority_citations(indexed, sources=source) == (
        authority.validate_world_authority_review(
            review(source, conflict=True), sources=source,
            source_hash=authority.world_authority_source_hash(source),
        )
    )
    indexed["checks"][0]["citations"][0]["source_id"] = "S9999"
    with pytest.raises(ValueError, match="UNKNOWN_SOURCE_ID"):
        authority.resolve_world_authority_citations(indexed, sources=source)


def test_source_index_keeps_all_long_field_text_without_rephrasing():
    source = {"world": {"rule": "有效期内可读。" * 250}}
    index = authority.world_authority_source_index(source)
    assert "".join(e["quote"] for e in index) == source["world"]["rule"]
    assert all(source["world"]["rule"][e["start"]:e["end"]] == e["quote"] for e in index)


def test_source_index_keeps_short_names_and_tiers():
    source = {"world": {"tiers": ["甲", "乙", "起步期"], "person": "林舟"}}
    index = authority.world_authority_source_index(source)
    assert {e["quote"] for e in index} == {"甲", "乙", "起步期", "林舟"}


def test_uncertain_without_citations_is_diagnostic_not_a_pass():
    source = sources()
    payload = review(source)
    payload["checks"][0].update(status="uncertain", citations=[])
    result = authority.reconcile_world_authority_reviews([payload, payload], sources=source)
    assert result["status"] == "review_required"
    assert result["unsettled_axes"] == ["trigger_conditions"]
    assert result["evidence_complete"] is False
    assert result["coverage_complete"] is True


@pytest.mark.parametrize("status", ["consistent", "conflict", "not_applicable"])
def test_positive_or_conflict_claim_still_requires_evidence(status):
    source = sources()
    payload = review(source)
    payload["checks"][0].update(status=status, citations=[])
    with pytest.raises(ValueError, match="MISSING_EVIDENCE: trigger_conditions"):
        authority.reconcile_world_authority_reviews([payload, payload], sources=source)


def test_same_fields_different_spans_cannot_corroborate_conflicts():
    source = sources()
    source["world"]["power_system"]["hard_limits"] = (
        "Each use exhausts the pilot for two days. Other people recover instantly."
    )
    left, right = review(source, conflict=True), review(source, conflict=True)
    left["checks"][3]["citations"][1]["quote"] = "Each use exhausts the pilot for two days."
    right["checks"][3]["citations"][1]["quote"] = "Other people recover instantly."
    result = authority.reconcile_world_authority_reviews([left, right], sources=source)
    assert result["status"] == "review_required"
    assert result["corroborated_axes"] == []


def test_source_index_preserves_whitespace_only_chunks():
    source = {"world": {"rule": "a" * 600 + " " * 600 + "b" * 600}}
    index = authority.world_authority_source_index(source)
    assert "".join(entry["quote"] for entry in index) == source["world"]["rule"]


def test_source_index_exposes_typed_scalar_rules():
    source = {"world": {"chapter": 35, "requires_owner": False, "limit": None}}
    index = authority.world_authority_source_index(source)
    assert {tuple(e["path"]): e["quote"] for e in index} == {
        ("world", "chapter"): "35", ("world", "requires_owner"): "false",
        ("world", "limit"): "null",
    }
    for entry in index:
        assert authority._source_leaf(source, entry["path"]) == entry["quote"]


def test_index_rejects_unusable_deep_paths_before_model_call():
    source = {"world": "rule"}
    for _ in range(12):
        source = {"nested": source}
    with pytest.raises(ValueError, match="PATH_DEPTH"):
        authority.world_authority_source_index(source)


def test_repeated_text_in_distinct_source_spans_is_not_corroboration():
    source = sources()
    payload = indexed_review(source)
    source["world"]["power_system"]["hard_limits"] = "same" * 300
    payload["source_hash"] = authority.world_authority_source_hash(source)
    entries = [e for e in authority.world_authority_source_index(source)
               if e["path"] == ["world", "power_system", "hard_limits"]]
    left, right = copy.deepcopy(payload), copy.deepcopy(payload)
    for i, value in enumerate((left, right)):
        value["checks"][3]["status"] = "conflict"
        value["checks"][3]["citations"][1] = {"source_id": entries[i]["source_id"]}
    result = authority.reconcile_world_authority_reviews([
        authority.resolve_world_authority_citations(p, sources=source) for p in (left, right)
    ], sources=source)
    assert result["status"] == "review_required"


def test_single_field_conflict_cannot_be_confirmed_with_duplicate_citations():
    source = sources()
    payload = review(source)
    payload["checks"][-1].update(status="conflict")
    payload["checks"][-1]["citations"] *= 2
    with pytest.raises(ValueError, match="CONFLICT_REQUIRES_TWO_SOURCES"):
        authority.reconcile_world_authority_reviews([payload, payload], sources=source)
