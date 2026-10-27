import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

# ruff: noqa: RUF001 -- Chinese source text is intentionally exact.
from bestseller.services import world_source_partition as partition
from bestseller.services.ideology_kernel import cost_style_directive
from bestseller.services.llm import LLMCompletionResult
from bestseller.services.world_authority import (
    WorldAuthorityAuditError,
    world_authority_source_hash,
)
from bestseller.services.world_cost_claims import cost_clause_index, reconcile_cost_claims
from bestseller.settings import load_settings

pytestmark = pytest.mark.unit


def source():
    return {
        "creation": {"cost_style": "minimal", "cost_rule": cost_style_directive("minimal")},
        "world": {
            "power_system": {
                "hard_limits": "越阶会让主角手抖，但不会退阶。",
                "acquisition_method": "符纸需要三息制作。",
            }
        },
    }


def diagnostic(sources):
    rows = []
    for clause in cost_clause_index(sources):
        fact = {
            "effect": "no_cost_fact",
            "actor": "not_applicable",
            "usage_scope": "not_applicable",
            "polarity": "not_applicable",
        }
        if clause["path"][-1] == "hard_limits":
            fact = {
                "effect": "self_weakening",
                "actor": "protagonist",
                "usage_scope": "overreach",
                "polarity": "incurs",
            }
        rows.append({"clause_id": clause["clause_id"], "facts": [fact]})
    review = {
        "complete": True,
        "source_hash": world_authority_source_hash(sources),
        "clauses": rows,
    }
    return reconcile_cost_claims([review, review], sources=sources)


def review(sources, *, kind="remove_self_weakening"):
    targets = partition.source_patch_targets(sources=sources, diagnostic=diagnostic(sources))
    clauses = []
    for target in targets:
        quote = target["quote"]
        if target["path"][-1] == "hard_limits":
            marker = "手抖"
            at = quote.index(marker)
            segments = [
                {"quote": quote[:at], "kind": "keep"},
                {"quote": marker, "kind": kind},
                {"quote": quote[at + len(marker) :], "kind": "keep"},
            ]
        else:
            segments = [{"quote": quote, "kind": "keep"}]
        clauses.append({"clause_id": target["clause_id"], "segments": segments})
    return {
        "complete": True,
        "source_hash": world_authority_source_hash(sources),
        "clauses": clauses,
    }


def test_partition_compares_character_labels_not_segment_boundaries():
    sources = source()
    diag = diagnostic(sources)
    one = review(sources)
    two = review(sources)
    target = next(
        t
        for t in partition.source_patch_targets(sources=sources, diagnostic=diag)
        if t["path"][-1] == "hard_limits"
    )
    text = target["quote"]
    at = text.index("手抖")
    two["clauses"][0]["segments"] = [
        {"quote": text[:at], "kind": "keep"},
        {"quote": text[at : at + 1], "kind": "remove_self_weakening"},
        {"quote": text[at + 1 : at + 2], "kind": "remove_self_weakening"},
        {"quote": text[at + 2 :], "kind": "keep"},
    ]
    result = partition.reconcile_source_partition([one, two], sources=sources, diagnostic=diag)
    assert result["status"] == "partition_agreed"
    assert result["clauses"][0]["removable_spans"]


def test_unknown_or_disagreement_never_produces_spans():
    sources = source()
    diag = diagnostic(sources)
    one = review(sources)
    two = review(sources, kind="unknown")
    result = partition.reconcile_source_partition([one, two], sources=sources, diagnostic=diag)
    assert result["status"] == "review_required"
    assert all(not c["removable_spans"] for c in result["clauses"])


def test_terminal_punctuation_cannot_be_removed():
    sources = source()
    diag = diagnostic(sources)
    bad = review(sources)
    target = partition.source_patch_targets(sources=sources, diagnostic=diag)[0]
    bad["clauses"][0]["segments"] = [{"quote": target["quote"], "kind": "remove_self_weakening"}]
    with pytest.raises(ValueError, match="TERMINATOR"):
        partition.reconcile_source_partition([bad, bad], sources=sources, diagnostic=diag)


def test_candidate_recomputes_reviews_and_ignores_external_spans():
    sources = source()
    diag = diagnostic(sources)
    one = review(sources)
    report = partition.reconcile_source_partition([one, one], sources=sources, diagnostic=diag)
    report["clauses"][0]["removable_spans"] = [
        {"path": ["creation", "cost_rule"], "start": 0, "end": 99, "quote": "forged"}
    ]
    report["llm_run_ids"] = [str(uuid4()), str(uuid4())]
    candidate = partition.build_partition_candidate(
        sources=sources, diagnostic=diag, partition=report
    )
    assert candidate["version"] == partition.PARTITION_CANDIDATE_VERSION
    assert candidate["promotion_allowed"] is False
    assert candidate["partition_proof"]["clauses"][0]["removable_spans"]
    assert candidate["candidate_sources"]["creation"] == sources["creation"]


@pytest.mark.parametrize(
    "field, value, error",
    [
        ("version", "old", "UNSUPPORTED_VERSION"),
        ("llm_run_ids", [str(uuid4())], "MISSING_RECEIPTS"),
        ("llm_run_ids", [str(uuid4()), str(uuid4())[:-1]], "INVALID_RECEIPT"),
    ],
)
def test_candidate_requires_partition_version_and_two_unique_uuid_receipts(field, value, error):
    sources = source()
    diag = diagnostic(sources)
    report = partition.reconcile_source_partition(
        [review(sources), review(sources)], sources=sources, diagnostic=diag
    )
    report["llm_run_ids"] = [str(uuid4()), str(uuid4())]
    report[field] = value
    with pytest.raises(ValueError, match=error):
        partition.build_partition_candidate(sources=sources, diagnostic=diag, partition=report)


def test_segment_length_overflow_is_rejected_before_label_expansion():
    sources = source()
    diag = diagnostic(sources)
    report = review(sources)
    report["clauses"][0]["segments"][0]["quote"] += "x" * 100
    with pytest.raises(ValueError, match="LENGTH_EXCEEDED"):
        partition.reconcile_source_partition([report, report], sources=sources, diagnostic=diag)


@pytest.mark.asyncio
async def test_build_failure_is_wrapped_with_two_reviews_and_raw(monkeypatch):
    sources = source()
    diag = diagnostic(sources)

    def punctuation_only_review():
        targets = partition.source_patch_targets(sources=sources, diagnostic=diag)
        clauses = []
        for target in targets:
            quote = target["quote"]
            clauses.append(
                {
                    "clause_id": target["clause_id"],
                    "segments": [
                        {"quote": quote[:-1], "kind": "remove_self_weakening"},
                        {"quote": quote[-1:], "kind": "keep"},
                    ],
                }
            )
        return {
            "complete": True,
            "source_hash": world_authority_source_hash(sources),
            "clauses": clauses,
        }

    async def complete(session, settings, request):
        return LLMCompletionResult(
            content=json.dumps(punctuation_only_review(), ensure_ascii=False),
            provider="fixture",
            model_name="fixture",
            llm_run_id=uuid4(),
        )

    monkeypatch.setattr(partition, "complete_text", complete)
    with pytest.raises(WorldAuthorityAuditError) as captured:
        await partition.propose_partition_patch(
            None, load_settings(env={}), sources=sources, diagnostic=diag, project_id=uuid4()
        )
    assert captured.value.diagnostic["reviews"]
    assert len(captured.value.diagnostic["llm_run_ids"]) == 2


@pytest.mark.asyncio
async def test_proposal_uses_two_calls_and_returns_candidate(monkeypatch):
    sources = source()
    diag = diagnostic(sources)
    calls = []

    async def complete(session, settings, request):
        calls.append(request)
        return LLMCompletionResult(
            content=json.dumps(review(sources), ensure_ascii=False),
            provider="fixture",
            model_name="fixture",
            llm_run_id=uuid4(),
        )

    monkeypatch.setattr(partition, "complete_text", complete)
    result = await partition.propose_partition_patch(
        None, load_settings(env={}), sources=sources, diagnostic=diag, project_id=uuid4()
    )
    assert len(calls) == 2
    assert all(c.max_tokens_override == 3000 for c in calls)
    assert all(c.system_prompt == partition.PARTITION_SYSTEM_PROMPT for c in calls)
    assert all(c.prompt_version == partition.PARTITION_VERSION for c in calls)
    assert result["version"] == partition.PARTITION_CANDIDATE_VERSION
    assert len(result["partition_proof"]["llm_run_ids"]) == 2


@pytest.mark.asyncio
async def test_failed_receipt_preserves_raw_and_does_not_retry(monkeypatch):
    sources = source()
    diag = diagnostic(sources)
    calls = []

    async def complete(*args):
        calls.append(args)
        return LLMCompletionResult(content="bad json", provider="fixture", model_name="fixture")

    monkeypatch.setattr(partition, "complete_text", complete)
    with pytest.raises(WorldAuthorityAuditError) as captured:
        await partition.propose_partition_patch(
            None, load_settings(env={}), sources=sources, diagnostic=diag, project_id=uuid4()
        )
    assert len(calls) == 1
    assert (
        captured.value.diagnostic["raw_reviews_for_diagnosis_only"][0]["raw_excerpt"] == "bad json"
    )


@pytest.mark.asyncio
async def test_provider_exception_keeps_prior_evidence_and_does_not_leak_message(monkeypatch):
    sources = source()
    diag = diagnostic(sources)
    calls = []

    async def complete(*args):
        calls.append(args)
        if len(calls) == 1:
            return LLMCompletionResult(
                content=json.dumps(review(sources), ensure_ascii=False),
                provider="fixture",
                model_name="fixture",
                llm_run_id=uuid4(),
            )
        raise RuntimeError("secret provider response")

    monkeypatch.setattr(partition, "complete_text", complete)
    with pytest.raises(WorldAuthorityAuditError) as captured:
        await partition.propose_partition_patch(
            None, load_settings(env={}), sources=sources, diagnostic=diag, project_id=uuid4()
        )
    details = captured.value.diagnostic
    assert len(calls) == 2
    assert details["failed_review_index"] == 2
    assert len(details["reviews"]) == 1
    assert details["raw_reviews_for_diagnosis_only"][-1] == {"error_type": "RuntimeError"}
    assert "secret provider response" not in str(details)


@pytest.mark.asyncio
async def test_invalid_uuid_receipt_stops_before_second_request(monkeypatch):
    sources = source()
    diag = diagnostic(sources)
    calls = []

    async def complete(*args):
        calls.append(args)
        return SimpleNamespace(
            content=json.dumps(review(sources), ensure_ascii=False),
            provider="fixture",
            model_name="fixture",
            llm_run_id="not-a-uuid",
            fallback_used=False,
            finish_reason="stop",
        )

    monkeypatch.setattr(partition, "complete_text", complete)
    with pytest.raises(WorldAuthorityAuditError, match="RECEIPT"):
        await partition.propose_partition_patch(
            None, load_settings(env={}), sources=sources, diagnostic=diag, project_id=uuid4()
        )
    assert len(calls) == 1
