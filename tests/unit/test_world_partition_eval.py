import copy
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from bestseller.services import world_partition_eval as evaluation
from bestseller.services.llm import LLMCompletionResult
from bestseller.services.world_source_partition import PARTITION_SYSTEM_PROMPT, PARTITION_VERSION
from bestseller.settings import load_settings

pytestmark = pytest.mark.unit


def fixture():
    return json.loads(Path("config/evaluation/world_partition_semantics.json").read_text())


def answer(prepared):
    return {
        "complete": True,
        "source_hash": prepared["source_hash"],
        "clauses": copy.deepcopy(prepared["reference_clauses"]),
    }


def test_references_never_enter_blind_input():
    prepared = evaluation.prepare_eval(fixture())
    context = json.loads(prepared["context"])
    assert set(context) == {"sources", "targets", "repair_policy"}
    assert len(context["targets"]) == 6
    assert "rationale" not in prepared["context"]
    assert "segments" not in prepared["context"]
    assert "axis" not in prepared["context"]


def test_exact_reference_passes_but_never_grants_authority():
    prepared = evaluation.prepare_eval(fixture())
    report = evaluation.grade_eval(answer(prepared), prepared=prepared)
    assert report["passed_cases"] == 6
    assert report["status"] == "reference_match"
    assert report["promotion_allowed"] is False
    assert report["production_ready"] is False


def test_segment_boundaries_do_not_change_semantic_grade():
    prepared = evaluation.prepare_eval(fixture())
    payload = answer(prepared)
    first = payload["clauses"][0]["segments"].pop(0)
    split = [{"quote": char, "kind": first["kind"]} for char in first["quote"]]
    payload["clauses"][0]["segments"] = split + payload["clauses"][0]["segments"]
    assert evaluation.grade_eval(payload, prepared=prepared)["passed_cases"] == 6


def test_wrong_unknown_and_missed_removal_fail_independently():
    prepared = evaluation.prepare_eval(fixture())
    payload = answer(prepared)
    for row in payload["clauses"]:
        for segment in row["segments"]:
            if segment["kind"] != "keep":
                segment["kind"] = "keep"
    report = evaluation.grade_eval(payload, prepared=prepared)
    assert report["status"] == "reference_mismatch"
    assert report["missed_removal_chars"] > 0
    assert report["wrong_unknown_chars"] > 0


def test_protected_removal_is_explicit_failure():
    prepared = evaluation.prepare_eval(fixture())
    payload = answer(prepared)
    first = payload["clauses"][0]["segments"][0]
    assert first["kind"] == "keep"
    first["kind"] = "remove_self_weakening"
    report = evaluation.grade_eval(payload, prepared=prepared)
    assert report["protected_removal_chars"] > 0
    assert report["status"] == "reference_mismatch"


@pytest.mark.parametrize("mutation", ["missing", "hash", "duplicate"])
def test_invalid_coverage_or_hash_is_not_scored_as_success(mutation):
    prepared = evaluation.prepare_eval(fixture())
    payload = answer(prepared)
    if mutation == "missing":
        payload["clauses"].pop()
    elif mutation == "duplicate":
        payload["clauses"][-1] = payload["clauses"][0]
    else:
        payload["source_hash"] = "stale"
    with pytest.raises(ValueError):
        evaluation.grade_eval(payload, prepared=prepared)


def test_one_invalid_case_does_not_hide_other_results_or_pass_the_batch():
    prepared = evaluation.prepare_eval(fixture())
    payload = answer(prepared)
    payload["clauses"][1]["segments"][-1]["kind"] = "remove_self_weakening"
    report = evaluation.grade_eval(payload, prepared=prepared)
    assert report["status"] == "evaluation_failed"
    assert report["passed_cases"] == 5
    assert report["total_cases"] == 6
    assert report["invalid_cases"] == 1
    assert report["metrics_scope"] == "valid_cases_only"
    assert report["cases"][1]["passed"] is None
    assert report["cases"][1]["error"] == "WORLD_PARTITION_TERMINATOR_REMOVAL"
    assert report["production_ready"] is False


def test_replay_uses_raw_response_preserves_original_and_does_not_call_model():
    data = fixture()
    prepared = evaluation.prepare_eval(data)
    original = {
        "version": evaluation.EVAL_VERSION,
        "fixture_hash": prepared["fixture_hash"],
        "source_hash": prepared["source_hash"],
        "raw_response": json.dumps(answer(prepared)),
        "llm_run_ids": [str(uuid4())],
        "prompt_version": "original-prompt",
        "status": "evaluation_failed",
        "error": "old-failure",
    }
    before = copy.deepcopy(original)
    report = evaluation.replay_partition_eval(original, fixture=data)
    assert original == before
    assert report["new_llm_calls"] == 0
    assert report["prompt_version"] == "original-prompt"
    assert report["original_status"] == "evaluation_failed"
    assert report["status"] == "reference_match"
    assert report["production_ready"] is False
    assert report["llm_run_ids"] == original["llm_run_ids"]


@pytest.mark.parametrize("field", ["fixture_hash", "source_hash"])
def test_replay_rejects_changed_reference_or_source(field):
    data = fixture()
    prepared = evaluation.prepare_eval(data)
    original = {
        "version": evaluation.EVAL_VERSION,
        "fixture_hash": prepared["fixture_hash"],
        "source_hash": prepared["source_hash"],
        "raw_response": "{}",
    }
    original[field] = "changed"
    with pytest.raises(ValueError, match="REPLAY_BINDING"):
        evaluation.replay_partition_eval(original, fixture=data)


@pytest.mark.parametrize("explicit_flag", [False, True])
def test_replay_cannot_turn_provider_fallback_into_a_matching_reference(explicit_flag):
    data = fixture()
    prepared = evaluation.prepare_eval(data)
    original = {
        "version": evaluation.EVAL_VERSION,
        "fixture_hash": prepared["fixture_hash"],
        "source_hash": prepared["source_hash"],
        "raw_response": json.dumps(answer(prepared)),
        "llm_run_ids": [str(uuid4())],
        "error": "PARTITION_EVAL_PROVIDER_UNAVAILABLE",
    }
    if explicit_flag:
        original.pop("error")
        original.update({"provider": "openai", "finish_reason": "stop", "fallback_used": True})
    with pytest.raises(ValueError, match="REPLAY_PROVIDER_UNAVAILABLE"):
        evaluation.replay_partition_eval(original, fixture=data)


def test_replay_cannot_erase_requested_vs_actual_model_mismatch():
    data = fixture()
    prepared = evaluation.prepare_eval(data)
    original = {
        "version": evaluation.EVAL_VERSION,
        "fixture_hash": prepared["fixture_hash"],
        "source_hash": prepared["source_hash"],
        "raw_response": json.dumps(answer(prepared)),
        "llm_run_ids": [str(uuid4())],
        "requested_model": "expected",
        "model_name": "wrong",
    }
    with pytest.raises(ValueError, match="REPLAY_MODEL_ROUTE_MISMATCH"):
        evaluation.replay_partition_eval(original, fixture=data)


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [False, "fallback", "receipt", "json"])
async def test_one_call_preserves_evidence_and_never_retries(monkeypatch, bad):
    data = fixture()
    prepared = evaluation.prepare_eval(data)
    requests = []

    async def complete(session, settings, request):
        requests.append(request)
        return LLMCompletionResult(
            content="bad" if bad == "json" else json.dumps(answer(prepared)),
            provider="fixture",
            model_name="fixture",
            fallback_used=bad == "fallback",
            llm_run_id=None if bad == "receipt" else uuid4(),
        )

    monkeypatch.setattr(evaluation, "complete_text", complete)
    report = await evaluation.run_partition_eval(
        None, load_settings(env={}), fixture=data, project_id=uuid4()
    )
    assert len(requests) == 1
    assert requests[0].model_catalog_key is None
    assert requests[0].system_prompt == PARTITION_SYSTEM_PROMPT
    assert report["prompt_version"] == PARTITION_VERSION
    assert report["raw_response"]
    assert report["status"] == ("reference_match" if not bad else "evaluation_failed")
    assert report["production_ready"] is False
    if bad:
        assert report["passed_cases"] is None
        assert len(report["cases"]) == 6
        assert all(row["passed"] is None for row in report["cases"])


@pytest.mark.asyncio
@pytest.mark.parametrize("available,mismatch", [(False, False), (True, False), (True, True)])
async def test_shadow_route_is_explicit_available_and_receipt_bound(
    monkeypatch, available, mismatch
):
    data = fixture()
    prepared = evaluation.prepare_eval(data)
    settings = load_settings(env={})
    key = settings.llm.independent_judge_primary_model_key
    monkeypatch.setattr(
        evaluation,
        "get_model_catalog_entry",
        lambda k: SimpleNamespace(model="shadow-model", available=available, id=k),
    )
    calls = []

    async def complete(session, settings, request):
        calls.append(request)
        return LLMCompletionResult(
            content=json.dumps(answer(prepared)),
            provider="fixture",
            model_name="wrong-model" if mismatch else "shadow-model",
            llm_run_id=uuid4(),
        )

    monkeypatch.setattr(evaluation, "complete_text", complete)
    if not available:
        with pytest.raises(ValueError, match="SHADOW_UNAVAILABLE"):
            await evaluation.run_partition_eval(
                None, settings, fixture=data, project_id=uuid4(), shadow_judge=True
            )
        assert not calls
        return
    report = await evaluation.run_partition_eval(
        None, settings, fixture=data, project_id=uuid4(), shadow_judge=True
    )
    assert len(calls) == 1
    assert calls[0].model_catalog_key == key
    assert report["requested_model"] == "shadow-model"
    assert report["judge_route"] == "configured_primary_shadow"
    assert report["status"] == ("evaluation_failed" if mismatch else "reference_match")
    assert report["production_ready"] is False
