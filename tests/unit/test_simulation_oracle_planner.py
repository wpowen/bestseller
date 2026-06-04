"""Tests for planner-side oracle integration (config-gated, safe-degrading)."""

from __future__ import annotations

import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from bestseller.services.simulation_oracle_planner import (
    _build_request,
    _characters_from_cast,
    augment_story_design_kernel_with_oracle,
    planner_oracle_enabled,
)
from bestseller.services.story_design_kernel import story_design_kernel_from_dict

_CAST = {
    "protagonist": {"name": "林烬", "role": "矿镇少年，打破修行垄断"},
    "antagonist": {"name": "宁玄策", "role": "九宗天骄，既得利益代表"},
    "supporting_cast": [
        {"name": "苏晚照", "role": "阵法师同伴"},
        {"name": "顾行舟", "role": "师尊，立场复杂"},
    ],
}

_GOOD_JSON = json.dumps(
    {
        "natural_direction": "赢之后的治理真空。",
        "beats": [
            {"chapter_range": "1-20", "duty": "宁玄策当场逼林烬证明否则灭口",
             "state_change": "林烬反击夺回主动", "payoff": "以小博大首胜",
             "hook_or_aftereffect": "限期威胁"},
            {"chapter_range": "21-40", "duty": "开放反噬，林烬付出代价",
             "state_change": "林烬舍弃挚友承受major loss", "payoff": "兑现反噬",
             "hook_or_aftereffect": "幕后者浮现"},
        ],
        "subplots": [
            {"key": "su", "line_type": "relationship", "label": "苏晚照暗线",
             "role": "同伴", "current_state": "未冲突", "target_state": "引爆",
             "dependency_on_mainline": "改变林烬处境", "failure_if_removed": "缺映照"}
        ],
        "motivation_flags": [
            {"character": "宁玄策", "issue": "stake薄", "suggested_fix": "补失去"}
        ],
    },
    ensure_ascii=False,
)


def _base_kernel() -> dict:
    return {
        "reader_promise": "每章有可见变化。",
        "premise_contract": {"unique_hook": "空脉重写规则。",
                             "core_question": "破垄断者会否成新垄断?",
                             "commercial_pull": "逆袭加反思。"},
        "character_conflict_contracts": [{
            "character_key": "林烬", "external_goal": "破垄断。", "internal_need": "证明配求道。",
            "pressure_source": "九宗围剿。", "choice_axis": "独善 还是 立规。",
            "change_vector": "挑战者转立规者。"}],
        "structure_strategy": {"macro_strategy": "逆袭加冲突。",
                               "chapter_engine": "每章一兑现一钩。",
                               "pacing_rule": "铺垫爆发交替。", "freshness_rule": "每卷新压强。"},
        "plot_tree": [{"key": "main", "line_type": "main", "label": "林烬重写规则", "role": "主线",
                       "current_state": "刚开缝。", "target_state": "新秩序站稳。",
                       "failure_if_removed": "无主线全崩。"}],
        "beat_schedule": [{"chapter_range": "1-3", "duty": "开局承诺。", "state_change": "出招。",
                           "payoff": "首爽点。", "hook_or_aftereffect": "旧案谜团。"}],
        "change_vectors": ["从弱到强"],
    }


def _project() -> SimpleNamespace:
    return SimpleNamespace(slug="jin-tian-wen-dao", target_chapters=40, id=uuid4())


class TestPureHelpers:
    def test_characters_from_cast_typing(self) -> None:
        seeds = _characters_from_cast(_CAST)
        by = {s.name: s.entity_type for s in seeds}
        assert by["林烬"] == "Protagonist"
        assert by["宁玄策"] == "Rival"
        assert by["苏晚照"] == "Ally"

    def test_build_request(self) -> None:
        req = _build_request(_project(), "矿镇少年逆袭。", _CAST)
        assert req.target_chapters == 40 and req.slug == "jin-tian-wen-dao"
        assert req.protagonist.name == "林烬"

    def test_disabled_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MIROFISH_ORACLE_PLANNER", raising=False)
        assert planner_oracle_enabled() is False


class TestAugmentGate:
    @pytest.mark.asyncio
    async def test_disabled_is_noop(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MIROFISH_ORACLE_PLANNER", raising=False)
        base = _base_kernel()
        out = await augment_story_design_kernel_with_oracle(
            None, None, payload=base, project=_project(), premise="p", cast_spec_payload=_CAST
        )
        assert out is base  # 关闭时原样返回,不改

    @pytest.mark.asyncio
    async def test_enabled_augments(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MIROFISH_ORACLE_PLANNER", "true")

        async def fake_complete(session, settings, request):
            return SimpleNamespace(content=_GOOD_JSON)

        import bestseller.services.llm as llm_mod

        monkeypatch.setattr(llm_mod, "complete_text", fake_complete)
        out = await augment_story_design_kernel_with_oracle(
            None, None, payload=_base_kernel(), project=_project(),
            premise="矿镇少年逆袭。", cast_spec_payload=_CAST,
        )
        # beat 覆盖被补全到 40、注入支线、整体仍是合法 kernel
        story_design_kernel_from_dict(out)
        assert out["oracle_meta"]["source"] == "llm"
        assert len(out["plot_tree"]) > 1

    @pytest.mark.asyncio
    async def test_llm_failure_degrades_to_original(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MIROFISH_ORACLE_PLANNER", "true")

        async def boom(session, settings, request):
            raise RuntimeError("model down")

        import bestseller.services.llm as llm_mod

        monkeypatch.setattr(llm_mod, "complete_text", boom)
        base = _base_kernel()
        out = await augment_story_design_kernel_with_oracle(
            None, None, payload=base, project=_project(), premise="p", cast_spec_payload=_CAST
        )
        assert out is base  # 失败不抛,原样返回(不阻断规划)
