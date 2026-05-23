from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from bestseller.domain.chapter_entry_exit import ChapterEntryAndExit
from bestseller.domain.gate_verdict import GateFinding, GateVerdict


def evaluate_chapter_entry_exit(
    payload: ChapterEntryAndExit | Mapping[str, Any],
) -> GateVerdict:
    contract = (
        payload
        if isinstance(payload, ChapterEntryAndExit)
        else ChapterEntryAndExit.model_validate(payload)
    )
    findings: list[GateFinding] = []
    for entry in contract.entries:
        if entry.is_new and not entry.entry_verb.strip():
            findings.append(
                GateFinding(
                    code="entry_verb_missing",
                    severity="critical",
                    message=f"New {entry.kind} {entry.name} lacks entry_verb",
                    path=f"chapter:{contract.chapter_no}:entry:{entry.name}",
                    repair_action="add a concrete entry verb and entry context",
                )
            )
        if entry.is_new and not entry.entry_context.strip():
            findings.append(
                GateFinding(
                    code="entry_context_missing",
                    severity="high",
                    message=f"New {entry.kind} {entry.name} lacks entry_context",
                    path=f"chapter:{contract.chapter_no}:entry:{entry.name}",
                    repair_action="anchor the entity to current conflict or scene pressure",
                )
            )
    for chapter_exit in contract.exits:
        if not chapter_exit.exit_state.strip():
            findings.append(
                GateFinding(
                    code="exit_state_missing",
                    severity="critical",
                    message=f"{chapter_exit.name} lacks exit_state",
                    path=f"chapter:{contract.chapter_no}:exit:{chapter_exit.name}",
                    repair_action="state what changed and what pressure carries forward",
                )
            )
    verdict = (
        "blocked"
        if any(f.critical for f in findings)
        else ("warn_only" if findings else "pass")
    )
    return GateVerdict(
        gate_name="chapter_entry_exit_gate",
        verdict=verdict,
        coverage=0.0 if findings else 1.0,
        findings=tuple(findings),
        metrics={
            "chapter_no": contract.chapter_no,
            "entry_count": len(contract.entries),
            "exit_count": len(contract.exits),
        },
    )
