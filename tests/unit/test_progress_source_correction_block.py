"""Source correction blocks apply equally to adoption and bulk UI readback."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import event
from test_adopted_knowledge_lineage import db as db
from test_adopted_knowledge_lineage import make_accepted_debt
from test_progress_adopted_source_selection import (
    _draft,
    _seed_knowledge,
)
from test_progress_adopted_source_selection import (
    sqlite_and_no_models as sqlite_and_no_models,
)

from bestseller.infra.db.models import ChapterModel, ProjectModel
from bestseller.services.adopted_chapter_source import select_adopted_chapter_source
from bestseller.web.server import _load_adopted_chapter_progress

pytestmark = pytest.mark.unit


def _block(draft, *, passed=False):
    body = draft.content_md.replace("\r\n", "\n").replace("\r", "\n")
    return {
        "draft_id": str(draft.id),
        "body_sha256": hashlib.sha256(body.encode()).hexdigest(),
        "passed": passed,
    }


def _ledger(receipt):
    return {f"{receipt['draft_id']}:{receipt['body_sha256']}": receipt}


@pytest.mark.parametrize("case", ["exact", "other_id", "other_body", "passed", "bad_key"])
def test_pure_usable_chooser_keeps_exact_source_and_raw_ownership(case):
    from bestseller.services.adopted_chapter_source import (
        choose_promoted_chapter_source,
        choose_usable_promoted_chapter_source,
    )

    chapter = ChapterModel(id=uuid4(), project_id=uuid4(), metadata_json={})
    source = _draft(chapter, 9, body="检查。\r\n开封。\r装带。")
    candidate = _draft(chapter, 10, current=True, state="candidate")
    foreign = _draft(chapter, 11, current=True, body=source.content_md)
    foreign.project_id = uuid4()
    receipt = _block(source, passed=case == "passed")
    if case == "other_id":
        receipt = _block(candidate)
    elif case == "other_body":
        receipt["body_sha256"] = "0" * 64
    ledger = _ledger(receipt) if case != "bad_key" else {"wrong-key": receipt}
    chapter.metadata_json = {"continuity_admission_by_source": ledger}
    before = deepcopy(chapter.metadata_json)
    rows = [candidate, foreign, source]
    assert choose_promoted_chapter_source(
        rows, project_id=chapter.project_id, chapter_id=chapter.id,
    ) is source
    assert choose_usable_promoted_chapter_source(chapter=chapter, drafts=iter(rows)) is (
        None if case == "exact" else source
    )
    assert chapter.metadata_json == before


def test_blocked_latest_promotion_does_not_fall_back_to_older_source():
    from test_progress_adopted_source_selection import EARLY, LATE

    from bestseller.services.adopted_chapter_source import choose_usable_promoted_chapter_source

    chapter = ChapterModel(id=uuid4(), project_id=uuid4())
    old = _draft(chapter, 1, when=EARLY)
    selected = _draft(chapter, 9, when=LATE)
    chapter.metadata_json = {"continuity_admission_by_source": _ledger(_block(selected))}
    assert choose_usable_promoted_chapter_source(chapter=chapter, drafts=[old, selected]) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("line_endings", ["\n", "\r\n", "\r"])
@pytest.mark.parametrize("block_source", ["none", "source", "candidate", "old_body"])
async def test_sqlite_block_overrides_historical_closed_knowledge(db, line_endings, block_source):
    session, project, chapter, source = db
    assert session.bind.dialect.name == "sqlite"
    source.version_no = 9
    source.content_md = line_endings.join(["检查机器。", "拆开封条。", "装入原带。"])
    # These are historical grades on V9, never fresh grades on the V10 candidate.
    score, report = await make_accepted_debt(db)
    source.promotion_state = "promoted"
    chapter.status, chapter.production_state = "complete", "complete"
    chapter.metadata_json = {}
    await _seed_knowledge(session, chapter, source)
    before = (await _load_adopted_chapter_progress(session, [project.id]))[project.id]
    assert before["knowledge"]["closed_chapters"] == 1
    source.is_current = False
    candidate = _draft(chapter, 10, current=True, state="candidate")
    session.add(candidate)
    receipt = _block(candidate if block_source == "candidate" else source)
    if block_source == "old_body":
        receipt["body_sha256"] = "0" * 64
    blocked = block_source == "source"
    chapter.metadata_json = {
        **chapter.metadata_json,
        "continuity_admission_by_source": _ledger(receipt) if block_source != "none" else {},
    }
    await session.flush()
    metadata = deepcopy(chapter.metadata_json)
    grades = (deepcopy(score.evidence_summary), deepcopy(report.structured_output))
    statements = []

    def record(_conn, _cursor, statement, *_args):
        statements.append(statement)

    event.listen(session.sync_session.bind, "before_cursor_execute", record)
    try:
        adopted, debt = await select_adopted_chapter_source(session, chapter=chapter, lock=False)
        stats = (await _load_adopted_chapter_progress(session, [project.id]))[project.id]
    finally:
        event.remove(session.sync_session.bind, "before_cursor_execute", record)
    count = int(not blocked)
    assert adopted is (None if blocked else source) and debt is None
    assert stats["completed_chapters"] == stats["adopted_chapters"] == count
    assert stats["promoted_chapters"] == count
    assert stats["accepted_with_debt_chapters"] == 0
    assert stats["candidate_chapters"] == 1
    assert [row["draft_id"] for row in stats["chapters"]] == ([] if blocked else [str(source.id)])
    knowledge = stats["knowledge"]
    for key in ("adopted_chapters", "feedback_chapters", "snapshot_chapters", "closed_chapters"):
        assert knowledge[key] == count
    assert knowledge["bible"]["closed_chapters"] == count
    assert knowledge["status"] == ("pending" if blocked else "closed")
    assert bool(stats["word_count_total"]) == bool(count)
    assert chapter.metadata_json == metadata
    assert grades == (score.evidence_summary, report.structured_output)
    assert candidate.promotion_state == "candidate" and candidate.llm_run_id is None
    assert statements and all(sql.lstrip().upper().startswith("SELECT") for sql in statements)
    assert not session.dirty and not session.new and not session.deleted


@pytest.mark.asyncio
@pytest.mark.parametrize("prior_promoted", [False, True])
async def test_sqlite_numeric_debt_cannot_bypass_source_block(db, prior_promoted):
    session, project, chapter, source = db
    assert session.bind.dialect.name == "sqlite"
    old = None
    if prior_promoted:
        old = _draft(chapter, 0, body="先前采用的独立正文。")
        session.add(old)
    await make_accepted_debt(db)
    chapter.metadata_json = {
        **chapter.metadata_json,
        "continuity_admission_by_source": _ledger(_block(source)),
    }
    await session.flush()
    adopted, debt = await select_adopted_chapter_source(session, chapter=chapter, lock=False)
    assert adopted is old and debt is None
    stats = (await _load_adopted_chapter_progress(session, [project.id]))[project.id]
    assert stats["accepted_with_debt_chapters"] == 0
    assert stats["adopted_chapters"] == int(prior_promoted)
    assert stats["knowledge"]["closed_chapters"] == 0
    assert stats["candidate_chapters"] == 1


@pytest.mark.asyncio
async def test_sqlite_recorded_post8y_source9_block_and_unrelated_book(db):
    session, unrelated, _, unrelated_source = db
    assert session.bind.dialect.name == "sqlite"
    artifact = Path(__file__).resolve().parents[2] / (
        "reports/framework-recovery-2026-10-21/phase8y-deployed-correction-application.json"
    )
    recorded = json.loads(artifact.read_text())
    plan, result = recorded["plan"], recorded["result"]
    project = ProjectModel(
        id=UUID(plan["project_id"]), slug="post8y-sourceblock-test", title="test",
        genre="test", target_word_count=10000, target_chapters=4,
    )
    chapter = ChapterModel(
        id=UUID(plan["chapter_id"]), project_id=project.id, chapter_number=2,
        chapter_goal="test", status="revision", production_state="pending", metadata_json={},
    )
    session.add_all([project, chapter])
    await session.flush()
    source = _draft(chapter, 9, current=True, body=plan["source_before"]["content_md"])
    source.id = UUID(plan["source_draft_id"])
    source.promotion_metadata = {"historical_review_snapshot": {"verdict": "pass"}}
    session.add(source)
    await session.flush()
    # Persist real historical report/score rows before the working pointer moves.
    await make_accepted_debt((session, project, chapter, source))
    source.promotion_state, source.is_current = "promoted", False
    chapter.status, chapter.production_state = "revision", "pending"
    candidate = _draft(chapter, 10, current=True, state="candidate", body=plan["result_content_md"])
    candidate.id = UUID(result["candidate_id"])
    assert _block(source)["body_sha256"] == plan["source_body_sha256"]
    assert _block(candidate)["body_sha256"] == result["body_sha256"]
    chapter.metadata_json = {"continuity_admission_by_source": _ledger(_block(source))}
    # A different book with the same prose has its own source and ledger.
    unrelated_source.content_md = source.content_md
    session.add_all([source, candidate])
    await session.flush()
    adopted, receipt = await select_adopted_chapter_source(session, chapter=chapter, lock=False)
    assert adopted is None and receipt is None
    stats = await _load_adopted_chapter_progress(session, [project.id, unrelated.id])
    assert stats[project.id]["adopted_chapters"] == stats[project.id]["word_count_total"] == 0
    assert stats[project.id]["knowledge"]["adopted_chapters"] == 0
    assert stats[project.id]["knowledge"]["closed_chapters"] == 0
    assert stats[project.id]["knowledge"]["status"] == "pending"
    assert stats[project.id]["candidate_chapters"] == 1
    assert stats[unrelated.id]["adopted_chapters"] == 1
    assert stats[unrelated.id]["chapters"][0]["draft_id"] == str(unrelated_source.id)
    assert source.promotion_state == "promoted" and source.version_no == 9
    assert candidate.promotion_state == "candidate" and candidate.llm_run_id is None
