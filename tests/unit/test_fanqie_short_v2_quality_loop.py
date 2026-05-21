# ruff: noqa: RUF001
"""Fanqie short-story v2 quality-loop tests."""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from bestseller.services.fanqie_short_emotion_bank import (
    load_public_emotion_cards,
    render_emotion_stack_prompt_block,
    select_emotion_stack,
)
from bestseller.services.fanqie_short_export import (
    build_signing_readiness_report,
    export_fanqie_short_rejected_draft,
)
from bestseller.services.fanqie_short_gate_v2 import (
    build_fanqie_short_v2_rewrite_instructions,
    build_fanqie_short_v2_rewrite_routes,
    evaluate_fanqie_short_v2_readiness,
    evaluate_fanqie_short_v2_title_gate,
    evaluate_reader_retention_gate,
)
from bestseller.services.fanqie_short_pipeline import (
    _short_v2_finding_chapter_numbers,
    _short_v2_rewrite_strategy,
)
from bestseller.services.fanqie_short_ranking_gate import FanqieRankingFinding
from bestseller.services.fanqie_short_resource_adapter import (
    adapt_long_form_resources_for_short,
    render_short_resource_prompt_block,
)

pytestmark = pytest.mark.unit


def test_emotion_bank_has_built_in_social_depth() -> None:
    cards = load_public_emotion_cards()

    assert len(cards) >= 100
    categories = {card.category for card in cards}
    assert {"职场", "家庭", "爱情", "喜剧", "催泪"}.issubset(categories)
    assert all(card.risk_boundary for card in cards)


def test_select_emotion_stack_renders_prompt_block() -> None:
    stack = select_emotion_stack(
        premise="我被主管逼签责任书，父亲手术费只剩最后一小时。",
        genre="都市现实逆袭",
        tone_preferences=["爽文", "催泪"],
    )
    block = render_emotion_stack_prompt_block(stack)

    assert "【短篇社会情绪栈】" in block
    assert stack.primary.category in block
    assert "安全边界" in block


def test_resource_adapter_reuses_long_form_assets_without_serial_language() -> None:
    cards = adapt_long_form_resources_for_short(
        premise="陆砚用非侵入式脑机接口突破行业围剿。",
        book_spec={
            "core_promise": "全书主线是技术理想对抗资本围剿。",
            "series_engine": {
                "chapter_arc": "第1章技术亮相→第2章初创危机→第3章资本反扑",
                "payoff_rhythm": "每章兑现一次技术反击。",
            },
            "golden_finger": {"name": "情绪算力", "rule": "能力有反噬和冷却。"},
        },
        cast_spec={
            "protagonist": {"name": "陆砚", "core_wound": "被资本污名化为骗子"},
            "antagonist": {"name": "郑鸿朗", "pressure_method": "公开压价收购"},
        },
    )
    block = render_short_resource_prompt_block(cards)

    assert "陆砚" in block
    assert "郑鸿朗" in block
    assert "情绪算力" in block
    assert "第1章" not in block
    assert "全书" not in block
    assert "单篇" not in block or "不继承长篇" in block


def test_v2_title_gate_blocks_abstract_setting_title() -> None:
    report = evaluate_fanqie_short_v2_title_gate("器语者")

    assert not report.passed
    assert {finding.code for finding in report.findings} == {"title_abstract_setting_only"}


def test_reader_retention_gate_accepts_fast_social_pressure_opening() -> None:
    text = (
        "我被主管按在会议桌前，逼我签下挪用公款的认罪书。父亲手术费只剩最后一小时，"
        "他却把伪造证据推到我脸上。我反手夺过录音笔，当众放出他威胁财务的原声，"
        "全场安静，他第一次露馅。"
    )
    report = evaluate_reader_retention_gate(text, protagonist_name="我")

    assert report.passed


def test_v2_readiness_blocks_qiyuzhe_style_longform_opening() -> None:
    text = (
        "昨夜子时，寄渊阁的古琴霜钟失语了。多年以前，器灵制度由此建立，"
        "世界观由此展开。第一章要先建立阁中来历，下一章再揭开真正的真相。"
    )
    report = evaluate_fanqie_short_v2_readiness(text, title="器语者", protagonist_name="我")
    codes = {finding.code for finding in report.findings if finding.severity == "critical"}

    assert not report.passed
    assert "title_abstract_setting_only" in codes
    assert "first_screen_protagonist_missing" in codes
    assert "social_resonance_missing" in codes
    assert "longform_contamination" in codes


def test_export_readiness_requires_v2_title_gate() -> None:
    text = (
        "我被主管按在会议桌前，逼我签下挪用公款的认罪书。父亲手术费只剩最后一小时，"
        "他却把伪造证据推到我脸上。我反手夺过录音笔，当众放出他威胁财务的原声，"
        "全场安静，他第一次露馅。\n\n"
        "我继续追查转账记录，拿到第二份证据，公开反制他的局。最终真相大白，"
        "主管认罪，父亲获救，我离开公司，故事在这里收场。"
    )

    report = build_signing_readiness_report(
        text,
        title="器语者",
        protagonist_name="我",
        target_word_count=len(text),
    )
    codes = {
        finding["code"]
        for finding in report["short_v2_findings"]
        if finding["severity"] == "critical"
    }

    assert report["short_v2_gate_passed"] is False
    assert report["ready_for_upload"] is False
    assert "title_abstract_setting_only" in codes


def test_v2_rewrite_routes_map_failures_to_workers() -> None:
    report = evaluate_fanqie_short_v2_readiness(
        "清晨的阳光落在窗台上，世界观由此展开。",
        title="器语者",
        protagonist_name="我",
    )
    routes = build_fanqie_short_v2_rewrite_routes(report)
    workers = {route.worker for route in routes}

    assert "TitleWorker" in workers
    assert "OpeningContractWorker" in workers
    assert "ResourceAdapterWorker" in workers

    instructions = build_fanqie_short_v2_rewrite_instructions(report)
    assert "番茄短故事 v2 门禁未过" in instructions
    assert "TitleWorker" in instructions


def test_v2_rewrite_scope_maps_findings_to_repairable_segments() -> None:
    opening = FanqieRankingFinding(
        code="first_screen_pressure_missing",
        severity="critical",
        message="开屏缺压迫",
        evidence="",
        phase="opening",
        target="opening_80",
    )
    payoff = FanqieRankingFinding(
        code="payoff_density_too_low",
        severity="critical",
        message="回报点不足",
        evidence="",
        phase="payoff",
        target="whole_story",
    )

    assert _short_v2_finding_chapter_numbers(
        opening, segment_total=12, unlock_segment=4
    ) == [1]
    assert _short_v2_finding_chapter_numbers(
        payoff, segment_total=12, unlock_segment=4
    ) == list(range(1, 13))
    assert len(_short_v2_rewrite_strategy("payoff_density_too_low")) <= 64


def test_rejected_draft_export_is_not_upload_package() -> None:
    text = (
        "昨夜子时，寄渊阁的古琴霜钟失语了。多年以前，器灵制度由此建立，"
        "世界观由此展开。第一章先铺设定，下一章再揭真相。"
    )

    with TemporaryDirectory() as tmp_dir:
        root = Path(tmp_dir)
        paths = export_fanqie_short_rejected_draft(
            root,
            title="器语者",
            genre="奇幻",
            full_text=text,
            review_report={"passed": False, "notes": ["v2 gate failed"]},
            protagonist_name="我",
            target_word_count=len(text),
        )

        assert not (root / "exports" / "fanqie-short.md").exists()
        assert "rejected-drafts" in paths["rejected_markdown_path"]

        rejected_md = Path(paths["rejected_markdown_path"]).read_text(encoding="utf-8")
        report = json.loads(
            Path(paths["rejection_report_path"]).read_text(encoding="utf-8")
        )

    assert "不得作为上传稿使用" in rejected_md
    assert report["ready_for_upload"] is False
    assert report["readiness"]["ready_for_upload"] is False
