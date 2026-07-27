"""Immutable semantic source of truth for a book's creation boundary.

The snapshot is deliberately a small, JSON-safe domain contract.  It composes
the existing creation-intent contract, assigns deterministic entity IDs, and
provides a deterministic report for assets that drift from identity, tone, or
snapshot lineage.  Legacy payloads are accepted at this boundary so callers
can migrate without making old metadata authoritative again.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import hashlib
import json
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from bestseller.services.creation_intent_contract import (
    CreationIntentContract,
)
from bestseller.services.creation_intent_contract import (
    contract_from_payload as creation_contract_from_payload,
)
from bestseller.services.genre_intent_contract import GenreIntentContract, contract_from_selection

BOOK_DESIGN_SNAPSHOT_VERSION = "book-design.v1"


def _normalise_text(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _coerce_creation_intent(
    value: CreationIntentContract | Mapping[str, Any],
) -> CreationIntentContract:
    if isinstance(value, CreationIntentContract):
        return value
    raw = dict(value)
    nested = raw.get("genre_intent")
    if isinstance(nested, Mapping) and "prompt_pack_key" not in nested:
        legacy_selection = dict(nested)
        legacy_selection.setdefault(
            "genre", legacy_selection.get("genre_key") or legacy_selection.get("genre_label")
        )
        legacy_selection.setdefault("channel", legacy_selection.get("channel_key"))
        legacy_selection.setdefault("sub_genre", legacy_selection.get("sub_genre_key"))
        try:
            resolved_intent = contract_from_selection(
                legacy_selection,
                source="legacy_inferred",
                audience_orientation=legacy_selection.get("audience_orientation"),
                narrative_scale=legacy_selection.get("narrative_scale"),
                tone_preference=raw.get("tone_preference")
                or legacy_selection.get("tone_preference"),
            )
        except ValueError:
            legacy_genre = _normalise_text(
                legacy_selection.get("genre_key")
                or legacy_selection.get("genre")
                or legacy_selection.get("genre_label")
                or "legacy-fiction"
            )
            resolved_intent = GenreIntentContract(
                source="legacy_inferred",
                channel_key=_normalise_text(legacy_selection.get("channel_key")) or None,
                genre_key=legacy_genre,
                genre_label=_normalise_text(legacy_selection.get("genre_label"))
                or legacy_genre,
                sub_genre_key=_normalise_text(legacy_selection.get("sub_genre_key")) or None,
                sub_genre_label=_normalise_text(legacy_selection.get("sub_genre_label"))
                or None,
                tags=tuple(legacy_selection.get("tags") or ()),
                prompt_pack_key=_normalise_text(
                    legacy_selection.get("prompt_pack_key")
                    or legacy_selection.get("pack")
                    or "legacy-general-fiction"
                ),
                audience_orientation=legacy_selection.get("audience_orientation"),
                narrative_scale=legacy_selection.get("narrative_scale"),
                tone_preference=raw.get("tone_preference")
                or legacy_selection.get("tone_preference"),
            )
        raw["genre_intent"] = resolved_intent.model_dump(mode="json")
    elif "genre_intent" not in raw and "genre" in raw:
        raw = contract_from_selection(raw, tone_preference=raw.get("tone_preference")).model_dump(
            mode="json"
        )
        raw["chapter_count"] = value.get("chapter_count", 500)
    return creation_contract_from_payload(raw)


def _entity_key(entity_type: str, name: str) -> str:
    compact_name = re.sub(r"\s+", "", _normalise_text(name)).casefold()
    return f"{_normalise_text(entity_type).lower()}:{compact_name}"


def stable_entity_id(entity_type: str, name: str) -> str:
    """Return an ID stable across ordering, aliases, and process restarts."""

    return f"ent-{_sha256(_entity_key(entity_type, name))[:20]}"


class EntityRecord(BaseModel, frozen=True):
    model_config = ConfigDict(extra="ignore")

    entity_id: str = Field(min_length=1)
    entity_type: str = Field(min_length=1)
    canonical_name: str = Field(min_length=1)
    aliases: tuple[str, ...] = ()
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _normalise(cls, value: object) -> object:
        if not isinstance(value, Mapping):
            raise TypeError("entity must be an object")
        raw = dict(value)
        entity_type = _normalise_text(raw.get("entity_type") or raw.get("type") or "entity")
        name = _normalise_text(raw.get("canonical_name") or raw.get("name"))
        if not name:
            raise ValueError("entity canonical_name is required")
        aliases_raw = raw.get("aliases") or ()
        if isinstance(aliases_raw, str):
            aliases_raw = (aliases_raw,)
        aliases = tuple(
            dict.fromkeys(_normalise_text(item) for item in aliases_raw if _normalise_text(item))
        )
        raw.update(
            entity_id=raw.get("entity_id") or stable_entity_id(entity_type, name),
            entity_type=entity_type,
            canonical_name=name,
            aliases=aliases,
        )
        return raw


class EntityRegistry(BaseModel, frozen=True):
    model_config = ConfigDict(extra="ignore")

    entities: tuple[EntityRecord, ...] = ()

    @model_validator(mode="after")
    def _validate_unique(self) -> EntityRegistry:
        identities: dict[str, str] = {}
        ids: set[str] = set()
        names: dict[str, str] = {}
        for entity in self.entities:
            identity = _entity_key(entity.entity_type, entity.canonical_name)
            if identity in identities and identities[identity] != entity.entity_id:
                raise ValueError(
                    f"duplicate entity identity: {entity.entity_type}/{entity.canonical_name}"
                )
            if entity.entity_id in ids:
                raise ValueError(
                    f"duplicate entity identity: {entity.entity_type}/{entity.canonical_name}"
                )
            ids.add(entity.entity_id)
            identities[identity] = entity.entity_id
            for alias in (entity.canonical_name, *entity.aliases):
                key = _entity_key(entity.entity_type, alias)
                previous = names.get(key)
                if previous is not None and previous != entity.entity_id:
                    raise ValueError(f"ambiguous entity alias: {alias}")
                names[key] = entity.entity_id
        return self

    @classmethod
    def from_items(cls, items: Sequence[Mapping[str, Any] | EntityRecord] | None) -> EntityRegistry:
        records = tuple(
            item if isinstance(item, EntityRecord) else EntityRecord.model_validate(item)
            for item in (items or ())
        )
        return cls(entities=records)

    def resolve(self, name: str, entity_type: str | None = None) -> EntityRecord:
        key = re.sub(r"\s+", "", _normalise_text(name)).casefold()
        for entity in self.entities:
            if (
                entity_type
                and _normalise_text(entity.entity_type).casefold()
                != _normalise_text(entity_type).casefold()
            ):
                continue
            candidates = {
                _entity_key(entity.entity_type, entity.canonical_name).split(":", 1)[1],
                *(
                    _entity_key(entity.entity_type, alias).split(":", 1)[1]
                    for alias in entity.aliases
                ),
            }
            if key in candidates:
                return entity
        raise KeyError(f"unknown entity: {name}")


class ProtagonistIdentity(BaseModel, frozen=True):
    model_config = ConfigDict(extra="ignore")

    entity_id: str = ""
    name: str = Field(min_length=1)
    age: str | int | None = None
    core_wound: str | None = None
    identity_claim: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _coerce(cls, value: object) -> object:
        if isinstance(value, str):
            name = re.sub(r"\s+", "", _normalise_text(value))
            return {"name": name, "entity_id": stable_entity_id("character", name)}
        if isinstance(value, Mapping):
            raw = dict(value)
            raw["name"] = re.sub(
                r"\s+", "", _normalise_text(raw.get("name") or raw.get("canonical_name"))
            )
            raw.setdefault("entity_id", stable_entity_id("character", raw["name"]))
            return raw
        raise TypeError("protagonist identity must be a name or object")


class WordBudget(BaseModel, frozen=True):
    total_words: int = Field(default=0, ge=0)
    per_chapter: int | None = Field(default=None, ge=0)


class ChapterBudget(BaseModel, frozen=True):
    total_chapters: int = Field(default=1, ge=1)
    per_volume: tuple[int, ...] = ()


class AssetSourceRef(BaseModel, frozen=True):
    asset_id: str = Field(min_length=1)
    source_snapshot_id: str | None = None
    source_hash: str | None = None
    dependency_version: str | None = None


class ConsistencyIssue(BaseModel, frozen=True):
    code: str
    asset_id: str
    message: str


class CrossAssetConsistencyReport(BaseModel, frozen=True):
    snapshot_id: str
    issues: tuple[ConsistencyIssue, ...] = ()

    @property
    def passed(self) -> bool:
        return not self.issues


class BookDesignSnapshot(BaseModel, frozen=True):
    model_config = ConfigDict(extra="ignore")

    schema_version: Literal["book-design.v1"] = BOOK_DESIGN_SNAPSHOT_VERSION
    snapshot_id: str = Field(min_length=1)
    version: int = Field(default=1, ge=1)
    source_hash: str = Field(min_length=64, max_length=64)
    creation_intent: CreationIntentContract
    protagonist: ProtagonistIdentity
    tone: str = Field(min_length=1, max_length=240)
    word_budget: WordBudget = Field(default_factory=WordBudget)
    chapter_budget: ChapterBudget = Field(default_factory=ChapterBudget)
    reader_promise: str | None = None
    core_story_engine: str | None = None
    entity_registry: EntityRegistry = Field(default_factory=EntityRegistry)
    derived_assets: tuple[AssetSourceRef, ...] = ()

    def canonical_payload(self) -> dict[str, Any]:
        data = self.model_dump(mode="json")
        data.pop("snapshot_id", None)
        data.pop("source_hash", None)
        return data

    def canonical_hash(self) -> str:
        return _sha256(self.canonical_payload())

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> BookDesignSnapshot:
        raw = dict(payload)
        book_spec = raw.get("book_spec") if isinstance(raw.get("book_spec"), Mapping) else {}
        protagonist = (
            raw.get("protagonist")
            or raw.get("protagonist_identity")
            or book_spec.get("protagonist")
            or "Unknown protagonist"
        )
        intent_raw = raw.get("creation_intent") or raw.get("creation_intent_contract") or {}
        if (
            isinstance(intent_raw, Mapping)
            and "genre_intent" not in intent_raw
            and "genre" in intent_raw
        ):
            intent_raw = contract_from_selection(intent_raw).model_dump(mode="json")
        intent = _coerce_creation_intent(intent_raw)
        legacy_word_budget = (
            raw.get("word_budget") if isinstance(raw.get("word_budget"), Mapping) else {}
        )
        legacy_chapter_budget = (
            raw.get("chapter_budget") if isinstance(raw.get("chapter_budget"), Mapping) else {}
        )
        registry_raw = raw.get("entity_registry")
        if raw.get("entities") is not None:
            entities_raw = raw.get("entities")
        elif isinstance(registry_raw, Mapping):
            entities_raw = registry_raw.get("entities", ())
        else:
            entities_raw = ()
        return build_book_design_snapshot(
            creation_intent=intent,
            protagonist=protagonist,
            tone=raw.get("tone") or book_spec.get("tone") or intent.tone_preference or "未指定",
            target_words=raw.get("target_words")
            or book_spec.get("target_words")
            or legacy_word_budget.get("total_words", 0),
            chapter_count=raw.get("chapter_count")
            or book_spec.get("target_chapters")
            or legacy_chapter_budget.get("total_chapters")
            or intent.chapter_count,
            entities=entities_raw,
            reader_promise=raw.get("reader_promise") or book_spec.get("reader_promise"),
            core_story_engine=raw.get("core_story_engine") or book_spec.get("core_story_engine"),
            version=int(raw.get("version") or 1),
            derived_assets=raw.get("derived_assets") or (),
        )


def build_book_design_snapshot(
    *,
    creation_intent: CreationIntentContract | Mapping[str, Any],
    protagonist: ProtagonistIdentity | Mapping[str, Any] | str,
    tone: str | None = None,
    target_words: int = 0,
    chapter_count: int | None = None,
    entities: Sequence[Mapping[str, Any] | EntityRecord] | None = None,
    reader_promise: str | None = None,
    core_story_engine: str | None = None,
    version: int = 1,
    derived_assets: Sequence[Mapping[str, Any] | AssetSourceRef] = (),
) -> BookDesignSnapshot:
    intent = _coerce_creation_intent(creation_intent)
    identity = (
        protagonist
        if isinstance(protagonist, ProtagonistIdentity)
        else ProtagonistIdentity.model_validate(protagonist)
    )
    registry = EntityRegistry.from_items(entities)
    if not any(
        _entity_key(entity.entity_type, entity.canonical_name)
        == _entity_key("character", identity.name)
        for entity in registry.entities
    ):
        registry = EntityRegistry.from_items(
            (
                *registry.entities,
                {
                    "entity_id": identity.entity_id,
                    "entity_type": "character",
                    "canonical_name": identity.name,
                },
            )
        )
    snapshot = BookDesignSnapshot.model_construct(
        schema_version=BOOK_DESIGN_SNAPSHOT_VERSION,
        snapshot_id="pending",
        version=version,
        source_hash="0" * 64,
        creation_intent=intent,
        protagonist=identity,
        tone=_normalise_text(tone) or _normalise_text(intent.tone_preference) or "未指定",
        word_budget=WordBudget(total_words=max(0, int(target_words or 0))),
        chapter_budget=ChapterBudget(
            total_chapters=max(1, int(chapter_count or intent.chapter_count))
        ),
        reader_promise=_normalise_text(reader_promise) or None,
        core_story_engine=_normalise_text(core_story_engine) or None,
        entity_registry=registry,
        derived_assets=tuple(
            item if isinstance(item, AssetSourceRef) else AssetSourceRef.model_validate(item)
            for item in derived_assets
        ),
    )
    digest = snapshot.canonical_hash()
    return snapshot.model_copy(update={"snapshot_id": digest[:16], "source_hash": digest})


def validate_cross_asset_consistency(
    snapshot: BookDesignSnapshot,
    assets: Sequence[Mapping[str, Any] | AssetSourceRef],
) -> CrossAssetConsistencyReport:
    issues: list[ConsistencyIssue] = []
    for raw in assets:
        asset = raw if isinstance(raw, AssetSourceRef) else AssetSourceRef.model_validate(raw)
        data = raw if isinstance(raw, Mapping) else raw.model_dump(mode="json")
        if (
            asset.source_snapshot_id != snapshot.snapshot_id
            or asset.source_hash != snapshot.source_hash
        ):
            issues.append(
                ConsistencyIssue(
                    code="source_snapshot_mismatch",
                    asset_id=asset.asset_id,
                    message="asset does not derive from the active book design snapshot",
                )
            )
        protagonist = data.get("protagonist") or data.get("protagonist_name")
        if protagonist is not None:
            name = protagonist.get("name") if isinstance(protagonist, Mapping) else protagonist
            if _normalise_text(name) != snapshot.protagonist.name:
                issues.append(
                    ConsistencyIssue(
                        code="protagonist_identity_mismatch",
                        asset_id=asset.asset_id,
                        message="asset protagonist differs from snapshot identity",
                    )
                )
        tone = data.get("tone") or data.get("tone_preference")
        if tone is not None and _normalise_text(tone) != snapshot.tone:
            issues.append(
                ConsistencyIssue(
                    code="tone_mismatch",
                    asset_id=asset.asset_id,
                    message="asset tone differs from snapshot tone",
                )
            )
    return CrossAssetConsistencyReport(snapshot_id=snapshot.snapshot_id, issues=tuple(issues))


__all__ = [
    "BOOK_DESIGN_SNAPSHOT_VERSION",
    "AssetSourceRef",
    "BookDesignSnapshot",
    "ChapterBudget",
    "ConsistencyIssue",
    "CrossAssetConsistencyReport",
    "EntityRecord",
    "EntityRegistry",
    "ProtagonistIdentity",
    "WordBudget",
    "build_book_design_snapshot",
    "stable_entity_id",
    "validate_cross_asset_consistency",
]
