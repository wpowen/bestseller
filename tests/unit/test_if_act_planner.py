from __future__ import annotations

from types import SimpleNamespace

import pytest

from bestseller.services.if_act_planner import _generate_fallback_acts


pytestmark = pytest.mark.unit


def test_if_fallback_acts_use_neutral_stage_labels_in_chinese() -> None:
    cfg = SimpleNamespace(
        target_chapters=300,
        act_count=3,
        arc_batch_size=50,
        branch_chapter_span=30,
        language="zh-CN",
    )

    acts = _generate_fallback_acts(cfg)

    assert [act["title"] for act in acts] == ["第1幕", "第2幕", "第3幕"]
    assert {act["core_theme"] for act in acts} <= {"起势推进", "压力升级", "终局收束"}
    assert all("觉醒崛起" not in act["act_goal"] for act in acts)


def test_if_fallback_acts_use_neutral_stage_labels_in_english() -> None:
    cfg = SimpleNamespace(
        target_chapters=300,
        act_count=3,
        arc_batch_size=50,
        branch_chapter_span=30,
        language="en-US",
    )

    acts = _generate_fallback_acts(cfg)

    assert [act["title"] for act in acts] == ["Act 1", "Act 2", "Act 3"]
    assert {act["core_theme"] for act in acts} <= {"Initial Momentum", "Pressure Escalation", "Endgame Resolution"}
    assert all("Awakening" not in act["act_goal"] for act in acts)
