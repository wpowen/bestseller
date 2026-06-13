"""Unit tests for the 文采 (literary craft) capability.

Covers the distilled craft KB loader/renderer
(``quality_levers.prose_craft_techniques``) and its soft wiring into the
PROSE_SCENE methodology block. The capability MUST:

* distil 绝句-style craft into transferable technique skeletons (not phrases);
* route modern genres away from 古风 imagery (anti-purple);
* rotate technique subsets by chapter (anti-homogenisation);
* surface only as writer guidance — never as a gate (soft).
"""

from __future__ import annotations

import pytest

from bestseller.services.methodology_compiler import (
    MethodologyStage,
    compile_methodology,
)
from bestseller.services.quality_levers.prose_craft_techniques import (
    load_prose_craft_techniques,
    render_prose_craft_block,
    resolve_genre_emphasis_key,
    select_techniques,
)


pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# KB integrity
# ---------------------------------------------------------------------------


def test_load_returns_expected_techniques() -> None:
    config = load_prose_craft_techniques()
    ids = set(config.techniques)
    assert {
        "image_juxtaposition",
        "parallelism",
        "synesthesia",
        "concrete_abstract_pivot",
        "numeric_tension",
        "end_on_image",
        "contrast_turn",
        "sober_aphorism",
        "colloquial_blade",
    } <= ids
    assert config.techniques_per_scene >= 2
    assert config.purple_guard, "purple_prose_guard must be populated"


def test_every_technique_has_actionable_fields() -> None:
    config = load_prose_craft_techniques()
    for technique in config.techniques.values():
        assert technique.principle, technique.technique_id
        assert technique.structure, technique.technique_id
        # Each technique must ship at least one synthetic micro-example so the
        # writer sees the skeleton in action.
        assert technique.examples, technique.technique_id


def test_genre_emphasis_has_default_fallback() -> None:
    config = load_prose_craft_techniques()
    assert "default" in config.genre_emphasis
    assert config.genre_emphasis["default"]


# ---------------------------------------------------------------------------
# Genre routing — the anti-purple guarantee
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("terms", "expected"),
    [
        (("都市", "职场"), "都市"),
        (("古风言情",), "古风"),
        (("悬疑",), "悬疑"),
        (("科幻",), "科幻"),
        (("仙侠升级",), "仙侠"),
        (("东方美学幻想",), "东方美学"),
        (("Romance", "Dark Romance"), "default"),  # English → no 古风 leak
        ((), "default"),
        (("完全未知题材X",), "default"),
    ],
)
def test_genre_routing(terms: tuple[str, ...], expected: str) -> None:
    assert resolve_genre_emphasis_key(terms) == expected


@pytest.mark.parametrize("modern", [("都市",), ("职场",), ("科幻",), ("现实",)])
def test_modern_genres_never_get_guofeng_imagery(modern: tuple[str, ...]) -> None:
    """都市/职场/科幻/现实 must not be routed to 意象并置 (the古风 purple risk)."""

    ids = {t.technique_id for t in select_techniques(genre_terms=modern, chapter_number=1)}
    assert "image_juxtaposition" not in ids, (modern, ids)


def test_guofeng_does_get_imagery() -> None:
    ids = {
        t.technique_id
        for ch in range(1, 5)
        for t in select_techniques(genre_terms=("古风",), chapter_number=ch)
    }
    assert "image_juxtaposition" in ids


# ---------------------------------------------------------------------------
# Rotation — anti-homogenisation
# ---------------------------------------------------------------------------


def test_rotation_varies_selection_across_chapters() -> None:
    sel1 = [t.technique_id for t in select_techniques(genre_terms=("古风",), chapter_number=1)]
    sel2 = [t.technique_id for t in select_techniques(genre_terms=("古风",), chapter_number=2)]
    assert sel1 != sel2, "consecutive chapters should rotate the technique window"


def test_selection_respects_per_scene_cap() -> None:
    config = load_prose_craft_techniques()
    sel = select_techniques(genre_terms=("古风",), chapter_number=1)
    assert 1 <= len(sel) <= config.techniques_per_scene


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def test_render_block_has_header_examples_and_purple_guard() -> None:
    block = render_prose_craft_block(genre_terms=("都市",), chapter_number=1)
    assert "文采技法" in block
    assert "例：" in block  # at least one worked micro-example
    assert "文采≠辞藻堆砌" in block  # the purple-prose guard line


def test_render_block_is_genre_specific() -> None:
    urban = render_prose_craft_block(genre_terms=("都市",), chapter_number=1)
    guofeng = render_prose_craft_block(genre_terms=("古风",), chapter_number=1)
    assert "口语锋利" in urban
    assert "口语锋利" not in guofeng  # colloquial blade is wrong for 古风
    assert "意象并置" in guofeng


def test_render_block_empty_terms_falls_back_to_default() -> None:
    block = render_prose_craft_block(genre_terms=(), chapter_number=1)
    assert "文采技法" in block  # default emphasis still renders a usable block


# ---------------------------------------------------------------------------
# Soft wiring into the writer (PROSE_SCENE) — present in writing, absent in review
# ---------------------------------------------------------------------------


def test_prose_scene_includes_craft_block() -> None:
    out = compile_methodology(
        stage=MethodologyStage.PROSE_SCENE,
        prompt_pack_key="suspense-mystery",
        language="zh-CN",
        chapter_no=3,
        token_budget=4000,  # production budget — fits cinematic_pov + craft together
    )
    assert "prose_craft_techniques.yaml" in out.used_sources
    assert "文采技法" in out.text


def test_review_stage_excludes_craft_block() -> None:
    out = compile_methodology(
        stage=MethodologyStage.REVIEW,
        prompt_pack_key="suspense-mystery",
        language="zh-CN",
        chapter_no=3,
        token_budget=2500,
    )
    assert "prose_craft_techniques.yaml" not in out.used_sources


def test_english_language_emits_no_craft_block() -> None:
    out = compile_methodology(
        stage=MethodologyStage.PROSE_SCENE,
        prompt_pack_key="suspense-mystery",
        language="en",
        chapter_no=3,
        token_budget=2500,
    )
    assert "prose_craft_techniques.yaml" not in out.used_sources
