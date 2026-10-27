"""Adopted knowledge must follow the selected application, never an old promoted label."""

from __future__ import annotations

from copy import deepcopy
from uuid import uuid4

import pytest
from sqlalchemy import select
from test_adopted_knowledge_lineage import db as db

from bestseller.domain.feedback import ChapterFeedbackPayload, ChapterFeedbackResult
from bestseller.infra.db.models import CanonFactModel, ChapterDraftVersionModel, TimelineEventModel
from bestseller.services import feedback

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


async def apply_payload(session, project, chapter, draft, *, has_facts):
    lineage, rejection = await feedback.validate_adopted_knowledge_source(
        session,
        project_id=project.id,
        chapter_id=chapter.id,
        source_chapter_draft_version_id=draft.id,
        chapter_md=draft.content_md,
    )
    assert rejection is None
    payload = ChapterFeedbackPayload.model_validate(
        {
            "canon_facts": [
                {
                    "subject": "钥匙",
                    "predicate": "location",
                    "value": "原文观察",
                    "evidence": draft.content_md,
                }
            ],
            "timeline_events": [{"event_name": "放好钥匙", "evidence": draft.content_md}],
        }
        if has_facts
        else {}
    )
    return await feedback._apply_verified_feedback(
        session,
        chapter=chapter,
        chapter_md=draft.content_md,
        result=ChapterFeedbackResult(
            project_id=project.id,
            chapter_id=chapter.id,
            chapter_number=chapter.chapter_number,
            source_lineage=lineage,
        ),
        payload=payload,
        llm_receipts=[
            {
                "operation_id": f"isolated:{draft.id}",
                "llm_run_id": str(uuid4()),
                "fallback_used": False,
            }
        ],
    )


async def replace_with_promoted_b(db, *, has_facts, b_has_facts=True):
    session, project, chapter, a = db
    await apply_payload(session, project, chapter, a, has_facts=has_facts)
    a.is_current = False
    await session.flush()
    b = ChapterDraftVersionModel(
        project_id=project.id,
        chapter_id=chapter.id,
        version_no=2,
        content_md="阿青把钥匙留在柜内。",
        promotion_state="promoted",
        is_current=True,
        promotion_metadata={},
    )
    session.add(b)
    await session.flush()
    await apply_payload(session, project, chapter, b, has_facts=b_has_facts)
    return b


@pytest.mark.parametrize("has_facts", [False, True])
async def test_old_noncurrent_promoted_source_is_rejected_after_b_application(db, has_facts):
    session, project, chapter, a = db
    b = await replace_with_promoted_b(db, has_facts=has_facts)
    metadata = deepcopy(chapter.metadata_json)
    _, rejection = await feedback.validate_adopted_knowledge_source(
        session,
        project_id=project.id,
        chapter_id=chapter.id,
        source_chapter_draft_version_id=a.id,
        chapter_md=a.content_md,
    )
    assert rejection is not None, f"old A {a.id} was admitted while current B {b.id} is promoted"
    assert chapter.metadata_json == metadata


@pytest.fixture(autouse=True)
def no_models(monkeypatch):
    from bestseller.services import continuity, llm
    from bestseller.services import knowledge_evidence_reconciliation as reconciliation

    async def forbidden(*args, **kwargs):
        pytest.fail("Knowledge source tests must not call models")

    for module in (feedback, continuity, llm, reconciliation):
        monkeypatch.setattr(module, "complete_text", forbidden)


async def active_sources(session, chapter):
    facts = list(
        await session.scalars(
            select(CanonFactModel).where(
                CanonFactModel.project_id == chapter.project_id,
                CanonFactModel.source_chapter_id == chapter.id,
                CanonFactModel.is_current.is_(True),
            )
        )
    )
    events = [
        event
        for event in await session.scalars(
            select(TimelineEventModel).where(
                TimelineEventModel.project_id == chapter.project_id,
                TimelineEventModel.chapter_id == chapter.id,
            )
        )
        if event.metadata_json.get("knowledge_lineage_status") == "current"
    ]
    return facts, events


@pytest.mark.parametrize("a_has_facts", [False, True])
@pytest.mark.parametrize("b_has_facts", [False, True])
async def test_a_b_a_requires_real_readoption_and_restores_exact_latest_application(
    db, a_has_facts, b_has_facts
):
    from bestseller.settings import load_settings

    session, project, chapter, a = db
    b = await replace_with_promoted_b(db, has_facts=a_has_facts, b_has_facts=b_has_facts)
    before = deepcopy(chapter.metadata_json["knowledge_feedback_receipts"])
    counts = tuple(map(len, await active_sources(session, chapter)))
    assert counts == (int(b_has_facts), int(b_has_facts))
    rejected = await feedback.extract_chapter_feedback(
        session,
        load_settings(),
        project_id=project.id,
        chapter=chapter,
        source_chapter_draft_version_id=a.id,
        chapter_md=a.content_md,
    )
    assert rejected.extraction_status == "rejected_source"
    assert tuple(map(len, await active_sources(session, chapter))) == counts
    # Genuine re-adoption differs from merely presenting the historical A receipt.
    b.is_current = False
    b.promotion_state = "superseded"
    await session.flush()
    a.is_current = True
    await session.flush()
    restored = await feedback.extract_chapter_feedback(
        session,
        load_settings(),
        project_id=project.id,
        chapter=chapter,
        source_chapter_draft_version_id=a.id,
        chapter_md=a.content_md,
    )
    assert restored.extraction_status in {"ok", "empty"}
    facts, events = await active_sources(session, chapter)
    assert len(facts) == len(events) == int(a_has_facts)
    for fact in facts:
        assert fact.value_json["source_lineage"]["source_chapter_draft_version_id"] == str(a.id)
    for event in events:
        assert event.metadata_json["source_lineage"]["source_chapter_draft_version_id"] == str(a.id)
    history = chapter.metadata_json["knowledge_feedback_receipts"]
    assert history[: len(before)] == before
    assert history[-1]["source_lineage"]["source_chapter_draft_version_id"] == str(a.id)
    assert (
        await feedback.extract_chapter_feedback(
            session,
            load_settings(),
            project_id=project.id,
            chapter=chapter,
            source_chapter_draft_version_id=a.id,
            chapter_md=a.content_md,
        )
    ).extraction_status == "already_applied"
    assert len(chapter.metadata_json["knowledge_feedback_receipts"]) == len(history)


@pytest.mark.parametrize("other_project", [False, True])
async def test_same_body_in_other_book_or_chapter_never_authorizes_source(db, other_project):
    from bestseller.infra.db.models import ChapterModel, ProjectModel

    session, project, chapter, a = db
    owner = project
    if other_project:
        owner = ProjectModel(
            slug=f"source-isolation-{uuid4().hex}",
            title="异书: 海港诊所",
            genre="test",
            target_word_count=12000,
            target_chapters=4,
        )
        session.add(owner)
        await session.flush()
    other = ChapterModel(
        project_id=owner.id,
        chapter_number=1 if other_project else 2,
        chapter_goal="独立知识边界",
        metadata_json={},
    )
    session.add(other)
    await session.flush()
    foreign = ChapterDraftVersionModel(
        project_id=owner.id,
        chapter_id=other.id,
        version_no=1,
        content_md=a.content_md,
        promotion_state="promoted",
        is_current=True,
    )
    session.add(foreign)
    await session.flush()
    await apply_payload(session, owner, other, foreign, has_facts=True)
    foreign_before = deepcopy(other.metadata_json)
    _, rejection = await feedback.validate_adopted_knowledge_source(
        session,
        project_id=project.id,
        chapter_id=chapter.id,
        source_chapter_draft_version_id=foreign.id,
        chapter_md=foreign.content_md,
    )
    assert rejection == "source_not_found_or_wrong_owner"
    b = await replace_with_promoted_b(db, has_facts=True)
    assert (
        await feedback.validate_adopted_knowledge_source(
            session,
            project_id=project.id,
            chapter_id=chapter.id,
            source_chapter_draft_version_id=a.id,
            chapter_md=a.content_md,
        )
    )[1] == "source_replaced_by_adopted_promoted"
    assert other.metadata_json == foreign_before
    facts, events = await active_sources(session, other)
    assert len(facts) == len(events) == 1
    assert all(
        f.value_json["source_lineage"]["source_chapter_draft_version_id"] == str(foreign.id)
        for f in facts
    )
    assert b.project_id == project.id


async def test_working_current_candidate_keeps_unique_promoted_adoption_readable(db):
    session, project, chapter, adopted = db
    adopted.is_current = False
    await session.flush()
    working = ChapterDraftVersionModel(
        project_id=project.id,
        chapter_id=chapter.id,
        version_no=2,
        content_md="未被采用的工作候选。",
        promotion_state="candidate",
        is_current=True,
    )
    session.add(working)
    await session.flush()
    lineage, rejection = await feedback.validate_adopted_knowledge_source(
        session,
        project_id=project.id,
        chapter_id=chapter.id,
        source_chapter_draft_version_id=adopted.id,
        chapter_md=adopted.content_md,
        lock=False,
    )
    assert rejection is None and lineage["adoption_state"] == "promoted"
    assert (
        await feedback.validate_adopted_knowledge_source(
            session,
            project_id=project.id,
            chapter_id=chapter.id,
            source_chapter_draft_version_id=working.id,
            chapter_md=working.content_md,
        )
    )[1] == "source_not_adopted"


async def test_ambiguous_legacy_promoted_sources_do_not_guess_from_version_or_facts(db):
    session, project, chapter, a = db
    b = await replace_with_promoted_b(db, has_facts=True)
    b.is_current = False
    await session.flush()
    session.add(
        ChapterDraftVersionModel(
            project_id=project.id,
            chapter_id=chapter.id,
            version_no=3,
            content_md="工作候选不构成采用。",
            promotion_state="candidate",
            is_current=True,
        )
    )
    await session.flush()
    for draft in (a, b):
        assert (
            await feedback.validate_adopted_knowledge_source(
                session,
                project_id=project.id,
                chapter_id=chapter.id,
                source_chapter_draft_version_id=draft.id,
                chapter_md=draft.content_md,
            )
        )[1] == "source_not_adopted"


@pytest.mark.parametrize("b_has_facts", [False, True])
async def test_cached_actual_b_repairs_legacy_wrong_a_projection_without_models(db, b_has_facts):
    from bestseller.settings import load_settings

    session, project, chapter, a = db
    b = await replace_with_promoted_b(db, has_facts=True, b_has_facts=b_has_facts)
    prior = deepcopy(chapter.metadata_json["knowledge_feedback_receipts"])
    # Preserved legacy corruption fixture: old A was wrongly re-applied after B.
    for row in await session.scalars(
        select(CanonFactModel).where(CanonFactModel.source_chapter_id == chapter.id)
    ):
        row.is_current = row.value_json["source_lineage"]["source_chapter_draft_version_id"] == str(
            a.id
        )
    for row in await session.scalars(
        select(TimelineEventModel).where(TimelineEventModel.chapter_id == chapter.id)
    ):
        metadata = dict(row.metadata_json)
        metadata["knowledge_lineage_status"] = (
            "current"
            if metadata["source_lineage"]["source_chapter_draft_version_id"] == str(a.id)
            else "superseded"
        )
        row.metadata_json = metadata
    chapter.metadata_json = {
        **chapter.metadata_json,
        "knowledge_feedback_receipts": [
            *prior,
            {**deepcopy(prior[0]), "recorded_at": "legacy-wrong-a-application"},
        ],
    }
    await session.flush()
    all_fact_ids = set(await session.scalars(select(CanonFactModel.id)))
    result = await feedback.extract_chapter_feedback(
        session,
        load_settings(),
        project_id=project.id,
        chapter=chapter,
        source_chapter_draft_version_id=b.id,
        chapter_md=b.content_md,
    )
    assert result.extraction_status in {"ok", "empty"}
    facts, events = await active_sources(session, chapter)
    assert len(facts) == len(events) == int(b_has_facts)
    assert all_fact_ids <= set(await session.scalars(select(CanonFactModel.id)))
    for fact in facts:
        assert fact.value_json["source_lineage"]["source_chapter_draft_version_id"] == str(b.id)
    for event in events:
        assert event.metadata_json["source_lineage"]["source_chapter_draft_version_id"] == str(b.id)
    assert chapter.metadata_json["knowledge_feedback_receipts"][: len(prior)] == prior
