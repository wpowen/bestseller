from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from bestseller.domain.ethical_dilemma import EthicalDilemmaKernel


@dataclass(frozen=True)
class EthicalDilemmaFinding:
    code: str
    severity: str
    message: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EthicalDilemmaReport:
    findings: tuple[EthicalDilemmaFinding, ...]

    @property
    def is_critical(self) -> bool:
        return any(f.severity == "critical" for f in self.findings)


def scan_ethical_dilemma_slots(
    kernel: EthicalDilemmaKernel | dict,
    *,
    total_chapters: int,
    landed_chapters: list[int],
    consequence_echoes: dict[int, list[str]] | None = None,
) -> EthicalDilemmaReport:
    if isinstance(kernel, dict):
        kernel = EthicalDilemmaKernel.model_validate(kernel)
    total = max(int(total_chapters or 0), 1)
    cadence = max(kernel.minimum_cadence_chapters, 1)
    landed = set(landed_chapters)
    findings: list[EthicalDilemmaFinding] = []

    for start in range(1, total + 1, cadence):
        end = min(start + cadence - 1, total)
        if not any(start <= chapter <= end for chapter in landed):
            findings.append(
                EthicalDilemmaFinding(
                    code="dilemma_cadence_gap",
                    severity="warning",
                    message=f"No ethical dilemma landed in chapter window {start}-{end}.",
                    payload={"window": [start, end]},
                )
            )

    echoes = consequence_echoes or {}
    for slot in kernel.slots:
        landed_for_slot = [
            chapter
            for chapter in landed
            if slot.chapter_window[0] <= chapter <= slot.chapter_window[1]
        ]
        for chapter in landed_for_slot:
            if not echoes.get(chapter):
                findings.append(
                    EthicalDilemmaFinding(
                        code="dilemma_consequence_echo_missing",
                        severity="warning",
                        message=(
                            "Ethical dilemma landed but the unchosen consequence "
                            "has no echo in the follow-up ledger."
                        ),
                        payload={"chapter": chapter},
                    )
                )
    return EthicalDilemmaReport(findings=tuple(findings))

