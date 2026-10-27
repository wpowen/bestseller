import copy
import json
from uuid import uuid4

import pytest

from bestseller.services import world_cost_claims as claims
from bestseller.services.ideology_kernel import cost_style_directive
from bestseller.services.llm import LLMCompletionResult
from bestseller.services.world_authority import (
    WorldAuthorityAuditError,
    world_authority_source_hash,
)
from bestseller.settings import load_settings

pytestmark = pytest.mark.unit
# ruff: noqa: RUF001 -- Exact Chinese punctuation is part of the source fixture.


def source():
    return {
        "creation": {"cost_style": "minimal", "cost_rule": cost_style_directive("minimal")},
        "world": {
            "power_system": {
                "hard_limits": "Overreach leaves the pilot unable to steer.",
                "acquisition_method": "A compass reads direction.",
            }
        },
    }


def review(
    sources, *, effect="self_weakening", polarity="incurs", actor="protagonist", scope="overreach"
):
    rows = []
    for c in claims.cost_clause_index(sources):
        fact = {
            "effect": "no_cost_fact",
            "actor": "not_applicable",
            "usage_scope": "not_applicable",
            "polarity": "not_applicable",
        }
        if c["path"][-1] == "hard_limits":
            fact = {"effect": effect, "actor": actor, "usage_scope": scope, "polarity": polarity}
        rows.append({"clause_id": c["clause_id"], "facts": [fact]})
    return {"complete": True, "source_hash": world_authority_source_hash(sources), "clauses": rows}


@pytest.mark.parametrize("scope", ["ordinary", "overreach", "any_use"])
def test_direct_weakening_is_conflict_even_without_any_victory_interval(scope):
    sources = source()
    before = copy.deepcopy(sources)
    r = review(sources, scope=scope)
    result = claims.reconcile_cost_claims([r, r], sources=sources)
    assert result["status"] == "conflict"
    assert len(result["conflicts"]) == 1
    assert result["conflicts"][0]["code"] == "MINIMAL_SELF_WEAKENING"
    assert result["semantic_review_required"] is True
    assert sources == before


@pytest.mark.parametrize(
    "effect", ["material_consumption", "production_time", "external_opposition"]
)
def test_resource_time_and_external_pressure_are_not_self_weakening(effect):
    sources = source()
    r = review(sources, effect=effect)
    result = claims.reconcile_cost_claims([r, r], sources=sources)
    assert result["conflicts"] == []
    assert result["scope"] == "cost_rule_clauses_only"


@pytest.mark.parametrize("polarity", ["forbids", "absent"])
def test_prohibiting_or_negating_weakening_is_not_incurring_it(polarity):
    sources = source()
    r = review(sources, polarity=polarity)
    assert claims.reconcile_cost_claims([r, r], sources=sources)["conflicts"] == []


def test_other_actor_cost_cannot_become_protagonist_cost():
    sources = source()
    r = review(sources, actor="other")
    assert claims.reconcile_cost_claims([r, r], sources=sources)["conflicts"] == []


@pytest.mark.parametrize("field", ["effect", "actor", "scope", "polarity"])
def test_unknown_remains_unresolved(field):
    sources = source()
    r = review(sources, **{field: "unknown"})
    result = claims.reconcile_cost_claims([r, r], sources=sources)
    assert result["status"] == "review_required"
    assert result["unresolved_clause_ids"]


def test_different_classifications_cannot_confirm_conflict():
    sources = source()
    result = claims.reconcile_cost_claims(
        [review(sources), review(sources, effect="external_opposition")],
        sources=sources,
    )
    assert result["status"] == "review_required"
    assert result["conflicts"] == []


def test_unresolved_details_distinguish_scope_disagreement_from_unknown():
    sources = source()
    result = claims.reconcile_cost_claims(
        [
            review(sources, effect="material_consumption", scope="ordinary"),
            review(sources, effect="material_consumption", scope="any_use"),
        ],
        sources=sources,
    )
    detail = result["unresolved_details"][0]
    assert detail["fact_set_disagreement"] is True
    assert detail["differing_value_axes"] == ["usage_scope"]
    assert detail["unknown_axes"] == []
    assert detail["policy_conflict_confirmed"] is False
    assert detail["quote"] == sources["world"]["power_system"]["hard_limits"]
    assert result["status"] == "review_required"


def hard_limit_row(payload, sources):
    clause_id = next(
        c["clause_id"] for c in claims.cost_clause_index(sources) if c["path"][-1] == "hard_limits"
    )
    return next(row for row in payload["clauses"] if row["clause_id"] == clause_id)


@pytest.mark.parametrize("field", ["effect", "actor", "usage_scope", "polarity"])
def test_equal_unknown_facts_still_have_actionable_axis_and_never_resolve(field):
    sources = source()
    r = review(sources)
    hard_limit_row(r, sources)["facts"][0][field] = "unknown"
    result = claims.reconcile_cost_claims([r, r], sources=sources)
    detail = result["unresolved_details"][0]
    assert detail["unknown_axes"] == [field]
    assert detail["fact_set_disagreement"] is False
    assert detail["differing_value_axes"] == []
    assert result["unresolved_clause_ids"]


def test_shared_conflict_and_additional_negation_remain_distinct():
    sources = source()
    first = review(sources)
    second = copy.deepcopy(first)
    row = hard_limit_row(second, sources)
    row["facts"].append({**row["facts"][0], "polarity": "absent"})
    result = claims.reconcile_cost_claims([first, second], sources=sources)
    detail = result["unresolved_details"][0]
    assert detail["policy_conflict_confirmed"] is True
    assert detail["differing_value_axes"] == ["polarity"]
    assert result["conflicts"] and result["unresolved_clause_ids"]


def test_tuple_disagreement_cannot_be_hidden_by_equal_axis_value_sets():
    sources = source()
    first = review(sources)
    row = hard_limit_row(first, sources)
    row["facts"].append({**row["facts"][0], "actor": "other", "polarity": "absent"})
    second = copy.deepcopy(first)
    second_row = hard_limit_row(second, sources)
    second_row["facts"][0]["actor"] = "other"
    second_row["facts"][1]["actor"] = "protagonist"
    result = claims.reconcile_cost_claims([first, second], sources=sources)
    detail = result["unresolved_details"][0]
    assert detail["fact_set_disagreement"] is True
    assert detail["differing_value_axes"] == []
    assert detail["review_facts"][0] != detail["review_facts"][1]
    assert result["status"] == "review_required"


@pytest.mark.parametrize("fault", ["other_policy", "changed_policy"])
def test_unsupported_policy_never_silently_uses_minimal(fault):
    sources = source()
    if fault == "other_policy":
        sources["creation"]["cost_style"] = "heavy"
    else:
        sources["creation"]["cost_rule"] = "Self weakening is allowed."
    r = review(sources)
    with pytest.raises(ValueError, match=r"WORLD_COST_(UNSUPPORTED_POLICY|FORMAL_POLICY_MISMATCH)"):
        claims.reconcile_cost_claims([r, r], sources=sources)


@pytest.mark.parametrize("fault", ["missing", "duplicate", "invented", "stale", "exception"])
def test_invalid_extractions_fail_closed(fault):
    sources = source()
    r = review(sources)
    if fault == "missing":
        r["clauses"].pop()
    elif fault == "duplicate":
        r["clauses"][-1] = copy.deepcopy(r["clauses"][0])
    elif fault == "invented":
        r["clauses"][0]["clause_id"] = "C999"
    elif fault == "stale":
        r["source_hash"] = "old"
    else:
        r["clauses"][0]["facts"][0]["victory_interval"] = "two weeks"
    with pytest.raises(ValueError, match="WORLD_COST"):
        claims.validate_cost_claims(r, sources=sources)


def test_clause_index_retains_source_positions_and_duplicate_text_identity():
    sources = source()
    text = "甲耗墨；乙失能。甲耗墨；"
    sources["world"]["power_system"]["hard_limits"] = text
    indexed = [c for c in claims.cost_clause_index(sources) if c["path"][-1] == "hard_limits"]
    assert "".join(c["quote"] for c in indexed) == text
    assert all(text[c["start"] : c["end"]] == c["quote"] for c in indexed)
    assert indexed[0]["clause_id"] != indexed[-1]["clause_id"]


def test_long_single_clause_is_not_split_by_the_prompt_chunk_boundary():
    sources = source()
    text = "使用时消耗材料但" * 100 + "不会让使用者失去操作能力。"
    sources["world"]["power_system"]["hard_limits"] = text
    indexed = [c for c in claims.cost_clause_index(sources) if c["path"][-1] == "hard_limits"]
    assert len(indexed) == 1
    assert indexed[0]["quote"] == text
    assert (indexed[0]["start"], indexed[0]["end"]) == (0, len(text))


@pytest.mark.parametrize("field", ["actor", "scope", "polarity"])
def test_real_fact_cannot_hide_as_not_applicable(field):
    sources = source()
    r = review(sources, **{field: "not_applicable"})
    with pytest.raises(ValueError, match="WORLD_COST"):
        claims.reconcile_cost_claims([r, r], sources=sources)


@pytest.mark.asyncio
async def test_two_real_request_shapes_are_bounded_independent_and_traceable(monkeypatch):
    sources = source()
    requests = []

    async def complete(session, settings, request):
        requests.append(request)
        return LLMCompletionResult(
            content=json.dumps(review(sources)),
            provider="fixture",
            model_name="fixture",
            llm_run_id=uuid4(),
        )

    monkeypatch.setattr(claims, "complete_text", complete)
    result = await claims.audit_world_cost_claims(
        None, load_settings(env={}), sources=sources, project_id=uuid4()
    )
    assert result["status"] == "conflict"
    assert len(requests) == len(result["llm_run_ids"]) == 2
    assert requests[0].user_prompt != requests[1].user_prompt
    assert requests[0].project_id is not None
    assert requests[0].max_tokens_override == 7000
    assert requests[0].prompt_version == claims.CLAIMS_VERSION


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["fallback", "length", "malformed"])
async def test_invalid_first_call_retains_diagnostic_and_does_not_retry(monkeypatch, fault):
    calls = []

    async def complete(*args):
        calls.append(args)
        return LLMCompletionResult(
            content="bad json",
            provider="fixture",
            model_name="fixture",
            fallback_used=fault == "fallback",
            finish_reason="length" if fault == "length" else "stop",
        )

    monkeypatch.setattr(claims, "complete_text", complete)
    with pytest.raises(WorldAuthorityAuditError) as captured:
        await claims.audit_world_cost_claims(
            None, load_settings(env={}), sources=source(), project_id=uuid4()
        )
    assert len(calls) == 1
    assert (
        captured.value.diagnostic["raw_reviews_for_diagnosis_only"][0]["raw_excerpt"] == "bad json"
    )
    assert captured.value.diagnostic["failed_review_index"] == 1


@pytest.mark.asyncio
async def test_invalid_second_call_preserves_both_attempts_without_retry(monkeypatch):
    sources = source()
    calls = []

    async def complete(*args):
        calls.append(args)
        payload = review(sources)
        if len(calls) == 2:
            payload["clauses"].pop()
        return LLMCompletionResult(
            content=json.dumps(payload),
            provider="fixture",
            model_name="fixture",
            llm_run_id=uuid4(),
        )

    monkeypatch.setattr(claims, "complete_text", complete)
    with pytest.raises(WorldAuthorityAuditError) as captured:
        await claims.audit_world_cost_claims(
            None,
            load_settings(env={}),
            sources=sources,
            project_id=uuid4(),
        )
    assert len(calls) == 2
    assert captured.value.diagnostic["failed_review_index"] == 2
    assert len(captured.value.diagnostic["llm_run_ids"]) == 2
    assert len(captured.value.diagnostic["raw_reviews_for_diagnosis_only"]) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["missing", "policy", "oversized"])
async def test_invalid_preflight_makes_no_model_call(monkeypatch, fault):
    sources = source()
    if fault == "missing":
        sources.pop("creation")
    elif fault == "policy":
        sources["creation"]["cost_style"] = "heavy"
    else:
        sources["world"]["power_system"]["hard_limits"] = "x" * 30000

    async def forbidden(*args):
        pytest.fail("Preflight failures must not call a model")

    monkeypatch.setattr(claims, "complete_text", forbidden)
    with pytest.raises(ValueError, match="WORLD_COST"):
        await claims.audit_world_cost_claims(
            None,
            load_settings(env={}),
            sources=sources,
            project_id=uuid4(),
        )
