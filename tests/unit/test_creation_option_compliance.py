"""The compliance checker must catch the real 2026-08-05 violation.

Ground truth is the actual metadata of ``custom-xuanhuan-1785911501``, a book
created with 爽文无代价 that built a cost ledger as its core mechanic. Testing
the detector against invented strings would only prove it matches its own
regexes; these are the strings that actually shipped.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_MODULE_PATH = (
    Path(__file__).resolve().parents[2] / "scripts" / "check_creation_option_compliance.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location("_compliance", _MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["_compliance"] = module
    spec.loader.exec_module(module)
    return module


compliance = _load_module()


# Verbatim from the failed book's project metadata.
_REAL_TAGS = ["玄幻", "嘴炮流", "反英雄", "规则怪谈", "命格师", "影子系", "代价型金手指"]
_REAL_TROPES = ["玄幻", "命格师", "影子系", "成长-代价型金手指", "爽文"]
_REAL_PROFILE = {
    "forbidden": ["系统流无成本喂饭的金手指设定（本作主角必须有代价）"],
    "rule_hardness": (
        "硬规则：偷言-裂纹-窥旧事-命格碎片这条因果链前后必须自洽，"
        "破碗碎=联系断=功法废=主角变回真废物，不许开金手指救"
    ),
    "selling_points": [
        "破碗双向代价博弈：偷得越多碗越脆、与影子联系越薄，"
        "每章都得在『再偷一句』与『碗别碎』之间重新算账"
    ],
}


class TestCatchesTheRealViolation:
    def test_cost_typed_golden_finger_tag_is_flagged(self) -> None:
        assert compliance._scan("tags", _REAL_TAGS)

    def test_trope_keyword_variant_is_flagged(self) -> None:
        """The trope list used a different spelling of the same violation."""

        assert compliance._scan("trope_keywords", _REAL_TROPES)

    def test_writing_profile_cost_rules_are_flagged(self) -> None:
        violations = compliance._scan("writing_profile", _REAL_PROFILE)

        labels = {v.pattern_label for v in violations}
        assert violations, "the writing profile is what reaches the writer prompt"
        assert any("必须有代价" in label or "账本" in label for label in labels)

    def test_violation_reports_where_and_quotes_the_text(self) -> None:
        """An unevidenced finding cannot be acted on."""

        violation = compliance._scan("writing_profile", _REAL_PROFILE)[0]
        assert violation.where.startswith("writing_profile")
        assert violation.excerpt


class TestDoesNotPunishALegitimateBook:
    def test_a_dangerous_world_is_not_a_violation(self) -> None:
        """无代价 constrains the hero's bill, not the setting's stakes.

        If this fired, the fix would be pushing books toward stakeless mush —
        the opposite failure.
        """

        payload = {
            "rule_hardness": "硬规则：宗门械斗致死不受律法追究，越界者当场格杀。",
            "selling_points": ["主角一路碾压，敌人越打越强，局势越来越危险"],
        }
        assert compliance._scan("writing_profile", payload) == []

    def test_costs_borne_by_the_world_are_allowed(self) -> None:
        """external/minimal both permit the world and opponents to pay."""

        payload = {"note": "主角每次出手都会树敌，代价由对手和世界承担"}
        assert compliance._scan("writing_profile", payload) == []

    def test_ordinary_xianxia_vocabulary_is_not_flagged(self) -> None:
        """The 2026-08-02 lesson: do not flatten a genre's vocabulary."""

        payload = {
            "tags": ["血脉觉醒", "废柴逆袭", "升级流", "宗门大比", "灵石", "丹药"],
        }
        assert compliance._scan("tags", payload) == []


class TestNegationIsNotAViolation:
    """Verbatim from custom-xuanhuan-1785952019 (2026-08-06).

    The checker flagged the book's own reader_promise for containing 自损 — in
    the phrase 「借他人一息灵机而不自损」, which asserts precisely the
    compliance it was accused of breaking. A checker that cries wolf on correct
    text gets ignored, and then it is worth nothing when it is right.
    """

    @pytest.mark.parametrize(
        "text",
        [
            "他偶然发现自己能借他人一息灵机而不自损，凭此在宗门试炼中一路逆袭。",
            "此法无反噬，用之不折寿。",
            "这门功法不会反噬施术者。",
        ],
    )
    def test_negated_cost_words_are_not_flagged(self, text: str) -> None:
        assert compliance._scan("writing_profile", {"note": text}) == []

    def test_the_real_violation_is_still_caught_alongside(self) -> None:
        """Negation handling must not blind the checker to actual breaches."""

        real = {
            "升级引擎": "灵机窃取+人情债滚动；每10章解一层境界，但必须先还清上一阶段欠下的债",
            "代表能力": "触碰规则，开始反噬债主",
        }
        assert compliance._scan("commercial_brief", real)


class TestProhibitionListsAreNotViolations:
    """A cost word inside a taboo list is a promise, not a breach.

    2026-08-06: the checker flagged ``writing_profile.style.taboo_words[1] =
    「反噬」`` — the book listing 反噬 as a word it will not use. Reporting that
    as a violation buries the three real ones in the same output.
    """

    def test_taboo_word_list_is_skipped(self) -> None:
        payload = {"style": {"taboo_words": ["反噬", "自损"]}}
        assert compliance._scan("writing_profile", payload) == []

    def test_real_violation_outside_the_taboo_list_still_flagged(self) -> None:
        payload = {
            "style": {"taboo_words": ["反噬"]},
            "character": {"protagonist_archetype": "债主催收链反噬型血脉逆袭者"},
        }
        hits = compliance._scan("writing_profile", payload)
        assert len(hits) == 1
        assert "protagonist_archetype" in hits[0].where


class TestCostStyleReachesMechanicDesign:
    """The stage that designs the power system must know the cost setting.

    2026-08-06: ``book_spec_instruction`` orders a 力量体系 + 升级引擎 with no
    awareness of cost_style, so a 爽文无代价 book came back with repayment as
    its progression engine. Fourth instance of the same family — a creation
    choice that never reaches the stage acting on it.
    """

    def test_commercial_positioning_prompt_carries_the_directive(self) -> None:
        from bestseller.services.conception import _commercial_positioning_user_prompt

        ctx = {
            "genre": "东方玄幻",
            "sub_genre": "东方玄幻",
            "description": "少年借灵机逆袭",
            "chapter_count": 50,
            "recommended_platforms": ["起点"],
            "recommended_audiences": ["男频"],
            "trend_keywords": ["升级"],
            "genre_intent_contract": {
                "explicit_enhancers": {"cost_style": "minimal"},
            },
        }
        prompt = _commercial_positioning_user_prompt(ctx)

        assert "不会削弱主角" in prompt, "力量体系设计阶段必须看得到代价设定"

    def test_standard_books_get_no_extra_block(self) -> None:
        from bestseller.services.conception import _commercial_positioning_user_prompt

        ctx = {
            "genre": "东方玄幻",
            "sub_genre": "东方玄幻",
            "description": "x",
            "chapter_count": 50,
            "recommended_platforms": [],
            "recommended_audiences": [],
            "trend_keywords": [],
            "genre_intent_contract": {"explicit_enhancers": {"cost_style": "standard"}},
        }
        assert "代价风格" not in _commercial_positioning_user_prompt(ctx)

    def test_injected_block_names_no_motif_vocabulary(self) -> None:
        """The injection must not re-seed what the whole fix removed."""

        from bestseller.services.conception import _cost_style_block_for_ctx

        block = _cost_style_block_for_ctx(
            {"genre_intent_contract": {"explicit_enhancers": {"cost_style": "minimal"}}},
            is_en=False,
        )
        for token in ("债", "账", "欠", "寿", "记忆"):
            assert token not in block


class TestReportSemantics:
    def test_standard_books_skip_the_cost_verdict(self) -> None:
        report = compliance.ComplianceReport(slug="x", cost_style="standard")
        report.violations.append(
            compliance.Violation(where="tags", pattern_label="l", excerpt="e")
        )
        assert "跳过代价合规判定" in report.render()

    def test_minimal_book_with_violations_renders_them(self) -> None:
        report = compliance.ComplianceReport(slug="x", cost_style="minimal")
        report.violations.append(
            compliance.Violation(where="tags", pattern_label="代价型金手指", excerpt="e")
        )
        rendered = report.render()
        assert "❌" in rendered and "代价型金手指" in rendered

    def test_clean_minimal_book_passes(self) -> None:
        report = compliance.ComplianceReport(slug="x", cost_style="minimal")
        assert report.passed
        assert "✅" in report.render()


class TestEveryMechanicStageSeesTheCostSetting:
    """The 5th and last instance of "a creation choice never reached the stage".

    2026-08-06: with the motif police fully retired (all detectors are
    False-shims since 2026-08-02) nothing on the output side notices a debt
    engine, so the setting can only be honoured by the stages that *design*
    mechanics actually seeing it. Commercial positioning alone was not enough —
    ``writing_profile.market`` / ``.character`` come from their own LLM calls.
    """

    _CTX = {
        "genre": "东方玄幻",
        "sub_genre": "东方玄幻",
        "description": "少年血脉觉醒逆袭",
        "chapter_count": 50,
        "recommended_platforms": ["起点"],
        "recommended_audiences": ["男频"],
        "trend_keywords": ["升级"],
        "trend_summary": "",
        "trend_score": 80,
        "recommended_tags": [],
        "user_hints": {"audience_orientation": "male"},
        "genre_intent_contract": {"explicit_enhancers": {"cost_style": "minimal"}},
    }

    @pytest.mark.parametrize(
        "builder_name",
        [
            "_commercial_positioning_user_prompt",
            "_market_user_prompt",
            "_character_user_prompt",
            "_world_user_prompt",
        ],
    )
    def test_zh_stage_carries_the_directive(self, builder_name: str) -> None:
        from bestseller.services import conception

        builder = getattr(conception, builder_name)
        prompt = builder(dict(self._CTX))
        assert "不会削弱主角" in prompt, f"{builder_name} 看不到代价设定"

    @pytest.mark.parametrize(
        "builder_name",
        [
            "_market_user_prompt_en",
            "_character_user_prompt_en",
            "_world_user_prompt_en",
        ],
    )
    def test_en_stage_carries_the_directive(self, builder_name: str) -> None:
        from bestseller.services import conception

        builder = getattr(conception, builder_name)
        prompt = builder(dict(self._CTX))
        assert "does not diminish them" in prompt, f"{builder_name} 看不到代价设定"

    def test_standard_books_are_untouched(self) -> None:
        """standard must stay byte-identical — it is every existing book."""

        from bestseller.services import conception

        ctx = dict(self._CTX)
        ctx["genre_intent_contract"] = {"explicit_enhancers": {"cost_style": "standard"}}
        for name in ("_market_user_prompt", "_character_user_prompt", "_world_user_prompt"):
            assert "代价风格" not in getattr(conception, name)(dict(ctx))
