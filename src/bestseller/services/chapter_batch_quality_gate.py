"""Batch-level quality regression gates for generated chapters."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from bestseller.services.deduplication import (
    check_opening_diversity,
    detect_cross_chapter_repetition,
    extract_chapter_opening,
)


@dataclass(frozen=True)
class BatchQualityFinding:
    code: str
    severity: str
    chapter_number: int
    message: str
    evidence: dict[str, object]

    def to_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "severity": self.severity,
            "chapter_number": self.chapter_number,
            "message": self.message,
            "evidence": dict(self.evidence),
        }


@dataclass(frozen=True)
class BatchQualityGateReport:
    start_chapter: int
    end_chapter: int
    findings: tuple[BatchQualityFinding, ...]

    @property
    def passed(self) -> bool:
        return not self.findings

    @property
    def blocking_codes(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(finding.code for finding in self.findings))

    def to_dict(self) -> dict[str, object]:
        return {
            "start_chapter": self.start_chapter,
            "end_chapter": self.end_chapter,
            "passed": self.passed,
            "blocking_codes": list(self.blocking_codes),
            "findings": [finding.to_dict() for finding in self.findings],
        }


def _ending_signature(text: str, *, limit: int = 80) -> str:
    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    if not lines:
        return ""
    tail = lines[-1]
    return tail[-limit:]


def evaluate_chapter_batch_quality(
    chapter_texts: Iterable[tuple[int, str]],
    *,
    opening_similarity_threshold: float = 0.72,
    max_repeated_ending_signature: int = 1,
) -> BatchQualityGateReport | None:
    """Evaluate a contiguous chapter window for cross-chapter regressions."""

    items = [(int(number), text or "") for number, text in chapter_texts if text]
    if not items:
        return None
    items.sort(key=lambda item: item[0])
    findings: list[BatchQualityFinding] = []

    previous_openings: list[tuple[int, str]] = []
    for chapter_number, text in items:
        opening = extract_chapter_opening(text)
        if opening:
            for raw in check_opening_diversity(
                opening,
                previous_openings[-12:],
                similarity_threshold=opening_similarity_threshold,
                opening_length=80,
            )[:3]:
                source_chapter = int(raw.get("chapter") or 0)
                findings.append(
                    BatchQualityFinding(
                        code="CHAPTER_OPENING_REPETITION",
                        severity="critical",
                        chapter_number=chapter_number,
                        message=str(raw.get("message") or "recent opening repeated"),
                        evidence={
                            "source_chapter": source_chapter,
                            "similarity": raw.get("similarity"),
                            "opening": opening,
                        },
                    )
                )
            previous_openings.append((chapter_number, opening))

    for raw in detect_cross_chapter_repetition(items):
        findings.append(
            BatchQualityFinding(
                code="CROSS_CHAPTER_REPETITION",
                severity=str(raw.get("severity") or "critical"),
                chapter_number=int(raw.get("chapter") or 0),
                message=str(raw.get("message") or "cross-chapter repetition detected"),
                evidence=dict(raw),
            )
        )

    endings: dict[str, list[int]] = {}
    for chapter_number, text in items:
        signature = _ending_signature(text)
        if len(signature) < 20:
            continue
        endings.setdefault(signature, []).append(chapter_number)
    for signature, chapters in endings.items():
        if len(chapters) <= max_repeated_ending_signature:
            continue
        for chapter_number in chapters[1:]:
            findings.append(
                BatchQualityFinding(
                    code="ENDING_SENTENCE_WEAK",
                    severity="critical",
                    chapter_number=chapter_number,
                    message="chapter ending repeats a prior ending signature",
                    evidence={
                        "source_chapter": chapters[0],
                        "ending_signature": signature,
                    },
                )
            )

    return BatchQualityGateReport(
        start_chapter=items[0][0],
        end_chapter=items[-1][0],
        findings=tuple(findings),
    )


__all__ = [
    "BatchQualityFinding",
    "BatchQualityGateReport",
    "evaluate_chapter_batch_quality",
]
