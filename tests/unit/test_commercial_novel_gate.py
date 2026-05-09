# ruff: noqa: RUF001

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bestseller.services.commercial_novel_gate import (
    _callback_present,
    _infer_commercial_anchors,
    evaluate_book_package,
)

pytestmark = pytest.mark.unit


def _write_package(root: Path, *, drift: bool = False) -> None:
    (root / "listing").mkdir(parents=True)
    (root / "story-bible").mkdir(parents=True)
    (root / "listing" / "book-listing-metadata.json").write_text(
        json.dumps(
            {
                "book_id": "qingnang-test",
                "primary_title": "青囊不语问阴阳",
                "recommended_subtitle": "子时不入镜，否认者先入账",
                "logline": "落魄风水师林渊接下凶宅委托，卷入以否认为食的困魂镜。",
                "tags": ["民俗悬疑", "风水师", "规则怪谈", "三族契约"],
                "reader_promise": [
                    "每个诡案都有规则。",
                    "每次破局都有逼出真相和反制镜局的爽点。",
                ],
                "not_recommended_categories": ["纯无限流"],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (root / "README.md").write_text("# 青囊不语问阴阳\n", encoding="utf-8")
    (root / "story-bible" / "series-brief.md").write_text(
        "青囊秘卷、困魂镜、否认者先入账、三族契约、风水破局。",
        encoding="utf-8",
    )
    (root / "story-bible" / "reader-desire-map.md").write_text(
        "每章看林渊用罗盘、阴阳眼、铜钱逼人认账。",
        encoding="utf-8",
    )
    (root / "story-bible" / "series-bible.md").write_text(
        "林正淳入镜，林渊查张家开门人，钱家守镜。",
        encoding="utf-8",
    )
    (root / "story-bible" / "continuity-ledger.md").write_text(
        "第 1-7 章围绕困魂镜和回执推进。",
        encoding="utf-8",
    )
    (root / "story-bible" / "batch-queue.csv").write_text(
        "batch,chapters,goal,required_callbacks,end_hook\n"
        "1,1-6,完成入局,\"青囊;否认;回执\",镜影出现\n",
        encoding="utf-8",
    )
    (root / "story-bible" / "volume-plan.csv").write_text(
        "volume,chapters,premise,major_payoff,terminal_hook\n"
        "1,1-80,十七栋困魂镜局,林渊破镜救出一半受困者并确认父亲抵债真相,"
        "困魂镜只是第一笔旧账\n",
        encoding="utf-8",
    )
    (root / "story-bible" / "canon-guardrails.json").write_text(
        json.dumps(
            {
                "forbidden_terms": [
                    {"term": "守夜人", "reason": "旧版世界观词"},
                    {"term": "裴正阳", "reason": "旧版裴家线人物"},
                ],
                "state_rules": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    if drift:
        (root / "story-bible" / "volume-plan.csv").write_text(
            "volume,chapters,premise,major_payoff,terminal_hook\n"
            "1,1-80,十七栋困魂镜局,林渊破镜救出一半玩家并确认父亲抵债真相,"
            "困魂镜只是第一笔旧账\n",
            encoding="utf-8",
        )
        chapters = {
            1: "林渊持青囊进凶宅，罗盘疯转。否认者先入账，回执在镜中亮起！",
            2: "困魂镜开局，玩家出现，APP提示第二副本。守夜人和裴正阳在等他。",
            3: "第3章 破镜\n游戏副本继续加载，玩家必须找出真正敌人归墟之主。",
            4: "APP副本里，玩家直播间刷屏。青囊、罗盘、风水都被抛在一边。",
            5: "游戏继续，副本继续，玩家继续。没有认账，也没有张家开门人。",
        }
    else:
        chapters = {
            1: (
                "林渊持青囊进凶宅，罗盘疯转。王建业逼他子时入镜，"
                "否认者先入账，回执在镜中亮起：第一名会死？"
            ),
            2: "困魂镜开局，阴阳眼看见灰线。小雨否认，林渊逼她认账，门外突然传来老张被拖走的血声？",
            3: (
                "青囊秘卷显字，三族契约浮出水面。张家开门，"
                "钱家守镜，镜影冷笑着逼林渊交出父亲的真相？"
            ),
            4: "林渊以铜钱定方位，风水局压住回执。林正淳的名字出现。",
            5: "镜影逼近，困魂镜吞光。林渊让陈默承认隐瞒，认账才可活！",
            6: "青囊发烫，张家线索落地。王建业留下回执，门外传来敲门声？",
        }
    for chapter_no, body in chapters.items():
        (root / f"chapter-{chapter_no:03d}.md").write_text(
            f"# 第{chapter_no}章 测试\n\n{body}",
            encoding="utf-8",
        )


def test_commercial_gate_accepts_aligned_package(tmp_path: Path) -> None:
    _write_package(tmp_path, drift=False)

    report = evaluate_book_package(tmp_path)

    assert report.passed
    assert report.overall_score >= 75
    assert not any(issue.severity == "critical" for issue in report.issues)


def test_suspense_terms_do_not_hide_weak_golden_three(tmp_path: Path) -> None:
    _write_package(tmp_path, drift=False)
    weak_chapters = {
        1: "林渊接过钥匙，十五分钟凶宅，子时，青囊，镜，否认，入账。最后他收好东西。",
        2: "走廊里出现血字规则和灰线。父亲失踪，真相秘密都在镜里。最后青囊合上。",
        3: "困魂镜仍在，林渊说明三族契约和凶宅来历。最后他把铜钱放回口袋。",
    }
    for chapter_no, body in weak_chapters.items():
        (tmp_path / f"chapter-{chapter_no:03d}.md").write_text(
            f"# 第{chapter_no}章 测试\n\n{body}",
            encoding="utf-8",
        )

    report = evaluate_book_package(tmp_path)
    issue = next(issue for issue in report.issues if issue.code == "GOLDEN_THREE_COMMERCIAL_WEAK")

    assert not report.passed
    assert issue.severity == "critical"
    assert issue.evidence["suspense_fallback_applied"] is True
    assert "GOLDEN_THREE_LOW_HYPE" not in issue.evidence["issue_codes"]
    assert "GOLDEN_THREE_WEAK_OPEN_CONFLICT" in issue.evidence["issue_codes"]


def test_commercial_gate_skips_incomplete_batch_callbacks(tmp_path: Path) -> None:
    _write_package(tmp_path, drift=False)
    (tmp_path / "chapter-006.md").unlink()

    report = evaluate_book_package(tmp_path)
    codes = {issue.code for issue in report.issues}

    assert "BATCH_MISSION_MISSING_CALLBACK" not in codes


def test_commercial_gate_flags_contract_drift_and_canon_leak(tmp_path: Path) -> None:
    _write_package(tmp_path, drift=True)

    report = evaluate_book_package(tmp_path)
    codes = {issue.code for issue in report.issues}

    assert not report.passed
    assert "CANON_FORBIDDEN_TERM" in codes
    assert "GENRE_CONTRACT_DRIFT" in codes
    assert "PLANNING_ARTIFACT_GENRE_DRIFT" in codes
    assert "READER_CONTRACT_GAP" in codes
    assert "PREMATURE_MAJOR_PAYOFF" in codes


def test_batch_callback_matching_accepts_qingnang_aliases() -> None:
    assert _callback_present("王老板回执", "王建业尸体手里攥着回执镜片。")
    assert _callback_present("老张临死话", "张建军临死前留了一句话：那扇门不是张家开的。")


def test_qingnang_core_rule_anchor_accepts_evolved_debt_vocabulary() -> None:
    anchors = _infer_commercial_anchors(
        {
            "primary_title": "青囊不语问阴阳",
            "reader_promise": ["否认者先入账，镜债会不断升级。"],
        },
        "第22章开始出现镜债过户、承认与替认、第四人偿。",
    )
    core_rule = next(anchor for anchor in anchors if anchor.key == "core_rule")

    assert "镜债" in core_rule.terms
    assert "替认" in core_rule.terms
    assert "偿" in core_rule.terms
