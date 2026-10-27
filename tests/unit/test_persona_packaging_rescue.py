from __future__ import annotations

import inspect

import pytest

from bestseller.services import conception

pytestmark = pytest.mark.unit


def _report(*, blurb: float = 82.2, title: float = 94.8) -> dict[str, object]:
    return {
        "blurb": {"total": blurb},
        "title": {"total": title},
        "persona_judge": {"advisory_pass": False},
    }


def _config(*, blurb_min: float = 68.0, title_min: float = 80.0) -> dict[str, object]:
    return {
        "meets_bar": {"blurb_min": blurb_min, "title_min": title_min},
        "persona_judge": {"block_below": True, "click_rate_min": 0.34},
    }


def test_persona_packaging_rescue_is_only_for_persona_only_numeric_pass() -> None:
    assert conception._persona_packaging_rescue_is_eligible(
        blocked_by=["persona_judge"],
        story_appeal_report=_report(),
        config=_config(),
    )

    assert not conception._persona_packaging_rescue_is_eligible(
        blocked_by=["persona_judge", "logline_gate"],
        story_appeal_report=_report(),
        config=_config(),
    )
    assert not conception._persona_packaging_rescue_is_eligible(
        blocked_by=["persona_judge"],
        story_appeal_report=_report(blurb=67.9),
        config=_config(),
    )
    assert not conception._persona_packaging_rescue_is_eligible(
        blocked_by=["meets_bar"],
        story_appeal_report=_report(),
        config=_config(),
    )


@pytest.mark.asyncio
async def test_persona_packaging_repair_is_listing_only_seed_bound_and_reason_driven(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, str] = {}

    async def fake_repair(*_args, **kwargs):
        captured["system"] = kwargs["system_prompt"]
        captured["prompt"] = kwargs["user_prompt"]
        return (
            {
                "title_candidates": ["我让废城重回大陆商路", "废城经营，从重开商路开始"],
                "synopsis": "三条商路都绕着这座废城走。商洛要让它重新活过来。",
            },
            [],
        )

    monkeypatch.setattr(conception, "_llm_call_json", fake_repair)

    package, _ = await conception._repair_persona_rejected_packaging(
        object(),
        object(),
        concept_seed="主角重组流通规则；禁止沿用收费站城主和纯收过路费。",
        title="路契之城",
        premise="主角修复废城生存循环并争夺大陆交通主权。",
        synopsis="他以路契重构驼夫与行会的通行权。",
        persona_feedback="书名看不懂；路契、驼夫、行会像啃字典；爽点不直给。",
        ctx={
            "language": "zh-CN",
            "genre": "异世大陆",
            "sub_genre": "经营升级",
            "genre_intent_contract": {
                "user_tags": ["商路博弈"],
                "explicit_enhancers": {"effect_skills": ["brainhole"]},
            },
            "commercial_brief": {"pollution_sentinel": "旧商业提案不应进入救援 prompt"},
            "story_facets": {"setting": "旧城档案馆污染 sentinel"},
            "high_concept": {
                "concept": "废城重新接入大陆商路",
                "core_abnormality": "路契能显示断裂的通行权",
            },
        },
        attempt=1,
    )

    assert package["title_candidates"][0] == "我让废城重回大陆商路"
    assert package["synopsis"].startswith("三条商路")
    assert "只改书名与简介" in captured["system"]
    assert "不得改动故事" in captured["system"]
    assert "零生造黑话" in captured["system"]
    assert "唯一上位事实源" in captured["prompt"]
    assert "禁止沿用收费站城主" in captured["prompt"]
    assert "书名看不懂" in captured["prompt"]
    assert "旧商业提案不应进入救援 prompt" not in captured["prompt"]
    assert "旧城档案馆污染 sentinel" not in captured["prompt"]
    assert "废城重新接入大陆商路" in captured["prompt"]
    assert "路契能显示断裂的通行权" in captured["prompt"]


def test_persona_packaging_rescue_runs_before_shared_appeal_exception() -> None:
    source = inspect.getsource(conception.run_conception_pipeline)
    rescue = source.index("_repair_persona_rejected_packaging(")
    shared_block = source.index("if _appeal_block_below:", rescue)
    final_seed_gate = source.rindex("_audit_final_packaging_candidate(")

    assert rescue < shared_block < final_seed_gate
    rescue_region = source[rescue:shared_block]
    assert "_persona_click_advisory(" in rescue_region
    assert "_appeal_blocked_by = []" in rescue_region
    assert "persona_hard_veto(" in rescue_region
    assert 'story_appeal_report["persona_packaging_rescue"]' in rescue_region
