"""Authoritative chapter word-count from body text (never trust metadata alone).

Frontmatter / DB ``word_count`` fields are writer-reported and can diverge
from real CJK body length (e.g. 5840 claimed vs 531 actual). Production
gates must use ``count_zh_chars`` on stripped prose.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Final

from bestseller.services.chapter_length_gate import count_zh_chars

WORD_COUNT_METADATA_MISMATCH: Final[str] = "WORD_COUNT_METADATA_MISMATCH"

# Relative drift above this ratio triggers a critical finding.
DEFAULT_METADATA_MISMATCH_RATIO: Final[float] = 0.15
# Absolute gap (chars) when stored count is much larger than body.
DEFAULT_METADATA_MISMATCH_ABS_CHARS: Final[int] = 200

_FRONTMATTER_RE = re.compile(r"^---\s*\n.*?\n---\s*\n", re.DOTALL | re.MULTILINE)
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)


@dataclass(frozen=True)
class WordCountTruthFinding:
    severity: str  # critical | high | info
    code: str
    detail: str
    actual_zh_chars: int
    stored_word_count: int | None
    draft_word_count: int | None


@dataclass(frozen=True)
class WordCountTruthReport:
    actual_zh_chars: int
    finding: WordCountTruthFinding

    @property
    def passed(self) -> bool:
        return self.finding.severity == "info"


def strip_markdown_frontmatter(text: str) -> str:
    """Remove YAML frontmatter if present."""

    if not text:
        return ""
    return _FRONTMATTER_RE.sub("", text, count=1).strip()


def measure_chapter_body_zh_chars(text: str) -> int:
    """Count reader-visible CJK characters (excludes production metadata)."""

    body = strip_markdown_frontmatter(text)
    return count_zh_chars(_HTML_COMMENT_RE.sub("", body))


def authoritative_zh_word_count(
    text: str,
    *,
    language: str = "zh-CN",
) -> int:
    """Return the word count that gates and DB fields should use."""

    if str(language or "").lower().startswith("zh"):
        return measure_chapter_body_zh_chars(text)
    from bestseller.services.drafts import count_words

    return count_words(strip_markdown_frontmatter(text))


def check_word_count_metadata_truth(
    chapter_text: str,
    *,
    stored_word_count: int | None,
    draft_word_count: int | None = None,
    mismatch_ratio: float = DEFAULT_METADATA_MISMATCH_RATIO,
    mismatch_abs_chars: int = DEFAULT_METADATA_MISMATCH_ABS_CHARS,
) -> WordCountTruthReport:
    """Detect when stored counts disagree with measured body length."""

    actual = measure_chapter_body_zh_chars(chapter_text)
    candidates = [wc for wc in (stored_word_count, draft_word_count) if wc and wc > 0]
    if not candidates:
        return WordCountTruthReport(
            actual_zh_chars=actual,
            finding=WordCountTruthFinding(
                severity="info",
                code="WORD_COUNT_OK",
                detail=f"body CJK={actual}, no stored counts to compare",
                actual_zh_chars=actual,
                stored_word_count=stored_word_count,
                draft_word_count=draft_word_count,
            ),
        )

    worst = max(candidates)
    gap = worst - actual
    ratio_gap = gap / worst if worst > 0 else 0.0
    if gap >= mismatch_abs_chars and ratio_gap >= mismatch_ratio:
        return WordCountTruthReport(
            actual_zh_chars=actual,
            finding=WordCountTruthFinding(
                severity="critical",
                code=WORD_COUNT_METADATA_MISMATCH,
                detail=(
                    f"metadata claims up to {worst} chars but body has {actual} CJK "
                    f"(gap={gap}, ratio={ratio_gap:.2f})"
                ),
                actual_zh_chars=actual,
                stored_word_count=stored_word_count,
                draft_word_count=draft_word_count,
            ),
        )

    if gap >= mismatch_abs_chars // 2 and ratio_gap >= mismatch_ratio / 2:
        return WordCountTruthReport(
            actual_zh_chars=actual,
            finding=WordCountTruthFinding(
                severity="high",
                code=WORD_COUNT_METADATA_MISMATCH,
                detail=(
                    f"metadata drift: stored up to {worst}, body {actual} CJK"
                ),
                actual_zh_chars=actual,
                stored_word_count=stored_word_count,
                draft_word_count=draft_word_count,
            ),
        )

    return WordCountTruthReport(
        actual_zh_chars=actual,
        finding=WordCountTruthFinding(
            severity="info",
            code="WORD_COUNT_OK",
            detail=f"body CJK={actual}, stored max={worst}",
            actual_zh_chars=actual,
            stored_word_count=stored_word_count,
            draft_word_count=draft_word_count,
        ),
    )


__all__ = [
    "DEFAULT_METADATA_MISMATCH_ABS_CHARS",
    "DEFAULT_METADATA_MISMATCH_RATIO",
    "WORD_COUNT_METADATA_MISMATCH",
    "WordCountTruthFinding",
    "WordCountTruthReport",
    "authoritative_zh_word_count",
    "check_word_count_metadata_truth",
    "measure_chapter_body_zh_chars",
    "strip_markdown_frontmatter",
]
