"""Archive and rebuild three derived Bible tables before any prose exists.

This transaction-local migration selects a proved foundation cohort, not a
latest asset. Archives are historical data, never author authority or grades.
The caller owns commit; this helper performs no provider or financial action.
"""

# ruff: noqa: ANN401 -- Full SQL column snapshots require validated JSON values.

from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime
from decimal import Decimal
import hashlib
import json
import math
from numbers import Real
from typing import Any
from uuid import UUID, uuid4

from pgvector.sqlalchemy import Vector
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.infra.db.models import (
    CanonFactModel,
    ChapterDraftVersionModel,
    ChapterModel,
    FactionModel,
    LocationModel,
    PlanningArtifactVersionModel,
    ProjectModel,
    RetrievalChunkModel,
    TimelineEventModel,
    WorkflowRunModel,
    WorldRuleModel,
)
from bestseller.services import foundation_selection
from bestseller.services.story_bible import (
    parse_world_spec_input,
    stable_faction_id,
    stable_location_id,
    stable_world_rule_id,
)
from bestseller.settings import apply_runtime_llm_profile, load_settings

POLICY = "foundation-materialization-rebuild.v1"
ARCHIVE_TYPE = "foundation_materialization_archive_v1"
HISTORY = "deterministic_audit_foundation_materialization_rebuild"
_TABLES = (WorldRuleModel, LocationModel, FactionModel)
_GUARDS = (ChapterModel, ChapterDraftVersionModel, CanonFactModel, TimelineEventModel)
BIBLE_RETRIEVAL_TYPES = ("world_rule", "character", "relationship", "volume")


def _need(ok: Any, code: str) -> None:
    if not ok:
        raise ValueError("foundation_rebuild_" + code)


def _json(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def _hash(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _wire(value: Any) -> Any:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {key: _wire(child) for key, child in value.items()}
    if isinstance(value, (tuple, list)):
        return [_wire(child) for child in value]
    _json(value)
    return value


def _wire_row(model: type, row: dict) -> dict:
    """Encode PG Vector's float32 ndarray by its declared column, without loss.

    A stored float32 is exactly representable as a Python/JSON float64. Keep
    signed zero and every component; arbitrary arrays in other fields fail.
    """
    result = {}
    for column in model.__table__.columns:
        value = row[column.name]
        if isinstance(column.type, Vector):
            vector = value.tolist() if hasattr(value, "tolist") else value
            _need(isinstance(vector, list) and len(vector) == column.type.dim, "vector_shape")
            _need(
                all(
                    isinstance(item, Real)
                    and not isinstance(item, bool)
                    and math.isfinite(float(item))
                    for item in vector
                ),
                "vector_nonfinite_or_nonnumeric",
            )
            result[column.name] = [float(item) for item in vector]
        else:
            result[column.name] = _wire(value)
    return result


async def _rows(session: AsyncSession, model: type, project_id: UUID) -> list[dict]:
    rows = (
        (
            await session.execute(
                select(model.__table__).where(model.project_id == project_id).order_by(model.id)
            )
        )
        .mappings()
        .all()
    )
    return [_wire_row(model, dict(row)) for row in rows]


async def _snapshots(session: AsyncSession, project_id: UUID) -> dict:
    return {model.__tablename__: await _rows(session, model, project_id) for model in _TABLES}


async def _retrieval_rows(
    session: AsyncSession,
    project_id: UUID,
    *,
    protected: bool = False,
) -> list[dict]:
    owned = (RetrievalChunkModel.project_id == project_id) & (
        RetrievalChunkModel.source_type.in_(BIBLE_RETRIEVAL_TYPES)
    )
    rows = (
        (
            await session.execute(
                select(RetrievalChunkModel.__table__)
                .where(~owned if protected else owned)
                .order_by(RetrievalChunkModel.id)
            )
        )
        .mappings()
        .all()
    )
    return [_wire_row(RetrievalChunkModel, dict(row)) for row in rows]


async def _verify_world_retrieval(session: AsyncSession, project_id: UUID) -> None:
    actual = set(
        await session.scalars(
            select(WorldRuleModel.id).where(WorldRuleModel.project_id == project_id)
        )
    )
    indexed = set(
        await session.scalars(
            select(RetrievalChunkModel.source_id).where(
                RetrievalChunkModel.project_id == project_id,
                RetrievalChunkModel.source_type == "world_rule",
            )
        )
    )
    _need(indexed == actual, "world_retrieval_missing_or_orphaned")


async def _zero_prose(session: AsyncSession, project_id: UUID) -> dict:
    counts = {}
    for model in _GUARDS:
        counts[model.__tablename__] = await session.scalar(
            select(func.count()).select_from(model).where(model.project_id == project_id)
        )
    _need(all(value == 0 for value in counts.values()), "prose_or_knowledge_exists")
    return counts


def _expected_world(project_id: UUID, content: dict) -> dict:
    world = parse_world_spec_input(content)
    pid = str(project_id)
    expected = {
        "world_rules": [
            {
                "id": str(stable_world_rule_id(project_id, rule.rule_id or f"R{index:03d}")),
                "project_id": pid,
                "rule_code": rule.rule_id or f"R{index:03d}",
                "name": rule.name,
                "description": rule.description,
                "story_consequence": rule.story_consequence,
                "exploitation_potential": rule.exploitation_potential,
                "metadata": {"world_name": world.world_name, "world_premise": world.world_premise},
            }
            for index, rule in enumerate(world.rules, 1)
        ],
        "locations": [
            {
                "id": str(stable_location_id(project_id, row.name)),
                "project_id": pid,
                "name": row.name,
                "location_type": row.location_type,
                "atmosphere": row.atmosphere,
                "key_rule_codes": list(row.key_rules),
                "story_role": row.story_role,
                "metadata": {},
            }
            for row in world.locations
        ],
        "factions": [
            {
                "id": str(stable_faction_id(project_id, row.name)),
                "project_id": pid,
                "name": row.name,
                "goal": row.goal,
                "method": row.method,
                "relationship_to_protagonist": row.relationship_to_protagonist,
                "internal_conflict": row.internal_conflict,
                "metadata": {},
            }
            for row in world.factions
        ],
    }
    for name, rows in expected.items():
        _need(len({row["id"] for row in rows}) == len(rows), "selected_world_duplicate_identity")
        _need(len({row["name"] for row in rows}) == len(rows), "selected_world_duplicate_name")
        if name == "world_rules":
            _need(
                len({row["rule_code"] for row in rows}) == len(rows),
                "selected_world_duplicate_code",
            )
        rows.sort(key=lambda row: row["id"])
    return expected


def _domain(rows: dict) -> dict:
    return {
        name: [
            {key: value for key, value in row.items() if key not in {"created_at", "updated_at"}}
            for row in values
        ]
        for name, values in rows.items()
    }


async def _selection(session: AsyncSession, project: ProjectModel, workflow_id: UUID) -> dict:
    # Refresh only the exact selector inputs; stale identity-map rows cannot
    # make an inspect/apply CAS accept changed artifacts or canonical metadata.
    workflow = await session.get(WorkflowRunModel, workflow_id, populate_existing=True)
    if workflow is not None and isinstance(workflow.metadata_json, dict):
        mapping = workflow.metadata_json.get("artifact_ids")
        if isinstance(mapping, dict):
            for kind in foundation_selection.KINDS:
                if kind in mapping:
                    await session.get(
                        PlanningArtifactVersionModel,
                        UUID(str(mapping[kind])),
                        populate_existing=True,
                    )
    canonical_id = project.metadata_json.get("creation_canonical_snapshot_id")
    if canonical_id:
        await session.get(
            PlanningArtifactVersionModel, UUID(str(canonical_id)), populate_existing=True
        )
    return await foundation_selection.select_completed_foundation(
        session,
        project=project,
        workflow_id=workflow_id,
        settings=apply_runtime_llm_profile(load_settings()),
    )


async def _inspect(session: AsyncSession, project: ProjectModel, workflow_id: UUID) -> dict:
    counts = await _zero_prose(session, project.id)
    selected = await _selection(session, project, workflow_id)
    latest = (
        (
            await session.execute(
                select(WorkflowRunModel.__table__)
                .where(
                    WorkflowRunModel.project_id == project.id,
                    WorkflowRunModel.workflow_type == "materialize_story_bible",
                    WorkflowRunModel.status == "completed",
                )
                .order_by(WorkflowRunModel.created_at.desc())
                .limit(2)
            )
        )
        .mappings()
        .all()
    )
    _need(bool(latest), "completed_materialization_missing")
    _need(
        len(latest) < 2 or latest[0]["created_at"] != latest[1]["created_at"],
        "completed_materialization_ambiguous",
    )
    source = _wire(dict(latest[0]))
    meta = source.get("metadata")
    _need(
        source["scope_type"] == "project"
        and source["scope_id"] == str(project.id)
        and isinstance(meta, dict)
        and isinstance(meta.get("source_artifact_ids"), dict)
        and meta["source_artifact_ids"].get("cast_spec") == selected["artifact_ids"]["cast_spec"],
        "materialized_cast_binding",
    )
    world = await session.get(
        PlanningArtifactVersionModel,
        UUID(selected["artifact_ids"]["world_spec"]),
        populate_existing=True,
    )
    _need(world is not None, "selected_world_missing")
    before = await _snapshots(session, project.id)
    before_retrieval = await _retrieval_rows(session, project.id)
    expected = _expected_world(project.id, world.content)
    history = project.metadata_json.get(HISTORY, [])
    _need(
        isinstance(history, list) and all(isinstance(row, dict) for row in history),
        "history_invalid",
    )
    for previous in reversed(history):
        if (
            previous.get("selection") == selected
            and previous.get("post_rows_sha256") == _hash(before)
            and project.metadata_json.get(foundation_selection.POINTER) == selected
        ):
            archive = await session.get(
                PlanningArtifactVersionModel,
                UUID(previous["archive_artifact_id"]),
                populate_existing=True,
            )
            _need(
                archive is not None
                and archive.project_id == project.id
                and archive.artifact_type == ARCHIVE_TYPE
                and archive.status == "archived"
                and _hash(archive.content) == previous.get("archive_content_sha256")
                and archive.content.get("plan_sha256") == previous.get("plan_sha256"),
                "archive_changed",
            )
            _need(
                previous.get("schema") == POLICY
                and previous.get("receipt_sha256")
                == _hash(
                    {key: value for key, value in previous.items() if key != "receipt_sha256"}
                ),
                "history_receipt_changed",
            )
            _need(_domain(before) == expected, "post_domain_changed")
            _need(
                previous.get("post_retrieval_rows_sha256") == _hash(before_retrieval),
                "post_retrieval_changed",
            )
            await _verify_world_retrieval(session, project.id)
            return {**deepcopy(previous), "status": "noop"}
    plan = {
        "schema": POLICY,
        "project_id": str(project.id),
        "selection": selected,
        "source_materialization": source,
        "before_rows": before,
        "before_rows_sha256": _hash(before),
        "before_retrieval_rows": before_retrieval,
        "before_retrieval_rows_sha256": _hash(before_retrieval),
        "retrieval_source_types": list(BIBLE_RETRIEVAL_TYPES),
        "protected_retrieval_sha256": _hash(
            await _retrieval_rows(session, project.id, protected=True)
        ),
        "expected_domain": expected,
        "zero_prose_counts": counts,
        "archive_column_types": {
            model.__tablename__: {
                column.name: str(column.type) for column in model.__table__.columns
            }
            for model in (*_TABLES, RetrievalChunkModel)
        },
        "archive_vector_encoding": {
            "retrieval_chunks.embedding": {
                "type": str(RetrievalChunkModel.__table__.c.embedding.type),
                "dimensions": RetrievalChunkModel.__table__.c.embedding.type.dim,
                "stored_precision": "float32",
                "encoding": "finite-json-number-array",
                "component_conversion": "exact-float32-to-float64",
            },
        },
        "archival_only": True,
        "author_authority": False,
        "grade_asserted": False,
        "credit_granted": False,
    }
    return {"status": "prepared", "plan_sha256": _hash(plan), "plan": plan}


async def rebuild_selected_foundation_bible(
    session: AsyncSession,
    *,
    project: ProjectModel,
    workflow_id: UUID,
    requested_by: str,
    execute: bool = False,
    expected_plan_sha256: str | None = None,
) -> dict:
    """Inspect by default; atomically archive/rebuild after exact reviewed-plan CAS.

    Apply requires a clean session so opening its savepoint never flushes
    unrelated caller work. The caller must still commit or roll back afterwards.
    """
    _need(
        isinstance(requested_by, str) and bool(requested_by.strip()) and len(requested_by) <= 64,
        "requested_by",
    )
    _need(type(execute) is bool, "execute_type")
    _need(
        not session.new
        and not session.deleted
        and not any(session.is_modified(row, include_collections=True) for row in session.dirty),
        "unflushed_caller_changes",
    )
    workflow_id = UUID(str(workflow_id))
    project_id = project.id
    if not execute:
        with session.no_autoflush:
            current = await session.get(ProjectModel, project_id, populate_existing=True)
            _need(current is not None, "project_missing")
            return await _inspect(session, current, workflow_id)
    _need(
        isinstance(expected_plan_sha256, str) and len(expected_plan_sha256) == 64,
        "expected_plan_required",
    )
    # SQLite legacy transaction mode does not BEGIN for reads. Without an
    # actual parent transaction, RELEASE SAVEPOINT would commit the migration
    # and defeat a caller rollback. PostgreSQL already begins on the first SQL.
    connection = await session.connection()
    if connection.dialect.name == "sqlite":
        driver = (await connection.get_raw_connection()).driver_connection
        in_transaction = getattr(driver, "in_transaction", None)
        _need(type(in_transaction) is bool, "sqlite_transaction_unknown")
        if not in_transaction:
            await connection.exec_driver_sql("BEGIN")
    async with session.begin_nested():
        current = await session.scalar(
            select(ProjectModel)
            .where(ProjectModel.id == project_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        _need(current is not None, "project_missing")
        inspected = await _inspect(session, current, workflow_id)
        _need(inspected["plan_sha256"] == expected_plan_sha256, "plan_changed")
        if inspected["status"] == "noop":
            return inspected
        plan = inspected["plan"]
        await foundation_selection.pin_completed_foundation(
            session,
            project=current,
            workflow_id=workflow_id,
            settings=apply_runtime_llm_profile(load_settings()),
        )
        version = (
            await session.scalar(
                select(func.max(PlanningArtifactVersionModel.version_no)).where(
                    PlanningArtifactVersionModel.project_id == project_id,
                    PlanningArtifactVersionModel.artifact_type == ARCHIVE_TYPE,
                    PlanningArtifactVersionModel.scope_ref_id.is_(None),
                )
            )
            or 0
        ) + 1
        archive_body = {
            "schema": POLICY + ".archive",
            "plan_sha256": inspected["plan_sha256"],
            "requested_by": requested_by,
            "plan": deepcopy(plan),
        }
        archive = PlanningArtifactVersionModel(
            id=uuid4(),
            project_id=project_id,
            artifact_type=ARCHIVE_TYPE,
            scope_ref_id=None,
            version_no=version,
            status="archived",
            schema_version="1.0",
            content=archive_body,
            source_run_id=workflow_id,
            idempotency_key=POLICY + ":" + inspected["plan_sha256"],
            created_by=requested_by,
            notes="Archival derived-table history only; no author, grade or credit authority.",
        )
        session.add(archive)
        await session.flush()
        persisted_archive = await session.scalar(
            select(PlanningArtifactVersionModel.__table__.c.content).where(
                PlanningArtifactVersionModel.id == archive.id
            )
        )
        _need(
            archive.status == "archived" and _hash(persisted_archive) == _hash(archive_body),
            "archive_not_persisted",
        )
        for model in _TABLES:
            await session.execute(delete(model).where(model.project_id == project_id))
        # Archive above includes every own Bible chunk that the normal full
        # materializer can rewrite, including pre-existing orphan chunks.
        old_rule_ids = [UUID(row["id"]) for row in plan["before_rows"]["world_rules"]]
        await session.execute(
            delete(RetrievalChunkModel).where(
                RetrievalChunkModel.project_id == project_id,
                RetrievalChunkModel.source_type == "world_rule",
                RetrievalChunkModel.source_id.in_(old_rule_ids),
            )
        )
        from bestseller.services import workflows

        materialized = await workflows.materialize_latest_story_bible(
            session, current.slug, requested_by=requested_by
        )
        await session.flush()
        new_workflow = await session.get(
            WorkflowRunModel, materialized.workflow_run_id, populate_existing=True
        )
        _need(
            new_workflow is not None
            and new_workflow.project_id == project_id
            and new_workflow.workflow_type == "materialize_story_bible"
            and new_workflow.status == "completed"
            and new_workflow.scope_type == "project"
            and new_workflow.scope_id == project_id
            and isinstance(new_workflow.metadata_json, dict)
            and new_workflow.metadata_json.get("source_artifact_ids")
            == plan["selection"]["artifact_ids"],
            "new_materialization_binding",
        )
        post = await _snapshots(session, project_id)
        _need(_domain(post) == plan["expected_domain"], "post_domain_mismatch")
        await _verify_world_retrieval(session, project_id)
        _need(
            _hash(await _retrieval_rows(session, project_id, protected=True))
            == plan["protected_retrieval_sha256"],
            "protected_retrieval_changed",
        )
        _need(
            await _zero_prose(session, project_id) == plan["zero_prose_counts"], "knowledge_changed"
        )
        selection = await foundation_selection.select_completed_foundation(
            session,
            project=current,
            workflow_id=workflow_id,
            settings=apply_runtime_llm_profile(load_settings()),
        )
        _need(selection == plan["selection"], "selection_changed")
        preserved_archive = await session.scalar(
            select(PlanningArtifactVersionModel.__table__.c.content).where(
                PlanningArtifactVersionModel.id == archive.id
            )
        )
        _need(_hash(preserved_archive) == _hash(archive_body), "archive_changed")
        receipt = {
            "schema": POLICY,
            "status": "applied",
            "plan_sha256": inspected["plan_sha256"],
            "project_id": str(project_id),
            "selection": selection,
            "archive_artifact_id": str(archive.id),
            "archive_content_sha256": _hash(archive_body),
            "source_materialization_workflow_id": plan["source_materialization"]["id"],
            "materialization_workflow_id": str(materialized.workflow_run_id),
            "before_rows_sha256": plan["before_rows_sha256"],
            "post_rows_sha256": _hash(post),
            "before_retrieval_rows_sha256": plan["before_retrieval_rows_sha256"],
            "post_retrieval_rows_sha256": _hash(await _retrieval_rows(session, project_id)),
            "zero_prose_counts": plan["zero_prose_counts"],
            "requested_by": requested_by,
            "archival_only": True,
            "author_authority": False,
            "grade_asserted": False,
            "credit_granted": False,
        }
        receipt["receipt_sha256"] = _hash(receipt)
        metadata = deepcopy(current.metadata_json)
        metadata[HISTORY] = [*metadata.get(HISTORY, []), deepcopy(receipt)]
        current.metadata_json = metadata
        await session.flush()
        return receipt
