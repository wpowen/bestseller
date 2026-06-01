"""Chapter word target must AIM at the target band, never the hard max.

Root-cause fix (2026-05-31): LLM outlines echo chapter_max (e.g. 3500) into
target_word_count. Accepting that verbatim left zero headroom for the model's
natural overshoot, so chapters blew past the cap. The writing goal must clamp
to chapter_target; chapter_max is only the hard ceiling.
"""
from __future__ import annotations

import pytest

from bestseller.services.word_targets import (
    normalize_chapter_word_target,
    scene_word_target_for_chapter,
    word_target_policy,
)
from bestseller.settings import load_settings


@pytest.mark.unit
class TestChapterTargetClampsToTarget:
    def test_max_proposal_clamped_to_target(self) -> None:
        s = load_settings()
        policy = word_target_policy(s)
        out = normalize_chapter_word_target(policy.chapter_max, None, s)
        assert out == policy.chapter_target
        assert out < policy.chapter_max

    def test_above_max_clamped(self) -> None:
        s = load_settings()
        policy = word_target_policy(s)
        out = normalize_chapter_word_target(policy.chapter_max + 2000, None, s)
        # out-of-range -> effective target (also the target band)
        assert out <= policy.chapter_target

    def test_shorter_proposal_honored(self) -> None:
        s = load_settings()
        policy = word_target_policy(s)
        shorter = policy.chapter_min + 50
        out = normalize_chapter_word_target(shorter, None, s)
        assert out == shorter

    def test_worst_case_overshoot_stays_under_hard_max(self) -> None:
        """3 scenes, each overshooting 1.3x, must stay <= chapter_max."""
        s = load_settings()
        policy = word_target_policy(s)
        chapter_target = normalize_chapter_word_target(policy.chapter_max, None, s)
        per_scene = scene_word_target_for_chapter(chapter_target, 3, s)
        worst_case = per_scene * 1.3 * 3
        assert worst_case <= policy.chapter_max
