# ruff: noqa: RUF001
"""Worldview compliance gate for outline planning.

The StoryDesignKernel can now carry a WorldviewKernel: a book-specific operating
system for rules, factions, locations, systems, and staged reveals.  This gate
checks generated volume/chapter outlines against that contract before drafting.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from bestseller.services.story_design_kernel import (
    StoryDesignKernel,
    WorldviewKernel,
    story_design_kernel_from_dict,
)

_BLOCKING_SEVERITIES = {"critical", "high"}
_RULE_REF_FIELDS = (
    "world_rule_refs",
    "world_rules_used",
    "active_world_rules",
    "worldview_rule_refs",
    "system_refs",
    "world_system_refs",
)
_RULE_LANDING_FIELDS = (
    "world_rule_landing",
    "world_consequence",
    "rule_landing",
    "world_state_change",
    "system_consequence",
)
_LOCATION_REF_FIELDS = (
    "location_refs",
    "locations",
    "setting",
    "primary_location",
    "scene_locations",
)
_FACTION_REF_FIELDS = (
    "faction_refs",
    "factions",
    "active_factions",
    "pressure_factions",
    "opposing_faction",
)
_REVEAL_FIELDS = (
    "key_reveals",
    "world_reveals",
    "information_revealed",
    "reveals",
    "lore_reveals",
    "summary",
    "synopsis",
)
_WORLD_STATE_DELTA_FIELDS = ("world_state_deltas", "state_deltas", "world_state_changes")
_WORLD_ASSET_REF_FIELDS = ("world_asset_refs", "asset_refs", "world_assets")
_AUTHORITY_CLAIM_REF_FIELDS = (
    "authority_claim_refs",
    "authority_refs",
    "world_authority_refs",
)
_WORLD_SCENE_TEMPLATE_REF_FIELDS = (
    "world_scene_template_ref",
    "scene_template_ref",
    "world_scene_template",
)
_ANTI_COPY_BOUNDARY_FIELDS = (
    "title",
    "chapter_title",
    "goal",
    "chapter_goal",
    "chapter_goal",
    "main_conflict",
    "conflict",
    "core_conflict",
    "hook_description",
    "key_reveals",
    "world_reveals",
    "reveals",
)
_DEFAULT_CHAPTER_REVEAL_WEIGHT_BUDGET = 2


@dataclass(frozen=True, slots=True)
class WorldviewComplianceFinding:
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
class WorldviewComplianceReport:
    passed: bool
    score: int
    blocking_findings: tuple[WorldviewComplianceFinding, ...]
    warnings: tuple[WorldviewComplianceFinding, ...]
    worldview_snapshot: Mapping[str, Any]


def worldview_compliance_report_to_dict(
    report: WorldviewComplianceReport,
) -> dict[str, Any]:
    return {
        "passed": report.passed,
        "score": report.score,
        "blocking_findings": [finding.to_dict() for finding in report.blocking_findings],
        "warnings": [finding.to_dict() for finding in report.warnings],
        "worldview_snapshot": dict(report.worldview_snapshot),
    }


def evaluate_worldview_compliance_gate(
    story_design_kernel: Mapping[str, Any] | None,
    outline_payload: Mapping[str, Any] | Sequence[Any] | None,
) -> WorldviewComplianceReport:
    findings: list[WorldviewComplianceFinding] = []
    chapters = _outline_chapters(outline_payload)
    kernel = _hydrate_kernel(story_design_kernel, findings)
    worldview = kernel.worldview_kernel if kernel else None
    snapshot = _worldview_snapshot(worldview, chapters)

    if worldview is None:
        findings.append(
            WorldviewComplianceFinding(
                code="worldview_kernel_missing",
                severity="high",
                message="Outline cannot be verified without StoryDesignKernel.worldview_kernel.",
                path="story_design_kernel.worldview_kernel",
            )
        )
    if not chapters:
        findings.append(
            WorldviewComplianceFinding(
                code="outline_missing_chapters",
                severity="high",
                message="Outline has no chapters to verify against the worldview kernel.",
                path="chapters",
            )
        )

    if worldview is not None:
        allowed_rule_refs = _allowed_rule_refs(worldview)
        allowed_locations = {_normalize(location.name) for location in worldview.locations}
        allowed_factions = {_normalize(faction.name) for faction in worldview.factions}
        outline_volume = _outline_volume_number(outline_payload)

        for index, chapter in enumerate(chapters, 1):
            path = f"chapters[{index - 1}]"
            chapter_number = _chapter_number(chapter, index)
            chapter_volume = _int(chapter.get("volume_number")) or outline_volume
            rule_refs = _refs_from_fields(chapter, _RULE_REF_FIELDS)
            landing_texts = _texts_from_fields(chapter, _RULE_LANDING_FIELDS)

            if not rule_refs and not any(_text(text) for text in landing_texts):
                findings.append(
                    WorldviewComplianceFinding(
                        code="world_rule_not_grounded",
                        severity="high",
                        message="Chapter does not ground a worldview rule through refs, consequence, cost, or state change.",
                        path=path,
                        evidence={
                            "chapter_number": chapter_number,
                            "chapter_rule": worldview.integration_contract.chapter_rule,
                        },
                    )
                )

            unknown_rules = [
                ref for ref in rule_refs if not _is_registered_ref(ref, allowed_rule_refs)
            ]
            if unknown_rules:
                findings.append(
                    WorldviewComplianceFinding(
                        code="unregistered_world_rule_ref",
                        severity="high",
                        message="Chapter references worldview rules or systems not registered in the worldview kernel.",
                        path=path,
                        evidence={
                            "chapter_number": chapter_number,
                            "refs": unknown_rules,
                        },
                    )
                )

            unknown_locations = _unknown_refs(
                _refs_from_fields(chapter, _LOCATION_REF_FIELDS),
                allowed_locations,
            )
            if unknown_locations:
                findings.append(
                    WorldviewComplianceFinding(
                        code="unregistered_world_location",
                        severity="warning",
                        message="Chapter uses locations that are not registered in the worldview kernel.",
                        path=path,
                        evidence={
                            "chapter_number": chapter_number,
                            "locations": unknown_locations,
                        },
                    )
                )

            unknown_factions = _unknown_refs(
                _refs_from_fields(chapter, _FACTION_REF_FIELDS),
                allowed_factions,
            )
            if unknown_factions:
                findings.append(
                    WorldviewComplianceFinding(
                        code="unregistered_world_faction",
                        severity="warning",
                        message="Chapter uses factions that are not registered in the worldview kernel.",
                        path=path,
                        evidence={
                            "chapter_number": chapter_number,
                            "factions": unknown_factions,
                        },
                    )
                )

            leaked_reveals = _future_reveal_leaks(
                worldview,
                chapter,
                chapter_number=chapter_number,
                volume_number=chapter_volume,
            )
            for leak in leaked_reveals:
                findings.append(
                    WorldviewComplianceFinding(
                        code="world_reveal_leak",
                        severity="high",
                        message="Chapter reveals a staged worldview truth before its allowed slot.",
                        path=path,
                        evidence={
                            "chapter_number": chapter_number,
                            "stage": leak["stage"],
                            "reveal": leak["reveal"],
                            "earliest_chapter": leak.get("earliest_chapter"),
                            "earliest_volume": leak.get("earliest_volume"),
                        },
                    )
                )

            findings.extend(
                _evaluate_enhanced_worldview_contracts(
                    worldview,
                    chapter,
                    path=path,
                    chapter_number=chapter_number,
                    active_factions=_refs_from_fields(chapter, _FACTION_REF_FIELDS),
                )
            )

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
    return WorldviewComplianceReport(
        passed=not blocking,
        score=max(0, 100 - penalty),
        blocking_findings=blocking,
        warnings=warnings,
        worldview_snapshot=snapshot,
    )


def _evaluate_enhanced_worldview_contracts(
    worldview: WorldviewKernel,
    chapter: Mapping[str, Any],
    *,
    path: str,
    chapter_number: int,
    active_factions: Sequence[str],
) -> list[WorldviewComplianceFinding]:
    findings: list[WorldviewComplianceFinding] = []

    state_variables = {
        _normalize(variable.key): variable for variable in worldview.state_variables
    }
    if state_variables:
        state_deltas = _world_state_deltas(chapter)
        if not state_deltas:
            findings.append(
                WorldviewComplianceFinding(
                    code="world_state_delta_missing",
                    severity="high",
                    message="Chapter must update at least one registered worldview state variable.",
                    path=path,
                    evidence={
                        "chapter_number": chapter_number,
                        "registered_state_variables": [
                            variable.key for variable in worldview.state_variables
                        ],
                    },
                )
            )
        else:
            unknown_state_keys = [
                key
                for key in _state_delta_keys(state_deltas)
                if key and _normalize(key) not in state_variables
            ]
            if unknown_state_keys:
                findings.append(
                    WorldviewComplianceFinding(
                        code="unregistered_world_state_variable",
                        severity="high",
                        message="Chapter updates worldview state variables not registered in the worldview kernel.",
                        path=path,
                        evidence={
                            "chapter_number": chapter_number,
                            "state_variables": unknown_state_keys,
                        },
                    )
                )

    asset_refs = _refs_from_fields(chapter, _WORLD_ASSET_REF_FIELDS)
    if asset_refs and worldview.asset_ledger:
        asset_lookup = _world_asset_lookup(worldview)
        for asset_ref in asset_refs:
            asset = _lookup_registered_ref(asset_ref, asset_lookup)
            if asset is None:
                findings.append(
                    WorldviewComplianceFinding(
                        code="world_asset_exposure_missing",
                        severity="high",
                        message="Chapter references a world asset that is not registered in the asset ledger.",
                        path=path,
                        evidence={
                            "chapter_number": chapter_number,
                            "asset_ref": asset_ref,
                        },
                    )
                )
                continue
            if not _asset_cost_or_exposure_visible(asset, chapter):
                findings.append(
                    WorldviewComplianceFinding(
                        code="world_asset_cost_missing",
                        severity="high",
                        message="Referenced world asset is used without visible cost or exposure in the chapter plan.",
                        path=path,
                        evidence={
                            "chapter_number": chapter_number,
                            "asset_ref": asset_ref,
                            "cost": asset.cost,
                            "exposure_risk": asset.exposure_risk,
                        },
                    )
                )

    if worldview.authority_claims and active_factions:
        claim_refs = _refs_from_fields(chapter, _AUTHORITY_CLAIM_REF_FIELDS)
        if not claim_refs:
            findings.append(
                WorldviewComplianceFinding(
                    code="authority_claim_missing",
                    severity="warning",
                    message="Chapter activates factions but does not bind the conflict to a registered authority claim.",
                    path=path,
                    evidence={
                        "chapter_number": chapter_number,
                        "active_factions": list(active_factions),
                    },
                )
            )
        else:
            allowed_claim_refs = _authority_claim_refs(worldview)
            unknown_claims = _unknown_refs(claim_refs, allowed_claim_refs)
            if unknown_claims:
                findings.append(
                    WorldviewComplianceFinding(
                        code="authority_claim_missing",
                        severity="warning",
                        message="Chapter authority claim refs do not match registered authority claims.",
                        path=path,
                        evidence={
                            "chapter_number": chapter_number,
                            "authority_claim_refs": unknown_claims,
                        },
                    )
                )

    if worldview.scene_templates:
        template_refs = _refs_from_fields(chapter, _WORLD_SCENE_TEMPLATE_REF_FIELDS)
        if not template_refs:
            findings.append(
                WorldviewComplianceFinding(
                    code="world_scene_template_missing",
                    severity="warning",
                    message="Chapter does not name a world scene template from the worldview kernel.",
                    path=path,
                    evidence={"chapter_number": chapter_number},
                )
            )
        else:
            allowed_template_refs = _scene_template_refs(worldview)
            unknown_templates = _unknown_refs(template_refs, allowed_template_refs)
            if unknown_templates:
                findings.append(
                    WorldviewComplianceFinding(
                        code="world_scene_template_missing",
                        severity="warning",
                        message="Chapter world scene template ref is not registered in the worldview kernel.",
                        path=path,
                        evidence={
                            "chapter_number": chapter_number,
                            "world_scene_template_refs": unknown_templates,
                        },
                    )
                )

    reveal_weight = _int(chapter.get("reveal_weight")) or 0
    if reveal_weight > _DEFAULT_CHAPTER_REVEAL_WEIGHT_BUDGET:
        findings.append(
            WorldviewComplianceFinding(
                code="world_reveal_budget_exceeded",
                severity="high",
                message="Chapter reveal_weight exceeds the default per-chapter worldview reveal budget.",
                path=path,
                evidence={
                    "chapter_number": chapter_number,
                    "reveal_weight": reveal_weight,
                    "budget": _DEFAULT_CHAPTER_REVEAL_WEIGHT_BUDGET,
                },
            )
        )

    anti_copy_hits = _anti_copy_boundary_hits(worldview, chapter)
    if anti_copy_hits:
        findings.append(
            WorldviewComplianceFinding(
                code="world_anti_copy_boundary_hit",
                severity="critical",
                message="Chapter uses a phrase blocked by the worldview anti-copy boundaries.",
                path=path,
                evidence={
                    "chapter_number": chapter_number,
                    "boundaries": anti_copy_hits,
                },
            )
        )

    return findings


def _hydrate_kernel(
    story_design_kernel: Mapping[str, Any] | None,
    findings: list[WorldviewComplianceFinding],
) -> StoryDesignKernel | None:
    if not story_design_kernel:
        findings.append(
            WorldviewComplianceFinding(
                code="story_design_kernel_missing",
                severity="high",
                message="Worldview gate requires a StoryDesignKernel.",
                path="story_design_kernel",
            )
        )
        return None
    try:
        return story_design_kernel_from_dict(dict(story_design_kernel))
    except Exception as exc:
        findings.append(
            WorldviewComplianceFinding(
                code="story_design_kernel_invalid",
                severity="high",
                message="StoryDesignKernel could not be validated for worldview compliance.",
                path="story_design_kernel",
                evidence={"error": str(exc)},
            )
        )
        return None


def _worldview_snapshot(
    worldview: WorldviewKernel | None,
    chapters: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    if worldview is None:
        return {"chapter_count": len(chapters)}
    return {
        "chapter_count": len(chapters),
        "premise": worldview.premise,
        "invariants": [invariant.key for invariant in worldview.invariants],
        "systems": [system.name for system in worldview.systems],
        "factions": [faction.name for faction in worldview.factions],
        "locations": [location.name for location in worldview.locations],
        "reveal_count": len(worldview.reveal_ladder),
        "state_variables": [variable.key for variable in worldview.state_variables],
        "asset_count": len(worldview.asset_ledger),
        "authority_claim_count": len(worldview.authority_claims),
        "scene_template_count": len(worldview.scene_templates),
        "anti_copy_boundary_count": len(worldview.anti_copy_boundaries),
    }


def _allowed_rule_refs(worldview: WorldviewKernel) -> set[str]:
    refs: set[str] = set()
    for invariant in worldview.invariants:
        refs.update(
            _normalize(value)
            for value in (invariant.key, invariant.rule)
            if _normalize(value)
        )
    for system in worldview.systems:
        refs.update(
            _normalize(value)
            for value in (system.name, system.operating_logic)
            if _normalize(value)
        )
    return refs


def _world_state_deltas(chapter: Mapping[str, Any]) -> list[dict[str, Any]]:
    deltas: list[dict[str, Any]] = []
    for field_name in _WORLD_STATE_DELTA_FIELDS:
        deltas.extend(_mapping_list(chapter.get(field_name)))
    return deltas


def _state_delta_keys(deltas: Sequence[Mapping[str, Any]]) -> list[str]:
    keys: list[str] = []
    for delta in deltas:
        key = _first_text_field(delta, ("key", "state_key", "variable", "variable_key"))
        if key:
            keys.append(key)
    return _dedupe(keys)


def _world_asset_lookup(worldview: WorldviewKernel) -> dict[str, Any]:
    lookup: dict[str, Any] = {}
    for asset in worldview.asset_ledger:
        for value in (asset.key, asset.asset_type, asset.value):
            normalized = _normalize(value)
            if normalized:
                lookup[normalized] = asset
    return lookup


def _lookup_registered_ref(ref: str, lookup: Mapping[str, Any]) -> Any | None:
    normalized = _normalize(ref)
    if not normalized:
        return None
    if normalized in lookup:
        return lookup[normalized]
    for allowed_ref, value in lookup.items():
        if allowed_ref and allowed_ref in normalized:
            return value
    return None


def _asset_cost_or_exposure_visible(asset: Any, chapter: Mapping[str, Any]) -> bool:
    chapter_text = _chapter_contract_text(chapter)
    return any(
        _contract_text_visible(chapter_text, value)
        for value in (getattr(asset, "cost", ""), getattr(asset, "exposure_risk", ""))
    )


def _chapter_contract_text(chapter: Mapping[str, Any]) -> str:
    texts = _texts_from_fields(
        chapter,
        (
            *_RULE_LANDING_FIELDS,
            *_REVEAL_FIELDS,
            "world_state_deltas",
            "anti_copy_boundary_notes",
        ),
    )
    return _normalize(" ".join(texts))


def _contract_text_visible(chapter_text: str, value: object) -> bool:
    normalized = _normalize(_text(value))
    if not normalized:
        return False
    return normalized in chapter_text


def _authority_claim_refs(worldview: WorldviewKernel) -> set[str]:
    refs: set[str] = set()
    for claim in worldview.authority_claims:
        refs.update(
            _normalize(value)
            for value in (
                claim.claimant,
                claim.target,
                claim.claim_basis,
                claim.legitimacy,
                claim.escalation_path,
            )
            if _normalize(value)
        )
    return refs


def _scene_template_refs(worldview: WorldviewKernel) -> set[str]:
    refs: set[str] = set()
    for template in worldview.scene_templates:
        refs.update(
            _normalize(value)
            for value in (template.key, template.template_name, template.use_case)
            if _normalize(value)
        )
    return refs


def _anti_copy_boundary_hits(
    worldview: WorldviewKernel,
    chapter: Mapping[str, Any],
) -> list[str]:
    haystack = _normalize(" ".join(_texts_from_fields(chapter, _ANTI_COPY_BOUNDARY_FIELDS)))
    if not haystack:
        return []
    hits = [
        boundary
        for boundary in worldview.anti_copy_boundaries
        if _normalize(boundary) and _normalize(boundary) in haystack
    ]
    return _dedupe(hits)


def _future_reveal_leaks(
    worldview: WorldviewKernel,
    chapter: Mapping[str, Any],
    *,
    chapter_number: int,
    volume_number: int | None,
) -> list[dict[str, Any]]:
    reveal_text = _normalize(" ".join(_texts_from_fields(chapter, _REVEAL_FIELDS)))
    if not reveal_text:
        return []

    leaks: list[dict[str, Any]] = []
    for step in worldview.reveal_ladder:
        if not _reveal_locked(
            step.earliest_chapter,
            step.earliest_volume,
            chapter_number=chapter_number,
            volume_number=volume_number,
        ):
            continue
        normalized_reveal = _normalize(step.reveal)
        if len(normalized_reveal) >= 8 and normalized_reveal in reveal_text:
            leaks.append(
                {
                    "stage": step.stage,
                    "reveal": step.reveal,
                    "earliest_chapter": step.earliest_chapter,
                    "earliest_volume": step.earliest_volume,
                }
            )
    return leaks


def _reveal_locked(
    earliest_chapter: int | None,
    earliest_volume: int | None,
    *,
    chapter_number: int,
    volume_number: int | None,
) -> bool:
    if earliest_volume is not None and volume_number is not None:
        if volume_number < earliest_volume:
            return True
        if volume_number > earliest_volume:
            return False
    if earliest_chapter is not None:
        return chapter_number < earliest_chapter
    return False


def _outline_chapters(
    outline_payload: Mapping[str, Any] | Sequence[Any] | None,
) -> list[dict[str, Any]]:
    if isinstance(outline_payload, Mapping):
        for key in ("chapters", "chapter_outlines"):
            chapters = _mapping_list(outline_payload.get(key))
            if chapters:
                return chapters
        outline = outline_payload.get("outline")
        if isinstance(outline, Mapping):
            return _outline_chapters(outline)
        return []
    return _mapping_list(outline_payload)


def _outline_volume_number(
    outline_payload: Mapping[str, Any] | Sequence[Any] | None,
) -> int | None:
    if not isinstance(outline_payload, Mapping):
        return None
    return _int(outline_payload.get("volume_number")) or _int(outline_payload.get("volume"))


def _chapter_number(chapter: Mapping[str, Any], fallback: int) -> int:
    return (
        _int(chapter.get("chapter_number"))
        or _int(chapter.get("number"))
        or _int(chapter.get("chapter"))
        or fallback
    )


def _refs_from_fields(chapter: Mapping[str, Any], fields: Sequence[str]) -> list[str]:
    refs: list[str] = []
    for field_name in fields:
        refs.extend(_string_list(chapter.get(field_name)))
    return _dedupe([ref for ref in refs if ref])


def _texts_from_fields(chapter: Mapping[str, Any], fields: Sequence[str]) -> list[str]:
    texts: list[str] = []
    for field_name in fields:
        value = chapter.get(field_name)
        if isinstance(value, Mapping):
            texts.extend(_texts_from_mapping(value))
        elif isinstance(value, Sequence) and not isinstance(value, str):
            texts.extend(_text(item) for item in value if _text(item))
        else:
            text = _text(value)
            if text:
                texts.append(text)
    return texts


def _texts_from_mapping(value: Mapping[str, Any]) -> list[str]:
    texts: list[str] = []
    for item in value.values():
        if isinstance(item, Mapping):
            texts.extend(_texts_from_mapping(item))
        elif isinstance(item, Sequence) and not isinstance(item, str):
            texts.extend(_text(part) for part in item if _text(part))
        else:
            text = _text(item)
            if text:
                texts.append(text)
    return texts


def _unknown_refs(refs: Sequence[str], allowed: set[str]) -> list[str]:
    if not refs:
        return []
    if not allowed:
        return list(refs)
    return [ref for ref in refs if not _is_registered_ref(ref, allowed)]


def _is_registered_ref(ref: str, allowed: set[str]) -> bool:
    normalized = _normalize(ref)
    return bool(
        normalized
        and (
            normalized in allowed
            or any(allowed_ref and allowed_ref in normalized for allowed_ref in allowed)
        )
    )


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
        preferred_keys = (
            "key",
            "name",
            "id",
            "rule_key",
            "rule_name",
            "system",
            "location",
            "faction",
            "label",
        )
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


def _int(value: object) -> int | None:
    try:
        return int(value) if value is not None and str(value).strip() else None
    except (TypeError, ValueError):
        return None


def _first_text_field(value: Mapping[str, Any], keys: Sequence[str]) -> str:
    for key in keys:
        text = _text(value.get(key))
        if text:
            return text
    return ""


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
