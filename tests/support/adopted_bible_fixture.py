"""Exercise real empty Bible application in SQLite fixtures, stubbing transport only."""
from sqlalchemy.schema import CreateTable
from bestseller.infra.db.models import (
    RelationshipModel, WorldRuleModel, CharacterStateSnapshotModel, PlanningArtifactVersionModel,
)
from bestseller.services import llm, story_bible


async def create_bible_fixture_tables(session):
    connection = await session.connection()
    if connection.dialect.name == "sqlite":
        for model in (RelationshipModel, WorldRuleModel, CharacterStateSnapshotModel, PlanningArtifactVersionModel):
            ddl = str(CreateTable(model.__table__).compile(dialect=connection.dialect))
            await connection.exec_driver_sql(ddl.replace("::jsonb", "").replace("CREATE TABLE ", "CREATE TABLE IF NOT EXISTS ", 1))


async def seed_empty_bible(configured, monkeypatch):
    from test_adopted_knowledge_lineage import stub_llm
    from story_bible_read_views_test_support import create_story_bible_read_view_tables
    session, settings, project, chapter, draft = configured
    # Reuse the existing native SQLite projection fixture. Its advisory UDFs
    # permit SQL execution only; PostgreSQL concurrency is verified separately.
    await create_story_bible_read_view_tables(session)
    await create_bible_fixture_tables(session)
    with monkeypatch.context() as patch:
        calls = stub_llm(patch, llm, {})
        outcome = await story_bible.update_story_bible_from_chapter(
            session, settings, project=project, chapter=chapter, chapter_text=draft.content_md,
            source_chapter_draft_version_id=draft.id,
        )
    assert outcome["extraction_status"] in {"empty", "ok", "already_applied"}, outcome
    await session.commit()
    return calls
