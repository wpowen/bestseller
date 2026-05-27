from __future__ import annotations

from collections.abc import Mapping, Sequence
from itertools import pairwise
from pathlib import Path
from typing import Any

import yaml

from bestseller.domain.gate_verdict import GateFinding, GateVerdict
from bestseller.domain.volume_plan import VolumePlanV2, load_volume_plans_v2
from bestseller.services.outline_specificity_gate import PLACEHOLDER_BLACKLIST


def load_volume_plan_v2_file(path: str | Path) -> tuple[VolumePlanV2, ...]:
    loaded = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(loaded, Mapping):
        return ()
    return load_volume_plans_v2(loaded)


def evaluate_volume_plan_resolution(
    volume_plans: Sequence[VolumePlanV2] | Mapping[str, Any],
) -> GateVerdict:
    plans = (
        load_volume_plans_v2(volume_plans)
        if isinstance(volume_plans, Mapping)
        else tuple(volume_plans)
    )
    findings: list[GateFinding] = []
    metrics: dict[str, Any] = {"volume_count": len(plans), "coverage_by_volume": {}}

    if not plans:
        findings.append(
            GateFinding(
                code="VOLUME_PLAN_TOO_COARSE",
                severity="critical",
                message="volume-plan-v2 contains no validated volumes",
                path="volume-plan-v2.yaml",
                repair_action=(
                    "materialize volume-plan-v2.yaml with at least five "
                    "milestones per volume"
                ),
            )
        )

    for plan in plans:
        findings.extend(_evaluate_one_volume(plan, metrics))

    verdict = (
        "blocked"
        if any(f.severity == "critical" for f in findings)
        else ("warn_only" if findings else "pass")
    )
    return GateVerdict(
        gate_name="volume_plan_resolution_gate",
        verdict=verdict,
        coverage=1.0 if not findings else max(0.0, 1.0 - min(1.0, len(findings) / 4)),
        findings=tuple(findings),
        metrics=metrics,
    )


def _evaluate_one_volume(plan: VolumePlanV2, metrics: dict[str, Any]) -> list[GateFinding]:
    findings: list[GateFinding] = []
    volume_path = f"volume:{plan.volume_no}"
    if len(plan.milestones) < 5:
        findings.append(
            GateFinding(
                code="VOLUME_PLAN_TOO_COARSE",
                severity="critical",
                message=f"volume {plan.volume_no} has fewer than five milestones",
                path=volume_path,
                repair_action=(
                    "split the volume into five to eight concrete 6-10 "
                    "chapter milestones"
                ),
            )
        )

    ranges: list[tuple[int, int]] = []
    for index, milestone in enumerate(plan.milestones, start=1):
        start, end = milestone.chapter_range
        ranges.append((start, end))
        path = f"{volume_path}:milestone:{index}"
        if end - start + 1 > 10:
            findings.append(
                GateFinding(
                    code="VOLUME_PLAN_TOO_COARSE",
                    severity="critical",
                    message=(
                        f"volume {plan.volume_no} milestone {index} spans "
                        "more than 10 chapters"
                    ),
                    path=path,
                    repair_action="narrow each milestone to a 6-10 chapter window",
                )
            )
        if len(milestone.milestone_label.strip()) < 12 or _has_placeholder(
            milestone.milestone_label
        ):
            findings.append(
                GateFinding(
                    code="VOLUME_PLAN_TOO_COARSE",
                    severity="critical",
                    message=f"volume {plan.volume_no} milestone {index} label is too generic",
                    path=f"{path}:milestone_label",
                    repair_action=(
                        "rewrite milestone label as a concrete action/payoff, "
                        "not a template"
                    ),
                )
            )

    coverage = _range_coverage(plan.chapter_range, ranges)
    metrics["coverage_by_volume"][str(plan.volume_no)] = coverage
    if coverage < 0.9:
        findings.append(
            GateFinding(
                code="MILESTONE_GAP",
                severity="high",
                message=f"volume {plan.volume_no} milestone coverage is {coverage:.0%}, below 90%",
                path=volume_path,
                repair_action="fill uncovered chapter windows with concrete milestones",
            )
        )
    if _has_overlap(ranges):
        findings.append(
            GateFinding(
                code="MILESTONE_OVERLAP",
                severity="high",
                message=f"volume {plan.volume_no} milestones overlap",
                path=volume_path,
                repair_action="make milestone chapter ranges ordered and non-overlapping",
            )
        )
    return findings


def _has_placeholder(text: str) -> bool:
    return any(pattern and pattern in text for pattern in PLACEHOLDER_BLACKLIST)


def _range_coverage(
    chapter_range: tuple[int, int],
    ranges: Sequence[tuple[int, int]],
) -> float:
    start, end = chapter_range
    total = end - start + 1
    if total <= 0:
        return 0.0
    covered: set[int] = set()
    for left, right in ranges:
        covered.update(range(max(start, left), min(end, right) + 1))
    return len(covered) / total


def _has_overlap(ranges: Sequence[tuple[int, int]]) -> bool:
    ordered = sorted(ranges)
    for (_, prev_end), (start, _) in pairwise(ordered):
        if start <= prev_end:
            return True
    return False
