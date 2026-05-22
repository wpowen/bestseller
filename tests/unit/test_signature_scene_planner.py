from __future__ import annotations

import pytest

from bestseller.domain.signature_scene import (
    SignatureSceneArchetype,
    SignatureSceneStake,
)
from bestseller.services.signature_scene_planner import (
    plan_signature_scenes,
    render_signature_scene_block,
)

pytestmark = pytest.mark.unit


def test_plan_rejects_invalid_total() -> None:
    with pytest.raises(ValueError):
        plan_signature_scenes(total_chapters=0)


def test_plan_rejects_invalid_cadence() -> None:
    with pytest.raises(ValueError):
        plan_signature_scenes(total_chapters=20, cadence=0)


def test_plan_default_cadence_produces_correct_slots() -> None:
    plan = plan_signature_scenes(
        total_chapters=60, cadence=10, include_golden_three=False
    )

    positions = [m.chapter_position for m in plan.mandates]
    assert positions == [10, 20, 30, 40, 50, 60]
    assert plan.total_chapters == 60
    assert plan.cadence == 10


def test_plan_adds_tail_mandate_when_uneven() -> None:
    plan = plan_signature_scenes(
        total_chapters=55, cadence=10, include_golden_three=False
    )

    positions = [m.chapter_position for m in plan.mandates]
    assert positions[-1] == 55  # tail patch added
    assert positions[:5] == [10, 20, 30, 40, 50]


def test_plan_includes_golden_three_by_default() -> None:
    """Chapters 1, 2, 3 are forced mandate positions — the retention fix."""

    plan = plan_signature_scenes(total_chapters=60, cadence=10)
    positions = [m.chapter_position for m in plan.mandates]

    assert positions[:3] == [1, 2, 3]
    # Then the normal cadence picks up at 10, 20, …
    assert 10 in positions
    assert 20 in positions


def test_golden_three_have_high_intensity() -> None:
    """Retention-critical chapters get intensity ≥0.85 unconditionally."""

    plan = plan_signature_scenes(total_chapters=60, cadence=10)
    by_pos = {m.chapter_position: m for m in plan.mandates}

    assert by_pos[1].intensity_target >= 0.85
    assert by_pos[2].intensity_target >= 0.85
    assert by_pos[3].intensity_target >= 0.85


def test_golden_three_archetypes_default_to_retention_pattern() -> None:
    """ch1=revelation, ch2=apotheosis (agency proof), ch3=oath_bound."""

    from bestseller.domain.signature_scene import SignatureSceneArchetype

    plan = plan_signature_scenes(total_chapters=30, cadence=10)
    by_pos = {m.chapter_position: m for m in plan.mandates}

    assert by_pos[1].archetype == SignatureSceneArchetype.REVELATION
    assert by_pos[2].archetype == SignatureSceneArchetype.APOTHEOSIS
    assert by_pos[3].archetype == SignatureSceneArchetype.OATH_BOUND


def test_golden_three_can_be_disabled() -> None:
    plan = plan_signature_scenes(
        total_chapters=30, cadence=10, include_golden_three=False
    )
    positions = [m.chapter_position for m in plan.mandates]

    assert 1 not in positions
    assert 2 not in positions
    assert 3 not in positions


def test_golden_three_respects_short_books() -> None:
    """A 2-chapter book gets ch1+ch2 mandates, no ch3."""

    plan = plan_signature_scenes(total_chapters=2, cadence=10)
    positions = [m.chapter_position for m in plan.mandates]

    assert positions == [1, 2]


def test_plan_archetype_and_stake_rotate() -> None:
    plan = plan_signature_scenes(total_chapters=100, cadence=10)
    archetypes = [m.archetype for m in plan.mandates]
    stakes = [m.stake for m in plan.mandates]

    assert len(set(archetypes)) >= 5  # rotation covers multiple archetypes
    assert len(set(stakes)) >= 4


def test_plan_intensity_curve_rises() -> None:
    plan = plan_signature_scenes(total_chapters=100, cadence=10)
    intensities = [m.intensity_target for m in plan.mandates]

    assert intensities[0] < intensities[-1]
    assert all(0 <= v <= 1 for v in intensities)


def test_plan_image_hints_populated() -> None:
    plan = plan_signature_scenes(total_chapters=30, cadence=10)
    for mandate in plan.mandates:
        assert mandate.must_include_image
        assert mandate.must_include_line


def test_mandate_for_chapter_lookup() -> None:
    plan = plan_signature_scenes(total_chapters=30, cadence=10)

    assert plan.mandate_for_chapter(10) is not None
    assert plan.mandate_for_chapter(15) is None  # not a slot position


def test_upcoming_lookahead() -> None:
    plan = plan_signature_scenes(total_chapters=60, cadence=10)

    upcoming = plan.upcoming(15, lookahead=2)
    assert len(upcoming) == 2
    assert upcoming[0].chapter_position == 20
    assert upcoming[1].chapter_position == 30


def test_plan_with_custom_rotations() -> None:
    plan = plan_signature_scenes(
        total_chapters=30,
        cadence=10,
        archetype_rotation=[SignatureSceneArchetype.SACRIFICE],
        stake_rotation=[SignatureSceneStake.LIFE_DEATH],
        include_golden_three=False,
    )

    for mandate in plan.mandates:
        assert mandate.archetype == SignatureSceneArchetype.SACRIFICE
        assert mandate.stake == SignatureSceneStake.LIFE_DEATH


def test_plan_with_explicit_intensity_curve() -> None:
    plan = plan_signature_scenes(
        total_chapters=30,
        cadence=10,
        intensity_curve=[0.5, 0.7, 0.9],
        include_golden_three=False,
    )

    intensities = [m.intensity_target for m in plan.mandates]
    assert intensities == [0.5, 0.7, 0.9]


def test_plan_intensity_curve_too_short_extended() -> None:
    plan = plan_signature_scenes(
        total_chapters=60,
        cadence=10,
        intensity_curve=[0.5, 0.9],
        include_golden_three=False,
    )

    intensities = [m.intensity_target for m in plan.mandates]
    assert intensities[0] == 0.5
    assert intensities[1] == 0.9
    assert intensities[2] == 0.9  # extended


def test_plan_intensity_curve_too_long_truncated() -> None:
    plan = plan_signature_scenes(
        total_chapters=20,
        cadence=10,
        intensity_curve=[0.5, 0.7, 0.9, 0.95, 0.99],
        include_golden_three=False,
    )

    intensities = [m.intensity_target for m in plan.mandates]
    assert intensities == [0.5, 0.7]


def test_plan_title_and_summary_hints_propagate() -> None:
    plan = plan_signature_scenes(
        total_chapters=30,
        cadence=10,
        title_hints=["十年之约", "故人来访", "断剑"],
        summary_hints=["十年前的恩怨揭开", "故人重逢", "信物归还"],
    )

    assert plan.mandates[0].title_hint == "十年之约"
    assert plan.mandates[1].summary == "故人重逢"
    assert plan.mandates[2].title_hint == "断剑"


def test_plan_payoff_targets_propagate() -> None:
    plan = plan_signature_scenes(
        total_chapters=20,
        cadence=10,
        payoff_targets=[["伏笔A", "伏笔B"], ["伏笔C"]],
    )

    assert "伏笔A" in plan.mandates[0].payoff_targets
    assert plan.mandates[1].payoff_targets == ["伏笔C"]


def test_render_block_emits_zh() -> None:
    plan = plan_signature_scenes(total_chapters=30, cadence=10)
    mandate = plan.mandates[0]

    block = render_signature_scene_block(mandate)

    assert "招牌场景指令" in block
    assert "本章质检硬指标" in block
    assert mandate.archetype.value in block


def test_render_block_handles_none() -> None:
    assert render_signature_scene_block(None) == ""


def test_render_block_supports_english() -> None:
    plan = plan_signature_scenes(total_chapters=10, cadence=10)
    mandate = plan.mandates[0]

    block = render_signature_scene_block(mandate, language="en")

    assert "Signature Scene Mandate" in block
