"""W4-3 回归：量尺**不改数值**，只如实标注"哪些通道没有实测"（2026-09-10）。

计划 §3 W4 步骤 3 要求"文案降级、门禁降级和评分公式变更**分别**提交与验证，
避免一个改动同时改变量尺和被测对象"。复核结果：

* 公式本身**未发现可证缺陷**——已证的量尺问题是**阈值校准**（0.62 门槛高于
  写手实测 ~0.51 上限，见 `config/quality_gates.yaml` 的自曝），需真实分数分布
  才能定，属独立校准步骤；因此本步**不改** `_normalize_channels` /
  `_score_for_persona` 的任何数值，2026-09-10 的基线保持有效。
* 但复核发现一处**可证的诚实缺陷**：三个批评器输入是**可选**的
  （`chapter_signal_builder.build_signal_pack` 的 `novelty_score` /
  `consistency_score` / `prose_quality_score`），缺省时用固定常数代替
  （0.55 / 0.75 / 0.65）；而**生产调用点只传 `prose_quality_score`**，
  于是 `novelty` / `consistency` 两个通道**每章都在用常数计分**，
  诊断却把它们说成本章的问题。

本步修的是"诚实化测量"：把未实测通道如实带到回执与诊断文案，
**不动任何分数**。这样既不改变量尺（可回归），又让"测出来的低分"与
"默认值带来的分"可分辨。
"""

from __future__ import annotations

import inspect

import pytest

from bestseller.services.chapter_signal_builder import build_signal_pack
from bestseller.services.reader_persona_simulator import (
    ChapterSignalPack,
    _normalize_channels,
    render_persona_feedback_block,
    simulate_readers,
)

pytestmark = pytest.mark.unit


# ── 1. 量尺数值未变（W4-3 的核心承诺）────────────────────────────────────


def _strong_signals() -> ChapterSignalPack:
    return ChapterSignalPack(
        chapter_position=5,
        chapter_text_chars=2500,
        hook_count=5,
        payoff_count=3,
        cliffhanger_strength=0.85,
        voice_dna_drift=0.1,
        market_hooks_hit=4,
        market_hooks_required=3,
        novelty_score=0.7,
        consistency_score=0.9,
        emotional_beat_count=3,
        saturated_trope_hits=0,
        target_length_min=2200,
        target_length_max=3000,
        dialogue_ratio=0.35,
        action_ratio=0.25,
        interior_ratio=0.2,
        prose_quality_score=0.8,
    )


def _weak_signals() -> ChapterSignalPack:
    return ChapterSignalPack(
        chapter_position=5,
        chapter_text_chars=4500,
        hook_count=0,
        payoff_count=0,
        cliffhanger_strength=0.1,
        voice_dna_drift=0.7,
        market_hooks_hit=0,
        market_hooks_required=3,
        novelty_score=0.15,
        consistency_score=0.4,
        emotional_beat_count=0,
        saturated_trope_hits=3,
        target_length_min=2200,
        target_length_max=3000,
        dialogue_ratio=0.05,
        action_ratio=0.05,
        interior_ratio=0.05,
        prose_quality_score=0.35,
    )


def test_numeric_ruler_is_unchanged():
    """与 `test_reader_persona_simulator.py` 的 pinning 同源，证明分毫未动。"""

    strong = simulate_readers(_strong_signals())
    assert strong.weighted_score == pytest.approx(0.8634631208120976, abs=1e-12)
    assert strong.abandon_rate == pytest.approx(0.038011571977255916, abs=1e-12)

    weak = simulate_readers(_weak_signals())
    assert weak.weighted_score == pytest.approx(0.02726468601639946, abs=1e-12)
    assert weak.abandon_rate == pytest.approx(0.9578282470529792, abs=1e-12)


def test_channel_formula_is_unchanged():
    channels = _normalize_channels(_strong_signals())
    assert channels["hook_density"] == pytest.approx(0.8189195275630781, abs=1e-12)
    assert channels["payoff_density"] == pytest.approx(0.6899744811276125, abs=1e-12)
    assert channels["pacing"] == pytest.approx(0.85, abs=1e-12)
    assert channels["emotional_impact"] == pytest.approx(0.7109495026250039, abs=1e-12)


# ── 2. 量尺成分：客观文本度量 vs 调用方传入 ──────────────────────────────


def test_hook_density_depends_only_on_objective_counts():
    """hook_density 只由正文计数/cliff 决定，不受调用方传入分影响。"""

    base = _strong_signals()
    other = ChapterSignalPack(
        **{**_strong_signals().__dict__, "novelty_score": 0.05, "consistency_score": 0.05}
    )
    assert _normalize_channels(base)["hook_density"] == _normalize_channels(other)[
        "hook_density"
    ]


def test_novelty_and_consistency_channels_depend_on_caller_scores():
    base = _normalize_channels(_strong_signals())
    lowered = _normalize_channels(
        ChapterSignalPack(
            **{
                **_strong_signals().__dict__,
                "novelty_score": 0.05,
                "consistency_score": 0.05,
            }
        )
    )
    assert lowered["novelty"] < base["novelty"]
    assert lowered["consistency"] < base["consistency"]
    # 反向：客观计数不改变 novelty / consistency
    counted = ChapterSignalPack(**{**_strong_signals().__dict__, "hook_count": 0})
    assert _normalize_channels(counted)["novelty"] == base["novelty"]
    assert _normalize_channels(counted)["consistency"] == base["consistency"]


# ── 3. 诚实化测量：未实测通道被如实记录 ──────────────────────────────────


def test_build_signal_pack_records_defaulted_channels():
    only_prose = build_signal_pack(
        "他抬手。",
        chapter_position=1,
        prose_quality_score=0.7,
    )
    assert only_prose.defaulted_channels == ("novelty", "consistency")

    all_three = build_signal_pack(
        "他抬手。",
        chapter_position=1,
        novelty_score=0.6,
        consistency_score=0.8,
        prose_quality_score=0.7,
    )
    assert all_three.defaulted_channels == ()

    none = build_signal_pack("他抬手。", chapter_position=1)
    assert set(none.defaulted_channels) == {"novelty", "consistency", "prose_quality"}


def test_production_call_site_only_supplies_prose_quality():
    """生产调用点只传 prose_quality_score —— 这是"常数计分"的证据。"""

    from bestseller.services import pipelines

    src = inspect.getsource(pipelines)
    assert "prose_quality_score=_prose_quality_score" in src
    assert "novelty_score=" not in src, "生产链没有传 novelty_score（故走常数 0.55）"
    assert "consistency_score=" not in src, "生产链没有传 consistency_score（故走常数 0.75）"


def test_result_carries_unmeasured_channels():
    pack = ChapterSignalPack(
        **{
            **_strong_signals().__dict__,
            "defaulted_channels": ("novelty", "consistency"),
        }
    )
    result = simulate_readers(pack)
    assert result.unmeasured_channels == ["novelty", "consistency"]


def test_feedback_block_names_unmeasured_channels_zh_and_en():
    pack = ChapterSignalPack(
        **{**_strong_signals().__dict__, "defaulted_channels": ("novelty",)}
    )
    result = simulate_readers(pack)

    zh = render_persona_feedback_block(result, language="zh-CN")
    assert "未实测通道" in zh and "novelty" in zh

    en = render_persona_feedback_block(result, language="en-US")
    assert "Channels not measured" in en and "novelty" in en


def test_feedback_card_carries_unmeasured_channels():
    result = simulate_readers(
        ChapterSignalPack(
            **{**_strong_signals().__dict__, "defaulted_channels": ("consistency",)}
        )
    )
    card = result.to_feedback_card()
    assert card["unmeasured_channels"] == ["consistency"]


def test_old_payload_without_unmeasured_field_still_validates():
    """兼容既有落盘 JSON：缺字段 → 默认空列表。"""

    from bestseller.domain.reader_persona import PersonaSimulationResult

    legacy = {
        "chapter_position": 3,
        "weighted_score": 0.4,
        "abandon_rate": 0.5,
        "high_risk_personas": [],
        "aggregated_concerns": [],
        "next_chapter_directives": [],
        "per_persona": [],
    }
    restored = PersonaSimulationResult.model_validate(legacy)
    assert restored.unmeasured_channels == []


def test_pipelines_records_unmeasured_channels_in_evidence():
    from bestseller.services import pipelines

    src = inspect.getsource(pipelines._evaluate_retention_safety_after_assembly)
    assert '"unmeasured_channels"' in src
