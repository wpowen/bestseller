"""Repair Bible read models without changing source truth or calling a provider.

The receipt certifies native projections of current Bible rows, not adoption,
chapter-body correctness, grades, or knowledge closure. Callers own the commit.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
import hashlib
import json
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import delete, func, inspect, select, text
from sqlalchemy.engine import Result
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from bestseller.infra.db.models import (
    ChapterModel,
    CharacterModel,
    NarrativeTreeNodeModel,
    ProjectModel,
    RelationshipModel,
    RetrievalChunkModel,
    VolumeModel,
    WorldRuleModel,
)
from bestseller.services import narrative_tree, retrieval, world_rule_current
from bestseller.settings import AppSettings

_RECEIPT = "deterministic_audit_story_bible_read_views"
_TYPES = ("world_rule", "character", "relationship", "volume")
# Only fields consumed by the native Bible RAG / corresponding tree nodes.
_FIELDS = {
    "world_rule": (
        "id",
        "rule_code",
        "name",
        "description",
        "story_consequence",
        "exploitation_potential",
    ),
    "character": (
        "id",
        "name",
        "role",
        "goal",
        "fear",
        "flaw",
        "secret",
        "arc_trajectory",
        "arc_state",
        "power_tier",
    ),
    "relationship": (
        "id",
        "relationship_type",
        "public_face",
        "private_reality",
        "tension_summary",
    ),
    "volume": (
        "id",
        "volume_number",
        "title",
        "theme",
        "goal",
        "obstacle",
        "target_word_count",
        "target_chapter_count",
    ),
}
_PROJECT_META = (
    "logline",
    "themes",
    "stakes",
    "series_engine",
    "world_name",
    "world_premise",
    "power_structure",
    "power_system",
)


def _hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        ).encode()
    ).hexdigest()


def _fields(row: object, names: tuple[str, ...]) -> dict[str, Any]:
    return {name: getattr(row, name) for name in names}


def _projection_identity(settings: AppSettings) -> str:
    # Hash the actual implementation, including path allocation, tokenizer and
    # embedding code, rather than trusting a manually bumped version alone.
    return _hash(
        {
            "code": {
                Path(module.__file__).name: hashlib.sha256(
                    Path(module.__file__).read_bytes()
                ).hexdigest()
                for module in (retrieval, narrative_tree, world_rule_current)
            },
            "sidecar": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "chunk_size": settings.retrieval.chunk_size,
            "chunk_overlap": settings.retrieval.chunk_overlap,
            "embedding_model": settings.retrieval.embedding_model,
            "embedding_dimensions": settings.retrieval.embedding_dimensions,
        }
    )


async def _sources(session: AsyncSession, project: ProjectModel) -> tuple[dict[str, list], str]:
    # A long-lived reader may retain ORM objects across an ordinary producer
    # write. Reload persisted fields after begin_nested has flushed local edits.
    world_rules = list(
        await session.scalars(
            select(WorldRuleModel)
            .where(WorldRuleModel.project_id == project.id)
            .order_by(WorldRuleModel.rule_code, WorldRuleModel.name)
            .execution_options(populate_existing=True)
        )
    )
    sources = {
        "world_rule": await world_rule_current.current_world_rules(
            session,
            project_id=project.id,
            world_rules=world_rules,
        )
    }
    for kind, model in (
        ("character", CharacterModel),
        ("relationship", RelationshipModel),
        ("volume", VolumeModel),
    ):
        sources[kind] = list(
            await session.scalars(
                select(model)
                .where(model.project_id == project.id)
                .execution_options(populate_existing=True)
            )
        )
    payload = {
        kind: [_fields(row, _FIELDS[kind]) for row in sorted(rows, key=lambda row: str(row.id))]
        for kind, rows in sources.items()
    }
    payload["project"] = {
        **_fields(project, ("id", "title", "genre", "audience", "language")),
        **{key: (project.metadata_json or {}).get(key) for key in _PROJECT_META},
    }
    # Volume nodes consume the first chapter's number, never chapter runtime
    # metadata. Keep only that aggregate, so critic cycles cannot churn it.
    first_chapter = {}
    for volume_id, number in await session.execute(
        select(
            ChapterModel.volume_id,
            ChapterModel.chapter_number,
        ).where(ChapterModel.project_id == project.id, ChapterModel.volume_id.is_not(None))
    ):
        first_chapter[volume_id] = min(number, first_chapter.get(volume_id, number))
    for item, volume in zip(
        payload["volume"], sorted(sources["volume"], key=lambda row: str(row.id)), strict=True
    ):
        item["reader_hook_to_next"] = (volume.metadata_json or {}).get("reader_hook_to_next")
        item["first_chapter_number"] = first_chapter.get(volume.id)
    return sources, _hash(payload)


def _bible_node(node: NarrativeTreeNodeModel) -> bool:
    return narrative_tree._native_bible_tree_node(node)


async def _derived(session: AsyncSession, project_id: UUID) -> tuple[dict, list, list]:
    chunks = list(
        await session.scalars(
            select(RetrievalChunkModel)
            .where(
                RetrievalChunkModel.project_id == project_id,
                RetrievalChunkModel.source_type.in_(_TYPES),
            )
            .execution_options(populate_existing=True)
        )
    )
    nodes = [
        node
        for node in await session.scalars(
            select(NarrativeTreeNodeModel)
            .where(
                NarrativeTreeNodeModel.project_id == project_id,
            )
            .execution_options(populate_existing=True)
        )
        if _bible_node(node)
    ]
    chunk_payload = []
    for row in sorted(chunks, key=lambda row: str(row.id)):
        item = _fields(
            row,
            (
                "id",
                "source_type",
                "source_id",
                "chunk_index",
                "chunk_text",
                "embedding_model",
                "embedding_dim",
                "lexical_document",
                "metadata_json",
            ),
        )
        # pgvector is read back as float32 on both SQLite and PostgreSQL.
        item["embedding"] = [float(value) for value in row.embedding]
        chunk_payload.append(item)
    node_payload = []
    for row in sorted(nodes, key=lambda row: str(row.id)):
        item = _fields(
            row,
            (
                "id",
                "node_path",
                "parent_path",
                "depth",
                "node_type",
                "title",
                "summary",
                "body_md",
                "source_type",
                "source_ref_id",
                "scope_level",
                "scope_volume_number",
                "scope_chapter_number",
                "scope_scene_number",
                "lexical_document",
            ),
        )
        item["metadata"] = {
            key: value for key, value in (row.metadata_json or {}).items() if key != _RECEIPT
        }
        node_payload.append(item)
    return {"rag_sha256": _hash(chunk_payload), "tree_sha256": _hash(node_payload)}, chunks, nodes


def _require_complete(sources: dict[str, list], chunks: list, nodes: list) -> None:
    expected = {(kind, row.id) for kind, rows in sources.items() for row in rows}
    if {(row.source_type, row.source_id) for row in chunks} != expected:
        raise ValueError("story_bible_read_views_rag_incomplete")
    for kind in ("world_rule", "character", "volume"):
        actual = [row.source_ref_id for row in nodes if row.source_type == kind]
        if len(actual) != len(sources[kind]) or set(actual) != {row.id for row in sources[kind]}:
            raise ValueError("story_bible_read_views_tree_incomplete")
    expected_paths = narrative_tree._world_rule_paths(sources["world_rule"])
    if {
        row.source_ref_id: row.node_path for row in nodes if row.source_type == "world_rule"
    } != expected_paths:
        raise ValueError("story_bible_read_views_world_paths_incomplete")


class _PersistedReadSession:
    """SELECT-only column reads, including current-world lineage dependencies.

    Build unbound model copies so native isinstance checks still apply without
    populating/refreshing the caller's identity map or discarding dirty edits.
    This adapter deliberately exposes neither a connection nor transactions.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @property
    def no_autoflush(self) -> AbstractContextManager:
        return self._session.no_autoflush

    async def execute(self, statement: Select) -> Result:
        if not getattr(statement, "is_select", False) or statement._for_update_arg is not None:
            raise ValueError("story_bible_read_views_select_only_required")
        with self.no_autoflush:
            return await self._session.execute(
                statement.execution_options(autoflush=False, populate_existing=False)
            )

    async def scalars(self, statement: Select) -> list:
        descriptions = statement.column_descriptions
        model = descriptions[0].get("entity") if len(descriptions) == 1 else None
        if model is not None and descriptions[0]["expr"] is model:
            columns = [getattr(model, attr.key) for attr in inspect(model).column_attrs]
            result = await self.execute(
                statement.with_only_columns(*columns, maintain_column_froms=True)
            )
            return [model(**dict(row)) for row in result.mappings()]
        return list((await self.execute(statement)).scalars())

    async def scalar(self, statement: Select) -> object:
        rows = await self.scalars(statement)
        return rows[0] if rows else None

    async def get(self, model: type, key: UUID) -> object:
        primary = inspect(model).primary_key
        if len(primary) != 1:
            raise ValueError("story_bible_read_views_single_primary_key_required")
        return await self.scalar(select(model).where(primary[0] == key))


async def validate_story_bible_read_views(
    session: AsyncSession,
    settings: AppSettings,
    *,
    project: ProjectModel,
) -> dict[str, Any]:
    """Validate persisted sources, complete native views and their exact receipt.

    No autoflush, ORM refresh, savepoint, lock, rebuild or receipt write. Pending
    caller edits are neither certified nor changed. Missing/stale views raise;
    mutable owners must refresh them separately. This is an unlocked diagnostic,
    not a concurrent-producer serialization or adoption/quality guarantee.
    """
    from bestseller.services.creation_configuration import project_settings

    reader = _PersistedReadSession(session)
    with reader.no_autoflush:
        current = await reader.scalar(select(ProjectModel).where(ProjectModel.id == project.id))
        if current is None:
            raise ValueError("story_bible_read_views_project_missing")
        sources, source_hash = await _sources(reader, current)
        derived, chunks, nodes = await _derived(reader, current.id)
        _require_complete(sources, chunks, nodes)
        root = next((row for row in nodes if row.node_path == "/book"), None)
        if root is None or _RECEIPT not in (root.metadata_json or {}):
            raise ValueError("story_bible_read_views_receipt_required")
        expected_receipt = {
            "source_sha256": source_hash,
            "projection_sha256": _projection_identity(project_settings(settings, current)),
            **derived,
        }
        if root.metadata_json[_RECEIPT] != expected_receipt:
            raise ValueError("story_bible_read_views_receipt_mismatch")
        return {
            "status": "current",
            "refreshed": False,
            **expected_receipt,
            "retrieval_chunk_count": len(chunks),
            "tree_node_count": await reader.scalar(
                select(func.count()).select_from(NarrativeTreeNodeModel)
                .where(NarrativeTreeNodeModel.project_id == current.id)
            ),
            "bible_tree_node_count": len(nodes),
            "source_counts": {kind: len(rows) for kind, rows in sources.items()},
        }


async def ensure_story_bible_read_views(
    session: AsyncSession,
    settings: AppSettings,
    *,
    project: ProjectModel,
) -> dict[str, Any]:
    """Validate a source/derived receipt or atomically refresh both native views.

    No receipt (including legacy populated views) means one native rebuild.
    Subsequent calls verify actual rows and do no writes when unchanged. Only
    Bible projections are certified: native trees have no RelationshipModel
    nodes; relationship text/metadata are certified through Bible RAG instead.
    Errors propagate after rolling back the savepoint, retaining both old views.
    """
    if getattr(type(session), "_bestseller_read_views_validate_only", False) is True:
        return await validate_story_bible_read_views(session, settings, project=project)
    # SQLAlchemy's SQLite legacy mode otherwise lets RELEASE SAVEPOINT commit
    # without a real outer transaction. Preserve caller rollback there as well.
    connection = await session.connection()
    if connection.dialect.name == "sqlite":
        driver = (await connection.get_raw_connection()).driver_connection
        if not driver.in_transaction:
            await connection.exec_driver_sql("BEGIN")
    async with session.begin_nested():
        # Match producer lock order: project row first, native tree advisory
        # second. Lock before reading the receipt, including the no-op branch.
        current = await session.scalar(
            select(ProjectModel)
            .where(
                ProjectModel.id == project.id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if current is None:
            raise ValueError("story_bible_read_views_project_missing")
        await session.execute(
            text(
                "SELECT pg_advisory_xact_lock("
                "hashtextextended('narrative_tree_rebuild:' || :pid, 0))"
            ),
            {"pid": str(current.id)},
        )
        sources, source_hash = await _sources(session, current)
        identity = _projection_identity(settings)
        derived, chunks, nodes = await _derived(session, current.id)
        root = next((row for row in nodes if row.node_path == "/book"), None)
        expected_receipt = {"source_sha256": source_hash, "projection_sha256": identity, **derived}
        refreshed = root is None or (root.metadata_json or {}).get(_RECEIPT) != expected_receipt
        if refreshed:
            # Native refresh already removes retired world rules; also remove
            # orphaned Bible entity chunks without touching canon/draft RAG.
            for kind in _TYPES:
                await session.execute(
                    delete(RetrievalChunkModel).where(
                        RetrievalChunkModel.project_id == current.id,
                        RetrievalChunkModel.source_type == kind,
                        ~RetrievalChunkModel.source_id.in_([row.id for row in sources[kind]]),
                    )
                )
            await retrieval.refresh_story_bible_retrieval_index(session, settings, current.id)
            await narrative_tree.rebuild_narrative_tree(session, project=current, bible_only=True)
            # Never stamp the pre-build fingerprint onto a changed source.
            # Recheck consumed project fields and source rows, without hashing
            # critic/runtime metadata or adding broader source locks.
            await session.flush()
            await session.refresh(
                current,
                attribute_names=[
                    "title",
                    "genre",
                    "audience",
                    "language",
                    "metadata_json",
                ],
            )
            _, after_source_hash = await _sources(session, current)
            if after_source_hash != source_hash:
                raise ValueError("story_bible_read_views_source_changed")
            derived, chunks, nodes = await _derived(session, current.id)
            _require_complete(sources, chunks, nodes)
            root = next(row for row in nodes if row.node_path == "/book")
            expected_receipt = {
                "source_sha256": source_hash,
                "projection_sha256": identity,
                **derived,
            }
            root.metadata_json = {**(root.metadata_json or {}), _RECEIPT: expected_receipt}
            await session.flush()
        else:
            _require_complete(sources, chunks, nodes)
        return {
            "status": "refreshed" if refreshed else "current",
            "refreshed": refreshed,
            **expected_receipt,
            "retrieval_chunk_count": len(chunks),
            "tree_node_count": await session.scalar(
                select(func.count())
                .select_from(
                    NarrativeTreeNodeModel,
                )
                .where(NarrativeTreeNodeModel.project_id == current.id)
            ),
            "bible_tree_node_count": len(nodes),
            "source_counts": {kind: len(rows) for kind, rows in sources.items()},
        }
