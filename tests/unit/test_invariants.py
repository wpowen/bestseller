"""Unit tests for L1 ``ProjectInvariants`` seeding and round-trip.

These lock in the two behaviors downstream stages depend on:
    * seeding from ``generation.words_per_chapter`` produces an envelope
      scaled correctly for CJK vs Latin languages
    * JSONB round-trip (``invariants_to_dict`` / ``invariants_from_dict``)
      is information-preserving
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from bestseller.services.invariants import (
    CliffhangerPolicy,
    CliffhangerType,
    InvariantSeedError,
    LengthEnvelope,
    NamingScheme,
    OpeningArchetype,
    ProjectInvariants,
    infer_pov_from_sample,
    invariants_from_dict,
    invariants_to_dict,
    seed_invariants,
)

pytestmark = pytest.mark.unit


def _words(min_: int = 5000, target: int = 6400, max_: int = 7500) -> SimpleNamespace:
    return SimpleNamespace(min=min_, target=target, max=max_)


# ---------------------------------------------------------------------------
# LengthEnvelope
# ---------------------------------------------------------------------------


class TestLengthEnvelope:
    def test_monotonic_is_accepted(self) -> None:
        env = LengthEnvelope(1000, 2000, 3000)
        assert env.min_chars < env.target_chars < env.max_chars

    def test_non_monotonic_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            LengthEnvelope(2000, 1000, 3000)

    def test_equal_bounds_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            LengthEnvelope(1000, 1000, 2000)


# ---------------------------------------------------------------------------
# seed_invariants
# ---------------------------------------------------------------------------


class TestSeedInvariants:
    def test_english_scales_chars_up(self) -> None:
        inv = seed_invariants(
            project_id=uuid4(),
            language="en",
            words_per_chapter=_words(5000, 6400, 7500),
        )
        # English scale is 5.5× so min=27500 at 5000 words.
        assert inv.length_envelope.min_chars == 27500
        assert inv.length_envelope.target_chars == 35200
        assert inv.length_envelope.max_chars == 41250
        assert inv.language == "en"

    def test_chinese_uses_char_count_as_is(self) -> None:
        inv = seed_invariants(
            project_id=uuid4(),
            language="zh-CN",
            words_per_chapter=_words(5000, 6400, 7500),
        )
        assert inv.length_envelope.min_chars == 5000
        assert inv.length_envelope.target_chars == 6400
        assert inv.length_envelope.max_chars == 7500
        assert inv.language == "zh-CN"

    def test_unknown_pov_falls_back_to_close_third(self) -> None:
        inv = seed_invariants(
            project_id=uuid4(),
            language="zh-CN",
            words_per_chapter=_words(),
            pov="unknown-value",
        )
        assert inv.pov == "close_third"

    def test_present_tense_is_normalized(self) -> None:
        inv = seed_invariants(
            project_id=uuid4(),
            language="en",
            words_per_chapter=_words(),
            tense="present perfect",  # anything present-prefixed → present
        )
        assert inv.tense == "present"

    def test_overrides_length_envelope(self) -> None:
        inv = seed_invariants(
            project_id=uuid4(),
            language="en",
            words_per_chapter=_words(),
            overrides={"length_envelope": LengthEnvelope(100, 200, 300)},
        )
        assert inv.length_envelope == LengthEnvelope(100, 200, 300)


# ---------------------------------------------------------------------------
# JSONB round-trip
# ---------------------------------------------------------------------------


class TestSerialization:
    def test_round_trip_preserves_all_fields(self) -> None:
        original = ProjectInvariants(
            project_id=uuid4(),
            language="en",
            length_envelope=LengthEnvelope(1000, 2000, 3000),
            pov="first",
            tense="present",
            naming_scheme=NamingScheme(
                style="latinate",
                seed_pool=("Lyra", "Idris"),
                reserved_surnames=("Ashford",),
                validator_regex=r"^[A-Z][a-z]+$",
            ),
            cliffhanger_policy=CliffhangerPolicy(
                no_repeat_within=4,
                allowed_types=(CliffhangerType.REVELATION, CliffhangerType.INTRUSION),
            ),
            banned_formulaic_phrases=("shard",),
        )
        restored = invariants_from_dict(invariants_to_dict(original))
        assert restored == original

    def test_missing_mandatory_key_raises(self) -> None:
        with pytest.raises(InvariantSeedError):
            invariants_from_dict({"project_id": str(uuid4())})

    def test_default_opening_archetype_pool(self) -> None:
        payload = invariants_to_dict(
            seed_invariants(
                project_id=uuid4(),
                language="en",
                words_per_chapter=_words(),
            )
        )
        restored = invariants_from_dict(payload)
        # Default pool is the full enum.
        assert set(restored.opening_archetype_pool) == set(OpeningArchetype)


# ---------------------------------------------------------------------------
# infer_pov_from_sample
# ---------------------------------------------------------------------------


class TestInferPovFromSample:
    """Audit-time POV inference for projects that predate L1 seeding.

    The function must be *conservative* — defaulting to ``close_third`` when
    the signal is ambiguous — because a wrong ``first`` declaration would
    flag nearly every narrative sentence as POV drift, creating alarm fatigue.
    """

    def test_empty_sample_returns_close_third(self) -> None:
        assert infer_pov_from_sample("", language="en") == "close_third"

    def test_en_first_person_narrative_is_detected(self) -> None:
        # Dense first-person narrative with some dialogue. Dialogue contains
        # "she" / "he" which should be stripped before counting.
        text = (
            ("I walked into the room. I saw my reflection. "
             "I felt myself tremble. My heart raced. I knew me. ") * 10
            + '"She is gone," she whispered. '
            + '"He will not return," he said.'
        )
        assert infer_pov_from_sample(text, language="en") == "first"

    def test_en_close_third_narrative_is_detected(self) -> None:
        text = (
            ("She walked into the room. She saw her reflection. "
             "Her heart raced. He knew her well. His hand trembled. ") * 10
        )
        assert infer_pov_from_sample(text, language="en") == "close_third"

    def test_en_borderline_returns_close_third(self) -> None:
        # First-person count is below the 30-hit floor — stay conservative.
        text = "I walked in. I saw her. She was crying. She turned away."
        assert infer_pov_from_sample(text, language="en") == "close_third"

    def test_zh_first_person_narrative_is_detected(self) -> None:
        text = ("我走进房间。我看着镜子里的自己。我感到心跳加速。" * 20
                + "「她已经走了，」她低声说。")
        assert infer_pov_from_sample(text, language="zh-CN") == "first"

    def test_zh_close_third_narrative_is_detected(self) -> None:
        text = ("她走进房间。她看着镜子里的她。她感到心跳加速。"
                "他看着她，他的心也在颤抖。" * 20)
        assert infer_pov_from_sample(text, language="zh-CN") == "close_third"

    def test_zh_dialogue_first_person_is_stripped(self) -> None:
        # All "我" appear inside dialogue — narrator is third-person.
        text = (("她走进房间。她看着他。" * 20)
                + "「我来了，」她说。「我不走了，」他回答。" * 10)
        assert infer_pov_from_sample(text, language="zh-CN") == "close_third"
