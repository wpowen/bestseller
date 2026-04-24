"""Regression tests for DiversityBudget title-pattern cooldown.

Covers:
 * extract_title_ngrams — correct 2-3 char CJK n-gram extraction
 * register_title — n-grams persisted with correct chapter numbers
 * title_pattern_cooldown_violations — correct windowing logic
 * to_dict / from_dict round-trip for title_patterns
 * register_chapter passes chapter_no through to register_title
"""

from __future__ import annotations

import uuid

import pytest

from bestseller.services.diversity_budget import (
    DEFAULT_TITLE_COOLDOWN_CHAPTERS,
    DiversityBudget,
    extract_title_ngrams,
)


_PROJECT_ID = uuid.uuid4()


# ---------------------------------------------------------------------------
# extract_title_ngrams
# ---------------------------------------------------------------------------


class TestExtractTitleNgrams:
    def test_basic_2gram(self) -> None:
        grams = extract_title_ngrams("决堤")
        assert "决堤" in grams

    def test_basic_3gram(self) -> None:
        grams = extract_title_ngrams("血脉决堤")
        assert "血脉决" in grams
        assert "脉决堤" in grams

    def test_2gram_from_4char(self) -> None:
        grams = extract_title_ngrams("血脉决堤")
        assert "血脉" in grams
        assert "脉决" in grams
        assert "决堤" in grams

    def test_no_duplicates(self) -> None:
        grams = extract_title_ngrams("决堤决堤")
        assert len(grams) == len(set(grams))

    def test_empty_string(self) -> None:
        assert extract_title_ngrams("") == ()

    def test_ascii_only_returns_empty(self) -> None:
        assert extract_title_ngrams("Storm Rising") == ()

    def test_single_char_returns_empty(self) -> None:
        assert extract_title_ngrams("决") == ()

    def test_mixed_ascii_and_cjk(self) -> None:
        grams = extract_title_ngrams("第1章·决堤")
        assert "决堤" in grams
        # ASCII characters should not appear in grams
        for g in grams:
            assert all("\u4e00" <= c <= "\u9fff" for c in g)

    def test_punctuation_splits_runs(self) -> None:
        # "决堤" and "破虚" are separate runs — no cross-run n-gram
        grams = extract_title_ngrams("决堤·破虚")
        assert "堤破" not in grams  # cross-boundary gram should not exist
        assert "决堤" in grams
        assert "破虚" in grams


# ---------------------------------------------------------------------------
# register_title + title_patterns update
# ---------------------------------------------------------------------------


class TestRegisterTitle:
    def test_title_appended_to_titles_used(self) -> None:
        budget = DiversityBudget(project_id=_PROJECT_ID)
        budget.register_title("血脉决堤", chapter_no=10)
        assert "血脉决堤" in budget.titles_used

    def test_ngrams_recorded_with_chapter(self) -> None:
        budget = DiversityBudget(project_id=_PROJECT_ID)
        budget.register_title("血脉决堤", chapter_no=10)
        assert budget.title_patterns.get("决堤") == 10
        assert budget.title_patterns.get("血脉") == 10

    def test_later_chapter_overwrites_earlier(self) -> None:
        budget = DiversityBudget(project_id=_PROJECT_ID)
        budget.register_title("决堤", chapter_no=5)
        budget.register_title("风暴决堤", chapter_no=20)
        assert budget.title_patterns["决堤"] == 20

    def test_zero_chapter_no_skips_ngram_update(self) -> None:
        budget = DiversityBudget(project_id=_PROJECT_ID)
        budget.register_title("决堤", chapter_no=0)
        assert "决堤" not in budget.title_patterns
        assert "决堤" in budget.titles_used

    def test_no_chapter_arg_skips_ngram_update(self) -> None:
        budget = DiversityBudget(project_id=_PROJECT_ID)
        budget.register_title("决堤")
        assert not budget.title_patterns
        assert "决堤" in budget.titles_used

    def test_empty_title_is_ignored(self) -> None:
        budget = DiversityBudget(project_id=_PROJECT_ID)
        budget.register_title("", chapter_no=5)
        assert not budget.titles_used
        assert not budget.title_patterns


# ---------------------------------------------------------------------------
# title_pattern_cooldown_violations
# ---------------------------------------------------------------------------


class TestTitlePatternCooldownViolations:
    def _budget_with_title(
        self, title: str, chapter_no: int
    ) -> DiversityBudget:
        b = DiversityBudget(project_id=_PROJECT_ID)
        b.register_title(title, chapter_no=chapter_no)
        return b

    def test_same_pattern_within_cooldown_fires(self) -> None:
        budget = self._budget_with_title("决堤", chapter_no=100)
        violations = budget.title_pattern_cooldown_violations(
            "灵脉决堤", current_chapter=150, cooldown_chapters=75
        )
        assert "决堤" in violations

    def test_same_pattern_outside_cooldown_passes(self) -> None:
        budget = self._budget_with_title("决堤", chapter_no=10)
        violations = budget.title_pattern_cooldown_violations(
            "灵脉决堤", current_chapter=200, cooldown_chapters=75
        )
        assert violations == []

    def test_no_overlap_passes(self) -> None:
        budget = self._budget_with_title("血脉决堤", chapter_no=100)
        violations = budget.title_pattern_cooldown_violations(
            "青云破晓", current_chapter=150, cooldown_chapters=75
        )
        assert violations == []

    def test_empty_budget_always_passes(self) -> None:
        budget = DiversityBudget(project_id=_PROJECT_ID)
        violations = budget.title_pattern_cooldown_violations(
            "决堤破虚", current_chapter=50, cooldown_chapters=75
        )
        assert violations == []

    def test_empty_candidate_passes(self) -> None:
        budget = self._budget_with_title("决堤", chapter_no=100)
        assert budget.title_pattern_cooldown_violations("", 150) == []

    def test_boundary_exactly_at_threshold(self) -> None:
        # last_seen == current_chapter - cooldown → still within window
        budget = self._budget_with_title("决堤", chapter_no=75)
        violations = budget.title_pattern_cooldown_violations(
            "灵脉决堤", current_chapter=150, cooldown_chapters=75
        )
        assert "决堤" in violations

    def test_boundary_one_past_threshold(self) -> None:
        # last_seen == current_chapter - cooldown - 1 → just outside
        budget = self._budget_with_title("决堤", chapter_no=74)
        violations = budget.title_pattern_cooldown_violations(
            "灵脉决堤", current_chapter=150, cooldown_chapters=75
        )
        assert violations == []

    def test_default_cooldown_used(self) -> None:
        budget = self._budget_with_title("决堤", chapter_no=100)
        violations = budget.title_pattern_cooldown_violations(
            "决堤再起",
            current_chapter=100 + DEFAULT_TITLE_COOLDOWN_CHAPTERS - 1,
        )
        assert "决堤" in violations

    def test_consecutive_same_pattern_both_fire(self) -> None:
        budget = DiversityBudget(project_id=_PROJECT_ID)
        budget.register_title("血脉决堤", chapter_no=100)
        budget.register_title("灵脉决堤", chapter_no=120)
        violations = budget.title_pattern_cooldown_violations(
            "道心决堤", current_chapter=150, cooldown_chapters=75
        )
        assert "决堤" in violations


# ---------------------------------------------------------------------------
# Serialization round-trip
# ---------------------------------------------------------------------------


class TestTitlePatternsRoundTrip:
    def test_to_dict_includes_title_patterns(self) -> None:
        budget = DiversityBudget(project_id=_PROJECT_ID)
        budget.register_title("决堤破虚", chapter_no=42)
        data = budget.to_dict()
        assert "title_patterns" in data
        assert data["title_patterns"].get("决堤") == 42

    def test_from_dict_restores_title_patterns(self) -> None:
        budget = DiversityBudget(project_id=_PROJECT_ID)
        budget.register_title("决堤破虚", chapter_no=42)
        restored = DiversityBudget.from_dict(_PROJECT_ID, budget.to_dict())
        assert restored.title_patterns.get("决堤") == 42
        assert restored.title_patterns.get("破虚") == 42

    def test_from_dict_handles_missing_title_patterns(self) -> None:
        data = {
            "openings_used": [],
            "cliffhangers_used": [],
            "titles_used": ["决堤"],
            "vocab_freq": {},
            "hype_moments": [],
            # no title_patterns key (legacy)
        }
        restored = DiversityBudget.from_dict(_PROJECT_ID, data)
        assert restored.title_patterns == {}

    def test_from_dict_ignores_malformed_values(self) -> None:
        data = {
            "openings_used": [],
            "cliffhangers_used": [],
            "titles_used": [],
            "vocab_freq": {},
            "hype_moments": [],
            "title_patterns": {"决堤": "not_an_int", "破虚": 10},
        }
        restored = DiversityBudget.from_dict(_PROJECT_ID, data)
        assert "决堤" not in restored.title_patterns
        assert restored.title_patterns.get("破虚") == 10


# ---------------------------------------------------------------------------
# register_chapter passes chapter_no through
# ---------------------------------------------------------------------------


class TestRegisterChapterPassesChapterNo:
    def test_register_chapter_with_title_updates_patterns(self) -> None:
        budget = DiversityBudget(project_id=_PROJECT_ID)
        budget.register_chapter(99, title="血脉决堤")
        assert budget.title_patterns.get("决堤") == 99

    def test_register_chapter_without_title_leaves_patterns_empty(self) -> None:
        budget = DiversityBudget(project_id=_PROJECT_ID)
        budget.register_chapter(99)
        assert budget.title_patterns == {}
