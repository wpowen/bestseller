# ruff: noqa: RUF001
from __future__ import annotations

from bestseller.services.ranking_seriality_gate import evaluate_ranking_seriality


def _proof(**overrides: object) -> dict[str, object]:
    proof: dict[str, object] = {
        "repeatable_story_unit": "每次接手一座失序关卡，解决新商路冲突并留下不可逆的城务变化",
        "unit_frequency": "每2-4章一次",
        "unit_count_estimate": 8,
        "unit_families": ["经营建设", "商路谈判", "边境冲突"],
        "renewal_sources": ["新商队与族群持续进入", "旧势力和竞争据点持续反制"],
        "accumulation_tracks": ["城防与设施", "商路盟约与声望"],
        "phase_transitions": ["废城立足", "区域通商", "多方争城"],
        "opposing_ecology": ["掠夺者", "竞争据点"],
    }
    proof.update(overrides)
    return proof


def test_thin_one_shot_black_coffin_fails_30_chapter_ranking_gate() -> None:
    report = evaluate_ranking_seriality(
        {
            "repeatable_story_unit": "找到等待主角七十年的黑棺",
            "unit_frequency": "全书一次",
            "unit_count_estimate": 1,
            "unit_families": ["调查"],
            "renewal_sources": ["黑棺真相"],
            "accumulation_tracks": ["死亡倒计时"],
            "phase_transitions": ["找到黑棺"],
            "opposing_ecology": ["送葬人"],
        },
        target_chapters=30,
    )

    assert not report.passed
    assert report.status == "BLOCKED"
    assert {
        "unit_frequency_unproven_or_sparse",
        "unit_count_below_minimum",
        "unit_family_breadth_thin",
        "renewal_sources_thin",
        "accumulation_tracks_thin",
        "phase_transitions_thin",
        "opposing_ecology_thin",
    } <= set(report.blocking_codes)


def test_operating_ruined_city_multi_engine_passes_30_chapter_gate() -> None:
    report = evaluate_ranking_seriality(_proof(), target_chapters=30)

    assert report.passed
    assert report.status == "PASS"
    assert report.blocking_codes == ()
    assert report.metrics["minimum_unit_count"] == 8


def test_30_chapter_gate_requires_cadence_and_budget_not_claim_only() -> None:
    report = evaluate_ranking_seriality(
        _proof(unit_frequency="每10章一次", unit_count_estimate=30),
        target_chapters=30,
    )

    assert not report.passed
    assert "unit_frequency_unproven_or_sparse" in report.blocking_codes


def test_longer_targets_delegate_to_existing_capacity_report() -> None:
    report = evaluate_ranking_seriality(
        _proof(
            unit_count_estimate=50,
            phase_transitions=["阶段一", "阶段二", "阶段三", "阶段四"],
            opposing_ecology=["掠夺者", "竞争据点", "监管者"],
            mystery_ladder=["谁在阻断商路", "谁在操纵城盟", "谁定义新秩序"],
            endgame_direction="让关卡从收费点变为新秩序的入口",
        ),
        target_chapters=200,
    )

    assert report.metrics["capacity_report_delegated"] is True
    assert "capacity_report" in report.metrics


def test_short_targets_remain_outside_ranking_seriality_gate() -> None:
    report = evaluate_ranking_seriality({}, target_chapters=20)

    assert report.passed
    assert report.status == "NOT_APPLICABLE"


def test_chinese_cadence_is_equivalent_to_arabic_cadence() -> None:
    for cadence in (
        "每两到三章一次新选择与反制",
        "每二至四章一轮关系变化",
        "每三章一次",
        "每 ２－４ 章一次",
        "每2—4章一次",
    ):
        report = evaluate_ranking_seriality(_proof(unit_frequency=cadence), target_chapters=30)
        assert report.passed, (cadence, report.blocking_codes)


def test_chinese_sparse_or_malformed_cadence_still_fails() -> None:
    for cadence in ("每十章一次", "每两到五章一次", "每四到两章一次", "每二三章一次", "每零章一次", "隔几章一次"):
        report = evaluate_ranking_seriality(_proof(unit_frequency=cadence), target_chapters=30)
        assert "unit_frequency_unproven_or_sparse" in report.blocking_codes, cadence
