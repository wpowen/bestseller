"""Cross-chapter duplicate / template detection for assembled chapters."""

from __future__ import annotations

from dataclasses import dataclass
import re
from difflib import SequenceMatcher
from typing import Final

# Emit the framework's canonical repair codes so existing playbooks
# (quality_repair_playbooks), contracts (quality_contract_registry) and
# export handling apply without duplication. The local names are kept as
# stable aliases for callers importing them.
CHAPTER_OPENING_DUPLICATE: Final[str] = "CHAPTER_OPENING_REPETITION"
CHAPTER_BODY_TEMPLATE_REPEAT: Final[str] = "CROSS_CHAPTER_REPETITION"

_PARAGRAPH_SPLIT_RE = re.compile(r"\n\s*\n+")


@dataclass(frozen=True)
class ChapterDuplicateFinding:
    severity: str
    code: str
    detail: str
    similarity: float
    evidence: dict[str, str]


@dataclass(frozen=True)
class ChapterDuplicateReport:
    chapter_position: int
    findings: tuple[ChapterDuplicateFinding, ...]

    @property
    def passed(self) -> bool:
        return not self.findings

    @property
    def has_critical(self) -> bool:
        return any(f.severity == "critical" for f in self.findings)


def _normalize_paragraph(text: str) -> str:
    return re.sub(r"\s+", "", (text or "").strip())


def _paragraphs(text: str, *, min_chars: int = 40) -> list[str]:
    chunks = [_normalize_paragraph(p) for p in _PARAGRAPH_SPLIT_RE.split(text or "")]
    return [p for p in chunks if len(p) >= min_chars]


def _opening_fingerprint(text: str, *, chars: int = 280) -> str:
    body = (text or "").strip()
    return _normalize_paragraph(body[:chars])


def check_chapter_duplicates(
    *,
    chapter_position: int,
    chapter_text: str,
    prev_chapter_text: str | None = None,
    opening_similarity_threshold: float = 0.82,
    body_similarity_threshold: float = 0.88,
    opening_chars: int = 280,
) -> ChapterDuplicateReport:
    """Flag template reuse across consecutive chapters."""

    findings: list[ChapterDuplicateFinding] = []
    if chapter_position < 2 or not prev_chapter_text:
        return ChapterDuplicateReport(chapter_position=chapter_position, findings=())

    cur_open = _opening_fingerprint(chapter_text, chars=opening_chars)
    prev_open = _opening_fingerprint(prev_chapter_text, chars=opening_chars)
    if cur_open and prev_open:
        open_ratio = SequenceMatcher(None, cur_open, prev_open).ratio()
        if open_ratio >= opening_similarity_threshold:
            findings.append(
                ChapterDuplicateFinding(
                    severity="critical",
                    code=CHAPTER_OPENING_DUPLICATE,
                    detail=(
                        f"opening fingerprint {open_ratio:.2f} similar to previous chapter "
                        f"(threshold {opening_similarity_threshold})"
                    ),
                    similarity=open_ratio,
                    evidence={
                        "opening_sample": cur_open[:120],
                        "prev_opening_sample": prev_open[:120],
                    },
                )
            )

    cur_paras = _paragraphs(chapter_text)
    prev_paras = _paragraphs(prev_chapter_text)
    if cur_paras and prev_paras:
        best = 0.0
        best_pair = ("", "")
        for cur in cur_paras[:8]:
            for prev in prev_paras[:8]:
                ratio = SequenceMatcher(None, cur, prev).ratio()
                if ratio > best:
                    best = ratio
                    best_pair = (cur[:80], prev[:80])
        if best >= body_similarity_threshold:
            findings.append(
                ChapterDuplicateFinding(
                    severity="critical",
                    code=CHAPTER_BODY_TEMPLATE_REPEAT,
                    detail=(
                        f"paragraph similarity {best:.2f} vs previous chapter "
                        f"(threshold {body_similarity_threshold})"
                    ),
                    similarity=best,
                    evidence={
                        "current_para": best_pair[0],
                        "prev_para": best_pair[1],
                    },
                )
            )

    return ChapterDuplicateReport(
        chapter_position=chapter_position,
        findings=tuple(findings),
    )


__all__ = [
    "CHAPTER_BODY_TEMPLATE_REPEAT",
    "CHAPTER_OPENING_DUPLICATE",
    "ChapterDuplicateFinding",
    "ChapterDuplicateReport",
    "check_chapter_duplicates",
]
