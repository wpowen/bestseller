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


def test_plan_without_book_anchors_has_no_framework_anchor_content() -> None:
    """通用性硬约束：框架自身不得提供任何题材味锚词。

    无书内锚词时 mandate 锚词必须为空（验收走语义判官），而不是回落到
    硬编码的仙侠/探案字典（旧行为，对其他题材是不可达噪声）。
    """
    plan = plan_signature_scenes(total_chapters=30, cadence=10)
    for mandate in plan.mandates:
        assert mandate.must_include_image == []
        assert mandate.must_include_line == []


def test_plan_book_anchors_flow_into_every_mandate() -> None:
    plan = plan_signature_scenes(
        total_chapters=30,
        cadence=10,
        anchor_images=["灵务局工牌", "审批红章", "巡查记录仪", "多余的"],
        anchor_lines=["编制不是天道，是人定的"],
    )
    for mandate in plan.mandates:
        assert mandate.must_include_image == [
            "灵务局工牌",
            "审批红章",
            "巡查记录仪",
        ]
        assert mandate.must_include_line == ["编制不是天道，是人定的"]


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
    plan = plan_signature_scenes(
        total_chapters=30,
        cadence=10,
        summary_hints=["十年前的恩怨当场揭开"],
    )
    mandate = plan.mandates[0]

    block = render_signature_scene_block(mandate)

    assert block is not None
    assert "招牌场景指令" in block
    assert "本章质检硬指标" in block
    assert mandate.archetype.value in block


def test_render_block_handles_none() -> None:
    assert render_signature_scene_block(None) == ""


def test_render_block_supports_english() -> None:
    plan = plan_signature_scenes(
        total_chapters=10,
        cadence=10,
        summary_hints=["The decade-old feud is exposed on the spot."],
    )
    mandate = plan.mandates[0]

    block = render_signature_scene_block(mandate, language="en")

    assert block is not None
    assert "Signature Scene Mandate" in block


def test_render_block_requires_verbatim_anchor_inclusion() -> None:
    """指令与验收对齐：验收是 exact substring，指令必须要求原词/原句出现。

    旧措辞「择一改写出现」教写手输出改写台词 → 验收 0 命中 →
    SIGNATURE_SCENE_MISSING critical（500章跑书 10/10 失败成因之一）。
    """
    from bestseller.services.signature_scene_planner import (
        plan_signature_scenes,
        render_signature_scene_block,
    )

    plan = plan_signature_scenes(
        total_chapters=20,
        anchor_images=["灵务局工牌"],
        anchor_lines=["编制不是天道"],
    )
    mandate = plan.mandate_for_chapter(1)
    block = render_signature_scene_block(mandate)

    assert "原词完整出现" in block
    assert "原句完整出现" in block
    assert "灵务局工牌" in block
    assert "择一改写" not in block


def test_render_block_without_anchors_gives_concept_guidance_only() -> None:
    plan = plan_signature_scenes(
        total_chapters=20,
        summary_hints=["主角在众目睽睽之下揭开尘封十年的真相"],
    )
    mandate = plan.mandate_for_chapter(1)
    block = render_signature_scene_block(mandate)

    assert block is not None
    assert "场景概念要求" in block
    assert "原词完整出现" not in block  # 无锚词时不得提出原词验收要求


# ── R25: 空壳 mandate 自举闭环 ──────────────────────────────────────────────


def test_plan_without_any_concrete_target_marks_skeleton() -> None:
    """无章纲、无锚词、无 hints → mandate 全部是 skeleton（空壳）。"""

    plan = plan_signature_scenes(total_chapters=30, cadence=10)

    for mandate in plan.mandates:
        assert mandate.status == "skeleton"
        assert mandate.is_skeleton


def test_plan_with_anchors_marks_ready() -> None:
    plan = plan_signature_scenes(
        total_chapters=30, cadence=10, anchor_images=["灵务局工牌"]
    )

    for mandate in plan.mandates:
        assert mandate.status == "ready"
        assert not mandate.is_skeleton


def test_render_block_returns_none_for_skeleton_mandate() -> None:
    """空壳不下发：写手不能被拿空标准考核（R25）。"""

    plan = plan_signature_scenes(total_chapters=20)
    mandate = plan.mandate_for_chapter(1)

    assert mandate is not None
    assert render_signature_scene_block(mandate) is None
    assert render_signature_scene_block(mandate, language="en") is None


def test_legacy_persisted_empty_mandate_loads_as_skeleton() -> None:
    """旧版持久化 plan（无 status 字段、全空 mandate）加载后判 skeleton。"""

    from bestseller.domain.signature_scene import SignatureSceneMandate

    legacy = SignatureSceneMandate.model_validate(
        {
            "chapter_position": 1,
            "archetype": "revelation",
            "stake": "identity_truth",
        }
    )

    assert legacy.is_skeleton
    assert render_signature_scene_block(legacy) is None


def test_plan_derives_mandate_targets_from_chapter_outline() -> None:
    """章纲存在时确定性派生 must_include_image(前2)/summary(120字)/title_hint。"""

    long_goal = "主角在义庄的铜镜前发现自己倒影缺失，" * 20  # > 120 chars
    outline = {
        1: {
            "title": "镜中无人",
            "goal": long_goal,
            "signature_images": ["铜镜里缺失的倒影", "义庄的长明灯", "第三个不该派生的意象"],
        },
    }

    plan = plan_signature_scenes(total_chapters=30, cadence=10, chapter_outline=outline)
    mandate = plan.mandate_for_chapter(1)

    assert mandate is not None
    assert mandate.must_include_image == ["铜镜里缺失的倒影", "义庄的长明灯"]
    assert mandate.summary == long_goal[:120]
    assert mandate.title_hint == "镜中无人"
    assert mandate.status == "ready"

    # 章纲没覆盖到的槽位仍是 skeleton，不下发。
    uncovered = plan.mandate_for_chapter(10)
    assert uncovered is not None
    assert uncovered.is_skeleton


def test_chapter_outline_tolerates_str_keys_and_chapter_goal_field() -> None:
    outline = {
        "2": {"chapter_goal": "第二章目标", "signature_image": "断裂的桃木剑"},
    }

    plan = plan_signature_scenes(total_chapters=30, cadence=10, chapter_outline=outline)
    mandate = plan.mandate_for_chapter(2)

    assert mandate is not None
    assert mandate.summary == "第二章目标"
    assert mandate.must_include_image == ["断裂的桃木剑"]
    assert mandate.status == "ready"


def test_chapter_outline_images_take_precedence_over_global_anchors() -> None:
    outline = {1: {"signature_images": ["本章专属意象"]}}

    plan = plan_signature_scenes(
        total_chapters=30,
        cadence=10,
        anchor_images=["全书通用锚"],
        chapter_outline=outline,
    )

    assert plan.mandate_for_chapter(1).must_include_image == ["本章专属意象"]
    # 未覆盖章回落到全书锚词。
    assert plan.mandate_for_chapter(10).must_include_image == ["全书通用锚"]
