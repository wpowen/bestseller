import copy
import json
from uuid import uuid4

import pytest

from bestseller.services import world_source_patch as patch
from bestseller.services import world_source_patch_review as review
from bestseller.services.ideology_kernel import cost_style_directive
from bestseller.services.llm import LLMCompletionResult
from bestseller.services.world_authority import (
    WorldAuthorityAuditError,
    world_authority_source_hash,
)
from bestseller.services.world_cost_claims import cost_clause_index, reconcile_cost_claims
from bestseller.settings import load_settings

pytestmark = pytest.mark.unit


def _partition_candidate():
    from bestseller.services.world_source_partition import (
        build_partition_candidate,
        reconcile_source_partition,
    )

    sources = source()
    diagnostic = diagnosis(sources)
    raw = {
        "complete": True,
        "source_hash": world_authority_source_hash(sources),
        "clauses": [
            {
                "clause_id": diagnostic["conflicts"][0]["clause_id"],
                "segments": [
                    {"quote": "Overreach", "kind": "keep"},
                    {"quote": " leaves the pilot unable to steer", "kind": "remove_self_weakening"},
                    {"quote": ";", "kind": "keep"},
                ],
            }
        ],
    }
    proof = reconcile_source_partition(
        [raw, copy.deepcopy(raw)], sources=sources, diagnostic=diagnostic
    )
    proof["llm_run_ids"] = [str(uuid4()), str(uuid4())]
    candidate = build_partition_candidate(sources=sources, diagnostic=diagnostic, partition=proof)
    return sources, diagnostic, candidate


@pytest.mark.parametrize(
    "fault", [None, "missing_proof", "altered_spans", "altered_edit", "different_reviews"]
)
def test_partition_candidate_is_rebuilt_from_complete_original_reviews(fault):
    sources, diagnostic, candidate = _partition_candidate()
    if fault is None:
        rebuilt = review._validate_candidate(candidate, sources=sources, diagnostic=diagnostic)
        assert rebuilt["partition_proof"] == candidate["partition_proof"]
        assert rebuilt["promotion_allowed"] is False
        return
    if fault == "missing_proof":
        candidate.pop("partition_proof")
    elif fault == "altered_spans":
        candidate["partition_proof"]["clauses"][0]["protected_spans"] = []
    elif fault == "altered_edit":
        candidate["edits"][0]["replacement"] = ";"
    else:
        candidate["partition_proof"]["reviews"][0]["clauses"][0]["segments"][1]["kind"] = "keep"
    with pytest.raises(ValueError):
        review._validate_candidate(candidate, sources=sources, diagnostic=diagnostic)


@pytest.mark.asyncio
async def test_partition_protection_is_carried_to_real_review_request(monkeypatch):
    sources, diagnostic, candidate = _partition_candidate()
    calls = []

    async def complete(session, settings, request):
        calls.append(request)
        return LLMCompletionResult(
            content=json.dumps(_model_review(candidate)),
            provider="fixture",
            model_name="fixture",
            llm_run_id=uuid4(),
        )

    monkeypatch.setattr(review, "complete_text", complete)
    result = await review.review_world_source_patch(
        None,
        load_settings(env={}),
        sources=sources,
        diagnostic=diagnostic,
        candidate=candidate,
        project_id=uuid4(),
    )
    assert len(calls) == 2
    for request in calls:
        data = json.loads(request.user_prompt.split("edits=", 1)[1])
        assert data["protected_partition"] == candidate["partition_proof"]["clauses"]
    assert result["promotion_allowed"] is False


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
    item = {"complete": True, "source_hash": world_authority_source_hash(sources), "clauses": rows}
    return reconcile_cost_claims([item, copy.deepcopy(item)], sources=sources)


def response(sources, report):
    return {
        "source_hash": world_authority_source_hash(sources),
        "edits": [
            {"clause_id": c["clause_id"], "replacement": "Overreach;"} for c in report["conflicts"]
        ],
    }


def _candidate():
    sources = source()
    report = diagnosis(sources)
    candidate = patch.build_source_patch_candidate(
        response(sources, report), sources=sources, diagnostic=report
    )
    return sources, report, candidate


def _model_review(candidate, verdict="preserved", *, forged=False):
    checks = []
    for edit in candidate["edits"]:
        for axis in review.REVIEW_AXES:
            checks.append(
                {
                    "clause_id": edit["clause_id"],
                    "axis": axis,
                    "verdict": verdict,
                    "change_basis": {
                        "preserved": "authorized_cost_removal"
                        if axis == "cost_conflict_resolved"
                        else "unchanged",
                        "violated": "unlicensed_change",
                        "uncertain": "unknown",
                    }[verdict],
                    "before_quote": "forged" if forged else edit["quote"],
                    "after_quote": "forged" if forged else edit["replacement"][:12],
                    "reason": "保留同一条款边界。",
                }
            )
    return {
        "complete": True,
        "source_hash": candidate["source_hash"],
        "candidate_hash": candidate["candidate_hash"],
        "reviews": [{"checks": checks}],
    }


@pytest.mark.asyncio
async def test_two_independent_reviews_pass_but_never_promote(monkeypatch):
    sources, report, candidate = _candidate()
    calls = []

    async def complete(_session, _settings, request):
        calls.append(request)
        return LLMCompletionResult(
            content=json.dumps(_model_review(candidate), ensure_ascii=False),
            provider="real",
            model_name="critic",
            llm_run_id=uuid4(),
        )

    monkeypatch.setattr(review, "complete_text", complete)
    result = await review.review_world_source_patch(
        None,
        load_settings(env={}),
        sources=sources,
        diagnostic=report,
        candidate=candidate,
        project_id=uuid4(),
    )
    assert len(calls) == 2
    assert all(call.max_tokens_override == 4000 for call in calls)
    assert all(call.temperature_override == 0.1 for call in calls)
    for call in calls:
        context = json.loads(call.user_prompt.split("edits=", 1)[1])
        assert context["sources"] == sources
        assert context["repair_policy"] == patch.SOURCE_PATCH_POLICY
        assert context["edits"][0]["deleted_spans"] == candidate["edits"][0]["deleted_spans"]
    assert result["status"] == "semantic_checks_passed"
    assert result["promotion_allowed"] is False
    assert result["scope"] == "changed_clauses_only"
    assert result["unresolved_clause_ids"] == candidate["unresolved_clause_ids"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("first", "second", "expected"),
    [
        ("violated", "preserved", "rejected"),
        ("uncertain", "preserved", "review_required"),
        ("preserved", "violated", "rejected"),
        ("preserved", "uncertain", "review_required"),
    ],
)
async def test_failed_or_disputed_axis_is_readable_and_not_promoted(
    monkeypatch, first, second, expected
):
    sources, report, candidate = _candidate()
    responses = [_model_review(candidate, first), _model_review(candidate, second)]
    calls = []

    async def complete(_session, _settings, _request):
        calls.append(True)
        return LLMCompletionResult(
            content=json.dumps(responses[len(calls) - 1], ensure_ascii=False),
            provider="real",
            model_name="critic",
            llm_run_id=uuid4(),
        )

    monkeypatch.setattr(review, "complete_text", complete)
    result = await review.review_world_source_patch(
        None,
        load_settings(env={}),
        sources=sources,
        diagnostic=report,
        candidate=candidate,
        project_id=uuid4(),
    )
    assert result["status"] == expected
    assert result["failed_axes"] or result["disagreement_axes"]
    assert result["promotion_allowed"] is False
    if result["failed_axes"]:
        assert all(item["verdict"] == "violated" for item in result["failed_axes"])
    else:
        assert all(item["verdict"] == "uncertain" for item in result["disagreement_axes"])


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["candidate_hash", "candidate_sources", "edits"])
async def test_candidate_tampering_is_rejected_before_any_model_call(monkeypatch, field):
    sources, report, candidate = _candidate()
    tampered = copy.deepcopy(candidate)
    if field == "candidate_hash":
        tampered[field] = "bad"
    elif field == "candidate_sources":
        tampered[field]["world"]["power_system"]["hard_limits"] += " forged"
    else:
        tampered[field][0]["replacement"] += " forged"

    async def boom(*_args, **_kwargs):
        raise AssertionError("must not call model for a tampered candidate")

    monkeypatch.setattr(review, "complete_text", boom)
    with pytest.raises(ValueError, match="SOURCE_PATCH"):
        await review.review_world_source_patch(
            None,
            load_settings(env={}),
            sources=sources,
            diagnostic=report,
            candidate=tampered,
            project_id=uuid4(),
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind",
    [
        "forged_quote",
        "unknown_axis",
        "missing_axis",
        "extra_field",
        "two_reviews",
        "invalid_id",
        "invalid_axis",
        "invalid_verdict",
        "duplicate_axis",
        "after_quote",
        "hash",
    ],
)
async def test_review_protocol_fails_closed_without_retry(monkeypatch, kind):
    sources, report, candidate = _candidate()
    payload = _model_review(candidate)
    check = payload["reviews"][0]["checks"][0]
    if kind == "forged_quote":
        check["before_quote"] = "not in source"
    elif kind == "unknown_axis":
        check["axis"] = "new_mechanism"
    elif kind == "missing_axis":
        payload["reviews"][0]["checks"].pop()
    elif kind == "two_reviews":
        payload["reviews"].append(copy.deepcopy(payload["reviews"][0]))
    elif kind in {"invalid_id", "invalid_axis", "invalid_verdict"}:
        check[
            {"invalid_id": "clause_id", "invalid_axis": "axis", "invalid_verdict": "verdict"}[kind]
        ] = []
    elif kind == "duplicate_axis":
        payload["reviews"][0]["checks"][-1] = copy.deepcopy(check)
    elif kind == "after_quote":
        check["after_quote"] = "invented quotation"
    elif kind == "hash":
        payload["candidate_hash"] = "stale"
    else:
        check["extra"] = "reject"
    calls = []

    async def complete(_session, _settings, _request):
        calls.append(True)
        return LLMCompletionResult(
            content=json.dumps(payload, ensure_ascii=False),
            provider="real",
            model_name="critic",
            llm_run_id=uuid4(),
        )

    monkeypatch.setattr(review, "complete_text", complete)
    with pytest.raises(WorldAuthorityAuditError) as exc:
        await review.review_world_source_patch(
            None,
            load_settings(env={}),
            sources=sources,
            diagnostic=report,
            candidate=candidate,
            project_id=uuid4(),
        )
    assert len(calls) == 1
    assert exc.value.diagnostic["raw_reviews_for_diagnosis_only"]


@pytest.mark.asyncio
async def test_source_budget_rejected_before_model_call(monkeypatch):
    sources, report, candidate = _candidate()
    monkeypatch.setattr(review, "MAX_SOURCE_CHARS", 10)

    async def fail(*args):
        raise AssertionError("budget must be checked before model call")

    monkeypatch.setattr(review, "complete_text", fail)
    with pytest.raises(ValueError, match="BUDGET_EXCEEDED"):
        await review.review_world_source_patch(
            None,
            load_settings(env={}),
            sources=sources,
            diagnostic=report,
            candidate=candidate,
            project_id=uuid4(),
        )


def test_uncertain_empty_evidence_is_valid_but_stays_pending():
    _, _, candidate = _candidate()
    payload = _model_review(candidate, "uncertain")
    for check in payload["reviews"][0]["checks"]:
        check["before_quote"] = check["after_quote"] = ""
    validated = review._validate_review_payload(
        payload,
        candidate=candidate,
        source_hash=candidate["source_hash"],
        candidate_hash=candidate["candidate_hash"],
        review_count=1,
    )
    validated["reviews"] *= 2
    result = review._result_from_reviews(candidate, validated, llm_run_ids=[])
    assert result["status"] == "review_required"
    assert result["promotion_allowed"] is False


@pytest.mark.parametrize(
    "fault", ["basis_mismatch", "missing_basis", "unbound_deletion", "unknown_basis"]
)
def test_change_basis_must_match_verdict_and_actual_deletion(fault):
    _, _, candidate = _candidate()
    payload = _model_review(candidate)
    check = payload["reviews"][0]["checks"][0]
    if fault == "basis_mismatch":
        check["change_basis"] = "unlicensed_change"
    elif fault == "missing_basis":
        del check["change_basis"]
    elif fault == "unbound_deletion":
        check["change_basis"] = "authorized_cost_removal"
        check["before_quote"] = "Overreach"
    else:
        check["change_basis"] = "blanket_approval"
    with pytest.raises(ValueError, match="SOURCE_PATCH_REVIEW"):
        review._validate_review_payload(
            payload,
            candidate=candidate,
            source_hash=candidate["source_hash"],
            candidate_hash=candidate["candidate_hash"],
            review_count=1,
        )


def test_authorized_removal_evidence_is_retained_without_promoting():
    _, _, candidate = _candidate()
    payload = _model_review(candidate)
    check = payload["reviews"][0]["checks"][-1]
    check["change_basis"] = "authorized_cost_removal"
    check["before_quote"] = candidate["edits"][0]["quote"]
    validated = review._validate_review_payload(
        payload,
        candidate=candidate,
        source_hash=candidate["source_hash"],
        candidate_hash=candidate["candidate_hash"],
        review_count=1,
    )
    validated["reviews"] *= 2
    result = review._result_from_reviews(candidate, validated, llm_run_ids=[])
    assert result["reviews"][0]["checks"][-1]["change_basis"] == "authorized_cost_removal"
    assert result["promotion_allowed"] is False


@pytest.mark.parametrize(
    "basis,verdict", [("authorized_cost_removal", "preserved"), ("unlicensed_change", "violated")]
)
def test_deleted_content_absence_can_be_evidence_without_inventing_new_quote(basis, verdict):
    _, _, candidate = _candidate()
    payload = _model_review(candidate)
    check = payload["reviews"][0]["checks"][-1]
    check.update(
        change_basis=basis,
        verdict=verdict,
        before_quote=candidate["edits"][0]["quote"],
        after_quote="",
    )
    result = review._validate_review_payload(
        payload,
        candidate=candidate,
        source_hash=candidate["source_hash"],
        candidate_hash=candidate["candidate_hash"],
        review_count=1,
    )
    assert result["reviews"][0]["checks"][-1]["after_quote"] == ""


@pytest.mark.parametrize("fault", ["protected_axis", "all_unchanged", "partial_coverage", "policy"])
def test_every_deleted_span_needs_a_permitted_change_explanation(fault):
    sources, diagnostic, candidate = _candidate()
    payload = _model_review(candidate)
    checks = payload["reviews"][0]["checks"]
    if fault == "protected_axis":
        checks[0]["change_basis"] = "authorized_cost_removal"
    elif fault == "all_unchanged":
        for check in checks:
            check["change_basis"] = "unchanged"
    elif fault == "partial_coverage":
        checks[-1]["before_quote"] = candidate["edits"][0]["deleted_spans"][0]["quote"][:1]
    else:
        candidate["repair_policy"] = {"allow_everything": True}
        with pytest.raises(ValueError, match="POLICY_MISMATCH"):
            review._validate_candidate(candidate, sources=sources, diagnostic=diagnostic)
        return
    with pytest.raises(ValueError, match="SOURCE_PATCH_REVIEW"):
        review._validate_review_payload(
            payload,
            candidate=candidate,
            source_hash=candidate["source_hash"],
            candidate_hash=candidate["candidate_hash"],
            review_count=1,
        )


def test_one_explained_span_cannot_hide_another_deletion():
    sources = source()
    sources["world"]["power_system"]["hard_limits"] = "Anchor lasts two days but weakens pilot;"
    diagnostic = diagnosis(sources)
    payload = response(sources, diagnostic)
    payload["edits"][0]["replacement"] = "Anchor lasts days;"
    candidate = patch.build_source_patch_candidate(payload, sources=sources, diagnostic=diagnostic)
    spans = candidate["edits"][0]["deleted_spans"]
    assert len(spans) >= 2
    reviewed = _model_review(candidate)
    reviewed["reviews"][0]["checks"][-1]["before_quote"] = spans[0]["quote"]
    with pytest.raises(ValueError, match="UNEXPLAINED_DELETION"):
        review._validate_review_payload(
            reviewed,
            candidate=candidate,
            source_hash=candidate["source_hash"],
            candidate_hash=candidate["candidate_hash"],
            review_count=1,
        )


@pytest.mark.parametrize(
    "original,kept",
    [
        ("Beacon lasts two days, but weakens the operator for two days;", "Beacon lasts two days;"),
        ("结界维持三日，但使用者虚弱三日；", "结界维持三日；"),  # noqa: RUF001 -- exact source punctuation
    ],
)
def test_duration_deletion_is_reviewed_not_automatically_authorized(original, kept):
    sources = source()
    sources["world"]["power_system"]["hard_limits"] = original
    diagnostic = diagnosis(sources)
    payload = response(sources, diagnostic)
    payload["edits"][0]["replacement"] = kept
    candidate = patch.build_source_patch_candidate(payload, sources=sources, diagnostic=diagnostic)
    model = _model_review(candidate)
    row = next(c for c in model["reviews"][0]["checks"] if c["axis"] == "time_and_information")
    row.update(
        change_basis="unlicensed_change",
        verdict="violated",
        before_quote=original,
        after_quote=kept,
        reason="Fixture counterevidence must remain blocking.",
    )
    checked = review._validate_review_payload(
        model,
        candidate=candidate,
        source_hash=candidate["source_hash"],
        candidate_hash=candidate["candidate_hash"],
        review_count=1,
    )
    checked["reviews"] *= 2
    result = review._result_from_reviews(candidate, checked, llm_run_ids=[])
    assert result["status"] == "rejected"
    assert result["promotion_allowed"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["length", "fallback", "second_invalid"])
async def test_provider_or_later_review_failure_keeps_receipts_without_retry(monkeypatch, failure):
    sources, diagnostic, candidate = _candidate()
    calls = []

    async def complete(*args):
        run_id = uuid4()
        calls.append(run_id)
        content = json.dumps(_model_review(candidate))
        if failure == "second_invalid" and len(calls) == 2:
            content = "not JSON"
        return LLMCompletionResult(
            content=content,
            provider="real",
            model_name="critic",
            llm_run_id=run_id,
            finish_reason="length" if failure == "length" else "stop",
            fallback_used=failure == "fallback",
        )

    monkeypatch.setattr(review, "complete_text", complete)
    with pytest.raises(WorldAuthorityAuditError) as captured:
        await review.review_world_source_patch(
            None,
            load_settings(env={}),
            sources=sources,
            diagnostic=diagnostic,
            candidate=candidate,
            project_id=uuid4(),
        )
    assert len(calls) == (2 if failure == "second_invalid" else 1)
    assert captured.value.diagnostic["llm_run_ids"] == [str(value) for value in calls]
    assert len(captured.value.diagnostic["raw_reviews_for_diagnosis_only"]) == len(calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", [True, False])
async def test_missing_or_duplicate_call_receipt_cannot_pass(monkeypatch, missing):
    sources, diagnostic, candidate = _candidate()
    run_id = None if missing else uuid4()
    calls = []

    async def complete(*args):
        calls.append(True)
        return LLMCompletionResult(
            content=json.dumps(_model_review(candidate)),
            provider="real",
            model_name="critic",
            llm_run_id=run_id,
        )

    monkeypatch.setattr(review, "complete_text", complete)
    with pytest.raises(WorldAuthorityAuditError, match="RECEIPT") as captured:
        await review.review_world_source_patch(
            None,
            load_settings(env={}),
            sources=sources,
            diagnostic=diagnostic,
            candidate=candidate,
            project_id=uuid4(),
        )
    assert len(calls) == (1 if missing else 2)
    assert captured.value.diagnostic["status"] == "review_required"
