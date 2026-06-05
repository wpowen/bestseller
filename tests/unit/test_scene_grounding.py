"""Unit tests for the 镜头化场景锚定 (scene grounding) capability.

Covers the distilled craft KB loader/renderer + the three deterministic
detectors (``quality_levers.scene_grounding``) and its soft wiring into the
PROSE_SCENE methodology block. The capability MUST:

* distil camera discipline into transferable technique skeletons (not phrases);
* route per genre family with a default fallback (no 古风 leak into 都市);
* rotate technique subsets by chapter (anti-homogenisation);
* surface as writer guidance only — never as a gate (soft);
* deterministically separate authorial-intrusion ("作文") prose from cinematic
  prose (the A/B scoreboard contract).
"""

from __future__ import annotations

import pytest

from bestseller.services.methodology_compiler import (
    MethodologyStage,
    compile_methodology,
)
from bestseller.services.quality_levers.scene_grounding import (
    audit_scene_grounding,
    detect_authorial_intrusion,
    detect_proper_noun_flood,
    load_scene_grounding,
    measure_grounding_coverage,
    render_scene_grounding_block,
    resolve_genre_emphasis_key,
    select_techniques,
)

pytestmark = pytest.mark.unit


# Cinematic, anchored prose (excerpt-style, protagonist POV, no author summary).
_CINEMATIC = (
    "拇指按在二号杆塔的螺栓上，电弧咬进掌心。陆沉没松手。\n"
    "暴雨把绝缘手套浇透了，电流从指缝钻进去，往上爬。\n"
    "「别动。」声音从右边传来。陆沉转头，五十米外站着个老头。\n"
    "他低头看掌心。黑印在雨里看不太清，但他感觉得到它在动。\n"
)

# Essay-like prose: author explains causation/theme, abstract plot mechanics.
_ESSAY = (
    "合同编号触发了追踪模型，三个词凑齐，自动打标签。能调出行为预警的，内部不超过五个。\n"
    "沈墨白把他塞进名单，不是因为信任，是因为他好用。\n"
    "他被当成了工具。方远知道追的人会死，所以他把自己也烧进去，让账结在他身上。\n"
    "这一切都说明，他从头到尾就是被挑中的那头羊。\n"
)


# ---------------------------------------------------------------------------
# KB integrity
# ---------------------------------------------------------------------------


def test_load_returns_expected_techniques() -> None:
    config = load_scene_grounding()
    ids = set(config.techniques)
    assert {
        "establishing_through_want",
        "grounded_transition",
        "show_dont_explain",
        "detail_serves_plot",
        "one_name_at_a_time",
        "pov_camera_continuity",
    } <= ids
    assert config.techniques_per_scene >= 2
    assert config.intrusion_guard, "authorial_intrusion_guard must be populated"


def test_every_technique_has_actionable_fields() -> None:
    config = load_scene_grounding()
    for technique in config.techniques.values():
        assert technique.principle, technique.technique_id
        assert technique.structure, technique.technique_id
        assert technique.examples, technique.technique_id


def test_genre_emphasis_has_default_fallback() -> None:
    config = load_scene_grounding()
    assert "default" in config.genre_emphasis
    assert config.genre_emphasis["default"]


# ---------------------------------------------------------------------------
# Genre routing — longest-match wins, default fallback
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("terms", "expected"),
    [
        (("都市异能", "身份反转"), "都市异能"),  # longest match beats "都市"
        (("都市", "职场"), "都市"),
        (("悬疑",), "悬疑"),
        (("科幻",), "科幻"),
        (("Romance",), "default"),
        ((), "default"),
        (("完全未知题材X",), "default"),
    ],
)
def test_genre_routing(terms: tuple[str, ...], expected: str) -> None:
    assert resolve_genre_emphasis_key(terms) == expected


def test_rotation_varies_selection_across_chapters() -> None:
    sel1 = [t.technique_id for t in select_techniques(genre_terms=("都市异能",), chapter_number=1)]
    sel2 = [t.technique_id for t in select_techniques(genre_terms=("都市异能",), chapter_number=2)]
    assert sel1 != sel2


def test_selection_respects_per_scene_cap() -> None:
    config = load_scene_grounding()
    sel = select_techniques(genre_terms=("都市异能",), chapter_number=1)
    assert 1 <= len(sel) <= config.techniques_per_scene


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def test_render_block_has_header_examples_and_guard() -> None:
    block = render_scene_grounding_block(genre_terms=("都市异能",), chapter_number=1)
    assert "场景锚定" in block
    assert "例：" in block
    assert "镜头化≠作者旁白" in block


def test_render_block_empty_terms_falls_back_to_default() -> None:
    block = render_scene_grounding_block(genre_terms=(), chapter_number=1)
    assert "场景锚定" in block


# ---------------------------------------------------------------------------
# Detectors — the A/B scoreboard contract
# ---------------------------------------------------------------------------


def test_authorial_intrusion_separates_essay_from_cinematic() -> None:
    cinematic = detect_authorial_intrusion(_CINEMATIC)
    essay = detect_authorial_intrusion(_ESSAY)
    assert cinematic.density_per_kchars < essay.density_per_kchars
    assert cinematic.passed, "cinematic prose must pass the intrusion gate"
    assert not essay.passed, "essay-like prose must fail the intrusion gate"


def test_authorial_intrusion_ignores_dialogue() -> None:
    # The same causal connective inside dialogue must not be counted.
    spoken = '「他之所以来，是因为他知道账要结了。」陆沉没接话。'
    result = detect_authorial_intrusion(spoken)
    assert result.hits == 0


def test_grounding_coverage_runs_and_reports() -> None:
    result = measure_grounding_coverage(_CINEMATIC)
    assert 0.0 <= result.coverage <= 1.0
    assert result.narrative_paragraphs >= 1


def test_proper_noun_flood_flags_number_storm() -> None:
    storm = "名单上二十三个人，背后还有一百个，另一个通道七十七个，百分之十二点七是他的。\n"
    result = detect_proper_noun_flood(storm)
    assert result.number_tokens >= 3


def test_audit_aggregates_three_signals() -> None:
    audit = audit_scene_grounding(_CINEMATIC)
    assert audit.passed
    data = audit.to_dict()
    assert set(data) >= {
        "passed",
        "authorial_intrusion",
        "grounding_coverage",
        "proper_noun_flood",
    }


# ---------------------------------------------------------------------------
# Soft wiring into the writer (PROSE_SCENE) — present in writing, absent in review
# ---------------------------------------------------------------------------


def test_prose_scene_includes_scene_grounding_block() -> None:
    out = compile_methodology(
        stage=MethodologyStage.PROSE_SCENE,
        prompt_pack_key="urban-power-reversal",
        language="zh-CN",
        chapter_no=5,
        token_budget=3000,
    )
    assert "scene_grounding.yaml" in out.used_sources
    assert "场景锚定" in out.text


def test_review_stage_excludes_scene_grounding_block() -> None:
    out = compile_methodology(
        stage=MethodologyStage.REVIEW,
        prompt_pack_key="urban-power-reversal",
        language="zh-CN",
        chapter_no=5,
        token_budget=3000,
    )
    assert "scene_grounding.yaml" not in out.used_sources


def test_english_language_emits_no_scene_grounding_block() -> None:
    out = compile_methodology(
        stage=MethodologyStage.PROSE_SCENE,
        prompt_pack_key="urban-power-reversal",
        language="en",
        chapter_no=5,
        token_budget=3000,
    )
    assert "scene_grounding.yaml" not in out.used_sources
