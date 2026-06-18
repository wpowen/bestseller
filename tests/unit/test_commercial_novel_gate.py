# ruff: noqa: RUF001

from __future__ import annotations

import json
from pathlib import Path

import pytest

from bestseller.services.commercial_novel_gate import (
    CommercialGatePolicy,
    _callback_present,
    _infer_commercial_anchors,
    commercial_gate_report_to_dict,
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
    assert report.overall_score >= 95
    assert report.metrics["blocking_issue_counts"] == {}


def test_commercial_gate_integrates_retention_onboarding_findings(tmp_path: Path) -> None:
    _write_package(tmp_path, drift=False)
    (tmp_path / "story-bible" / "canonical-terms.yaml").write_text(
        """
terms:
  - term: 王建业
    category: character
  - term: 张建军
    category: character
  - term: 钱婆婆
    category: character
  - term: 罗盘
    category: object
  - term: 康熙铜钱
    category: object
  - term: 阴阳眼
    category: object
  - term: 十七栋
    category: place
  - term: 302
    category: place
""".strip(),
        encoding="utf-8",
    )
    (tmp_path / "chapter-001.md").write_text(
        "# 第1章 测试\n\n"
        "23:47，王建业在十七栋递钥匙。林渊握着罗盘、康熙铜钱和阴阳眼，看见302门开。",
        encoding="utf-8",
    )
    (tmp_path / "chapter-002.md").write_text(
        "# 第2章 测试\n\n21:30，张建军敲门。章尾：罗盘指向302。",
        encoding="utf-8",
    )

    report = evaluate_book_package(tmp_path)
    codes = {issue.code for issue in report.issues}

    assert not report.passed
    assert "ONBOARDING_OVERLOAD" in codes
    assert "TIME_ANCHOR_BACKWARDS" in codes
    assert report.metrics["retention_onboarding_gate"]["verdict"] == "blocked"


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
    assert report.gate_verdict.verdict == "blocked"
    assert report.gate_verdict.passed is False


def test_commercial_gate_flags_qingnang_front_canon_regressions(tmp_path: Path) -> None:
    _write_package(tmp_path, drift=False)
    (tmp_path / "story-bible" / "canon-guardrails.json").write_text(
        json.dumps(
            {
                "forbidden_terms": [],
                "state_rules": [
                    {
                        "subject": "林远山",
                        "status": "三百年前封镜先祖，不是三十年前补镜人",
                        "applies_after_chapter": 0,
                        "forbidden_patterns": [
                            "林远山.{0,40}(三十年前|二十三年前|三年前).{0,40}(封|补).{0,20}(困魂镜|镜)",
                            "(三十年前|二十三年前|三年前).{0,40}林远山.{0,40}(封|补).{0,20}(困魂镜|镜)",
                        ],
                    },
                    {
                        "subject": "小雨",
                        "status": "第4章已认账获救",
                        "applies_after_chapter": 4,
                        "forbidden_patterns": [
                            "小雨.{0,60}(脸.{0,12}变淡|脸正在变淡|近乎透明|身体.{0,20}往镜子里缩|身体.{0,20}往镜面里陷|她还在里面)"
                        ],
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (tmp_path / "chapter-007.md").write_text(
        "# 第7章 测试\n\n"
        "林渊说，林远山三十年前补过困魂镜。小雨的脸正在变淡，身体在往镜子里缩。",
        encoding="utf-8",
    )

    report = evaluate_book_package(tmp_path)
    issue = next(issue for issue in report.issues if issue.code == "CANON_STATE_REGRESSION")

    assert not report.passed
    assert issue.chapter_no == 7
    assert issue.evidence["subject"] == "林远山"


def test_commercial_gate_allows_negated_forbidden_terms_in_planning_contracts(
    tmp_path: Path,
) -> None:
    _write_package(tmp_path, drift=False)
    (tmp_path / "story-bible" / "canon-guardrails.json").write_text(
        json.dumps(
            {
                "forbidden_terms": [{"term": "玩家", "reason": "游戏化漂移"}],
                "state_rules": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (tmp_path / "story-bible" / "prewrite-contract.json").write_text(
        json.dumps({"protagonist_forbidden_vocabulary": ["玩家"]}, ensure_ascii=False),
        encoding="utf-8",
    )
    (tmp_path / "story-bible" / "ch51-75-recovery-contract.md").write_text(
        "禁用游戏化漂移词：玩家。后续必须替换为入局者或欠账人。",
        encoding="utf-8",
    )

    report = evaluate_book_package(tmp_path)
    codes = {issue.code for issue in report.issues}

    assert "PLANNING_ARTIFACT_CANON_LEAK" not in codes
    assert "PLANNING_ARTIFACT_GENRE_DRIFT" not in codes


def test_commercial_gate_flags_stitched_chapter_integrity_defects(tmp_path: Path) -> None:
    _write_package(tmp_path, drift=False)
    (tmp_path / "story-bible" / "cast-and-promises.md").write_text(
        "# Cast\n\n"
        "## 裴镜渊\n\n"
        "功能：旧账名，代表债务过户方法。\n\n"
        "承诺：第 16 章之后可用其名字解释债如何过户。\n\n"
        "禁止：第一卷前段不得作为现场人物抢走十七栋主线。\n",
        encoding="utf-8",
    )
    (tmp_path / "chapter-001.md").write_text(
        "# 第1章 测试\n\n"
        "林渊按下三十三层的按钮，镜子里站着七个人。王老板让他别问。\n\n"
        "镜面忽然裂开，青囊发烫，门外传来三短一长的敲门声。"
        "他用罗盘压住电梯门，又看见倒影比自己先抬手。"
        "铜钱在掌心发黑，青囊只吐出半句账文。\n\n"
        "十一点二十九分，十七栋楼下。\n\n"
        "王老板快步迎上来，指着二十三层那扇亮灯的窗户说：就是那里。\n",
        encoding="utf-8",
    )
    (tmp_path / "chapter-002.md").write_text(
        "# 第2章 测试\n\n"
        "小雨跪在镜前。裴镜渊忽然开口，说否认者先入账。\n",
        encoding="utf-8",
    )
    (tmp_path / "chapter-003.md").write_text(
        "# 第3章 测试\n\n"
        "林渊翻开青囊。\n\n---\n\n"
        "另一段草稿从这里开始，人物和地点都重新进入。\n",
        encoding="utf-8",
    )

    report = evaluate_book_package(tmp_path)
    codes = {issue.code for issue in report.issues}

    assert "CHAPTER_LOCATION_CONFLICT" in codes
    assert "CHAPTER_OPENING_RESET" in codes
    assert "CAST_NAME_EARLY_USE" in codes
    assert "MANUSCRIPT_STITCH_MARKER" in codes


def test_commercial_gate_ignores_material_layer_as_floor_anchor(tmp_path: Path) -> None:
    _write_package(tmp_path, drift=False)
    (tmp_path / "chapter-001.md").write_text(
        "# 第1章 测试\n\n"
        "林渊按下二十三层的按钮，电梯门合上。\n\n"
        "镜子里站着七个人，面目模糊，像隔着一层磨砂玻璃在看。\n",
        encoding="utf-8",
    )

    report = evaluate_book_package(tmp_path)
    scoped_codes = {issue.code for issue in report.issues if issue.chapter_no == 1}

    assert "CHAPTER_LOCATION_CONFLICT" not in scoped_codes


def test_commercial_gate_blocks_opening_reset_in_first_ten_chapters(
    tmp_path: Path,
) -> None:
    _write_package(tmp_path, drift=False)
    (tmp_path / "chapter-008.md").write_text(
        "# 第8章 测试\n\n"
        "林渊蹲在太平间的不锈钢台前，铜钱压住王建业冰冷的手指。"
        "苏婉宁递过镊子时，镜片已经从尸体指缝里露出来。"
        "灯管闪了三下，冷柜深处传来三短一长的敲击声。\n\n"
        "三天前碎玉出现在太平间。今天，镜中的东西在用尸体撬门。\n",
        encoding="utf-8",
    )

    report = evaluate_book_package(tmp_path)
    issue = next(issue for issue in report.issues if issue.code == "CHAPTER_OPENING_RESET")

    assert not report.passed
    assert issue.chapter_no == 8
    assert issue.severity == "high"
    assert report.metrics["blocking_issue_counts"]["CHAPTER_OPENING_RESET"] == 1


def test_commercial_gate_issues_include_full_closure_contract(
    tmp_path: Path,
) -> None:
    _write_package(tmp_path, drift=False)
    (tmp_path / "chapter-008.md").write_text(
        "# 第8章 测试\n\n"
        "林渊蹲在太平间的不锈钢台前，铜钱压住王建业冰冷的手指。"
        "苏婉宁递过镊子时，镜片已经从尸体指缝里露出来。"
        "灯管闪了三下，冷柜深处传来三短一长的敲击声。\n\n"
        "三天前碎玉出现在太平间。今天，镜中的东西在用尸体撬门。\n",
        encoding="utf-8",
    )

    payload = commercial_gate_report_to_dict(evaluate_book_package(tmp_path))
    issue = next(item for item in payload["issues"] if item["code"] == "CHAPTER_OPENING_RESET")

    assert payload["quality_score"] == payload["overall_score"]
    assert payload["passed"] is payload["gate_verdict"]["passed"]
    assert payload["gate_verdict"]["gate_name"] == "commercial_novel_gate"
    assert payload["closure_plan"]["required"] is True
    assert payload["closure_plan"]["blocking_issue_count"] >= 1
    assert "chapter:8" in payload["closure_plan"]["rerun_scopes"]
    assert set(issue["closure"]) == {
        "immediate_repair",
        "recurrence_prevention",
        "verification",
        "rerun_scope",
    }
    assert all(issue["closure"].values())
    assert "前置" in issue["closure"]["recurrence_prevention"]
    assert issue["closure"]["rerun_scope"] == "chapter:8"


def test_commercial_gate_policy_can_relax_medium_blocking_for_diagnostics(
    tmp_path: Path,
) -> None:
    _write_package(tmp_path, drift=False)

    report = evaluate_book_package(
        tmp_path,
        policy=CommercialGatePolicy(
            min_professional_score=75,
            blocking_severities=("critical", "high"),
            anchors=_infer_commercial_anchors({}, ""),
        ),
    )

    assert report.passed


def test_commercial_gate_blocks_outline_asset_gates_when_policy_requires(
    tmp_path: Path,
) -> None:
    _write_package(tmp_path, drift=False)
    (tmp_path / "story-bible" / "prewrite-contract.json").write_text(
        json.dumps(
            {
                "chapters": {
                    "1": {
                        "prewrite_anchor": "接住上一章具体尾钩，推动本章主线。",
                        "chapter_objective": "推动本章剧情发展。",
                        "scene_beats": ["让林渊主动判断并付出代价。"],
                    }
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    report = evaluate_book_package(
        tmp_path,
        policy=CommercialGatePolicy(outline_asset_gates_block_on_failure=True),
    )
    codes = {issue.code for issue in report.issues}

    assert report.passed is False
    assert "PREWRITE_PLACEHOLDER_TEXT" in codes
    issue = next(issue for issue in report.issues if issue.code == "PREWRITE_PLACEHOLDER_TEXT")
    assert issue.evidence["gate_name"] == "prewrite_contract_readiness"


def test_batch_callback_matching_accepts_generic_aliases() -> None:
    # De-hardcoded: callback aliasing now covers genre-neutral term variants
    # (回执↔回执镜片, 临死话↔临死前/遗言) rather than one pilot book's baked
    # character-name aliases (王老板↔王建业 …).
    assert _callback_present("回执", "他手里攥着回执镜片。")
    assert _callback_present("临死话", "那人临死前留了一句话：门不是他开的。")


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
