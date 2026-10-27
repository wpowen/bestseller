"""Real isolated SQLite knowledge + normal exporter + UTF-8 readback, no models."""

from copy import deepcopy
import hashlib
from pathlib import Path
import socket
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.schema import CreateTable
from test_adopted_knowledge_lineage import db as db
from test_adopted_knowledge_lineage import make_accepted_debt, stub_llm

from bestseller.infra.db.models import (
    ChapterContractModel,
    ChapterDraftVersionModel,
    ChapterModel,
    ChapterStateSnapshotModel,
    DeferredRevealModel,
    DraftPromotionDecisionModel,
    ExpansionGateModel,
    ExportArtifactModel,
    FactionModel,
    LocationModel,
    NarrativeTreeNodeModel,
    PayoffModel,
    RetrievalChunkModel,
    RewriteTaskModel,
    SceneCardModel,
    SceneContractModel,
    VolumeFrontierModel,
    VolumeModel,
    WorldBackboneModel,
)
from bestseller.services import book_closure, continuity, exports, feedback, llm, pipelines
from bestseller.services.book_delivery_readiness import (
    read_book_delivery_closure,
    read_book_delivery_readiness,
)
from bestseller.settings import load_settings
from support.adopted_bible_fixture import seed_empty_bible

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.delenv("BESTSELLER_LINEAGE_PG", raising=False)

    def forbidden(*args, **kwargs):
        raise AssertionError("closure test must not call models/network")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    for module in (llm, feedback, continuity):
        monkeypatch.setattr(module, "complete_text", forbidden)


@pytest.fixture
async def book(db, tmp_path):
    session, project, chapter, draft = db
    connection = await session.connection()
    assert connection.dialect.name == "sqlite"
    assert "test" in str(connection.engine.url.database)
    raw = await connection.get_raw_connection()
    # Let native Bible/view SQL execute on this isolated connection. These
    # no-ops do not emulate PostgreSQL locks or establish concurrency safety.
    await raw.driver_connection.create_function("hashtextextended", 2, lambda *_: 0)
    await raw.driver_connection.create_function("pg_advisory_xact_lock", 1, lambda *_: None)
    # Keep the shared adopted-body/Bible fixtures and real view rebuild. The
    # rebuild reads these planning tables even for an empty Bible payload.
    for model in (
        DraftPromotionDecisionModel,
        RewriteTaskModel,
        ExportArtifactModel,
        RetrievalChunkModel,
        NarrativeTreeNodeModel,
        VolumeModel,
        SceneCardModel,
        WorldBackboneModel,
        VolumeFrontierModel,
        DeferredRevealModel,
        ExpansionGateModel,
        LocationModel,
        FactionModel,
        PayoffModel,
        ChapterContractModel,
        SceneContractModel,
    ):
        ddl = str(CreateTable(model.__table__).compile(dialect=connection.dialect))
        await connection.exec_driver_sql(ddl.replace("::jsonb", ""))
    project.status = "writing"
    project.target_chapters = 1
    project.metadata_json = {"execution_scope_id": "unchanged-scope", "spent_tokens": 123}
    chapter.status = "complete"
    chapter.production_state = "ok"
    draft.assembled_from_scene_draft_ids = [str(uuid4())]
    settings = load_settings().model_copy(deep=True)
    settings.output.base_dir = tmp_path / "output"
    settings.pipeline.enable_chapter_feedback = True
    await session.commit()
    return session, settings, project, chapter, draft


async def seed_knowledge(book, monkeypatch, *, feedback_done=True, snapshot_done=True):
    session, settings, project, chapter, draft = book
    # The actual extraction/application writes are exercised; transport alone is
    # a local fixture. Restore the prohibitions before closure or dry-run reads.
    with monkeypatch.context() as patch:
        if feedback_done:
            stub_llm(patch, feedback, {})
            await feedback.extract_chapter_feedback(
                session,
                settings,
                project_id=project.id,
                chapter=chapter,
                chapter_md=draft.content_md,
                source_chapter_draft_version_id=draft.id,
            )
        if snapshot_done:
            stub_llm(patch, continuity, {"facts": []})
            await continuity.extract_chapter_state_snapshot(
                session,
                settings,
                project_id=project.id,
                chapter=chapter,
                chapter_md=draft.content_md,
                source_chapter_draft_version_id=draft.id,
            )
    await session.commit()
    await seed_empty_bible(book, monkeypatch)


async def settle(book, *, fallback="writing"):
    session, settings, project, _, _ = book
    result = await book_closure.settle_project_status_on_closure(
        session,
        project,
        settings=settings,
        fallback_status=fallback,
        now_iso="2026-10-22T12:00:00Z",
    )
    await session.commit()
    return result


async def task(book):
    session, _, project, _, _ = book
    row = RewriteTaskModel(
        project_id=project.id,
        trigger_type="chapter_review",
        rewrite_strategy="targeted",
        instructions="Keep unresolved work",
        status="pending",
        context_required=[],
        metadata_json={"history": "preserved"},
    )
    session.add(row)
    await session.commit()
    return row


async def test_normal_export_and_both_utf8_files_are_required_for_completion(book, monkeypatch):
    await seed_knowledge(book, monkeypatch)
    result = await settle(book)
    session, _, project, _, draft = book
    assert result.is_complete, project.metadata_json
    assert result.body_complete and result.is_clean
    meta = project.metadata_json
    artifact = await session.scalar(select(ExportArtifactModel))
    for path in (artifact.storage_uri, meta["completion_export_path"]):
        data = Path(path).read_bytes()
        assert (
            hashlib.sha256(data).hexdigest()
            == artifact.checksum
            == meta["completion_export_checksum"]
        )
        assert draft.content_md in data.decode("utf-8")
    readback = await read_book_delivery_closure(session, project)
    assert readback.verdict.is_complete, readback.blockers
    assert meta["spent_tokens"] == 123 and meta["execution_scope_id"] == "unchanged-scope"


async def test_export_failure_then_recovery_preserves_body_and_pending_rewrite(book, monkeypatch):
    await seed_knowledge(book, monkeypatch)
    row = await task(book)
    session, _, project, chapter, draft = book
    original_export = exports.export_project_markdown
    body, knowledge = draft.content_md, deepcopy(chapter.metadata_json)

    async def fail(*args, **kwargs):
        raise OSError("disk unavailable")

    monkeypatch.setattr(exports, "export_project_markdown", fail)
    result = await settle(book, fallback="completed")
    assert not result.is_complete and result.body_complete
    assert project.status == "revising"
    assert project.metadata_json["book_delivery"]["state"] == "export_pending"
    assert "completed_at" not in project.metadata_json
    await session.refresh(row)
    assert row.status == "pending"
    assert draft.content_md == body and chapter.metadata_json == knowledge
    monkeypatch.setattr(exports, "export_project_markdown", original_export)
    result = await settle(book)
    assert result.is_complete, project.metadata_json
    await session.refresh(row)
    assert row.status == "cancelled"
    assert draft.content_md == body
    assert "completion_export_error" not in project.metadata_json
    assert len(list(await session.scalars(select(ExportArtifactModel)))) == 1


async def test_valid_numeric_debt_exports_without_promotion(book, monkeypatch):
    session, _, project, chapter, draft = book
    await make_accepted_debt((session, project, chapter, draft))
    receipt = deepcopy(chapter.metadata_json["numeric_promotion_debt"])
    await seed_knowledge(book, monkeypatch)
    result = await settle(book)
    assert result.is_complete and not result.is_clean, project.metadata_json
    assert result.debt_chapters == (1,)
    assert draft.promotion_state == "under_review"
    assert chapter.metadata_json["numeric_promotion_debt"] == receipt
    artifact = await session.scalar(select(ExportArtifactModel))
    source = artifact.metadata_json["source_drafts"][0]
    assert source["adoption_state"] == "accepted_with_debt"
    assert source["adoption_receipt"] == receipt
    assert not list(await session.scalars(select(DraftPromotionDecisionModel)))


@pytest.mark.parametrize("missing", ["feedback", "snapshot", "bible", "wrong_body"])
async def test_missing_or_wrong_body_knowledge_does_not_complete(book, monkeypatch, missing):
    await seed_knowledge(
        book, monkeypatch, feedback_done=missing != "feedback", snapshot_done=missing != "snapshot"
    )
    session, _, project, chapter, draft = book
    if missing == "bible":
        chapter.metadata_json = {
            **chapter.metadata_json,
            "story_bible_update_receipts": [{"status": "llm_error"}],
        }
    elif missing == "wrong_body":
        draft.content_md += "另一个尚未抽取知识的结尾。"
    await session.commit()
    result = await settle(book)
    assert not result.is_complete and not result.body_complete
    assert project.status != "completed" and "completed_at" not in project.metadata_json
    assert not list(await session.scalars(select(ExportArtifactModel)))


async def test_candidate_cannot_be_promoted_or_complete(book):
    session, _, project, _, draft = book
    draft.promotion_state = "candidate"
    await session.commit()
    result = await settle(book)
    assert not result.is_complete and draft.promotion_state == "candidate"
    assert not list(await session.scalars(select(DraftPromotionDecisionModel)))
    assert not list(await session.scalars(select(ExportArtifactModel)))
    assert (
        project.metadata_json["book_delivery"]["blockers"][0]["reason"] == "adopted_source_missing"
    )


async def test_working_candidate_does_not_displace_adopted_source(book, monkeypatch):
    await seed_knowledge(book, monkeypatch)
    session, _, project, chapter, draft = book
    draft.is_current = False
    await session.flush()
    candidate = ChapterDraftVersionModel(
        project_id=project.id,
        chapter_id=chapter.id,
        version_no=2,
        content_md="未采用的新候选。",
        is_current=True,
        promotion_state="candidate",
    )
    session.add(candidate)
    await session.commit()
    result = await settle(book)
    assert result.is_complete, project.metadata_json
    assert candidate.promotion_state == "candidate" and not draft.is_current
    assert project.metadata_json["book_delivery"]["sources"][0]["draft_id"] == str(draft.id)


@pytest.mark.parametrize("origin", ["metadata", "audit", "reason_code"])
@pytest.mark.parametrize("title", ["海港急诊", "星际勘探日志"])
async def test_legacy_closure_promotion_is_reported_before_knowledge_and_preserved(
    book,
    monkeypatch,
    origin,
    title,
):
    session, _, project, _chapter, draft = book
    project.title = title
    project.status = "completed"
    previous = {
        "completed_at": "old",
        "completion_export_artifact_id": "old-artifact",
        "completion_reason": "all_chapters_settled",
        "completion_is_clean": True,
    }
    project.metadata_json = {**project.metadata_json, **previous}
    if origin == "metadata":
        draft.promotion_metadata = {"decision_source": "book_closure"}
    elif origin == "reason_code":
        draft.promotion_reason_codes = ["settled_at_closure"]
    else:
        session.add(
            DraftPromotionDecisionModel(
                project_id=project.id,
                chapter_draft_version_id=draft.id,
                from_state="eligible",
                to_state="promoted",
                decision_source="book_closure",
                reason_codes=["settled_at_closure"],
                evidence_json={},
            )
        )
    await session.commit()
    before = deepcopy(project.metadata_json)
    from bestseller.services import book_delivery_readiness as readiness

    async def forbidden(*args, **kwargs):
        pytest.fail("pseudo-adoption must be rejected before reading knowledge")

    monkeypatch.setattr(readiness, "read_adopted_knowledge", forbidden)
    result = await read_book_delivery_closure(session, project)
    assert not result.verdict.is_complete
    assert result.blockers[0]["reason"] == "legacy_closure_promotion_requires_readoption"
    assert project.status == "completed" and project.metadata_json == before
    result = await settle(book, fallback="completed")
    assert not result.is_complete and project.status == "revising"
    history = project.metadata_json["completion_history"]
    assert all(history[0]["previous"][key] == value for key, value in previous.items())
    assert "completed_at" not in project.metadata_json
    assert draft.promotion_state == "promoted"
    await settle(book)
    assert project.metadata_json["completion_history"] == history


@pytest.mark.parametrize("corruption", ["mirror", "frozen", "checksum", "source", "body"])
async def test_export_readback_corruption_never_completes_or_cancels_work(
    book, monkeypatch, corruption
):
    await seed_knowledge(book, monkeypatch)
    row = await task(book)
    original = exports.export_project_markdown

    async def corrupt(*args, **kwargs):
        artifact, path = await original(*args, **kwargs)
        if corruption == "mirror":
            Path(path).write_bytes(b"different")
        elif corruption == "frozen":
            Path(artifact.storage_uri).unlink()
        elif corruption == "checksum":
            artifact.checksum = "0" * 64
        elif corruption == "source":
            meta = deepcopy(artifact.metadata_json)
            meta["source_drafts"][0]["draft_id"] = str(uuid4())
            artifact.metadata_json = meta
        else:
            # A matching checksum on unrelated UTF-8 bytes is still no delivery.
            data = "错误正文。".encode()
            Path(path).write_bytes(data)
            Path(artifact.storage_uri).write_bytes(data)
            artifact.checksum = hashlib.sha256(data).hexdigest()
        return artifact, path

    monkeypatch.setattr(exports, "export_project_markdown", corrupt)
    result = await settle(book)
    session, _, project, _, _ = book
    assert not result.is_complete and result.body_complete
    assert project.status != "completed" and "completed_at" not in project.metadata_json
    await session.refresh(row)
    assert row.status == "pending"
    assert not list(await session.scalars(select(ExportArtifactModel)))


@pytest.mark.parametrize("debt", [False, True])
async def test_default_terminal_gate_failure_is_not_conceded(book, monkeypatch, debt):
    session, _, project, chapter, draft = book
    if debt:
        await make_accepted_debt((session, project, chapter, draft))
    await seed_knowledge(book, monkeypatch)
    observed = []

    def fail(**kwargs):
        observed.append(kwargs)
        return SimpleNamespace(
            passed=False, errors=["evaluator_error:contract"], issues=[], patched_text=None
        )

    monkeypatch.setattr(pipelines, "run_final_quality_gates", fail)
    result = await settle(book)
    assert not result.is_complete
    assert len(observed) == 1  # closure adds no second terminal literary assessment
    assert observed[0]["chapter_metadata"] == chapter.metadata_json
    assert "evaluator_error:contract" in project.metadata_json["completion_export_error"]


async def test_four_chapters_close_through_real_readers_and_exporter(book, monkeypatch):
    session, settings, project, _first, _draft = book
    project.target_chapters = 4
    await session.commit()
    await seed_knowledge(book, monkeypatch)
    for number in range(2, 5):
        chapter = ChapterModel(
            project_id=project.id,
            chapter_number=number,
            title=f"第{number}次出航",
            chapter_goal="调查航线",
            status="complete",
            production_state="ok",
        )
        session.add(chapter)
        await session.flush()
        body = f"船长在第{number}座码头停下，把航图压在灯下，记下新的坐标。"  # noqa: RUF001
        source = ChapterDraftVersionModel(
            project_id=project.id,
            chapter_id=chapter.id,
            version_no=1,
            content_md=body,
            is_current=True,
            promotion_state="promoted",
            assembled_from_scene_draft_ids=[str(uuid4())],
        )
        session.add(source)
        await session.commit()
        await seed_knowledge((session, settings, project, chapter, source), monkeypatch)
    result = await settle(book)
    assert result.is_complete and result.settled_chapters == 4, project.metadata_json
    artifact = await session.scalar(select(ExportArtifactModel))
    assert [s["chapter_number"] for s in artifact.metadata_json["source_drafts"]] == [1, 2, 3, 4]
    assert "第4座码头" in Path(artifact.storage_uri).read_text(encoding="utf-8")


async def test_missing_chapter_blocks_even_with_closed_existing_knowledge(book, monkeypatch):
    await seed_knowledge(book, monkeypatch)
    book[2].target_chapters = 2
    await book[0].commit()
    result = await settle(book)
    assert not result.is_complete and result.unsettled_chapters == (2,)


async def test_readonly_revalidation_detects_deleted_export_and_migrates_stale_complete(
    book, monkeypatch
):
    await seed_knowledge(book, monkeypatch)
    assert (await settle(book)).is_complete
    session, _, project, _, _ = book
    previous = deepcopy(project.metadata_json)
    Path(previous["completion_export_storage_uri"]).unlink()
    result = await read_book_delivery_closure(session, project)
    assert not result.verdict.is_complete and result.verdict.body_complete
    assert project.metadata_json == previous and project.status == "completed"

    async def fail(*args, **kwargs):
        raise OSError("temporarily unavailable")

    monkeypatch.setattr(exports, "export_project_markdown", fail)
    result = await settle(book, fallback="completed")
    assert not result.is_complete and project.status == "revising"
    history = project.metadata_json["completion_history"][-1]
    assert (
        history["previous"]["completion_export_checksum"] == previous["completion_export_checksum"]
    )
    assert len(list(await session.scalars(select(ExportArtifactModel)))) == 1


async def test_valid_completion_is_idempotent_without_export_or_rejudging(book, monkeypatch):
    await seed_knowledge(book, monkeypatch)
    assert (await settle(book)).is_complete
    session, _, project, _, _ = book
    before = deepcopy(project.metadata_json)

    async def forbidden(*args, **kwargs):
        pytest.fail("verified completion must only re-read")

    monkeypatch.setattr(exports, "export_project_markdown", forbidden)
    monkeypatch.setattr(pipelines, "run_final_quality_gates", forbidden)
    assert (await settle(book)).is_complete
    assert project.metadata_json == before
    assert len(list(await session.scalars(select(ExportArtifactModel)))) == 1


async def test_export_db_failure_rolls_back_without_poisoning_retry(book, monkeypatch):
    await seed_knowledge(book, monkeypatch)
    row = await task(book)
    session, _, project, _, _ = book
    original = exports.export_project_markdown

    async def fail_after_flush(*args, **kwargs):
        await original(*args, **kwargs)
        session.add(
            ExportArtifactModel(
                project_id=project.id,
                source_scope="project",
                source_id=project.id,
                storage_uri="unused",
                export_type=None,
            )
        )
        await session.flush()
        pytest.fail("invalid artifact should fail SQL flush")

    monkeypatch.setattr(exports, "export_project_markdown", fail_after_flush)
    assert not (await settle(book)).is_complete
    await session.refresh(row)
    assert row.status == "pending"
    assert not list(await session.scalars(select(ExportArtifactModel)))
    monkeypatch.setattr(exports, "export_project_markdown", original)
    assert (await settle(book)).is_complete


async def test_changed_adopted_body_during_export_never_completes(book, monkeypatch):
    await seed_knowledge(book, monkeypatch)
    session, _, project, _, draft = book
    original = exports.export_project_markdown
    body = draft.content_md

    async def drift(*args, **kwargs):
        result = await original(*args, **kwargs)
        draft.content_md += "变更。"
        await session.flush()
        return result

    monkeypatch.setattr(exports, "export_project_markdown", drift)
    assert not (await settle(book)).is_complete
    await session.refresh(draft)
    assert draft.content_md == body
    assert (
        "completion_source_changed_during_export"
        in project.metadata_json["completion_export_error"]
    )


async def test_failed_task_settlement_does_not_cancel_some_tasks_or_fake_delivery(
    book, monkeypatch
):
    await seed_knowledge(book, monkeypatch)
    row = await task(book)
    session, _, project, _, _ = book

    async def fail(*args, **kwargs):
        row.status = "cancelled"
        await session.flush()
        raise RuntimeError("task settlement unavailable")

    monkeypatch.setattr(book_closure, "settle_outstanding_rewrite_tasks", fail)
    assert not (await settle(book)).is_complete
    await session.refresh(row)
    assert row.status == "pending"
    assert project.status != "completed"


async def test_readonly_closure_uses_database_target_without_overwriting_pending_changes(
    book, monkeypatch
):
    from sqlalchemy import update

    from bestseller.infra.db.models import ProjectModel

    await seed_knowledge(book, monkeypatch)
    assert (await settle(book)).is_complete
    session, _, project, _, _ = book
    original_title = project.title
    project.title = "unsaved caller title"
    await session.execute(
        update(ProjectModel)
        .where(ProjectModel.id == project.id)
        .values(
            target_chapters=2,
        )
        .execution_options(synchronize_session=False, autoflush=False)
    )
    assert project.target_chapters == 1
    result = await read_book_delivery_closure(session, project)
    assert not result.verdict.is_complete and result.verdict.unsettled_chapters == (2,)
    assert project.title == "unsaved caller title" and session.is_modified(project)
    stored_title = await session.scalar(
        select(ProjectModel.title)
        .where(
            ProjectModel.id == project.id,
        )
        .execution_options(autoflush=False)
    )
    assert stored_title == original_title


@pytest.mark.parametrize("reader_kind", ["readiness", "delivery"])
async def test_readonly_readers_preserve_unflushed_source_objects_and_outer_transaction(
    book,
    monkeypatch,
    reader_kind,
):
    from sqlalchemy import update

    from bestseller.infra.db.models import ProjectModel

    await seed_knowledge(book, monkeypatch)
    assert (await settle(book)).is_complete
    session, _, project, chapter, draft = book
    stored_body, stored_title = draft.content_md, chapter.title
    chapter.title = "unflushed chapter title"
    draft.content_md = "unflushed candidate body"
    snapshot = await session.scalar(
        select(ChapterStateSnapshotModel).execution_options(autoflush=False)
    )
    snapshot.extraction_status = "unflushed status"
    await session.execute(
        update(ProjectModel)
        .where(ProjectModel.id == project.id)
        .values(
            title="outer transaction survives reader close",
        )
        .execution_options(synchronize_session=False, autoflush=False)
    )
    reader = (
        read_book_delivery_readiness if reader_kind == "readiness" else read_book_delivery_closure
    )
    result = await reader(session, project)
    # Persisted title change invalidates the old rendered artifact, but sources
    # are still read from the persisted rows, never the pending local mutation.
    assert result.verdict.body_complete
    assert result.payloads[0][1].content_md == stored_body
    assert chapter.title == "unflushed chapter title" and session.is_modified(chapter)
    assert draft.content_md == "unflushed candidate body" and session.is_modified(draft)
    assert snapshot.extraction_status == "unflushed status" and session.is_modified(snapshot)
    assert session.in_transaction()
    persisted = await session.scalar(
        select(ProjectModel.title)
        .where(
            ProjectModel.id == project.id,
        )
        .execution_options(autoflush=False)
    )
    assert persisted == "outer transaction survives reader close"
    await session.rollback()
    await session.refresh(project)
    await session.refresh(chapter)
    await session.refresh(draft)
    assert project.title != "outer transaction survives reader close"
    assert chapter.title == stored_title and draft.content_md == stored_body


async def test_missing_explicit_bible_receipt_cannot_complete_delivery(book, monkeypatch):
    await seed_knowledge(book, monkeypatch)
    session, _, _, chapter, _ = book
    metadata = dict(chapter.metadata_json)
    metadata.pop("story_bible_update_receipts")
    chapter.metadata_json = metadata
    await session.commit()
    verdict = await settle(book)
    assert not verdict.is_complete and not verdict.body_complete
