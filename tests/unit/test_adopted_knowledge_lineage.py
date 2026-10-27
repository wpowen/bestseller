"""Adopted-body knowledge boundaries exercised with real isolated SQL writes."""

from __future__ import annotations

import hashlib
import json
import os
from uuid import uuid4

import pytest
from sqlalchemy import event, select, text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.schema import CreateTable

from bestseller.infra.db.models import (
    AntagonistPlanModel,
    CanonFactModel,
    ChapterDraftVersionModel,
    ChapterModel,
    ChapterStateSnapshotModel,
    CharacterModel,
    CharacterStateSnapshotModel,
    ClueModel,
    EmotionTrackModel,
    PlotArcModel,
    PlanningArtifactVersionModel,
    ProjectModel,
    QualityScoreModel,
    RelationshipEventModel,
    RelationshipModel,
    ReviewReportModel,
    TimelineEventModel,
    WorldRuleModel,
)
from bestseller.services import continuity, feedback
from bestseller.services.llm import LLMCompletionResult
from bestseller.settings import load_settings

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@compiles(JSONB, "sqlite")
def _jsonb(_type, _compiler, **_kw):
    return "JSON"


@compiles(PGUUID, "sqlite")
def _uuid_as_text(_type, _compiler, **_kw):
    # PostgreSQL UUID values are opaque identifiers. SQLite's unknown UUID
    # type has NUMERIC affinity and corrupts numeric/exponent-looking hex.
    # TEXT preserves the exact existing UUID bind/result contract in fixtures.
    return "TEXT"


@pytest.fixture
async def db(tmp_path):
    path = tmp_path / "adopted_knowledge_test.sqlite3"
    assert "test" in path.name
    use_pg = os.environ.get("BESTSELLER_LINEAGE_PG") == "1"
    url = (
        make_url(load_settings().database.url)
        if use_pg
        else make_url(f"sqlite+aiosqlite:///{path}")
    )
    if use_pg:
        assert url.database == "bestseller_repair_test_20261015"
    engine = create_async_engine(url)
    if not use_pg:
        async with engine.begin() as conn:
            for model in (
                ProjectModel,
                PlanningArtifactVersionModel,
                ChapterModel,
                ChapterDraftVersionModel,
                ChapterStateSnapshotModel,
                CanonFactModel,
                TimelineEventModel,
                CharacterModel,
                CharacterStateSnapshotModel,
                PlotArcModel,
                ClueModel,
                AntagonistPlanModel,
                QualityScoreModel,
                ReviewReportModel,
                RelationshipEventModel,
                EmotionTrackModel,
                RelationshipModel,
                WorldRuleModel,
            ):
                ddl = str(CreateTable(model.__table__).compile(dialect=engine.dialect))
                await conn.exec_driver_sql(ddl.replace("::jsonb", ""))
    async with engine.connect() as conn:
        if use_pg:
            assert (
                await conn.scalar(text("select current_database()"))
                == "bestseller_repair_test_20261015"
            )
            await conn.rollback()
        transaction = await conn.begin()
        session = async_sessionmaker(
            conn, expire_on_commit=False, join_transaction_mode="create_savepoint"
        )()
        project = ProjectModel(
            slug=f"lineage-test-{uuid4().hex[:12]}",
            title="test",
            genre="test",
            target_word_count=10000,
            target_chapters=4,
        )
        session.add(project)
        await session.flush()
        chapter = ChapterModel(
            project_id=project.id, chapter_number=1, chapter_goal="test", metadata_json={}
        )
        session.add(chapter)
        await session.flush()
        draft = ChapterDraftVersionModel(
            project_id=project.id,
            chapter_id=chapter.id,
            version_no=1,
            content_md="阿青把钥匙放在桌上。",
            promotion_state="promoted",
            is_current=True,
            promotion_metadata={},
        )
        session.add(draft)
        await session.commit()
        yield session, project, chapter, draft
        await session.close()
        await transaction.rollback()
    await engine.dispose()


def stub_llm(monkeypatch, module, payload, on_call=None):
    calls = []

    async def complete(session, settings, request):
        assert not session.in_nested_transaction(), "Paid boundary must be outside a savepoint"
        calls.append(request)
        if on_call:
            await on_call(session)
        return LLMCompletionResult(
            content=json.dumps(payload, ensure_ascii=False),
            provider="test",
            model_name="test",
            llm_run_id=uuid4(),
        )

    monkeypatch.setattr(module, "complete_text", complete)
    return calls


def canon(evidence="阿青把钥匙放在桌上。"):
    return {
        "canon_facts": [
            {
                "subject": "钥匙",
                "predicate": "location",
                "value": "桌上",
                "evidence": evidence,
                "confidence": 0.85,
            }
        ]
    }


async def extract(db, **kw):
    session, project, chapter, draft = db
    return await feedback.extract_chapter_feedback(
        session,
        load_settings(),
        project_id=project.id,
        chapter=chapter,
        chapter_md=draft.content_md,
        source_chapter_draft_version_id=draft.id,
        **kw,
    )


@pytest.mark.parametrize("invalid", ["missing", "candidate", "wrong_body", "wrong_chapter"])
async def test_feedback_rejects_unadopted_source_before_payment(db, monkeypatch, invalid):
    session, project, chapter, draft = db
    calls = stub_llm(monkeypatch, feedback, canon())
    source = draft.id
    body = draft.content_md
    if invalid == "missing":
        source = None
    elif invalid == "candidate":
        draft.promotion_state = "candidate"
    elif invalid == "wrong_body":
        body += "另一版结尾。"
    else:
        other = ChapterModel(project_id=project.id, chapter_number=2, chapter_goal="other")
        session.add(other)
        await session.flush()
        draft.chapter_id = other.id
    await session.flush()
    result = await feedback.extract_chapter_feedback(
        session,
        load_settings(),
        project_id=project.id,
        chapter=chapter,
        chapter_md=body,
        source_chapter_draft_version_id=source,
    )
    assert result.extraction_status == "rejected_source"
    assert not calls
    assert not list(await session.scalars(select(CanonFactModel)))
    assert chapter.metadata_json["knowledge_feedback_receipts"][-1]["status"] == "rejected_source"


async def test_feedback_rechecks_source_after_payment(db, monkeypatch):
    async def change(session):
        db[3].content_md += "稿件已变化。"
        await session.flush()

    calls = stub_llm(monkeypatch, feedback, canon(), change)
    result = await extract(db)
    assert len(calls) == 1
    assert result.extraction_status == "rejected_source"
    assert not list(await db[0].scalars(select(CanonFactModel)))


async def test_feedback_receipt_and_idempotency_survive_session_refresh(db, monkeypatch):
    session, project, chapter, draft = db
    calls = stub_llm(monkeypatch, feedback, canon())
    first = await extract(db, workflow_run_id=uuid4(), step_run_id=uuid4())
    await session.commit()
    await session.refresh(chapter)
    second = await extract(db)
    assert len(calls) == 1
    assert first.canon_facts_created == 1
    assert second.extraction_status == "already_applied"
    rows = list(await session.scalars(select(CanonFactModel)))
    assert len(rows) == 1
    lineage = rows[0].value_json["source_lineage"]
    assert lineage["source_chapter_draft_version_id"] == str(draft.id)
    assert lineage["source_body_sha256"] == hashlib.sha256(draft.content_md.encode()).hexdigest()
    assert lineage["source_version_no"] == 1
    assert lineage["llm_run_id"] == str(first.llm_run_id)
    assert lineage["operation_id"] == calls[0].operation_id
    assert float(rows[0].confidence) == 0.85


@pytest.mark.parametrize("quote", ["", "正文不存在的引文"])
async def test_missing_or_fabricated_canon_evidence_stays_candidate(db, monkeypatch, quote):
    calls = stub_llm(monkeypatch, feedback, canon(quote))
    result = await extract(db)
    assert len(calls) == 1
    assert result.canon_facts_created == 0
    assert not list(await db[0].scalars(select(CanonFactModel)))
    receipt = db[2].metadata_json["knowledge_feedback_receipts"][-1]
    assert receipt["candidate_canon_facts"][0]["evidence"] == quote


async def test_new_body_is_not_hidden_by_old_receipt_and_preserves_supersession(db, monkeypatch):
    calls = stub_llm(monkeypatch, feedback, canon())
    await extract(db)
    db[3].content_md += "这时钟响了。"
    await db[0].flush()
    result = await extract(db)
    assert len(calls) == 2
    assert result.canon_facts_created == 1
    rows = list(await db[0].scalars(select(CanonFactModel).order_by(CanonFactModel.created_at)))
    assert len(rows) == 2
    assert rows[0].is_current is False
    assert rows[1].supersedes_fact_id == rows[0].id
    assert len(db[2].metadata_json["knowledge_feedback_receipts"]) == 2


async def snapshot(db, **kw):
    session, project, chapter, draft = db
    return await continuity.extract_chapter_state_snapshot(
        session,
        load_settings(),
        project_id=project.id,
        chapter=chapter,
        chapter_md=draft.content_md,
        source_chapter_draft_version_id=draft.id,
        **kw,
    )


async def previous(db):
    return await continuity.load_previous_chapter_snapshot(
        db[0], project_id=db[1].id, current_chapter_number=2
    )


async def test_snapshot_flag_cannot_promote_candidate_or_trigger_payment(db, monkeypatch):
    db[3].promotion_state = "candidate"
    await db[0].flush()
    calls = stub_llm(monkeypatch, continuity, {"facts": []})
    row = await snapshot(db, source_is_promoted=True)
    assert not calls
    assert row.extraction_status == "rejected_source"
    assert await previous(db) is None


async def test_snapshot_load_checks_current_source_and_full_body(db, monkeypatch):
    calls = stub_llm(
        monkeypatch, continuity, {"facts": [{"name": "钥匙", "value": "桌上", "kind": "location"}]}
    )
    row = await snapshot(db)  # actual promoted row is authoritative, flag is irrelevant
    assert row.extraction_status == "ok_promoted"
    assert await previous(db) is not None
    await snapshot(db)
    assert len(calls) == 1
    db[3].content_md += "正文原地被修改。"
    await db[0].flush()
    with pytest.raises(RuntimeError, match="source_context_unavailable: previous_chapter_snapshot"):
        await previous(db)
    assert not await continuity.snapshot_evidence_current(
        db[0], project_id=db[1].id, chapter_number=1
    )
    await snapshot(db)
    assert len(calls) == 2
    assert len(row.facts["snapshot_history"]) == 1


async def test_snapshot_rechecks_source_after_payment(db, monkeypatch):
    async def change(session):
        db[3].promotion_state = "superseded"
        await session.flush()

    calls = stub_llm(monkeypatch, continuity, {"facts": []}, change)
    row = await snapshot(db, source_is_promoted=True)
    assert len(calls) == 1
    assert row.extraction_status == "rejected_source"
    assert await previous(db) is None


async def test_timeline_lineage_and_retry_do_not_duplicate_events(db, monkeypatch):
    payload = {
        "timeline_events": [
            {"event_name": "放下钥匙", "story_time_label": "午后", "evidence": db[3].content_md}
        ]
    }
    calls = stub_llm(monkeypatch, feedback, payload)
    result = await extract(db)
    await extract(db)
    rows = list(await db[0].scalars(select(TimelineEventModel)))
    assert result.timeline_events_created == 1
    assert len(rows) == len(calls) == 1
    assert (
        rows[0].metadata_json["source_lineage"]["source_body_sha256"]
        == result.source_lineage["source_body_sha256"]
    )


async def test_historical_canon_repair_cannot_supersede_future_fact(db, monkeypatch):
    db[0].add(
        CanonFactModel(
            project_id=db[1].id,
            subject_type="world",
            subject_label="钥匙",
            predicate="location",
            fact_type="extracted",
            value_json={"value": "船上"},
            valid_from_chapter_no=3,
            is_current=True,
        )
    )
    await db[0].flush()
    stub_llm(monkeypatch, feedback, canon())
    result = await extract(db)
    assert result.canon_facts_created == 0
    assert result.candidate_canon_facts[0]["reason"] == "historical_writeback"
    rows = list(await db[0].scalars(select(CanonFactModel)))
    assert len(rows) == 1 and rows[0].is_current


async def test_fresh_snapshot_replacement_preserves_legacy_and_engine_history(db, monkeypatch):
    session, project, chapter, draft = db
    session.add(
        ChapterStateSnapshotModel(
            project_id=project.id,
            chapter_id=chapter.id,
            chapter_number=chapter.chapter_number,
            extraction_status="ok_promoted",
            facts={"facts": [], "story_engine": {"receipt_id": "old-engine"}},
            raw_extraction="legacy payload",
        )
    )
    await session.flush()
    with pytest.raises(RuntimeError, match="source_context_unavailable: previous_chapter_snapshot"):
        await previous(db)
    stub_llm(monkeypatch, continuity, {"facts": []})
    row = await snapshot(db)
    assert row.facts["story_engine"] == {"receipt_id": "old-engine"}
    assert row.facts["snapshot_history"][0]["raw_extraction"] == "legacy payload"


async def test_superseded_snapshot_stops_being_authority(db, monkeypatch):
    stub_llm(monkeypatch, continuity, {"facts": []})
    await snapshot(db)
    db[3].promotion_state = "superseded"
    await db[0].flush()
    assert await previous(db) is None


async def test_formal_promoted_pointer_is_independent_of_working_candidate(db, monkeypatch):
    session, project, chapter, draft = db
    draft.is_current = False
    await session.flush()
    session.add(
        ChapterDraftVersionModel(
            project_id=project.id,
            chapter_id=chapter.id,
            version_no=2,
            content_md="尚未采用的新候选正文。",
            promotion_state="candidate",
            is_current=True,
        )
    )
    await session.flush()
    stub_llm(monkeypatch, feedback, canon())
    stub_llm(monkeypatch, continuity, {"facts": []})
    assert (await extract(db)).canon_facts_created == 1
    assert (await snapshot(db)).extraction_status == "ok_promoted"
    assert await previous(db) is not None


async def test_new_promoted_draft_with_identical_body_has_its_own_receipt(db, monkeypatch):
    session, project, chapter, draft = db
    calls = stub_llm(monkeypatch, feedback, canon())
    await extract(db)
    draft.promotion_state = "superseded"
    draft.is_current = False
    await session.flush()
    new_draft = ChapterDraftVersionModel(
        project_id=project.id,
        chapter_id=chapter.id,
        version_no=2,
        content_md=draft.content_md,
        promotion_state="promoted",
        is_current=True,
    )
    session.add(new_draft)
    await session.flush()
    result = await extract((session, project, chapter, new_draft))
    assert len(calls) == 2
    assert result.canon_facts_created == 1
    assert result.source_lineage["source_version_no"] == 2


async def test_failed_snapshot_keeps_raw_candidate_and_cannot_be_authority(db, monkeypatch):
    stub_llm(monkeypatch, continuity, {"bad_payload": True})
    row = await snapshot(db)
    assert row.extraction_status.startswith("failed:")
    assert row.raw_extraction
    assert len(row.facts["snapshot_contract"]["llm_operations"]) == 2
    with pytest.raises(RuntimeError, match="source_context_unavailable: previous_chapter_snapshot"):
        await previous(db)
    assert not await continuity.snapshot_evidence_current(
        db[0], project_id=db[1].id, chapter_number=1
    )


async def test_late_rejected_snapshot_cannot_erase_current_authority(db, monkeypatch):
    stub_llm(monkeypatch, continuity, {"facts": []})
    row = await snapshot(db)
    rejected = await continuity.extract_chapter_state_snapshot(
        db[0],
        load_settings(),
        project_id=db[1].id,
        chapter=db[2],
        chapter_md="错误的旧版正文",
        source_chapter_draft_version_id=db[3].id,
        source_is_promoted=True,
    )
    assert rejected.extraction_status == "rejected_source"
    await db[0].refresh(row)
    assert row.extraction_status == "ok_promoted"
    assert row.facts["snapshot_attempts"][-1]["extraction_status"] == "rejected_source"
    assert await previous(db) is not None


async def test_empty_new_body_retires_removed_knowledge_without_deleting_history(db, monkeypatch):
    payload = {
        **canon(),
        "timeline_events": [{"event_name": "放下钥匙", "evidence": db[3].content_md}],
    }
    stub_llm(monkeypatch, feedback, payload)
    await extract(db)
    db[3].content_md = "她转身离开，什么也没有留下。"
    await db[0].flush()
    stub_llm(monkeypatch, feedback, {})
    assert (await extract(db)).extraction_status == "empty"
    facts = list(await db[0].scalars(select(CanonFactModel)))
    events = list(await db[0].scalars(select(TimelineEventModel)))
    assert len(facts) == len(events) == 1
    assert not facts[0].is_current
    assert events[0].metadata_json["knowledge_lineage_status"] == "superseded"


async def test_body_return_to_a_reuses_extraction_but_restores_current_knowledge(db, monkeypatch):
    calls = stub_llm(monkeypatch, feedback, canon())
    original = db[3].content_md
    await extract(db)
    db[3].content_md = "钥匙被放到柜里。"
    await db[0].flush()
    stub_llm(
        monkeypatch,
        feedback,
        {
            "canon_facts": [
                {
                    "subject": "钥匙",
                    "predicate": "location",
                    "value": "柜里",
                    "evidence": db[3].content_md,
                }
            ]
        },
    )
    await extract(db)
    db[3].content_md = original
    await db[0].flush()
    repeated_calls = stub_llm(monkeypatch, feedback, {"canon_facts": []})
    result = await extract(db)
    assert result.extraction_status == "ok"
    current = list(
        await db[0].scalars(select(CanonFactModel).where(CanonFactModel.is_current.is_(True)))
    )
    assert len(current) == 1 and current[0].value_json["value"] == "桌上"
    assert len(calls) == 1
    assert not repeated_calls


@pytest.mark.parametrize("module", [feedback, continuity])
async def test_budget_stop_is_never_swallowed_as_knowledge_success(db, monkeypatch, module):
    from bestseller.services.budget import ExecutionBudgetError

    async def stop(*args):
        raise ExecutionBudgetError("execution_transaction_not_releasable", "test")

    monkeypatch.setattr(module, "complete_text", stop)
    with pytest.raises(ExecutionBudgetError, match="execution_transaction_not_releasable"):
        await (extract(db) if module is feedback else snapshot(db))


@pytest.mark.parametrize("module", [feedback, continuity])
async def test_gateway_commit_requires_a_fresh_apply_time_source_check(db, monkeypatch, module):
    async def change(session):
        await session.commit()  # Real gateway releases the original transaction.
        db[3].content_md += "网关返回前正文发生了变化。"
        await session.flush()

    calls = stub_llm(monkeypatch, module, canon() if module is feedback else {"facts": []}, change)
    result = await (extract(db) if module is feedback else snapshot(db))
    assert result.extraction_status == "rejected_source"
    assert len(calls) == 1
    assert not list(await db[0].scalars(select(CanonFactModel)))


async def test_legacy_unbound_fact_is_archived_when_current_body_is_empty(db, monkeypatch):
    db[0].add(
        CanonFactModel(
            project_id=db[1].id,
            subject_type="world",
            subject_label="旧断言",
            predicate="location",
            fact_type="extracted",
            value_json={"value": "未知"},
            source_type="feedback",
            source_chapter_id=db[2].id,
            valid_from_chapter_no=1,
            is_current=True,
        )
    )
    await db[0].flush()
    stub_llm(monkeypatch, feedback, {})
    await extract(db)
    facts = list(await db[0].scalars(select(CanonFactModel)))
    assert len(facts) == 1 and not facts[0].is_current
    assert "source_body_superseded" in facts[0].notes


async def test_adopted_debt_replaces_old_formal_pointer_for_knowledge(db, monkeypatch):
    session, project, chapter, old = db
    stub_llm(monkeypatch, continuity, {"facts": []})
    await snapshot(db)
    old.is_current = False
    await session.flush()
    new = ChapterDraftVersionModel(
        project_id=project.id,
        chapter_id=chapter.id,
        version_no=2,
        content_md="她把钥匙留在柜内。",
        promotion_state="candidate",
        is_current=True,
    )
    session.add(new)
    await session.flush()
    await make_accepted_debt((session, project, chapter, new))
    calls = stub_llm(monkeypatch, feedback, canon())
    assert (await extract(db)).extraction_status == "rejected_source"
    assert not calls
    with pytest.raises(RuntimeError, match="source_context_unavailable: previous_chapter_snapshot"):
        await previous(db)


async def test_snapshot_body_return_reuses_archived_extraction(db, monkeypatch):
    calls = stub_llm(monkeypatch, continuity, {"facts": []})
    original = db[3].content_md
    await snapshot(db)
    db[3].content_md += "另一个结尾。"
    await db[0].flush()
    await snapshot(db)
    db[3].content_md = original
    await db[0].flush()
    row = await snapshot(db)
    assert len(calls) == 2
    assert row.extraction_status == "ok_promoted"
    assert await previous(db) is not None


async def test_failed_apply_rolls_back_all_facts_and_success_receipt(db, monkeypatch):
    stub_llm(monkeypatch, feedback, canon())

    async def fail_after_canon(*args, **kwargs):
        raise RuntimeError("timeline application failed")

    monkeypatch.setattr(feedback, "_apply_timeline_extractions", fail_after_canon)
    with pytest.raises(RuntimeError, match="timeline application failed"):
        await extract(db)
    assert not list(await db[0].scalars(select(CanonFactModel)))
    await db[0].refresh(db[2])
    assert not db[2].metadata_json.get("knowledge_feedback_receipts")


async def test_reusing_adopted_body_does_not_repeat_relationship_increments(db, monkeypatch):
    payload = {
        **canon(),
        "relationship_events": [
            {
                "character_a": "阿青",
                "character_b": "阿白",
                "event_description": "分享钥匙",
                "relationship_change": "trust increased",
            }
        ],
    }
    stub_llm(monkeypatch, feedback, payload)
    original = db[3].content_md
    await extract(db)
    db[3].content_md = "她独自离开。"
    await db[0].flush()
    stub_llm(monkeypatch, feedback, {})
    await extract(db)
    db[3].content_md = original
    await db[0].flush()
    await extract(db)
    rows = list(await db[0].scalars(select(RelationshipEventModel)))
    assert len(rows) == 1


async def test_knowledge_writes_are_inside_service_savepoint(db, monkeypatch):
    flushed = []

    def before_flush(session, _context, _instances):
        for row in set(session.new) | set(session.dirty):
            if isinstance(row, (CanonFactModel, ChapterStateSnapshotModel)):
                assert session.in_nested_transaction()
                flushed.append(type(row).__name__)

    event.listen(db[0].sync_session, "before_flush", before_flush)
    stub_llm(monkeypatch, feedback, canon())
    stub_llm(monkeypatch, continuity, {"facts": []})
    await extract(db)
    await snapshot(db)
    assert {"CanonFactModel", "ChapterStateSnapshotModel"} <= set(flushed)
    assert not db[0].in_nested_transaction()


async def make_accepted_debt(db):
    from bestseller.services.draft_promotion import numeric_promotion_debt_receipt
    from bestseller.services.reviews import chapter_review_body_fingerprint

    session, project, chapter, draft = db
    draft.promotion_state = "under_review"
    report = ReviewReportModel(
        project_id=project.id,
        target_type="chapter",
        target_id=chapter.id,
        reviewer_type="chapter",
        verdict="pass",
        structured_output={
            "draft_id": str(draft.id),
            "findings": [],
            "reviewed_body_fingerprint": chapter_review_body_fingerprint(draft.content_md),
        },
    )
    session.add(report)
    await session.flush()
    score = QualityScoreModel(
        project_id=project.id,
        target_type="chapter",
        target_id=chapter.id,
        chapter_draft_version_id=draft.id,
        review_report_id=report.id,
        judge_key="test",
        score_overall=0.8,
        score_goal=0.8,
        score_conflict=0.8,
        score_emotion=0.8,
        score_style=0.8,
        is_current=True,
        evidence_summary={"hard_gates_passed": True},
    )
    session.add(score)
    await session.flush()
    receipt = numeric_promotion_debt_receipt(draft, score, report)
    assert receipt
    chapter.production_state = "quality_debt"
    chapter.metadata_json = {
        "chapter_quality_debt": True,
        "numeric_promotion_debt": receipt,
        "chapter_quality_debt_reason": "chapter_promotion_ineligible",
    }
    await session.flush()
    return score, report


async def test_legitimate_accepted_debt_can_extract_feedback_and_snapshot(db, monkeypatch):
    await make_accepted_debt(db)
    stub_llm(monkeypatch, feedback, canon())
    stub_llm(monkeypatch, continuity, {"facts": []})
    result = await extract(db)
    row = await snapshot(db)
    assert result.canon_facts_created == 1
    assert result.source_lineage["adoption_state"] == "accepted_with_debt"
    assert row.extraction_status == "ok_accepted_with_debt"
    assert await previous(db) is not None


@pytest.mark.parametrize("corruption", ["body", "hard_gate", "wrong_score", "marker_only"])
async def test_debt_marker_cannot_authorize_unbound_or_blocked_prose(db, monkeypatch, corruption):
    score, report = await make_accepted_debt(db)
    if corruption == "body":
        db[3].content_md += "新结尾。"
    elif corruption == "hard_gate":
        score.evidence_summary = {"hard_gates_passed": False}
    elif corruption == "wrong_score":
        score.chapter_draft_version_id = None
    else:
        db[2].metadata_json = {"chapter_quality_debt": True}
    await db[0].flush()
    calls = stub_llm(monkeypatch, feedback, canon())
    result = await extract(db)
    assert result.extraction_status == "rejected_source"
    assert not calls
