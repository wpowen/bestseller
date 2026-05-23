from __future__ import annotations

from collections.abc import Mapping
import re

from bestseller.domain.gate_verdict import GateFinding, GateVerdict

_VAGUE_OPENERS = ("这时", "那人", "他", "她", "他们", "有人")
_RESET_MARKERS = ("多年以前", "话说", "重新开始", "另一个故事")


def evaluate_paragraph_coherence(chapter_texts: Mapping[int, str]) -> GateVerdict:
    findings: list[GateFinding] = []
    total = 0
    for chapter_no, text in sorted(chapter_texts.items()):
        paragraphs = _paragraphs(text)
        total += len(paragraphs)
        for index, paragraph in enumerate(paragraphs, start=1):
            compact = "".join(paragraph.split())
            path = f"chapter:{chapter_no}:paragraph:{index}"
            if len(compact) < 12:
                findings.append(
                    GateFinding(
                        code="paragraph_too_thin",
                        severity="medium",
                        message="paragraph is too thin to carry action or causality",
                        path=path,
                        repair_action="merge with adjacent beat or add causal action",
                    )
                )
            if index == 1 and compact.startswith(_RESET_MARKERS):
                findings.append(
                    GateFinding(
                        code="chapter_opening_reset",
                        severity="critical",
                        message=(
                            "chapter opens with a reset marker instead of "
                            "prior-state carryover"
                        ),
                        path=path,
                        repair_action="rewrite opening from prior chapter consequence",
                    )
                )
            if compact.startswith(_VAGUE_OPENERS) and not _has_anchor(compact):
                findings.append(
                    GateFinding(
                        code="floating_pronoun_or_subject",
                        severity="high",
                        message="paragraph starts from an unclear subject or weak transition",
                        path=path,
                        repair_action="name the actor and connect the beat to prior state",
                    )
                )
    verdict = (
        "blocked"
        if any(f.critical for f in findings)
        else ("warn_only" if findings else "pass")
    )
    coverage = (
        1.0 if not findings else max(0.0, 1.0 - (len(findings) / max(total, 1)))
    )
    return GateVerdict(
        gate_name="paragraph_coherence_gate",
        verdict=verdict,
        coverage=coverage,
        findings=tuple(findings),
        metrics={"paragraph_count": total, "finding_count": len(findings)},
    )


def _paragraphs(text: str) -> list[str]:
    body = re.sub(r"(?m)^#.*$", "", text)
    return [part.strip() for part in re.split(r"\n\s*\n", body) if part.strip()]


def _has_anchor(text: str) -> bool:
    return any(mark in text[:80] for mark in ("因为", "刚才", "上一", "回执", "血", "门"))
