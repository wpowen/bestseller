"""爽文融合开关 (enable_shuangwen_fusion) tests.

The switch keeps every 文采 lever in the PROSE_SCENE writer prompt but lifts the
爽点 engines (弹簧法情绪压缩/释放、节奏、信息节奏、章节爽点) above the literary
flourish levers (金句/意象/留白), so 爽点 is never the first content the token
budget drops. Default ON — most commercial books should carry 爽点.
"""

from __future__ import annotations

import pytest

from bestseller.services.methodology_compiler import (
    _PROSE_SCENE_SHUANGWEN_PRIORITY,
    SECTION_PRIORITY,
    MethodologyStage,
    compile_methodology,
)
from bestseller.settings import PipelineSettings

_PAYOFF = "emotion_choreography_current"  # 弹簧法 — 爽点核心
_FLOURISH = ("prose_craft_techniques", "imagery_system_current", "prose_lever_framing")


def test_pipeline_enables_shuangwen_fusion_by_default() -> None:
    assert PipelineSettings().enable_shuangwen_fusion is True


def test_default_priority_puts_literary_levers_before_payoff_engines() -> None:
    order = SECTION_PRIORITY[MethodologyStage.PROSE_SCENE]
    # The regression we are fixing: today the 爽点 engine sits *after* the
    # literary flourish levers, so it is first to be starved by the budget.
    for lever in _FLOURISH:
        assert order.index(lever) < order.index(_PAYOFF)


def test_shuangwen_priority_lifts_payoff_engines_above_flourish() -> None:
    order = _PROSE_SCENE_SHUANGWEN_PRIORITY
    for lever in _FLOURISH:
        assert order.index(_PAYOFF) < order.index(lever), (
            f"爽文模式下 {_PAYOFF} 必须排在 {lever} 之前"
        )


def test_shuangwen_priority_keeps_every_section_no_drop() -> None:
    # Fusion, not removal: same set of levers, only reordered.
    assert set(_PROSE_SCENE_SHUANGWEN_PRIORITY) == set(
        SECTION_PRIORITY[MethodologyStage.PROSE_SCENE]
    )


def test_shuangwen_priority_keeps_concrete_grounding_levers_high() -> None:
    # 物料具体化 + 镜头锚定 are anti-作文 and *help* 爽点 land, so they stay
    # ahead of the payoff engines (concrete action before the spring releases).
    order = _PROSE_SCENE_SHUANGWEN_PRIORITY
    assert order.index("material_concretization_current") < order.index(_PAYOFF)
    assert order.index("scene_grounding_current") < order.index(_PAYOFF)


@pytest.mark.parametrize("pack", ["xianxia-upgrade-core", "apocalypse-supply-chain"])
def test_compile_orders_payoff_before_craft_in_shuangwen_mode(pack: str) -> None:
    base = compile_methodology(
        stage=MethodologyStage.PROSE_SCENE,
        prompt_pack_key=pack,
        chapter_no=7,
        token_budget=4000,  # production budget — fits all levers + cinematic_pov
    )
    sw = compile_methodology(
        stage=MethodologyStage.PROSE_SCENE,
        prompt_pack_key=pack,
        chapter_no=7,
        token_budget=4000,
        shuangwen_mode=True,
    )
    base_srcs = list(base.used_sources)
    sw_srcs = list(sw.used_sources)

    # Same levers reach the writer either way (fusion preserves 文采).
    assert set(sw_srcs) == set(base_srcs)

    # Default: 金句 lever lands before the 弹簧法 engine (the regression).
    assert base_srcs.index("prose_craft_techniques.yaml") < base_srcs.index(
        "emotion_choreography.yaml"
    )
    # 爽文模式: 弹簧法 engine lands before the 金句 lever.
    assert sw_srcs.index("emotion_choreography.yaml") < sw_srcs.index(
        "prose_craft_techniques.yaml"
    )
