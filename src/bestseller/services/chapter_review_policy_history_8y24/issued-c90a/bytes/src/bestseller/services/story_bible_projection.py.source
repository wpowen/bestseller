"""Replay owned contributions over retained preimages, without models.

Chapter success receipts and entity histories remain append-only contribution
logs. Missing legacy baselines are never inferred by subtracting old effects.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from copy import deepcopy
import hashlib
import json
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.infra.db.models import (
    ChapterDraftVersionModel,
    ChapterModel,
    CharacterModel,
    ProjectModel,
    RelationshipModel,
    WorldRuleModel,
)
from bestseller.services.adopted_chapter_source import (
    _continuity_source_blocked, choose_promoted_chapter_source, select_adopted_chapter_source,
)
from bestseller.services.feedback import KNOWLEDGE_SOURCE_KEYS, validate_adopted_knowledge_source

BASE = "story_bible_projection_base"
SCHEMA = "bible-source-baseline.v1"
PREMIUM_VIEW = "premium_state_source_projection"
PREMIUM_SCHEMA = "bible-premium-source-projection.v1"
BibleRow = CharacterModel | RelationshipModel | WorldRuleModel
_CHAR_FIELDS = ("emotional_state", "physical_state", "latest_power_change", "power_change_chapter")
_SOURCE_FIELDS = ("source_lineage", "story_bible_source_lineage")


class BibleProjectionAttributionError(ValueError):
    """No complete, unambiguous attribution exists for automatic projection."""


def _require(condition: object) -> None:
    if not condition:
        raise BibleProjectionAttributionError("bible_projection_attribution_incomplete")


def same_source(left: object, right: object) -> bool:
    return isinstance(left, dict) and isinstance(right, dict) and all(
        left.get(k) is not None and type(left[k]) is type(right.get(k)) and left[k] == right[k]
        for k in KNOWLEDGE_SOURCE_KEYS
    )


def _key(source: dict[str, Any]) -> str:
    _require(source.get("schema") == "adopted-knowledge.v1"
             and type(source.get("source_version_no")) is int
             and source["source_version_no"] > 0
             and all(source.get(k) is not None for k in KNOWLEDGE_SOURCE_KEYS))
    return json.dumps([source[k] for k in KNOWLEDGE_SOURCE_KEYS], ensure_ascii=False)


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _full_source(source: dict[str, Any], project_id: str) -> dict[str, Any]:
    _key(source)
    _require(source.get("project_id") == project_id)
    for key in ("project_id", "chapter_id", "source_chapter_draft_version_id"):
        _require(isinstance(source[key], str) and str(UUID(source[key])) == source[key])
    body = source["source_body_sha256"]
    _require(isinstance(body, str) and len(body) == 64
             and all(ch in "0123456789abcdef" for ch in body))
    return {key: source[key] for key in KNOWLEDGE_SOURCE_KEYS}


def _history(row: BibleRow) -> list[dict[str, Any]]:
    key = "events" if isinstance(row, RelationshipModel) else "story_bible_update_history"
    history = (row.metadata_json or {}).get(key, [])
    _require(isinstance(history, list) and all(isinstance(v, dict) for v in history))
    return history


def _fields(row: BibleRow) -> dict[str, Any]:
    metadata = row.metadata_json or {}
    if isinstance(row, CharacterModel):
        return {"knowledge": deepcopy(row.knowledge_state_json or {}), "metadata": {
            k: deepcopy(metadata[k]) for k in (*_CHAR_FIELDS, *_SOURCE_FIELDS) if k in metadata
        }}
    if isinstance(row, RelationshipModel):
        return {"relationship_type": row.relationship_type, "strength": float(row.strength or 0),
                "last_changed_chapter_no": row.last_changed_chapter_no, "metadata": {
                    k: deepcopy(metadata[k]) for k in ("current_affinity", *_SOURCE_FIELDS)
                    if k in metadata
                }}
    return {"description": row.description, "metadata": {
        k: deepcopy(metadata[k]) for k in ("source_lineage", "description_source_lineage",
                                           "source", "reveal_chapter") if k in metadata
    }}


def capture_bible_baseline(row: BibleRow, *, created: bool = False) -> None:
    """Capture an opaque preimage, never infer authorship or a legacy source."""
    metadata = row.metadata_json or {}
    if BASE in metadata:
        return
    history = _history(row)
    attributed = metadata.get("story_bible_source_lineage") or metadata.get("source_lineage")
    _require(created or not (
        isinstance(attributed, dict) and attributed.get("schema") == "adopted-knowledge.v1"
    ))
    _require(created or not any("source_lineage" in item for item in history))
    fields = _fields(row)
    prefix = deepcopy(history)
    if created:
        prefix = []
        fields["metadata"] = {}
        if isinstance(row, RelationshipModel):
            fields.update(relationship_type="neutral", strength=0.0, last_changed_chapter_no=None)
        elif isinstance(row, WorldRuleModel):
            fields["description"] = ""
    row.metadata_json = {**metadata, BASE: {
        "schema": SCHEMA, "project_id": str(row.project_id), "entity_id": str(row.id),
        "created": created, "fields": fields, "history_prefix": prefix,
    }}


def _apply_fields(row: BibleRow, desired: dict[str, Any]) -> bool:
    if _fields(row) == desired:
        return False
    old_fields = _fields(row)
    metadata = dict(row.metadata_json or {})
    for key in old_fields["metadata"].keys() | desired["metadata"].keys():
        if key in desired["metadata"]:
            metadata[key] = deepcopy(desired["metadata"][key])
        else:
            metadata.pop(key, None)
    row.metadata_json = metadata
    if isinstance(row, CharacterModel):
        row.knowledge_state_json = deepcopy(desired["knowledge"])
        admission = desired["knowledge"].get("epistemic_admission")
        if admission:
            receipt = {
                **deepcopy(admission),
                "item_dispositions": deepcopy(desired["knowledge"]["unverified_interpretations"]),
                "literal_source_observations": deepcopy(
                    desired["knowledge"]["literal_source_observations"]
                ),
            }
            history = list(metadata.get("knowledge_epistemic_disposition_history", []))
            if receipt not in history:
                history.append(receipt)
            row.metadata_json = {**metadata, "knowledge_epistemic_disposition_history": history}
    elif isinstance(row, RelationshipModel):
        for key in ("relationship_type", "strength", "last_changed_chapter_no"):
            setattr(row, key, desired[key])
    else:
        row.description = desired["description"]
    return True


def _fold(
    row: BibleRow, baseline: dict[str, Any], active: list[tuple[int, dict[str, Any]]],
) -> dict[str, Any]:
    from bestseller.services.knowledge_epistemic_admission import (
        VIEW_KEYS,
        admit_cognitive_update,
        merge_cognitive_admission,
    )

    desired = deepcopy(baseline["fields"])
    metadata = desired["metadata"]
    if isinstance(row, CharacterModel):
        # Other authored knowledge keys are outside the updater's ownership.
        desired["knowledge"].update({k: deepcopy(v) for k, v in
            (row.knowledge_state_json or {}).items()
            if k not in {"knows", "beliefs", "falsely_believes", *VIEW_KEYS}})
    for number, entry in active:
        source = entry["source_lineage"]
        update = entry["update"]
        if isinstance(row, CharacterModel):
            admission = admit_cognitive_update(
                update=update, source_lineage=source,
                source_body=entry.get("epistemic_source_body"),
                application_payload=entry.get("epistemic_application_payload", update),
                item_path=entry.get("epistemic_item_path", "/character_updates"),
            )
            update = admission["admitted_update"]
            desired["knowledge"] = merge_cognitive_admission(desired["knowledge"], admission)
            for field, key in (
                ("knowledge_gained", "knows"), ("false_beliefs", "falsely_believes"),
                ("beliefs_gained", "beliefs"),
            ):
                if field == "beliefs_gained" and not update.get(field):
                    continue
                previous_values = desired["knowledge"].get(key, [])
                _require(isinstance(previous_values, list))
                values = list(previous_values)
                incoming = update.get(field, [])
                _require(isinstance(incoming, list) and all(isinstance(v, str) for v in incoming))
                for value in incoming:
                    if value and value not in values:
                        values.append(value)
                desired["knowledge"][key] = values
            invalidated = update.get("beliefs_invalidated", [])
            _require(isinstance(invalidated, list) and all(isinstance(v, str) for v in invalidated))
            for key in ("beliefs", "falsely_believes"):
                if key in desired["knowledge"]:
                    desired["knowledge"][key] = [
                        value for value in desired["knowledge"][key] if value not in invalidated
                    ]
            for field in ("emotional_state", "physical_state"):
                if update.get(field):
                    metadata[field] = update[field]
            if update.get("power_change"):
                metadata.update(
                    latest_power_change=update["power_change"], power_change_chapter=number,
                )
        elif isinstance(row, RelationshipModel):
            affinity = max(0.0, min(1.0, (desired["strength"] + 1) / 2
                + {"up": 0.1, "down": -0.1}.get(update.get("affinity_direction"), 0.0)))
            desired.update(strength=round(2 * affinity - 1, 4),
                           relationship_type=update.get("change", desired["relationship_type"]),
                           last_changed_chapter_no=number)
            metadata["current_affinity"] = affinity
        else:
            description = update.get("description")
            metadata.setdefault("description_source_lineage", None)
            if description and len(description) > len(desired["description"]):
                desired["description"] = description
                metadata["description_source_lineage"] = source
            if baseline["created"] and "source" not in metadata:
                metadata.update(source=f"chapter_{number}", reveal_chapter=number)
        metadata["source_lineage"] = source
        if not isinstance(row, WorldRuleModel):
            metadata["story_bible_source_lineage"] = source
    return desired


async def _feedback_knowledge_effects(
    session: AsyncSession, character: CharacterModel, chapters: dict[str, ChapterModel],
) -> list[tuple[int, dict[str, Any]]]:
    """Use successful current feedback deltas; raw legacy snapshots stay audit-only."""
    from bestseller.services.character_snapshot_sources import load_eligible_character_snapshots

    effects = []
    for snapshot in reversed(await load_eligible_character_snapshots(
        session, project_id=character.project_id, character_id=character.id, limit=None,
    )):
        binding = snapshot.source_binding
        if binding.get("kind") != "adopted_feedback":
            continue
        source = binding["adopted_source"]
        chapter = chapters.get(source["chapter_id"])
        _require(chapter is not None)
        receipts = (chapter.metadata_json or {}).get("knowledge_feedback_receipts", [])
        _require(isinstance(receipts, list))
        receipt = next((r for r in reversed(receipts)
                        if isinstance(r, dict) and r.get("status") in {"ok", "empty"}), None)
        _require(receipt is not None and same_source(receipt.get("source_lineage"), source))
        payload = receipt.get("candidate_payload")
        _require(isinstance(payload, dict))
        delta = binding.get("knowledge_delta")
        _require(isinstance(delta, dict))
        states = payload.get("character_states", [])
        _require(isinstance(states, list) and any(
            isinstance(state, dict) and state.get("character_name") == character.name
            and all(state.get(key, []) == delta.get(key, []) for key in (
                "knowledge_gained", "beliefs_gained", "beliefs_invalidated",
            )) for state in states
        ))
        effects.append((chapter.chapter_number, {"source_lineage": source, "update": delta}))
    return effects


async def _plan(
    session: AsyncSession, project_id: UUID,
    pending: tuple[dict[str, Any], dict[str, Any]] | None = None,
) -> tuple[list[tuple[BibleRow, dict[str, Any]]], list[str],
           dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    chapters = list(await session.scalars(select(ChapterModel).where(
        ChapterModel.project_id == project_id,
    ).execution_options(populate_existing=True, autoflush=False)))
    known: dict[str, dict[str, Any]] = {}
    witnesses: dict[str, dict[str, Any]] = {}
    active: dict[str, dict[str, Any]] = {}
    source_bodies: dict[str, str] = {}
    numbers = {str(ch.id): ch.chapter_number for ch in chapters}
    for chapter in chapters:
        receipts = (chapter.metadata_json or {}).get("story_bible_update_receipts", [])
        successes = [r for r in receipts if r.get("status") in {"ok", "empty"}]
        for receipt in successes:
            source = receipt.get("source_lineage")
            _require(isinstance(source, dict) and source.get("project_id") == str(project_id)
                     and source.get("chapter_id") == str(chapter.id))
            known[_key(source)] = receipt.get("candidate_payload")
            witnesses[_key(source)] = {
                "source_lineage": _full_source(source, str(project_id)),
                "chapter_number": chapter.chapter_number,
                "premium_updates": _premium_updates(receipt.get("candidate_payload")),
            }
        if not successes and not (pending and pending[0]["chapter_id"] == str(chapter.id)):
            continue
        draft, _ = await select_adopted_chapter_source(session, chapter=chapter, lock=False)
        if draft is None and successes and not (pending and pending[0]["chapter_id"] == str(chapter.id)):
            # A positively blocked retained source is known history, not an
            # unknown attribution. Withdraw only its derived effects; retain
            # every original contribution for replay and audit below.
            retained = choose_promoted_chapter_source(
                await session.scalars(select(ChapterDraftVersionModel).where(
                    ChapterDraftVersionModel.project_id == project_id,
                    ChapterDraftVersionModel.chapter_id == chapter.id,
                    ChapterDraftVersionModel.promotion_state == "promoted",
                ).execution_options(populate_existing=True, autoflush=False)),
                project_id=project_id, chapter_id=chapter.id,
            )
            if retained is not None and _continuity_source_blocked(chapter, retained):
                expected = {
                    "schema": "adopted-knowledge.v1", "project_id": str(project_id),
                    "chapter_id": str(chapter.id),
                    "source_chapter_draft_version_id": str(retained.id),
                    "source_version_no": retained.version_no,
                    "source_body_sha256": hashlib.sha256(retained.content_md.encode()).hexdigest(),
                }
                _require(same_source(successes[-1].get("source_lineage"), expected))
                continue
        _require(draft is not None)
        source, rejection = await validate_adopted_knowledge_source(
            session, project_id=project_id, chapter_id=chapter.id,
            source_chapter_draft_version_id=draft.id, chapter_md=draft.content_md, lock=False,
        )
        _require(not rejection)
        if pending and same_source(pending[0], source):
            known[_key(source)] = pending[1]
            witnesses[_key(source)] = {
                "source_lineage": _full_source(source, str(project_id)),
                "chapter_number": chapter.chapter_number,
                "premium_updates": _premium_updates(pending[1]),
            }
        else:
            _require(successes and same_source(successes[-1].get("source_lineage"), source))
        active[str(chapter.id)] = source
        source_bodies[_key(source)] = draft.content_md
    from bestseller.services.story_bible import normalize_world_rule_name
    from bestseller.services.world_rule_identity_recovery import (
        validated_world_rule_identity_mappings,
    )

    identity_exclusions = await validated_world_rule_identity_mappings(
        session, project_id=project_id,
    )
    changes: list[tuple[BibleRow, dict[str, Any]]] = []
    legacy: list[str] = []
    for model, category in ((CharacterModel, "character_updates"),
                            (RelationshipModel, "relationship_updates"),
                            (WorldRuleModel, "world_updates")):
        rows = await session.scalars(select(model).where(model.project_id == project_id)
            .execution_options(populate_existing=True, autoflush=False))
        for row in rows:
            metadata = row.metadata_json or {}
            history = _history(row)
            baseline = metadata.get(BASE)
            if isinstance(row, WorldRuleModel):
                for history_index, entry in enumerate(history):
                    if "source_lineage" not in entry:
                        continue  # Opaque authored history is never reclassified.
                    update = entry.get("update")
                    name = update.get("rule_or_location") if isinstance(update, dict) else None
                    if (not isinstance(name, str) or not name.strip()
                        or normalize_world_rule_name(name) != normalize_world_rule_name(row.name)):
                        if (str(row.id), history_index) not in identity_exclusions:
                            raise BibleProjectionAttributionError("world_rule_identity_mismatch")
            if baseline is None:
                pointer = metadata.get("story_bible_source_lineage", metadata.get("source_lineage"))
                sources = [e.get("source_lineage") for e in history if "source_lineage" in e]
                if not sources:
                    if (isinstance(pointer, dict)
                        and pointer.get("schema") == "adopted-knowledge.v1"):
                        sources = [pointer]
                    else:
                        _require(not metadata.get("story_bible_source_lineage"))
                        continue  # Unscoped authored fields are outside projection ownership.
                _require(all(isinstance(s, dict) and _key(s) in known for s in sources))
                all_current = all(same_source(s, active.get(s.get("chapter_id"))) for s in sources)
                latest_number = max(numbers.get(s.get("chapter_id"), 0) for s in sources)
                pointer_current = isinstance(pointer, dict) and same_source(
                    pointer, active.get(pointer.get("chapter_id")),
                ) and numbers.get(pointer.get("chapter_id")) == latest_number
                legacy.append("legacy_current" if all_current and pointer_current else "stale")
                continue
            _require(isinstance(baseline, dict) and baseline.get("schema") == SCHEMA
                     and baseline.get("project_id") == str(project_id)
                     and baseline.get("entity_id") == str(row.id))
            prefix = baseline.get("history_prefix")
            _require(isinstance(prefix, list) and history[:len(prefix)] == prefix
                     and isinstance(baseline.get("fields"), dict))
            effects: list[tuple[int, dict[str, Any]]] = []
            if isinstance(row, CharacterModel):
                effects.extend(await _feedback_knowledge_effects(
                    session, row, {str(ch.id): ch for ch in chapters},
                ))
            for history_index, entry in enumerate(history[len(prefix):], start=len(prefix)):
                source = entry.get("source_lineage")
                _require(isinstance(source, dict) and source.get("project_id") == str(project_id)
                         and source.get("chapter_id") in numbers)
                payload = known.get(_key(source))
                update = entry.get("update")
                _require(isinstance(payload, dict) and isinstance(update, dict)
                         and update in payload.get(category, []))
                if (isinstance(row, WorldRuleModel)
                    and (str(row.id), history_index) in identity_exclusions):
                    continue  # Explicit validated correction, never an unknown-source skip.
                if same_source(source, active.get(source["chapter_id"])):
                    if isinstance(row, CharacterModel):
                        # Identical retained updates have no historical index witness.
                        # Cover every matching original pointer instead of inventing one.
                        for update_index, candidate in enumerate(payload[category]):
                            if candidate == update:
                                effects.append((numbers[source["chapter_id"]], {
                                    **entry,
                                    "epistemic_source_body": source_bodies.get(_key(source)),
                                    "epistemic_application_payload": payload,
                                    "epistemic_item_path": f"/character_updates/{update_index}",
                                }))
                    else:
                        effects.append((numbers[source["chapter_id"]], entry))
            if isinstance(row, WorldRuleModel):
                # Correction copies append to immutable histories. Recover the
                # original receipt order, including ties under the longer-only
                # description policy, instead of letting append order decide.
                effects.sort(key=lambda value: (
                    value[0],
                    known[_key(value[1]["source_lineage"])]["world_updates"].index(
                        value[1]["update"],
                    ),
                ))
            else:
                effects.sort(key=lambda value: value[0])
            changes.append((row, _fold(row, baseline, effects)))
    return changes, legacy, active, witnesses


def _premium_updates(payload: object) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    updates = payload.get("premium_engine_updates") or payload.get("premium_state_updates") or {}
    _require(isinstance(updates, dict))
    return deepcopy(updates)


def premium_projection_baseline(
    metadata: Mapping[str, Any], project_id: UUID, *, capture: bool = False,
) -> dict[str, Any]:
    """Retain an opaque prefix before the first owned append; infer no authorship."""
    if PREMIUM_VIEW in metadata:
        try:
            baseline = deepcopy(metadata[PREMIUM_VIEW]["receipt"]["opaque_baseline"])
        except (KeyError, TypeError) as exc:
            raise BibleProjectionAttributionError("premium_baseline_missing") from exc
        _baseline_ledger(baseline, str(project_id))
        return baseline
    ledger = deepcopy(metadata.get("premium_state_ledger", {}))
    _require(isinstance(ledger, dict))
    # No scoped event may be retrospectively reclassified as opaque baseline.
    for events in ledger.values():
        _require(isinstance(events, list))
        _require(all(isinstance(e, dict) and "source_lineage" not in e for e in events))
        _require(capture or not events)
    return {"kind": "opaque-unscoped-prefix.v1", "project_id": str(project_id),
            "ledger": ledger, "ledger_sha256": _digest(ledger)}


def _baseline_ledger(baseline: object, project_id: str) -> dict[str, Any]:
    _require(isinstance(baseline, dict) and baseline.get("kind") == "opaque-unscoped-prefix.v1"
             and baseline.get("project_id") == project_id)
    ledger = baseline.get("ledger")
    _require(isinstance(ledger, dict) and baseline.get("ledger_sha256") == _digest(ledger))
    for events in ledger.values():
        _require(isinstance(events, list) and all(
            isinstance(e, dict) and "source_lineage" not in e for e in events
        ))
    return ledger


def _derive_premium_ledger(
    ledger: dict[str, Any], active: dict[str, dict[str, Any]],
    successes: list[dict[str, Any]], project_id: str, baseline: dict[str, Any],
) -> dict[str, Any]:
    from bestseller.services.story_bible import _normalize_premium_state_event

    prefix = _baseline_ledger(baseline, project_id)
    _require(prefix.keys() <= ledger.keys())
    known = {}
    for witness in successes:
        _require(isinstance(witness, dict) and isinstance(witness.get("source_lineage"), dict))
        source = _full_source(witness["source_lineage"], project_id)
        _require(type(witness.get("chapter_number")) is int and witness["chapter_number"] > 0)
        _require(_key(source) not in known)
        known[_key(source)] = witness
    needed = {_key(_full_source(source, project_id)) for source in active.values()}
    for chapter_id, source in active.items():
        _require(chapter_id == source["chapter_id"])
        _require(_key(source) in known)
    _require(len({known[_key(s)]["chapter_number"] for s in active.values()}) == len(active))
    filtered: dict[str, Any] = {}
    used: Counter[tuple[str, str, str]] = Counter()
    for key, events in ledger.items():
        _require(isinstance(events, list))
        original = prefix.get(key, [])
        _require(events[:len(original)] == original)
        included = []
        for item in events[len(original):]:
            _require(isinstance(item, dict))
            source = item.get("source_lineage")
            # Unscoped additions after the captured prefix have ambiguous authority.
            _require(isinstance(source, dict))
            source = _full_source(source, project_id)
            token = _key(source)
            needed.add(token)
            _require(token in known)
            witness = known[token]
            payload = witness.get("premium_updates")
            _require(isinstance(payload, dict) and isinstance(payload.get(key, []), list))
            candidates = [_normalize_premium_state_event(value,
                chapter_number=witness["chapter_number"]) for value in payload.get(key, [])]
            bare = {k: v for k, v in item.items() if k != "source_lineage"}
            matching = sum(
                candidate is not None and bare == {
                    k: v for k, v in candidate.items() if k != "source_lineage"
                } for candidate in candidates
            )
            token_event = (token, key, _digest(bare))
            used[token_event] += 1
            _require(item.get("chapter_number") == witness["chapter_number"]
                     and used[token_event] <= matching)
            if same_source(source, active.get(source["chapter_id"])):
                included.append((witness["chapter_number"], deepcopy(item)))
        included.sort(key=lambda value: value[0])
        filtered[key] = [*deepcopy(original), *(item for _, item in included)]
    _require(set(known) == needed)  # Witnesses are bounded to raw events and active sources.
    return filtered


def _premium_view(
    ledger: dict[str, Any], active: dict[str, dict[str, Any]],
    witnesses: dict[str, dict[str, Any]], project_id: UUID,
    baseline: dict[str, Any], history: list[dict[str, Any]],
) -> dict[str, Any]:
    from bestseller.services.premium_state_ledger import (
        materialize_premium_state_snapshot,
        validate_premium_state_ledger,
    )

    project = str(project_id)
    sources = {key: _full_source(value, project) for key, value in sorted(active.items())}
    prefix = _baseline_ledger(baseline, project)
    needed = {_key(source) for source in sources.values()}
    for key, events in ledger.items():
        _require(isinstance(events, list))
        for event in events[len(prefix.get(key, [])):]:
            _require(isinstance(event, dict) and isinstance(event.get("source_lineage"), dict))
            needed.add(_key(_full_source(event["source_lineage"], project)))
    _require(needed <= witnesses.keys())
    successes = [deepcopy(witnesses[key]) for key in sorted(needed)]
    filtered = _derive_premium_ledger(ledger, sources, successes, project, baseline)
    snapshot = materialize_premium_state_snapshot(filtered)
    report = validate_premium_state_ledger(filtered).to_dict()
    return {"active_ledger": filtered, "snapshot": snapshot, "receipt": {
        "schema": PREMIUM_SCHEMA, "project_id": project, "active_sources": sources,
        "opaque_baseline": deepcopy(baseline), "projection_history_sha256": _digest(history),
        "known_successes": successes, "raw_event_counts": {k: len(v) for k, v in ledger.items()},
        "raw_ledger_sha256": _digest(ledger), "active_ledger_sha256": _digest(filtered),
        "snapshot_sha256": _digest(snapshot), "sources_sha256": _digest(sources),
        "ledger_report_sha256": _digest(report),
        "known_successes_sha256": _digest(successes), "provider_calls": 0, "new_grade": 0,
        "new_adoption": 0,
    }}


def validated_premium_projection(metadata: Mapping[str, Any]) -> dict[str, Any] | None:
    """Pure consumer API. Absence returns None; any present invalid view raises ValueError.

    This validates retained producer witnesses, not live SQL adoption. The batch
    status API additionally compares them against the actual selected DB sources.
    """
    if PREMIUM_VIEW not in metadata:
        return None
    try:
        view = metadata[PREMIUM_VIEW]
        _require(isinstance(view, dict) and isinstance(view.get("receipt"), dict))
        receipt = view["receipt"]
        _require(receipt.get("schema") == PREMIUM_SCHEMA)
        project_id = UUID(receipt["project_id"])
        active, successes = receipt["active_sources"], receipt["known_successes"]
        ledger = metadata.get("premium_state_ledger")
        _require(isinstance(active, dict) and isinstance(successes, list)
                 and isinstance(ledger, dict))
        filtered = _derive_premium_ledger(
            ledger, active, successes, str(project_id), receipt["opaque_baseline"],
        )
        from bestseller.services.premium_state_ledger import (
            materialize_premium_state_snapshot,
            validate_premium_state_ledger,
        )

        snapshot = materialize_premium_state_snapshot(filtered)
        report = validate_premium_state_ledger(filtered).to_dict()
        projection_history = view["projection_history"]
        _require(isinstance(projection_history, list)
                 and all(isinstance(v, dict) for v in projection_history))
        _require(receipt.get("raw_ledger_sha256") == _digest(ledger)
                 and receipt.get("active_ledger_sha256") == _digest(filtered)
                 and receipt.get("snapshot_sha256") == _digest(snapshot)
                 and receipt.get("sources_sha256") == _digest(active)
                 and receipt.get("known_successes_sha256") == _digest(successes)
                 and receipt.get("raw_event_counts") == {k: len(v) for k, v in ledger.items()}
                 and view.get("active_ledger") == filtered and view.get("snapshot") == snapshot
                 and metadata.get("premium_state_snapshot") == snapshot
                 and metadata.get("premium_state_ledger_report") == report
                 and receipt.get("ledger_report_sha256") == _digest(report)
                 and receipt.get("projection_history_sha256") == _digest(projection_history)
                 and all(type(receipt.get(k)) is int and receipt[k] == 0
                         for k in ("provider_calls", "new_grade", "new_adoption")))
        history = view.get("receipt_history", [])
        _require(isinstance(history, list) and all(isinstance(v, dict) for v in history))
        return {key: deepcopy(view[key]) for key in ("active_ledger", "snapshot", "receipt")}
    except (KeyError, TypeError, AttributeError, ValueError) as exc:
        raise ValueError("premium_source_projection_invalid") from exc


def _validate_previous_premium_view(metadata: dict[str, Any], project_id: UUID) -> None:
    if PREMIUM_VIEW not in metadata:
        return
    try:
        counts = metadata[PREMIUM_VIEW]["receipt"]["raw_event_counts"]
        ledger = metadata["premium_state_ledger"]
        _require(isinstance(counts, dict) and isinstance(ledger, dict))
        old_prefix = {}
        for key, size in counts.items():
            _require(type(size) is int and size >= 0 and isinstance(ledger.get(key), list)
                     and len(ledger[key]) >= size)
            old_prefix[key] = ledger[key][:size]
        previous = validated_premium_projection({**metadata, "premium_state_ledger": old_prefix})
        _require(previous["receipt"]["project_id"] == str(project_id))
    except (KeyError, TypeError, AttributeError, ValueError) as exc:
        raise BibleProjectionAttributionError("premium_source_projection_invalid") from exc


async def reproject_adopted_bible(
    session: AsyncSession, project: ProjectModel,
    *, pending: tuple[dict[str, Any], dict[str, Any]] | None = None,
    premium_baseline: dict[str, Any] | None = None,
) -> bool:
    """DB-only; caller holds the project lock and an encompassing savepoint."""
    changes, legacy, active, witnesses = await _plan(session, project.id, pending)
    _require(all(status == "legacy_current" for status in legacy))
    changed = False
    for row, desired in changes:
        changed = _apply_fields(row, desired) or changed
    metadata = project.metadata_json or {}
    _validate_previous_premium_view(metadata, project.id)
    ledger = metadata.get("premium_state_ledger")
    if isinstance(ledger, dict):
        old_view = metadata.get(PREMIUM_VIEW)
        baseline = premium_baseline if premium_baseline is not None else (
            premium_projection_baseline(metadata, project.id)
        )
        projection_history = deepcopy(old_view.get("projection_history", [])) if old_view else []
        view = _premium_view(ledger, active, witnesses, project.id, baseline, projection_history)
        old_current = ({key: old_view[key] for key in ("active_ledger", "snapshot", "receipt")}
                       if isinstance(old_view, dict) else None)
        if view != old_current:
            history = deepcopy(old_view.get("receipt_history", [])) if old_view else []
            if old_view:
                history.append(deepcopy(old_view["receipt"]))
            # Preserve exact old values, including absent versus explicit None,
            # alongside the receipt that previously governed those values.
            projection_history.append({
                "previous_values": {key: deepcopy(metadata[key]) for key in (
                    "premium_state_snapshot", "premium_state_ledger_report",
                ) if key in metadata},
                "previous_receipt": deepcopy(old_view["receipt"]) if old_view else None,
                "raw_ledger_sha256": (old_view["receipt"]["raw_ledger_sha256"] if old_view
                                      else baseline["ledger_sha256"]),
            })
            view = _premium_view(
                ledger, active, witnesses, project.id, baseline, projection_history,
            )
            from bestseller.services.premium_state_ledger import validate_premium_state_ledger

            project.metadata_json = {
                **metadata, "premium_state_snapshot": deepcopy(view["snapshot"]),
                "premium_state_ledger_report": validate_premium_state_ledger(
                    view["active_ledger"],
                ).to_dict(),
                PREMIUM_VIEW: {**view, "receipt_history": history,
                               "projection_history": projection_history},
            }
            changed = True
    if changed:
        await session.flush()
    return changed


async def preflight_adopted_bible_projection(
    session: AsyncSession, *, project_id: UUID, source_lineage: dict[str, Any],
) -> None:
    """Read-only attribution check before extracting a new adopted source.

    An empty pending contribution proves only whether retained projection roots
    can be replayed. It never approves or reuses a failed model sample.
    """
    with session.no_autoflush:
        try:
            _, legacy, active, witnesses = await _plan(
                session, project_id, pending=(source_lineage, {}),
            )
            _require(all(status == "legacy_current" for status in legacy))
            project = await session.scalar(select(ProjectModel).where(ProjectModel.id == project_id)
                .execution_options(populate_existing=True, autoflush=False))
            _require(project is not None)
            metadata = project.metadata_json or {}
            _validate_previous_premium_view(metadata, project_id)
            baseline = premium_projection_baseline(metadata, project_id, capture=True)
            ledger = metadata.get("premium_state_ledger")
            if isinstance(ledger, dict):
                _premium_view(ledger, active, witnesses, project_id, baseline,
                              metadata.get(PREMIUM_VIEW, {}).get("projection_history", []))
        except BibleProjectionAttributionError:
            raise
        except (KeyError, TypeError, ValueError) as exc:
            raise BibleProjectionAttributionError(
                "bible_projection_attribution_incomplete",
            ) from exc


async def adopted_bible_projection_status(
    session: AsyncSession, *, project_id: UUID, source_lineage: dict[str, Any],
) -> str:
    """Unlocked read-only API: current / legacy_current / stale / unknown.

    Legacy_current retains the old exact-receipt semantics without creating a
    baseline or a derived proof. Callers must supply the selected full lineage.
    """
    chapter_id = UUID(source_lineage["chapter_id"])
    return (await adopted_bible_projection_statuses(
        session, project_id=project_id, source_lineages={chapter_id: source_lineage},
    ))[chapter_id]


async def adopted_bible_projection_statuses(
    session: AsyncSession, *, project_id: UUID,
    source_lineages: dict[UUID, dict[str, Any]],
) -> dict[UUID, str]:
    """Batch read: one project replay plan for all requested selected sources."""
    if not source_lineages:
        return {}
    with session.no_autoflush:
        try:
            changes, legacy, active, witnesses = await _plan(session, project_id)
            status = "current"
            if "unknown" in legacy:
                status = "unknown"
            elif any(_fields(row) != desired for row, desired in changes) or "stale" in legacy:
                status = "stale"
            project = await session.scalar(select(ProjectModel).where(ProjectModel.id == project_id)
                .execution_options(populate_existing=True, autoflush=False))
            if project is None:
                return dict.fromkeys(source_lineages, "unknown")
            metadata = project.metadata_json or {}
            stored = validated_premium_projection(metadata)
            ledger = metadata.get("premium_state_ledger")
            missing_view = isinstance(ledger, dict) and stored is None
            if isinstance(ledger, dict) and stored is not None:
                expected = _premium_view(
                    ledger, active, witnesses, project_id,
                    premium_projection_baseline(metadata, project_id),
                    metadata[PREMIUM_VIEW]["projection_history"],
                )
                if stored != expected:
                    status = "stale"
            chapters = {ch.id: ch for ch in await session.scalars(select(ChapterModel).where(
                ChapterModel.project_id == project_id,
            ).execution_options(populate_existing=True, autoflush=False))}
            result = {}
            for chapter_id, source in source_lineages.items():
                if (str(chapter_id) != source.get("chapter_id")
                    or not same_source(source, active.get(str(chapter_id)))):
                    result[chapter_id] = "stale"
                    continue
                receipts = (chapters[chapter_id].metadata_json or {}).get(
                    "story_bible_update_receipts", [],
                )
                current = next((r for r in reversed(receipts)
                    if r.get("status") in {"ok", "empty"}), {})
                proven = (current.get("result") or {}).get("projection_reconciled") is True
                if status == "current" and missing_view and proven and not legacy:
                    result[chapter_id] = "stale"
                    continue
                result[chapter_id] = ("legacy_current" if status == "current" and (
                    legacy or not proven
                ) else status)
            return result
        except (BibleProjectionAttributionError, KeyError, TypeError, ValueError):
            return dict.fromkeys(source_lineages, "unknown")
