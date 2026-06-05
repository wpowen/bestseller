"""Unit tests for the 物料具体化 (material concreteness) capability — Layer 3.

The capability MUST:

* render a soft, genre-neutral concretization directive into PROSE_SCENE;
* deterministically flag abstract §default-dominated material (run on the
  BIBLE, not the prose) and pass concrete material;
* never act as a hard gate (soft).
"""

from __future__ import annotations

import pytest

from bestseller.services.methodology_compiler import (
    MethodologyStage,
    compile_methodology,
)
from bestseller.services.quality_levers.material_concreteness import (
    detect_material_abstractness,
    load_material_concreteness,
    render_concretization_directive,
)

pytestmark = pytest.mark.unit

# Mirrors the real pilot bible: §default-* refs + mechanism vocabulary.
_ABSTRACT_BIBLE = (
    "§power_systems/uuid/default-core-system：商业类型状态引擎 — 章节必须围绕目标、阻力、"
    "选择、代价和状态变化推进。\n"
    "§power_systems/uuid/default-state-delta-rule：状态变化规则 — 每章产生一个可记录的状态变化。\n"
    "§world_settings/uuid/default-promise-world：通用品类读者承诺世界。\n"
    "§factions/uuid/default-core-faction：核心阻力方 — 阻力方会升级反应。\n"
)

_CONCRETE_BIBLE = (
    "陆沉因高压电击成了气运借贷节点，掌心一道会蔓延的黑纹，七日复利。\n"
    "陈三指收电费，缺三根手指。卫东把二十三人名单压在照片下。\n"
    "对手沈墨白盯上了陆沉的妹妹陆芷晴。\n"
)


def test_load_directive_and_markers() -> None:
    config = load_material_concreteness()
    assert config.directive.body, "concretization directive body must be populated"
    assert config.abstract_markers, "abstract markers must be populated"
    assert "商业类型状态引擎" in config.abstract_markers


def test_directive_renders_and_is_genre_neutral() -> None:
    block = render_concretization_directive(genre_terms=("都市异能",), chapter_number=5)
    assert "物料具体化" in block
    assert "实例化" in block
    # genre-neutral: same output regardless of genre terms
    assert block == render_concretization_directive(genre_terms=("悬疑",), chapter_number=1)


def test_detector_flags_abstract_bible() -> None:
    result = detect_material_abstractness(_ABSTRACT_BIBLE)
    assert not result.passed
    assert result.marker_density_per_kchars > result.marker_threshold
    assert result.default_slug_ratio == 1.0
    assert result.total_slug_refs == 4


def test_detector_passes_concrete_bible() -> None:
    result = detect_material_abstractness(_CONCRETE_BIBLE)
    assert result.passed
    assert result.default_slug_refs == 0


def test_detector_empty_text_is_safe() -> None:
    result = detect_material_abstractness("")
    assert result.passed
    assert result.marker_density_per_kchars == 0.0


def test_prose_scene_includes_concretization_directive() -> None:
    out = compile_methodology(
        stage=MethodologyStage.PROSE_SCENE,
        prompt_pack_key="urban-power-reversal",
        language="zh-CN",
        chapter_no=5,
        token_budget=3500,
    )
    assert "material_concreteness.yaml" in out.used_sources
    assert "物料具体化" in out.text


def test_review_stage_excludes_concretization_directive() -> None:
    out = compile_methodology(
        stage=MethodologyStage.REVIEW,
        prompt_pack_key="urban-power-reversal",
        language="zh-CN",
        chapter_no=5,
        token_budget=3500,
    )
    assert "material_concreteness.yaml" not in out.used_sources


def test_english_emits_no_concretization_directive() -> None:
    out = compile_methodology(
        stage=MethodologyStage.PROSE_SCENE,
        prompt_pack_key="urban-power-reversal",
        language="en",
        chapter_no=5,
        token_budget=3500,
    )
    assert "material_concreteness.yaml" not in out.used_sources
