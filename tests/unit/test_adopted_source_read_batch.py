"""R12: real source reads, concurrent commits and bounded SQL on isolated PG.

No model, grade, production rows, migrations or schema changes. Synthetic
project/chapter/draft fixtures are committed only in the verified test DB so a
second connection can exercise the final READ COMMITTED fence.
"""
from __future__ import annotations

import asyncio
import hashlib
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import delete, event, select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SAWarning
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bestseller.infra.db.models import ChapterDraftVersionModel, ChapterModel, ProjectModel
from bestseller.services import feedback, llm
from bestseller.services.adopted_knowledge_resume import read_adopted_knowledge

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.fixture
async def case(postgresql_test_settings, monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("R12 source-read fixtures forbid model calls")

    monkeypatch.setattr(llm, "complete_text", denied)
    monkeypatch.setattr(feedback, "complete_text", denied)
    url = make_url(postgresql_test_settings.database.url)
    assert url.database == "bestseller_repair_test_20261015"
    engine = create_async_engine(url, hide_parameters=True, echo=False)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    pid, cid, did = uuid4(), uuid4(), uuid4()
    body = "离线数据库边界样本：阿青把钥匙放在桌上。"
    try:
        async with factory() as session:
            assert await session.scalar(text("SELECT current_database()")) == url.database
            project = ProjectModel(id=pid, slug="r12-source-test-" + pid.hex, title="R12 fixture",
                                   genre="test", target_word_count=10000, target_chapters=1)
            session.add(project)
            await session.flush()
            chapter = ChapterModel(id=cid, project_id=pid, chapter_number=1,
                                   chapter_goal="test", metadata_json={})
            session.add(chapter)
            await session.flush()
            draft = ChapterDraftVersionModel(id=did, project_id=pid, chapter_id=cid,
                version_no=1, content_md=body, promotion_state="promoted", is_current=True,
                promotion_metadata={})
            session.add(draft)
            await session.commit()
            yield SimpleNamespace(session=session, engine=engine, factory=factory,
                project=project, chapter=chapter, draft=draft, pid=pid, cid=cid, did=did, body=body)
            await session.rollback()
    finally:
        # Explicit IDs only; no prefix deletion and no fixture survives a test.
        async with engine.begin() as connection:
            await connection.execute(delete(ChapterDraftVersionModel).where(ChapterDraftVersionModel.project_id == pid))
            await connection.execute(delete(ChapterModel).where(ChapterModel.project_id == pid))
            await connection.execute(delete(ProjectModel).where(ProjectModel.id == pid))
            assert not await connection.scalar(select(ProjectModel.id).where(ProjectModel.id == pid))
        await engine.dispose()


async def source(case, **overrides):
    fields = dict(project_id=case.pid, chapter_id=case.cid,
        source_chapter_draft_version_id=case.did, chapter_md=case.body, lock=False)
    fields.update(overrides)
    return await feedback.validate_adopted_knowledge_source(case.session, **fields)


async def concurrent_update(case, **values):
    async with case.engine.begin() as connection:
        await connection.execute(update(ChapterDraftVersionModel).where(
            ChapterDraftVersionModel.id == case.did).values(**values))


async def test_repeated_source_sql_is_bounded_and_caller_lineage_is_detached(case, record_property):
    calls = []

    def count(conn, cursor, statement, parameters, context, many):
        if statement.startswith("SELECT"):
            calls.append(statement)

    event.listen(case.engine.sync_engine, "before_cursor_execute", count)
    try:
        for _ in range(20):
            expected, rejection = await source(case)
            assert rejection is None
        baseline = len(calls)
        calls.clear()
        async with feedback.adopted_source_read_batch(case.session, case.pid):
            for _ in range(20):
                actual, rejection = await source(case)
                assert rejection is None and actual == expected
                actual["source_body_sha256"] = "caller must not poison internal witness"
        optimized = len(calls)
        # One real initial native validation and one real final native fence;
        # each executes the existing chapter/draft/selected-promotion reads.
        assert baseline == 60 and optimized == 6
        record_property("native_selects_before", baseline)
        record_property("native_selects_after_including_final_fence", optimized)
        assert not case.session.new and not case.session.dirty and not case.session.deleted
    finally:
        event.remove(case.engine.sync_engine, "before_cursor_execute", count)


async def test_each_distinct_valid_source_gets_its_own_initial_and_final_native_reads(case):
    async with case.factory() as writer:
        chapter = ChapterModel(project_id=case.pid, chapter_number=2,
                               chapter_goal="second source", metadata_json={})
        writer.add(chapter)
        await writer.flush()
        draft = ChapterDraftVersionModel(project_id=case.pid, chapter_id=chapter.id,
            version_no=1, content_md=case.body + "第二章", promotion_state="promoted",
            is_current=True, promotion_metadata={})
        writer.add(draft)
        await writer.commit()
        second = dict(chapter_id=chapter.id, source_chapter_draft_version_id=draft.id,
                      chapter_md=draft.content_md)
    calls = []

    def count(conn, cursor, statement, parameters, context, many):
        if statement.startswith("SELECT"):
            calls.append(True)

    event.listen(case.engine.sync_engine, "before_cursor_execute", count)
    try:
        async with feedback.adopted_source_read_batch(case.session, case.pid):
            for _ in range(10):
                one, reject = await source(case)
                two, reject_two = await source(case, **second)
                assert reject is None and reject_two is None
                assert one["chapter_id"] != two["chapter_id"]
        assert len(calls) == 12
    finally:
        event.remove(case.engine.sync_engine, "before_cursor_execute", count)


@pytest.mark.parametrize("invalid", ["body", "draft", "chapter", "project", "missing"])
async def test_invalid_source_never_reuses_valid_witness(case, invalid):
    overrides = {"body": {"chapter_md": case.body + "改"},
        "draft": {"source_chapter_draft_version_id": uuid4()},
        "chapter": {"chapter_id": uuid4()}, "project": {"project_id": uuid4()},
        "missing": {"source_chapter_draft_version_id": None}}[invalid]
    async with feedback.adopted_source_read_batch(case.session, case.pid):
        assert (await source(case))[1] is None
        for _ in range(2):
            if invalid == "project":
                with pytest.raises(ValueError, match="context_changed"):
                    await source(case, **overrides)
            else:
                assert (await source(case, **overrides))[1] is not None


async def test_rejection_is_not_cached(case):
    async with feedback.adopted_source_read_batch(case.session, case.pid):
        assert (await source(case, chapter_md=case.body + "替换"))[1] == "source_body_mismatch"
        await concurrent_update(case, content_md=case.body + "替换")
        assert (await source(case, chapter_md=case.body + "替换"))[1] is None


async def test_changed_expected_lineage_still_rejected_on_cache_hit(case):
    async with feedback.adopted_source_read_batch(case.session, case.pid):
        lineage, _ = await source(case)
        for field in feedback.KNOWLEDGE_SOURCE_KEYS:
            bad = {**lineage, field: "wrong"}
            with pytest.raises(ValueError, match="adopted_knowledge_source_changed"):
                await read_adopted_knowledge(case.session, project_id=case.pid,
                    chapter_id=case.cid, source_chapter_draft_version_id=case.did,
                    chapter_md=case.body, expected_source=bad)


@pytest.mark.parametrize("change", ["body", "promotion", "version", "ownership", "replacement", "continuity"])
async def test_concurrent_native_source_change_is_caught_at_exit(case, change):
    with pytest.raises(ValueError, match="adopted_source_batch_source_changed"):
        async with feedback.adopted_source_read_batch(case.session, case.pid):
            before, rejection = await source(case)
            assert rejection is None
            if change == "replacement":
                async with case.factory() as writer:
                    await writer.execute(update(ChapterDraftVersionModel).where(
                        ChapterDraftVersionModel.id == case.did).values(
                            is_current=False, promotion_state="superseded"))
                    writer.add(ChapterDraftVersionModel(project_id=case.pid, chapter_id=case.cid,
                        version_no=2, content_md=case.body + "新稿", promotion_state="promoted",
                        is_current=True, promotion_metadata={}))
                    await writer.commit()
            elif change == "continuity":
                async with case.engine.begin() as writer:
                    await writer.execute(update(ChapterModel).where(ChapterModel.id == case.cid).values(
                        metadata_json={"continuity_admission": {"draft_id": str(case.did),
                            "body_sha256": hashlib.sha256(case.body.encode()).hexdigest(),
                            "passed": False}}))
            elif change == "ownership":
                async with case.factory() as writer:
                    other_chapter = ChapterModel(project_id=case.pid, chapter_number=2,
                                                 chapter_goal="foreign source", metadata_json={})
                    writer.add(other_chapter)
                    await writer.flush()
                    await writer.execute(update(ChapterDraftVersionModel).where(
                        ChapterDraftVersionModel.id == case.did).values(chapter_id=other_chapter.id))
                    await writer.commit()
            else:
                fields = {"body": {"content_md": case.body + "新稿"},
                    "promotion": {"promotion_state": "quarantined"},
                    "version": {"version_no": 2}}[change]
                await concurrent_update(case, **fields)
            # Reuse is provisional until the mandatory outer fence succeeds.
            assert (await source(case))[0] == before


@pytest.mark.parametrize("change", ["tracked_body", "nested_json", "expire", "detach"])
async def test_caller_source_mutation_cannot_be_hidden_by_fresh_refresh(case, change):
    with pytest.raises(ValueError, match="adopted_source_batch_(source_changed|pending_state)"):
        async with feedback.adopted_source_read_batch(case.session, case.pid):
            await source(case)
            if change == "tracked_body":
                case.draft.content_md += "本地未写"
            elif change == "nested_json":
                # Plain JSON is not necessarily tracked by SQLAlchemy.
                case.chapter.metadata_json["local_untracked"] = {"value": True}
            elif change == "expire":
                case.session.expire(case.draft)
            else:
                case.session.expunge(case.draft)
            await source(case)


async def test_pending_source_is_rejected_before_read_and_not_discarded(case):
    case.draft.content_md = case.body + "待写"
    with pytest.raises(ValueError, match="pending_state"):
        async with feedback.adopted_source_read_batch(case.session, case.pid):
            pytest.fail("must reject before entering")
    assert case.draft.content_md == case.body + "待写" and case.draft in case.session.dirty


@pytest.mark.parametrize("operation", ["commit", "rollback", "connection_commit", "connection_rollback",
    "flush", "empty_flush", "savepoint", "sql", "locked_source"])
async def test_write_or_transaction_boundary_invalidates_batch(case, operation):
    with pytest.raises(ValueError, match="adopted_source_batch_"):
        async with feedback.adopted_source_read_batch(case.session, case.pid):
            await source(case)
            if operation == "commit":
                await case.session.commit()
            elif operation == "rollback":
                await case.session.rollback()
            elif operation == "connection_commit":
                await (await case.session.connection()).commit()
            elif operation == "connection_rollback":
                await (await case.session.connection()).rollback()
            elif operation == "flush":
                case.draft.content_md += "不能刷写"
                await case.session.flush()
            elif operation == "empty_flush":
                await case.session.flush()
            elif operation == "savepoint":
                await case.session.begin_nested()
            elif operation == "sql":
                await case.session.execute(update(ChapterDraftVersionModel).where(
                    ChapterDraftVersionModel.id == case.did).values(content_md="不能写"))
            else:
                await source(case, lock=True)
    if operation == "connection_rollback":
        # Direct Connection rollback deliberately deassociates Session's
        # transaction; acknowledge that native SQLAlchemy cleanup warning.
        with pytest.warns(SAWarning, match="transaction already deassociated from connection"):
            await case.session.rollback()
    else:
        await case.session.rollback()
    async with case.factory() as reader:
        assert await reader.scalar(select(ChapterDraftVersionModel.content_md).where(
            ChapterDraftVersionModel.id == case.did)) == case.body


async def test_context_does_not_cross_tasks_sessions_or_requests(case):
    async with feedback.adopted_source_read_batch(case.session, case.pid):
        await source(case)
        with pytest.raises(ValueError, match="context_changed"):
            await asyncio.create_task(source(case))
        async with case.factory() as other:
            result, rejection = await feedback.validate_adopted_knowledge_source(other,
                project_id=case.pid, chapter_id=case.cid,
                source_chapter_draft_version_id=case.did, chapter_md=case.body, lock=False)
            assert rejection is None and result["source_body_sha256"]
    await concurrent_update(case, content_md=case.body + "后续请求")
    async with feedback.adopted_source_read_batch(case.session, case.pid):
        assert (await source(case))[1] == "source_body_mismatch"


async def test_error_cleanup_removes_batch_and_listeners(case):
    with pytest.raises(RuntimeError, match="diagnostic"):
        async with feedback.adopted_source_read_batch(case.session, case.pid):
            await source(case)
            raise RuntimeError("diagnostic")
    assert feedback._SOURCE_READ_BATCH.get() is None
    # The caller can perform legitimate writes after leaving the read boundary.
    case.draft.content_md += "新请求"
    await case.session.flush()
    await case.session.rollback()


async def test_caught_commit_failure_cannot_resume_reusing_witnesses(case):
    with pytest.raises(ValueError, match="context_changed"):
        async with feedback.adopted_source_read_batch(case.session, case.pid):
            await source(case)
            with pytest.raises(ValueError, match="write_forbidden"):
                await case.session.commit()
            await source(case)


async def test_repeatable_read_cannot_claim_final_concurrency_fence(case):
    await case.session.rollback()
    await case.session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
    with pytest.raises(ValueError, match="requires_read_committed"):
        async with feedback.adopted_source_read_batch(case.session, case.pid):
            pytest.fail("snapshot isolation cannot supply a fresh concurrent fence")
