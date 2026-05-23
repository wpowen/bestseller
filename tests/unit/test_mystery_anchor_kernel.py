from __future__ import annotations

from pydantic import ValidationError
import pytest

from bestseller.domain.mystery_anchor import (
    MysteryAnchor,
    MysteryAnchorKernel,
    RevealMilestone,
)
from bestseller.services.mystery_anchor_reveal_gate import scan_mystery_anchor_reveals

pytestmark = pytest.mark.unit


def _anchor() -> MysteryAnchor:
    return MysteryAnchor(
        question="城中失踪的钟声来自哪里?",
        stake_if_solved="揭开后会动摇王城合法性。",
        reveal_milestones=[
            RevealMilestone(
                volume=1,
                fraction_revealed=0.2,
                reveal_kind="hint",
                description="钟声只在雨夜出现。",
            ),
            RevealMilestone(
                volume=3,
                fraction_revealed=1.0,
                reveal_kind="full_reveal",
                description="钟声来自地下旧朝祭坛。",
            ),
        ],
        false_lead_plan=["怀疑更夫"],
        final_payoff_chapter_range=(80, 100),
    )


def test_anchor_requires_two_milestones() -> None:
    with pytest.raises(ValidationError):
        MysteryAnchor(
            question="谁点燃了旧塔?",
            stake_if_solved="牵出旧案。",
            reveal_milestones=[
                RevealMilestone(
                    volume=1,
                    fraction_revealed=1.0,
                    reveal_kind="full_reveal",
                    description="真相。",
                )
            ],
            false_lead_plan=[],
            final_payoff_chapter_range=(10, 12),
        )


def test_volume_must_advance_some_anchor() -> None:
    report = scan_mystery_anchor_reveals(
        MysteryAnchorKernel(anchors=[_anchor()], inter_anchor_dependencies={}),
        volume=2,
        revealed_ledger=[],
    )
    assert any(f.code == "volume_without_anchor_advance" for f in report.findings)


def test_false_lead_actually_appears() -> None:
    report = scan_mystery_anchor_reveals(
        MysteryAnchorKernel(anchors=[_anchor()], inter_anchor_dependencies={}),
        volume=1,
        revealed_ledger=["雨夜钟声"],
    )
    assert any(f.code == "false_lead_missing" for f in report.findings)


def test_full_reveal_within_payoff_range() -> None:
    report = scan_mystery_anchor_reveals(
        MysteryAnchorKernel(anchors=[_anchor()], inter_anchor_dependencies={}),
        volume=3,
        revealed_ledger=["地下旧朝祭坛"],
        full_reveal_chapters={"城中失踪的钟声来自哪里?": 60},
    )
    assert any(f.code == "full_reveal_outside_payoff_range" for f in report.findings)
