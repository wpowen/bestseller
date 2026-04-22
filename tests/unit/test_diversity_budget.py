"""Unit tests for DiversityBudget (bugs 5/7/10).

DB repository helpers are covered in integration tests; here we exercise
the pure rotation logic and JSONB round-trip.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from bestseller.services.diversity_budget import (
    DEFAULT_HOT_VOCAB_MIN_COUNT,
    DEFAULT_HOT_VOCAB_TOP_N,
    DEFAULT_HOT_VOCAB_WINDOW,
    VOCAB_HISTORY_MAX_CHAPTERS,
    CliffhangerUse,
    DiversityBudget,
    OpeningUse,
    extract_tokens,
    render_budget_diversity_block,
)
from bestseller.services.hype_engine import (
    HYPE_DENSITY_CURVE,
    HypeMoment,
    HypeRecipe,
    HypeType,
)
from bestseller.services.invariants import (
    CliffhangerPolicy,
    CliffhangerType,
    OpeningArchetype,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# extract_tokens.
# ---------------------------------------------------------------------------


class TestExtractTokens:
    def test_english_lowercases_and_strips_stopwords(self) -> None:
        tokens = extract_tokens("The shard SHATTERED the stone.", "en")
        assert "shard" in tokens
        assert "shattered" in tokens
        assert "stone" in tokens
        # stopwords removed
        assert "the" not in tokens

    def test_english_keeps_hyphenated_words(self) -> None:
        tokens = extract_tokens("She used a half-life potion.", "en")
        assert "half-life" in tokens

    def test_chinese_emits_bigrams(self) -> None:
        tokens = extract_tokens("林晓晓握住长剑直指敌人", "zh-CN")
        assert any("林晓" in t or "林晓晓" in t for t in tokens)
        # English punctuation/latin within Chinese text is ignored.
        tokens_mixed = extract_tokens("林晓 X said hi 长剑", "zh-CN")
        assert any(t.startswith("林") for t in tokens_mixed)

    def test_none_language_defaults_to_chinese(self) -> None:
        tokens = extract_tokens("长剑直指", None)
        assert tokens  # some CJK ngram recognized

    def test_empty_text_returns_empty(self) -> None:
        assert extract_tokens("", "en") == []
        assert extract_tokens("", "zh-CN") == []


# ---------------------------------------------------------------------------
# next_opening.
# ---------------------------------------------------------------------------


class TestNextOpening:
    def test_empty_budget_returns_first_pool_member(self) -> None:
        budget = DiversityBudget(project_id=uuid4())
        pick = budget.next_opening(
            pool=[OpeningArchetype.HUMILIATION, OpeningArchetype.CRISIS]
        )
        assert pick == OpeningArchetype.HUMILIATION

    def test_avoids_recent_openings_within_window(self) -> None:
        budget = DiversityBudget(project_id=uuid4())
        budget.register_opening(1, OpeningArchetype.HUMILIATION)
        budget.register_opening(2, OpeningArchetype.CRISIS)
        pool = [
            OpeningArchetype.HUMILIATION,
            OpeningArchetype.CRISIS,
            OpeningArchetype.ENCOUNTER,
        ]
        pick = budget.next_opening(pool=pool, no_repeat_within=3)
        assert pick == OpeningArchetype.ENCOUNTER

    def test_window_exhausted_falls_back_to_lru(self) -> None:
        budget = DiversityBudget(project_id=uuid4())
        # Use both pool members many times; oldest usage is HUMILIATION.
        budget.register_opening(1, OpeningArchetype.HUMILIATION)
        budget.register_opening(2, OpeningArchetype.CRISIS)
        budget.register_opening(3, OpeningArchetype.CRISIS)
        pool = [OpeningArchetype.HUMILIATION, OpeningArchetype.CRISIS]
        pick = budget.next_opening(pool=pool, no_repeat_within=5)
        assert pick == OpeningArchetype.HUMILIATION  # less recently used

    def test_empty_pool_raises(self) -> None:
        budget = DiversityBudget(project_id=uuid4())
        with pytest.raises(ValueError):
            budget.next_opening(pool=[])

    def test_defaults_to_full_enum_pool(self) -> None:
        budget = DiversityBudget(project_id=uuid4())
        pick = budget.next_opening()
        assert pick in tuple(OpeningArchetype)


# ---------------------------------------------------------------------------
# next_cliffhanger.
# ---------------------------------------------------------------------------


class TestNextCliffhanger:
    def test_default_policy_picks_first_unused(self) -> None:
        budget = DiversityBudget(project_id=uuid4())
        budget.register_cliffhanger(1, CliffhangerType.REVELATION)
        pick = budget.next_cliffhanger()
        assert pick != CliffhangerType.REVELATION
        assert pick in tuple(CliffhangerType)

    def test_respects_allowed_types(self) -> None:
        policy = CliffhangerPolicy(
            no_repeat_within=3,
            allowed_types=(
                CliffhangerType.DECISION,
                CliffhangerType.POWER_SHIFT,
            ),
        )
        budget = DiversityBudget(project_id=uuid4())
        budget.register_cliffhanger(1, CliffhangerType.DECISION)
        pick = budget.next_cliffhanger(policy)
        assert pick == CliffhangerType.POWER_SHIFT

    def test_exhausted_window_with_fallback_false_raises(self) -> None:
        policy = CliffhangerPolicy(
            no_repeat_within=5,
            allowed_types=(CliffhangerType.REVELATION,),
        )
        budget = DiversityBudget(project_id=uuid4())
        budget.register_cliffhanger(1, CliffhangerType.REVELATION)
        with pytest.raises(ValueError):
            budget.next_cliffhanger(policy, fallback=False)

    def test_exhausted_window_with_fallback_true_returns_lru(self) -> None:
        policy = CliffhangerPolicy(
            no_repeat_within=10,
            allowed_types=(
                CliffhangerType.REVELATION,
                CliffhangerType.DECISION,
            ),
        )
        budget = DiversityBudget(project_id=uuid4())
        budget.register_cliffhanger(1, CliffhangerType.REVELATION)
        budget.register_cliffhanger(2, CliffhangerType.DECISION)
        budget.register_cliffhanger(3, CliffhangerType.DECISION)
        pick = budget.next_cliffhanger(policy)
        assert pick == CliffhangerType.REVELATION


# ---------------------------------------------------------------------------
# hot_vocab.
# ---------------------------------------------------------------------------


class TestHotVocab:
    def test_empty_budget_returns_empty(self) -> None:
        budget = DiversityBudget(project_id=uuid4())
        assert budget.hot_vocab() == ()

    def test_top_n_in_last_window_of_chapters(self) -> None:
        budget = DiversityBudget(project_id=uuid4())
        # ch1-3: shard repeated 6 times total across window of last 5
        budget.register_vocab(1, "shard shard shard the stone", "en")
        budget.register_vocab(2, "shard shard the stone", "en")
        budget.register_vocab(3, "shard the sky", "en")
        hot = budget.hot_vocab(window=5, top=5, min_count=3)
        assert "shard" in hot

    def test_min_count_filters_rare_tokens(self) -> None:
        budget = DiversityBudget(project_id=uuid4())
        budget.register_vocab(1, "rare word", "en")
        hot = budget.hot_vocab(window=5, top=20, min_count=3)
        assert hot == ()  # below min_count

    def test_window_slides(self) -> None:
        budget = DiversityBudget(project_id=uuid4())
        # older chapters dominate, but window only looks at last 2
        budget.register_vocab(1, "old " * 10, "en")
        budget.register_vocab(2, "new " * 5, "en")
        budget.register_vocab(3, "new " * 5, "en")
        hot = budget.hot_vocab(window=2, top=5, min_count=3)
        assert "new" in hot
        assert "old" not in hot

    def test_vocab_history_is_pruned_to_cap(self) -> None:
        budget = DiversityBudget(project_id=uuid4())
        for ch in range(1, VOCAB_HISTORY_MAX_CHAPTERS + 5):
            budget.register_vocab(ch, f"chapter{ch}_token " * 5, "en")
        assert len(budget.vocab_freq) == VOCAB_HISTORY_MAX_CHAPTERS
        # Earliest chapters dropped.
        assert "1" not in budget.vocab_freq
        assert str(VOCAB_HISTORY_MAX_CHAPTERS + 4) in budget.vocab_freq

    def test_default_constants_sane(self) -> None:
        assert DEFAULT_HOT_VOCAB_WINDOW == 5
        assert DEFAULT_HOT_VOCAB_TOP_N == 20
        assert DEFAULT_HOT_VOCAB_MIN_COUNT == 3


# ---------------------------------------------------------------------------
# register_chapter.
# ---------------------------------------------------------------------------


class TestRegisterChapter:
    def test_registers_all_provided_fields(self) -> None:
        budget = DiversityBudget(project_id=uuid4())
        budget.register_chapter(
            1,
            opening=OpeningArchetype.HUMILIATION,
            cliffhanger=CliffhangerType.REVELATION,
            title="Chapter One",
            text="shard shard shard the stone",
            language="en",
        )
        assert budget.openings_used == [
            OpeningUse(1, OpeningArchetype.HUMILIATION)
        ]
        assert budget.cliffhangers_used == [
            CliffhangerUse(1, CliffhangerType.REVELATION)
        ]
        assert budget.titles_used == ["Chapter One"]
        assert "shard" in budget.vocab_freq["1"]

    def test_none_fields_skipped(self) -> None:
        budget = DiversityBudget(project_id=uuid4())
        budget.register_chapter(1)
        assert budget.openings_used == []
        assert budget.cliffhangers_used == []
        assert budget.titles_used == []
        assert budget.vocab_freq == {}

    def test_empty_title_rejected(self) -> None:
        budget = DiversityBudget(project_id=uuid4())
        budget.register_chapter(1, title="   ")
        assert budget.titles_used == []


# ---------------------------------------------------------------------------
# Serialization.
# ---------------------------------------------------------------------------


class TestSerialization:
    def test_round_trip_preserves_state(self) -> None:
        pid = uuid4()
        original = DiversityBudget(project_id=pid)
        original.register_chapter(
            1,
            opening=OpeningArchetype.HUMILIATION,
            cliffhanger=CliffhangerType.REVELATION,
            title="Chapter One",
            text="shard shard stone",
            language="en",
        )
        original.register_chapter(
            2,
            opening=OpeningArchetype.CRISIS,
            cliffhanger=CliffhangerType.DECISION,
            title="Chapter Two",
            text="blade blade stone",
            language="en",
        )
        data = original.to_dict()
        restored = DiversityBudget.from_dict(pid, data)

        assert restored.project_id == pid
        assert restored.openings_used == original.openings_used
        assert restored.cliffhangers_used == original.cliffhangers_used
        assert restored.titles_used == original.titles_used
        assert restored.vocab_freq == original.vocab_freq

    def test_from_dict_handles_none(self) -> None:
        pid = uuid4()
        restored = DiversityBudget.from_dict(pid, None)
        assert restored.project_id == pid
        assert restored.openings_used == []
        assert restored.cliffhangers_used == []
        assert restored.titles_used == []
        assert restored.vocab_freq == {}

    def test_from_dict_drops_malformed_enum_values(self) -> None:
        pid = uuid4()
        data = {
            "openings_used": [
                {"chapter_no": 1, "archetype": "humiliation"},
                {"chapter_no": 2, "archetype": "not-a-real-archetype"},
            ],
            "cliffhangers_used": [
                {"chapter_no": 1, "kind": "revelation"},
                {"chapter_no": 2, "kind": "bogus"},
            ],
        }
        restored = DiversityBudget.from_dict(pid, data)
        assert restored.openings_used == [
            OpeningUse(1, OpeningArchetype.HUMILIATION)
        ]
        assert restored.cliffhangers_used == [
            CliffhangerUse(1, CliffhangerType.REVELATION)
        ]

    def test_from_dict_drops_malformed_vocab_counts(self) -> None:
        pid = uuid4()
        data = {
            "vocab_freq": {
                "1": {"good": 5, "bad": "not-a-number"},
                "2": "not-a-dict",
            }
        }
        restored = DiversityBudget.from_dict(pid, data)
        assert restored.vocab_freq == {"1": {"good": 5}}


# ---------------------------------------------------------------------------
# recent_* accessors.
# ---------------------------------------------------------------------------


class TestRecentAccessors:
    def test_recent_openings_window(self) -> None:
        budget = DiversityBudget(project_id=uuid4())
        for i, a in enumerate([
            OpeningArchetype.HUMILIATION,
            OpeningArchetype.CRISIS,
            OpeningArchetype.ENCOUNTER,
            OpeningArchetype.CONTRAST,
        ], start=1):
            budget.register_opening(i, a)
        assert budget.recent_openings(2) == (
            OpeningArchetype.ENCOUNTER,
            OpeningArchetype.CONTRAST,
        )
        assert budget.recent_openings(0) == ()

    def test_recent_cliffhangers_window(self) -> None:
        budget = DiversityBudget(project_id=uuid4())
        for i, k in enumerate([
            CliffhangerType.REVELATION,
            CliffhangerType.DECISION,
            CliffhangerType.BODY_REACTION,
        ], start=1):
            budget.register_cliffhanger(i, k)
        assert budget.recent_cliffhangers(2) == (
            CliffhangerType.DECISION,
            CliffhangerType.BODY_REACTION,
        )


# ---------------------------------------------------------------------------
# Hype moments — Phase 1 budget extension.
# ---------------------------------------------------------------------------


class TestHypeMoments:
    def test_register_hype_moment_appends(self) -> None:
        budget = DiversityBudget(project_id=uuid4())
        budget.register_hype_moment(1, HypeType.FACE_SLAP, "冥符拍脸", 8.5)
        budget.register_hype_moment(2, HypeType.POWER_REVEAL, "阴兵列阵", 9.0)
        assert len(budget.hype_moments) == 2
        assert budget.hype_moments[0].hype_type is HypeType.FACE_SLAP
        assert budget.hype_moments[1].recipe_key == "阴兵列阵"
        assert budget.hype_moments[1].intensity == 9.0

    def test_recent_hype_types_respects_window(self) -> None:
        budget = DiversityBudget(project_id=uuid4())
        for i, ht in enumerate(
            [
                HypeType.FACE_SLAP,
                HypeType.POWER_REVEAL,
                HypeType.COMEDIC_BEAT,
                HypeType.DOMINATION,
            ],
            start=1,
        ):
            budget.register_hype_moment(i, ht, f"r{i}", 7.0)
        assert budget.recent_hype_types(2) == (
            HypeType.COMEDIC_BEAT,
            HypeType.DOMINATION,
        )
        assert budget.recent_hype_types(0) == ()

    def test_recent_recipe_keys_skips_none(self) -> None:
        budget = DiversityBudget(project_id=uuid4())
        budget.register_hype_moment(1, HypeType.FACE_SLAP, "key_a", 7.0)
        budget.register_hype_moment(2, HypeType.POWER_REVEAL, None, 7.0)
        budget.register_hype_moment(3, HypeType.LEVEL_UP, "key_c", 7.0)
        assert budget.recent_recipe_keys(5) == ("key_a", "key_c")

    def test_register_chapter_accepts_hype_kwargs(self) -> None:
        budget = DiversityBudget(project_id=uuid4())
        budget.register_chapter(
            1,
            opening=OpeningArchetype.HUMILIATION,
            hype_type=HypeType.FACE_SLAP,
            hype_recipe_key="冥符拍脸",
            hype_intensity=8.5,
        )
        assert budget.openings_used[0].archetype is OpeningArchetype.HUMILIATION
        assert budget.hype_moments[0].hype_type is HypeType.FACE_SLAP
        assert budget.hype_moments[0].intensity == 8.5

    def test_round_trip_preserves_hype_moments(self) -> None:
        pid = uuid4()
        original = DiversityBudget(project_id=pid)
        original.register_hype_moment(1, HypeType.FACE_SLAP, "冥符拍脸", 8.0)
        original.register_hype_moment(2, HypeType.COMEDIC_BEAT, None, 5.5)
        data = original.to_dict()
        restored = DiversityBudget.from_dict(pid, data)
        assert restored.hype_moments == original.hype_moments

    def test_from_dict_drops_malformed_hype_rows(self) -> None:
        pid = uuid4()
        data = {
            "hype_moments": [
                {"chapter_no": 1, "hype_type": "face_slap",
                 "recipe_key": "a", "intensity": 8.0},
                {"chapter_no": 2, "hype_type": "not-a-real-type",
                 "recipe_key": "b", "intensity": 7.0},
                "nonsense-row",
            ],
        }
        restored = DiversityBudget.from_dict(pid, data)
        assert restored.hype_moments == [
            HypeMoment(1, HypeType.FACE_SLAP, "a", 8.0),
        ]

    def test_next_hype_with_empty_deck_returns_none(self) -> None:
        budget = DiversityBudget(project_id=uuid4())
        band = HYPE_DENSITY_CURVE[0]
        assert budget.next_hype((), band) is None

    def test_next_hype_prefers_expected_type_over_off_type(self) -> None:
        budget = DiversityBudget(project_id=uuid4())
        band = HYPE_DENSITY_CURVE[0]  # expects FACE_SLAP / POWER_REVEAL / GF
        deck = (
            HypeRecipe(
                key="fun_quip",
                hype_type=HypeType.COMEDIC_BEAT,
                trigger_keywords=("吐槽",),
                narrative_beats=("吐槽",),
            ),
            HypeRecipe(
                key="face_slap_1",
                hype_type=HypeType.FACE_SLAP,
                trigger_keywords=("打脸",),
                narrative_beats=("打脸",),
            ),
        )
        chosen = budget.next_hype(deck, band)
        assert chosen is not None
        assert chosen.key == "face_slap_1"

    def test_next_hype_lru_after_recent_use(self) -> None:
        budget = DiversityBudget(project_id=uuid4())
        band = HYPE_DENSITY_CURVE[0]
        deck = (
            HypeRecipe(
                key="face_slap_1",
                hype_type=HypeType.FACE_SLAP,
                trigger_keywords=("打脸",),
                narrative_beats=("打脸",),
            ),
            HypeRecipe(
                key="power_reveal_1",
                hype_type=HypeType.POWER_REVEAL,
                trigger_keywords=("列阵",),
                narrative_beats=("列阵",),
            ),
        )
        # Use face_slap_1 most recently — the selector should prefer the
        # un-used power_reveal_1.
        budget.register_hype_moment(1, HypeType.FACE_SLAP, "face_slap_1", 8.0)
        chosen = budget.next_hype(deck, band)
        assert chosen is not None
        assert chosen.key == "power_reveal_1"


# ---------------------------------------------------------------------------
# render_budget_diversity_block — prompt-block rendering for L3.
# ---------------------------------------------------------------------------


class TestRenderBudgetDiversityBlock:
    def test_none_budget_returns_none(self) -> None:
        assert (
            render_budget_diversity_block(None, language="zh-CN")
            is None
        )

    def test_empty_budget_returns_none(self) -> None:
        budget = DiversityBudget(project_id=uuid4())
        # No hot vocab, no openings, no cliffhangers → nothing to say.
        assert (
            render_budget_diversity_block(budget, language="zh-CN")
            is None
        )

    def test_english_block_surfaces_hot_vocab(self) -> None:
        budget = DiversityBudget(project_id=uuid4())
        # Repeat "shard" enough to clear the min_count filter across 2 chapters.
        budget.register_vocab(1, " ".join(["shard"] * 5), "en")
        budget.register_vocab(2, " ".join(["shard"] * 4), "en")
        block = render_budget_diversity_block(
            budget, language="en", vocab_min_count=3
        )
        assert block is not None
        assert "DIVERSITY BUDGET" in block
        assert "shard" in block

    def test_chinese_block_uses_chinese_headings(self) -> None:
        budget = DiversityBudget(project_id=uuid4())
        # Chinese bigrams fire for 2+ CJK chars.
        budget.register_vocab(1, "龙魂" * 6, "zh-CN")
        budget.register_vocab(2, "龙魂" * 5, "zh-CN")
        block = render_budget_diversity_block(
            budget, language="zh-CN", vocab_min_count=3
        )
        assert block is not None
        assert "多样性预算" in block
        assert "龙魂" in block

    def test_opening_archetypes_only_shown_for_chapter_opener(self) -> None:
        budget = DiversityBudget(project_id=uuid4())
        budget.register_opening(1, OpeningArchetype.HUMILIATION)
        budget.register_opening(2, OpeningArchetype.CRISIS)

        opener = render_budget_diversity_block(
            budget, language="en", is_chapter_opener=True
        )
        non_opener = render_budget_diversity_block(
            budget, language="en", is_chapter_opener=False
        )
        assert opener is not None and "humiliation" in opener
        # Without openings and no vocab / cliffhangers → nothing to render.
        assert non_opener is None

    def test_cliffhangers_only_shown_for_chapter_closer(self) -> None:
        budget = DiversityBudget(project_id=uuid4())
        budget.register_cliffhanger(1, CliffhangerType.REVELATION)
        budget.register_cliffhanger(2, CliffhangerType.DECISION)

        closer = render_budget_diversity_block(
            budget, language="en", is_chapter_closer=True
        )
        non_closer = render_budget_diversity_block(
            budget, language="en", is_chapter_closer=False
        )
        assert closer is not None and "revelation" in closer
        assert non_closer is None

    def test_hot_vocab_respects_min_count(self) -> None:
        budget = DiversityBudget(project_id=uuid4())
        # 2 total occurrences — below the default min_count (3).
        budget.register_vocab(1, "sparkle sparkle", "en")
        block = render_budget_diversity_block(budget, language="en")
        assert block is None

    def test_combined_signals_render_together(self) -> None:
        budget = DiversityBudget(project_id=uuid4())
        budget.register_vocab(1, " ".join(["glimmer"] * 4), "en")
        budget.register_opening(1, OpeningArchetype.HUMILIATION)
        budget.register_cliffhanger(1, CliffhangerType.REVELATION)

        block = render_budget_diversity_block(
            budget,
            language="en",
            is_chapter_opener=True,
            is_chapter_closer=True,
            vocab_min_count=3,
        )
        assert block is not None
        # All three signals share the block.
        assert "glimmer" in block
        assert "humiliation" in block
        assert "revelation" in block

