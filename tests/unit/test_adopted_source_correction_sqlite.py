"""Real SQLite correction transactions, historical preservation and prompt boundary.

Historical score/report rows below are inert archive fixtures. No fresh grade is
manufactured, and neither source guards, selectors nor the reader are replaced.
"""

# Exact Chinese prose fixtures intentionally use fullwidth punctuation.
# ruff: noqa: RUF001

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy.schema import CreateTable

from bestseller.infra.db.models import (
    BookProductionControlModel,
    CanonFactModel,
    ChapterDraftVersionModel,
    ChapterModel,
    ChapterStateSnapshotModel,
    LlmRunModel,
    PlanningArtifactVersionModel,
    ProjectModel,
    QualityScoreModel,
    ReviewReportModel,
    TimelineEventModel,
    WorkflowRunModel,
)
from bestseller.services import adopted_source_correction as correction
from bestseller.services.adopted_chapter_source import select_adopted_chapter_source
from bestseller.services.exact_draft_text_edit import prepare_exact_draft_text_edit
from bestseller.services.operator_action_approval import record_operator_action_approval
from bestseller.services.prompt_source_policy import content_hash
from bestseller.services.review_source_context import (
    load_review_source_context,
    prepare_source_complete_prompt,
)
from bestseller.services.rewrite_patch import text_hash
from bestseller.settings import load_settings

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

BODY = (
    "开场🌧️\r\n她把旧磁带放进播放机，却没按下播放键。桌角摊着登记册，纸页还沾着雨。\r\n"
    "守夜人把灯拧亮，仔细核对每一行记录；墙上的钟走了半圈，窗外的脚步声才停。\r\n"
    "封条仍完好，她这才第一次拆袋。两人先核对编号，又把空纸袋放到一旁，谁也没有离开屋子。\r\n"
    "灯光落在铜钥匙上。她关好窗户，抹去桌面水迹，在登记册末尾签名，等待下一位证人。\t\n"
)
EDITS = [
    {"before": "把灯拧亮", "after": "拨亮灯"},
    {"before": "放进播放机", "after": "放在播放机旁"},
]
EVIDENCE = [
    {
        "quote": "她把旧磁带放进播放机，却没按下播放键。",
        "observation": "装带动作发生在封袋首次拆开之前。",
    },
    {
        "quote": "封条仍完好，她这才第一次拆袋。",
        "observation": "此处仍声称封条完好，应核验先前动作的前置条件。",
    },
]
MODELS = (
    ProjectModel,
    PlanningArtifactVersionModel,
    ChapterModel,
    ChapterDraftVersionModel,
    WorkflowRunModel,
    LlmRunModel,
    ReviewReportModel,
    QualityScoreModel,
    CanonFactModel,
    TimelineEventModel,
    ChapterStateSnapshotModel,
)
ARCHIVES = (
    LlmRunModel,
    ReviewReportModel,
    QualityScoreModel,
    CanonFactModel,
    TimelineEventModel,
    ChapterStateSnapshotModel,
)


@compiles(JSONB, "sqlite")
def _jsonb(_type, _compiler, **_kw):
    return "JSON"


@compiles(PGUUID, "sqlite")
def _uuid_text(_type, _compiler, **_kw):
    return "TEXT"


def plain(value):
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def row_data(row):
    return plain({key: getattr(row, key) for key in row.__mapper__.column_attrs.keys()})


async def inventory(session, models=MODELS):
    return {
        model.__tablename__: sorted(
            [
                row_data(row)
                for row in await session.scalars(
                    select(model).execution_options(populate_existing=True)
                )
            ],
            key=lambda row: row["id"],
        )
        for model in models
    }


@pytest.fixture
async def db(tmp_path, monkeypatch):
    # Same JSONB/UUID DDL pattern as test_adopted_knowledge_lineage, but two
    # independent sessions and an explicit physical BEGIN for rollback proof.
    path = tmp_path / "adopted_source_correction_test.sqlite3"
    assert path.stem.endswith("_test")
    url = make_url(f"sqlite+aiosqlite:///{path}")
    assert url.get_backend_name() == "sqlite" and Path(url.database).stem.endswith("_test")
    engine = create_async_engine(url)

    @event.listens_for(engine.sync_engine, "connect")
    def disable_legacy_transaction_mode(connection, _record):
        connection.isolation_level = None

    @event.listens_for(engine.sync_engine, "begin")
    def explicit_begin(connection):
        connection.exec_driver_sql("BEGIN")

    async with engine.begin() as connection:
        for model in MODELS:
            ddl = str(CreateTable(model.__table__).compile(dialect=engine.dialect))
            await connection.exec_driver_sql(ddl.replace("::jsonb", ""))
        await connection.exec_driver_sql(
            "CREATE UNIQUE INDEX correction_current_test ON chapter_draft_versions(chapter_id) "
            "WHERE is_current"
        )
        await connection.exec_driver_sql(
            "CREATE UNIQUE INDEX correction_promoted_test ON chapter_draft_versions(chapter_id) "
            "WHERE promotion_state = 'promoted'"
        )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    calls = []

    async def forbidden_gateway(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("correction must never enter the paid gateway")

    monkeypatch.setattr("bestseller.services.llm.complete_text", forbidden_gateway)
    monkeypatch.setattr("bestseller.services.llm._call_litellm", forbidden_gateway)
    async with sessions() as session:
        books = []
        for index in range(2):
            project = ProjectModel(
                slug=f"correction-test-{uuid4().hex}",
                title=f"Book {index}",
                genre="test",
                target_word_count=10000,
                target_chapters=4,
                language="zh-CN",
            )
            session.add(project)
            await session.flush()
            chapter = ChapterModel(
                project_id=project.id,
                chapter_number=1,
                chapter_goal="Verify sequence",
                status="completed",
                production_state="completed",
                metadata_json={},
            )
            session.add(chapter)
            await session.flush()
            paid = LlmRunModel(
                project_id=project.id,
                logical_role="chapter_critic",
                provider="archive",
                model_name="historical-only",
                input_tokens=321,
                output_tokens=123,
                metadata_json={
                    "raw_response": "OLD_PAID_PASS_DO_NOT_TRANSFER",
                    "cost": "preserved",
                },
            )
            session.add(paid)
            await session.flush()
            draft = ChapterDraftVersionModel(
                project_id=project.id,
                chapter_id=chapter.id,
                version_no=1,
                content_md=BODY,
                is_current=True,
                promotion_state="promoted",
                promotion_score=0.91,
                llm_run_id=paid.id,
                assembled_from_scene_draft_ids=[str(uuid4())],
                promotion_metadata={"old_paid_grade": {"raw": "OLD_PAID_PASS_DO_NOT_TRANSFER"}},
            )
            session.add(draft)
            await session.flush()
            old_admission = {
                "draft_id": str(draft.id),
                "body_sha256": text_hash(BODY.replace("\r\n", "\n")),
                "passed": True,
                "old_receipt": "retained",
            }
            chapter.metadata_json = {
                "reader_judge": {
                    "raw": "OLD_PAID_PASS_DO_NOT_TRANSFER",
                    "cycles": ["large-cycle" * 1000],
                },
                "current_body_review_status": "pass",
                "continuity_admission": "legacy scalar receipt",
                "continuity_admission_by_source": {
                    f"{draft.id}:{old_admission['body_sha256']}": old_admission
                },
                "continuity_admission_history": [{"historical": i} for i in range(25)],
                "superseded_body_review_history": [{"historical": i} for i in range(25)],
                "chapter_first_local_repair_count": 2,
                "auto_repair_total_attempts": 3,
            }
            report = ReviewReportModel(
                project_id=project.id,
                target_type="chapter",
                target_id=chapter.id,
                reviewer_type="chapter",
                verdict="pass",
                llm_run_id=paid.id,
                structured_output={"archive_only": True, "raw": "OLD_PAID_PASS_DO_NOT_TRANSFER"},
            )
            session.add(report)
            await session.flush()
            session.add_all(
                [
                    QualityScoreModel(
                        project_id=project.id,
                        target_type="chapter",
                        target_id=chapter.id,
                        chapter_draft_version_id=draft.id,
                        review_report_id=report.id,
                        judge_key="historical-only",
                        score_overall=0.91,
                        is_current=True,
                        evidence_summary={"archive_only": True},
                    ),
                    CanonFactModel(
                        project_id=project.id,
                        source_chapter_id=chapter.id,
                        subject_type="object",
                        subject_label="磁带",
                        predicate="location",
                        fact_type="extracted",
                        value_json={"value": "播放机"},
                        is_current=True,
                    ),
                    TimelineEventModel(
                        project_id=project.id,
                        chapter_id=chapter.id,
                        event_name="装带",
                        event_type="action",
                        story_time_label="夜",
                        story_order=1,
                        metadata_json={"old": True},
                    ),
                    ChapterStateSnapshotModel(
                        project_id=project.id,
                        chapter_id=chapter.id,
                        chapter_number=1,
                        extraction_status="ok_promoted",
                        facts={"facts": [{"value": "播放机"}]},
                        raw_extraction="old snapshot bytes",
                    ),
                ]
            )
            books.append((project.id, chapter.id, draft.id))
        await session.commit()
    try:
        yield SimpleNamespace(sessions=sessions, book=books[0], other=books[1], calls=calls)
        assert calls == []
    finally:
        await engine.dispose()


async def prepare(session, book, **overrides):
    project_id, chapter_id, draft_id = book
    kwargs = {
        "project_id": project_id,
        "chapter_id": chapter_id,
        "draft_id": draft_id,
        "body_sha256": text_hash(BODY),
        "edits": deepcopy(EDITS),
        "evidence": deepcopy(EVIDENCE),
        "reason": "核验装带与首次拆袋之间的前置条件，仅提出精确修改。",
    }
    return await correction.prepare_adopted_source_correction(session, **{**kwargs, **overrides})


async def approve(session, plan, **overrides):
    kwargs = {
        "purpose": "explicit_draft_text_edit",
        "binding": plan,
        "capabilities": ["apply_exact_text_edits"],
        "actor": "isolated-test-operator",
        "authorization_ref": "isolated-test-explicit-plan-only",
        "explicit_user_approval": True,
    }
    return await record_operator_action_approval(session, **{**kwargs, **overrides})


async def issue(db):
    async with db.sessions() as session:
        plan = await prepare(session, db.book)
        approval_id = await approve(session, plan)
        await session.commit()
    async with db.sessions() as session:
        result = await correction.apply_adopted_source_correction(
            session, plan=plan, approval_id=approval_id
        )
        await session.commit()
    return plan, approval_id, result


async def context(session, db, body=None):
    return await correction.read_adopted_source_correction_context(
        session, project_id=db.book[0], chapter_id=db.book[1], content_md=body or BODY
    )


async def real_review_context(session, db, body):
    return await load_review_source_context(
        session,
        project=await session.get(ProjectModel, db.book[0]),
        chapter=await session.get(ChapterModel, db.book[1]),
        scenes=[],
        context_packet=SimpleNamespace(),
        content_md=body,
    )


async def test_two_sessions_preserve_archives_block_only_old_source_and_require_fresh_review(db):
    async with db.sessions() as session:
        before = await inventory(session, ARCHIVES)
        old = row_data(await session.get(ChapterDraftVersionModel, db.book[2]))
        chapter_before = row_data(await session.get(ChapterModel, db.book[1]))
        foreign_before = row_data(await session.get(ChapterModel, db.other[1]))
    plan, approval_id, result = await issue(db)
    assert (
        "metadata_json" not in plan["chapter_before"]
        and "promotion_metadata" not in plan["source_before"]
    )
    assert plan["source_before"]["fullrow_sha256"] == content_hash(old)
    assert plan["chapter_before"]["fullrow_sha256"] == content_hash(chapter_before)
    assert plan["chapter_before"]["metadata_sha256"] == content_hash(
        chapter_before["metadata_json"]
    )
    assert "large-cycle" not in json.dumps(plan) and "OLD_PAID_PASS" not in json.dumps(plan)
    async with db.sessions() as session:
        chapter = await session.get(ChapterModel, db.book[1])
        candidate = await session.get(ChapterDraftVersionModel, UUID(result["candidate_id"]))
        parent = await session.get(ChapterDraftVersionModel, db.book[2])
        assert candidate.is_current and candidate.promotion_state == "candidate"
        assert candidate.llm_run_id is None and candidate.promotion_score is None
        assert candidate.assembled_from_scene_draft_ids == parent.assembled_from_scene_draft_ids
        assert chapter.metadata_json["current_body_review_status"] == "pending"
        assert chapter.status == "revision" and chapter.production_state == "pending"
        assert "reader_judge" not in chapter.metadata_json
        assert (
            chapter.metadata_json["superseded_body_review_history"][:25]
            == chapter_before["metadata_json"]["superseded_body_review_history"]
        )
        assert (
            chapter.metadata_json["superseded_body_review_history"][-1][
                "historical_review_snapshot"
            ]["reports"]["reader_judge"]
            == chapter_before["metadata_json"]["reader_judge"]
        )
        assert (
            chapter.metadata_json["continuity_admission_history"][:25]
            == chapter_before["metadata_json"]["continuity_admission_history"]
        )
        assert "legacy scalar receipt" in chapter.metadata_json["continuity_admission_history"]
        assert chapter.metadata_json["chapter_first_local_repair_count"] == 2
        assert chapter.metadata_json["auto_repair_total_attempts"] == 3
        assert not parent.is_current and parent.promotion_state == "promoted"
        preserved = row_data(parent)
        preserved["is_current"] = old["is_current"]
        preserved["promotion_metadata"].pop("source_correction_links")
        assert preserved == old
        assert await inventory(session, ARCHIVES) == before
        assert (await select_adopted_chapter_source(session, chapter=chapter, lock=False))[
            0
        ] is None
        foreign = await session.get(ChapterModel, db.other[1])
        assert row_data(foreign) == foreign_before
        assert (await select_adopted_chapter_source(session, chapter=foreign, lock=False))[
            0
        ].id == db.other[2]
        # Byte-level proof for every unchanged interval, including CRLF/emoji/trailing whitespace.
        cursor = new_cursor = 0
        for span, edit in sorted(
            zip(plan["edit_spans"], plan["edits"], strict=True), key=lambda item: item[0]["start"]
        ):
            unchanged = BODY[cursor : span["start"]]
            assert (
                candidate.content_md[new_cursor : new_cursor + len(unchanged)].encode()
                == unchanged.encode()
            )
            new_cursor += len(unchanged)
            assert (
                candidate.content_md[new_cursor : new_cursor + len(edit["after"])] == edit["after"]
            )
            new_cursor += len(edit["after"])
            cursor = span["end"]
        assert candidate.content_md[new_cursor:].encode() == BODY[cursor:].encode()
        assert not list(
            await session.scalars(
                select(QualityScoreModel).where(
                    QualityScoreModel.chapter_draft_version_id == candidate.id
                )
            )
        )
        assert not list(
            await session.scalars(
                select(ReviewReportModel).where(
                    ReviewReportModel.structured_output["draft_id"].as_string() == str(candidate.id)
                )
            )
        )
        assert (
            await correction.apply_adopted_source_correction(
                session, plan=plan, approval_id=approval_id
            )
            == result
        )
        await session.commit()
    async with db.sessions() as session:
        assert len(list(await session.scalars(select(ChapterDraftVersionModel)))) == 3
        assert (
            len(
                list(
                    await session.scalars(
                        select(WorkflowRunModel).where(
                            WorkflowRunModel.workflow_type == "adopted_source_correction"
                        )
                    )
                )
            )
            == 1
        )


@pytest.mark.parametrize(
    "fault",
    [
        "hash",
        "foreign",
        "unadopted",
        "not_current",
        "empty_edits",
        "duplicate",
        "overlap",
        "oversize",
        "extra_edit_field",
        "one_observation",
        "duplicate_quote",
        "missing_quote",
        "canon",
        "ledger",
        "history",
    ],
)
async def test_prepare_rejects_malformed_or_unbound_observations_without_writes(db, fault):
    async with db.sessions() as session:
        kwargs = {}
        draft = await session.get(ChapterDraftVersionModel, db.book[2])
        chapter = await session.get(ChapterModel, db.book[1])
        if fault == "hash":
            kwargs["body_sha256"] = "bad"
        elif fault == "foreign":
            kwargs["draft_id"] = db.other[2]
        elif fault == "unadopted":
            draft.promotion_state = "candidate"
        elif fault == "not_current":
            draft.is_current = False
        elif fault == "empty_edits":
            kwargs["edits"] = []
        elif fault == "duplicate":
            kwargs["edits"] = [EDITS[0], EDITS[0]]
        elif fault == "overlap":
            kwargs["edits"] = [EDITS[1], {"before": "旧磁带放进播放机", "after": "旧磁带"}]
        elif fault == "oversize":
            kwargs["edits"] = [{"before": BODY[:-2], "after": "x"}]
        elif fault == "extra_edit_field":
            kwargs["edits"] = [{**EDITS[0], "approved": True}]
        elif fault == "one_observation":
            kwargs["evidence"] = EVIDENCE[:1]
        elif fault == "duplicate_quote":
            kwargs["evidence"] = [EVIDENCE[0], EVIDENCE[0]]
        elif fault == "missing_quote":
            kwargs["evidence"] = [EVIDENCE[0], {"quote": "missing", "observation": "x"}]
        elif fault == "canon":
            kwargs["evidence"] = [EVIDENCE[0], {**EVIDENCE[1], "canon": True}]
        elif fault == "ledger":
            chapter.metadata_json = {"continuity_admission_by_source": []}
        elif fault == "history":
            chapter.metadata_json = {"continuity_admission_history": {}}
        await session.commit()
        before = await inventory(session)
        with pytest.raises(ValueError):
            await prepare(session, db.book, **kwargs)
        assert await inventory(session) == before


@pytest.mark.parametrize(
    "field",
    [
        "result_content_md",
        "source_bound_evidence",
        "edit_spans",
        "protected_spans",
        "source_before",
        "chapter_before",
        "plan_sha256",
        "target_id",
        "candidate_id",
        "model_calls",
        "fresh_review_required",
        "extra",
    ],
)
async def test_tampered_plan_is_rejected_before_any_write(db, field):
    async with db.sessions() as session:
        plan = await prepare(session, db.book)
        approval_id = await approve(session, plan)
        await session.commit()
        tampered = plain(plan)
        if field == "source_bound_evidence":
            tampered[field][0]["observation"] += " tampered"
        elif field in {"edit_spans", "protected_spans"}:
            tampered[field][0]["start"] += 1
        elif field in {"source_before", "chapter_before"}:
            tampered[field]["fullrow_sha256"] = "tampered"
        elif field == "model_calls":
            tampered[field] = False  # False == 0 must not pass exact JSON binding.
        elif field == "fresh_review_required":
            tampered[field] = 1
        else:
            tampered[field] = "tampered"
        before = await inventory(session)
        with pytest.raises(ValueError):
            await correction.apply_adopted_source_correction(
                session, plan=tampered, approval_id=approval_id
            )
        assert await inventory(session) == before


@pytest.mark.parametrize(
    "fault",
    [
        "missing",
        "unknown",
        "foreign",
        "purpose",
        "capability",
        "binding",
        "malformed",
        "status",
        "scalar_capability",
        "actor_tampered",
        "reference_tampered",
    ],
)
async def test_exact_operator_approval_required_no_partial_writes(db, fault):
    async with db.sessions() as session:
        plan = await prepare(session, db.book)
        if fault == "missing":
            approval_id = None
        elif fault == "unknown":
            approval_id = uuid4()
        elif fault == "foreign":
            approval_id = await approve(session, await prepare(session, db.other))
        elif fault == "purpose":
            approval_id = await approve(
                session,
                plan,
                purpose="draft_continuity_mode",
                capabilities=["enable_pending_manuscript"],
            )
        else:
            approval_id = await approve(session, plan)
            row = await session.get(WorkflowRunModel, approval_id)
            data = deepcopy(row.metadata_json)
            if fault == "capability":
                data["capabilities"] = []
            elif fault == "scalar_capability":
                data["capabilities"] = "apply_exact_text_edits"
            elif fault == "actor_tampered":
                data["actor"] = "changed after approval"
            elif fault == "reference_tampered":
                data["authorization_ref"] = "changed after approval"
            elif fault == "binding":
                data["binding"] = {**data["binding"], "model_calls": False}
            elif fault == "malformed":
                data = ["corrupt"]
            elif fault == "status":
                row.status = "failed"
            row.metadata_json = data
            flag_modified(row, "metadata_json")
        await session.commit()
        before = await inventory(session)
        with pytest.raises(ValueError):
            await correction.apply_adopted_source_correction(
                session, plan=plan, approval_id=approval_id
            )
        assert await inventory(session) == before


@pytest.mark.parametrize(
    "fault", ["body", "source_metadata", "chapter_metadata", "chapter_status", "promotion"]
)
async def test_second_session_changes_invalidate_approved_plan_even_with_cached_rows(db, fault):
    async with db.sessions() as first:
        cached_draft = await first.get(ChapterDraftVersionModel, db.book[2])
        cached_chapter = await first.get(ChapterModel, db.book[1])
        plan = await prepare(first, db.book)
        approval_id = await approve(first, plan)
        await first.commit()
        async with db.sessions() as second:
            draft = await second.get(ChapterDraftVersionModel, db.book[2])
            chapter = await second.get(ChapterModel, db.book[1])
            if fault == "body":
                draft.content_md += "后加句。"
            elif fault == "source_metadata":
                draft.promotion_metadata = {**draft.promotion_metadata, "new_receipt": True}
            elif fault == "chapter_metadata":
                chapter.metadata_json = {**chapter.metadata_json, "new_cycle": True}
            elif fault == "chapter_status":
                chapter.status = "revision"
            else:
                draft.promotion_state = "superseded"
            await second.commit()
        assert cached_draft.content_md == BODY and cached_draft.promotion_state == "promoted"
        assert (
            cached_chapter.status == "completed" and "new_cycle" not in cached_chapter.metadata_json
        )
        async with db.sessions() as observer:
            before = await inventory(observer)
        with pytest.raises(ValueError):
            await correction.apply_adopted_source_correction(
                first, plan=plan, approval_id=approval_id
            )
        assert await inventory(first) == before


async def test_owned_savepoint_rolls_back_mid_write_failure_and_preserves_caller_work(db):
    async with db.sessions() as session:
        plan = await prepare(session, db.book)
        approval_id = await approve(session, plan)
        await session.commit()
        unrelated = await session.get(ProjectModel, db.other[0])
        unrelated.title = "caller work must survive"
        writes = []

        def fail_receipt(sync_session, _context, _instances):
            for row in list(sync_session.new) + list(sync_session.dirty):
                if isinstance(row, ChapterDraftVersionModel):
                    assert sync_session.in_nested_transaction()
                    writes.append(row.id)
                if (
                    isinstance(row, WorkflowRunModel)
                    and row.workflow_type == "adopted_source_correction"
                ):
                    raise RuntimeError("injected receipt flush failure")

        event.listen(session.sync_session, "before_flush", fail_receipt)
        try:
            with pytest.raises(RuntimeError, match="injected receipt flush failure"):
                await correction.apply_adopted_source_correction(
                    session, plan=plan, approval_id=approval_id
                )
        finally:
            event.remove(session.sync_session, "before_flush", fail_receipt)
        assert writes and not session.in_nested_transaction()
        await session.commit()
    async with db.sessions() as session:
        assert (await session.get(ProjectModel, db.other[0])).title == "caller work must survive"
        assert await session.get(ChapterDraftVersionModel, UUID(plan["candidate_id"])) is None
        assert await session.get(WorkflowRunModel, UUID(plan["target_id"])) is None
        chapter = await session.get(ChapterModel, db.book[1])
        assert correction.REVIEW_KEY not in chapter.metadata_json
        assert (await select_adopted_chapter_source(session, chapter=chapter, lock=False))[
            0
        ].id == db.book[2]
        assert (await session.get(ChapterDraftVersionModel, db.book[2])).is_current


async def test_successful_apply_does_not_commit_caller_transaction(db):
    async with db.sessions() as session:
        plan = await prepare(session, db.book)
        approval_id = await approve(session, plan)
        await session.commit()
        before = await inventory(session)
        await correction.apply_adopted_source_correction(
            session, plan=plan, approval_id=approval_id
        )
        await session.rollback()
    async with db.sessions() as session:
        assert await inventory(session) == before


@pytest.mark.parametrize(
    "fault",
    [
        "marker",
        "plan",
        "evidence",
        "receipt",
        "pointer_deleted",
        "ledger",
        "history",
        "workflow",
        "approval",
        "parent",
        "llm",
        "lineage",
    ],
)
async def test_exact_current_own_marker_corruption_fails_closed(db, fault):
    plan, approval_id, result = await issue(db)
    async with db.sessions() as session:
        candidate = await session.get(ChapterDraftVersionModel, UUID(result["candidate_id"]))
        chapter = await session.get(ChapterModel, db.book[1])
        metadata = deepcopy(candidate.promotion_metadata)
        if fault == "marker":
            metadata[correction.KEY] = "bad"
        elif fault == "plan":
            metadata[correction.KEY]["plan"] = []
        elif fault == "evidence":
            metadata[correction.KEY]["plan"]["source_bound_evidence"][0]["quote"] = "foreign quote"
        elif fault == "receipt":
            metadata[correction.KEY]["receipt"]["grade_asserted"] = 0
        elif fault == "pointer_deleted":
            chapter.metadata_json = {
                k: v for k, v in chapter.metadata_json.items() if k != correction.REVIEW_KEY
            }
        elif fault == "ledger":
            chapter.metadata_json = {**chapter.metadata_json, "continuity_admission_by_source": {}}
        elif fault == "history":
            chapter.metadata_json = {**chapter.metadata_json, "continuity_admission_history": []}
        elif fault == "workflow":
            (await session.get(WorkflowRunModel, UUID(plan["target_id"]))).status = "failed"
        elif fault == "approval":
            (await session.get(WorkflowRunModel, approval_id)).status = "failed"
        elif fault == "parent":
            (await session.get(ChapterDraftVersionModel, db.book[2])).content_md += "改动"
        elif fault == "llm":
            candidate.llm_run_id = uuid4()
        elif fault == "lineage":
            metadata["parent_draft_id"] = str(db.other[2])
        candidate.promotion_metadata = metadata
        flag_modified(candidate, "promotion_metadata")
        await session.commit()
    async with db.sessions() as session:
        before = await inventory(session)
        with pytest.raises(ValueError):
            await context(session, db, plan["result_content_md"])
        assert await inventory(session) == before


async def test_read_context_survives_adopted_state_but_never_authorizes_replay_or_later_draft(db):
    plan, approval_id, result = await issue(db)
    async with db.sessions() as session:
        assert await context(session, db, "transient preview") is None
        candidate = await session.get(ChapterDraftVersionModel, UUID(result["candidate_id"]))
        parent = await session.get(ChapterDraftVersionModel, db.book[2])
        # Persist the post-adoption state only; this is not a fake fresh grade
        # or a claim that this test performed the review/promotion workflow.
        parent.promotion_state = "superseded"
        await session.flush()
        candidate.promotion_state = "promoted"
        chapter = await session.get(ChapterModel, db.book[1])
        chapter.status, chapter.production_state = "completed", "completed"
        await session.commit()
        observed = await context(session, db, candidate.content_md)
        assert observed["parent_content_md"] == BODY
        assert observed["candidate_content_md"] == plan["result_content_md"]
        assert observed["grade_asserted"] is False and observed["adoption_authorized"] is False
        assert (await select_adopted_chapter_source(session, chapter=chapter, lock=False))[
            0
        ].id == candidate.id
        with pytest.raises(ValueError, match="replay_candidate_changed"):
            await correction.apply_adopted_source_correction(
                session, plan=plan, approval_id=approval_id
            )
        candidate.is_current = False
        await session.flush()
        session.add(
            ChapterDraftVersionModel(
                project_id=db.book[0],
                chapter_id=db.book[1],
                version_no=3,
                content_md=plan["result_content_md"],
                is_current=True,
                promotion_state="candidate",
                promotion_metadata={},
            )
        )
        await session.commit()
        assert await context(session, db, plan["result_content_md"]) is None
        with pytest.raises(ValueError):
            await correction.apply_adopted_source_correction(
                session, plan=plan, approval_id=approval_id
            )


@pytest.mark.parametrize("operation", ["prepare", "read"])
async def test_pending_caller_context_edits_are_rejected_and_never_overwritten(db, operation):
    if operation == "read":
        plan, _, result = await issue(db)
    async with db.sessions() as session:
        if operation == "prepare":
            row = await session.get(ChapterModel, db.book[1])
            row.metadata_json = {**row.metadata_json, "pending_caller_change": True}
        else:
            row = await session.get(ChapterDraftVersionModel, UUID(result["candidate_id"]))
            row.promotion_metadata = {
                **row.promotion_metadata,
                correction.KEY: "pending corrupt marker",
            }
        before = row_data(row)
        with pytest.raises(ValueError, match="unflushed_context"):
            if operation == "prepare":
                await prepare(session, db.book)
            else:
                await context(session, db, plan["result_content_md"])
        assert row_data(row) == before and row in session.dirty


async def test_real_review_entry_and_complete_prompt_keep_exact_quoted_proposal(db):
    async with db.sessions() as session:
        ordinary = await real_review_context(session, db, BODY)
        assert correction.REVIEW_KEY not in ordinary
    plan, _, result = await issue(db)
    async with db.sessions() as session:
        source = await real_review_context(session, db, plan["result_content_md"])
        observed = source[correction.REVIEW_KEY]
        assert observed == await context(session, db, plan["result_content_md"])
        assert observed["parent_content_md"] == BODY
        assert observed["candidate_content_md"] == plan["result_content_md"]
        assert observed["source_bound_evidence"] == plan["source_bound_evidence"]
        assert observed["edits"] == EDITS
        for evidence in observed["source_bound_evidence"]:
            assert BODY[evidence["start"] : evidence["end"]] == evidence["quote"]
            assert evidence["parent_draft_id"] == str(db.book[2])
            assert evidence["parent_body_sha256"] == text_hash(BODY)
            assert evidence["is_observation_not_canon"] is True
        system, user, receipt = prepare_source_complete_prompt(
            system_prompt="Critic protocol.",
            user_prompt="Verify exact passages.",
            source_context=source,
            output_tokens=6000,
        )
        decoded, _ = json.JSONDecoder().raw_decode(user)
        assert decoded == source and decoded[correction.REVIEW_KEY] == observed
        assert "quoted proposals" in system and "neither transfer a historical PASS" in system
        assert "OLD_PAID_PASS_DO_NOT_TRANSFER" not in user + system
        assert receipt["source_complete"] and not receipt["compiler"]["truncated"]
        assert not receipt["compiler"]["dropped"]
        # Entry-level regression: deleting the chapter pointer must not bypass
        # the real reader while the exact current candidate owns its marker.
        chapter = await session.get(ChapterModel, db.book[1])
        chapter.metadata_json = {
            k: v for k, v in chapter.metadata_json.items() if k != correction.REVIEW_KEY
        }
        await session.commit()
        with pytest.raises(ValueError, match="pointer_changed"):
            await real_review_context(session, db, plan["result_content_md"])
        candidate = await session.get(ChapterDraftVersionModel, UUID(result["candidate_id"]))
        candidate.is_current = False
        await session.flush()
        session.add(
            ChapterDraftVersionModel(
                project_id=db.book[0],
                chapter_id=db.book[1],
                version_no=3,
                content_md=plan["result_content_md"],
                is_current=True,
                promotion_state="candidate",
                promotion_metadata={},
            )
        )
        await session.commit()
        later = await real_review_context(session, db, plan["result_content_md"])
        assert correction.REVIEW_KEY not in later


async def test_existing_exact_text_edit_stays_candidate_only(db):
    async with db.sessions() as session:
        with pytest.raises(ValueError, match="current_unadopted_required"):
            await prepare_exact_draft_text_edit(
                session,
                project_id=db.book[0],
                chapter_id=db.book[1],
                draft_id=db.book[2],
                body_sha256=text_hash(BODY),
                edits=EDITS,
            )


@pytest.mark.parametrize("retained_binding", ["pointer", "origin"])
async def test_missing_own_marker_with_current_correction_binding_fails_closed(
    db, retained_binding
):
    plan, _, result = await issue(db)
    async with db.sessions() as session:
        candidate = await session.get(ChapterDraftVersionModel, UUID(result["candidate_id"]))
        chapter = await session.get(ChapterModel, db.book[1])
        metadata = deepcopy(candidate.promotion_metadata)
        metadata.pop(correction.KEY)
        if retained_binding == "pointer":
            metadata.pop("origin")
        else:
            chapter.metadata_json = {
                key: value
                for key, value in chapter.metadata_json.items()
                if key != correction.REVIEW_KEY
            }
        candidate.promotion_metadata = metadata
        await session.commit()
        before = await inventory(session)
        with pytest.raises(ValueError, match="marker_missing"):
            await context(session, db, plan["result_content_md"])
        with pytest.raises(ValueError, match="marker_missing"):
            await real_review_context(session, db, plan["result_content_md"])
        assert await inventory(session) == before


@pytest.mark.parametrize("state", ["validated", "missing_pointer", "unmarked"])
async def test_real_fanqie_entry_routes_exact_correction_to_review_only(db, monkeypatch, state):
    from bestseller.services import fanqie_short_pipeline as fanqie

    plan, _, result = await issue(db)
    calls = []

    class TerminalSentinelError(Exception):
        pass

    async with db.sessions() as session:
        connection = await session.connection()
        ddl = str(
            CreateTable(BookProductionControlModel.__table__).compile(dialect=connection.dialect)
        )
        await connection.exec_driver_sql(ddl.replace("::jsonb", ""))
        project = await session.get(ProjectModel, db.book[0])
        session.add(
            BookProductionControlModel(project_id=project.id, desired_state="run", command_serial=1)
        )
        candidate = await session.get(ChapterDraftVersionModel, UUID(result["candidate_id"]))
        chapter = await session.get(ChapterModel, db.book[1])
        if state == "missing_pointer":
            chapter.metadata_json = {
                key: value
                for key, value in chapter.metadata_json.items()
                if key != correction.REVIEW_KEY
            }
        elif state == "unmarked":
            candidate.is_current = False
            await session.flush()
            candidate = ChapterDraftVersionModel(
                project_id=db.book[0],
                chapter_id=db.book[1],
                version_no=3,
                content_md=plan["result_content_md"],
                is_current=True,
                promotion_state="candidate",
                promotion_metadata={},
            )
            session.add(candidate)
        await session.commit()
        before = await inventory(session)

        async def terminal_pipeline(actual_session, settings, slug, number, **kwargs):
            assert actual_session is session and slug == project.slug and number == 1
            calls.append(kwargs)
            assert row_data(candidate) == next(
                row for row in before["chapter_draft_versions"] if row["id"] == str(candidate.id)
            )
            assert candidate.is_current and candidate.llm_run_id is None
            assert candidate.content_md == plan["result_content_md"]
            expected = {
                "requested_by": "correction-routing-test",
                "export_markdown": False,
                "progress": None,
            }
            if state == "validated":
                expected.update(
                    chapter_first=True,
                    resume_existing_draft_only=True,
                    supersede_pending_rewrites=False,
                    review_source_binding={
                        "draft_id": result["candidate_id"],
                        "body_hash": plan["result_body_sha256"],
                    },
                )
            assert kwargs == expected
            raise TerminalSentinelError("stop at pipeline entry before payment")

        monkeypatch.setattr(fanqie, "run_chapter_pipeline", terminal_pipeline)
        expected_exception = ValueError if state == "missing_pointer" else TerminalSentinelError
        expected_message = (
            "pointer_changed" if state == "missing_pointer" else "stop at pipeline entry"
        )
        with pytest.raises(expected_exception, match=expected_message):
            await fanqie._run_fanqie_segments(
                session, load_settings(), project, requested_by="correction-routing-test"
            )
        assert len(calls) == (0 if state == "missing_pointer" else 1)
        assert await inventory(session) == before
