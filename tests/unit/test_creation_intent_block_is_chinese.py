"""用户勾选进 prompt 时必须是中文，不能是英文机器键。

2026-08-23 真机（验证书 8）：用户勾了【轻松调性 + 喜剧引擎 + 爽点满足引擎】，
产出却是「市井悬疑 / 庙堂暗战」的沉重官场稿，模拟读者 0/3 会点、评语是
「一堆神神叨叨的名词，文绉绉像古代公文」。渲染 `_creation_intent_prompt_block`
看到实情——一份中文创作 prompt 里塞的是：

    {"tone": "light", "effect_skills": ["comedy_engine", "hype_satisfaction_engine"]}

英文 snake_case 机器键。而框架自己在 `story_effect_skills.py` 里早就建了
`comedy_engine → 喜剧` 的映射，注释写明「需要把用户勾了什么讲给模型时无从
下手」——这个块却直接 dump 原始 JSON，翻译表建了没人用。

同款教训已定案两次：「中文 prompt 嵌英文判据，判据本身会被绕过」
（2026-08-22 测试写中文「代价」而原文是英文 cost）。
"""

from __future__ import annotations

# ruff: noqa: RUF002 — 中文标点是刻意的。
from bestseller.services.conception import _creation_intent_prompt_block


def _ctx(**over: object) -> dict:
    enh = {
        "brainhole": False,
        "cost_style": "minimal",
        "concept_lab": False,
        "wild_concept": False,
        "effect_skills": ["comedy_engine", "hype_satisfaction_engine"],
        "creativity_direction": None,
    }
    enh.update(over.pop("enhancers", {}) or {})
    contract = {
        "channel_key": "male",
        "genre_label": "玄幻",
        "sub_genre_label": None,
        "tags": [],
        "default_tags": [],
        "user_tags": [],
        "audience_orientation": "male",
        "narrative_scale": None,
        "tone_preference": "light",
        "explicit_enhancers": enh,
    }
    contract.update(over)
    return {"genre_intent_contract": contract}


class TestPicksAreSpelledOutInChinese:
    def test_effect_skills_appear_as_chinese_labels(self) -> None:
        block = _creation_intent_prompt_block(_ctx())
        assert "喜剧" in block
        assert "爽点满足" in block

    def test_tone_appears_as_chinese(self) -> None:
        block = _creation_intent_prompt_block(_ctx())
        assert "轻松" in block

    def test_dark_tone_is_also_translated(self) -> None:
        block = _creation_intent_prompt_block(_ctx(tone_preference="dark"))
        assert "沉重" in block or "压抑" in block or "黑暗" in block

    def test_unknown_skill_key_degrades_to_the_raw_key(self) -> None:
        """未知键不能凭空消失——宁可露出机器键，也不能悄悄丢掉用户的勾选。"""

        block = _creation_intent_prompt_block(
            _ctx(enhancers={"effect_skills": ["comedy_engine", "made_up_engine"]})
        )
        assert "喜剧" in block
        assert "made_up_engine" in block

    def test_no_picks_still_renders_nothing(self) -> None:
        """无勾选契约仍须是空块（no-selection 契约不得被本次改动破坏）。

        ⚠️ 首版夹具留着 audience="male" 却声称「无勾选」——频道本身就是一个
        勾选，块当然要渲染。夹具自相矛盾，不是代码有问题。
        """

        block = _creation_intent_prompt_block(
            _ctx(
                tone_preference=None,
                audience_orientation=None,
                enhancers={"effect_skills": [], "cost_style": "standard"},
            )
        )
        assert block.strip() == ""
