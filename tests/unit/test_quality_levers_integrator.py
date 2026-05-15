# ruff: noqa: RUF001
"""Unit tests for the writer / critic prompt integrators + multi-persona executor."""

from __future__ import annotations

import pytest

from bestseller.services.quality_levers.integrator import (
    CriticLeverContext,
    WriterLeverContext,
    build_critic_quality_levers_block,
    build_writer_quality_levers_block,
)
from bestseller.services.quality_levers.multi_persona_executor import (
    MultiPersonaExecution,
    decode_runner_result,
    run_multi_persona_critique,
)

pytestmark = pytest.mark.unit


def _strategy_card() -> dict[str, object]:
    return {
        "aggregate_key": "otherworld-cross-system",
        "maturity_score": 0.62,
        "maturity_status": "review",
        "source_count": 2,
        "selected_mechanisms": [
            {
                "mechanism_id": "cross-system-rule-arbitrage",
                "source_confidence": 0.86,
                "design_role": "series_engine",
                "adaptation_instruction": "转化为本项目因果链。",
                "required_project_specific_binding": "绑定到失效航图。",
                "failure_mode": "未绑定项目元素。",
            }
        ],
        "required_state_variables": ["cross_system_understanding"],
        "required_change_vectors": ["exploit_rule_gap"],
        "reader_reward_mix": ["knowledge_arbitrage"],
        "craft_controls": ["changed-state endings"],
        "anti_copy_boundaries": ["exact-opening-chain"],
    }


def _emotion_kernel() -> dict[str, object]:
    return {
        "reader_emotion_promise": "让读者先替主角着急，再等待听证会爆炸。",
        "primary_reader_waiting": ["听证会证物袋打开"],
        "empathy_contracts": [
            {
                "contract_id": "empathy-1",
                "character_key": "protagonist",
                "chapter_range": "1-3",
                "situation": "主角被迫回到旧案现场。",
                "current_desire": "找到第一份能翻案的证据。",
                "fear_or_loss": "妹妹会被当成同谋带走。",
                "sensory_entry": "潮湿档案纸的霉味。",
                "judgment_logic": "他会先怀疑帮助自己的人。",
                "reasonable_action": "假装配合问询，实际调换封条。",
                "consequence": "拿到线索，也暴露自己仍在追查。",
            }
        ],
        "bomb_contracts": [
            {
                "bomb_id": "bomb-hearing",
                "bomb_type": "danger",
                "chapter_range": "1-5",
                "reader_knows": "证物袋已经被调包。",
                "character_blindspot": "主角信任旧同僚。",
                "danger": "听证会上会反向坐实伪证。",
                "trigger_condition": "证物袋当众打开。",
                "countdown": "三章后。",
                "consequence": "妹妹被追加拘押。",
                "payoff_window": "第4-5章。",
                "rational_ignorance": "封缄来自旧同僚。",
            }
        ],
        "ending_texture_contract": {
            "ending_type": "HE",
            "core_wish_fulfilled": "旧案翻盘。",
            "relationship_settlement": "兄妹重建归处。",
            "irreversible_cost_retained": "错过的十年无法补回。",
            "theme_answer": "真相不能复原过去，但能阻止伤害继续。",
            "future_open": "主角成立调查所。",
        },
    }


# ---------------------------------------------------------------------------
# integrator — writer side
# ---------------------------------------------------------------------------


def test_build_writer_quality_levers_block_minimal_returns_phase4_blocks() -> None:
    block = build_writer_quality_levers_block(
        WriterLeverContext(chapter_number=5)
    )
    # Phase 4 blocks always render (chapter_signature / rhythm / emotion /
    # information_choreography) even without platform / anchors / etc.
    assert "chapter_signature_audit" in block
    assert "rhythm_engineering" in block
    assert "emotion_choreography" in block
    assert "information_choreography" in block


def test_build_writer_quality_levers_block_full_context() -> None:
    block = build_writer_quality_levers_block(
        WriterLeverContext(
            chapter_number=1,
            language="zh-CN",
            platform="qimao",
            style_anchors=("lu_xun_cold", "yan_leisheng"),
            chapter_positions=("first_chapter",),
            chapter_role="hook_chapter",
            scene_type="investigation_scene",
            scene_stimulus="confrontation_with_villain",
            participating_character_ids=("shen_qingya", "zhou_shensuan"),
            rejection_cause_ids=("ordinary_entry", "weak_attraction"),
        )
    )
    # Platform-aware block
    assert "七猫" in block or "qimao" in block.lower()
    # Position-aware block (first_chapter window banned patterns)
    assert "first_chapter" in block
    # Style anchors injected
    assert "lu_xun_cold" in block or "鲁迅" in block
    # Character profile injected
    assert "shen_qingya" in block
    # Sensory requirement injected
    assert "investigation_scene" in block
    # Rejection repair playbook injected
    assert "ordinary_entry" in block


def test_build_writer_quality_levers_block_includes_project_character_profiles() -> None:
    block = build_writer_quality_levers_block(
        WriterLeverContext(
            chapter_number=3,
            scene_stimulus="moral_dilemma",
            participating_character_profiles=(
                {
                    "character_id": "lin_che",
                    "display_name": "林澈",
                    "role": "protagonist",
                    "want_vs_need": {
                        "want": "夺回灵田",
                        "need": "学会授权",
                        "tension": "越清账越暴露孤身习惯",
                    },
                    "unique_response_chain": {
                        "moral_dilemma": {
                            "step_1": "先停住算盘。",
                            "step_2": "重新衡量授权代价。",
                            "step_3": "付出明面损失。",
                        }
                    },
                    "voice_dna": {"signature_words": ["先把账算清"]},
                    "signature_assets": {"object": "裂纹账珠"},
                },
            ),
        )
    )

    assert "character_engine 融合档案" in block
    assert "林澈" in block
    assert "先把账算清" in block
    assert "反应链[moral_dilemma]" in block


def test_build_writer_quality_levers_block_english_skips_platform_block() -> None:
    block = build_writer_quality_levers_block(
        WriterLeverContext(
            chapter_number=1,
            language="en",
            platform="qimao",
        )
    )
    # English projects skip the Qimao-specific Chinese platform block
    assert "七猫" not in block


def test_build_writer_quality_levers_block_empty_when_no_phase4_data() -> None:
    # Even with no inputs, Phase 4 phase-independent blocks always emit.
    block = build_writer_quality_levers_block(WriterLeverContext(chapter_number=0))
    assert block != ""


def test_build_writer_quality_levers_block_includes_distilled_strategy_card() -> None:
    block = build_writer_quality_levers_block(
        WriterLeverContext(
            chapter_number=5,
            distilled_strategy_card=_strategy_card(),
        )
    )

    assert "蒸馏策略卡" in block
    assert "cross-system-rule-arbitrage" in block
    assert "禁止出现策略卡、机制名或规划术语" in block


def test_build_writer_quality_levers_block_includes_emotion_driven_kernel() -> None:
    block = build_writer_quality_levers_block(
        WriterLeverContext(
            chapter_number=2,
            emotion_driven_kernel=_emotion_kernel(),
        )
    )

    assert "emotion_driven_core" in block
    assert "主角当前欲望" in block
    assert "证物袋已经被调包" in block
    assert "不可逆代价" in block


# ---------------------------------------------------------------------------
# integrator — critic side
# ---------------------------------------------------------------------------


def test_build_critic_quality_levers_block_minimal_empty() -> None:
    assert (
        build_critic_quality_levers_block(CriticLeverContext(chapter_number=1))
        == ""
    )


def test_build_critic_quality_levers_block_with_platform_and_position() -> None:
    block = build_critic_quality_levers_block(
        CriticLeverContext(
            chapter_number=1,
            language="zh-CN",
            platform="qimao",
            chapter_positions=("first_chapter",),
        )
    )
    assert "七猫" in block or "qimao" in block.lower()
    assert "first_chapter" in block
    # Critic block should NOT carry writer-only fragments
    assert "rhythm_engineering" not in block
    assert "emotion_choreography" not in block


def test_build_critic_quality_levers_block_includes_distilled_strategy_checks() -> None:
    block = build_critic_quality_levers_block(
        CriticLeverContext(
            chapter_number=5,
            distilled_strategy_card=_strategy_card(),
        )
    )

    assert "蒸馏策略卡" in block
    assert "cross_system_understanding" in block
    assert "避免泄露策略/机制术语" in block


# ---------------------------------------------------------------------------
# multi-persona executor
# ---------------------------------------------------------------------------


def _build_runner(
    responses: dict[str, dict] | None = None,
    *,
    raise_for: set[str] | None = None,
):
    responses = responses or {}
    raise_for = raise_for or set()

    def runner(system_prompt: str, user_prompt: str) -> dict:
        # Tease out the persona_id from the system prompt header.
        persona_id = ""
        for known in (
            "platform_editor",
            "new_reader",
            "loyal_reader",
            "peer_author",
        ):
            if known in system_prompt:
                persona_id = known
                break
        if persona_id in raise_for:
            raise RuntimeError(f"forced failure for {persona_id}")
        return responses.get(persona_id, {"overall_score": 0.80, "must_rewrite": False})

    return runner


def test_run_multi_persona_critique_aggregates_four_personas() -> None:
    runner = _build_runner(
        responses={
            "platform_editor": {"overall_score": 0.82, "must_rewrite": False},
            "new_reader": {"overall_score": 0.78, "must_rewrite": False},
            "loyal_reader": {"overall_score": 0.80, "must_rewrite": False},
            "peer_author": {"overall_score": 0.76, "must_rewrite": False},
        }
    )
    execution = run_multi_persona_critique(
        chapter_text="两个时辰。",
        persona_runner=runner,
    )
    assert isinstance(execution, MultiPersonaExecution)
    persona_ids = {inv.persona_id for inv in execution.invocations}
    assert {"platform_editor", "new_reader", "loyal_reader", "peer_author"} <= persona_ids
    aggregate = execution.aggregate
    assert pytest.approx(aggregate.min_score, abs=1e-3) == 0.76
    assert aggregate.must_rewrite is False


def test_run_multi_persona_critique_triggers_rewrite_on_low_score() -> None:
    runner = _build_runner(
        responses={
            "platform_editor": {"overall_score": 0.80, "must_rewrite": False},
            "new_reader": {"overall_score": 0.62, "must_rewrite": True},  # below hard floor
            "loyal_reader": {"overall_score": 0.80, "must_rewrite": False},
            "peer_author": {"overall_score": 0.80, "must_rewrite": False},
        }
    )
    execution = run_multi_persona_critique(
        chapter_text="...",
        persona_runner=runner,
    )
    assert execution.aggregate.must_rewrite is True


def test_run_multi_persona_critique_records_runner_errors() -> None:
    runner = _build_runner(raise_for={"peer_author"})
    execution = run_multi_persona_critique(
        chapter_text="...",
        persona_runner=runner,
    )
    error_records = [inv for inv in execution.invocations if inv.error]
    assert any(inv.persona_id == "peer_author" for inv in error_records)
    # The other personas still completed
    assert any(inv.result is not None for inv in execution.invocations)


def test_run_multi_persona_critique_handles_non_dict_response() -> None:
    def bad_runner(_sys: str, _user: str) -> dict:
        return "not a dict"  # type: ignore[return-value]

    execution = run_multi_persona_critique(
        chapter_text="...", persona_runner=bad_runner
    )
    assert all(inv.result is None for inv in execution.invocations)


def test_decode_runner_result_handles_dict_str_other() -> None:
    assert decode_runner_result({"a": 1}) == {"a": 1}
    assert decode_runner_result('{"a": 1}') == {"a": 1}
    assert decode_runner_result("not json") == {}
    assert decode_runner_result(42) == {}
