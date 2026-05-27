"""Prompt compaction helpers for scene-writer user prompts."""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any


@dataclass(frozen=True)
class CompactionReport:
    original_chars: int
    compacted_chars: int
    saved_tokens_estimate: int


def compact_user_prompt(
    raw_user_prompt: str,
    *,
    chapter_no: int,
    forbidden_terms_full: list[str],
) -> tuple[str, CompactionReport]:
    """Return a compacted user prompt plus a small savings report."""

    original = raw_user_prompt or ""
    compacted = original
    compacted = _dedupe_chapter_contract_digest_blocks(compacted)
    compacted = _slice_forbidden_terms(compacted, chapter_no, forbidden_terms_full)
    compacted = _wrap_retention_findings(compacted)
    compacted = _collapse_blank_lines(compacted)
    report = CompactionReport(
        original_chars=len(original),
        compacted_chars=len(compacted),
        saved_tokens_estimate=max(0, _estimate_tokens(original) - _estimate_tokens(compacted)),
    )
    return compacted, report


def _dedupe_chapter_contract_digest_blocks(text: str) -> str:
    seen = False

    def replace(match: re.Match[str]) -> str:
        nonlocal seen
        block = match.group(0)
        if not seen:
            seen = True
            return _compact_json_payload(block)
        return '"chapter_contract_digest": "see: first chapter_contract_digest above"'

    return re.sub(
        r'"chapter_contract_digest"\s*:\s*(?:\{(?:[^{}]|\{[^{}]*\})*\}|\[[^\]]*\]|"[^"]*")',
        replace,
        text,
        flags=re.S,
    )


def _compact_json_payload(block: str) -> str:
    key, _, raw_value = block.partition(":")
    value = raw_value.strip().rstrip(",")
    try:
        parsed: Any = json.loads(value)
    except json.JSONDecodeError:
        return block
    compact = json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))
    suffix = "," if block.rstrip().endswith(",") else ""
    return f"{key}:{compact}{suffix}"


def _slice_forbidden_terms(text: str, chapter_no: int, forbidden_terms_full: list[str]) -> str:
    if not forbidden_terms_full:
        return text
    allowed = _terms_for_chapter(chapter_no, forbidden_terms_full)
    if len(allowed) >= len(forbidden_terms_full):
        return text
    replacement = "forbidden_early_leaks_active=" + json.dumps(
        allowed,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    patterns = (
        r"forbidden_early_leaks_archived[^:\n]*:\s*\[[^\]]*\]",
        r'"forbidden_early_leaks(?:_archived)?"\s*:\s*\[[^\]]*\]',
    )
    compacted = text
    for pattern in patterns:
        compacted = re.sub(pattern, replacement, compacted, flags=re.S)
    return compacted


def _terms_for_chapter(chapter_no: int, terms: list[str]) -> list[str]:
    if chapter_no <= 10:
        keep = {
            "玩家",
            "副本",
            "通关",
            "系统",
            "困魂镜",
            "母镜",
            "源门",
            "扣账人",
            "林正淳",
            "林家辉",
            "林远山",
            "三代以内",
            "血债血偿",
        }
        return [term for term in terms if term in keep]
    return [term for term in terms if term in {"玩家", "副本", "通关", "系统"}]


def _wrap_retention_findings(text: str) -> str:
    marker = "retention_gate_last_findings"
    if marker not in text or "<REPAIR_HINT>" in text:
        return text
    pattern = re.compile(
        r"(?P<block>(?:^|\n).{0,80}retention_gate_last_findings.{0,4000}?)(?=\n\S|$)",
        flags=re.S,
    )

    def replace(match: re.Match[str]) -> str:
        block = match.group("block").strip()
        return f"\n<REPAIR_HINT>\n{block}\n</REPAIR_HINT>\n"

    return pattern.sub(replace, text, count=1)


def _collapse_blank_lines(text: str) -> str:
    return re.sub(r"\n{4,}", "\n\n\n", text).strip()


def _estimate_tokens(text: str) -> int:
    if not text:
        return 0
    han = len(re.findall(r"[\u4e00-\u9fff]", text))
    latin = len(re.findall(r"[A-Za-z0-9_]+", text))
    punct = len(re.findall(r"[^\w\s]", text))
    return han + int(latin * 1.3) + int(punct * 0.5)


__all__ = ["CompactionReport", "compact_user_prompt"]
