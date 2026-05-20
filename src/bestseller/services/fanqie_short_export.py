"""番茄短故事单篇导出与签约就绪报告。"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from bestseller.domain.fanqie_short import DEFAULT_UNLOCK_LINE_RATIO, DEFAULT_SIGNING_TARGET_UNLOCKS
from bestseller.services.drafts import count_words
from bestseller.services.fanqie_short_opening_gate import (
    evaluate_fanqie_short_opening_gate,
    scan_fanqie_short_taboo_signals,
)
from bestseller.services.fanqie_short_ranking_gate import evaluate_fanqie_ranking_readiness


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
    ranking = evaluate_fanqie_ranking_readiness(
        full_text,
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
        "taboo_signals": taboo,
        "signing_target_unlocks": DEFAULT_SIGNING_TARGET_UNLOCKS,
        "ready_for_upload": opening.passed and ranking.passed and not taboo and word_delta_pct <= 15.0,
    }


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
    clean_text = re.sub(
        r"\n{0,2}<!--\s*UNLOCK_LINE:.*?-->\s*\n{0,2}",
        "\n\n",
        full_text.strip(),
        flags=re.DOTALL,
    )
    clean_text = re.sub(r"(?m)^\s*---+\s*$\n?", "\n", clean_text)
    clean_text = re.sub(r"\n{3,}", "\n\n", clean_text).strip()
    body = header + clean_text

    md_path = exports_dir / "fanqie-short.md"
    md_path.write_text(body, encoding="utf-8")

    readiness = build_signing_readiness_report(
        full_text,
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
