"""Evidence-preserving, DB-only correction of chapter world-rule routing.

Public plan is read-only. Apply owns a savepoint, never commits, and revalidates
the complete plan under the project lock. Original receipts, bodies, identity,
baseline and history remain untouched; only derived fields are reprojected.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.infra.db.models import (
    ChapterDraftVersionModel,
    ChapterModel,
    ProjectModel,
    WorldRuleModel,
)
from bestseller.services import story_bible_projection as projection
from bestseller.services.adopted_chapter_source import select_adopted_chapter_source
from bestseller.services.feedback import validate_adopted_knowledge_source

SCHEMA = "world-rule-identity-recovery.v1"
LEDGER = "world_rule_identity_recovery_receipts"
ORIGIN = "world_rule_identity_origin"


def _require(value: object) -> None:
    if not value:
        raise projection.BibleProjectionAttributionError("world_rule_identity_evidence_incomplete")


def _digest(value: object) -> str:
    return projection._digest(value)


def _signed(value: dict[str, Any], key: str) -> dict[str, Any]:
    return {**value, key: _digest(value)}


def _verify(value: object, key: str) -> None:
    _require(isinstance(value, dict))
    _require(value.get(key) == _digest({k: v for k, v in value.items() if k != key}))


def _name(value: object) -> str:
    from bestseller.services.story_bible import normalize_world_rule_name

    _require(isinstance(value, str) and bool(value.strip()))
    return normalize_world_rule_name(value)


def _row_image(row: WorldRuleModel) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "project_id": str(row.project_id),
        "name": row.name,
        "rule_code": row.rule_code,
        "description": row.description,
        "story_consequence": row.story_consequence,
        "exploitation_potential": row.exploitation_potential,
        "metadata": deepcopy(row.metadata_json or {}),
    }


async def _context(
    session: AsyncSession,
    project_id: UUID,
) -> tuple[ProjectModel, list[WorldRuleModel], list[ChapterModel], list[ChapterDraftVersionModel]]:
    async def rows(
        model: type[WorldRuleModel] | type[ChapterModel] | type[ChapterDraftVersionModel],
    ) -> list[Any]:
        return list(
            await session.scalars(
                select(model)
                .where(model.project_id == project_id)
                .order_by(model.id)
                .execution_options(populate_existing=True, autoflush=False)
            )
        )

    project = await session.scalar(
        select(ProjectModel)
        .where(ProjectModel.id == project_id)
        .execution_options(populate_existing=True, autoflush=False)
    )
    _require(project is not None)
    return (
        project,
        await rows(WorldRuleModel),
        await rows(ChapterModel),
        await rows(ChapterDraftVersionModel),
    )


def _receipt_proof(
    entry: dict[str, Any],
    chapters: dict[str, ChapterModel],
    drafts: dict[str, ChapterDraftVersionModel],
    project_id: UUID,
) -> dict[str, Any]:
    """One exact retained payload occurrence, raw return and owned source body."""
    from bestseller.services.story_bible import _parse_adopted_bible_updates

    source, update = entry.get("source_lineage"), entry.get("update")
    _require(isinstance(source, dict) and isinstance(update, dict))
    projection._full_source(source, str(project_id))
    chapter = chapters.get(source["chapter_id"])
    draft = drafts.get(source["source_chapter_draft_version_id"])
    _require(
        chapter is not None
        and draft is not None
        and str(draft.chapter_id) == source["chapter_id"]
        and draft.version_no == source["source_version_no"]
        and hashlib.sha256(draft.content_md.encode()).hexdigest() == source["source_body_sha256"]
    )
    matches = []
    receipts = (chapter.metadata_json or {}).get("story_bible_update_receipts", [])
    _require(isinstance(receipts, list))
    for ri, receipt in enumerate(receipts):
        if (
            not isinstance(receipt, dict)
            or receipt.get("status") not in {"ok", "empty"}
            or receipt.get("source_lineage") != source
        ):
            continue
        payload = receipt.get("candidate_payload")
        _require(isinstance(payload, dict))
        indexes = [i for i, u in enumerate(payload.get("world_updates", [])) if u == update]
        if not indexes:
            continue
        _require(len(indexes) == 1)  # Identical occurrences have no index witness.
        raw_matches = []
        raw_values = receipt.get("raw_extractions", [])
        _require(isinstance(raw_values, list))
        for raw_index, raw in enumerate(raw_values):
            _require(isinstance(raw, str))
            try:
                parsed = _parse_adopted_bible_updates(raw)
            except (ValueError, TypeError):
                continue  # A retained failed extraction may precede the exact repair.
            if parsed == payload:
                raw_matches.append((raw_index, hashlib.sha256(raw.encode()).hexdigest()))
        _require(len(raw_matches) == 1)
        matches.append(
            {
                "chapter_id": source["chapter_id"],
                "receipt_index": ri,
                "receipt_sha256": _digest(receipt),
                "candidate_sha256": _digest(payload),
                "payload_index": indexes[0],
                "update_sha256": _digest(update),
                "raw_extraction_index": raw_matches[0][0],
                "raw_sha256": raw_matches[0][1],
            }
        )
    _require(len(matches) == 1)
    return matches[0]


def _validate_baseline(row: WorldRuleModel) -> dict[str, Any]:
    metadata = row.metadata_json or {}
    baseline = metadata.get(projection.BASE)
    history = projection._history(row)
    _require(
        isinstance(baseline, dict)
        and baseline.get("schema") == projection.SCHEMA
        and baseline.get("entity_id") == str(row.id)
        and baseline.get("project_id") == str(row.project_id)
        and isinstance(baseline.get("fields"), dict)
        and isinstance(baseline.get("history_prefix"), list)
        and history[: len(baseline["history_prefix"])] == baseline["history_prefix"]
    )
    return baseline


def _occurrence_key(evidence: dict[str, Any]) -> str:
    return _digest(
        {
            key: evidence[key]
            for key in (
                "chapter_id",
                "receipt_index",
                "receipt_sha256",
                "raw_extraction_index",
                "raw_sha256",
                "payload_index",
            )
        }
    )


def _validate_occurrence_ownership(
    mappings: list[dict[str, Any]],
    rows: list[WorldRuleModel],
    chapters: dict[str, ChapterModel],
    drafts: dict[str, ChapterDraftVersionModel],
    project_id: UUID,
) -> None:
    """A raw occurrence can justify exactly one original contribution project-wide.

    Derived copies are separately verified against their mapping. Ambiguous
    duplicates are retained untouched and rejected, never silently multiplied
    or deduplicated by guessing which original was intended.
    """
    by_id = {str(row.id): row for row in rows}
    claimed = set()
    for mapping in mappings:
        occurrence = _occurrence_key(mapping["evidence"])
        _require(occurrence not in claimed)
        claimed.add(occurrence)
        owner = mapping["old_row_id"], mapping["history_index"]
        original = projection._history(by_id[owner[0]])[owner[1]]
        owners = []
        for row in rows:
            for index, entry in enumerate(projection._history(row)):
                if (
                    ORIGIN not in entry
                    and projection.same_source(
                        entry.get("source_lineage"), original["source_lineage"]
                    )
                    and entry.get("update") == original["update"]
                ):
                    proof = _receipt_proof(entry, chapters, drafts, project_id)
                    if _occurrence_key(proof) == occurrence:
                        owners.append((str(row.id), index))
        _require(owners == [owner])


def _target_initial_baseline(
    target: WorldRuleModel | None,
    *,
    project_id: UUID,
    target_id: UUID,
) -> dict[str, Any]:
    """Predict exactly capture_bible_baseline's opaque preimage without mutating."""
    if target is not None and projection.BASE in (target.metadata_json or {}):
        return deepcopy(_validate_baseline(target))
    if target is None:
        fields, prefix = {"description": "", "metadata": {}}, []
    else:
        metadata = target.metadata_json or {}
        pointer = metadata.get("story_bible_source_lineage") or metadata.get("source_lineage")
        prefix = deepcopy(projection._history(target))
        _require(
            not (isinstance(pointer, dict) and pointer.get("schema") == "adopted-knowledge.v1")
        )
        _require(not any("source_lineage" in entry for entry in prefix))
        fields = projection._fields(target)
    return {
        "schema": projection.SCHEMA,
        "project_id": str(project_id),
        "entity_id": str(target_id),
        "created": target is None,
        "fields": deepcopy(fields),
        "history_prefix": prefix,
    }


def _validated_mappings(
    project: ProjectModel,
    rows: list[WorldRuleModel],
    chapters: list[ChapterModel],
    drafts: list[ChapterDraftVersionModel],
) -> dict[tuple[str, int], dict[str, Any]]:
    """Validate both ends against raw evidence; hashes alone confer no authority."""
    ledger = (project.metadata_json or {}).get(LEDGER, [])
    _require(isinstance(ledger, list))
    by_id = {str(row.id): row for row in rows}
    chapters = {str(row.id): row for row in chapters}
    drafts = {str(row.id): row for row in drafts}
    excluded = {}
    for receipt in ledger:
        _verify(receipt, "plan_sha256")
        _require(receipt.get("schema") == SCHEMA and receipt.get("project_id") == str(project.id))
        _require(
            isinstance(receipt.get("mappings"), list)
            and isinstance(receipt.get("preimages"), dict)
            and isinstance(receipt.get("target_baselines"), dict)
        )
        for mapping in receipt["mappings"]:
            _verify(mapping, "mapping_sha256")
            _require(mapping.get("schema") == SCHEMA)
            old = by_id.get(mapping.get("old_row_id"))
            target = by_id.get(mapping.get("target_row_id"))
            _require(old is not None and target is not None and old.id != target.id)
            baseline = _validate_baseline(old)
            target_baseline = _validate_baseline(target)
            pinned_baseline = receipt["target_baselines"].get(str(target.id))
            _require(
                isinstance(pinned_baseline, dict)
                and target_baseline == pinned_baseline
                and _digest(target_baseline) == mapping.get("target_baseline_sha256")
            )
            index = mapping.get("history_index")
            history = projection._history(old)
            _require(type(index) is int and len(baseline["history_prefix"]) <= index < len(history))
            entry = history[index]
            image = receipt["preimages"].get(str(old.id))
            _require(
                isinstance(image, dict)
                and image["name"] == old.name
                and image["rule_code"] == old.rule_code
                and image["project_id"] == str(project.id)
                and image["metadata"].get(projection.BASE) == baseline
            )
            saved_history = image["metadata"].get("story_bible_update_history", [])
            _require(history[: len(saved_history)] == saved_history and index < len(saved_history))
            _require(
                _digest(entry) == mapping.get("history_entry_sha256")
                and entry.get("source_lineage") == mapping.get("source_lineage")
                and _name(old.name) != _name(entry["update"].get("rule_or_location"))
                and _name(old.name) == mapping.get("old_name")
                and _name(target.name) == mapping.get("target_name")
                and _name(target.name) == _name(entry["update"].get("rule_or_location"))
                and target.rule_code == mapping.get("target_rule_code")
            )
            _require(_receipt_proof(entry, chapters, drafts, project.id) == mapping.get("evidence"))
            adoption = mapping.get("adoption_witness")
            _require(
                isinstance(adoption, dict)
                and adoption.get("chapter_id") == entry["source_lineage"]["chapter_id"]
            )
            projection._full_source(adoption, str(project.id))
            adopted_draft = drafts.get(adoption["source_chapter_draft_version_id"])
            _require(
                adopted_draft is not None
                and str(adopted_draft.chapter_id) == adoption["chapter_id"]
                and adopted_draft.version_no == adoption["source_version_no"]
                and hashlib.sha256(adopted_draft.content_md.encode()).hexdigest()
                == adoption["source_body_sha256"]
            )
            expected = {**deepcopy(entry), ORIGIN: mapping["mapping_sha256"]}
            _require(sum(e == expected for e in projection._history(target)) == 1)
            key = str(old.id), index
            _require(key not in excluded)
            excluded[key] = mapping
    _validate_occurrence_ownership(list(excluded.values()), rows, chapters, drafts, project.id)
    # A copied contribution may never outlive, or invent, its correction proof.
    origins = {m["mapping_sha256"]: m for m in excluded.values()}
    for row in rows:
        for entry in projection._history(row):
            if ORIGIN in entry:
                mapping = origins.get(entry[ORIGIN])
                _require(mapping is not None and mapping["target_row_id"] == str(row.id))
                original = by_id[mapping["old_row_id"]]
                expected = {
                    **deepcopy(projection._history(original)[mapping["history_index"]]),
                    ORIGIN: mapping["mapping_sha256"],
                }
                _require(entry == expected)
    return excluded


async def validated_world_rule_identity_mappings(
    session: AsyncSession,
    *,
    project_id: UUID,
) -> dict[tuple[str, int], dict[str, Any]]:
    """Internal replay/consumer boundary; returns only fully proved exclusions."""
    with session.no_autoflush:
        # Most projects have no migration. Do not load every chapter body just
        # to prove there are no corrections; still reject orphan derived copies.
        project = await session.scalar(
            select(ProjectModel)
            .where(ProjectModel.id == project_id)
            .execution_options(populate_existing=True, autoflush=False)
        )
        _require(project is not None)
        if not (project.metadata_json or {}).get(LEDGER):
            rows = list(
                await session.scalars(
                    select(WorldRuleModel)
                    .where(
                        WorldRuleModel.project_id == project_id,
                    )
                    .execution_options(populate_existing=True, autoflush=False)
                )
            )
            return _validated_mappings(project, rows, [], [])
        return _validated_mappings(*await _context(session, project_id))


async def world_rule_effective_history(
    session: AsyncSession,
    *,
    project_id: UUID,
    row_id: UUID,
) -> list[dict[str, Any]]:
    """Raw history minus proved wrong-row contributions; source activity stays with _plan.

    Consumers must still use projection._plan's adopted active sources and owned
    fields. Empty prose or an unvalidated metadata marker is never retirement.
    """
    with session.no_autoflush:
        context = await _context(session, project_id)
        excluded = _validated_mappings(*context)
        row = next((r for r in context[1] if r.id == row_id), None)
        _require(row is not None)
        return [
            deepcopy(entry)
            for i, entry in enumerate(projection._history(row))
            if (str(row.id), i) not in excluded
        ]


async def plan_world_rule_identity_recovery(
    session: AsyncSession, *, project_id: UUID
) -> dict[str, Any]:
    """Read-only per-project plan. Unknown source, raw or occurrence fails closed."""
    from bestseller.services.story_bible import chapter_world_rule_code, stable_world_rule_id

    _require(not session.new and not session.dirty and not session.deleted)
    with session.no_autoflush:
        project, rows, chapters, drafts = await _context(session, project_id)
        excluded = _validated_mappings(project, rows, chapters, drafts)
        by_name = {}
        for row in rows:
            name = _name(row.name)
            _require(name not in by_name)
            by_name[name] = row
        chapter_map = {str(c.id): c for c in chapters}
        draft_map = {str(d.id): d for d in drafts}
        adopted = {}
        mappings, preimages = [], {}
        planned_targets = {}
        target_baselines = {}
        for row in rows:
            history = projection._history(row)
            for i, entry in enumerate(history):
                if ORIGIN in entry or (str(row.id), i) in excluded:
                    continue
                update = entry.get("update")
                # Opaque/unscoped authored history is outside correction ownership.
                if "source_lineage" not in entry:
                    continue
                _require(isinstance(update, dict))
                target_name = _name(update.get("rule_or_location"))
                if target_name == _name(row.name):
                    continue
                baseline = _validate_baseline(row)
                _require(i >= len(baseline["history_prefix"]))
                proof = _receipt_proof(entry, chapter_map, draft_map, project_id)
                cid = entry["source_lineage"]["chapter_id"]
                if cid not in adopted:
                    chapter = chapter_map[cid]
                    draft, _ = await select_adopted_chapter_source(
                        session, chapter=chapter, lock=False
                    )
                    _require(draft is not None)
                    source, rejection = await validate_adopted_knowledge_source(
                        session,
                        project_id=project_id,
                        chapter_id=chapter.id,
                        source_chapter_draft_version_id=draft.id,
                        chapter_md=draft.content_md,
                        lock=False,
                    )
                    _require(not rejection)
                    successes = [
                        r
                        for r in (chapter.metadata_json or {}).get(
                            "story_bible_update_receipts", []
                        )
                        if r.get("status") in {"ok", "empty"}
                    ]
                    _require(
                        successes
                        and projection.same_source(successes[-1].get("source_lineage"), source)
                    )
                    adopted[cid] = projection._full_source(source, str(project_id))
                target = by_name.get(target_name)
                code = (
                    target.rule_code if target is not None else chapter_world_rule_code(target_name)
                )
                target_id = (
                    target.id if target is not None else stable_world_rule_id(project_id, code)
                )
                if target is None:
                    _require(all(r.id != target_id and r.rule_code != code for r in rows))
                    _require(await session.get(WorldRuleModel, target_id) is None)
                    _require(planned_targets.get(str(target_id), target_name) == target_name)
                    planned_targets[str(target_id)] = target_name
                target_baseline = _target_initial_baseline(
                    target,
                    project_id=project_id,
                    target_id=target_id,
                )
                target_baselines[str(target_id)] = target_baseline
                mapping = {
                    "schema": SCHEMA,
                    "old_row_id": str(row.id),
                    "old_name": _name(row.name),
                    "history_index": i,
                    "history_entry_sha256": _digest(entry),
                    "source_lineage": deepcopy(entry["source_lineage"]),
                    "evidence": proof,
                    "adoption_witness": adopted[cid],
                    "target_name": target_name,
                    "target_rule_code": code,
                    "target_row_id": str(target_id),
                    "target_baseline_sha256": _digest(target_baseline),
                }
                mappings.append(_signed(mapping, "mapping_sha256"))
                preimages[str(row.id)] = _row_image(row)
        _validate_occurrence_ownership(
            [*excluded.values(), *mappings],
            rows,
            chapter_map,
            draft_map,
            project_id,
        )
        inputs = {
            "project_metadata": project.metadata_json,
            "rows": [_row_image(row) for row in rows],
            "chapters": [
                {"id": str(c.id), "number": c.chapter_number, "metadata": c.metadata_json}
                for c in chapters
            ],
            "drafts": [
                {
                    "id": str(d.id),
                    "chapter_id": str(d.chapter_id),
                    "body": d.content_md,
                    "version": d.version_no,
                    "current": d.is_current,
                    "promotion": d.promotion_state,
                    "promoted_at": d.promoted_at.isoformat() if d.promoted_at else None,
                    "metadata": d.promotion_metadata,
                }
                for d in drafts
            ],
        }
        return _signed(
            {
                "schema": SCHEMA,
                "project_id": str(project_id),
                "provider_calls": 0,
                "input_sha256": _digest(inputs),
                "mappings": mappings,
                "preimages": preimages,
                "target_baselines": target_baselines,
            },
            "plan_sha256",
        )


async def apply_world_rule_identity_recovery(
    session: AsyncSession,
    *,
    project_id: UUID,
    expected_plan_sha256: str,
) -> dict[str, Any]:
    """Atomic, idempotent derived correction; caller commits/rolls back and refreshes indexes."""
    _require(not session.new and not session.dirty and not session.deleted)
    async with session.begin_nested():
        project = await session.scalar(
            select(ProjectModel)
            .where(ProjectModel.id == project_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        _require(project is not None)
        plan = await plan_world_rule_identity_recovery(session, project_id=project_id)
        _require(plan["plan_sha256"] == expected_plan_sha256)
        added = 0
        targets = {}
        for mapping in plan["mappings"]:
            target_id = UUID(mapping["target_row_id"])
            target = targets.get(target_id)
            if target is None:
                target = await session.get(WorldRuleModel, target_id)
                created = target is None
                if created:
                    target = WorldRuleModel(
                        id=target_id,
                        project_id=project_id,
                        name=mapping["target_name"],
                        rule_code=mapping["target_rule_code"],
                        description="",
                        metadata_json={},
                    )
                    session.add(target)
                    added += 1
                _require(
                    target.project_id == project_id and _name(target.name) == mapping["target_name"]
                )
                projection.capture_bible_baseline(target, created=created)
                _require(_digest(_validate_baseline(target)) == mapping["target_baseline_sha256"])
                targets[target_id] = target
            old = await session.get(WorldRuleModel, UUID(mapping["old_row_id"]))
            entry = deepcopy(projection._history(old)[mapping["history_index"]])
            target.metadata_json = {
                **target.metadata_json,
                "story_bible_update_history": [
                    *projection._history(target),
                    {**entry, ORIGIN: mapping["mapping_sha256"]},
                ],
            }
        if plan["mappings"]:
            project.metadata_json = {
                **(project.metadata_json or {}),
                LEDGER: [
                    *(project.metadata_json or {}).get(LEDGER, []),
                    deepcopy(plan),
                ],
            }
        await session.flush()
        changed = await projection.reproject_adopted_bible(session, project)
        return {
            "schema": SCHEMA,
            "project_id": str(project_id),
            "provider_calls": 0,
            "plan_sha256": plan["plan_sha256"],
            "mappings_added": len(plan["mappings"]),
            "rows_added": added,
            "projection_changed": changed,
        }
