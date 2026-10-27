"""Pure post-rebind identity diagnosis; no repair, storage or story authority.

The raw paid proposal and the exact existing rebind output remain separate.
Passing these necessary domain checks is not semantic approval. Collision
locations are diagnostics, never evidence that two people may be merged.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field
import json
import re
from typing import Any

from bestseller.services.narrative_contracts import (
    NarrativeContractReport,
    NarrativeContractViolation,
    validate_commercial_zh_identity_policy,
    validate_foundation_identity_contract,
)
from bestseller.services.prompt_source_policy import content_hash

POLICY = "source-bound-cast-candidate.v1"
_IDENTITY_FIELDS = (
    "name",
    "gender",
    "pronoun_set_zh",
    "pronoun_set_en",
    "aliases",
    "alias",
    "also_known_as",
    "name_variants",
    "nicknames",
    "id",
    "canonical_id",
    "entity_id",
)
_LOCATION = re.compile(r"^cast_spec\.(protagonist|antagonist|supporting_cast\[(\d+)\])(?:\.(.+))?$")


@dataclass(frozen=True)
class SourceBoundCastCandidate:
    original: dict[str, Any] = field(repr=False)
    candidate: dict[str, Any] | None = field(repr=False)
    raw_hash: str
    final_hash: str | None
    baseline_hash: str
    canonical_protagonist_hash: str
    report: NarrativeContractReport
    reports: tuple[NarrativeContractReport, ...]
    unresolved_targets: tuple[dict[str, Any], ...]

    def to_dict(self) -> dict[str, Any]:
        return deepcopy(
            {
                "schema": POLICY,
                "original": self.original,
                "candidate": self.candidate,
                "raw_hash": self.raw_hash,
                "final_hash": self.final_hash,
                "baseline_hash": self.baseline_hash,
                "canonical_protagonist_hash": self.canonical_protagonist_hash,
                "report": self.report.to_dict(),
                "reports": [report.to_dict() for report in self.reports],
                "unresolved_targets": list(self.unresolved_targets),
                "preparation": "existing_source_bound_rebind",
                "changed_from_raw": self.final_hash is not None
                and self.final_hash != self.raw_hash,
                "additional_identity_repair_applied": False,
                "semantic_approval_asserted": False,
                "automatic_merge_authorized": False,
            }
        )


def _member(candidate: dict[str, Any], location: str) -> dict[str, Any] | None:
    match = _LOCATION.fullmatch(location)
    if match is None:
        return None
    path: list[str | int] = (
        ["supporting_cast", int(match[2])] if match[2] is not None else [match[1]]
    )
    node: Any = candidate
    try:
        for key in path:
            node = node[key]
    except (KeyError, IndexError, TypeError):
        return None
    if not isinstance(node, Mapping) or not isinstance(node.get("name"), str) or not node["name"]:
        return None
    return {
        "path": path,
        "name": node["name"],
        "member_hash": content_hash(node),
        "identity_fields": {key: deepcopy(node[key]) for key in _IDENTITY_FIELDS if key in node},
    }


def _targets(
    candidate: dict[str, Any] | None, reports: tuple[NarrativeContractReport, ...]
) -> tuple[dict[str, Any], ...]:
    targets = []
    for report in reports:
        for index, violation in enumerate(report.violations):
            if violation not in report.blocking_violations:
                continue
            target = {
                "gate": report.gate_name,
                "violation_index": index,
                "code": violation.code,
                "location": violation.location,
                "violation": violation.to_dict(),
                "location_bound": False,
                "members": [],
                "fields": [],
                "automatic_merge_authorized": False,
            }
            if candidate is not None and violation.code == "FOUNDATION_IDENTITY_ALIAS_COLLISION":
                locations = [
                    violation.metadata.get("first_location"),
                    violation.metadata.get("second_location"),
                ]
                members = [
                    _member(candidate, location) if isinstance(location, str) else None
                    for location in locations
                ]
                target.update(
                    kind="identity_collision",
                    collision_literal=violation.metadata.get("alias"),
                    members=[member for member in members if member is not None],
                    location_bound=all(member is not None for member in members),
                    identity_equivalence_asserted=False,
                    source_identity_evidence_required=True,
                )
            else:
                target["kind"] = "unresolved_identity"
                member = _member(candidate, violation.location) if candidate is not None else None
                if member is not None:
                    fields = []
                    match = _LOCATION.fullmatch(violation.location)
                    suffix = match[3] if match is not None else None
                    keys = (
                        ("pronoun_set_zh", "pronoun_set_en")
                        if suffix == "pronouns"
                        else (suffix,)
                        if suffix in _IDENTITY_FIELDS
                        else ()
                    )
                    values = member["identity_fields"]
                    for key in keys:
                        if suffix == "pronouns" and values.get(key):
                            continue
                        fields.append(
                            {
                                "path": [*member["path"], key],
                                "present": key in values,
                                "value": deepcopy(values.get(key)),
                            }
                        )
                    target.update(location_bound=bool(fields), members=[member], fields=fields)
            targets.append(target)
    return tuple(deepcopy(targets))


def prepare_source_bound_cast_candidate(
    *,
    project: object,
    baseline: Mapping[str, Any],
    raw: Mapping[str, Any],
    canonical_protagonist: Mapping[str, Any],
) -> SourceBoundCastCandidate:
    """Prepare the exact rebind view and diagnose it without guessing fixes.

    Caller supplies the actual selected canonical protagonist, as required by
    the existing rebind. All unknown fields and duplicate entries stay present.
    No legacy identity repair, pronoun inference beyond the existing rebind,
    alias deletion, roster deduplication, model call or settings lookup occurs.
    The final runtime gate and independent semantic review remain necessary.
    """
    if not all(isinstance(value, Mapping) for value in (baseline, raw, canonical_protagonist)):
        raise ValueError("source_bound_cast_candidate_mappings_required")
    original, source, canonical = (
        deepcopy(dict(value)) for value in (raw, baseline, canonical_protagonist)
    )
    # Reject non-JSON/nonfinite inputs without normalizing their story fields.
    json.dumps([original, source, canonical], ensure_ascii=False, allow_nan=False)
    from bestseller.services import planner

    candidate = None
    try:
        candidate = planner._rebind_source_bound_cast_expansion(
            deepcopy(source), deepcopy(original), canonical_protagonist=deepcopy(canonical)
        )
    except (ValueError, TypeError, KeyError) as exc:
        reports = (
            NarrativeContractReport(
                gate_name="source_bound_cast_rebind",
                violations=(
                    NarrativeContractViolation(
                        code="SOURCE_BOUND_CAST_REBIND_FAILED",
                        location="cast_spec",
                        message=str(exc),
                        metadata={"error_type": type(exc).__name__},
                    ),
                ),
            ),
        )
    else:
        # Use the same validators as the final planner gate, on disposable
        # copies. Deliberately do not run its legacy mutation/inference helpers.
        reports = (
            validate_foundation_identity_contract(deepcopy(candidate)),
            validate_commercial_zh_identity_policy(
                deepcopy(candidate),
                language=getattr(project, "language", None) or "zh-CN",
                premise_text=planner._cast_identity_policy_premise(project),
            ),
        )
    report = NarrativeContractReport(
        gate_name="source_bound_cast_candidate",
        violations=tuple(violation for item in reports for violation in item.violations),
        warnings=tuple(warning for item in reports for warning in item.warnings),
    )
    return SourceBoundCastCandidate(
        original=original,
        candidate=deepcopy(candidate),
        raw_hash=content_hash(original),
        final_hash=content_hash(candidate) if candidate is not None else None,
        baseline_hash=content_hash(source),
        canonical_protagonist_hash=content_hash(canonical),
        report=deepcopy(report),
        reports=deepcopy(reports),
        unresolved_targets=_targets(candidate, reports),
    )


def bind_completed_source_bound_cast(
    *,
    project: object,
    projection: Mapping[str, Any],
    raw: Mapping[str, Any],
    canonical_protagonist: Mapping[str, Any],
) -> dict[str, Any]:
    """Bind a complete proposal without activating the projection's whole roster.

    The caller proves the canonical source independently. This receipt records
    selection only, never source authority, identity-gate success or admission.
    Root runtime metadata is excluded from story identity. A prior receipt's
    raw digest is only a lineage reference, not paid-producer evidence.
    """
    from bestseller.services import planner
    from bestseller.services.source_bound_planning_submission import parse_planning_submission

    if not all(isinstance(value, Mapping) for value in (projection, raw, canonical_protagonist)):
        raise ValueError("source_bound_complete_cast_mappings_required")
    original, source, canonical = (
        deepcopy(dict(value)) for value in (raw, projection, canonical_protagonist)
    )
    json.dumps([original, source, canonical], ensure_ascii=False, allow_nan=False)
    # Validate the native shape without replacing it with Pydantic defaults.
    parse_planning_submission(
        {"content": json.dumps(original, ensure_ascii=False, allow_nan=False)}, kind="cast_spec"
    )

    def story(payload: Mapping[str, Any]) -> dict[str, Any]:
        return {key: deepcopy(value) for key, value in payload.items() if key != "_meta"}

    def members(payload: Mapping[str, Any]) -> list[tuple[str, Mapping[str, Any]]]:
        result = []
        for key in ("protagonist", "antagonist"):
            value = payload.get(key)
            if isinstance(value, Mapping) and isinstance(value.get("name"), str) and value["name"]:
                result.append((key, value))
        for key in ("supporting_cast", "antagonists"):
            for index, value in enumerate(payload.get(key) or []):
                if (
                    isinstance(value, Mapping)
                    and isinstance(value.get("name"), str)
                    and value["name"]
                ):
                    result.append((f"{key}[{index}]", value))
        return result

    source_by_name: dict[str, list[Mapping[str, Any]]] = {}
    for _, person in members(source):
        source_by_name.setdefault(person["name"], []).append(person)
    for path, person in members(original):
        matches = source_by_name.get(person["name"], [])
        if len(matches) > 1:
            raise ValueError(f"source_bound_complete_cast_ambiguous_identity:{path}")
        known = canonical if path == "protagonist" else matches[0] if matches else None
        if known is None:
            continue
        normalized_known = planner._normalize_source_bound_character_identity(known)
        normalized_person = planner._normalize_source_bound_character_identity(person)
        for key in ("gender", "pronoun_set_zh", "pronoun_set_en"):
            # Explicit unknown is absence. Derived source defaults are not locks.
            if known.get(key) in (None, "", "unknown"):
                continue
            proposed = normalized_person.get(key)
            if proposed not in (None, "", "unknown") and proposed != normalized_known.get(key):
                raise ValueError(f"source_bound_complete_cast_identity_conflict:{path}.{key}")

    bound = planner._rebind_source_bound_cast_expansion(
        deepcopy(original), deepcopy(original), canonical_protagonist=deepcopy(canonical)
    )
    # Self-rebind must not become a partial projection or erase creative roots.
    for key, value in story(original).items():
        if key not in {"protagonist", "antagonist", "supporting_cast"} and bound.get(key) != value:
            raise ValueError(f"source_bound_complete_cast_root_changed:{key}")

    def roster(payload: Mapping[str, Any]) -> list[dict[str, str]]:
        return [{"path": path, "name": person["name"]} for path, person in members(payload)]

    receipt = {
        "schema": "source-bound-completed-cast-selection.v1",
        "raw_story_hash": content_hash(story(original)),
        "projection_story_hash": content_hash(story(source)),
        "canonical_protagonist_hash": content_hash(canonical),
        "bound_story_hash": content_hash(story(bound)),
        "projected_roster": roster(source),
        "complete_roster": roster(bound),
        "projection_role": "independent_semantic_source_baseline",
        "selection_only": True,
        "author_selection_asserted": False,
        "admission_asserted": False,
        "grade_asserted": False,
        "registry_obligations_asserted": False,
        "force_reference_status": "not_evaluated",
    }
    prior_meta = original.get("_meta")
    prior = prior_meta.get("completed_cast_selection") if isinstance(prior_meta, Mapping) else None
    if isinstance(prior, Mapping) and set(prior) == set(receipt):
        raw_digest = prior.get("raw_story_hash")
        if (
            isinstance(raw_digest, str)
            and re.fullmatch(r"[0-9a-f]{64}", raw_digest)
            and receipt["raw_story_hash"] == receipt["bound_story_hash"]
            and all(
                prior[key] == value for key, value in receipt.items() if key != "raw_story_hash"
            )
        ):
            receipt["raw_story_hash"] = raw_digest
    bound["_meta"]["completed_cast_selection"] = receipt
    return bound
