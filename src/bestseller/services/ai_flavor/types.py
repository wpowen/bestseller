"""Frozen value types for AI-flavor detection.

Kept in their own module so neither the detector nor the patcher needs to
import the other to share the data shapes. All structures are immutable
(``frozen=True``) so reports can be serialised, cached, or passed across
async boundaries without ownership concerns.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


Severity = Literal["block", "warn", "info"]


@dataclass(frozen=True)
class AiFlavorSpan:
    """One AI-flavor finding pinned to a character range in the chapter.

    ``sentence_span`` carries the surrounding sentence boundaries so the
    patcher can hand a sentence-sized context window to an LLM rewriter
    without re-splitting the text. ``suggestions`` empty + severity=block
    means the patcher should drop the entire sentence.
    """

    start: int
    end: int
    matched_text: str
    rule_id: str
    category: str
    severity: Severity
    suggestions: tuple[str, ...]
    sentence_span: tuple[int, int]
    why: str
    remove_sentence_on_block: bool = True


@dataclass(frozen=True)
class AiFlavorReport:
    """Detection result for a single chapter."""

    language: str
    chapter_number: int
    overall_score: float
    spans: tuple[AiFlavorSpan, ...]

    @property
    def block_spans(self) -> tuple[AiFlavorSpan, ...]:
        return tuple(s for s in self.spans if s.severity == "block")

    @property
    def warn_spans(self) -> tuple[AiFlavorSpan, ...]:
        return tuple(s for s in self.spans if s.severity == "warn")

    @property
    def info_spans(self) -> tuple[AiFlavorSpan, ...]:
        return tuple(s for s in self.spans if s.severity == "info")
