"""故事脊柱框架层测试(2026-07-08 用户终审"不知道在讲啥/没故事性"根治)。"""

from __future__ import annotations

import pytest

from bestseller.services.story_spine import (
    SPINE_FIELDS,
    render_story_spine_block,
    validate_story_spine,
)

pytestmark = pytest.mark.unit

_GOOD = {
    "who": "被裁员的急诊科主治医师纪蘅",
    "wants": "在第七天禁言期满前找出让母亲昏迷的下咒人",
    "why_now": "母亲今晚突然昏迷,而他刚被规则夺走说话能力",
    "against": "藏在医院里、能改写规则的同行闻琢",
    "stakes": "母亲死,他自己也会被下一条规则吃掉",
    "question": "一个不能说话的医生,能不能在七天内揪出下咒人？",
}


def test_good_spine_passes_and_renders() -> None:
    assert validate_story_spine(_GOOD) == []
    block = render_story_spine_block(_GOOD)
    assert "故事脊柱" in block
    assert "想要" in block and "挡着" in block
    assert _GOOD["question"] in block
    assert "更近一步或更远一步" in block


def test_missing_and_empty_fields_flagged() -> None:
    assert validate_story_spine(None)
    assert validate_story_spine({})
    bad = dict(_GOOD); bad["stakes"] = ""
    v = validate_story_spine(bad)
    assert any("stakes" in x for x in v)


def test_vague_wants_rejected() -> None:
    bad = dict(_GOOD); bad["wants"] = "活下去"
    v = validate_story_spine(bad)
    assert any("太模糊" in x for x in v)


def test_question_must_be_interrogative() -> None:
    bad = dict(_GOOD); bad["question"] = "他会找出真凶。"
    v = validate_story_spine(bad)
    assert any("疑问句" in x for x in v)


def test_invalid_spine_renders_empty() -> None:
    assert render_story_spine_block({}) == ""
    assert render_story_spine_block(None) == ""


def test_fields_constant_complete() -> None:
    assert set(SPINE_FIELDS) == set(_GOOD)


def test_layered_spine_uses_units_accumulation_and_phase_change() -> None:
    layered = {
        **_GOOD,
        "schema_version": "story-spine.v2",
        "core_reader_promise": "看一个不能说话的医生用诊断能力反制改写规则的人",
        "long_term_desire": "夺回人对自身病历和命运的解释权",
        "terminal_question": "规则究竟应该由谁解释？",
        "unit_engine_ref": "每个医疗事件暴露一条被篡改的规则",
        "phase_desire_ladder": ["救母", "清理医院", "追查规则制定者"],
    }

    block = render_story_spine_block(layered)

    assert "每个故事单元" in block
    assert "永久变化" in block
    assert "阶段质变" in block
    assert "全书唯一主线" not in block


def test_finalize_prompt_requires_spine() -> None:
    from bestseller.services import conception

    # zh finalize 模板必须带 story_spine schema + 硬测试
    prompt = conception._finalize_user_prompt(
        {"genre": "都市", "sub_genre": "规则怪谈", "chapter_count": 10, "language": "zh-CN"},
        {}, {}, {}, {},
    )
    assert '"story_spine"' in prompt
    assert "故事脊柱硬测试" in prompt
    assert "讲给朋友听" in prompt
