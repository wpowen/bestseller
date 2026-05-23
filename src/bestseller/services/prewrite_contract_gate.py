from __future__ import annotations

from collections.abc import Mapping
import json
from pathlib import Path
from typing import Any

from bestseller.domain.gate_verdict import GateFinding, GateVerdict


def load_prewrite_contract(story_bible_dir: str | Path) -> dict[str, object]:
    path = Path(story_bible_dir) / "prewrite-contract.json"
    if not path.exists():
        return {}
    loaded = json.loads(path.read_text(encoding="utf-8"))
    return loaded if isinstance(loaded, dict) else {}


def evaluate_prewrite_contract_coverage(
    *,
    chapter_no: int,
    contract: Mapping[str, Any] | None = None,
    story_bible_dir: str | Path | None = None,
) -> GateVerdict:
    payload = dict(contract or {})
    if story_bible_dir is not None and not payload:
        payload = load_prewrite_contract(story_bible_dir)
    chapter_payload = _chapter_payload(payload, chapter_no)
    findings: list[GateFinding] = []
    if not chapter_payload:
        findings.append(
            GateFinding(
                code="prewrite_contract_chapter_missing",
                severity="critical",
                message=f"prewrite contract missing chapter {chapter_no}",
                path=f"prewrite-contract.json:chapter:{chapter_no}",
                repair_action="materialize chapter prewrite contract before writing",
            )
        )
    elif not str(chapter_payload.get("prewrite_anchor") or "").strip():
        findings.append(
            GateFinding(
                code="prewrite_anchor_missing",
                severity="critical",
                message=f"chapter {chapter_no} lacks prewrite_anchor",
                path=f"prewrite-contract.json:chapter:{chapter_no}:prewrite_anchor",
                repair_action="add prewrite_anchor linking seam, outline, and payoff",
            )
        )
    return GateVerdict(
        gate_name="prewrite_contract_coverage",
        verdict="blocked" if findings else "pass",
        coverage=0.0 if findings else 1.0,
        findings=tuple(findings),
        metrics={"chapter_no": chapter_no},
    )


def _chapter_payload(payload: Mapping[str, Any], chapter_no: int) -> Mapping[str, Any]:
    chapters = payload.get("chapters")
    if isinstance(chapters, Mapping):
        raw = chapters.get(str(chapter_no)) or chapters.get(chapter_no)
        if isinstance(raw, Mapping):
            return raw
    contracts = payload.get("chapter_contracts")
    if isinstance(contracts, list):
        for item in contracts:
            if isinstance(item, Mapping) and int(item.get("chapter_no") or 0) == chapter_no:
                return item
    if int(payload.get("chapter_no") or 0) == chapter_no:
        return payload
    return {}
