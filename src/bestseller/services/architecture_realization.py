"""Pure audit of architecture -> outline -> scene -> prose evidence."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

COMEDY_STAGES = ("setup", "incongruity", "reaction", "situation_change")


@dataclass(frozen=True, slots=True)
class RealizationIssue:
    code: str
    ref: str
    detail: str = ""


@dataclass(frozen=True, slots=True)
class ArchitectureRealizationReport:
    passed: bool
    issues: tuple[RealizationIssue, ...] = ()
    realized_units: tuple[str, ...] = ()
    selected_effects: dict[str, tuple[str, ...]] = field(default_factory=dict)

    @property
    def blocking_codes(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(item.code for item in self.issues))

    def to_payload(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "blocking_codes": self.blocking_codes,
            "realized_units": self.realized_units,
            "selected_effects": self.selected_effects,
            "issues": [
                {"code": item.code, "ref": item.ref, "detail": item.detail} for item in self.issues
            ],
        }


def _items(value: object) -> list[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        return [value]
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [x for x in value if isinstance(x, Mapping)]
    return []


def _id(item: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        if item.get(key) is not None and str(item[key]).strip():
            return str(item[key]).strip()
    return ""


def audit_architecture_realization(
    architecture: Mapping[str, Any] | Sequence[Mapping[str, Any]],
    materialization: Mapping[str, Any] | Sequence[Mapping[str, Any]] = (),
    prose: Mapping[str, Any] | Sequence[Mapping[str, Any]] = (),
    receipts: Mapping[str, Any] | Sequence[Mapping[str, Any]] = (),
    *,
    selected_effects: Sequence[str] = (),
) -> ArchitectureRealizationReport:
    """Require every unit to reach prose and a verifiable receipt.

    Inputs are intentionally loose mappings: persisted model adapters can feed
    this audit without making the pure gate depend on SQLAlchemy models.
    """
    issues: list[RealizationIssue] = []
    arch = _items(architecture)
    mats = _items(materialization)
    prose_rows = _items(prose)
    receipt_rows = _items(receipts)
    mat_by = {_id(x, "unit_id", "architecture_unit_id", "ref", "id"): x for x in mats}
    prose_by = {_id(x, "unit_id", "architecture_unit_id", "scene_id", "id"): x for x in prose_rows}
    receipt_by = {
        _id(x, "unit_id", "architecture_unit_id", "scene_id", "id"): x for x in receipt_rows
    }
    realized: list[str] = []
    for unit in arch:
        ref = _id(unit, "unit_id", "id", "ref") or "<missing>"
        mat = mat_by.get(ref)
        if mat is None:
            issues.append(
                RealizationIssue(
                    "ARCHITECTURE_UNIT_ORPHANED", ref, "no volume/chapter/scene materialization"
                )
            )
            continue
        scene_ref = _id(mat, "scene_id", "chapter_id", "id")
        prose_row = prose_by.get(ref) or prose_by.get(scene_ref)
        receipt = receipt_by.get(ref) or receipt_by.get(scene_ref)
        if (
            prose_row is None
            or not str(prose_row.get("text") or prose_row.get("prose") or "").strip()
        ):
            issues.append(RealizationIssue("ARCHITECTURE_UNIT_ORPHANED", ref, "no prose evidence"))
            continue
        if receipt is None:
            issues.append(RealizationIssue("REALIZATION_RECEIPT_MISSING", ref))
        elif mat.get("realization_revision") is not None and receipt.get(
            "realization_revision"
        ) != mat.get("realization_revision"):
            issues.append(RealizationIssue("STALE_REALIZATION_VERDICT", ref))
        choice = mat.get("choice") or mat.get("choice_id") or mat.get("chosen_option_id")
        cost = mat.get("cost") or mat.get("costs") or mat.get("alternative_costs")
        delta = (
            mat.get("state_delta") or mat.get("state_changes") or mat.get("required_state_changes")
        )
        opponent = (
            mat.get("opponent_adaptation")
            or mat.get("opponent_counteraction")
            or mat.get("opponent_strategy")
        )
        if not choice:
            issues.append(
                RealizationIssue(
                    "RESULT_ONLY_STATE_DELTA" if delta else "CHAPTER_CAUSAL_CONTRACT_INCOMPLETE",
                    ref,
                    "choice missing",
                )
            )
        if not cost:
            issues.append(
                RealizationIssue("CHAPTER_CAUSAL_CONTRACT_INCOMPLETE", ref, "cost missing")
            )
        if not delta:
            issues.append(
                RealizationIssue("CHAPTER_CAUSAL_CONTRACT_INCOMPLETE", ref, "state delta missing")
            )
        if not opponent:
            issues.append(RealizationIssue("OPPONENT_LEARNING_UNREALIZED", ref))
        if choice and cost and delta and opponent and receipt is not None:
            realized.append(ref)
    for effect in selected_effects:
        rows = [
            x
            for x in (*mats, *prose_rows)
            if str(x.get("effect") or x.get("effect_key") or "") == effect
        ]
        if not rows:
            issues.append(RealizationIssue("EFFECT_CONTRACT_UNREALIZED", effect))
            continue
        if effect == "comedy_engine":
            stages = {str(x.get("stage") or x.get("comedy_stage") or "") for x in rows}
            stages.update({"incongruity" for x in rows if x.get("misalignment")})
            stages.update(
                {
                    "situation_change"
                    for x in rows
                    if x.get("situation") or x.get("relationship_change")
                }
            )
            missing = tuple(stage for stage in COMEDY_STAGES if stage not in stages)
            for stage in missing:
                issues.append(
                    RealizationIssue(
                        "EFFECT_CONTRACT_UNREALIZED", effect, f"missing comedy stage: {stage}"
                    )
                )
            if not any(x.get("callback") or x.get("callback_ref") for x in rows):
                issues.append(
                    RealizationIssue("EFFECT_CONTRACT_UNREALIZED", effect, "callback missing")
                )
    return ArchitectureRealizationReport(
        not issues,
        tuple(issues),
        tuple(realized),
        {
            effect: tuple(
                _id(x, "unit_id", "scene_id", "id")
                for x in mats
                if str(x.get("effect") or x.get("effect_key") or "") == effect
            )
            for effect in selected_effects
        },
    )


def audit_story_architecture(*args: Any, **kwargs: Any) -> ArchitectureRealizationReport:  # noqa: ANN401
    return audit_architecture_realization(*args, **kwargs)


__all__ = [
    "COMEDY_STAGES",
    "ArchitectureRealizationReport",
    "RealizationIssue",
    "audit_architecture_realization",
    "audit_story_architecture",
]
