from contextlib import asynccontextmanager
import json

import pytest

from bestseller.domain.llm_quality_judge import LLMQualityJudgeResult
from bestseller.infra.db import session as session_module
from bestseller.services import chapter_llm_quality_judge as judge
from bestseller.settings import load_settings


def verdict(slot, score, *, unavailable=False, critical=False):
    return LLMQualityJudgeResult.model_validate({
        "passed": not (unavailable or critical),
        "overall_score": score,
        "dimension_scores": {"readability": score},
        "blocking_issues": [{
            "code": "CHAPTER_JUDGE_UNAVAILABLE" if unavailable else "SOURCE_CONTRADICTION",
            "severity": "critical",
            "evidence": f"exact source citation for sample {slot}",
            "required_fix": "Rejudge only" if unavailable else "Preserve the adopted fact",
        }] if unavailable or critical else [],
        "rewrite_plan": {"scope": "judge" if unavailable else "chapter"},
        "raw_excerpt": f"original report {slot}",
        "llm_run_id": f"paid-sample-{slot}",
    })


async def aggregate(monkeypatch, reports):
    slots = []

    @asynccontextmanager
    async def session():
        yield object()

    async def sample(*args, **kwargs):
        slot = kwargs["sample_slot"]
        slots.append(slot)
        return reports[int(slot.rsplit(":", 1)[1])]

    monkeypatch.setattr(session_module, "get_server_session", session)
    monkeypatch.setattr(judge, "judge_chapter_commercial_quality", sample)
    monkeypatch.setattr(judge, "chapter_commercial_thresholds", lambda *a: (
        0.90, {"readability": 0.90},
    ))
    originals = [r.model_dump(mode="json") for r in reports]
    result = await judge.judge_chapter_commercial_quality_stable(
        object(), load_settings(env={}), chapter_number=2,
        content_md="Unchanged source-bound chapter.", samples=len(reports),
    )
    assert slots == [f"chapter-quality.independent-v2:{i}" for i in range(len(reports))]
    assert [r.model_dump(mode="json") for r in reports] == originals
    return result, originals


@pytest.mark.asyncio
async def test_one_valid_two_unavailable_is_judge_only_not_a_verdict(monkeypatch):
    result, originals = await aggregate(monkeypatch, [
        verdict(0, 0.99), verdict(1, 0.0, unavailable=True),
        verdict(2, 0.0, unavailable=True),
    ])
    assert result.infrastructure_failure
    assert not result.passed and not result.quality_rejected
    assert result.rewrite_plan.scope == "judge"
    assert not result.rewrite_plan.change
    receipt = json.loads(result.raw_excerpt)
    assert receipt["valid_samples"] == 1 and receipt["required_valid_samples"] == 2
    assert [s["report"] for s in receipt["samples"]] == originals


@pytest.mark.asyncio
@pytest.mark.parametrize("critical_slot", [0, 1, 2])
async def test_high_median_preserves_critical_from_any_valid_sample(monkeypatch, critical_slot):
    reports = [verdict(i, score, critical=i == critical_slot)
               for i, score in enumerate([0.91, 0.95, 0.99])]
    result, originals = await aggregate(monkeypatch, reports)
    assert not result.infrastructure_failure
    assert not result.passed and result.quality_rejected and result.has_critical
    assert result.overall_score == pytest.approx(0.95)
    assert reports[critical_slot].blocking_issues[0] in result.blocking_issues
    receipt = json.loads(result.raw_excerpt)
    assert [s["report"] for s in receipt["samples"]] == originals
    assert receipt["critical_sample_slots"] == [f"chapter-quality.independent-v2:{critical_slot}"]


@pytest.mark.asyncio
async def test_two_valid_one_unavailable_uses_valid_median_and_preserves_reports(monkeypatch):
    result, originals = await aggregate(monkeypatch, [
        verdict(0, 0.91), verdict(1, 0.0, unavailable=True), verdict(2, 0.95),
    ])
    assert result.passed and not result.infrastructure_failure
    assert result.overall_score == pytest.approx(0.93)
    assert result.dimension_scores == {"readability": pytest.approx(0.93)}
    receipt = json.loads(result.raw_excerpt)
    assert receipt["valid_samples"] == 2 and receipt["required_valid_samples"] == 2
    assert [s["report"] for s in receipt["samples"]] == originals
    assert receipt["representative_sample_slot"] in {
        "chapter-quality.independent-v2:0", "chapter-quality.independent-v2:2",
    }
