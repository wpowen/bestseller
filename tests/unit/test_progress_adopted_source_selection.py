"""Adoption selection and real SQLite progress must agree on exact source ownership."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime, timedelta
import hashlib
import json
from uuid import uuid4

import pytest
from sqlalchemy import event, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker
from test_adopted_knowledge_lineage import db as db
from test_adopted_knowledge_lineage import make_accepted_debt

from bestseller.infra.db.models import (
    ChapterDraftVersionModel,
    ChapterModel,
    ChapterStateSnapshotModel,
    ProjectModel,
)
from bestseller.services.adopted_chapter_source import select_adopted_chapter_source
from bestseller.services.chapter_word_count_truth import authoritative_zh_word_count
from bestseller.services import continuity
from bestseller.web.server import _load_adopted_chapter_progress

pytestmark = pytest.mark.unit
EARLY = datetime(2026, 10, 1, tzinfo=UTC)
LATE = EARLY + timedelta(days=2)


@pytest.fixture(autouse=True)
def sqlite_and_no_models(monkeypatch):
    from bestseller.services import continuity, feedback, llm
    from bestseller.services import knowledge_evidence_reconciliation as reconciliation

    monkeypatch.setenv("BESTSELLER_LINEAGE_PG", "0")

    async def forbidden(*_args, **_kwargs):
        pytest.fail("Progress source-selection tests must not call models")

    for module in (continuity, feedback, llm, reconciliation):
        monkeypatch.setattr(module, "complete_text", forbidden)


def _draft(chapter, version, *, current=False, when=None, state="promoted", body=None):
    return ChapterDraftVersionModel(
        id=uuid4(), project_id=chapter.project_id, chapter_id=chapter.id,
        version_no=version, content_md=body or "采用正文。" * version,
        promotion_state=state, is_current=current, promoted_at=when,
    )


def _lineage(draft):
    return {
        "schema": "adopted-knowledge.v1",
        "project_id": str(draft.project_id), "chapter_id": str(draft.chapter_id),
        "source_chapter_draft_version_id": str(draft.id),
        "source_version_no": draft.version_no,
        "source_body_sha256": hashlib.sha256(draft.content_md.encode()).hexdigest(),
    }


async def _admit_snapshot(session, chapter, draft):
    binding = {
        "project_id": chapter.project_id,
        "chapter_id": chapter.id,
        "source_chapter_draft_version_id": draft.id,
        "chapter_md": draft.content_md,
    }
    proof = await continuity.validate_snapshot_epistemic_view(session, **binding)
    result = await continuity.apply_snapshot_epistemic_view(
        session, **binding,
        expected_snapshot_sha256=proof["snapshot_sha256"],
        admission_receipt=proof["admission_receipt"],
        actor="offline-progress-fixture",
        reason="Admit an exact-source fixture without changing its literal extraction",
    )
    assert result["status"] == "applied"


async def _seed_knowledge(session, chapter, draft, *, mode="promoted"):
    source = _lineage(draft)
    chapter.metadata_json = {
        **(chapter.metadata_json or {}),
        "knowledge_feedback_receipts": [
            {"status": "empty", "source_lineage": source, "candidate_payload": {}},
        ],
        "story_bible_update_receipts": [{"status": "empty", "source_lineage": source}],
    }
    session.add(ChapterStateSnapshotModel(
        project_id=chapter.project_id, chapter_id=chapter.id,
        chapter_number=chapter.chapter_number, extraction_status=f"ok_{mode}",
        raw_extraction=json.dumps({"facts": []}),
        extraction_model="offline-fixture",
        facts={"facts": [], "snapshot_contract": {**source, "is_usable": True}},
    ))
    await session.flush()
    selected, _ = await select_adopted_chapter_source(session, chapter=chapter, lock=False)
    if selected is draft:
        await _admit_snapshot(session, chapter, draft)
    else:
        # Invalid-source cases must remain rejected by real admission too.
        with pytest.raises(ValueError, match="snapshot_epistemic_source_rejected:"):
            await _admit_snapshot(session, chapter, draft)


@pytest.mark.parametrize("owner", ["project", "chapter", "both"])
@pytest.mark.parametrize("reverse", [False, True])
def test_choose_scopes_both_owners_despite_same_body(owner, reverse):
    from bestseller.services.adopted_chapter_source import choose_promoted_chapter_source

    chapter = ChapterModel(id=uuid4(), project_id=uuid4())
    local = _draft(chapter, 1)
    foreign = _draft(chapter, 2, current=True, when=LATE, body=local.content_md)
    if owner in {"project", "both"}:
        foreign.project_id = uuid4()
    if owner in {"chapter", "both"}:
        foreign.chapter_id = uuid4()
    rows = [local, foreign] if reverse else [foreign, local]
    before = list(rows)
    assert choose_promoted_chapter_source(
        iter(rows), project_id=chapter.project_id, chapter_id=chapter.id,
    ) is local
    assert rows == before
    assert choose_promoted_chapter_source(
        [foreign], project_id=chapter.project_id, chapter_id=chapter.id,
    ) is None


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("currents,dates,expected", [
    ((False, True), (LATE, EARLY), 1),  # current wins even over a later date
    ((True, True), (EARLY, LATE), None),
    ((False, False), (LATE, EARLY), 0),  # dated re-adoption of the older version
    ((False, False), (None, LATE), None),
    ((False, False), (EARLY, None), None),
    ((False, False), (None, None), None),
    ((False, False), (LATE, LATE), None),
])
def test_choose_current_or_unique_latest_date_without_version_guess(
    currents, dates, expected, reverse,
):
    from bestseller.services.adopted_chapter_source import choose_promoted_chapter_source

    chapter = ChapterModel(id=uuid4(), project_id=uuid4())
    promoted = [
        _draft(chapter, i + 1, current=currents[i], when=dates[i]) for i in range(2)
    ]
    rows = [*promoted, _draft(chapter, 99, current=True, state="candidate", when=LATE)]
    if reverse:
        rows.reverse()
    assert choose_promoted_chapter_source(
        rows, project_id=chapter.project_id, chapter_id=chapter.id,
    ) is (None if expected is None else promoted[expected])


def test_choose_empty_single_and_tied_historical_dates():
    from bestseller.services.adopted_chapter_source import choose_promoted_chapter_source

    chapter = ChapterModel(id=uuid4(), project_id=uuid4())
    scope = {"project_id": chapter.project_id, "chapter_id": chapter.id}
    assert choose_promoted_chapter_source([], **scope) is None
    candidate = _draft(chapter, 99, current=True, state="candidate")
    assert choose_promoted_chapter_source([candidate], **scope) is None
    one = _draft(chapter, 1)
    assert choose_promoted_chapter_source([one, candidate], **scope) is one
    one.promoted_at = LATE
    history = [_draft(chapter, 2, when=EARLY), _draft(chapter, 3, when=EARLY)]
    assert choose_promoted_chapter_source([*history, one, candidate], **scope) is one
    history[0].promoted_at = LATE
    assert choose_promoted_chapter_source([*history, one, candidate], **scope) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("receipt_matches", [False, True])
@pytest.mark.parametrize("selection", [
    "current_new", "dated_older", "missing_date", "equal_date", "current_multi", "unique",
])
async def test_sqlite_progress_matches_actual_selector(db, selection, receipt_matches):
    session, project, chapter, oldest = db
    assert session.bind.dialect.name == "sqlite"
    oldest.is_current = False
    oldest.promoted_at = EARLY
    adopted = _draft(chapter, 2, when=LATE, current=selection == "current_new")
    newer = _draft(chapter, 3, when=EARLY + timedelta(days=1))
    candidate = _draft(chapter, 4, current=selection != "current_new", state="candidate")
    expected = adopted
    if selection == "current_new":
        newer.promoted_at = LATE + timedelta(days=1)
    elif selection == "missing_date":
        oldest.promoted_at = None
        expected = None
    elif selection == "equal_date":
        newer.promoted_at = LATE
        expected = None
    elif selection == "current_multi":
        adopted.is_current = newer.is_current = True
        candidate.is_current = False
        expected = None
    elif selection == "unique":
        adopted.promotion_state = newer.promotion_state = "superseded"
        expected = oldest
    session.add_all([adopted, newer, candidate])
    await session.flush()
    evidence = expected if receipt_matches and expected is not None else candidate
    await _seed_knowledge(session, chapter, evidence)
    await session.flush()
    metadata = deepcopy(chapter.metadata_json)

    actual, receipt = await select_adopted_chapter_source(session, chapter=chapter, lock=False)
    assert actual is expected and receipt is None
    stats = (await _load_adopted_chapter_progress(session, [project.id]))[project.id]
    assert [row["draft_id"] for row in stats["chapters"]] == (
        [str(expected.id)] if expected is not None else []
    )
    count = int(expected is not None)
    assert stats["completed_chapters"] == stats["adopted_chapters"] == count
    assert stats["promoted_chapters"] == count
    assert stats["accepted_with_debt_chapters"] == 0
    assert stats["word_count_total"] == (
        authoritative_zh_word_count(expected.content_md, language="zh-CN") if count else 0
    )
    knowledge = stats["knowledge"]
    closed = int(bool(count and receipt_matches))
    assert knowledge["adopted_chapters"] == count
    assert knowledge["feedback_chapters"] == knowledge["snapshot_chapters"] == closed
    assert knowledge["closed_chapters"] == knowledge["bible"]["closed_chapters"] == closed
    assert knowledge["pending_chapters"] == count - closed
    assert knowledge["status"] == ("closed" if closed else "pending")
    assert chapter.metadata_json == metadata
    assert not session.dirty and not session.new and not session.deleted


@pytest.mark.asyncio
@pytest.mark.parametrize("other_project", [False, True])
async def test_sqlite_same_body_other_owner_cannot_close_local_knowledge(db, other_project):
    session, project, chapter, adopted = db
    assert session.bind.dialect.name == "sqlite"
    owner = project
    if other_project:
        owner = ProjectModel(
            slug=f"progress-owner-test-{uuid4().hex}", title="Other", genre="test",
            target_word_count=10000, target_chapters=1,
        )
        session.add(owner)
        await session.flush()
    other = ChapterModel(
        project_id=owner.id, chapter_number=1 if other_project else 2, chapter_goal="other",
    )
    session.add(other)
    await session.flush()
    foreign = _draft(other, 1, current=True, body=adopted.content_md)
    session.add(foreign)
    await session.flush()
    await _seed_knowledge(session, chapter, foreign)
    await session.flush()
    stats = (await _load_adopted_chapter_progress(session, [project.id]))[project.id]
    local = next(row for row in stats["chapters"] if row["number"] == chapter.chapter_number)
    assert local["draft_id"] == str(adopted.id)
    assert stats["knowledge"]["closed_chapters"] == 0
    assert stats["knowledge"]["feedback_chapters"] == stats["knowledge"]["snapshot_chapters"] == 0
    actual, receipt = await select_adopted_chapter_source(session, chapter=chapter, lock=False)
    assert actual is adopted and receipt is None


@pytest.mark.asyncio
@pytest.mark.parametrize("valid_debt", [False, True])
async def test_sqlite_numeric_debt_precedence_is_preserved(db, valid_debt):
    session, project, chapter, old = db
    assert session.bind.dialect.name == "sqlite"
    old.is_current = False
    current = _draft(chapter, 2, current=True, state="candidate")
    session.add(current)
    await session.flush()
    await make_accepted_debt((session, project, chapter, current))
    if not valid_debt:
        current.content_md += "改变正文使回执失效。"
    expected = current if valid_debt else old
    mode = "accepted_with_debt" if valid_debt else "promoted"
    await _seed_knowledge(session, chapter, expected, mode=mode)
    await session.flush()
    metadata = deepcopy(chapter.metadata_json)
    actual, receipt = await select_adopted_chapter_source(session, chapter=chapter, lock=False)
    assert actual is expected
    assert receipt == (metadata["numeric_promotion_debt"] if valid_debt else None)
    stats = (await _load_adopted_chapter_progress(session, [project.id]))[project.id]
    assert stats["chapters"][0]["draft_id"] == str(expected.id)
    assert stats["chapters"][0]["acceptance"] == mode
    assert stats["accepted_with_debt_chapters"] == int(valid_debt)
    assert stats["promoted_chapters"] == int(not valid_debt)
    assert stats["candidate_chapters"] == int(not valid_debt)
    assert stats["word_count_total"] == authoritative_zh_word_count(
        expected.content_md, language="zh-CN",
    )
    assert stats["knowledge"]["closed_chapters"] == 1
    assert chapter.metadata_json == metadata
    assert not session.dirty and not session.new and not session.deleted


@pytest.mark.asyncio
async def test_sqlite_progress_foreign_current_is_not_a_candidate(db):
    session, project, chapter, adopted = db
    assert session.bind.dialect.name == "sqlite"
    adopted.is_current = False
    owner = ProjectModel(
        slug=f"progress-foreign-test-{uuid4().hex}", title="Other", genre="test",
        target_word_count=10000, target_chapters=1,
    )
    session.add(owner)
    await session.flush()
    # Independent foreign keys permit legacy rows with a mismatched project owner.
    foreign = _draft(chapter, 0, current=True, body=adopted.content_md)
    foreign.project_id = owner.id
    session.add(foreign)
    await session.flush()
    stats = (await _load_adopted_chapter_progress(session, [project.id]))[project.id]
    assert stats["chapters"][0]["draft_id"] == str(adopted.id)
    assert stats["adopted_chapters"] == 1
    assert stats["candidate_chapters"] == stats["candidate_word_count_total"] == 0
    assert stats["word_count_total"] == authoritative_zh_word_count(
        adopted.content_md, language="zh-CN",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("pending_local_edits", [False, True])
async def test_sqlite_progress_refreshes_stale_orm_without_flushing(db, pending_local_edits):
    session, project, chapter, old = db
    assert session.bind.dialect.name == "sqlite"
    adopted = _draft(chapter, 2)
    session.add(adopted)
    await session.flush()
    await _seed_knowledge(session, chapter, old)
    await session.flush()
    snapshot = await session.scalar(select(ChapterStateSnapshotModel))
    old_metadata = deepcopy(chapter.metadata_json)
    database_body = "数据库已重新采用的正文。" * 5
    source = {**_lineage(adopted), "source_body_sha256": hashlib.sha256(
        database_body.encode(),
    ).hexdigest()}
    fresh_metadata = {
        "knowledge_feedback_receipts": [{"status": "empty", "source_lineage": source}],
        "story_bible_update_receipts": [{"status": "empty", "source_lineage": source}],
    }
    fresh_facts = {"facts": [], "snapshot_contract": {**source, "is_usable": True}}
    # Change SQL truth without updating this session's loaded identity map.
    for model, identity, values in (
        (ChapterDraftVersionModel, old.id, {"is_current": False}),
        (ChapterDraftVersionModel, adopted.id, {"is_current": True, "content_md": database_body}),
        (ChapterModel, chapter.id, {"metadata_json": fresh_metadata}),
        (ChapterStateSnapshotModel, snapshot.id, {"facts": fresh_facts}),
    ):
        await session.execute(update(model).where(model.id == identity).values(**values)
                              .execution_options(synchronize_session=False))
    # Admit SQL truth in another identity map so the caller retains stale rows.
    factory = async_sessionmaker(
        session.bind, expire_on_commit=False, join_transaction_mode="create_savepoint",
    )
    async with factory() as stored:
        stored_chapter = await stored.get(ChapterModel, chapter.id)
        stored_draft = await stored.get(ChapterDraftVersionModel, adopted.id)
        await _admit_snapshot(stored, stored_chapter, stored_draft)
        stored_snapshot = await stored.get(ChapterStateSnapshotModel, snapshot.id)
        fresh_facts = deepcopy(stored_snapshot.facts)
        await stored.commit()
    assert old.is_current and not adopted.is_current
    assert chapter.metadata_json == old_metadata
    assert snapshot.facts != fresh_facts
    if pending_local_edits:
        chapter.metadata_json = {"uncommitted": True}
        adopted.content_md = "未提交的编辑正文。"
        snapshot.facts = {"uncommitted": True}
    statements = []

    def record_statement(_conn, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)

    bind = session.sync_session.bind
    event.listen(bind, "before_cursor_execute", record_statement)
    try:
        stats = (await _load_adopted_chapter_progress(session, [project.id]))[project.id]
    finally:
        event.remove(bind, "before_cursor_execute", record_statement)
    assert [row["draft_id"] for row in stats["chapters"]] == [str(adopted.id)]
    assert stats["word_count_total"] == authoritative_zh_word_count(
        database_body, language="zh-CN",
    )
    assert stats["knowledge"]["closed_chapters"] == 1
    assert chapter.metadata_json == fresh_metadata
    assert adopted.content_md == database_body and snapshot.facts == fresh_facts
    assert statements and all(sql.lstrip().upper().startswith("SELECT") for sql in statements)
    assert not session.dirty and not session.new and not session.deleted
