"""Unified chapter quality bundle runner."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from bestseller.services.deduplication import (
    check_opening_diversity,
    detect_chapter_text_loop,
    detect_cross_chapter_repetition,
    detect_intra_chapter_repetition,
    detect_short_cluster_near_repeat,
    extract_chapter_opening,
)
from bestseller.services.quality_contract_registry import QUALITY_CONTRACT_VERSION
from bestseller.services.quality_finding_schema import (
    QualityFinding,
    dump_quality_findings,
    quality_finding_from_retention,
)


@dataclass(frozen=True)
class ChapterQualityBundleContext:
    chapter_number: int
    previous_chapter_text: str | None = None
    previous_chapter_position: int | None = None
    previous_chapter_texts: tuple[tuple[int, str], ...] = ()
    total_chapters: int = 500
    language: str = "zh-CN"
    target_chapter_words: int | None = None
    commercial_strict: bool = True


@dataclass(frozen=True)
class ChapterQualityBundleReport:
    chapter_number: int
    findings: tuple[QualityFinding, ...]
    contract_version: str = QUALITY_CONTRACT_VERSION

    @property
    def blocking_findings(self) -> tuple[QualityFinding, ...]:
        return tuple(f for f in self.findings if f.blocking)

    @property
    def repairable_findings(self) -> tuple[QualityFinding, ...]:
        from bestseller.services.quality_contract_registry import contract_for_code

        return tuple(
            f
            for f in self.blocking_findings
            if contract_for_code(f.code, commercial_strict=True).repairable
        )

    @property
    def nonrepairable_findings(self) -> tuple[QualityFinding, ...]:
        from bestseller.services.quality_contract_registry import contract_for_code

        return tuple(
            f
            for f in self.blocking_findings
            if not contract_for_code(f.code, commercial_strict=True).repairable
        )

    @property
    def passed(self) -> bool:
        return not self.blocking_findings

    def to_dict(self) -> dict[str, Any]:
        return {
            "chapter_number": self.chapter_number,
            "contract_version": self.contract_version,
            "passed": self.passed,
            "finding_count": len(self.findings),
            "blocking_codes": [f.code for f in self.blocking_findings],
            "repairable_codes": [f.code for f in self.repairable_findings],
            "nonrepairable_codes": [f.code for f in self.nonrepairable_findings],
            "findings": dump_quality_findings(self.findings),
        }


def _severity_from_raw(raw: str) -> str:
    severity = str(raw or "").strip().lower()
    if severity == "major":
        return "critical"
    return severity or "critical"


def _finding(
    *,
    code: str,
    source: str,
    chapter_number: int,
    detail: str,
    severity: str = "critical",
    evidence: dict[str, Any] | None = None,
    repair_scope: str = "chapter",
    blocking: bool = True,
) -> QualityFinding:
    return QualityFinding(
        code=code,
        severity=_severity_from_raw(severity),
        source=source,
        chapter_number=chapter_number,
        evidence=evidence or {},
        repair_hint=detail,
        repair_scope=repair_scope,
        blocking=blocking,
    )


def run_chapter_quality_bundle(
    chapter_text: str,
    context: ChapterQualityBundleContext,
) -> ChapterQualityBundleReport:
    findings: list[QualityFinding] = []
    chapter_number = int(context.chapter_number)
    text = chapter_text or ""

    for raw in (
        detect_chapter_text_loop(text)
        + detect_short_cluster_near_repeat(text)
        + detect_intra_chapter_repetition(text)
    ):
        findings.append(
            _finding(
                code="INTRA_CHAPTER_REPETITION",
                source="chapter_quality_bundle.local_repetition",
                chapter_number=chapter_number,
                severity=str(raw.get("severity") or "critical"),
                detail=str(raw.get("message") or "chapter contains repeated prose"),
                evidence=dict(raw),
                repair_scope="paragraph",
            )
        )

    previous_texts = tuple((int(n), t or "") for n, t in context.previous_chapter_texts if t)
    current_opening = extract_chapter_opening(text)
    if current_opening and previous_texts:
        previous_openings = [
            (n, extract_chapter_opening(previous_text))
            for n, previous_text in previous_texts[-12:]
        ]
        for raw in check_opening_diversity(
            current_opening,
            [(n, opening) for n, opening in previous_openings if opening],
            similarity_threshold=0.72,
            opening_length=80,
        )[:5]:
            source_chapter = int(raw.get("chapter") or 0)
            findings.append(
                _finding(
                    code="CHAPTER_OPENING_REPETITION",
                    source="chapter_quality_bundle.opening_diversity",
                    chapter_number=chapter_number,
                    detail=str(raw.get("message") or "chapter opening repeats a recent opening"),
                    evidence={
                        "opening": current_opening,
                        "source_chapter": source_chapter,
                        "similarity": raw.get("similarity"),
                    },
                    repair_scope="paragraph",
                )
            )

    if previous_texts:
        comparison = [*previous_texts, (chapter_number, text)]
        for raw in detect_cross_chapter_repetition(comparison):
            if int(raw.get("chapter") or 0) != chapter_number:
                continue
            findings.append(
                _finding(
                    code="CROSS_CHAPTER_REPETITION",
                    source="chapter_quality_bundle.cross_chapter_repetition",
                    chapter_number=chapter_number,
                    severity=str(raw.get("severity") or "critical"),
                    detail=str(raw.get("message") or "chapter repeats prior chapter prose"),
                    evidence=dict(raw),
                    repair_scope="paragraph",
                )
            )

    try:
        from bestseller.services.chapter_splice_coherence_gate import (
            as_quality_findings,
            evaluate_chapter_splice_coherence,
        )

        report = evaluate_chapter_splice_coherence(text, chapter_number=chapter_number)
        findings.extend(as_quality_findings(report, chapter_number=chapter_number))
    except Exception as exc:
        if context.commercial_strict:
            findings.append(
                _finding(
                    code="QUALITY_GATE_EXECUTION_FAILED",
                    source="chapter_quality_bundle.splice_coherence",
                    chapter_number=chapter_number,
                    detail=f"splice coherence gate failed: {type(exc).__name__}: {exc}",
                    evidence={"gate": "chapter_splice_coherence"},
                    repair_scope="package",
                )
            )

    try:
        from bestseller.services.anti_meta_gate import check_anti_meta_gate

        report = check_anti_meta_gate(text, chapter_position=chapter_number)
        for raw in report.findings:
            if str(raw.severity).lower() != "block":
                continue
            findings.append(
                _finding(
                    code="ANTI_META_LEAK",
                    source="chapter_quality_bundle.anti_meta",
                    chapter_number=chapter_number,
                    detail=f"正文泄露章节边界或设计语言：{raw.term}",
                    evidence={
                        "term": raw.term,
                        "excerpt": raw.excerpt,
                        "location": raw.location,
                    },
                    repair_scope="paragraph",
                )
            )
        if not report.ending_passed:
            findings.append(
                _finding(
                    code="ANTI_META_ENDING_OUT_OF_SCENE",
                    source="chapter_quality_bundle.anti_meta",
                    chapter_number=chapter_number,
                    detail="章末没有落在动作、画面或新事实揭示的一帧。",
                    evidence={"ending_excerpt": report.ending_excerpt},
                    repair_scope="ending",
                )
            )
    except Exception as exc:
        if context.commercial_strict:
            findings.append(
                _finding(
                    code="QUALITY_GATE_EXECUTION_FAILED",
                    source="chapter_quality_bundle.anti_meta",
                    chapter_number=chapter_number,
                    detail=f"anti-meta gate failed: {type(exc).__name__}: {exc}",
                    evidence={"gate": "anti_meta"},
                    repair_scope="package",
                )
            )

    try:
        from bestseller.services.show_dont_tell_gate import check_show_dont_tell_gate

        report = check_show_dont_tell_gate(text, chapter_position=chapter_number)
        if report.findings:
            findings.append(
                _finding(
                    code="SHOW_DONT_TELL",
                    source="chapter_quality_bundle.show_dont_tell",
                    chapter_number=chapter_number,
                    severity="high",
                    detail=f"chapter has {len(report.findings)} show-don't-tell issue(s)",
                    evidence={
                        "findings": [
                            {
                                "code": item.code,
                                "category": item.category,
                                "excerpt": item.excerpt,
                                "location": item.location,
                            }
                            for item in report.findings[:10]
                        ]
                    },
                    repair_scope="paragraph",
                    blocking=False,
                )
            )
    except Exception as exc:
        if context.commercial_strict:
            findings.append(
                _finding(
                    code="QUALITY_GATE_EXECUTION_FAILED",
                    source="chapter_quality_bundle.show_dont_tell",
                    chapter_number=chapter_number,
                    detail=f"show-don't-tell gate failed: {type(exc).__name__}: {exc}",
                    evidence={"gate": "show_dont_tell"},
                    repair_scope="package",
                )
            )

    try:
        from bestseller.services.common_sense_gate import evaluate_common_sense_gate

        report = evaluate_common_sense_gate(
            text,
            genre=None,
            sub_genre=None,
            chapter_number=chapter_number,
        )
        for raw in report.findings:
            if raw.severity not in {"high", "medium"}:
                continue
            findings.append(
                _finding(
                    code=raw.code.upper(),
                    source="chapter_quality_bundle.common_sense",
                    chapter_number=chapter_number,
                    severity=raw.severity,
                    detail=raw.message,
                    evidence=dict(raw.evidence),
                    repair_scope="chapter",
                )
            )
    except Exception as exc:
        if context.commercial_strict:
            findings.append(
                _finding(
                    code="QUALITY_GATE_EXECUTION_FAILED",
                    source="chapter_quality_bundle.common_sense",
                    chapter_number=chapter_number,
                    detail=f"common sense gate failed: {type(exc).__name__}: {exc}",
                    evidence={"gate": "common_sense"},
                    repair_scope="package",
                )
            )

    try:
        from bestseller.services.retention_safety_gate import evaluate_retention_safety

        length_kwargs: dict[str, int] = {}
        if context.target_chapter_words:
            length_kwargs["chapter_length_hard_floor"] = max(
                1500,
                int(context.target_chapter_words * 0.7),
            )
            length_kwargs["chapter_length_soft_warning"] = max(
                2000,
                int(context.target_chapter_words * 0.85),
            )
            length_kwargs["chapter_length_hard_max"] = max(
                3000,
                int(context.target_chapter_words * 1.2),
            )
        report = evaluate_retention_safety(
            chapter_position=chapter_number,
            chapter_text=text,
            prev_chapter_text=context.previous_chapter_text,
            prev_chapter_position=context.previous_chapter_position,
            total_chapters=context.total_chapters,
            skip_signature=True,
            skip_cast_compliance=True,
            skip_timeline=True,
            skip_character_role=True,
            skip_dialogue_voice=True,
            **length_kwargs,
        )
        findings.extend(
            quality_finding_from_retention(
                item,
                chapter_number=chapter_number,
                commercial_strict=context.commercial_strict,
            )
            for item in report.findings
            if item.code in set(report.auto_repair_codes) or item.severity == "critical"
        )
    except Exception as exc:
        if context.commercial_strict:
            findings.append(
                _finding(
                    code="QUALITY_GATE_EXECUTION_FAILED",
                    source="chapter_quality_bundle.retention_safety",
                    chapter_number=chapter_number,
                    detail=f"retention safety gate failed: {type(exc).__name__}: {exc}",
                    evidence={"gate": "retention_safety"},
                    repair_scope="package",
                )
            )

    # Keep one finding per source/code/evidence tuple; several legacy gates can
    # report the same root cause.
    deduped: list[QualityFinding] = []
    seen: set[tuple[str, str, str]] = set()
    for finding in findings:
        key = (finding.code, finding.source, repr(sorted(finding.evidence.items())))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(finding)

    return ChapterQualityBundleReport(
        chapter_number=chapter_number,
        findings=tuple(deduped),
    )


__all__ = [
    "ChapterQualityBundleContext",
    "ChapterQualityBundleReport",
    "run_chapter_quality_bundle",
]
