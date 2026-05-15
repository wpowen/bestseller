# ruff: noqa: RUF001
"""Volume-level worldview progression gate.

Chapter worldview checks catch local execution mistakes.  This gate checks the
volume plan earlier, before chapter outlines are generated, so worldbuilding
progresses as a book-level system instead of repeating the same pressure under
new scenery.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from bestseller.services.story_design_kernel import (
    StoryDesignKernel,
    WorldviewKernel,
    story_design_kernel_from_dict,
)

_BLOCKING_SEVERITIES = {"critical", "high"}
_MAP_FUNCTION_MARKERS = (
    "resource",
    "authority",
    "faction",
    "rule",
    "pressure",
    "anomaly",
    "cost",
    "risk",
    "资源",
    "权威",
    "势力",
    "规则",
    "压力",
    "异常",
    "代价",
    "风险",
)
_REVEAL_BUDGET_PER_VOLUME = 2


@dataclass(frozen=True, slots=True)
class WorldviewProgressionFinding:
    code: str
    severity: str
    message: str
    path: str
    evidence: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "path": self.path,
            "evidence": dict(self.evidence),
        }


@dataclass(frozen=True, slots=True)
class WorldviewProgressionReport:
    passed: bool
    score: int
    blocking_findings: tuple[WorldviewProgressionFinding, ...]
    warnings: tuple[WorldviewProgressionFinding, ...]


def worldview_progression_report_to_dict(
    report: WorldviewProgressionReport,
) -> dict[str, Any]:
    return {
        "passed": report.passed,
        "score": report.score,
        "blocking_findings": [
            finding.to_dict() for finding in report.blocking_findings
        ],
        "warnings": [finding.to_dict() for finding in report.warnings],
    }


def evaluate_worldview_progression_gate(
    story_design_kernel: Mapping[str, Any] | None,
    volume_plan: Mapping[str, Any] | Sequence[Any] | None,
) -> WorldviewProgressionReport:
    findings: list[WorldviewProgressionFinding] = []
    kernel = _hydrate_kernel(story_design_kernel, findings)
    volumes = _volume_entries(volume_plan)
    worldview = kernel.worldview_kernel if kernel else None

    if worldview is None:
        findings.append(
            WorldviewProgressionFinding(
                code="worldview_kernel_missing",
                severity="high",
                message="Volume progression requires StoryDesignKernel.worldview_kernel.",
                path="story_design_kernel.worldview_kernel",
            )
        )
    if not volumes:
        findings.append(
            WorldviewProgressionFinding(
                code="volume_plan_missing",
                severity="high",
                message="Volume progression cannot be verified without volume entries.",
                path="volume_plan",
            )
        )

    if worldview is not None and volumes:
        findings.extend(_check_authority_ladder(volumes))
        findings.extend(_check_map_functions(worldview, volumes))
        findings.extend(_check_state_variable_progression(worldview, volumes))
        findings.extend(_check_asset_risk_progression(volumes))
        findings.extend(_check_reveal_distribution(volumes))

    blocking = tuple(
        finding for finding in findings if finding.severity in _BLOCKING_SEVERITIES
    )
    warnings = tuple(
        finding for finding in findings if finding.severity not in _BLOCKING_SEVERITIES
    )
    penalty = sum(
        20 if finding.severity == "critical" else 12 if finding.severity == "high" else 5
        for finding in findings
    )
    return WorldviewProgressionReport(
        passed=not blocking,
        score=max(0, 100 - penalty),
        blocking_findings=blocking,
        warnings=warnings,
    )


def _hydrate_kernel(
    story_design_kernel: Mapping[str, Any] | None,
    findings: list[WorldviewProgressionFinding],
) -> StoryDesignKernel | None:
    if not story_design_kernel:
        findings.append(
            WorldviewProgressionFinding(
                code="story_design_kernel_missing",
                severity="high",
                message="Worldview progression gate requires a StoryDesignKernel.",
                path="story_design_kernel",
            )
        )
        return None
    try:
        return story_design_kernel_from_dict(dict(story_design_kernel))
    except Exception as exc:
        findings.append(
            WorldviewProgressionFinding(
                code="story_design_kernel_invalid",
                severity="high",
                message="StoryDesignKernel could not be validated for worldview progression.",
                path="story_design_kernel",
                evidence={"error": str(exc)},
            )
        )
        return None


def _check_authority_ladder(
    volumes: Sequence[Mapping[str, Any]],
) -> list[WorldviewProgressionFinding]:
    findings: list[WorldviewProgressionFinding] = []
    for index in range(1, len(volumes)):
        prev = volumes[index - 1]
        curr = volumes[index]
        prev_pressure = _authority_pressure_key(prev)
        curr_pressure = _authority_pressure_key(curr)
        if not prev_pressure or prev_pressure != curr_pressure:
            continue
        findings.append(
            WorldviewProgressionFinding(
                code="authority_ladder_flat",
                severity="high",
                message="Adjacent volumes repeat the same authority pressure without escalation.",
                path=f"volume_plan[{index}]",
                evidence={
                    "previous_volume": _volume_number(prev, index),
                    "current_volume": _volume_number(curr, index + 1),
                    "pressure": curr_pressure,
                },
            )
        )
    return findings


def _check_map_functions(
    worldview: WorldviewKernel,
    volumes: Sequence[Mapping[str, Any]],
) -> list[WorldviewProgressionFinding]:
    if not worldview.locations:
        return []
    findings: list[WorldviewProgressionFinding] = []
    for index, volume in enumerate(volumes):
        map_function = _text(volume.get("map_function"))
        if not map_function:
            continue
        if not _contains_any(map_function, _MAP_FUNCTION_MARKERS):
            findings.append(
                WorldviewProgressionFinding(
                    code="map_function_missing",
                    severity="warning",
                    message="Volume map/location function does not expose resource anomaly, faction pressure, rule demonstration, or risk.",
                    path=f"volume_plan[{index}].map_function",
                    evidence={
                        "volume_number": _volume_number(volume, index + 1),
                        "map_function": map_function,
                    },
                )
            )
    return findings


def _check_state_variable_progression(
    worldview: WorldviewKernel,
    volumes: Sequence[Mapping[str, Any]],
) -> list[WorldviewProgressionFinding]:
    if not worldview.state_variables:
        return []
    plan_text = _normalize(" ".join(_volume_state_target_texts(volumes)))
    stalled = [
        variable.key
        for variable in worldview.state_variables
        if _normalize(variable.key) not in plan_text
    ]
    if not stalled:
        return []
    return [
        WorldviewProgressionFinding(
            code="state_variable_stalls",
            severity="high",
            message="Tracked worldview state variables never change across the volume plan.",
            path="volume_plan.world_state_targets",
            evidence={"state_variables": stalled},
        )
    ]


def _check_asset_risk_progression(
    volumes: Sequence[Mapping[str, Any]],
) -> list[WorldviewProgressionFinding]:
    refs_by_asset: dict[str, list[tuple[int, str]]] = defaultdict(list)
    for index, volume in enumerate(volumes):
        risk_text = _text(volume.get("asset_risk_escalation"))
        for ref in _asset_refs(volume):
            refs_by_asset[_normalize(ref)].append((index, risk_text))

    findings: list[WorldviewProgressionFinding] = []
    for asset_key, uses in refs_by_asset.items():
        if len(uses) < 2:
            continue
        normalized_risks = [_normalize(risk) for _, risk in uses if risk]
        if len(set(normalized_risks)) >= len(normalized_risks) and all(normalized_risks):
            continue
        findings.append(
            WorldviewProgressionFinding(
                code="asset_risk_not_scaled",
                severity="high",
                message="Repeated asset use does not scale cost, exposure, or attention across volumes.",
                path="volume_plan.asset_risk_escalation",
                evidence={
                    "asset": asset_key,
                    "volumes": [_volume_number(volumes[index], index + 1) for index, _ in uses],
                    "asset_risk_escalation": [risk for _, risk in uses],
                },
            )
        )
    return findings


def _check_reveal_distribution(
    volumes: Sequence[Mapping[str, Any]],
) -> list[WorldviewProgressionFinding]:
    findings: list[WorldviewProgressionFinding] = []
    for index, volume in enumerate(volumes):
        reveal_budget = _int(volume.get("reveal_budget")) or 0
        reveal_count = len(_string_list(volume.get("key_reveals"))) + len(
            _string_list(volume.get("major_reveals"))
        )
        effective_count = max(reveal_budget, reveal_count)
        if effective_count <= _REVEAL_BUDGET_PER_VOLUME:
            continue
        findings.append(
            WorldviewProgressionFinding(
                code="reveal_distribution_imbalanced",
                severity="high",
                message="Too many major world reveals are clustered into one volume.",
                path=f"volume_plan[{index}].reveal_budget",
                evidence={
                    "volume_number": _volume_number(volume, index + 1),
                    "reveal_budget": reveal_budget,
                    "reveal_count": reveal_count,
                    "budget": _REVEAL_BUDGET_PER_VOLUME,
                },
            )
        )
    return findings


def _authority_pressure_key(volume: Mapping[str, Any]) -> str:
    claims = _string_list(volume.get("active_authority_claims"))
    if claims:
        return _normalize("|".join(claims))
    return _normalize(_text(volume.get("primary_force_name")))


def _volume_entries(
    volume_plan: Mapping[str, Any] | Sequence[Any] | None,
) -> list[dict[str, Any]]:
    if isinstance(volume_plan, Mapping):
        for key in ("volumes", "volume_plan"):
            values = _mapping_list(volume_plan.get(key))
            if values:
                return values
        return []
    return _mapping_list(volume_plan)


def _volume_state_target_texts(volumes: Sequence[Mapping[str, Any]]) -> list[str]:
    texts: list[str] = []
    for volume in volumes:
        texts.extend(_string_list(volume.get("world_state_targets")))
        texts.extend(_string_list(volume.get("state_targets")))
        text = _text(volume.get("distilled_state_delta"))
        if text:
            texts.append(text)
    return texts


def _asset_refs(volume: Mapping[str, Any]) -> list[str]:
    refs: list[str] = []
    for key in ("world_asset_refs", "asset_refs", "asset_gains"):
        refs.extend(_string_list(volume.get(key)))
    return _dedupe([ref for ref in refs if ref])


def _volume_number(volume: Mapping[str, Any], fallback: int) -> int:
    return _int(volume.get("volume_number")) or _int(volume.get("volume")) or fallback


def _mapping_list(value: object) -> list[dict[str, Any]]:
    if value is None or isinstance(value, str):
        return []
    if isinstance(value, Sequence):
        return [dict(item) for item in value if isinstance(item, Mapping)]
    return []


def _string_list(value: object) -> list[str]:
    if isinstance(value, str):
        return [_text(value)] if _text(value) else []
    if isinstance(value, Mapping):
        preferred_keys = ("key", "name", "id", "target", "claimant", "label")
        preferred = [
            _text(value.get(key)) for key in preferred_keys if _text(value.get(key))
        ]
        if preferred:
            return preferred
        return [_text(item) for item in value.values() if _text(item)]
    if isinstance(value, Sequence):
        result: list[str] = []
        for item in value:
            result.extend(_string_list(item))
        return result
    return []


def _contains_any(text: str, markers: Sequence[str]) -> bool:
    normalized = _normalize(text)
    return any(_normalize(marker) in normalized for marker in markers if _normalize(marker))


def _int(value: object) -> int | None:
    try:
        return int(value) if value is not None and str(value).strip() else None
    except (TypeError, ValueError):
        return None


def _text(value: object) -> str:
    return str(value).strip() if value is not None else ""


def _normalize(text: str) -> str:
    return "".join(_text(text).lower().split()).strip("。,.，；;：:")


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


__all__ = [
    "WorldviewProgressionFinding",
    "WorldviewProgressionReport",
    "evaluate_worldview_progression_gate",
    "worldview_progression_report_to_dict",
]
