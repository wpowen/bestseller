import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from bestseller.services import world_partition_staged_eval as staged
from bestseller.services.llm import LLMCompletionResult
from bestseller.services.world_partition_eval import prepare_eval
from bestseller.settings import load_settings

pytestmark = pytest.mark.unit


def fixture():
    return json.loads(Path("config/evaluation/world_partition_semantics.json").read_text())


def stage_a(prepared):
    def pieces(row):
        result = [s["quote"] for s in row["segments"]]
        if len(result[-1]) > 1 and result[-1][-1] in "。；;.!?！？":  # noqa: RUF001
            ending = result[-1][-1]
            result[-1] = result[-1][:-1]
            result.append(ending)
        return result

    return {
        "complete": True,
        "source_hash": prepared["source_hash"],
        "clauses": [
            {"clause_id": row["clause_id"], "segments": pieces(row)}
            for row in prepared["reference_clauses"]
        ],
    }


def stage_b(prepared, segmentation):
    expected = prepared["expected"]
    labels = []
    for clause in segmentation["clauses"]:
        chars = expected[clause["clause_id"]]
        for segment in clause["segments"]:
            labels.append({"segment_id": segment["segment_id"], "kind": chars[segment["start"]]})
    return {
        "complete": True,
        "source_hash": prepared["source_hash"],
        "segmentation_hash": segmentation["segmentation_hash"],
        "labels": labels,
    }


def test_build_segmentation_recomputes_ids_offsets_and_hash():
    prepared = prepare_eval(fixture())
    result = staged.build_segmentation(stage_a(prepared), prepared=prepared)
    assert result["segmentation_hash"]
    assert result["clauses"][0]["segments"][0]["segment_id"] == "C001-S001"
    assert result["clauses"][0]["segments"][0]["start"] == 0
    assert result["clauses"][0]["segments"][0]["end"] > 0


def test_build_segmentation_rejects_model_offsets_ids_and_incomplete_concat():
    prepared = prepare_eval(fixture())
    payload = stage_a(prepared)
    payload["clauses"][0]["segments"][0] = prepared["targets"]["C001"]["quote"][:-1]
    with pytest.raises(ValueError, match=r"(INCOMPLETE|LENGTH)"):
        staged.build_segmentation(payload, prepared=prepared)


def test_labels_to_partition_rejects_missing_or_forged_segment_ids():
    prepared = prepare_eval(fixture())
    segmentation = staged.build_segmentation(stage_a(prepared), prepared=prepared)
    payload = stage_b(prepared, segmentation)
    payload["labels"][0]["segment_id"] = "forged"
    with pytest.raises(ValueError, match="COVERAGE"):
        staged.labels_to_partition(payload, segmentation=segmentation, prepared=prepared)


@pytest.mark.parametrize(
    "mutation, error",
    [
        ("duplicate_clause", r"(ID_COVERAGE|SEGMENT_COVERAGE)"),
        ("stale_hash", "STALE_SEGMENT_SOURCE"),
        ("extra_field", "INVALID_SEGMENT_CLAUSE"),
        ("too_many", "SEGMENT_COUNT"),
        ("whitespace_loss", r"(LENGTH|NOT_CONTIGUOUS)"),
    ],
)
def test_stage_a_partition_contract_rejects_structural_mutations(mutation, error):
    prepared = prepare_eval(fixture())
    payload = stage_a(prepared)
    if mutation == "duplicate_clause":
        payload["clauses"].append(payload["clauses"][0])
    elif mutation == "stale_hash":
        payload["source_hash"] = "stale"
    elif mutation == "extra_field":
        payload["clauses"][0]["offset"] = 0
    elif mutation == "too_many":
        payload["clauses"][0]["segments"] = ["x"] * 65
    else:
        quote = payload["clauses"][0]["segments"][0]
        payload["clauses"][0]["segments"][0] = quote + " "
    with pytest.raises(ValueError, match=error):
        staged.build_segmentation(payload, prepared=prepared)


@pytest.mark.parametrize("mutation", ["stale_hash", "stale_segmentation", "duplicate", "extra"])
def test_stage_b_label_contract_rejects_structural_mutations(mutation):
    prepared = prepare_eval(fixture())
    segmentation = staged.build_segmentation(stage_a(prepared), prepared=prepared)
    payload = stage_b(prepared, segmentation)
    if mutation == "stale_hash":
        payload["source_hash"] = "stale"
    elif mutation == "stale_segmentation":
        payload["segmentation_hash"] = "stale"
    elif mutation == "duplicate":
        payload["labels"].append(payload["labels"][0])
    else:
        payload["labels"][0]["text"] = "forged"
    with pytest.raises(ValueError):
        staged.labels_to_partition(payload, segmentation=segmentation, prepared=prepared)


@pytest.mark.asyncio
async def test_staged_eval_makes_exactly_two_calls_and_grades_without_reference_prompt(monkeypatch):
    prepared = prepare_eval(fixture())
    segmentation = staged.build_segmentation(stage_a(prepared), prepared=prepared)
    responses = [stage_a(prepared), stage_b(prepared, segmentation)]
    calls = []

    async def complete(session, settings, request):
        calls.append(request)
        return LLMCompletionResult(
            content=json.dumps(responses[len(calls) - 1], ensure_ascii=False),
            provider="fixture",
            model_name="fixture",
            llm_run_id=uuid4(),
            latency_ms=12,
        )

    monkeypatch.setattr(staged, "complete_text", complete)
    report = await staged.run_staged_partition_eval(
        None, load_settings(env={}), fixture=fixture(), project_id=uuid4()
    )
    assert len(calls) == 2
    assert all(request.max_tokens_override == 3000 for request in calls)
    assert all(request.temperature_override == 0.1 for request in calls)
    assert calls[0].user_prompt.startswith(f"source_hash={prepared['source_hash']}")
    assert all("rationale" not in request.user_prompt for request in calls)
    assert all("axis" not in request.user_prompt for request in calls)
    assert all("reference_status" not in request.user_prompt for request in calls)
    assert report["version"] == staged.STAGED_VERSION
    assert report["segmentation_hash"]
    assert len(report["llm_run_ids"]) == 2
    assert report["promotion_allowed"] is False
    assert report["production_ready"] is False
    assert report["stages"]["a"]["parsed_response"]
    assert report["stages"]["b"]["parsed_response"]
    assert report["reference_status"] == "agent_authored_requires_human_validation"
    assert report["grader_version"]
    assert report["stages"]["a"]["llm_run_id"]
    assert report["stages"]["b"]["llm_run_id"]


@pytest.mark.asyncio
async def test_stage_a_failure_does_not_call_stage_b_or_drop_raw(monkeypatch):
    calls = []

    async def complete(session, settings, request):
        calls.append(request)
        return LLMCompletionResult(
            content="bad",
            provider="fixture",
            model_name="fixture",
            llm_run_id=uuid4(),
        )

    monkeypatch.setattr(staged, "complete_text", complete)
    report = await staged.run_staged_partition_eval(
        None, load_settings(env={}), fixture=fixture(), project_id=uuid4()
    )
    assert len(calls) == 1
    assert report["failed_stage"] == "a"
    assert report["stages"]["a"]["raw_response"] == "bad"
    assert "b" not in report["stages"]
    assert report["total_cases"] == 6
    assert report["passed_cases"] is None
    assert len(report["cases"]) == 6
    assert all(row["status"] == "not_scored" and row["passed"] is None for row in report["cases"])


@pytest.mark.asyncio
async def test_stage_b_provider_error_preserves_stage_a_evidence(monkeypatch):
    prepared = prepare_eval(fixture())
    calls = []

    async def complete(session, settings, request):
        calls.append(request)
        if len(calls) == 1:
            return LLMCompletionResult(
                content=json.dumps(stage_a(prepared), ensure_ascii=False),
                provider="fixture",
                model_name="fixture",
                llm_run_id=uuid4(),
            )
        raise RuntimeError("secret provider detail")

    monkeypatch.setattr(staged, "complete_text", complete)
    report = await staged.run_staged_partition_eval(
        None, load_settings(env={}), fixture=fixture(), project_id=uuid4()
    )
    assert len(calls) == 2
    assert report["failed_stage"] == "b"
    assert report["stages"]["a"]["parsed_response"]
    assert report["stages"]["b"]["error_type"] == "RuntimeError"
    assert "secret provider detail" not in str(report)
    assert len(report["cases"]) == 6
    assert all(row["status"] == "not_scored" and row["passed"] is None for row in report["cases"])


@pytest.mark.asyncio
async def test_all_keep_labels_are_scored_and_fail_reference_gate(monkeypatch):
    prepared = prepare_eval(fixture())
    segmentation = staged.build_segmentation(stage_a(prepared), prepared=prepared)
    payload = stage_b(prepared, segmentation)
    for label in payload["labels"]:
        label["kind"] = "keep"
    responses = [stage_a(prepared), payload]

    async def complete(session, settings, request):
        return LLMCompletionResult(
            content=json.dumps(responses.pop(0), ensure_ascii=False),
            provider="fixture",
            model_name="fixture",
            llm_run_id=uuid4(),
        )

    monkeypatch.setattr(staged, "complete_text", complete)
    report = await staged.run_staged_partition_eval(
        None, load_settings(env={}), fixture=fixture(), project_id=uuid4()
    )
    assert report["status"] == "reference_mismatch"
    assert report["passed_cases"] < report["total_cases"]
    assert report["missed_removal_chars"] > 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field, value",
    [
        ("fallback_used", True),
        ("finish_reason", "length"),
        ("provider", "mock"),
    ],
)
async def test_unusable_stage_a_provider_stops_before_stage_b(monkeypatch, field, value):
    calls = []

    async def complete(session, settings, request):
        calls.append(request)
        kwargs = {
            "content": "{}",
            "provider": "fixture",
            "model_name": "fixture",
            "llm_run_id": uuid4(),
        }
        kwargs[field] = value
        return LLMCompletionResult(**kwargs)

    monkeypatch.setattr(staged, "complete_text", complete)
    report = await staged.run_staged_partition_eval(
        None, load_settings(env={}), fixture=fixture(), project_id=uuid4()
    )
    assert len(calls) == 1
    assert report["error"] == "STAGED_EVAL_PROVIDER_UNAVAILABLE"


@pytest.mark.asyncio
async def test_duplicate_or_missing_stage_b_receipt_stops_with_stage_a_evidence(monkeypatch):
    prepared = prepare_eval(fixture())
    calls = []
    receipt = uuid4()

    async def complete(session, settings, request):
        calls.append(request)
        if len(calls) == 1:
            return LLMCompletionResult(
                content=json.dumps(stage_a(prepared), ensure_ascii=False),
                provider="fixture",
                model_name="fixture",
                llm_run_id=receipt,
            )
        return SimpleNamespace(
            content="{}",
            provider="fixture",
            model_name="fixture",
            llm_run_id=receipt,
            latency_ms=None,
            finish_reason="stop",
            fallback_used=False,
            fallback_source=None,
        )

    monkeypatch.setattr(staged, "complete_text", complete)
    report = await staged.run_staged_partition_eval(
        None, load_settings(env={}), fixture=fixture(), project_id=uuid4()
    )
    assert len(calls) == 2
    assert report["error"] == "STAGED_EVAL_DUPLICATE_RECEIPT"
    assert report["stages"]["a"]["parsed_response"]


@pytest.mark.asyncio
async def test_missing_stage_b_receipt_stops_with_stage_a_evidence(monkeypatch):
    prepared = prepare_eval(fixture())
    calls = []

    async def complete(session, settings, request):
        calls.append(request)
        if len(calls) == 1:
            return LLMCompletionResult(
                content=json.dumps(stage_a(prepared), ensure_ascii=False),
                provider="fixture",
                model_name="fixture",
                llm_run_id=uuid4(),
            )
        return LLMCompletionResult(
            content="{}", provider="fixture", model_name="fixture", llm_run_id=None
        )

    monkeypatch.setattr(staged, "complete_text", complete)
    report = await staged.run_staged_partition_eval(
        None, load_settings(env={}), fixture=fixture(), project_id=uuid4()
    )
    assert len(calls) == 2
    assert report["error"] == "STAGED_EVAL_MISSING_RECEIPT"
    assert report["stages"]["a"]["parsed_response"]


@pytest.mark.asyncio
async def test_budget_failure_and_cancellation_are_distinct(monkeypatch):
    monkeypatch.setattr(staged, "MAX_SOURCE_CHARS", 1)
    report = await staged.run_staged_partition_eval(
        None, load_settings(env={}), fixture=fixture(), project_id=uuid4()
    )
    assert report["error"] == "STAGED_EVAL_STAGE_A_INPUT_BUDGET"

    async def cancelled(*args):
        raise asyncio.CancelledError

    monkeypatch.setattr(staged, "MAX_SOURCE_CHARS", 20000)
    monkeypatch.setattr(staged, "complete_text", cancelled)
    with pytest.raises(asyncio.CancelledError):
        await staged.run_staged_partition_eval(
            None, load_settings(env={}), fixture=fixture(), project_id=uuid4()
        )


@pytest.mark.asyncio
async def test_stage_a_budget_includes_source_hash_prefix(monkeypatch):
    prepared = prepare_eval(fixture())
    monkeypatch.setattr(staged, "MAX_SOURCE_CHARS", len(prepared["context"]))

    async def forbidden(*args):
        raise AssertionError("over-budget prompts must not invoke a model")

    monkeypatch.setattr(staged, "complete_text", forbidden)
    result = await staged.run_staged_partition_eval(
        None, load_settings(env={}), fixture=fixture(), project_id=uuid4()
    )
    assert result["error"] == "STAGED_EVAL_STAGE_A_INPUT_BUDGET"
    assert result["failed_stage"] == "a"
    assert result["llm_run_ids"] == []


@pytest.mark.asyncio
async def test_merged_terminal_stops_before_paid_classification(monkeypatch):
    prepared = prepare_eval(fixture())
    payload = stage_a(prepared)
    pieces = payload["clauses"][1]["segments"]
    pieces[-2:] = ["".join(pieces[-2:])]
    calls = []

    async def complete(session, settings, request):
        calls.append(request)
        assert len(calls) == 1, "invalid segmentation must not trigger classification"
        return LLMCompletionResult(
            content=json.dumps(payload, ensure_ascii=False),
            provider="fixture",
            model_name="fixture",
            llm_run_id=uuid4(),
        )

    monkeypatch.setattr(staged, "complete_text", complete)
    result = await staged.run_staged_partition_eval(
        None, load_settings(env={}), fixture=fixture(), project_id=uuid4()
    )
    assert len(calls) == 1
    assert result["failed_stage"] == "a"
    assert result["error"] == "STAGED_EVAL_TERMINATOR_NOT_SEPARATED"
    assert "b" not in result["stages"]
