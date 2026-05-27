from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
import re

from bestseller.domain.gate_verdict import GateFinding, GateVerdict
from bestseller.services.outline_specificity_gate import _has_named_entity

_FORWARD_HEADING_RE = re.compile(r"^##\s+Forward Promises\b.*$", re.IGNORECASE | re.MULTILINE)
_CHAPTER_RE = re.compile(r"(?:ch|第)\s*([0-9]{1,4})\s*(?:章)?", re.IGNORECASE)


def evaluate_forward_state_contract(
    ledger: str | Path | Mapping[int, Sequence[str]],
    *,
    current_chapter: int,
    forward_window: int = 3,
) -> GateVerdict:
    entries = _load_entries(ledger)
    required = tuple(range(current_chapter + 1, current_chapter + forward_window + 1))
    findings: list[GateFinding] = []

    for chapter_no in required:
        chapter_entries = tuple(entries.get(chapter_no, ()))
        if not chapter_entries:
            findings.append(
                GateFinding(
                    code="FORWARD_STATE_MISSING",
                    severity="critical",
                    message=f"forward state promise missing for chapter {chapter_no}",
                    path=f"event-state-ledger.md:Forward Promises:chapter:{chapter_no}",
                    repair_action=(
                        "add next-chapter continuation and rollback prohibition "
                        "before drafting"
                    ),
                )
            )
            continue
        if not any(_has_named_entity(entry) for entry in chapter_entries):
            findings.append(
                GateFinding(
                    code="FORWARD_STATE_MISSING",
                    severity="critical",
                    message=f"forward state promise for chapter {chapter_no} lacks named entity",
                    path=f"event-state-ledger.md:Forward Promises:chapter:{chapter_no}",
                    repair_action=(
                        "bind the promise to a named person, object, place, or "
                        "clock anchor"
                    ),
                )
            )

    max_covered = max(entries) if entries else current_chapter
    if max_covered < current_chapter + forward_window:
        findings.append(
            GateFinding(
                code="FORWARD_STATE_TOO_SHORT",
                severity="high",
                message=(
                    f"forward state coverage reaches chapter {max_covered}, "
                    f"below required chapter {current_chapter + forward_window}"
                ),
                path="event-state-ledger.md:Forward Promises",
                repair_action=(
                    "extend forward promises through chapter "
                    f"{current_chapter + forward_window}"
                ),
            )
        )

    verdict = (
        "blocked"
        if any(f.severity == "critical" for f in findings)
        else ("warn_only" if findings else "pass")
    )
    return GateVerdict(
        gate_name="forward_state_contract_gate",
        verdict=verdict,
        coverage=1.0 - (len(findings) / max(1, len(required) + 1)) if findings else 1.0,
        findings=tuple(findings),
        metrics={
            "current_chapter": current_chapter,
            "forward_window": forward_window,
            "required_chapters": list(required),
            "covered_chapters": sorted(entries),
        },
    )


def _load_entries(ledger: str | Path | Mapping[int, Sequence[str]]) -> dict[int, list[str]]:
    if isinstance(ledger, Mapping):
        return {int(key): [str(item) for item in value] for key, value in ledger.items()}
    text = Path(ledger).read_text(encoding="utf-8") if isinstance(ledger, Path) else str(ledger)
    match = _FORWARD_HEADING_RE.search(text)
    if match is None:
        return {}
    section = text[match.end() :]
    next_heading = re.search(r"^##\s+", section, re.MULTILINE)
    if next_heading:
        section = section[: next_heading.start()]
    entries: dict[int, list[str]] = {}
    for line in section.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("| ---"):
            continue
        chapter_match = _CHAPTER_RE.search(stripped)
        if chapter_match is None:
            continue
        chapter_no = int(chapter_match.group(1))
        entries.setdefault(chapter_no, []).append(stripped)
    return entries
