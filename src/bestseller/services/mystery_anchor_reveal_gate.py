from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from bestseller.domain.mystery_anchor import MysteryAnchorKernel


@dataclass(frozen=True)
class MysteryAnchorFinding:
    code: str
    severity: str
    message: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MysteryAnchorReport:
    findings: tuple[MysteryAnchorFinding, ...]

    @property
    def is_critical(self) -> bool:
        return any(f.severity == "critical" for f in self.findings)


def scan_mystery_anchor_reveals(
    kernel: MysteryAnchorKernel | dict,
    *,
    volume: int,
    revealed_ledger: list[str],
    full_reveal_chapters: dict[str, int] | None = None,
) -> MysteryAnchorReport:
    if isinstance(kernel, dict):
        kernel = MysteryAnchorKernel.model_validate(kernel)
    ledger_text = "\n".join(revealed_ledger)
    findings: list[MysteryAnchorFinding] = []

    volume_milestones = [
        milestone
        for anchor in kernel.anchors
        for milestone in anchor.reveal_milestones
        if milestone.volume == volume
    ]
    if not volume_milestones:
        findings.append(
            MysteryAnchorFinding(
                code="volume_without_anchor_advance",
                severity="critical",
                message=f"Volume {volume} does not advance any mystery anchor.",
                payload={"volume": volume},
            )
        )

    for anchor in kernel.anchors:
        for false_lead in anchor.false_lead_plan:
            if false_lead and false_lead not in ledger_text:
                findings.append(
                    MysteryAnchorFinding(
                        code="false_lead_missing",
                        severity="warning",
                        message=(
                            "False lead is planned but absent from revealed ledger: "
                            f"{false_lead}"
                        ),
                        payload={"anchor": anchor.question, "false_lead": false_lead},
                    )
                )
        chapter = (full_reveal_chapters or {}).get(anchor.question)
        if chapter is not None:
            start, end = anchor.final_payoff_chapter_range
            if not start <= chapter <= end:
                findings.append(
                    MysteryAnchorFinding(
                        code="full_reveal_outside_payoff_range",
                        severity="critical",
                        message=f"Full reveal for {anchor.question} lands outside payoff range.",
                        payload={"chapter": chapter, "range": [start, end]},
                    )
                )
    return MysteryAnchorReport(findings=tuple(findings))
