from __future__ import annotations

from pathlib import Path

import pytest

from bestseller.services.commercial_planning_readiness import (
    evaluate_commercial_planning_readiness,
)

pytestmark = pytest.mark.unit


def _write_required_artifacts(package_root: Path) -> None:
    for relative in (
        "story-bible/series-brief.md",
        "story-bible/reader-desire-map.md",
        "story-bible/series-bible.md",
        "story-bible/continuity-ledger.md",
        "story-bible/batch-queue.csv",
        "story-bible/volume-plan.csv",
    ):
        path = package_root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("ok\n", encoding="utf-8")


def test_commercial_readiness_rejects_thin_solo_golden_three_plan(
    tmp_path: Path,
) -> None:
    chapters = [
        {
            "chapter_number": number,
            "title": f"第{number}章",
            "chapter_goal": "推进真相揭示",
            "opening_situation": "",
            "main_conflict": "",
            "hook_description": "",
            "hype_type": "reversal" if number == 1 else "",
            "hype_intensity": 0.1,
            "scenes": [
                {
                    "scene_number": 1,
                    "scene_type": "investigation",
                    "title": "独自调查",
                    "participants": ["沈青崖"],
                    "purpose": {"story": "独自调查推进剧情"},
                    "entry_state": {},
                    "exit_state": {},
                    "hook_requirement": "",
                }
            ],
        }
        for number in (1, 2, 3)
    ]

    report = evaluate_commercial_planning_readiness(
        chapters,
        target_chapters=500,
        package_root=tmp_path,
    )

    codes = {finding.code for finding in report.findings}
    assert report.passed is False
    assert "long_serial_artifacts_missing" in codes
    assert "missing_opening_situation" in codes
    assert "missing_main_conflict" in codes
    assert "missing_chapter_hook_plan" in codes
    assert "golden_three_solo_scene_chain" in codes
    assert "golden_three_external_pressure_missing" in codes
    assert "golden_three_visible_loss_missing" in codes
    assert "golden_three_protagonist_agency_missing" in codes
    assert "golden_three_hype_underpowered" in codes


def test_commercial_readiness_rejects_clue_only_opening_without_loss_or_agent(
    tmp_path: Path,
) -> None:
    _write_required_artifacts(tmp_path)
    chapters = [
        {
            "chapter_number": 1,
            "title": "验尸房来客",
            "chapter_goal": "主角发现溺亡尸体无积水。",
            "opening_situation": "沈青崖在验尸房检查尸体，发现死者鬼魂喊冤。",
            "main_conflict": "死者鬼魂声称凶手就在现场，但验尸房里只有活人。",
            "hook_description": "走廊尽头有黑影消失。",
            "hype_type": "reveal",
            "hype_intensity": 8.0,
            "scenes": [
                {
                    "scene_number": 1,
                    "scene_type": "investigation",
                    "title": "独自验尸",
                    "participants": ["沈青崖"],
                    "purpose": {"story": "沈青崖查看尸体并发现线索。"},
                    "hook_requirement": "黑影消失，留下疑点。",
                }
            ],
        },
        {
            "chapter_number": 2,
            "title": "李宅疑云",
            "chapter_goal": "主角发现枯井封印破损。",
            "opening_situation": "沈青崖抵达李宅后院。",
            "main_conflict": "管家逼沈青崖离开后院，否则天亮前官府会封井并烧掉残符。",
            "hook_description": "井底传出不属于李德盛的声音。",
            "hype_type": "reversal",
            "hype_intensity": 8.0,
            "scenes": [
                {
                    "scene_number": 1,
                    "scene_type": "confrontation",
                    "title": "后院封井",
                    "participants": ["沈青崖", "管家"],
                    "purpose": {"story": "沈青崖当场反制管家并保住残符。"},
                    "hook_requirement": "井底陌生声音喊出沈家的孩子。",
                }
            ],
        },
        {
            "chapter_number": 3,
            "title": "道士之死",
            "chapter_goal": "主角拿到第一条归字线索。",
            "opening_situation": "护城河边发现道士尸体。",
            "main_conflict": "巡捕逼沈青崖按溺亡结案，否则尸体日落前会被拖去焚化。",
            "hook_description": "道士血字指向归字黑袍人。",
            "hype_type": "reveal",
            "hype_intensity": 8.0,
            "scenes": [
                {
                    "scene_number": 1,
                    "scene_type": "confrontation",
                    "title": "河岸抢尸",
                    "participants": ["沈青崖", "巡捕"],
                    "purpose": {"story": "沈青崖拒绝交尸并逼问谁下令灭口。"},
                    "hook_requirement": "血字归与十五年前火场金线重合。",
                }
            ],
        },
    ]

    report = evaluate_commercial_planning_readiness(
        chapters,
        target_chapters=500,
        package_root=tmp_path,
    )

    codes = {finding.code for finding in report.findings}
    assert report.passed is False
    assert "golden_three_visible_loss_missing" in codes
    assert "golden_three_solo_scene_chain" in codes


def test_commercial_readiness_accepts_concrete_golden_three_and_artifacts(
    tmp_path: Path,
) -> None:
    _write_required_artifacts(tmp_path)
    chapters = [
        {
            "chapter_number": number,
            "title": f"证据第{number}次反转",
            "chapter_goal": "主角当场保住证据并逼出更深疑点。",
            "opening_situation": "尸体刚喊冤, 官府就要当场结案并封锁验尸房。",
            "main_conflict": "沈青崖必须在官府夺走证据前证明尸体被灭口, 否则唯一线索会被烧掉。",
            "hook_description": "章尾留下谁在尸体掌心写下归字的悬念。",
            "hype_type": "reversal",
            "hype_intensity": 8.0,
            "scenes": [
                {
                    "scene_number": 1,
                    "scene_type": "confrontation",
                    "title": "验尸房封锁",
                    "participants": ["沈青崖", "周捕头"],
                    "purpose": {"story": "周捕头逼他交出证据, 沈青崖当场反制。"},
                    "entry_state": {"evidence": "尸体喊冤"},
                    "exit_state": {"evidence": "保住第一条线索"},
                    "hook_requirement": "尸体掌心露出归字, 指向下一章追查。",
                }
            ],
        }
        for number in (1, 2, 3)
    ]

    report = evaluate_commercial_planning_readiness(
        chapters,
        target_chapters=500,
        package_root=tmp_path,
    )

    assert report.passed is True
    assert report.findings == ()
    assert report.strong_golden_hype_chapters == 3
    assert report.golden_three_hooked_chapters == 3
    assert report.golden_three_external_pressure_chapters == 3
