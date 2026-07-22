"""Chapter word target must AIM at the target band, never the hard max.

Root-cause fix (2026-05-31): LLM outlines echo chapter_max (e.g. 3500) into
target_word_count. Accepting that verbatim left zero headroom for the model's
natural overshoot, so chapters blew past the cap. The writing goal must clamp
to chapter_target; chapter_max is only the hard ceiling.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from bestseller.services.word_targets import (
    _floor_safe_chapter_target,
    chapter_rewrite_length_band,
    effective_chapter_word_target,
    normalize_chapter_word_target,
    project_word_target_policy,
    scene_word_target_for_chapter,
    word_target_policy,
)
from bestseller.settings import load_settings


@pytest.mark.unit
class TestChapterTargetClampsToTarget:
    def test_project_declared_chapter_band_overrides_global_policy(self) -> None:
        s = load_settings()
        project = SimpleNamespace(
            target_word_count=28_000,
            target_chapters=10,
            metadata_json={
                "words_per_chapter": {"min": 2500, "target": 2800, "max": 3500}
            },
        )

        policy = project_word_target_policy(project, s)

        assert (policy.chapter_min, policy.chapter_target, policy.chapter_max) == (
            2500,
            2800,
            3500,
        )
        assert effective_chapter_word_target(project, s) == 2800
        assert normalize_chapter_word_target(2800, project, s) == 2800
        rewrite_band = chapter_rewrite_length_band(
            s,
            2800,
            language="zh-CN",
            role="editor",
            project=project,
        )
        assert (rewrite_band.hard_min, rewrite_band.hard_target, rewrite_band.hard_max) == (
            2500,
            2800,
            3500,
        )

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

    def test_proposal_above_floor_safe_honored(self) -> None:
        # A shorter-than-target proposal that still clears the production
        # floor-safe minimum is honored verbatim (writer may aim shorter).
        s = load_settings()
        policy = word_target_policy(s)
        floor_safe = _floor_safe_chapter_target(policy)
        shorter = floor_safe + 50
        assert shorter < policy.chapter_target  # genuinely shorter than target
        out = normalize_chapter_word_target(shorter, None, s)
        assert out == shorter

    def test_too_short_proposal_floored_to_production_safe(self) -> None:
        # A proposal whose ~75%-realized length would breach the 1800 hard floor
        # (e.g. the fragile 2-scene/2200 outline) must be floored up so realistic
        # under-production still clears the floor — no CHAPTER_TOO_SHORT churn.
        s = load_settings()
        policy = word_target_policy(s)
        floor_safe = _floor_safe_chapter_target(policy)
        out = normalize_chapter_word_target(policy.chapter_min + 50, None, s)
        assert out == floor_safe
        assert out * 0.78 >= 1800  # realistic under-production still clears floor

    def test_worst_case_overshoot_stays_under_hard_max(self) -> None:
        """3 scenes, each overshooting 1.3x, must stay <= chapter_max."""
        s = load_settings()
        policy = word_target_policy(s)
        chapter_target = normalize_chapter_word_target(policy.chapter_max, None, s)
        per_scene = scene_word_target_for_chapter(chapter_target, 3, s)
        worst_case = per_scene * 1.3 * 3
        assert worst_case <= policy.chapter_max
