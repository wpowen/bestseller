"""Native length-stop receipts abstain without invalidating a verified quorum."""

from contextlib import asynccontextmanager
from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from test_commercial_judge_recovery import BODY, _review_completion, report, sources

from bestseller.services import chapter_llm_quality_judge as judge
from bestseller.services import commercial_judge_recovery as recovery
from bestseller.services import llm
from bestseller.services.commercial_action_projection import project_commercial_actions
from bestseller.services.commercial_review_wire import parse_commercial_wire
from bestseller.services.write_gate import GateConfig
from bestseller.settings import load_settings

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]
SLOTS = [f"chapter-quality.independent-v2:{i}" for i in range(3)]
LENGTH_STOPS = ["length", "max_tokens", "max_output_tokens"]


@pytest.fixture
def native_cohort(monkeypatch):
    """Keep producer, wire parsing, aggregation and projection real; deny all IO."""
    from bestseller.infra.db import session as db

    async def forbidden(*args, **kwargs):
        raise AssertionError("No database or provider call is authorized")

    @asynccontextmanager
    async def no_pool():
        raise RuntimeError("fixture: use borrowed session without database IO")
        yield

    monkeypatch.setattr(db, "get_server_session", no_pool)
    monkeypatch.setattr(llm, "complete_text", forbidden)
    monkeypatch.setattr(llm, "_call_litellm", forbidden)
    monkeypatch.setattr(llm, "_call_litellm_with_retry", forbidden)
    monkeypatch.setattr(
        judge, "chapter_commercial_thresholds", lambda *a: (0.82, {"readability": 0.8})
    )
    settings = load_settings(env={})
    session = SimpleNamespace(scalar=forbidden, execute=forbidden, commit=forbidden)

    async def run(finishes=("stop", "stop", "length")):
        requests, completions = [], []

        async def complete(sess, cfg, request):
            index = int(request.sample_slot.rsplit(":", 1)[1])
            payload = report((0.88, 0.94, 0.02)[index])
            payload["pass"] = True
            payload["issues"] = []
            # Even a parseable low score must not vote after a length stop.
            raw = json.dumps(payload, ensure_ascii=False)
            completion = _review_completion(
                content=raw,
                provider="fixture",
                model_name="fixture",
                finish_reason=finishes[index],
                llm_run_id=uuid4(),
            )
            requests.append(request)
            completions.append(completion)
            return completion

        monkeypatch.setattr(judge, "complete_text", complete)
        result = await judge.judge_chapter_commercial_quality_stable(
            session,
            settings,
            chapter_number=2,
            content_md=BODY,
            samples=3,
        )
        assert [request.sample_slot for request in requests] == SLOTS
        return result, sources(), requests, completions

    return run


def project(result, src):
    return project_commercial_actions(
        result,
        sources=src,
        gate_config=GateConfig({}),
        chapter_number=2,
    )


@pytest.mark.parametrize("finish", LENGTH_STOPS)
async def test_native_truncation_keeps_two_votes_scores_and_all_raw_evidence(native_cohort, finish):
    result, src, requests, completions = await native_cohort(("stop", "stop", finish))
    before, source_before = deepcopy(result.model_dump(mode="json")), deepcopy(src)
    aggregate = json.loads(result.raw_excerpt)
    samples = aggregate["samples"]
    truncated = samples[2]["report"]
    envelope = json.loads(truncated["raw_excerpt"])
    adapter = envelope["adapter_receipt"]
    assert adapter["status"] == "not_run_output_truncated"
    assert adapter["source_binding"] == src.receipt
    _, normal_adapter, _ = parse_commercial_wire(envelope["raw_report"], src)
    assert adapter["source_binding"] == normal_adapter["source_binding"]
    assert envelope["raw_report"] == completions[2].content
    assert (
        envelope["raw_report_sha256"] == hashlib.sha256(completions[2].content.encode()).hexdigest()
    )
    assert envelope["validation_errors"] == ["output: truncated at structured-review reserve"]
    assert aggregate["valid_samples"] == aggregate["required_valid_samples"] == 2
    assert [sample["valid"] for sample in samples] == [True, True, False]
    assert truncated["overall_score"] == 0 and truncated["dimension_scores"] == {}
    assert truncated["blocking_issues"][0]["code"] == "CHAPTER_JUDGE_UNAVAILABLE"
    assert result.passed and not result.infrastructure_failure
    assert result.overall_score == pytest.approx(0.91)
    assert result.dimension_scores == {"readability": pytest.approx(0.91)}
    assert all(request.tool_choice == "required" for request in requests)

    view, receipt = project(result, src)
    assert view is not None and receipt["status"] == "projected"
    assert view["overall_score"] == result.overall_score
    assert view["dimension_scores"] == result.dimension_scores and view["pass"] is True
    assert [slot["status"] for slot in receipt["slots"]] == ["verified", "verified", "unknown"]
    assert receipt["unknown_sample_slots"] == [SLOTS[2]]
    assert view["blocking_issues"] == [] and receipt["issue_actions"] == []
    assert view["raw_excerpt"] == result.raw_excerpt
    assert not receipt["raw_cohort_modified"] and not receipt["grants_slots"]
    assert result.model_dump(mode="json") == before and src == source_before


@pytest.mark.parametrize("finish", LENGTH_STOPS)
async def test_one_native_vote_two_length_stops_remains_unknown(native_cohort, finish):
    result, src, _, _ = await native_cohort(("stop", finish, finish))
    before = result.model_dump(mode="json")
    aggregate = json.loads(result.raw_excerpt)
    assert result.infrastructure_failure and not result.passed
    assert aggregate["valid_samples"] == 1 and aggregate["required_valid_samples"] == 2
    assert aggregate["status"] == "insufficient_valid_samples"
    view, receipt = project(result, src)
    assert view is None and receipt["status"] == "unsupported"
    assert receipt["reason"] == "insufficient_verified_quorum"
    assert result.model_dump(mode="json") == before


@pytest.mark.parametrize(
    "fault,reason",
    [
        ("body", "source_binding_changed"),
        ("source", "adapter_source_changed"),
        ("audit", "historical_audit_changed"),
        ("raw", "raw_report_hash_changed"),
        ("late_adapter", "unsupported_adapter_receipt"),
        ("historical_missing_binding", "adapter_source_missing"),
    ],
)
async def test_native_quorum_still_rejects_tampering_and_old_unbound_receipts(
    native_cohort, fault, reason
):
    # Slot 1 abstains before slot 2 is checked: a later invalid adapter must fail.
    result, src, _, _ = await native_cohort(("stop", "length", "stop"))
    assert project(result, src)[1]["status"] == "projected"
    aggregate = json.loads(result.raw_excerpt)
    sample = aggregate["samples"][2 if fault == "late_adapter" else 1]["report"]
    envelope = json.loads(sample["raw_excerpt"])
    adapter = envelope["adapter_receipt"]
    if fault == "body":
        envelope["source_binding"]["body_sha256"] = "0" * 64
    elif fault == "source":
        adapter["source_binding"]["semantic_source_sha256"] = "0" * 64
    elif fault == "audit":
        adapter["source_binding"]["source_sha256"] = "0" * 64
    elif fault == "raw":
        envelope["raw_report"] += " "
    elif fault == "late_adapter":
        adapter["adapter_version"] = "invalid"
    else:
        del adapter["source_binding"]
    sample["raw_excerpt"] = json.dumps(envelope, ensure_ascii=False)
    historical = result.model_copy(
        update={"raw_excerpt": json.dumps(aggregate, ensure_ascii=False)}
    )
    before = historical.model_dump(mode="json")
    view, receipt = project(historical, src)
    assert view is None and receipt["reason"] == reason
    assert receipt["status"] == (
        "unsupported" if fault in {"late_adapter", "historical_missing_binding"} else "blocked"
    )
    assert historical.model_dump(mode="json") == before
    if fault == "historical_missing_binding":
        # No adapter retrofit or historical UNKNOWN promotion on read.
        retained = json.loads(historical.raw_excerpt)["samples"][1]["report"]
        assert "source_binding" not in json.loads(retained["raw_excerpt"])["adapter_receipt"]


@pytest.mark.parametrize("finish", LENGTH_STOPS)
async def test_native_truncated_adapter_uses_actual_frozen_slot_receipt(
    monkeypatch, native_cohort, finish
):
    _, src, requests, _ = await native_cohort()
    # A resumed slot can retain different harmless audit bytes under the same
    # immutable semantic binding. Exercise the real freeze function's replay.
    frozen_receipt = dict(src.receipt)
    frozen_receipt["source_sha256"] = "a" * 64
    frozen_receipt["assembled_source_sha256"] = "b" * 64
    entry = {
        "used_attempts": 1,
        "request": requests[2].model_dump(mode="json"),
        "source_receipt": deepcopy(frozen_receipt),
    }
    owner = SimpleNamespace(
        metadata_json={"commercial_component_cohorts": {"cohort": {"slots": {SLOTS[2]: entry}}}}
    )
    owner_before = deepcopy(owner.metadata_json)
    binding = {
        "owner_id": str(uuid4()),
        "key": "cohort",
        "slot": SLOTS[2],
        "attempt": 1,
        "namespace": "test:truncated-frozen-receipt",
    }
    saved = []

    async def scalar(statement):
        return owner

    async def commit():
        pass

    async def begin_nested():
        return SimpleNamespace(is_active=True, commit=commit, rollback=commit)

    async def prepare(*args, **kwargs):
        return None, binding

    async def effective(*args, **kwargs):
        return load_settings(env={}).llm.critic.model_dump(mode="json")

    async def save(session, call, result):
        saved.append(result)

    async def complete(session, settings, request):
        assert request.model_dump(mode="json") == entry["request"]
        return _review_completion(
            content=json.dumps(report()),
            provider="fixture",
            model_name="fixture",
            finish_reason=finish,
            llm_run_id=uuid4(),
        )

    monkeypatch.setattr(judge, "_commercial_effective_critic_configuration", effective)
    monkeypatch.setattr(recovery, "prepare_commercial_component_slot", prepare)
    monkeypatch.setattr(recovery, "save_commercial_component_slot", save)
    monkeypatch.setattr(judge, "complete_text", complete)
    result = await judge.judge_chapter_commercial_quality(
        SimpleNamespace(scalar=scalar, commit=commit, begin_nested=begin_nested),
        load_settings(env={}),
        chapter_number=2,
        content_md=BODY,
        sample_slot=SLOTS[2],
        project_id=uuid4(),
        chapter_id=uuid4(),
        draft_id=uuid4(),
    )
    assert result.infrastructure_failure and saved == [result]
    envelope = json.loads(result.raw_excerpt)
    assert envelope["adapter_receipt"]["source_binding"] == frozen_receipt
    assert envelope["adapter_receipt"]["source_binding"] != src.receipt
    assert envelope["exact_source_audit"]["source_sha256"] == frozen_receipt["source_sha256"]
    assert owner.metadata_json == owner_before
