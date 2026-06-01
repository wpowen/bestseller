"""Methodology lineage data model and JSON helpers.

The lineage is selected once during chapter planning, then downstream stages
consume it read-only.  It is intentionally stored as plain JSON so it can live
inside ``ChapterContractModel.metadata_json`` without a schema migration.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, TypeAdapter, field_validator, model_validator

METHODOLOGY_LINEAGE_METADATA_KEY = "methodology_lineage"

_STAGE_SLOTS: dict[str, frozenset[str]] = {
    "conception": frozenset(
        {"premise_engine", "character_change_tracker", "worldview_theme"}
    ),
    "outline_volume": frozenset({"payoff_ledger", "pacing_compression_engine"}),
    "outline_chapter": frozenset(
        {
            "character_change_tracker",
            "scene_causality_engine",
            "hook_ledger",
            "payoff_ledger",
            "opening_three_function",
        }
    ),
    "pre_draft": frozenset(
        {
            "scene_causality_engine",
            "hook_ledger",
            "pov_distance_controller",
            "dialogue_subtext_engine",
        }
    ),
    "prose_scene": frozenset(
        {
            "scene_causality_engine",
            "pov_distance_controller",
            "dialogue_subtext_engine",
        }
    ),
    "review": frozenset(
        {
            "scene_causality_engine",
            "hook_ledger",
            "payoff_ledger",
            "opening_three_function",
            "pov_distance_controller",
            "dialogue_subtext_engine",
        }
    ),
    "repair": frozenset({"revision_repair_engine"}),
    "health": frozenset(
        {
            "premise_engine",
            "character_change_tracker",
            "worldview_theme",
            "scene_causality_engine",
            "hook_ledger",
            "payoff_ledger",
            "pacing_compression_engine",
            "opening_three_function",
            "pov_distance_controller",
            "dialogue_subtext_engine",
            "revision_repair_engine",
        }
    ),
}


class AppliedMethodology(BaseModel):
    """A single methodology rule selected for a chapter.

    Migrated from frozen dataclass to Pydantic BaseModel for stronger
    typing, automatic JSON serialisation, and TypeAdapter compatibility
    in the rest of the lineage machinery.  Existing ``to_dict()`` /
    ``from_dict()`` helpers are preserved as thin shims so callers that
    round-trip through dicts (e.g. ``workflows.py:1580``) keep working.
    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    rule_id: str
    slot: str
    craft_function: str
    target_artifact_path: str
    application_hint: str
    evidence_fields: tuple[str, ...] = ()
    verifiability: str = "advisory"
    gate_mode: str = "advisory"
    indicator_targets: tuple[str, ...] = ()
    source_lineage: str = ""
    why_selected: str = ""

    @field_validator("rule_id", "slot", "target_artifact_path")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        cleaned = (value or "").strip()
        if not cleaned:
            raise ValueError("AppliedMethodology field is required.")
        return cleaned

    @field_validator("verifiability")
    @classmethod
    def _verifiability_ok(cls, value: str) -> str:
        if value not in {"strict", "heuristic", "advisory"}:
            raise ValueError(f"Unsupported verifiability: {value!r}.")
        return value

    @field_validator("gate_mode")
    @classmethod
    def _gate_mode_ok(cls, value: str) -> str:
        if value not in {"advisory", "warn", "block"}:
            raise ValueError(f"Unsupported gate_mode: {value!r}.")
        return value

    @model_validator(mode="after")
    def _evidence_required(self) -> AppliedMethodology:
        if not self.evidence_fields:
            raise ValueError("AppliedMethodology.evidence_fields is required.")
        return self

    def to_dict(self) -> dict[str, Any]:
        payload = self.model_dump(mode="json")
        payload["evidence_fields"] = list(self.evidence_fields)
        payload["indicator_targets"] = list(self.indicator_targets)
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> AppliedMethodology:
        return cls(
            rule_id=str(payload.get("rule_id") or ""),
            slot=str(payload.get("slot") or ""),
            craft_function=str(payload.get("craft_function") or payload.get("slot") or ""),
            target_artifact_path=str(payload.get("target_artifact_path") or ""),
            application_hint=str(payload.get("application_hint") or ""),
            evidence_fields=tuple(
                str(item)
                for item in _sequence(payload.get("evidence_fields"))
                if str(item).strip()
            ),
            verifiability=str(payload.get("verifiability") or "advisory"),
            gate_mode=str(payload.get("gate_mode") or "advisory"),
            indicator_targets=tuple(
                str(item)
                for item in _sequence(payload.get("indicator_targets"))
                if str(item).strip()
            ),
            source_lineage=str(payload.get("source_lineage") or ""),
            why_selected=str(payload.get("why_selected") or ""),
        )


class MethodologyLineage(BaseModel):
    """Selection of methodology rules for a single chapter.

    Migrated to Pydantic BaseModel.  The lineage is selected once during
    chapter planning, then downstream stages consume it read-only via
    :func:`render_methodology_lineage_prompt_block` and the slot-based
    health machinery.  TypeAdapter (``MethodologyLineageAdapter``) gives
    callers a single-call validation path for dict / dataclass / Pydantic
    inputs, eliminating the older ``methodology_lineage_from_object``
    branching.
    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    chapter_no: int
    genre_profile: str = ""
    chapter_role: str = ""
    selected: tuple[AppliedMethodology, ...] = ()
    selection_seed: str = ""
    budget_tokens: int = 1
    budget_cards: int = 1

    @field_validator("chapter_no")
    @classmethod
    def _chapter_no_positive(cls, value: int) -> int:
        if value < 1:
            raise ValueError("MethodologyLineage.chapter_no must be >= 1.")
        return value

    @model_validator(mode="after")
    def _budgets_consistent(self) -> MethodologyLineage:
        if self.budget_cards < 1:
            raise ValueError("MethodologyLineage.budget_cards must be >= 1.")
        if self.budget_tokens < 1:
            raise ValueError("MethodologyLineage.budget_tokens must be >= 1.")
        if len(self.selected) > self.budget_cards:
            raise ValueError("MethodologyLineage.selected exceeds budget_cards.")
        return self

    def for_slot(self, slot: str) -> tuple[AppliedMethodology, ...]:
        return tuple(item for item in self.selected if item.slot == slot)

    def for_stage(self, stage: str) -> tuple[AppliedMethodology, ...]:
        slots = _STAGE_SLOTS.get(stage, frozenset())
        return tuple(item for item in self.selected if item.slot in slots)

    def strict_only(self) -> tuple[AppliedMethodology, ...]:
        return tuple(item for item in self.selected if item.verifiability == "strict")

    def to_dict(self) -> dict[str, Any]:
        return {
            "chapter_no": self.chapter_no,
            "genre_profile": self.genre_profile,
            "chapter_role": self.chapter_role,
            "selected": [item.to_dict() for item in self.selected],
            "selection_seed": self.selection_seed,
            "budget_tokens": self.budget_tokens,
            "budget_cards": self.budget_cards,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> MethodologyLineage:
        return cls(
            chapter_no=int(payload.get("chapter_no") or 0),
            genre_profile=str(payload.get("genre_profile") or ""),
            chapter_role=str(payload.get("chapter_role") or ""),
            selected=tuple(
                AppliedMethodology.from_dict(item)
                for item in _sequence(payload.get("selected"))
                if isinstance(item, Mapping)
            ),
            selection_seed=str(payload.get("selection_seed") or ""),
            budget_tokens=int(payload.get("budget_tokens") or 1),
            budget_cards=int(payload.get("budget_cards") or 1),
        )


# Single-call TypeAdapter for the lineage so call sites can do
# ``MethodologyLineageAdapter.validate_python(payload)`` without
# branching on dict / dataclass / Pydantic input shape.
MethodologyLineageAdapter: TypeAdapter[MethodologyLineage] = TypeAdapter(
    MethodologyLineage
)


def methodology_lineage_to_dict(lineage: MethodologyLineage) -> dict[str, Any]:
    return lineage.to_dict()


def methodology_lineage_from_dict(payload: Mapping[str, Any]) -> MethodologyLineage:
    return MethodologyLineage.from_dict(payload)


def attach_methodology_lineage(
    metadata: Mapping[str, Any] | None,
    lineage: MethodologyLineage,
) -> dict[str, Any]:
    updated = dict(metadata or {})
    updated[METHODOLOGY_LINEAGE_METADATA_KEY] = lineage.to_dict()
    return updated


def methodology_lineage_from_metadata(
    metadata: Mapping[str, Any] | None,
) -> MethodologyLineage | None:
    raw = (metadata or {}).get(METHODOLOGY_LINEAGE_METADATA_KEY)
    if not isinstance(raw, Mapping):
        return None
    return MethodologyLineage.from_dict(raw)


def methodology_lineage_from_object(value: object) -> MethodologyLineage | None:
    if value is None:
        return None
    if isinstance(value, MethodologyLineage):
        return value
    if isinstance(value, Mapping):
        raw = value.get(METHODOLOGY_LINEAGE_METADATA_KEY)
        if raw is None and _looks_like_lineage_payload(value):
            raw = value
        if isinstance(raw, Mapping):
            return MethodologyLineage.from_dict(raw)
        return None
    raw_attr = getattr(value, METHODOLOGY_LINEAGE_METADATA_KEY, None)
    if isinstance(raw_attr, Mapping):
        return MethodologyLineage.from_dict(raw_attr)
    if hasattr(value, "model_dump"):
        dumped = value.model_dump(mode="json")
        if isinstance(dumped, Mapping):
            return methodology_lineage_from_object(dumped)
    return None


def _looks_like_lineage_payload(value: Mapping[str, Any]) -> bool:
    return "chapter_no" in value and "selected" in value and "selection_seed" in value


def render_methodology_lineage_prompt_block(
    value: object,
    *,
    stage: str,
    language: str | None = None,
    max_cards: int = 6,
) -> str:
    lineage = methodology_lineage_from_object(value)
    if lineage is None:
        return ""
    selected = lineage.for_stage(stage)[:max_cards]
    if not selected:
        return ""
    is_en = str(language or "").lower().startswith("en")
    heading = (
        "[Methodology lineage - read only]"
        if is_en
        else "【方法论 lineage(read only; do not reselect)】"
    )
    lines = [
        heading,
        (
            f"- Chapter role: {lineage.chapter_role}; seed: {lineage.selection_seed}"
            if is_en
            else f"- chapter_role: {lineage.chapter_role}; selection_seed: {lineage.selection_seed}"
        ),
    ]
    for item in selected:
        if stage == "review":
            evidence = ", ".join(item.evidence_fields)
            line = f"- {item.rule_id} [{item.slot}/{item.gate_mode}]: verify {evidence}"
        else:
            line = (
                f"- {item.rule_id} [{item.slot} -> {item.target_artifact_path}]: "
                f"{item.application_hint}"
            )
        lines.append(line)
    return "\n".join(lines)


def methodology_lineage_review_expectations(value: object) -> list[tuple[str, str | None]]:
    lineage = methodology_lineage_from_object(value)
    if lineage is None:
        return []
    payload = _mapping_from_object(value)
    expectations: list[tuple[str, str | None]] = []
    for item in lineage.for_stage("review"):
        for field_path in item.evidence_fields:
            expected = _resolve_field_path(payload, field_path)
            if expected:
                expectations.append(
                    (
                        f"methodology:{item.rule_id}:{field_path}",
                        expected,
                    )
                )
    return expectations


def _mapping_from_object(value: object) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump(mode="json")
        return dict(dumped) if isinstance(dumped, Mapping) else {}
    raw = getattr(value, "__dict__", None)
    if isinstance(raw, Mapping):
        return {
            str(key): item
            for key, item in raw.items()
            if not str(key).startswith("_")
        }
    return {}


def _resolve_field_path(payload: Mapping[str, Any], field_path: str) -> str | None:
    parts = [part for part in str(field_path).split(".") if part]
    value: object = payload
    for part in parts:
        if isinstance(value, Mapping) and part in value:
            value = value[part]
            continue
        value = None
        break
    if value is None and parts:
        value = payload.get(parts[-1])
    if isinstance(value, (list, tuple, set)):
        text = "; ".join(str(item).strip() for item in value if str(item).strip())
    elif isinstance(value, Mapping):
        text = "; ".join(
            str(item).strip()
            for item in value.values()
            if isinstance(item, str) and item.strip()
        )
    else:
        text = str(value or "").strip()
    return text or None


def _sequence(value: object) -> tuple[object, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, tuple):
        return value
    if isinstance(value, list):
        return tuple(value)
    return (value,)
