"""Real SQLite archives/transactions; only the normal materializer is stubbed."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID, uuid4

import numpy as np
from pgvector.sqlalchemy import Vector
import pytest
from sqlalchemy import JSON, ForeignKeyConstraint, MetaData, delete, event, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from test_foundation_selection import seed_objects

from bestseller.infra.db.models import (
    CanonFactModel,
    ChapterDraftVersionModel,
    ChapterModel,
    CharacterModel,
    FactionModel,
    LocationModel,
    ProjectModel,
    RelationshipModel,
    RetrievalChunkModel,
    TimelineEventModel,
    VolumeModel,
    WorkflowRunModel,
    WorldRuleModel,
)
from bestseller.infra.db.models import (
    PlanningArtifactVersionModel as Artifact,
)
from bestseller.services import foundation_materialization_rebuild as rebuild
from bestseller.services import foundation_selection, planner, workflows
from bestseller.services.conception_snapshots import snapshot_hash
from bestseller.services.retrieval import refresh_story_bible_retrieval_index
from bestseller.services.story_bible import upsert_world_spec
from bestseller.settings import load_settings

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]
MODELS = (
    ProjectModel,
    WorkflowRunModel,
    Artifact,
    WorldRuleModel,
    LocationModel,
    FactionModel,
    CharacterModel,
    ChapterModel,
    ChapterDraftVersionModel,
    CanonFactModel,
    TimelineEventModel,
    RelationshipModel,
    VolumeModel,
    RetrievalChunkModel,
)


@pytest.fixture
async def case(tmp_path, monkeypatch):
    async def forbidden(*args, **kwargs):
        raise AssertionError("No provider may run during a derived-table rebuild")

    monkeypatch.setattr(planner, "complete_text", forbidden)
    monkeypatch.setattr("bestseller.services.llm.complete_text", forbidden)
    path = tmp_path / "foundation_rebuild_test.sqlite3"
    assert "test" in path.name
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    for model in MODELS:
        table = model.__table__.to_metadata(MetaData())
        for column in table.c:
            if isinstance(column.type, (JSONB, Vector)):
                column.type = JSON()
            if column.server_default is not None and "::" in str(column.server_default.arg):
                column.server_default = None
        for constraint in list(table.constraints):
            if isinstance(constraint, ForeignKeyConstraint):
                table.constraints.remove(constraint)
        async with engine.begin() as connection:
            await connection.run_sync(table.create)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    project, canonical, selected, failed, assets = seed_objects()
    world = {
        "world_name": "Glass observatory",
        "world_premise": "Records retain dates",
        "rules": [
            {
                "rule_id": "OBS-1",
                "name": "Sealed record",
                "description": "A seal records opening",
                "story_consequence": "Opening is observable",
                "exploitation_potential": "Compare seals",
            }
        ],
        "locations": [
            {
                "name": "测星台",
                "type": "observatory",
                "atmosphere": "Cold glass",
                "key_rules": ["OBS-1"],
                "story_role": "Compare records",
            }
        ],
        "factions": [
            {
                "name": "Record keepers",
                "goal": "Preserve logs",
                "method": "Duplicate copies",
                "relationship_to_protagonist": "Sponsors",
                "internal_conflict": "Public access",
            }
        ],
        "_meta": deepcopy(assets["world_spec"].content["_meta"]),
    }
    assets["world_spec"].content = world
    detached = SimpleNamespace(
        id=project.id,
        target_chapters=project.target_chapters,
        target_word_count=project.target_word_count,
        metadata_json=deepcopy(project.metadata_json),
        language=project.language,
    )
    detached._source_bound_planning_canonical = {
        "id": str(canonical.id),
        "content_hash": snapshot_hash(canonical.content),
        "content": deepcopy(canonical.content),
    }
    projection = planner._compile_source_bound_volume_plan(
        detached,
        project.metadata_json["premise"],
        *(assets[kind].content for kind in foundation_selection.KINDS[:3]),
    )
    assets["volume_plan"].content[0]["_meta"]["source_projection_sha256"] = (
        planner._approved_payload_hash(projection)
    )
    old_date = datetime(2024, 3, 2, 1, 2, 3, 456789, tzinfo=UTC)
    source = WorkflowRunModel(
        id=uuid4(),
        project_id=project.id,
        workflow_type="materialize_story_bible",
        status="completed",
        scope_type="project",
        scope_id=project.id,
        created_at=old_date,
        updated_at=old_date,
        metadata_json={
            "source_artifact_ids": {
                "cast_spec": str(assets["cast_spec"].id),
                "world_spec": str(uuid4()),
            },
            "old_receipt": ["keep"],
        },
    )
    foreign, *_ = seed_objects("zh-CN")
    oldrows = [
        WorldRuleModel(
            id=uuid4(),
            project_id=project.id,
            rule_code="OLD",
            name="Old unselected rule",
            description="Old description",
            story_consequence="Former consequence",
            exploitation_potential="Former option",
            metadata_json={"unknown": [None, "kept"]},
            created_at=old_date,
            updated_at=old_date + timedelta(days=1),
        ),
        LocationModel(
            id=uuid4(),
            project_id=project.id,
            name="Old pier",
            location_type="dock",
            atmosphere="Rain",
            key_rule_codes=["OLD"],
            story_role="Old role",
            metadata_json={"private_authored": "retain in history"},
            created_at=old_date,
            updated_at=old_date,
        ),
        FactionModel(
            id=uuid4(),
            project_id=project.id,
            name="Old guild",
            goal="Old goal",
            method="Old method",
            relationship_to_protagonist="Former",
            internal_conflict="Old conflict",
            metadata_json={"nested": {"all": True}},
            created_at=old_date,
            updated_at=old_date,
        ),
    ]
    character = CharacterModel(
        id=uuid4(),
        project_id=project.id,
        name="Mira",
        role="protagonist",
        metadata_json={"must_survive": True},
    )
    foreignrow = WorldRuleModel(
        id=uuid4(),
        project_id=foreign.id,
        rule_code="F",
        name="Foreign",
        description="Never delete",
        metadata_json={"foreign": True},
    )
    chunks = []
    for pid, source_type, source_id, index in (
        (project.id, "world_rule", oldrows[0].id, 0),
        (project.id, "world_rule", oldrows[0].id, 1),
        (project.id, "world_rule", uuid4(), 0),
        (project.id, "character", character.id, 0),
        (project.id, "relationship", uuid4(), 0),
        (project.id, "volume", uuid4(), 0),
        (project.id, "scene_context", oldrows[0].id, 0),
        (foreign.id, "world_rule", oldrows[0].id, 7),
    ):
        vector = np.zeros(1024, dtype=np.float32)
        vector[0], vector[1], vector[2] = np.float32(0.1), np.float32(-0.0), np.float32(1e-30)
        chunks.append(
            RetrievalChunkModel(
                id=uuid4(),
                project_id=pid,
                source_type=source_type,
                source_id=source_id,
                chunk_index=index,
                chunk_text="完整旧检索内容 " + source_type,
                embedding_model="retained-hash",
                embedding_dim=1024,
                embedding=vector,
                lexical_document="retained words",
                metadata_json={"unknown": {"all": [0, False, None]}},
                created_at=old_date,
            )
        )
    async with factory() as session:
        session.add_all(
            [
                project,
                foreign,
                canonical,
                selected,
                failed,
                source,
                *assets.values(),
                *oldrows,
                character,
                foreignrow,
                *chunks,
            ]
        )
        await session.commit()
    calls = []

    async def normal_materializer(session, slug, *, requested_by):
        current = await session.scalar(select(ProjectModel).where(ProjectModel.slug == slug))
        bound = await foundation_selection.load_selected_foundation_artifacts(
            session, project=current
        )
        assert bound is not None and bound["world_spec"].id == assets["world_spec"].id
        # Archive must already be persisted and complete before any derived insert.
        archives = list(
            await session.scalars(
                select(Artifact).where(Artifact.artifact_type == rebuild.ARCHIVE_TYPE)
            )
        )
        assert len(archives) == 1 and archives[0].status == "archived"
        assert all(not rows for rows in (await rebuild._snapshots(session, current.id)).values())
        assert archives[0].content["plan"]["before_retrieval_rows"]
        await upsert_world_spec(session, current, bound["world_spec"].content)
        await refresh_story_bible_retrieval_index(session, load_settings(env={}), current.id)
        materialized = WorkflowRunModel(
            id=uuid4(),
            project_id=current.id,
            workflow_type="materialize_story_bible",
            status="completed",
            scope_type="project",
            scope_id=current.id,
            metadata_json={
                "source_artifact_ids": {kind: str(row.id) for kind, row in bound.items()}
            },
        )
        session.add(materialized)
        await session.flush()
        calls.append(materialized.id)
        return SimpleNamespace(workflow_run_id=materialized.id)

    monkeypatch.setattr(workflows, "materialize_latest_story_bible", normal_materializer)
    try:
        yield SimpleNamespace(
            factory=factory,
            engine=engine,
            project_id=project.id,
            workflow_id=selected.id,
            source_id=source.id,
            assets=assets,
            foreign_id=foreign.id,
            character_id=character.id,
            old_rule_id=oldrows[0].id,
            calls=calls,
            materializer=normal_materializer,
        )
    finally:
        await engine.dispose()


async def all_rows(session):
    return {
        model.__tablename__: [
            rebuild._wire_row(model, dict(row))
            for row in (
                await session.execute(select(model.__table__).order_by(model.id))
            ).mappings()
        ]
        for model in MODELS
    }


async def inspect(session, case):
    project = await session.get(ProjectModel, case.project_id)
    return await rebuild.rebuild_selected_foundation_bible(
        session, project=project, workflow_id=case.workflow_id, requested_by="test-operator"
    )


async def apply(session, case, plan):
    project = await session.get(ProjectModel, case.project_id)
    return await rebuild.rebuild_selected_foundation_bible(
        session,
        project=project,
        workflow_id=case.workflow_id,
        requested_by="test-operator",
        execute=True,
        expected_plan_sha256=plan["plan_sha256"],
    )


async def test_full_all_column_archive_before_delete_exact_domain_and_idempotent(case):
    statements = []

    def track(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(case.engine.sync_engine, "before_cursor_execute", track)
    try:
        async with case.factory() as session:
            before = await all_rows(session)
            plan = await inspect(session, case)
            assert plan["status"] == "prepared"
            assert not any(
                sql.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))
                for sql in statements
            )
            assert await all_rows(session) == before
            receipt = await apply(session, case, plan)
            archive = await session.get(Artifact, UUID(receipt["archive_artifact_id"]))
            assert archive.status == "archived"
            assert archive.content["plan"] == plan["plan"]
            archived_chunks = archive.content["plan"]["before_retrieval_rows"]
            owned_chunks = [
                row
                for row in before["retrieval_chunks"]
                if row["project_id"] == str(case.project_id)
                and row["source_type"] in rebuild.BIBLE_RETRIEVAL_TYPES
            ]
            assert archived_chunks == owned_chunks
            assert len(archived_chunks) == 6
            assert all(
                set(row) == {c.name for c in RetrievalChunkModel.__table__.columns}
                and len(row["embedding"]) == 1024
                for row in archived_chunks
            )
            raw_vector = np.zeros(1024, dtype=np.float32)
            raw_vector[0], raw_vector[1], raw_vector[2] = (
                np.float32(0.1),
                np.float32(-0.0),
                np.float32(1e-30),
            )
            assert all(
                np.asarray(row["embedding"], dtype=np.float32).tobytes() == raw_vector.tobytes()
                for row in archived_chunks
            )
            assert (
                archive.content["plan"]["archive_vector_encoding"]["retrieval_chunks.embedding"][
                    "type"
                ]
                == "VECTOR(1024)"
            )
            for model in rebuild._TABLES:
                name = model.__tablename__
                expected = [
                    row for row in before[name] if row["project_id"] == str(case.project_id)
                ]
                assert archive.content["plan"]["before_rows"][name] == expected
                assert set(expected[0]) == {column.name for column in model.__table__.columns}
                assert "created_at" in expected[0] and "updated_at" in expected[0]
            insert = next(
                i
                for i, sql in enumerate(statements)
                if sql.startswith("INSERT INTO planning_artifact_versions")
            )
            deletion = next(
                i for i, sql in enumerate(statements) if sql.startswith("DELETE FROM world_rules")
            )
            assert insert < deletion
            assert insert < next(
                i
                for i, sql in enumerate(statements)
                if sql.startswith("DELETE FROM retrieval_chunks")
            )
            after = await all_rows(session)

            def protected_chunks(rows):
                return [
                    row
                    for row in rows
                    if row["project_id"] != str(case.project_id)
                    or row["source_type"] not in rebuild.BIBLE_RETRIEVAL_TYPES
                ]

            assert protected_chunks(after["retrieval_chunks"]) == protected_chunks(
                before["retrieval_chunks"]
            )
            own_indexed = {
                r["source_id"]
                for r in after["retrieval_chunks"]
                if r["project_id"] == str(case.project_id) and r["source_type"] == "world_rule"
            }
            current_rules = {
                r["id"] for r in after["world_rules"] if r["project_id"] == str(case.project_id)
            }
            assert own_indexed == current_rules
            assert after["characters"] == before["characters"]
            assert after["canon_facts"] == before["canon_facts"] == []
            assert after["timeline_events"] == before["timeline_events"] == []
            assert [r for r in after["world_rules"] if r["project_id"] == str(case.foreign_id)] == [
                r for r in before["world_rules"] if r["project_id"] == str(case.foreign_id)
            ]
            for name in ("planning_artifact_versions", "workflow_runs"):
                indexed = {row["id"]: row for row in after[name]}
                assert all(indexed[row["id"]] == row for row in before[name])
            assert (
                not receipt["author_authority"]
                and not receipt["grade_asserted"]
                and not receipt["credit_granted"]
            )
            await session.commit()
        async with case.factory() as session:
            before = await all_rows(session)
            assert (await inspect(session, case))["status"] == "noop"
            assert (await apply(session, case, plan))["status"] == "noop"
            assert await all_rows(session) == before
            assert len(case.calls) == 1
    finally:
        event.remove(case.engine.sync_engine, "before_cursor_execute", track)


@pytest.mark.parametrize(
    "failure",
    [
        "raise_after_upsert",
        "extra_row",
        "wrong_field",
        "knowledge",
        "orphan_chunk",
        "missing_rule_chunks",
        "protected_chunk",
    ],
)
async def test_savepoint_rolls_back_archive_pin_delete_upsert_and_materializer_writes(
    case, monkeypatch, failure
):
    async def failing(session, slug, *, requested_by):
        result = await case.materializer(session, slug, requested_by=requested_by)
        if failure == "raise_after_upsert":
            raise ValueError("injected materialization failure")
        if failure == "extra_row":
            session.add(
                FactionModel(project_id=case.project_id, name="Unselected extra", metadata_json={})
            )
        elif failure == "wrong_field":
            row = await session.scalar(
                select(LocationModel).where(LocationModel.project_id == case.project_id)
            )
            row.atmosphere = "Incorrect selected field"
        elif failure == "knowledge":
            session.add(
                ChapterModel(
                    project_id=case.project_id, chapter_number=1, chapter_goal="Prose arrived"
                )
            )
        elif failure == "orphan_chunk":
            session.add(
                RetrievalChunkModel(
                    project_id=case.project_id,
                    source_type="world_rule",
                    source_id=uuid4(),
                    chunk_index=0,
                    chunk_text="stale",
                    embedding_model="hash",
                    embedding_dim=1024,
                    embedding=[0.0] * 1024,
                    metadata_json={},
                )
            )
        elif failure == "missing_rule_chunks":
            await session.execute(
                delete(RetrievalChunkModel).where(
                    RetrievalChunkModel.project_id == case.project_id,
                    RetrievalChunkModel.source_type == "world_rule",
                )
            )
        elif failure == "protected_chunk":
            row = await session.scalar(
                select(RetrievalChunkModel).where(
                    RetrievalChunkModel.project_id == case.project_id,
                    RetrievalChunkModel.source_type == "scene_context",
                )
            )
            row.chunk_text = "Forbidden rewrite of unrelated source"
        return result

    monkeypatch.setattr(workflows, "materialize_latest_story_bible", failing)
    async with case.factory() as session:
        plan = await inspect(session, case)
        before = await all_rows(session)
        with pytest.raises(ValueError):
            await apply(session, case, plan)
        assert await all_rows(session) == before
        # A caller commit after catching the error must not publish a partial migration.
        await session.commit()
    async with case.factory() as session:
        assert await all_rows(session) == before


@pytest.mark.parametrize("kind", ["chapter", "draft", "canon", "timeline"])
async def test_any_prose_or_knowledge_including_noncurrent_draft_blocks_before_writes(case, kind):
    pid = case.project_id
    blocked = {
        "chapter": lambda: ChapterModel(
            project_id=pid, chapter_number=1, chapter_goal="Unwritten plan"
        ),
        "draft": lambda: ChapterDraftVersionModel(
            project_id=pid,
            chapter_id=uuid4(),
            version_no=1,
            content_md="Retained noncurrent body",
            is_current=False,
        ),
        "canon": lambda: CanonFactModel(
            project_id=pid,
            subject_type="world",
            subject_label="Bridge",
            predicate="open",
            fact_type="state",
            value_json={"open": True},
        ),
        "timeline": lambda: TimelineEventModel(
            project_id=pid,
            event_name="Arrival",
            event_type="event",
            story_time_label="Day 1",
            story_order=1,
        ),
    }
    async with case.factory() as session:
        plan = await inspect(session, case)
        session.add(blocked[kind]())
        await session.commit()
        before = await all_rows(session)
        with pytest.raises(ValueError, match="prose_or_knowledge"):
            await inspect(session, case)
        with pytest.raises(ValueError, match="prose_or_knowledge"):
            await apply(session, case, plan)
        assert await all_rows(session) == before


@pytest.mark.parametrize("change", ["derived", "cast", "ambiguous", "selection", "canonical"])
async def test_revalidate_plan_and_source_without_bypassing_selector(case, change):
    async with case.factory() as session:
        plan = await inspect(session, case)
        if change == "derived":
            row = await session.scalar(
                select(WorldRuleModel).where(WorldRuleModel.project_id == case.project_id)
            )
            row.description = "Changed after inspect"
        elif change in {"cast", "ambiguous"}:
            row = await session.get(WorkflowRunModel, case.source_id)
            if change == "cast":
                row.metadata_json = {"source_artifact_ids": {"cast_spec": str(uuid4())}}
            else:
                session.add(
                    WorkflowRunModel(
                        id=uuid4(),
                        project_id=case.project_id,
                        workflow_type=row.workflow_type,
                        status=row.status,
                        scope_type="project",
                        scope_id=case.project_id,
                        metadata_json=deepcopy(row.metadata_json),
                        created_at=row.created_at,
                    )
                )
        elif change == "selection":
            row = await session.get(Artifact, case.assets["world_spec"].id)
            row.content = {**row.content, "world_name": "Changed canonical-dependent asset"}
        else:
            project = await session.get(ProjectModel, case.project_id)
            project.metadata_json = {
                **project.metadata_json,
                "creation_canonical_snapshot_hash": "0" * 64,
            }
        await session.commit()
        before = await all_rows(session)
        with pytest.raises(ValueError):
            await apply(session, case, plan)
        assert await all_rows(session) == before


async def test_no_implicit_commit_caller_rollback_retains_every_original_row(case):
    async with case.factory() as session:
        before = await all_rows(session)
        plan = await inspect(session, case)
        await apply(session, case, plan)
        await session.rollback()
        assert await all_rows(session) == before


async def test_reject_unflushed_caller_changes_without_flushing_them(case):
    async with case.factory() as session:
        plan = await inspect(session, case)
        project = await session.get(ProjectModel, case.project_id)
        project.title = "Unsaved user edit"
        with pytest.raises(ValueError, match="unflushed_caller_changes"):
            await apply(session, case, plan)
        assert project.title == "Unsaved user edit" and project in session.dirty


async def test_archive_mutation_rejected_on_idempotent_repeat(case):
    async with case.factory() as session:
        plan = await inspect(session, case)
        receipt = await apply(session, case, plan)
        archive = await session.get(Artifact, UUID(receipt["archive_artifact_id"]))
        archive.content = {**archive.content, "unexpected": "tamper"}
        await session.commit()
        with pytest.raises(ValueError, match="archive_changed"):
            await inspect(session, case)


async def test_chunk_changes_after_inspect_fail_cas_without_writes(case):
    async with case.factory() as session:
        plan = await inspect(session, case)
        row = await session.scalar(
            select(RetrievalChunkModel).where(
                RetrievalChunkModel.project_id == case.project_id,
                RetrievalChunkModel.source_type == "world_rule",
            )
        )
        row.metadata_json = {"late": "retained change"}
        await session.commit()
        before = await all_rows(session)
        with pytest.raises(ValueError, match="plan_changed"):
            await apply(session, case, plan)
        assert await all_rows(session) == before


@pytest.mark.parametrize(
    "bad",
    [
        np.full(1024, np.nan, dtype=np.float32),
        np.full(1024, np.inf, dtype=np.float32),
        np.zeros(1023, dtype=np.float32),
        ["0"] * 1024,
    ],
)
async def test_vector_archive_rejects_nonfinite_wrong_shape_or_coercion(bad):
    row = {column.name: None for column in RetrievalChunkModel.__table__.columns}
    row["embedding"] = bad
    with pytest.raises(ValueError, match="foundation_rebuild_vector_"):
        rebuild._wire_row(RetrievalChunkModel, row)
