"""Unit tests for the four Phase 2 Hype Engine chapter-validator checks.

Covers:
    * HypeOccurrenceCheck   — assigned recipe trigger keywords / classifier
    * HypeDiversityCheck    — consecutive same-type assignments
    * EndingSentenceImpactCheck — 4-point last-sentence score + golden-3 block
    * GoldenThreeChapterCheck   — first 3 chapters must land selling-point +
                                  hype trigger density

The plan's Phase-2 test checklist (plan §Phase 2 "测试") is the source of
truth; each test below is labeled with the plan bullet it satisfies.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from bestseller.services.chapter_validator import (
    EndingSentenceImpactCheck,
    GoldenThreeChapterCheck,
    HypeDiversityCheck,
    HypeOccurrenceCheck,
)
from bestseller.services.hype_engine import (
    HypeRecipe,
    HypeScheme,
    HypeType,
)
from bestseller.services.invariants import (
    LengthEnvelope,
    ProjectInvariants,
)
from bestseller.services.output_validator import ValidationContext
from bestseller.services.write_gate import filter_blocking, QualityReport


pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _recipe(
    key: str = "冥符拍脸-当众羞辱反转",
    hype_type: HypeType = HypeType.FACE_SLAP,
    trigger_keywords: tuple[str, ...] = ("冥符", "贴脸", "僵住"),
    narrative_beats: tuple[str, ...] = ("挑衅", "主角收声", "冥符拍脸"),
    intensity_floor: float = 8.0,
    cadence_hint: str = "",
) -> HypeRecipe:
    return HypeRecipe(
        key=key,
        hype_type=hype_type,
        trigger_keywords=trigger_keywords,
        narrative_beats=narrative_beats,
        intensity_floor=intensity_floor,
        cadence_hint=cadence_hint,
    )


def _scheme(
    *,
    recipes: tuple[HypeRecipe, ...] = (),
    selling_points: tuple[str, ...] = ("诡异复苏", "阴阳万亿资产"),
    hook_keywords: tuple[str, ...] = ("冥符", "阴兵", "香火"),
    reader_promise: str = "第一章亮出钱不是钱、冥符阴兵才是万亿资产。",
    chapter_hook_strategy: str = "每章至少抛出一条新诡异资产。",
) -> HypeScheme:
    return HypeScheme(
        recipe_deck=recipes,
        selling_points=selling_points,
        hook_keywords=hook_keywords,
        reader_promise=reader_promise,
        chapter_hook_strategy=chapter_hook_strategy,
    )


def _invariants(
    scheme: HypeScheme | None = None,
    language: str = "zh-CN",
) -> ProjectInvariants:
    return ProjectInvariants(
        project_id=uuid4(),
        language=language,
        length_envelope=LengthEnvelope(
            min_chars=2500, target_chars=3200, max_chars=4000
        ),
        hype_scheme=scheme if scheme is not None else HypeScheme(),
    )


def _ctx(
    *,
    scheme: HypeScheme | None = None,
    chapter_no: int = 1,
    assigned_hype_type: HypeType | None = None,
    assigned_hype_recipe: HypeRecipe | None = None,
    recent_hype_types: tuple[Any, ...] = (),
    language: str = "zh-CN",
    scope: str = "chapter",
) -> ValidationContext:
    return ValidationContext(
        invariants=_invariants(scheme, language=language),
        chapter_no=chapter_no,
        scope=scope,  # type: ignore[arg-type]
        assigned_hype_type=assigned_hype_type,
        assigned_hype_recipe=assigned_hype_recipe,
        recent_hype_types=recent_hype_types,
    )


# ---------------------------------------------------------------------------
# HypeOccurrenceCheck.
# ---------------------------------------------------------------------------


class TestHypeOccurrenceCheck:
    """Plan §Phase 2: 纯叙事无关键词 → HYPE_MISSING;
    2 关键词命中 → 通过; classifier fallback 通过."""

    def test_empty_text_no_violations(self) -> None:
        check = HypeOccurrenceCheck()
        recipe = _recipe()
        ctx = _ctx(
            assigned_hype_type=HypeType.FACE_SLAP,
            assigned_hype_recipe=recipe,
        )
        assert list(check.run("", ctx)) == []

    def test_no_assigned_recipe_is_noop(self) -> None:
        check = HypeOccurrenceCheck()
        ctx = _ctx(
            assigned_hype_type=HypeType.FACE_SLAP,
            assigned_hype_recipe=None,
        )
        text = "这是一段平铺直叙的叙事内容，没有任何爽点特征。" * 20
        assert list(check.run(text, ctx)) == []

    def test_recipe_without_trigger_keywords_opts_out(self) -> None:
        recipe = _recipe(trigger_keywords=())
        ctx = _ctx(
            assigned_hype_type=HypeType.FACE_SLAP,
            assigned_hype_recipe=recipe,
        )
        text = "纯叙事段落，没有关键词。" * 10
        assert list(HypeOccurrenceCheck().run(text, ctx)) == []

    def test_plain_narrative_without_keywords_flags_missing(self) -> None:
        recipe = _recipe()
        ctx = _ctx(
            assigned_hype_type=HypeType.FACE_SLAP,
            assigned_hype_recipe=recipe,
        )
        text = (
            "清晨的雾气慢慢散去，街道上没有什么人。他走了很久，走到一家早点摊前，"
            "要了一碗豆浆和两根油条。天空渐渐亮起来，城市开始苏醒。"
        )
        violations = list(HypeOccurrenceCheck().run(text, ctx))
        assert len(violations) == 1
        v = violations[0]
        assert v.code == "HYPE_MISSING"
        assert "face_slap" in v.location
        assert "冥符拍脸-当众羞辱反转" in v.detail

    def test_two_keyword_hits_pass(self) -> None:
        recipe = _recipe()  # trigger_keywords = ("冥符", "贴脸", "僵住")
        ctx = _ctx(
            assigned_hype_type=HypeType.FACE_SLAP,
            assigned_hype_recipe=recipe,
        )
        text = "他一道冥符贴脸甩出去，对方瞬间僵住。"
        # "冥符" + "贴脸" + "僵住" → 3 hits, above the 2 min.
        assert list(HypeOccurrenceCheck().run(text, ctx)) == []

    def test_single_keyword_hit_still_fails(self) -> None:
        recipe = _recipe()
        ctx = _ctx(
            assigned_hype_type=HypeType.FACE_SLAP,
            assigned_hype_recipe=recipe,
        )
        # Only "冥符" appears once — one hit; classifier also has no traction.
        text = (
            "他把冥符收回袖中，转身离开了长街。"
            "雨水沿着屋檐滴落，街上没有几个人。他走过拐角的老槐树，"
            "路过书摊，路过卖甜酒的小车，一直走到城门边才停下。"
        )
        violations = list(HypeOccurrenceCheck().run(text, ctx))
        assert len(violations) == 1
        assert violations[0].code == "HYPE_MISSING"

    def test_classifier_fallback_rescues_missing_keywords(self) -> None:
        """Recipe triggers absent, but the chapter landed the assigned
        HypeType via other FACE_SLAP classifier keywords → pass."""

        recipe = _recipe(
            trigger_keywords=("古怪的A", "古怪的B", "古怪的C"),
        )
        ctx = _ctx(
            assigned_hype_type=HypeType.FACE_SLAP,
            assigned_hype_recipe=recipe,
        )
        # These are FACE_SLAP keywords from _HYPE_KEYWORDS_ZH in hype_engine.
        text = (
            "那群人本来在羞辱他，下一秒被当场打脸，所有人瞬间僵住，"
            "全场鸦雀无声，这一耳光脆响传出老远。"
        )
        assert list(HypeOccurrenceCheck().run(text, ctx)) == []

    def test_scope_scene_is_noop(self) -> None:
        recipe = _recipe()
        ctx = _ctx(
            assigned_hype_type=HypeType.FACE_SLAP,
            assigned_hype_recipe=recipe,
            scope="scene",
        )
        text = "普通场景文本。" * 20
        assert list(HypeOccurrenceCheck().run(text, ctx)) == []


# ---------------------------------------------------------------------------
# HypeDiversityCheck.
# ---------------------------------------------------------------------------


class TestHypeDiversityCheck:
    """Plan §Phase 2: 3 章连 FACE_SLAP → HYPE_REPEAT;
    同 type 不同 recipe → 只告警不阻断 (严肃来说配方差异不会救它)."""

    def test_no_assignment_is_noop(self) -> None:
        ctx = _ctx(
            assigned_hype_type=None,
            recent_hype_types=(HypeType.FACE_SLAP, HypeType.FACE_SLAP),
        )
        assert list(HypeDiversityCheck().run("draft", ctx)) == []

    def test_no_history_is_noop(self) -> None:
        ctx = _ctx(
            assigned_hype_type=HypeType.FACE_SLAP,
            recent_hype_types=(),
        )
        assert list(HypeDiversityCheck().run("draft", ctx)) == []

    def test_one_prior_same_type_is_noop(self) -> None:
        """Only 1 chapter ago — that's 2 in a row, not the 3 we forbid."""
        ctx = _ctx(
            assigned_hype_type=HypeType.FACE_SLAP,
            recent_hype_types=(HypeType.FACE_SLAP,),
        )
        assert list(HypeDiversityCheck().run("draft", ctx)) == []

    def test_two_prior_same_type_fires_repeat(self) -> None:
        """Current + previous 2 all FACE_SLAP → HYPE_REPEAT."""
        ctx = _ctx(
            assigned_hype_type=HypeType.FACE_SLAP,
            recent_hype_types=(HypeType.FACE_SLAP, HypeType.FACE_SLAP),
        )
        violations = list(HypeDiversityCheck().run("draft", ctx))
        assert len(violations) == 1
        v = violations[0]
        assert v.code == "HYPE_REPEAT"
        assert "face_slap" in v.location
        # The feedback mentions alternative types.
        assert "power_reveal" in v.prompt_feedback

    def test_prior_window_with_mixed_types_passes(self) -> None:
        ctx = _ctx(
            assigned_hype_type=HypeType.FACE_SLAP,
            recent_hype_types=(HypeType.FACE_SLAP, HypeType.POWER_REVEAL),
        )
        assert list(HypeDiversityCheck().run("draft", ctx)) == []

    def test_recipe_differences_irrelevant(self) -> None:
        """Same type, different recipe still fires — diversity is type-first.

        Plan reads '同 type 不同 recipe → 只告警不阻断': severity remains
        'block' on the Violation itself (so the gate can downgrade via the
        audit_only mapping in quality_gates.yaml), but the Check still
        reports the repeat. The "只告警" outcome is delivered by config,
        not by the check body.
        """

        ctx = _ctx(
            assigned_hype_type=HypeType.FACE_SLAP,
            recent_hype_types=(HypeType.FACE_SLAP, HypeType.FACE_SLAP),
        )
        violations = list(HypeDiversityCheck().run("draft", ctx))
        assert len(violations) == 1
        assert violations[0].code == "HYPE_REPEAT"


# ---------------------------------------------------------------------------
# EndingSentenceImpactCheck.
# ---------------------------------------------------------------------------


class TestEndingSentenceImpactCheck:
    """Plan §Phase 2: 短句+悬念词通过; 长陈述句+解决悬念触发 WEAK;
    第 2 章 WEAK → blocking_violations 非空 (gate 前三章升 block)."""

    def test_empty_text_is_noop(self) -> None:
        ctx = _ctx(chapter_no=1)
        assert list(EndingSentenceImpactCheck().run("", ctx)) == []

    def test_strong_short_hook_ending_passes(self) -> None:
        ctx = _ctx(chapter_no=5)
        text = "前文。\n\n就在此时，门忽然被推开！"
        assert list(EndingSentenceImpactCheck().run(text, ctx)) == []

    def test_long_resolving_ending_is_weak(self) -> None:
        ctx = _ctx(chapter_no=5)
        text = (
            "他回到家中坐下。\n\n"
            "一切都好，事情到此为止已经没有任何值得担心的地方"
            "全家人齐聚一堂吃完晚饭就去睡觉了圆满收场皆大欢喜。"
        )
        violations = list(EndingSentenceImpactCheck().run(text, ctx))
        assert len(violations) == 1
        assert violations[0].code == "ENDING_SENTENCE_WEAK"
        # Chapter 5 is outside the golden-3 window → warn severity.
        assert violations[0].severity == "warn"

    def test_chapter_two_weak_ending_is_blocking_via_gate(self) -> None:
        """Plan §2: 第 2 章 WEAK → blocking_violations 非空.

        The severity on the Violation is informational ('block'); the
        authoritative blocking signal is write_gate.resolve_mode, which
        promotes ENDING_SENTENCE_WEAK to 'block' for chapters 1-3 even
        though the default config maps it to audit_only.
        """

        ctx = _ctx(chapter_no=2)
        text = "他回到家坐下。\n\n一切都平静下来圆满收场皆大欢喜人人满意家和万事兴。"
        violations = list(EndingSentenceImpactCheck().run(text, ctx))
        assert len(violations) == 1
        report = QualityReport(tuple(violations))
        blocking = filter_blocking(report, chapter_no=2)
        assert len(blocking) == 1
        assert blocking[0].code == "ENDING_SENTENCE_WEAK"

    def test_chapter_four_weak_ending_not_blocking(self) -> None:
        """Chapter 4+ stays audit_only — weak endings log but don't gate."""

        ctx = _ctx(chapter_no=4)
        text = "他回到家坐下。\n\n一切都平静下来圆满收场皆大欢喜人人满意家和万事兴。"
        violations = list(EndingSentenceImpactCheck().run(text, ctx))
        assert len(violations) == 1
        assert violations[0].severity == "warn"
        report = QualityReport(tuple(violations))
        assert filter_blocking(report, chapter_no=4) == ()

    def test_english_strong_hook_passes(self) -> None:
        ctx = _ctx(chapter_no=5, language="en")
        text = "He stepped into the hall.\n\nSuddenly the lights flickered out!"
        assert list(EndingSentenceImpactCheck().run(text, ctx)) == []

    def test_english_resolution_ending_is_weak(self) -> None:
        ctx = _ctx(chapter_no=5, language="en")
        text = (
            "He went home.\n\n"
            "Everything settled and finally at peace he went to bed safely "
            "with a heart that was content beyond measure so he slept well"
        )
        violations = list(EndingSentenceImpactCheck().run(text, ctx))
        assert len(violations) == 1
        assert violations[0].code == "ENDING_SENTENCE_WEAK"

    def test_scope_scene_is_noop(self) -> None:
        ctx = _ctx(chapter_no=1, scope="scene")
        text = "这是场景。\n\n他回到家，一切平静，圆满收场。"
        assert list(EndingSentenceImpactCheck().run(text, ctx)) == []


# ---------------------------------------------------------------------------
# GoldenThreeChapterCheck.
# ---------------------------------------------------------------------------


class TestGoldenThreeChapterCheck:
    """Plan §Phase 2: 诡豪第 1 章无冥符关键词 → WEAK; 第 4 章 WEAK → noop."""

    def test_chapter_four_plus_noop(self) -> None:
        ctx = _ctx(
            scheme=_scheme(),
            chapter_no=4,
            assigned_hype_type=HypeType.FACE_SLAP,
            assigned_hype_recipe=_recipe(),
        )
        text = "普通叙事没有关键词" * 100
        assert list(GoldenThreeChapterCheck().run(text, ctx)) == []

    def test_empty_scheme_noop(self) -> None:
        ctx = _ctx(
            scheme=HypeScheme(),
            chapter_no=1,
            assigned_hype_type=HypeType.FACE_SLAP,
            assigned_hype_recipe=_recipe(),
        )
        text = "普通叙事没有关键词" * 100
        assert list(GoldenThreeChapterCheck().run(text, ctx)) == []

    def test_missing_selling_point_and_triggers_flags(self) -> None:
        ctx = _ctx(
            scheme=_scheme(),
            chapter_no=1,
            assigned_hype_type=HypeType.FACE_SLAP,
            assigned_hype_recipe=_recipe(),
        )
        # No selling_points (诡异复苏/阴阳万亿资产) or hook_keywords (冥符/阴兵/香火)
        # or trigger keywords (冥符/贴脸/僵住) anywhere in text.
        text = "清晨街道上空无一人他走了很久天色渐渐亮起来" * 40
        violations = list(GoldenThreeChapterCheck().run(text, ctx))
        assert len(violations) == 1
        v = violations[0]
        assert v.code == "GOLDEN_THREE_WEAK"
        assert "chapter 1" in v.detail.lower() or "chapter 1" in v.detail
        # Both rule-A and rule-B failures should surface in the detail.
        assert "selling_point" in v.detail or "hook_keyword" in v.detail
        assert "hype trigger" in v.detail

    def test_selling_point_in_head_and_triggers_present_passes(self) -> None:
        ctx = _ctx(
            scheme=_scheme(),
            chapter_no=1,
            assigned_hype_type=HypeType.FACE_SLAP,
            assigned_hype_recipe=_recipe(),
        )
        # "冥符" appears in head and text overall carries triggers.
        head = "冥符在他掌心轻震，"
        body = "他贴脸一甩，对方僵住。" * 3
        text = head + "内容展开" * 200 + body
        assert list(GoldenThreeChapterCheck().run(text, ctx)) == []

    def test_selling_point_absent_but_triggers_present_still_fails(self) -> None:
        """Rule A violation alone is enough to flag the chapter."""
        ctx = _ctx(
            scheme=_scheme(),
            chapter_no=2,
            assigned_hype_type=HypeType.FACE_SLAP,
            assigned_hype_recipe=_recipe(),
        )
        # head_filler uses tokens that share no chars with either
        # selling_points (诡异复苏/阴阳万亿资产) or hook_keywords
        # (冥符/阴兵/香火) or trigger_keywords (冥符/贴脸/僵住).
        head_filler = "平凡早晨他走过老槐树" * 120  # ~1200 chars, no hot words
        tail = "最后他转身。冥符贴脸一甩对方僵住。"
        text = head_filler + tail
        violations = list(GoldenThreeChapterCheck().run(text, ctx))
        assert len(violations) == 1
        assert "selling_point" in violations[0].detail

    def test_selling_point_present_but_triggers_below_threshold_fails(self) -> None:
        """Rule B violation alone is enough — <2 trigger keyword hits."""
        ctx = _ctx(
            scheme=_scheme(),
            chapter_no=1,
            assigned_hype_type=HypeType.FACE_SLAP,
            assigned_hype_recipe=_recipe(
                trigger_keywords=("特殊A", "特殊B", "特殊C"),
            ),
        )
        # selling_point "诡异复苏" in head, but trigger keywords absent.
        text = "诡异复苏的时代到了" + "普通叙事内容" * 200
        violations = list(GoldenThreeChapterCheck().run(text, ctx))
        assert len(violations) == 1
        assert "hype trigger" in violations[0].detail

    def test_hook_keyword_also_satisfies_rule_a(self) -> None:
        """Selling_points OR hook_keywords — either opens the door."""
        ctx = _ctx(
            scheme=_scheme(
                selling_points=(),  # force hook_keywords to carry rule A
            ),
            chapter_no=1,
            assigned_hype_type=HypeType.FACE_SLAP,
            assigned_hype_recipe=_recipe(),
        )
        # hook_keywords = ("冥符", "阴兵", "香火") → "阴兵" in head.
        text = "阴兵列队在他身后" + "叙事继续" * 100 + "冥符贴脸对方僵住。"
        assert list(GoldenThreeChapterCheck().run(text, ctx)) == []

    def test_empty_text_is_noop(self) -> None:
        ctx = _ctx(
            scheme=_scheme(),
            chapter_no=1,
            assigned_hype_type=HypeType.FACE_SLAP,
            assigned_hype_recipe=_recipe(),
        )
        assert list(GoldenThreeChapterCheck().run("", ctx)) == []

    def test_scope_scene_is_noop(self) -> None:
        ctx = _ctx(
            scheme=_scheme(),
            chapter_no=1,
            assigned_hype_type=HypeType.FACE_SLAP,
            assigned_hype_recipe=_recipe(),
            scope="scene",
        )
        text = "普通场景文本。" * 50
        assert list(GoldenThreeChapterCheck().run(text, ctx)) == []
