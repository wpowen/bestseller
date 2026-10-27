from __future__ import annotations

import json
from pathlib import Path

import pytest

from bestseller.services import conception

pytestmark = pytest.mark.unit

FIXTURE = Path(__file__).parents[1] / "fixtures" / "sixth-packaging-loop.json"


def test_real_237_char_fixture_identifies_four_unchanged_repairs():
    data = json.loads(FIXTURE.read_text())
    records = data["packaging"]
    synopses = [record["candidate_synopsis"] for record in records]
    assert len({conception._packaging_text_identity(value)["sha256"] for value in synopses}) == 1
    assert all(len(value) == 237 for value in synopses)
    assert all(
        record["quality"]["blurb_verdict"]["dimensions"][1]["score"] == 3.0 for record in records
    )
    # Historical evidence captures the old bug: the same text was still marked accepted.
    assert all(record["candidate_accepted"] is True for record in records)
    assert conception._packaging_text_changed(synopses[0], synopses[1]) is False


def test_packaging_identity_reports_unicode_char_count_and_utf8_bytes_separately():
    identity = conception._packaging_text_identity("甲乙\n")
    assert identity["char_count"] == 2
    assert identity["utf8_byte_count"] == 6


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "effect_skills, forbidden, required",
    [
        ((), "喜剧四拍", "每个简介候选必须恰好用三步"),
        (("comedy_engine",), "废城困境", "喜剧四拍"),
    ],
)
async def test_final_repair_prompt_only_adds_comedy_contract_when_explicit(
    monkeypatch, effect_skills, forbidden, required
):
    captured = {}

    async def fake_call(*_args, **kwargs):
        captured["prompt"] = kwargs["user_prompt"]
        return {
            "title_candidates": ["新标题"],
            "synopsis_candidates": ["新简介。"],
            "synopsis": "新简介。",
        }, []

    monkeypatch.setattr(conception, "_llm_call_json", fake_call)
    await conception._repair_final_packaging_to_explicit_seed(
        object(),
        object(),
        concept_seed="主角在山城守住一条路。",
        title="旧标题",
        premise="主角守住山城并继续扩张。",
        synopsis="旧简介。",
        report={"hard_conflicts": []},
        ctx={
            "language": "zh-CN",
            "genre_intent_contract": {"explicit_enhancers": {"effect_skills": list(effect_skills)}},
        },
        attempt=4,
        max_attempts=4,
        target_platform="fanqie",
        tags=("玄幻",),
    )
    prompt = captured["prompt"]
    assert required in prompt
    assert forbidden not in prompt
    if effect_skills:
        assert "喜剧四拍" in prompt


def test_candidate_decision_requires_text_change_but_does_not_require_hard_gate_pass():
    same = conception._packaging_candidate_decision(
        incumbent_title="旧",
        candidate_title="旧",
        incumbent_synopsis="同一简介",
        candidate_synopsis="同一简介",
        candidate_rank=(0.0,),
        incumbent_rank=(0.0,),
    )
    assert same == {
        "text_changed": False,
        "accepted": False,
        "candidate_explored": False,
        "repair_no_text_change": True,
    }
    changed = conception._packaging_candidate_decision(
        incumbent_title="旧",
        candidate_title="新",
        incumbent_synopsis="同一简介",
        candidate_synopsis="新简介",
        candidate_rank=(0.0,),
        incumbent_rank=(0.0,),
    )
    assert changed["text_changed"] is True
    assert changed["accepted"] is False
    assert changed["candidate_explored"] is True
    assert changed["repair_no_text_change"] is False
    improved = conception._packaging_candidate_decision(
        incumbent_title="旧",
        candidate_title="新",
        incumbent_synopsis="同一简介",
        candidate_synopsis="更强简介",
        candidate_rank=(1.0,),
        incumbent_rank=(0.0,),
    )
    assert improved["accepted"] is True
    passed = conception._packaging_candidate_decision(
        incumbent_title="旧",
        candidate_title="新2",
        incumbent_synopsis="同一简介",
        candidate_synopsis="过门简介",
        candidate_rank=(0.0,),
        incumbent_rank=(0.0,),
        candidate_passed=True,
    )
    assert passed["accepted"] is True


def test_comedy_flag_reads_real_creation_contract_location():
    real_sixth_ctx = {
        "genre_intent_contract": {
            "schema_version": "genre-intent.v1",
            "explicit_enhancers": {"effect_skills": [], "cost_style": "standard"},
        }
    }
    assert conception._packaging_comedy_enabled(real_sixth_ctx) is False
    real_sixth_ctx["genre_intent_contract"]["explicit_enhancers"]["effect_skills"] = [
        "comedy_engine"
    ]
    assert conception._packaging_comedy_enabled(real_sixth_ctx) is True
