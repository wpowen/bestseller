from __future__ import annotations

from dataclasses import dataclass, field
from itertools import pairwise
from typing import Any

from bestseller.domain.geography import GeographyKernel


@dataclass(frozen=True)
class GeographyContinuityFinding:
    code: str
    severity: str
    message: str
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class GeographyContinuityReport:
    chapter_no: int | None
    findings: tuple[GeographyContinuityFinding, ...]

    @property
    def is_critical(self) -> bool:
        return any(f.severity == "critical" for f in self.findings)


def scan_geography_continuity(
    kernel: GeographyKernel | dict,
    *,
    chapter_regions: list[str],
    chapter_no: int | None = None,
) -> GeographyContinuityReport:
    if isinstance(kernel, dict):
        kernel = GeographyKernel.model_validate(kernel)

    findings: list[GeographyContinuityFinding] = []
    known = kernel.region_names()
    for region in chapter_regions:
        if region not in known:
            findings.append(
                GeographyContinuityFinding(
                    code="unknown_region",
                    severity="critical",
                    message=f"Chapter references unknown region: {region}",
                    payload={"region": region},
                )
            )
    for previous, current in pairwise(chapter_regions):
        if previous == current or previous not in known or current not in known:
            continue
        if kernel.route_between(previous, current) is None:
            findings.append(
                GeographyContinuityFinding(
                    code="geographic_jump_without_route",
                    severity="critical",
                    message=(
                        f"Chapter jumps from {previous} to {current} without a route edge."
                    ),
                    payload={"from": previous, "to": current, "chapter_no": chapter_no},
                )
            )
    return GeographyContinuityReport(chapter_no=chapter_no, findings=tuple(findings))
