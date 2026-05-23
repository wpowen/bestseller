from __future__ import annotations

from collections.abc import Mapping
import json
from pathlib import Path
from typing import Any

from bestseller.domain.gate_verdict import GateFinding, GateVerdict


def evaluate_forbidden_terms_drift(
    chapter_texts: Mapping[int, str],
    *,
    guardrails: Mapping[str, Any] | None = None,
    guardrails_path: str | Path | None = None,
) -> GateVerdict:
    payload = dict(guardrails or {})
    if guardrails_path is not None and not payload:
        loaded = json.loads(Path(guardrails_path).read_text(encoding="utf-8"))
        payload = loaded if isinstance(loaded, dict) else {}
    terms = _forbidden_terms(payload)
    findings: list[GateFinding] = []
    for chapter_no, text in sorted(chapter_texts.items()):
        for term in terms:
            if term in text:
                findings.append(
                    GateFinding(
                        code="forbidden_term_drift",
                        severity="critical",
                        message=f"forbidden term appears in chapter {chapter_no}: {term}",
                        path=f"chapter:{chapter_no}",
                        repair_action=f"replace or justify forbidden term: {term}",
                    )
                )
    return GateVerdict(
        gate_name="forbidden_terms_drift_gate",
        verdict="blocked" if findings else "pass",
        coverage=0.0 if findings else 1.0,
        findings=tuple(findings),
        metrics={"forbidden_term_count": len(terms), "finding_count": len(findings)},
    )


def _forbidden_terms(payload: Mapping[str, Any]) -> tuple[str, ...]:
    out: list[str] = []
    for item in payload.get("forbidden_terms", []) or []:
        if isinstance(item, str) and item.strip():
            out.append(item.strip())
        elif isinstance(item, Mapping) and str(item.get("term") or "").strip():
            out.append(str(item["term"]).strip())
    return tuple(dict.fromkeys(out))
