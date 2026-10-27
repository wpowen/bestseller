"""Real isolated SQLite backfill preserves full/prefix source-owned planning."""

from copy import deepcopy
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.schema import CreateTable
from test_adopted_knowledge_lineage import _jsonb, _uuid_as_text  # noqa: F401

from bestseller.infra.db.models import ChapterDraftVersionModel, ChapterModel, ProjectModel
from bestseller.services import legacy_book_state_bootstrap as service
from bestseller.services.chapter_outline_readiness import outline_prefix_projection_hash

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]
FIELDS = ("hype_type", "hype_intensity", "hype_recipe_key")
MARKERS = ("outline_source_admission", "outline_prefix_readiness")


@pytest.fixture
async def store(tmp_path):
    path = tmp_path / "source_owned_hype_test.sqlite3"
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    assert engine.url.drivername == "sqlite+aiosqlite" and "test" in engine.url.database
    try:
        async with engine.begin() as connection:
            database = (await connection.execute(text("PRAGMA database_list"))).all()
            assert Path(database[0][2]).resolve() == path.resolve()
            for model in (ProjectModel, ChapterModel, ChapterDraftVersionModel):
                ddl = str(CreateTable(model.__table__).compile(dialect=engine.dialect))
                await connection.exec_driver_sql(ddl.replace("::jsonb", ""))
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            yield session
    finally:
        await engine.dispose()


async def add_project(session, title):
    project = ProjectModel(
        slug="source-hype-test-" + uuid4().hex, title=title, genre="test",
        language="zh-CN", target_chapters=500, target_word_count=100000,
    )
    session.add(project)
    await session.flush()
    return project


async def add_chapter(session, project, number, *, metadata=None, planned=False):
    chapter = ChapterModel(
        project_id=project.id, chapter_number=number, chapter_goal="Deliver the sealed letter",
        hype_type="status_jump" if planned else None,
        hype_intensity=0.2 if planned else None,
        hype_recipe_key="author-recipe" if planned else None,
        metadata_json=deepcopy(metadata or {}),
    )
    session.add(chapter)
    await session.flush()
    session.add(ChapterDraftVersionModel(
        project_id=project.id, chapter_id=chapter.id, version_no=1,
        content_md="The courier put the sealed letter on the table.",
        is_current=True, promotion_state="candidate", promotion_metadata={},
    ))
    return chapter


def row_state(chapter):
    return deepcopy({
        attr.key: getattr(chapter, attr.key) for attr in inspect(type(chapter)).column_attrs
    })


@pytest.mark.parametrize("title", ["The harbour ledger", "Letters from the mountain"])
async def test_source_owned_rows_skip_backfill_and_normalization_after_sqlite_readback(
    store, monkeypatch, title
):
    project = await add_project(store, title)
    rows = []
    for marker_key in MARKERS:
        marker_values = ({"projection_hash": "retained-old-marker"}, None, {}, [], False, "broken")
        for marker_value in marker_values:
            for planned in (False, True):
                rows.append(await add_chapter(
                    store, project, len(rows) + 1,
                    metadata={marker_key: marker_value, "retained_history": ["exact"]},
                    planned=planned,
                ))
    await store.commit()
    old = {row.id: row_state(row) for row in rows}
    hashes = {row.id: outline_prefix_projection_hash(row, []) for row in rows}

    def forbidden(*_args, **_kwargs):
        raise AssertionError("Source-owned plans must not enter legacy inference/normalization")

    monkeypatch.setattr(service, "_legacy_hype_assignment", forbidden)
    monkeypatch.setattr(service, "_normalized_legacy_hype_intensity", forbidden)
    for dry_run in (True, False, False):
        result = await service.backfill_legacy_hype_assignments(
            store, project, category_key="suspense-mystery", dry_run=dry_run
        )
        assert result == {
            "hype_backfilled": 0, "hype_intensity_normalized": 0,
            "hype_source_owned_skipped": len(rows),
        }
        assert not store.dirty
        await store.commit()
        for row in rows:
            await store.refresh(row)
            assert row_state(row) == old[row.id]
            assert outline_prefix_projection_hash(row, []) == hashes[row.id]


async def test_mixed_project_retains_legacy_backfill_dry_run_scope_and_idempotence(store):
    project = await add_project(store, "Unbound legacy")
    other = await add_project(store, "Unrelated project")
    protected = await add_chapter(
        store, project, 1, metadata=dict.fromkeys(MARKERS), planned=True
    )
    missing = await add_chapter(store, project, 2, metadata={"keep": "legacy"})
    normalized = await add_chapter(store, project, 3, planned=True)
    untouched_other = await add_chapter(store, other, 1)
    await store.commit()
    before = {row.id: row_state(row) for row in (protected, missing, normalized, untouched_other)}
    expected = service._legacy_hype_assignment(
        text="The courier put the sealed letter on the table.", language="zh-CN",
        category_key="suspense-mystery", chapter_number=2, target_chapters=500,
    )
    dry = await service.backfill_legacy_hype_assignments(
        store, project, category_key="suspense-mystery", dry_run=True
    )
    assert dry == {
        "hype_backfilled": 1, "hype_intensity_normalized": 1, "hype_source_owned_skipped": 1,
    }
    assert not store.dirty
    for row in (protected, missing, normalized, untouched_other):
        assert row_state(row) == before[row.id]
    actual = await service.backfill_legacy_hype_assignments(
        store, project, category_key="suspense-mystery"
    )
    assert actual == dry
    await store.commit()
    for row in (protected, missing, normalized, untouched_other):
        await store.refresh(row)
    assert row_state(protected) == before[protected.id]
    assert row_state(untouched_other) == before[untouched_other.id]
    assert missing.hype_type == expected[0] and missing.hype_intensity == expected[1]
    assert missing.metadata_json["keep"] == "legacy"
    assert missing.metadata_json["legacy_hype_backfill"]["hype_type_source"] == expected[2]
    assert normalized.hype_intensity == service._normalized_legacy_hype_intensity(
        0.2, chapter_number=3, target_chapters=500
    )
    assert normalized.hype_type == "status_jump" and normalized.hype_recipe_key == "author-recipe"
    repeat = await service.backfill_legacy_hype_assignments(
        store, project, category_key="suspense-mystery"
    )
    assert repeat == {
        "hype_backfilled": 0, "hype_intensity_normalized": 0, "hype_source_owned_skipped": 1,
    }
    # With no source-owned rows the previous return shape and behavior stay exact.
    assert await service.backfill_legacy_hype_assignments(
        store, other, category_key="suspense-mystery"
    ) == {"hype_backfilled": 1, "hype_intensity_normalized": 0}
