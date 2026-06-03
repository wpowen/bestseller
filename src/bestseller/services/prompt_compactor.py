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


# Craft-theory / metadata / reference-dump sections that belong to the planner
# and critic stages, not the writer's generation prompt. The writer still needs
# the compact execution core (distilled strategy, emotion kernel, public emotion,
# and event-unit contract), so those sections are intentionally preserved.
# Matched against the leading text of each top-level section; dropped in lean
# mode.
_LEAN_STRIP_MARKERS: tuple[str, ...] = (
    "【方法论 lineage",
    "## 文化原型",
    "文化原型 [cultural_archetypes",
    "### scene_templates",
    "### locale_templates",
    "### character_templates",
    "### power_systems",
    "### device_templates",
    "### world_settings",
    # Material-Forge reference catalogs (§slug dumps). The writer already gets a
    # short "素材锚点 §slug" pointer list; the full catalogs are reference, not
    # prose instructions.
    "### factions",
    "### character_archetypes",
    "### plot_patterns",
    "### thematic_motifs",
    "### emotion_arcs",
    "### dialogue_styles",
    "### anti_cliche_patterns",
    "### real_world_references",
    "## 风格参照 [reference_corpora",
    "# Reference corpus",
)

# A new top-level section begins at a line starting with 【, a markdown header
# (#, ##, ###), or a === fence.
_SECTION_BOUNDARY = re.compile(r"(?=\n【)|(?=\n#{1,3} )|(?=\n=== )")

# Only dedupe / strip substantial sections so short repeated tags (【语言】…) are
# never touched.
_MIN_SECTION_CHARS = 200


def _split_sections(text: str) -> list[str]:
    return _SECTION_BOUNDARY.split(text)


def _strip_meta_sections(text: str) -> str:
    kept: list[str] = []
    for part in _split_sections(text):
        head = part.lstrip()[:60]
        if any(marker in head for marker in _LEAN_STRIP_MARKERS):
            continue
        kept.append(part)
    return "".join(kept)


def _dedupe_repeated_sections(text: str) -> str:
    """Drop verbatim-duplicate sections (e.g. 题材方法论 / choreography contracts
    that get injected twice). Exact-match only — zero risk of over-stripping."""

    seen: set[str] = set()
    kept: list[str] = []
    for part in _split_sections(text):
        key = part.strip()
        if len(key) >= _MIN_SECTION_CHARS:
            if key in seen:
                continue
            seen.add(key)
        kept.append(part)
    return "".join(kept)


def compact_user_prompt(
    raw_user_prompt: str,
    *,
    chapter_no: int,
    forbidden_terms_full: list[str],
    lean: bool = True,
) -> tuple[str, CompactionReport]:
    """Return a compacted user prompt plus a small savings report.

    When ``lean`` is set, abstract craft-theory / metadata / reference-dump
    sections are stripped and verbatim-duplicate sections collapsed so the
    scene-writer prompt stays focused on *what to write* rather than *writing
    theory*.
    """

    original = raw_user_prompt or ""
    compacted = original
    if lean:
        compacted = _dedupe_repeated_sections(compacted)
        compacted = _strip_meta_sections(compacted)
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
