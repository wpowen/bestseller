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


def test_pipeline_enables_shuangwen_fusion_by_default() -> None:
    assert PipelineSettings().enable_shuangwen_fusion is True


def test_default_priority_keeps_payoff_after_concrete_grounding() -> None:
    order = SECTION_PRIORITY[MethodologyStage.PROSE_SCENE]
    assert order.index("material_concretization_current") < order.index(_PAYOFF)
    assert order.index("scene_grounding_current") < order.index(_PAYOFF)


def test_shuangwen_priority_reuses_canonical_prose_order() -> None:
    # 词藻型三杠杆已从正文写手注入移除，爽文开关保留兼容性但不再维护一份
    # 容易漂移的等价排序。
    assert _PROSE_SCENE_SHUANGWEN_PRIORITY == SECTION_PRIORITY[
        MethodologyStage.PROSE_SCENE
    ]


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
def test_compile_omits_negative_craft_levers_in_both_modes(pack: str) -> None:
    # 预算需"装下所有杠杆"以测排序不变量(与生产默认1500无关,此为宽松测试值)。
    # 2026-06-29 fusion block 增位置感知块(开篇炸点律/中段持续追读律,各~150token)后,
    # 4200 不再装下最低优先级的金句文采杠杆 → 抬到 4800 恢复"装下所有"前提。
    budget = 4800
    base = compile_methodology(
        stage=MethodologyStage.PROSE_SCENE,
        prompt_pack_key=pack,
        chapter_no=7,
        token_budget=budget,
    )
    sw = compile_methodology(
        stage=MethodologyStage.PROSE_SCENE,
        prompt_pack_key=pack,
        chapter_no=7,
        token_budget=budget,
        shuangwen_mode=True,
    )
    base_srcs = list(base.used_sources)
    sw_srcs = list(sw.used_sources)

    # The writer keeps the proven commercial controls in both modes.
    assert set(sw_srcs) == set(base_srcs)
    assert "emotion_choreography.yaml" in base_srcs
    assert "material_concreteness.yaml" in base_srcs
    assert "scene_grounding.yaml" in base_srcs
    assert "prose_craft_techniques.yaml" not in base_srcs
    assert "litstyle_prose.py" not in base_srcs
