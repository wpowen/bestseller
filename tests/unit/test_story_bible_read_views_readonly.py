"""Real existing SQLite fixtures; actual readonly adapters; strict SELECT audit."""

from contextlib import contextmanager
from copy import deepcopy

import pytest
from sqlalchemy import delete, event, inspect, select, text
from test_story_bible_read_views import (
    _rule,
)
from test_story_bible_read_views import (
    db as db,
)
from test_story_bible_read_views import (
    knowledge_db as knowledge_db,
)
from test_story_bible_read_views import (
    offline as offline,
)
from test_story_bible_read_views import (
    retired as retired,
)
from test_story_bible_read_views import (
    seeded as seeded,
)
from test_story_bible_read_views import (
    tree_tables as tree_tables,
)
from test_story_bible_read_views import (
    views as views,
)

from bestseller.infra.db.models import (
    CharacterModel,
    NarrativeTreeNodeModel,
    PlanningArtifactVersionModel,
    ProjectModel,
    RetrievalChunkModel,
)
from bestseller.services import narrative_tree, retrieval, story_bible, story_bible_projection
from bestseller.services.prompt_source_policy import content_hash
from bestseller.services.retained_review_input_proof import _ReadOnlySession
from bestseller.services.story_bible_read_views import (
    _RECEIPT,
    ensure_story_bible_read_views,
    validate_story_bible_read_views,
)
from bestseller.services.waiting_chapter_review_resume import _Reader
from bestseller.settings import load_settings

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]
READERS = [_ReadOnlySession, _Reader, None]
AUDITS = []


async def snapshot(session):
    with session.no_autoflush:
        names = list(
            await session.scalars(
                text("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
            )
        )
        return {
            name: sorted(
                [tuple(row) for row in await session.execute(text(f'SELECT * FROM "{name}"'))],
                key=repr,
            )
            for name in names
        }


@contextmanager
def select_only(session, monkeypatch):
    statements = []

    def audit(_conn, _cursor, statement, *_args):
        statements.append(statement)
        upper = statement.upper()
        assert upper.lstrip().startswith("SELECT "), statement
        assert not any(x in upper for x in ("FOR UPDATE", "ADVISORY", "SAVEPOINT"))

    def forbidden(*_args, **_kwargs):
        raise AssertionError("Validation must never flush/refresh/lock/rebuild")

    with monkeypatch.context() as patch:
        for name in ("connection", "begin_nested", "flush", "refresh"):
            patch.setattr(session, name, forbidden)
        patch.setattr(retrieval, "refresh_story_bible_retrieval_index", forbidden)
        patch.setattr(narrative_tree, "rebuild_narrative_tree", forbidden)
        event.listen(session.bind.engine.sync_engine, "before_cursor_execute", audit)
        try:
            yield statements
        finally:
            event.remove(session.bind.engine.sync_engine, "before_cursor_execute", audit)
            AUDITS.append(
                {
                    "select_count": len(statements),
                    "verbs": sorted({sql.lstrip().split()[0].upper() for sql in statements}),
                    "locking_or_savepoint_sql": any(
                        any(
                            token in sql.upper()
                            for token in ("FOR UPDATE", "ADVISORY", "SAVEPOINT")
                        )
                        for sql in statements
                    ),
                }
            )
    assert statements, "The validator must actually read persisted rows"


async def prepare(views):
    session, project, chapter, draft, settings = views
    rule = _rule(project, 1)
    session.add(rule)
    await session.commit()
    receipt = await ensure_story_bible_read_views(session, settings, project=project)
    await session.commit()
    return session, project, chapter, draft, settings, rule, receipt


async def check(session, settings, project, adapter):
    if adapter is None:
        return await validate_story_bible_read_views(session, settings, project=project)
    return await ensure_story_bible_read_views(adapter(session), settings, project=project)


@pytest.mark.parametrize("adapter", READERS)
async def test_current_is_select_only_and_exact_receipt(views, monkeypatch, adapter):
    session, project, _, _, settings, _, receipt = await prepare(views)
    before = await snapshot(session)
    with select_only(session, monkeypatch):
        result = await check(session, settings, project, adapter)
    assert result == {**receipt, "status": "current", "refreshed": False}
    assert await snapshot(session) == before


@pytest.mark.parametrize("adapter", [_ReadOnlySession, _Reader])
async def test_real_retrieval_entry_uses_marker_without_connection(views, monkeypatch, adapter):
    session, project, _, _, settings, _, _ = await prepare(views)
    before = await snapshot(session)
    with select_only(session, monkeypatch):
        assert (
            await retrieval.ensure_project_retrieval_index(adapter(session), settings, project.id)
            == 0
        )
    assert await snapshot(session) == before
    with pytest.raises(ValueError, match="readonly_operation_required"):
        _ = adapter(session).connection


@pytest.mark.parametrize("adapter", READERS)
@pytest.mark.parametrize(
    "corruption",
    [
        "source",
        "project",
        "settings",
        "rag_text",
        "rag_embedding",
        "rag_metadata",
        "tree_body",
        "tree_metadata",
        "partial_rag",
        "partial_tree",
        "missing_root",
        "missing_receipt",
        "partial_receipt",
        "stale_projection",
        "extra_source",
        "world_path",
        "missing_project",
    ],
)
async def test_invalid_fails_closed_without_any_repair(views, monkeypatch, adapter, corruption):
    session, project, _, _, settings, rule, _ = await prepare(views)
    root = await session.scalar(
        select(NarrativeTreeNodeModel).where(NarrativeTreeNodeModel.node_path == "/book")
    )
    node = await session.scalar(
        select(NarrativeTreeNodeModel).where(NarrativeTreeNodeModel.source_ref_id == rule.id)
    )
    chunk = await session.scalar(
        select(RetrievalChunkModel).where(RetrievalChunkModel.source_id == rule.id)
    )
    if corruption == "source":
        rule.description = "Persisted source changed"
    elif corruption == "project":
        project.title = "Persisted project changed"
    elif corruption == "settings":
        settings = settings.model_copy(deep=True)
        settings.retrieval.chunk_size += 1
    elif corruption == "rag_text":
        chunk.chunk_text = "Same ID changed text"
    elif corruption == "rag_embedding":
        chunk.embedding = [0.123] * len(chunk.embedding)
    elif corruption == "rag_metadata":
        chunk.metadata_json = {"changed": True}
    elif corruption == "tree_body":
        node.body_md = "Same ID changed body"
    elif corruption == "tree_metadata":
        node.metadata_json = {**node.metadata_json, "changed": True}
    elif corruption == "partial_rag":
        await session.delete(chunk)
    elif corruption == "partial_tree":
        await session.delete(node)
    elif corruption == "missing_root":
        await session.delete(root)
    elif corruption == "missing_receipt":
        root.metadata_json = {k: v for k, v in root.metadata_json.items() if k != _RECEIPT}
    elif corruption == "partial_receipt":
        root.metadata_json = {**root.metadata_json, _RECEIPT: {"source_sha256": "incomplete"}}
    elif corruption == "stale_projection":
        receipt = deepcopy(root.metadata_json[_RECEIPT])
        receipt["projection_sha256"] = "old-code"
        root.metadata_json = {**root.metadata_json, _RECEIPT: receipt}
    elif corruption == "extra_source":
        session.add(_rule(project, 2))
    elif corruption == "world_path":
        node.node_path = "/world/rules/wrong"
    elif corruption == "missing_project":
        await session.execute(delete(ProjectModel).where(ProjectModel.id == project.id))
    await session.commit()
    before = await snapshot(session)
    with select_only(session, monkeypatch):
        with pytest.raises(ValueError, match="story_bible_read_views_"):
            await check(session, settings, project, adapter)
    assert await snapshot(session) == before


@pytest.mark.parametrize("adapter", READERS)
async def test_dirty_new_deleted_caller_data_preserved(views, monkeypatch, adapter):
    session, project, chapter, draft, settings, rule, receipt = await prepare(views)
    root = await session.scalar(
        select(NarrativeTreeNodeModel).where(NarrativeTreeNodeModel.node_path == "/book")
    )
    chunk = await session.scalar(
        select(RetrievalChunkModel).where(RetrievalChunkModel.source_id == rule.id)
    )
    before = await snapshot(session)
    project.title = "Pending project edit"
    rule.description = "Pending source edit"
    chapter.chapter_number = 17
    draft.content_md = "Pending draft edit"
    root.body_md = "Pending tree edit"
    chunk.chunk_text = "Pending RAG edit"
    session.add(_rule(project, 9))
    await session.delete(rule)
    dirty = tuple(session.dirty)
    histories = {
        id(row): {
            attr.key: inspect(row).attrs[attr.key].history
            for attr in inspect(type(row)).column_attrs
        }
        for row in dirty
    }
    pending = (set(session.new), set(session.deleted), set(session.dirty))
    with select_only(session, monkeypatch):
        result = await check(session, settings, project, adapter)
        assert await snapshot(session) == before
    assert result["source_sha256"] == receipt["source_sha256"]
    assert (set(session.new), set(session.deleted), set(session.dirty)) == pending
    assert {
        id(row): {
            attr.key: inspect(row).attrs[attr.key].history
            for attr in inspect(type(row)).column_attrs
        }
        for row in dirty
    } == histories
    assert project.title == "Pending project edit" and rule.description == "Pending source edit"
    await session.rollback()


@pytest.mark.parametrize("adapter", READERS)
async def test_stale_persisted_source_cannot_be_hidden_by_dirty_identity(
    views, monkeypatch, adapter
):
    session, project, _, _, settings, rule, _ = await prepare(views)
    old = rule.description
    await session.execute(
        text("UPDATE world_rules SET description=:body WHERE id=:id"),
        {"body": "Persisted stale", "id": rule.id.hex},
    )
    await session.commit()
    rule.description = old + " caller change"
    before = await snapshot(session)
    with select_only(session, monkeypatch):
        with pytest.raises(ValueError, match="receipt_mismatch"):
            await check(session, settings, project, adapter)
        assert await snapshot(session) == before
    assert rule.description == old + " caller change" and rule in session.dirty
    await session.rollback()


@pytest.mark.parametrize("adapter", [_ReadOnlySession, _Reader])
async def test_retired_world_lineage_uses_persisted_copies(
    retired, tree_tables, monkeypatch, adapter
):
    book, rule = retired
    session, project, chapter, draft = book
    settings = load_settings(env={})
    # Exercise the native replay dependency that needs persisted get(ProjectModel).
    char = CharacterModel(
        project_id=project.id,
        name="Initial sailor",
        role="protagonist",
        knowledge_state_json={},
        metadata_json={},
    )
    cast = {"protagonist": {"name": char.name}}
    artifact = PlanningArtifactVersionModel(
        project_id=project.id,
        artifact_type="cast_spec",
        version_no=1,
        status="approved",
        schema_version="test",
        content=cast,
    )
    session.add_all([char, artifact])
    await session.flush()
    project.metadata_json = {
        **(project.metadata_json or {}),
        "cast_spec": cast,
        "active_foundation_selection": {
            "schema": "foundation-selection.v1",
            "project_id": str(project.id),
            "artifact_ids": {"cast_spec": str(artifact.id)},
            "artifact_content_hashes": {"cast_spec": content_hash(cast)},
        },
    }
    story_bible_projection.capture_bible_baseline(char)
    await session.commit()
    assert await story_bible._ensure_initial_character_state_snapshot(
        session, project_id=project.id, character=char
    )
    await session.commit()
    await ensure_story_bible_read_views(session, settings, project=project)
    await session.commit()
    before = await snapshot(session)
    # These fields are consumed by the native retirement replay planner. Dirty
    # copies must not be refreshed and must not affect persisted retirement.
    project.metadata_json = {}
    chapter.metadata_json = {}
    draft.content_md = "Pending adopted body edit"
    rule.description = "Pending retired edit"
    with select_only(session, monkeypatch):
        result = await check(session, settings, project, adapter)
        assert await snapshot(session) == before
    assert result["source_counts"]["world_rule"] == 0
    assert project.metadata_json == {}
    assert chapter.metadata_json == {} and draft.content_md == "Pending adopted body edit"
    assert rule.description == "Pending retired edit"
    await session.rollback()


@pytest.mark.parametrize("marker", [None, 1, "true", True])
async def test_only_exact_class_marker_selects_readonly(monkeypatch, marker):
    class NotAnInternalReader:
        async def connection(self):
            raise RuntimeError("normal mutable path selected")

    obj = NotAnInternalReader()
    # Instance marker, including True, cannot opt an ordinary session in.
    obj._bestseller_read_views_validate_only = marker
    with pytest.raises(RuntimeError, match="normal mutable path selected"):
        await ensure_story_bible_read_views(obj, None, project=None)
    if marker is not True:
        NotAnInternalReader._bestseller_read_views_validate_only = marker
        with pytest.raises(RuntimeError, match="normal mutable path selected"):
            await ensure_story_bible_read_views(obj, None, project=None)
