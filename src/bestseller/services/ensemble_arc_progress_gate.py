from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from bestseller.domain.ensemble_arc import EnsembleArcKernel


@dataclass(frozen=True)
class EnsembleArcFinding:
    code: str
    severity: str
    message: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EnsembleArcReport:
    findings: tuple[EnsembleArcFinding, ...]

    @property
    def is_critical(self) -> bool:
        return any(f.severity == "critical" for f in self.findings)


def scan_ensemble_arc_progress(
    kernel: EnsembleArcKernel | dict,
    *,
    total_chapters: int,
    category: str | None = None,
) -> EnsembleArcReport:
    if isinstance(kernel, dict):
        kernel = EnsembleArcKernel.model_validate(kernel)
    total = max(int(total_chapters or 0), 1)
    strict_category = str(category or "") in {"武侠群像", "古典权谋", "史诗", "epic"}
    severity = "critical" if strict_category else "warning"
    floor = 5 if total >= 600 else 3 if total >= 300 else 0
    findings: list[EnsembleArcFinding] = []
    if floor and len(kernel.arcs) < floor:
        findings.append(
            EnsembleArcFinding(
                code="ensemble_arc_count_below_floor",
                severity=severity,
                message=f"{total}-chapter book requires at least {floor} ensemble arcs.",
                payload={"count": len(kernel.arcs), "floor": floor},
            )
        )
    for arc in kernel.arcs:
        if not arc.private_payoff.strip() or not arc.final_state.strip():
            findings.append(
                EnsembleArcFinding(
                    code="ensemble_arc_missing_payoff",
                    severity=severity,
                    message=f"Ensemble arc {arc.owner_id} lacks payoff/final_state.",
                    payload={"owner_id": str(arc.owner_id)},
                )
            )
        if len(set(arc.pov_chapters)) / total < 0.01:
            findings.append(
                EnsembleArcFinding(
                    code="ensemble_arc_pov_coverage_low",
                    severity=severity,
                    message=f"Ensemble arc {arc.owner_id} has below 1% POV coverage.",
                    payload={"owner_id": str(arc.owner_id), "pov_chapters": arc.pov_chapters},
                )
            )
        if len(arc.intersect_main) < 2:
            findings.append(
                EnsembleArcFinding(
                    code="ensemble_arc_mainline_intersections_low",
                    severity=severity,
                    message=f"Ensemble arc {arc.owner_id} intersects mainline fewer than twice.",
                    payload={"owner_id": str(arc.owner_id)},
                )
            )
    return EnsembleArcReport(findings=tuple(findings))

