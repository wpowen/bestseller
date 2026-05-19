# ruff: noqa: RUF001
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from bestseller.services.category_hard_engines import evaluate_category_hard_engine
from bestseller.services.legacy_book_state_bootstrap import (
    LegacyChapterSummary,
    build_legacy_state_bootstrap_payload,
    _legacy_hype_assignment,
    _normalized_legacy_hype_intensity,
    parse_markdown_tables,
)
from bestseller.services.premium_book_gate import evaluate_premium_project_readiness
from bestseller.services.premium_state_ledger import validate_premium_state_ledger

pytestmark = pytest.mark.unit


def _legacy_project() -> SimpleNamespace:
    return SimpleNamespace(
        slug="legacy-suspense",
        genre="惊悚灵异",
        sub_genre="驱魔探案综合",
        metadata_json={
            "category_key": "default",
            "book_spec": {"genre": "惊悚灵异", "sub_genre": "驱魔探案综合"},
            "world_spec": {
                "factions": [
                    {
                        "name": "现实调查压力",
                        "goal": "用证据链压缩主角行动窗口",
                        "relationship_to_protagonist": "怀疑但必须合作",
                    }
                ]
            },
            "cast_spec": {
                "protagonist": {"name": "林渊"},
                "supporting_cast": [{"name": "苏婉宁"}],
            },
            "premium_state_ledger_report": {
                "passed": False,
                "findings": [
                    {
                        "code": "relationship_active_choice_missing",
                        "severity": "critical",
                        "path": "relationship_events[0]",
                    }
                ],
            },
            "premium_state_snapshot": {"passed": False, "blocking_findings": []},
        },
    )


def _chapter_rows() -> tuple[LegacyChapterSummary, ...]:
    return (
        LegacyChapterSummary(chapter_number=1, title="十五分钟凶宅"),
        LegacyChapterSummary(chapter_number=2, title="第一名否认者"),
        LegacyChapterSummary(chapter_number=3, title="旧门回声"),
    )


def _story_bible_tables() -> dict[str, list[dict[str, str]]]:
    return {
        "rule_ledger": [
            {
                "ID": "R-001",
                "规则": "否认者先入账，认账者可活",
                "首次出现": "第 2 章",
                "可见效果": "受害者因否认关键事实被规则追缴",
                "破局方法": "逼出当事人真正否认的事实",
                "代价/反噬": "承认只暂缓吞没，不抹除罪责",
                "后续用法": "每个受害者真相必须绑定认账",
            }
        ],
        "clue_ledger": [
            {
                "ID": "C-001",
                "线索": "匿名镜片",
                "投放章节": "第 1 章",
                "表面解释": "恐吓道具",
                "真正指向": "规则提前筛选入局者",
                "回收计划": "以寄件链路追出开门人",
            }
        ],
        "event_state_ledger": [
            {
                "章末": "第 2 章",
                "事件/人物": "第一名否认者",
                "当前状态": "已入账",
                "下一章只能怎么续": "查其真正否认的事实",
                "禁止回滚": "不得写成尚未入局",
            }
        ],
    }


def test_parse_markdown_tables_reads_story_bible_table() -> None:
    markdown = """# Rule Ledger

| ID | 规则 | 首次出现 |
| --- | --- | --- |
| R-001 | 规则一 | 第 1 章 |
"""

    tables = parse_markdown_tables(markdown)

    assert tables == [[{"ID": "R-001", "规则": "规则一", "首次出现": "第 1 章"}]]


def test_legacy_suspense_bootstrap_repairs_premium_and_category_gates() -> None:
    project = _legacy_project()
    metadata, report = build_legacy_state_bootstrap_payload(
        project,
        chapter_rows=_chapter_rows(),
        story_bible_tables=_story_bible_tables(),
    )

    assert report.category_key == "suspense-mystery"
    assert report.premium_gate_before_passed is False
    assert report.premium_gate_after_passed is True
    assert metadata["category_key"] == "suspense-mystery"
    assert metadata["canonical_category"] == "suspense-mystery"

    premium_report = evaluate_premium_project_readiness(
        metadata,
        genre="惊悚灵异",
        sub_genre="驱魔探案综合",
    )
    category_report = evaluate_category_hard_engine(
        metadata,
        category_key="suspense-mystery",
    )

    assert premium_report.passed is True
    assert category_report.passed is True
    assert set(category_report.present_state_ledgers) == {
        "rule_lattice",
        "clue_chain",
        "evidence_ledger",
        "suspect_timeline",
        "red_herring_ledger",
    }


def test_bootstrap_sanitizes_invalid_legacy_ledger_report() -> None:
    metadata, _ = build_legacy_state_bootstrap_payload(
        _legacy_project(),
        chapter_rows=_chapter_rows(),
        story_bible_tables=_story_bible_tables(),
    )

    ledger_report = validate_premium_state_ledger(metadata["premium_state_ledger"])  # type: ignore[arg-type]

    assert ledger_report.passed is True
    assert metadata["premium_state_ledger_report"]["passed"] is True  # type: ignore[index]
    assert metadata["premium_state_snapshot"]["passed"] is True  # type: ignore[index]
    assert metadata["premium_state_snapshot"]["legacy_bootstrap"]["validation_status"] == (  # type: ignore[index]
        "needs_live_validation"
    )


def test_bootstrap_payload_does_not_store_chapter_prose() -> None:
    raw_prose = "这是一段原章节正文，不应该进入历史状态引导产物。"
    metadata, _ = build_legacy_state_bootstrap_payload(
        _legacy_project(),
        chapter_rows=_chapter_rows(),
        story_bible_tables=_story_bible_tables(),
    )

    def walk(value: Any) -> list[str]:
        if isinstance(value, str):
            return [value]
        if isinstance(value, dict):
            result: list[str] = []
            for item in value.values():
                result.extend(walk(item))
            return result
        if isinstance(value, list | tuple):
            result = []
            for item in value:
                result.extend(walk(item))
            return result
        return []

    assert raw_prose not in "\n".join(walk(metadata))


def test_bootstrap_payload_injects_distilled_strategy_metadata() -> None:
    project = _legacy_project()
    metadata, report = build_legacy_state_bootstrap_payload(
        project,
        chapter_rows=_chapter_rows(),
        story_bible_tables=_story_bible_tables(),
    )

    assert report.notes
    assert any("distilled_payload" in str(item) for item in report.notes)
    assert metadata["distilled_strategy_expected"] is True  # type: ignore[index]
    assert isinstance(metadata["distilled_strategy_card"], dict)  # type: ignore[index]
    assert isinstance(metadata["distilled_strategy_blocks"], dict)  # type: ignore[index]
    assert isinstance(metadata["distilled_design_reference_blocks"], dict)  # type: ignore[index]
    assert isinstance(metadata["character_strategy"], dict)  # type: ignore[index]
    assert (
        metadata.get("distilled_strategy_card") is not None  # type: ignore[comparison-overlap]
    )


def test_bootstrap_payload_handles_distilled_payload_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    from bestseller.services import distilled_strategy_compiler as distilled_compiler

    def _null_strategy_card(**_: object) -> None:
        return None

    monkeypatch.setattr(
        distilled_compiler,
        "compile_distilled_strategy_card",
        _null_strategy_card,
    )

    metadata, report = build_legacy_state_bootstrap_payload(
        _legacy_project(),
        chapter_rows=_chapter_rows(),
        story_bible_tables=_story_bible_tables(),
    )

    assert "distilled_strategy_unavailable" in report.notes
    assert metadata.get("distilled_strategy_expected") is None or metadata.get("distilled_strategy_expected") is False


def test_legacy_hype_assignment_uses_category_default_when_classifier_is_empty() -> None:
    hype_type, intensity, source = _legacy_hype_assignment(
        text="他走进屋里，看见桌上一张纸。",
        language="zh-CN",
        category_key="suspense-mystery",
        chapter_number=22,
        target_chapters=500,
    )

    assert hype_type in {"reversal", "power_reveal", "counterattack", "underdog_win"}
    assert intensity >= 7.0
    assert source == "target_curve"


def test_legacy_hype_intensity_normalizes_legacy_zero_to_one_scale() -> None:
    normalized = _normalized_legacy_hype_intensity(
        0.2,
        chapter_number=12,
        target_chapters=500,
    )

    assert normalized == 7.5
