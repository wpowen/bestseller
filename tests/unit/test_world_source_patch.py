import copy
import json
from uuid import uuid4

import pytest

from bestseller.services import world_source_patch as patch
from bestseller.services.ideology_kernel import cost_style_directive
from bestseller.services.llm import LLMCompletionResult
from bestseller.services.world_authority import (
    WorldAuthorityAuditError,
    world_authority_source_hash,
)
from bestseller.services.world_cost_claims import cost_clause_index, reconcile_cost_claims
from bestseller.settings import load_settings

pytestmark = pytest.mark.unit


def test_candidate_cannot_delete_clause_terminator():
    sources = source()
    report = diagnosis(sources)
    payload = response(sources, report)
    payload["edits"][0]["replacement"] = "Overreach"
    with pytest.raises(ValueError, match="TERMINATOR"):
        patch.build_source_patch_candidate(payload, sources=sources, diagnostic=report)


def source():
    return {
        "creation": {
            "cost_style": "minimal",
            "cost_rule": cost_style_directive("minimal"),
            "creation_protagonist_name": "Aster",
        },
        "world": {
            "power_system": {
                "hard_limits": "Overreach leaves the pilot unable to steer;",
                "acquisition_method": "A compass reads direction.",
                "tiers": ["A", "B"],
            },
            "rules": [],
        },
    }


def diagnosis(sources):
    rows = []
    for clause in cost_clause_index(sources):
        effect = clause["path"][-1] in {"hard_limits", "breakthrough_cost"}
        fact = {
            "effect": "self_weakening" if effect else "no_cost_fact",
            "actor": "protagonist" if effect else "not_applicable",
            "usage_scope": "overreach" if effect else "not_applicable",
            "polarity": "incurs" if effect else "not_applicable",
        }
        rows.append({"clause_id": clause["clause_id"], "facts": [fact]})
    review = {
        "complete": True,
        "source_hash": world_authority_source_hash(sources),
        "clauses": rows,
    }
    return reconcile_cost_claims([review, review], sources=sources)


def response(sources, report):
    return {
        "source_hash": world_authority_source_hash(sources),
        "edits": [
            {"clause_id": c["clause_id"], "replacement": "Overreach leaves pilot unable steer;"}
            for c in report["conflicts"]
        ],
    }


def test_only_target_span_changes_and_candidate_cannot_promote():
    sources = source()
    sources["world"]["power_system"]["hard_limits"] += "This rule remains."
    report = diagnosis(sources)
    # The fixture classifies both hard-limit sentences; use one actual target
    # by preserving a non-cost classification in both underlying reviews.
    for review in report["reviews"]:
        review["clauses"][-1]["facts"] = [
            {
                "effect": "no_cost_fact",
                "actor": "not_applicable",
                "usage_scope": "not_applicable",
                "polarity": "not_applicable",
            }
        ]
    before = copy.deepcopy(sources)
    targets = patch.source_patch_targets(sources=sources, diagnostic=report)
    payload = {
        "source_hash": report["source_hash"],
        "edits": [
            {
                "clause_id": targets[0]["clause_id"],
                "replacement": "Overreach leaves pilot unable steer;",
            }
        ],
    }
    result = patch.build_source_patch_candidate(payload, sources=sources, diagnostic=report)
    assert result["candidate_sources"]["world"]["power_system"]["hard_limits"] == (
        "Overreach leaves pilot unable steer;This rule remains."
    )
    assert result["candidate_sources"]["creation"] == sources["creation"]
    for key in ("acquisition_method", "tiers"):
        assert (
            result["candidate_sources"]["world"]["power_system"][key]
            == (sources["world"]["power_system"][key])
        )
    assert sources == before
    assert result["status"] == "candidate_pending_review"
    assert result["promotion_allowed"] is False
    assert result["semantic_review_required"] is True
    assert result["repair_policy"]["status"] == "proposed_scope_not_automatic_approval"
    assert result["edits"][0]["deleted_spans"]


def test_forged_conflict_paths_are_not_executable():
    sources = source()
    report = diagnosis(sources)
    expected = patch.source_patch_targets(sources=sources, diagnostic=report)
    report["conflicts"] = [{"path": ["creation", "cost_style"], "clause_id": "forged"}]
    assert patch.source_patch_targets(sources=sources, diagnostic=report) == expected


@pytest.mark.parametrize("external_unresolved", [None, [], ["forged"]])
def test_candidate_recomputes_disagreement_and_preserves_triggering_facts(external_unresolved):
    sources = source()
    report = diagnosis(sources)
    conflict = report["conflicts"][0]
    clause = next(
        c for c in report["reviews"][0]["clauses"] if c["clause_id"] == conflict["clause_id"]
    )
    trigger = copy.deepcopy(clause["facts"][0])
    clause["facts"].append({**trigger, "polarity": "absent"})
    if external_unresolved is None:
        report.pop("unresolved_clause_ids")
    else:
        report["unresolved_clause_ids"] = external_unresolved
    result = patch.build_source_patch_candidate(
        response(sources, report), sources=sources, diagnostic=report
    )
    assert result["unresolved_clause_ids"] == [conflict["clause_id"]]
    assert result["edits"][0]["triggering_facts"] == [trigger]
    assert result["promotion_allowed"] is False


@pytest.mark.parametrize("fault", ["source", "version", "review_hash", "missing_reviews"])
def test_stale_or_invalid_diagnostic_cannot_generate_targets(fault):
    sources = source()
    report = diagnosis(sources)
    if fault == "source":
        sources["world"]["power_system"]["tiers"].append("C")
    elif fault == "version":
        report["version"] = "old"
    elif fault == "review_hash":
        report["reviews"][0]["source_hash"] = "old"
    else:
        report["reviews"].pop()
    with pytest.raises(ValueError, match="SOURCE_PATCH"):
        patch.source_patch_targets(sources=sources, diagnostic=report)


@pytest.mark.parametrize("fault", ["extra_path", "unknown_id", "empty", "large", "missing", "hash"])
def test_invalid_model_edits_fail_closed(fault):
    sources = source()
    report = diagnosis(sources)
    payload = response(sources, report)
    if fault == "extra_path":
        payload["edits"][0]["path"] = ["creation", "cost_style"]
    elif fault == "unknown_id":
        payload["edits"][0]["clause_id"] = "C999"
    elif fault == "empty":
        payload["edits"][0]["replacement"] = ""
    elif fault == "large":
        payload["edits"][0]["replacement"] = "x" * 2000
    elif fault == "missing":
        payload["edits"] = []
    else:
        payload["source_hash"] = "old"
    with pytest.raises(ValueError, match="SOURCE_PATCH"):
        patch.build_source_patch_candidate(payload, sources=sources, diagnostic=report)


def test_multiple_spans_in_one_field_use_original_offsets():
    sources = source()
    sources["world"]["power_system"]["hard_limits"] = (
        "Overreach leaves the pilot unable to steer;" * 2
    )
    report = diagnosis(sources)
    result = patch.build_source_patch_candidate(
        response(sources, report), sources=sources, diagnostic=report
    )
    assert result["candidate_sources"]["world"]["power_system"]["hard_limits"] == (
        "Overreach leaves pilot unable steer;" * 2
    )


@pytest.mark.parametrize(
    ("replacement", "error"),
    [
        ("Overreach leaves the pilot unable to steer safely;", "SOURCE_PATCH_ADDITION_FORBIDDEN"),
        ("Overreach leaves the pilot unable to wheel;", "SOURCE_PATCH_ADDITION_FORBIDDEN"),
        ("Overreach leaves the pilot unable to steer;", "SOURCE_PATCH_NO_CHANGE"),
        ("Overreach leaves the pilot unable steer;", "VALID_DELETION"),
    ],
)
def test_replacement_must_be_strict_deletion_subsequence(replacement, error):
    sources = source()
    report = diagnosis(sources)
    payload = response(sources, report)
    payload["edits"][0]["replacement"] = replacement
    if error == "VALID_DELETION":
        result = patch.build_source_patch_candidate(payload, sources=sources, diagnostic=report)
        assert result["promotion_allowed"] is False
    else:
        with pytest.raises(ValueError, match=error):
            patch.build_source_patch_candidate(payload, sources=sources, diagnostic=report)


def test_deleted_spans_are_relative_and_merge_adjacent_ranges():
    original = "A bc de!"
    spans = patch._deleted_spans(original, "A bde!")
    assert spans == [{"start": 3, "end": 5, "quote": "c "}]


def test_candidate_carries_triggering_facts_and_never_promotes():
    sources = source()
    report = diagnosis(sources)
    result = patch.build_source_patch_candidate(
        response(sources, report), sources=sources, diagnostic=report
    )
    assert result["edits"][0]["triggering_facts"]
    assert result["promotion_allowed"] is False


def test_tier_data_cannot_become_editable_from_a_cost_classification():
    sources = source()
    sources["world"]["power_system"]["tier_progression"] = [
        {"breakthrough_cost": "Loss of memory."}
    ]
    with pytest.raises(ValueError, match="PROTECTED_FIELD"):
        patch.source_patch_targets(sources=sources, diagnostic=diagnosis(sources))


@pytest.mark.asyncio
async def test_candidate_proposal_is_one_call_not_a_retry_loop(monkeypatch):
    sources = source()
    report = diagnosis(sources)
    calls = []

    async def complete(session, settings, request):
        calls.append(request)
        return LLMCompletionResult(
            content=json.dumps(response(sources, report)),
            provider="fixture",
            model_name="fixture",
            llm_run_id=uuid4(),
        )

    monkeypatch.setattr(patch, "complete_text", complete)
    result = await patch.propose_world_source_patch(
        None, load_settings(env={}), sources=sources, diagnostic=report, project_id=uuid4()
    )
    assert len(calls) == 1
    assert calls[0].max_tokens_override == 1800
    assert result["promotion_allowed"] is False


@pytest.mark.asyncio
async def test_failed_proposal_preserves_raw_output_and_stops(monkeypatch):
    sources = source()
    calls = []

    async def complete(*args):
        calls.append(args)
        return LLMCompletionResult(content="bad json", provider="fixture", model_name="fixture")

    monkeypatch.setattr(patch, "complete_text", complete)
    with pytest.raises(WorldAuthorityAuditError) as captured:
        await patch.propose_world_source_patch(
            None,
            load_settings(env={}),
            sources=sources,
            diagnostic=diagnosis(sources),
            project_id=uuid4(),
        )
    assert len(calls) == 1
    assert (
        captured.value.diagnostic["raw_reviews_for_diagnosis_only"][0]["raw_excerpt"] == "bad json"
    )
