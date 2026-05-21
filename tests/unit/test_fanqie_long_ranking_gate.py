# ruff: noqa: RUF001

from __future__ import annotations

import pytest

from bestseller.services.fanqie_long_ranking_gate import (
    FanqieLongRankingReport,
    evaluate_fanqie_long_ranking_gate,
)

pytestmark = pytest.mark.unit


def _strong_chapters() -> dict[int, str]:
    chapter_1 = (
        "林澈被主管逼到会议室门口，否则母亲手术押金就会被冻结。"
        "他抓住桌上的证据反手曝光账目，系统面板解锁第一条规则，疼痛和冷却同时袭来。"
        "全场认账，主管自爆漏洞，他赢下第一轮筹码。就在这时，门外响起总公司的电话？"
    )
    chapter_2 = (
        "总公司必须夺回证据，林澈决定用系统规则反制。"
        "能力进入可操作状态，但每次使用都会暴露坐标并带来反噬疼痛。"
        "他拿到新证据，发现背后还有更大的债主。下一刻，母亲病房门被推开？"
    )
    chapter_3 = (
        "债主逼他交出面板权限，否则病房会被封。林澈冲进走廊反击，"
        "用规则漏洞曝光对方假合同，赢下第二个筹码，也损失一次冷却机会。"
        "真相刚露出一角，电话里传来父亲还活着的声音？"
    )
    return {1: chapter_1, 2: chapter_2, 3: chapter_3}


def test_fanqie_long_gate_passes_strong_first_three_chapters() -> None:
    report = evaluate_fanqie_long_ranking_gate(
        _strong_chapters(),
        project_slug="urban-loop",
        protagonist_name="林澈",
    )

    assert isinstance(report, FanqieLongRankingReport)
    assert report.passed is True
    assert report.project_slug == "urban-loop"
    assert report.metrics["chapter_count"] == 3
    assert report.findings == []


def test_fanqie_long_gate_accepts_forensic_mystery_loop_terms() -> None:
    report = evaluate_fanqie_long_ranking_gate(
        {
            1: (
                "验尸房的门被人从外面锁上时，沈青崖正把银针探进死者喉间。"
                "周神算逼他日落前交出尸体，否则递解出城并焚尸灭口。"
                "他踢翻火盆，封存验尸格，从喉骨纸灰里挑出残符物证，抓到第一个破局线索。"
                "匣盖缝里，又渗出一模一样的纸灰。"
            ),
            2: (
                "林氏必须交代井下尸源，沈青崖用重瞳和验尸证据链取证。"
                "阴阳符能让死者残念开口，却会反噬咳血并暴露他能见鬼的秘密。"
                "他封存井泥里的钥匙，指认第一个知情者。下一刻，井底的人笑着说找到了。"
            ),
            3: (
                "周神算抢走残符逼他认罪，否则封井烧尸。沈青崖冲进义庄反击，"
                "用银针和物证供出真正搬尸人，赢下验尸权，也因反噬流血。"
                "真相只露一角，墙上忽然浮出一个归字？"
            ),
        },
        project_slug="exorcist-detective",
        protagonist_name="沈青崖",
    )

    assert report.passed is True
    assert report.project_slug == "exorcist-detective"
    assert report.metrics["ability_chapter_count"] == 3
    assert report.findings == []


def test_fanqie_long_gate_blocks_weak_background_opening() -> None:
    report = evaluate_fanqie_long_ranking_gate(
        {
            1: (
                "灵气复苏已经三百年。世界分为九大等级，历史上每个等级都有复杂设定。"
                "据说远古时代留下了很多传说，规则如下，背景是一个庞大的学院体系。"
            ),
            2: "世界分为更多等级，历史上各宗门互相制衡，设定继续展开。",
            3: "林澈终于决定出门。",
        },
        protagonist_name="林澈",
    )

    codes = {finding.code for finding in report.findings}

    assert report.passed is False
    assert "first_50_focus_missing" in codes
    assert "first_100_pressure_missing" in codes
    assert "first_3000_core_loop_missing" in codes
    assert "consecutive_exposition_only" in codes


def test_fanqie_long_gate_reports_ability_without_cost_as_warning() -> None:
    chapters = _strong_chapters()
    chapters[2] = (
        "总公司必须夺回证据，林澈决定用系统规则反制。"
        "能力进入可操作状态，他拿到新证据，发现背后还有更大的债主。"
        "下一刻，母亲病房门被推开？"
    )

    report = evaluate_fanqie_long_ranking_gate(chapters, protagonist_name="林澈")
    findings = {finding.code: finding for finding in report.findings}

    assert report.passed is True
    assert findings["advantage_cost_missing"].severity == "warning"
