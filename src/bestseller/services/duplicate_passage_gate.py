from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
import re

from bestseller.domain.gate_verdict import GateFinding, GateVerdict


def evaluate_duplicate_passages(
    chapter_texts: Mapping[int, str],
    *,
    min_chars: int = 24,
) -> GateVerdict:
    seen: dict[str, list[str]] = defaultdict(list)
    for chapter_no, text in sorted(chapter_texts.items()):
        for index, paragraph in enumerate(_paragraphs(text), start=1):
            key = _normalize(paragraph)
            if len(key) >= min_chars:
                seen[key].append(f"chapter:{chapter_no}:paragraph:{index}")
    findings = [
        GateFinding(
            code="duplicate_passage",
            severity="critical",
            message=f"duplicate passage appears {len(paths)} times",
            path=";".join(paths),
            repair_action="rewrite or delete duplicated passage",
        )
        for paths in seen.values()
        if len(paths) > 1
    ]
    return GateVerdict(
        gate_name="duplicate_passage_gate",
        verdict="blocked" if findings else "pass",
        coverage=0.0 if findings else 1.0,
        findings=tuple(findings),
        metrics={"duplicate_passage_count": len(findings)},
    )


def _paragraphs(text: str) -> list[str]:
    body = re.sub(r"(?m)^#.*$", "", text)
    return [part.strip() for part in re.split(r"\n\s*\n", body) if part.strip()]


def _normalize(text: str) -> str:
    return re.sub(r"\s+", "", text).lower()
