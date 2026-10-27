"""Real isolated SQLite writes/reads for revision history, not production QA."""

from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateTable

from bestseller.infra.db.models import (
    ChapterDraftVersionModel,
    ChapterModel,
    ChapterQualityReportModel,
    ReviewReportModel,
    RewriteTaskModel,
)
from bestseller.services import reviews
from bestseller.services.quality_report_claim import graded_text_fingerprint
from bestseller.services.rewrite_patch import text_hash

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@compiles(JSONB, "sqlite")
def _sqlite_jsonb(_type, _compiler, **_kwargs):
    return "JSON"


@pytest.fixture
async def db(tmp_path):
    path = tmp_path / "revision_binding_test.sqlite3"
    assert "test" in path.name
    # Use installed stdlib sqlite with an async call facade. The runtime SQL
    # and ORM mapping execute normally; no optional aiosqlite dependency.
    engine = create_engine(f"sqlite:///{path}")
    with engine.begin() as connection:
        for model in (
            ChapterModel,
            ChapterDraftVersionModel,
            RewriteTaskModel,
            ReviewReportModel,
            ChapterQualityReportModel,
        ):
            # Only table DDL: PostgreSQL partial indexes are not valid SQLite
            # index definitions. No production engine or migrations are used.
            connection.execute(CreateTable(model.__table__))
        connection.exec_driver_sql(
            "CREATE UNIQUE INDEX revision_test_current ON chapter_draft_versions(chapter_id) WHERE is_current = 1"
        )
    yield lambda: AsyncFacade(Session(engine, expire_on_commit=False))
    engine.dispose()


class AsyncFacade:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        self.session.close()

    def add(self, value):
        self.session.add(value)

    async def get(self, model, key):
        return self.session.get(model, key)

    async def scalar(self, stmt):
        return self.session.scalar(stmt)

    async def scalars(self, stmt):
        return self.session.scalars(stmt)

    async def execute(self, stmt):
        return self.session.execute(stmt)

    async def flush(self):
        self.session.flush()

    async def commit(self):
        self.session.commit()


async def seed(session):
    chapter = ChapterModel(
        id=uuid4(), project_id=uuid4(), chapter_number=1, chapter_goal="Choose a route"
    )
    session.add(chapter)
    draft = ChapterDraftVersionModel(
        id=uuid4(),
        project_id=chapter.project_id,
        chapter_id=chapter.id,
        version_no=1,
        content_md="A latch held.\n\nShe chose the stairs.",
        word_count=8,
        is_current=True,
    )
    session.add(draft)
    task = RewriteTaskModel(
        id=uuid4(),
        project_id=chapter.project_id,
        trigger_type="chapter_review",
        trigger_source_id=chapter.id,
        rewrite_strategy="chapter_coherence_bridge_rewrite",
        instructions="Clarify the choice.",
        status="pending",
        attempts=1,
        metadata_json={
            "draft_id": str(draft.id),
            "parent_draft_id": str(draft.id),
            "parent_draft_hash": text_hash(draft.content_md),
        },
    )
    session.add(task)
    await session.flush()
    return chapter, draft, task


async def test_stale_task_persists_evidence_and_old_parent_after_readback(db):
    async with db() as session:
        chapter, old, task = await seed(session)
        original_binding = dict(task.metadata_json)
        old.is_current = False
        await session.flush()
        new = ChapterDraftVersionModel(
            id=uuid4(),
            project_id=chapter.project_id,
            chapter_id=chapter.id,
            version_no=2,
            content_md="A different route.",
            word_count=3,
            is_current=True,
        )
        session.add(new)
        await session.flush()
        with pytest.raises(ValueError, match="source binding"):
            await reviews._validate_rewrite_task_parent(session, task, new)
        await session.commit()
    async with db() as session:
        stored = await session.get(RewriteTaskModel, task.id)
        assert stored.status == "stale"
        assert stored.attempts == 1
        assert stored.metadata_json["parent_draft_hash"] == original_binding["parent_draft_hash"]
        assert (await session.get(ChapterDraftVersionModel, old.id)).content_md == old.content_md
        assert (await session.get(ChapterDraftVersionModel, new.id)).is_current is True


async def test_legacy_report_migration_requires_exact_body_and_is_idempotent(db):
    async with db() as session:
        chapter, draft, task = await seed(session)
        report = ReviewReportModel(
            id=uuid4(),
            project_id=chapter.project_id,
            target_type="chapter",
            target_id=chapter.id,
            reviewer_type="fixture",
            verdict="rewrite",
            severity_max="major",
            structured_output={
                "draft_id": str(draft.id),
                "reviewed_body_fingerprint": reviews.chapter_review_body_fingerprint(
                    draft.content_md
                ),
            },
        )
        session.add(report)
        task.metadata_json = {"draft_id": str(draft.id), "review_report_id": str(report.id)}
        await session.flush()
        await reviews._validate_rewrite_task_parent(session, task, draft)
        migrated = dict(task.metadata_json)
        await reviews._validate_rewrite_task_parent(session, task, draft)
        assert task.metadata_json == migrated
        await session.commit()
    async with db() as session:
        stored = await session.get(RewriteTaskModel, task.id)
        assert stored.metadata_json["parent_draft_hash"] == text_hash(draft.content_md)
        assert stored.metadata_json["source_binding_migrated_from_report_id"] == str(report.id)
        assert stored.status == "pending" and stored.attempts == 1


async def test_gate_receipt_selects_its_call_even_when_newer_report_has_same_body(db, monkeypatch):
    async with db() as session:
        chapter, draft, task = await seed(session)
        ids = []

        async def gate(**kwargs):
            own = ChapterQualityReportModel(
                id=uuid4(),
                chapter_id=chapter.id,
                blocks_write=True,
                report_json={
                    "graded": graded_text_fingerprint(draft.content_md),
                    **kwargs["extra_report_payload"],
                    "violations": [{"code": "THIS_CALL"}],
                },
            )
            session.add(own)
            await session.flush()
            ids.append(own.id)
            # Another call writes later, even against identical body content.
            session.add(
                ChapterQualityReportModel(
                    id=uuid4(),
                    chapter_id=chapter.id,
                    blocks_write=True,
                    report_json={
                        "graded": graded_text_fingerprint(draft.content_md),
                        "rewrite_evaluation_id": str(uuid4()),
                        "violations": [{"code": "OTHER_CALL"}],
                    },
                )
            )
            await session.flush()
            return "blocked"

        monkeypatch.setattr(reviews, "_evaluate_chapter_quality_gate", gate)
        receipt = await reviews._evaluate_rewrite_quality_gate(
            session=session,
            project=chapter.__class__(id=chapter.project_id),
            chapter_number=1,
            content=draft.content_md,
        )
        assert receipt.report.id == ids[0]
        assert receipt.violations == ({"code": "THIS_CALL"},)
        await session.commit()


async def test_parent_recheck_uses_database_columns_instead_of_stale_identity(db):
    async with db() as session:
        chapter, draft, task = await seed(session)
        await reviews._validate_rewrite_task_parent(session, task, draft)
        await reviews._recheck_rewrite_parent(session, task, draft, parent=chapter)
        from sqlalchemy import update

        await session.execute(
            update(ChapterDraftVersionModel)
            .where(ChapterDraftVersionModel.id == draft.id)
            .values(content_md="Changed outside ORM identity map")
            .execution_options(synchronize_session=False)
        )
        assert draft.content_md != "Changed outside ORM identity map"
        with pytest.raises(ValueError, match="before commit"):
            await reviews._recheck_rewrite_parent(
                session, task, draft, parent=chapter, candidate_text="Candidate against old body")
        await session.commit()
    async with db() as session:
        stored = await session.get(RewriteTaskModel, task.id)
        assert stored.status == "stale" and stored.attempts == 1
        assert (
            stored.metadata_json["source_binding_rejection"]["reason"]
            == "parent_changed_before_commit"
        )
        assert stored.metadata_json["unpromoted_stale_candidate"]["content_md"] == "Candidate against old body"
        rows = list(await session.scalars(select(ChapterDraftVersionModel)))
        assert len(rows) == 1  # No wrong-parent revision was inserted or promoted.
