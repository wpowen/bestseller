"""Tests for the T1 LLM oracle client (real-reasoning path, offline via injected completion)."""

from __future__ import annotations

import json

from bestseller.services.simulation_oracle import (
    CharacterSeed,
    LLMOracleClient,
    OracleConfig,
    OracleRequest,
    SimulationOracle,
    augment_kernel,
)
from bestseller.services.story_design_kernel import story_design_kernel_from_dict


def _request(target: int = 40) -> OracleRequest:
    return OracleRequest(
        slug="llm-demo",
        target_chapters=target,
        characters=(
            CharacterSeed("林烬", "主角，打破修行垄断。", "Protagonist", "化海稳定。"),
            CharacterSeed("宁玄策", "九宗天骄，既得利益代表。", "Rival"),
        ),
    )


_GOOD_JSON = json.dumps(
    {
        "natural_direction": "林烬赢得开局后，真正的压力来自治理真空与代价。",
        "beats": [
            {
                "chapter_range": "1-20",
                "duty": "宁玄策当场拦截林烬，逼其证明血脉否则灭口",
                "state_change": "林烬被迫反击并夺回主动",
                "payoff": "以小博大兑现首胜",
                "hook_or_aftereffect": "宁玄策撂下限期威胁",
            },
            {
                "chapter_range": "21-40",
                "duty": "开放反噬，林烬因自己推动的变革付出代价",
                "state_change": "林烬舍弃挚友，承受major loss",
                "payoff": "兑现赢之后反噬的反套路",
                "hook_or_aftereffect": "真正幕后者浮现",
            },
        ],
        "subplots": [
            {
                "key": "emergent_su",
                "line_type": "relationship",
                "label": "苏晚照的情报私利暗线",
                "role": "同伴",
                "current_state": "公私身份未冲突",
                "target_state": "公私冲突被引爆",
                "dependency_on_mainline": "她的隐瞒改变林烬的处境",
                "failure_if_removed": "主角胜利缺乏外部映照",
            }
        ],
        "motivation_flags": [
            {"character": "宁玄策", "issue": "stake 过薄", "suggested_fix": "补一条个人会失去什么"}
        ],
    },
    ensure_ascii=False,
)


class TestLLMOracleParsing:
    def test_valid_json_parsed(self) -> None:
        client = LLMOracleClient(complete=lambda s, u: _GOOD_JSON)
        result = client.deduce(_request())
        assert result.source == "llm"
        assert len(result.beats) == 2
        assert result.beats[0].chapter_range == "1-20"
        assert result.subplots and result.motivation_flags
        assert result.ranking_ready  # 具象+落地 → 过榜单自检

    def test_code_fenced_json_parsed(self) -> None:
        fenced = f"```json\n{_GOOD_JSON}\n```"
        client = LLMOracleClient(complete=lambda s, u: fenced)
        result = client.deduce(_request())
        assert result.source == "llm" and len(result.beats) == 2

    def test_malformed_json_falls_back(self) -> None:
        client = LLMOracleClient(complete=lambda s, u: "对不起我不会输出JSON")
        result = client.deduce(_request())
        assert result.source == "heuristic"  # 容错降级

    def test_empty_beats_falls_back(self) -> None:
        client = LLMOracleClient(complete=lambda s, u: '{"beats": []}')
        result = client.deduce(_request())
        assert result.source == "heuristic"

    def test_llm_output_augments_kernel(self) -> None:
        client = LLMOracleClient(complete=lambda s, u: _GOOD_JSON)
        result = client.deduce(_request())
        base = {
            "reader_promise": "每章有可见变化。",
            "premise_contract": {
                "unique_hook": "空脉重写规则。",
                "core_question": "破垄断者会否成新垄断?",
                "commercial_pull": "逆袭加制度反思。",
            },
            "character_conflict_contracts": [
                {
                    "character_key": "林烬",
                    "external_goal": "破垄断。",
                    "internal_need": "证明配求道。",
                    "pressure_source": "九宗围剿。",
                    "choice_axis": "独善 还是 立规。",
                    "change_vector": "挑战者转立规者。",
                }
            ],
            "structure_strategy": {
                "macro_strategy": "逆袭加制度冲突。",
                "chapter_engine": "每章一兑现加一钩。",
                "pacing_rule": "铺垫与爆发交替。",
                "freshness_rule": "每卷新压强。",
            },
            "plot_tree": [
                {
                    "key": "main",
                    "line_type": "main",
                    "label": "林烬重写规则",
                    "role": "主线",
                    "current_state": "规则刚开缝。",
                    "target_state": "新秩序站稳。",
                    "failure_if_removed": "无主线全崩。",
                }
            ],
            "beat_schedule": [
                {
                    "chapter_range": "1-3",
                    "duty": "开局承诺。",
                    "state_change": "主角出招。",
                    "payoff": "首爽点。",
                    "hook_or_aftereffect": "旧案谜团。",
                }
            ],
            "change_vectors": ["从弱到强"],
        }
        augmented = augment_kernel(base, result, target_chapters=40)
        story_design_kernel_from_dict(augmented)  # 校验通过
        assert augmented["oracle_meta"]["source"] == "llm"


class TestFacadeSelection:
    def test_llm_enabled_uses_llm(self) -> None:
        oracle = SimulationOracle(
            config=OracleConfig(llm_enabled=True),
            llm_complete=lambda s, u: _GOOD_JSON,
        )
        assert oracle.deduce(_request()).source == "llm"

    def test_llm_failure_falls_back_to_heuristic(self) -> None:
        def boom(_s: str, _u: str) -> str:
            raise RuntimeError("model down")

        oracle = SimulationOracle(config=OracleConfig(llm_enabled=True), llm_complete=boom)
        assert oracle.deduce(_request()).source == "heuristic"  # 不抛,降级

    def test_llm_disabled_uses_heuristic(self) -> None:
        oracle = SimulationOracle(
            config=OracleConfig(llm_enabled=False),
            llm_complete=lambda s, u: _GOOD_JSON,
        )
        assert oracle.deduce(_request()).source == "heuristic"
