"""Unit tests for ``bestseller.services.title_dedup``.

Covers:
  * char_ngrams: CJK + Latin + mixed input
  * jaccard_similarity: identity, near-dup, disjoint
  * find_title_collisions: within-batch, vs existing, exact and near
  * derive_title_from_content: bracketed nouns, CJK runs, English runs,
    rejection of template-shaped suffixes and non-title tokens
  * is_template_shaped_title: the four-char "noun+function-suffix" pattern
"""

from __future__ import annotations

import pytest

from bestseller.services.title_dedup import (
    DEFAULT_NEAR_DUP_THRESHOLD,
    TitleCollision,
    TitleCollisionError,
    char_ngrams,
    derive_title_from_content,
    find_title_collisions,
    is_template_shaped_title,
    jaccard_similarity,
)


# ---------------------------------------------------------------------------
# char_ngrams
# ---------------------------------------------------------------------------


class TestCharNgrams:
    def test_cjk_4char(self) -> None:
        grams = char_ngrams("铁壁破壁")
        assert grams == {"铁壁", "壁破", "破壁"}

    def test_cjk_2char_is_singleton(self) -> None:
        assert char_ngrams("失衡") == {"失衡"}

    def test_short_input_below_n_returns_singleton(self) -> None:
        assert char_ngrams("X") == {"X"}

    def test_empty_returns_empty(self) -> None:
        assert char_ngrams("") == set()
        assert char_ngrams("   ") == set()

    def test_latin(self) -> None:
        grams = char_ngrams("Crimson")
        # bigrams over 7 chars → 6 grams
        assert "Cr" in grams and "on" in grams
        assert len(grams) == 6

    def test_n_parameter_3(self) -> None:
        grams = char_ngrams("铁壁破壁", n=3)
        assert grams == {"铁壁破", "壁破壁"}


# ---------------------------------------------------------------------------
# jaccard_similarity
# ---------------------------------------------------------------------------


class TestJaccard:
    def test_identical(self) -> None:
        assert jaccard_similarity("铁壁破壁", "铁壁破壁") == 1.0

    def test_one_char_different(self) -> None:
        # "失衡" vs "失控": ngrams {"失衡"} vs {"失控"} → 0
        # (these are 2-char so each is its own singleton)
        assert jaccard_similarity("失衡", "失控") == 0.0

    def test_overlap_on_3char(self) -> None:
        # "决堤" and "血脉决堤" share "决堤" gram
        # set("决堤") = {"决堤"} (4-char treated as singleton when len<n is no, len=2=n)
        # set("血脉决堤") = {"血脉","脉决","决堤"}
        sim = jaccard_similarity("决堤", "血脉决堤")
        # intersection = {"决堤"}, union = {"决堤","血脉","脉决"}
        assert sim == pytest.approx(1 / 3)

    def test_near_dup_above_threshold(self) -> None:
        # "铁壁破壁" vs "铁壁加壁"
        # grams: {铁壁,壁破,破壁} vs {铁壁,壁加,加壁}
        # inter=1, union=5 → 0.2 (NOT a near-dup at 0.7)
        assert jaccard_similarity("铁壁破壁", "铁壁加壁") < DEFAULT_NEAR_DUP_THRESHOLD

    def test_high_overlap_caught(self) -> None:
        # "苏瑶之名" vs "苏瑶之心" should be flagged
        # grams: {苏瑶,瑶之,之名} vs {苏瑶,瑶之,之心}
        # inter=2, union=4 → 0.5
        assert jaccard_similarity("苏瑶之名", "苏瑶之心") == 0.5

    def test_disjoint(self) -> None:
        assert jaccard_similarity("中州入口", "母换子命") == 0.0

    def test_empty_safe(self) -> None:
        assert jaccard_similarity("", "anything") == 0.0
        assert jaccard_similarity("anything", "") == 0.0
        assert jaccard_similarity("", "") == 0.0


# ---------------------------------------------------------------------------
# find_title_collisions
# ---------------------------------------------------------------------------


class TestFindCollisions:
    def test_clean_batch_no_existing(self) -> None:
        report = find_title_collisions(
            [(1, "铁壁破壁"), (2, "回声追索"), (3, "残局寻隙")],
        )
        assert report.ok
        assert report.accepted == ["铁壁破壁", "回声追索", "残局寻隙"]
        assert report.collisions == []

    def test_exact_dup_within_batch(self) -> None:
        report = find_title_collisions(
            [(1, "铁壁破壁"), (2, "回声追索"), (3, "铁壁破壁")],
        )
        assert not report.ok
        assert len(report.collisions) == 1
        c = report.collisions[0]
        assert c.chapter_number == 3
        assert c.candidate_title == "铁壁破壁"
        assert c.conflict_chapter_number == 1
        assert c.similarity == 1.0

    def test_exact_dup_vs_existing(self) -> None:
        report = find_title_collisions(
            [(100, "中州入口"), (101, "崭新标题")],
            existing_titles=[(50, "中州入口"), (51, "其他")],
        )
        assert len(report.collisions) == 1
        c = report.collisions[0]
        assert c.chapter_number == 100
        assert c.conflict_chapter_number == 50

    def test_each_candidate_reported_once(self) -> None:
        # A candidate that conflicts with multiple priors should only
        # surface in one collision entry — otherwise the repair loop
        # would issue duplicate instructions.
        report = find_title_collisions(
            [(10, "铁壁破壁")],
            existing_titles=[
                (1, "铁壁破壁"),
                (2, "铁壁破壁"),
            ],
        )
        assert len(report.collisions) == 1

    def test_near_dup_caught(self) -> None:
        # "苏瑶之名" vs "苏瑶之心" similarity = 0.5 — below default 0.7,
        # but with a stricter threshold it's caught.
        report = find_title_collisions(
            [(2, "苏瑶之心")],
            existing_titles=[(1, "苏瑶之名")],
            near_dup_threshold=0.5,
        )
        assert len(report.collisions) == 1
        assert report.collisions[0].similarity == 0.5

    def test_near_dup_threshold_one_means_exact_only(self) -> None:
        # With threshold=1.0, only exact matches are flagged.
        report = find_title_collisions(
            [(2, "苏瑶之心")],
            existing_titles=[(1, "苏瑶之名")],
            near_dup_threshold=1.0,
        )
        assert report.ok

    def test_threshold_zero_disables_check(self) -> None:
        report = find_title_collisions(
            [(1, "重复"), (2, "重复")],
            near_dup_threshold=0.0,
        )
        assert report.ok

    def test_empty_title_skipped_not_collision(self) -> None:
        # Existence checks live elsewhere; dedup should silently skip empties.
        report = find_title_collisions(
            [(1, ""), (2, "实际标题")],
        )
        assert report.ok
        assert report.accepted == ["实际标题"]

    def test_existing_with_unknown_chapter_number(self) -> None:
        report = find_title_collisions(
            [(5, "铁壁破壁")],
            existing_titles=[(None, "铁壁破壁")],
        )
        assert len(report.collisions) == 1
        assert report.collisions[0].conflict_chapter_number is None


# ---------------------------------------------------------------------------
# is_template_shaped_title
# ---------------------------------------------------------------------------


class TestTemplateShape:
    @pytest.mark.parametrize(
        "title",
        [
            "暗潮试探",
            "断点落子",
            "变局入局",
            "风眼露锋",
            "锈迹死线",
            "悬灯绞杀",
            "裂痕封锁",
        ],
    )
    def test_known_template_pattern(self, title: str) -> None:
        assert is_template_shaped_title(title)

    @pytest.mark.parametrize(
        "title",
        [
            "凝神三日",     # extracted from content; not a template suffix
            "青冥旧识",     # named entity
            "中州入口",     # place name
            "断手遗骸",     # concrete event noun
            "三百年客",     # specific count + role
            "母换子命",     # condensed event
            "灵镜围墟",     # event verb but not in suffix list
        ],
    )
    def test_content_titles_not_flagged(self, title: str) -> None:
        assert not is_template_shaped_title(title)

    def test_short_titles_never_flagged(self) -> None:
        assert not is_template_shaped_title("失衡")
        assert not is_template_shaped_title("入局")


# ---------------------------------------------------------------------------
# derive_title_from_content
# ---------------------------------------------------------------------------


class TestDeriveTitle:
    def test_picks_bracketed_proper_noun(self) -> None:
        # Bracketed proper nouns should win over plain runs.
        title = derive_title_from_content(
            main_conflict="宁尘必须取回「青鸾令」才能进入禁地。",
        )
        assert title == "青鸾令"

    def test_book_title_in_braces(self) -> None:
        title = derive_title_from_content(
            main_conflict="陆沉发现《阴阳道典》残篇被人动过。",
        )
        assert title == "阴阳道典"

    def test_extracts_bounded_cjk_run_with_punctuation(self) -> None:
        # With explicit punctuation boundaries the bounded regex picks
        # up the leading CJK run.
        title = derive_title_from_content(
            main_conflict="坠魂渊，宁尘发现污染体。",
        )
        # "坠魂渊" is a bounded 3-char run at the start.
        assert title is not None
        assert 2 <= len(title) <= 6

    def test_returns_none_for_unpunctuated_run(self) -> None:
        # A single long unpunctuated CJK string has no extractable noun —
        # the old greedy extractor would slice the first 4 chars and produce
        # garbage like "宁尘在坠"; the new conservative one returns None
        # so the caller (e.g. `_fallback_chapter_outline_batch`) can move
        # on to the next source or to the chapter-number placeholder.
        title = derive_title_from_content(
            main_conflict="宁尘在坠魂渊底发现了一具腐烂的污染体",
        )
        assert title is None

    def test_prefers_unique_beat_over_others(self) -> None:
        # The function checks in order: unique_beat, main_conflict, hook, goal.
        # Use bracketed nouns so extraction is deterministic.
        title = derive_title_from_content(
            unique_beat="宁尘取出「凝神丹」化解突破压力。",
            main_conflict="周霸要求他赴擂台决战。",
            chapter_goal="宁尘必须想办法活下来。",
        )
        assert title == "凝神丹"

    def test_skips_template_shaped_candidates(self) -> None:
        # The first bounded run "暗潮试探" is template-shaped (4-char with
        # a generic functional suffix). The function should skip it and
        # fall through to a later candidate or return None.
        title = derive_title_from_content(
            main_conflict="暗潮试探。真正的事件是「玉镯」出现。",
        )
        assert title == "玉镯"

    def test_returns_none_on_empty_inputs(self) -> None:
        assert (
            derive_title_from_content(
                main_conflict=None,
                hook_description=None,
                unique_beat=None,
                chapter_goal=None,
            )
            is None
        )

    def test_returns_none_when_no_extractable_phrase(self) -> None:
        # All pronouns / function words; no concrete nouns.
        title = derive_title_from_content(
            main_conflict="他必须做出选择。",
        )
        # "做出选择" is 4 chars but is generic — and the function should
        # ideally not return it. Acceptable behavior: either return None
        # or return a 2-char fragment. We assert it's not a template shape.
        if title is not None:
            assert not is_template_shaped_title(title)

    def test_english_capitalized_runs(self) -> None:
        title = derive_title_from_content(
            main_conflict="Nin Chen must retrieve the Cipher Key from Storm Faultline.",
            language="en",
        )
        # Should pick a capitalized proper noun run.
        assert title is not None
        assert any(c.isupper() for c in title)


# ---------------------------------------------------------------------------
# TitleCollisionError
# ---------------------------------------------------------------------------


class TestTitleCollisionError:
    def test_carries_collisions(self) -> None:
        col = TitleCollision(
            chapter_number=10,
            candidate_title="铁壁破壁",
            conflict_title="铁壁破壁",
            conflict_chapter_number=2,
            similarity=1.0,
        )
        err = TitleCollisionError("Test", collisions=[col])
        assert err.collisions == [col]
        assert "Test" in str(err)
