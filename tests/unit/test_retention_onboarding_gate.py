# ruff: noqa: RUF001

from __future__ import annotations

from pathlib import Path

import pytest

from bestseller.services.retention_onboarding_gate import scan_retention_onboarding_package

pytestmark = pytest.mark.unit


def _write_retention_package(root: Path) -> None:
    (root / "story-bible").mkdir(parents=True)
    (root / "story-bible" / "canonical-terms.yaml").write_text(
        """
terms:
  - term: 林渊
    category: character
    count_onboarding: false
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
  - term: 扣账人
    category: rule
  - term: 三代为一户
    category: rule
""".strip(),
        encoding="utf-8",
    )
    (root / "story-bible" / "reveal-schedule.yaml").write_text(
        """
reveals:
  - id: kou_zhang_ren
    earliest_chapter: 9
    tokens: [扣账人]
  - id: san_dai_wei_yi_hu
    earliest_chapter: 25
    tokens: [三代为一户]
""".strip(),
        encoding="utf-8",
    )


def _write_chapter(root: Path, number: int, body: str) -> None:
    (root / f"chapter-{number:03d}.md").write_text(
        f"# 第{number}章 测试\n\n{body}",
        encoding="utf-8",
    )


def test_retention_gate_blocks_opening_term_overload(tmp_path: Path) -> None:
    _write_retention_package(tmp_path)
    _write_chapter(
        tmp_path,
        1,
        "23:45，林渊进十七栋。王建业递钥匙，罗盘疯转，康熙铜钱裂开，阴阳眼看见302。",
    )
    _write_chapter(tmp_path, 2, "23:48，张建军敲门。章尾：林渊握紧罗盘，302门缝渗出血。")

    verdict = scan_retention_onboarding_package(tmp_path)
    codes = {finding.code for finding in verdict.findings}

    assert verdict.verdict == "blocked"
    assert "ONBOARDING_OVERLOAD" in codes


def test_retention_gate_flags_backwards_clock(tmp_path: Path) -> None:
    _write_retention_package(tmp_path)
    _write_chapter(tmp_path, 1, "23:47，林渊进门。章尾：王建业把罗盘塞给他。")
    _write_chapter(tmp_path, 2, "21:30，张建军还在楼下。章尾：十七栋的302亮灯。")

    verdict = scan_retention_onboarding_package(tmp_path)
    issue = next(finding for finding in verdict.findings if finding.code == "TIME_ANCHOR_BACKWARDS")

    assert verdict.verdict == "blocked"
    assert issue.path == "chapter-002.md"


def test_retention_gate_flags_premature_reveal(tmp_path: Path) -> None:
    _write_retention_package(tmp_path)
    _write_chapter(tmp_path, 1, "23:45，林渊进十七栋。章尾：罗盘指向302。")
    _write_chapter(tmp_path, 3, "23:50，扣账人写下三代为一户。章尾：林渊看见302里有人。")

    verdict = scan_retention_onboarding_package(tmp_path)
    premature = [finding for finding in verdict.findings if finding.code == "PREMATURE_REVEAL"]

    assert len(premature) == 2
    assert {finding.path for finding in premature} == {"chapter-003.md"}


def test_retention_gate_flags_abstract_hook(tmp_path: Path) -> None:
    _write_retention_package(tmp_path)
    _write_chapter(
        tmp_path,
        1,
        "23:45，林渊进十七栋。"
        + "潮气在墙缝里反复渗出，脚步声越来越轻。" * 12
        + "最后，他忽然觉得一切都不会结束。",
    )

    verdict = scan_retention_onboarding_package(tmp_path)
    codes = {finding.code for finding in verdict.findings}

    assert "HOOK_TOO_ABSTRACT" in codes


def test_retention_gate_skips_without_story_bible_configs(tmp_path: Path) -> None:
    _write_chapter(tmp_path, 1, "23:45，林渊进门。")

    verdict = scan_retention_onboarding_package(tmp_path)

    assert verdict.verdict == "not_run"
    assert verdict.required is False
