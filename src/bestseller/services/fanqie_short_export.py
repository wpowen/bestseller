# ruff: noqa: RUF001, RUF002
"""番茄短故事单篇导出与签约就绪报告。"""

from __future__ import annotations

from collections.abc import Mapping
import json
from pathlib import Path
import re
from typing import Any

from bestseller.domain.fanqie_short import DEFAULT_SIGNING_TARGET_UNLOCKS, DEFAULT_UNLOCK_LINE_RATIO
from bestseller.services.drafts import count_words
from bestseller.services.fanqie_short_gate_v2 import evaluate_fanqie_short_v2_readiness
from bestseller.services.fanqie_short_opening_gate import (
    evaluate_fanqie_short_opening_gate,
    scan_fanqie_short_taboo_signals,
)
from bestseller.services.fanqie_short_ranking_gate import evaluate_fanqie_core_ranking_readiness


def insert_unlock_line_marker(
    full_text: str,
    *,
    unlock_line_ratio: float = DEFAULT_UNLOCK_LINE_RATIO,
) -> tuple[str, int]:
    """在约 ``unlock_line_ratio`` 处插入解锁线标记，返回 (新文本, 字符位置)。"""
    text = full_text.strip()
    if not text:
        return text, 0
    total = len(text)
    position = min(total - 1, max(0, int(total * unlock_line_ratio)))
    # 尽量落在段落边界
    newline_pos = text.find("\n\n", position)
    if newline_pos != -1 and newline_pos < total - 1:
        position = newline_pos
    marker = (
        "\n\n---\n"
        f"<!-- UNLOCK_LINE: {int(unlock_line_ratio * 100)}% · 番茄短故事免费段截止 -->\n"
        "---\n\n"
    )
    return text[:position] + marker + text[position:], position


def build_signing_readiness_report(
    full_text: str,
    *,
    title: str | None = None,
    unlock_line_ratio: float = DEFAULT_UNLOCK_LINE_RATIO,
    protagonist_name: str | None = None,
    target_word_count: int | None = None,
) -> dict[str, Any]:
    total_words = count_words(full_text)
    opening = evaluate_fanqie_short_opening_gate(
        full_text,
        unlock_line_ratio=unlock_line_ratio,
        protagonist_name=protagonist_name,
    )
    ranking = evaluate_fanqie_core_ranking_readiness(
        full_text,
        unlock_line_ratio=unlock_line_ratio,
        protagonist_name=protagonist_name,
    )
    short_v2 = evaluate_fanqie_short_v2_readiness(
        full_text,
        title=title,
        unlock_line_ratio=unlock_line_ratio,
        protagonist_name=protagonist_name,
    )
    taboo = scan_fanqie_short_taboo_signals(full_text)
    target = target_word_count or total_words
    word_delta_pct = (
        abs(total_words - target) / target * 100.0 if target > 0 else 0.0
    )
    return {
        "platform": "tomato_short",
        "total_words": total_words,
        "target_word_count": target,
        "word_count_within_10pct": word_delta_pct <= 10.0,
        "unlock_line_ratio": unlock_line_ratio,
        "unlock_zone_words": opening.unlock_zone_words,
        "opening_gate_passed": opening.passed,
        "opening_findings": opening.to_dict()["findings"],
        "ranking_gate_passed": ranking.passed,
        "ranking_findings": ranking.to_dict()["findings"],
        "short_v2_gate_passed": short_v2.passed,
        "short_v2_findings": short_v2.to_dict()["findings"],
        "taboo_signals": taboo,
        "signing_target_unlocks": DEFAULT_SIGNING_TARGET_UNLOCKS,
        "ready_for_upload": (
            opening.passed
            and ranking.passed
            and short_v2.passed
            and not taboo
            and word_delta_pct <= 15.0
        ),
    }


def _clean_fanqie_short_body(full_text: str) -> str:
    clean_text = re.sub(
        r"\n{0,2}<!--\s*UNLOCK_LINE:.*?-->\s*\n{0,2}",
        "\n\n",
        full_text.strip(),
        flags=re.DOTALL,
    )
    clean_text = re.sub(r"(?m)^\s*---+\s*$\n?", "\n", clean_text)
    return re.sub(r"\n{3,}", "\n\n", clean_text).strip()


def export_fanqie_short_markdown(
    output_dir: Path,
    *,
    title: str,
    genre: str,
    full_text: str,
    unlock_line_ratio: float = DEFAULT_UNLOCK_LINE_RATIO,
    protagonist_name: str | None = None,
    target_word_count: int | None = None,
) -> dict[str, str]:
    """写入 ``exports/fanqie-short.md`` 与 ``exports/signing-readiness.json``。"""
    exports_dir = output_dir / "exports"
    exports_dir.mkdir(parents=True, exist_ok=True)

    _marked_text, unlock_pos = insert_unlock_line_marker(
        full_text, unlock_line_ratio=unlock_line_ratio
    )
    header = f"# {title}\n\n"
    body = header + _clean_fanqie_short_body(full_text)

    md_path = exports_dir / "fanqie-short.md"
    md_path.write_text(body, encoding="utf-8")

    readiness = build_signing_readiness_report(
        full_text,
        title=title,
        unlock_line_ratio=unlock_line_ratio,
        protagonist_name=protagonist_name,
        target_word_count=target_word_count,
    )
    readiness["unlock_line_char_position"] = unlock_pos
    json_path = exports_dir / "signing-readiness.json"
    json_path.write_text(
        json.dumps(readiness, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return {
        "markdown_path": str(md_path.resolve()),
        "readiness_path": str(json_path.resolve()),
    }


def export_fanqie_short_rejected_draft(
    output_dir: Path,
    *,
    title: str,
    genre: str,
    full_text: str,
    review_report: Mapping[str, Any],
    unlock_line_ratio: float = DEFAULT_UNLOCK_LINE_RATIO,
    protagonist_name: str | None = None,
    target_word_count: int | None = None,
) -> dict[str, str]:
    """Write a non-uploadable audit copy when the whole-piece gates fail."""
    rejected_dir = output_dir / "rejected-drafts"
    rejected_dir.mkdir(parents=True, exist_ok=True)

    readiness = build_signing_readiness_report(
        full_text,
        title=title,
        unlock_line_ratio=unlock_line_ratio,
        protagonist_name=protagonist_name,
        target_word_count=target_word_count,
    )
    readiness["ready_for_upload"] = False

    md_path = rejected_dir / "fanqie-short.rejected.md"
    md_path.write_text(
        "\n".join(
            [
                f"# {title}",
                "",
                "> 状态：未通过番茄短故事榜单级门禁，不得作为上传稿使用。",
                "",
                f"- genre: {genre}",
                "- ready_for_upload: false",
                f"- total_words: {readiness['total_words']}",
                "",
                "## 正文",
                "",
                _clean_fanqie_short_body(full_text),
                "",
            ]
        ),
        encoding="utf-8",
    )

    report_path = rejected_dir / "rejection-report.json"
    report_path.write_text(
        json.dumps(
            {
                "status": "rejected",
                "ready_for_upload": False,
                "readiness": readiness,
                "review_report": dict(review_report),
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )
    return {
        "rejected_markdown_path": str(md_path.resolve()),
        "rejection_report_path": str(report_path.resolve()),
    }
