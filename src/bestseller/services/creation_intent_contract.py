"""Creation-boundary contracts shared by planning and later materialization.

This module deliberately composes :class:`GenreIntentContract`; it does not
redefine the taxonomy.  The contract is a small, deterministic value object
that makes every creation option auditable and gives explicit V1/V2 attempts a
stable diff surface before a pipeline is allowed to promote a snapshot.
"""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from bestseller.domain.concept_lab import ConceptLabBundle
from bestseller.domain.enums import (
    ConceptionMode,
    IntentDiffDecision,
    IntentDiffSeverity,
    IntentFieldSource,
    ProjectType,
)
from bestseller.services.genre_intent_contract import GenreIntentContract
from bestseller.services.story_enhancers import StoryEnhancerSelection


class _FrozenSourceMap(dict[str, IntentFieldSource]):
    """Small immutable dict used to keep contract hashes stable after build."""

    def _immutable(self, *args: object, **kwargs: object) -> None:
        raise TypeError("creation intent field_sources is immutable")

    __setitem__ = _immutable
    __delitem__ = _immutable
    clear = _immutable
    pop = _immutable
    popitem = _immutable
    setdefault = _immutable
    update = _immutable


class CreationIntentContract(BaseModel, frozen=True):
    """Immutable, creation-time input contract.

    ``genre_intent`` remains the single taxonomy authority.  The other fields
    are the complete set of creation choices, while ``field_sources`` records
    whether each value was explicit, defaulted, derived, or inferred from a
    legacy project.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["creation-intent.v1"] = "creation-intent.v1"
    genre_intent: GenreIntentContract
    audience_orientation: str | None = Field(default=None, max_length=120)
    narrative_scale: str | None = Field(default=None, max_length=120)
    tone_preference: str | None = Field(default=None, max_length=240)
    chapter_count: int = Field(default=500, ge=1, le=2000)
    length_key: str = Field(default="long", min_length=1, max_length=80)
    pov: str = Field(default="third-limited", min_length=1, max_length=120)
    draft_mode: bool = False
    stop_after_conception: bool = False
    llm_model_id: str | None = Field(default=None, max_length=160)
    story_enhancers: StoryEnhancerSelection = Field(default_factory=StoryEnhancerSelection)
    concept_lab: ConceptLabBundle | dict[str, Any] | None = None
    creative_direction: str | None = Field(default=None, max_length=240)
    concept_seed: str | None = Field(default=None, max_length=1000)
    hook_spec: dict[str, Any] = Field(default_factory=dict)
    language: str = Field(default="zh-CN", min_length=2, max_length=20)
    project_type: ProjectType = ProjectType.LINEAR
    creation_mode: str = Field(default="long_serial", min_length=1, max_length=80)
    field_sources: dict[str, IntentFieldSource] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _normalise_sources(self) -> CreationIntentContract:
        known = set(type(self).model_fields) - {"schema_version", "field_sources"}
        sources = dict(self.field_sources)
        unknown = set(sources) - known
        if unknown:
            raise ValueError(f"unknown creation intent source fields: {sorted(unknown)}")
        # Direct model construction remains safe and auditable: omitted source
        # entries are system defaults, while nested genre provenance is kept as
        # its own contract and is not duplicated here.
        if "genre_intent" not in sources:
            sources["genre_intent"] = (
                IntentFieldSource.LEGACY
                if self.genre_intent.source == "legacy_inferred"
                else IntentFieldSource.EXPLICIT
            )
        for name in known:
            sources.setdefault(name, IntentFieldSource.DEFAULT)
        object.__setattr__(self, "field_sources", _FrozenSourceMap(sources))
        return self

    def contract_hash(self) -> str:
        """Return a stable hash independent of dict insertion order."""

        payload = self.model_dump(mode="json")
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def source_for(self, field_name: str) -> IntentFieldSource:
        try:
            return self.field_sources[field_name]
        except KeyError as exc:
            raise KeyError(f"unknown creation intent field: {field_name}") from exc

    @property
    def genre_key(self) -> str:
        return self.genre_intent.genre_key

    @property
    def prompt_pack_key(self) -> str:
        return self.genre_intent.prompt_pack_key


class ConceptionAttemptInput(BaseModel, frozen=True):
    """Complete input envelope for one initial or revision conception."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["conception-input.v1"] = "conception-input.v1"
    conception_mode: ConceptionMode = ConceptionMode.INITIAL
    contract: CreationIntentContract
    input_payload: dict[str, Any] = Field(default_factory=dict)
    reason: str | None = Field(default=None, max_length=1000)
    base_snapshot_id: UUID | str | None = None
    attempt_id: str = Field(min_length=1, max_length=200)
    idempotency_key: str = Field(min_length=1, max_length=300)

    @model_validator(mode="after")
    def _validate_revision_requirements(self) -> ConceptionAttemptInput:
        if self.conception_mode == ConceptionMode.REVISION:
            if not str(self.reason or "").strip():
                raise ValueError("revision conception requires a reason")
            if self.base_snapshot_id is None:
                raise ValueError("revision conception requires base_snapshot_id")
        return self

    def input_hash(self) -> str:
        payload = self.model_dump(mode="json")
        payload.pop("idempotency_key", None)
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class IntentDiffItem(BaseModel, frozen=True):
    """One deterministic field-level difference between two attempts."""

    path: str = Field(min_length=1, max_length=400)
    old_value: Any = None
    new_value: Any = None
    source: IntentFieldSource = IntentFieldSource.DERIVED
    severity: IntentDiffSeverity
    decision: IntentDiffDecision = IntentDiffDecision.UNRESOLVED
    resolved_value: Any = None
    resolved_by: str | None = None


class IntentDiff(BaseModel, frozen=True):
    """Diff result consumed by reconciliation/promotion code."""

    schema_version: Literal["conception-diff.v1"] = "conception-diff.v1"
    items: tuple[IntentDiffItem, ...] = ()

    @property
    def hard_conflicts(self) -> tuple[IntentDiffItem, ...]:
        return tuple(item for item in self.items if item.severity == IntentDiffSeverity.HARD)

    @property
    def soft_diffs(self) -> tuple[IntentDiffItem, ...]:
        return tuple(item for item in self.items if item.severity == IntentDiffSeverity.SOFT)

    @property
    def has_hard_conflicts(self) -> bool:
        return bool(self.hard_conflicts)


# Identity and user promise fields cannot be auto-selected during V2
# reconciliation.  Additive planning detail is soft by design.
_HARD_DIFF_PATHS = {
    "genre_intent.genre_key",
    "genre_intent.sub_genre_key",
    "genre_intent.prompt_pack_key",
    "genre_intent.allowed_modernity",
    "audience_orientation",
    "creation_mode",
    "project_type",
    "hook_spec",
    # These paths are also accepted when diffing materialised V1/V2 payloads
    # (rather than the contract itself), so identity drift cannot be hidden in
    # a generic metadata merge.
    "protagonist.name",
    "protagonist.age",
    "protagonist.core_wound",
    "book_spec.protagonist.name",
    "book_spec.protagonist.age",
    "book_spec.protagonist.core_wound",
    "reader_promise",
    "core_reader_promise",
    "opening_hook",
    "hook",
    "logline",
    "one_liner",
}


def _as_json(value: object) -> object:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, Mapping):
        return {str(key): _as_json(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_as_json(item) for item in value]
    return value


def _iter_differences(old: object, new: object, path: str = "") -> list[tuple[str, Any, Any]]:
    old = _as_json(old)
    new = _as_json(new)
    if isinstance(old, Mapping) and isinstance(new, Mapping):
        paths = sorted(set(old) | set(new))
        result: list[tuple[str, Any, Any]] = []
        for key in paths:
            child = f"{path}.{key}" if path else str(key)
            result.extend(_iter_differences(old.get(key), new.get(key), child))
        return result
    if old != new:
        return [(path, old, new)]
    return []


def _severity_for(path: str) -> IntentDiffSeverity:
    if path in _HARD_DIFF_PATHS or any(
        path.startswith(f"{prefix}.") or path.endswith(f".{prefix}")
        for prefix in _HARD_DIFF_PATHS
    ):
        return IntentDiffSeverity.HARD
    return IntentDiffSeverity.SOFT


def diff_creation_intents(
    old: CreationIntentContract | Mapping[str, Any],
    new: CreationIntentContract | Mapping[str, Any],
) -> IntentDiff:
    """Produce a deterministic field diff for V1/V2 reconciliation."""

    old_data = old.model_dump(mode="json") if isinstance(old, BaseModel) else dict(old)
    new_data = new.model_dump(mode="json") if isinstance(new, BaseModel) else dict(new)
    items = tuple(
        IntentDiffItem(
            path=path,
            old_value=old_value,
            new_value=new_value,
            severity=_severity_for(path),
        )
        for path, old_value, new_value in _iter_differences(old_data, new_data)
    )
    return IntentDiff(items=items)


def build_creation_intent_contract(
    genre_intent: GenreIntentContract,
    *,
    field_sources: Mapping[str, IntentFieldSource | str] | None = None,
    **values: object,
) -> CreationIntentContract:
    """Build the frozen contract without resolving genre a second time."""

    sources = {
        key: IntentFieldSource(value)
        for key, value in (field_sources or {}).items()
    }
    return CreationIntentContract(
        genre_intent=genre_intent,
        field_sources=sources,
        **values,
    )


def contract_from_payload(payload: Mapping[str, Any]) -> CreationIntentContract:
    """Load a persisted creation contract; malformed data fails closed."""

    raw = payload.get("creation_intent_contract", payload)
    if not isinstance(raw, Mapping):
        raise ValueError("creation intent contract must be an object")
    return CreationIntentContract.model_validate(dict(raw))
