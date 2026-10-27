"""Database regressions for AI flavor/deslop draft lineage."""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateTable

from bestseller.infra.db.models import ChapterDraftVersionModel
from bestseller.services.pipelines import (
    _persist_deslop_candidate,
    _select_deslop_candidate,
)
from bestseller.services.reviews import chapter_review_body_fingerprint

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@compiles(JSONB, "sqlite")
def _compile_jsonb_for_sqlite(_type, _compiler, **_kwargs) -> str:
    return "JSON"


class _AsyncSessionFacade:
    """Run production ORM statements on installed synchronous SQLite."""

    def __init__(self, session: Session) -> None:
        self.sync_session = session

    def add(self, value) -> None:
        self.sync_session.add(value)

    async def scalar(self, statement):
        return self.sync_session.scalar(statement)

    async def execute(self, statement):
        return self.sync_session.execute(statement)

    async def flush(self) -> None:
        self.sync_session.flush()


class _FailingSelectionFacade(_AsyncSessionFacade):
    async def execute(self, statement):
        raise DBAPIError("UPDATE current", {}, RuntimeError("write failed"))


@contextmanager
def _draft_database():
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        # Avoid compiling the PostgreSQL-only current index as an unconditional
        # SQLite unique index; install SQLite's real partial-current invariant.
        connection.execute(CreateTable(ChapterDraftVersionModel.__table__))
        connection.exec_driver_sql(
            "CREATE UNIQUE INDEX uq_test_chapter_draft_current "
            "ON chapter_draft_versions (chapter_id) WHERE is_current = 1"
        )
    with Session(engine, expire_on_commit=False) as sync_session:
        yield _AsyncSessionFacade(sync_session), sync_session
    engine.dispose()


def _original(sync_session: Session, *, content: str = "原稿正文"):
    original = ChapterDraftVersionModel(
        project_id=uuid4(),
        chapter_id=uuid4(),
        version_no=1,
        content_md=content,
        word_count=4,
        is_current=True,
        llm_run_id=uuid4(),
        promotion_metadata={
            "origin": "chapter_first_writer",
            "source_body_fingerprint": "original-source-proof",
        },
    )
    sync_session.add(original)
    sync_session.flush()
    return original


def _chapter_for(original: ChapterDraftVersionModel):
    return SimpleNamespace(
        id=original.chapter_id,
        current_word_count=original.word_count,
        status="revision",
        production_state="blocked",
        metadata_json={
            "reader_judge": {"decision": "fail", "issues": ["old-body"]},
            "retention_gate_last_findings": [{"code": "OLD_RETENTION_FAIL"}],
            "deterministic_audit_latest": {"passed": False},
            "current_body_review_status": "failed",
            "current_body_draft_id": str(original.id),
            "current_body_fingerprint": chapter_review_body_fingerprint(
                original.content_md
            ),
            "reader_review_required": True,
            "blocked_by_ai_flavor_gate": True,
        },
    )


async def _two_rounds(session, chapter, original):
    first_run, second_run = uuid4(), uuid4()
    round_one = await _persist_deslop_candidate(
        session,
        chapter=chapter,
        parent_draft=original,
        content="第一轮正文",
        llm_run_id=first_run,
        round_no=1,
    )
    round_two = await _persist_deslop_candidate(
        session,
        chapter=chapter,
        parent_draft=round_one,
        content="第二轮正文",
        llm_run_id=second_run,
        round_no=2,
    )
    return round_one, round_two, first_run, second_run


async def test_direct_second_round_selection_preserves_lineage_and_reviews() -> None:
    with _draft_database() as (session, sync_session):
        original = _original(sync_session)
        original_run_id = original.llm_run_id
        chapter = _chapter_for(original)
        round_one, round_two, first_run, second_run = await _two_rounds(
            session, chapter, original
        )

        selected = await _select_deslop_candidate(
            session,
            chapter=chapter,
            candidate=round_two,
            selected_reason="existing_deslop_quality_selection",
            selection_pool=(round_one, round_two),
        )
        rows = list(
            sync_session.scalars(
                select(ChapterDraftVersionModel).order_by(
                    ChapterDraftVersionModel.version_no
                )
            )
        )

        assert [row.version_no for row in rows] == [1, 2, 3]
        assert original.content_md == "原稿正文"
        assert original.llm_run_id == original_run_id
        assert original.promotion_metadata["source_body_fingerprint"] == (
            "original-source-proof"
        )
        assert round_one.llm_run_id == first_run
        assert round_two.llm_run_id == second_run
        assert round_one.promotion_metadata["parent_draft_id"] == str(original.id)
        assert round_two.promotion_metadata["parent_draft_id"] == str(round_one.id)
        assert round_one.promotion_metadata["source_body_fingerprint"] == (
            chapter_review_body_fingerprint(original.content_md)
        )
        assert round_two.promotion_metadata["source_body_fingerprint"] == (
            chapter_review_body_fingerprint(round_one.content_md)
        )
        assert all(row.promotion_state == "candidate" for row in (round_one, round_two))
        assert [row.id for row in rows if row.is_current] == [selected.id]
        assert selected.promotion_metadata["candidate_status"] == "selected"
        assert "unselected_reason" not in selected.promotion_metadata
        assert round_one.promotion_metadata["candidate_status"] == "unselected"
        assert round_one.promotion_metadata["unselected_reason"] == (
            "not_selected:existing_deslop_quality_selection"
        )
        assert "wrote_as_current:yes" in selected.promotion_reason_codes
        assert chapter.current_word_count == selected.word_count == 5

        old_snapshot = original.promotion_metadata["historical_review_snapshot"]
        assert old_snapshot["body_fingerprint"] == chapter_review_body_fingerprint(
            original.content_md
        )
        assert old_snapshot["review_status"] == "failed"
        assert old_snapshot["reports"]["reader_judge"]["decision"] == "fail"
        assert old_snapshot["reports"]["retention_gate_last_findings"] == [
            {"code": "OLD_RETENTION_FAIL"}
        ]
        assert chapter.metadata_json["superseded_body_review_history"][-1] == {
            "draft_id": str(original.id),
            "body_fingerprint": chapter_review_body_fingerprint(original.content_md),
            "review_status": "failed",
            "snapshot_metadata_key": "historical_review_snapshot",
        }
        assert chapter.metadata_json["current_body_review_status"] == "pending"
        assert chapter.metadata_json["current_body_draft_id"] == str(selected.id)
        assert chapter.metadata_json["current_body_fingerprint"] == (
            chapter_review_body_fingerprint(selected.content_md)
        )
        assert "reader_judge" not in chapter.metadata_json
        assert "retention_gate_last_findings" not in chapter.metadata_json
        assert chapter.metadata_json["reader_review_required"] is True
        assert chapter.metadata_json["blocked_by_ai_flavor_gate"] is True
        assert chapter.status == "revision"
        assert chapter.production_state == "blocked"


async def test_first_round_can_win_while_second_round_remains_auditable() -> None:
    with _draft_database() as (session, sync_session):
        original = _original(sync_session)
        chapter = _chapter_for(original)
        round_one, round_two, _, second_run = await _two_rounds(
            session, chapter, original
        )

        await _select_deslop_candidate(
            session,
            chapter=chapter,
            candidate=round_one,
            selected_reason="existing_deslop_quality_selection",
            selection_pool=(round_one, round_two),
        )

        rows = list(sync_session.scalars(select(ChapterDraftVersionModel)))
        assert [row.id for row in rows if row.is_current] == [round_one.id]
        assert round_one.promotion_metadata["candidate_status"] == "selected"
        assert "unselected_reason" not in round_one.promotion_metadata
        assert round_two.promotion_metadata["candidate_status"] == "unselected"
        assert round_two.promotion_metadata["unselected_reason"].startswith("not_selected:")
        assert round_two.llm_run_id == second_run
        assert round_two.promotion_state == "candidate"


async def test_postcheck_patch_descends_from_selected_deslop_candidate() -> None:
    with _draft_database() as (session, sync_session):
        original = _original(sync_session)
        chapter = _chapter_for(original)
        round_one, round_two, _, _ = await _two_rounds(session, chapter, original)
        await _select_deslop_candidate(
            session,
            chapter=chapter,
            candidate=round_one,
            selected_reason="existing_deslop_quality_selection",
            selection_pool=(round_one, round_two),
        )

        postcheck = await _persist_deslop_candidate(
            session,
            chapter=chapter,
            parent_draft=round_one,
            content="postcheck final body",
            llm_run_id=None,
            round_no=1,
            language="en-US",
            transformation="deslop_postcheck_patch",
            make_current=True,
        )

        rows = list(sync_session.scalars(select(ChapterDraftVersionModel)))
        assert len(rows) == 4
        assert [row.id for row in rows if row.is_current] == [postcheck.id]
        assert postcheck.promotion_metadata["parent_draft_id"] == str(round_one.id)
        assert postcheck.promotion_metadata["source_body_fingerprint"] == (
            chapter_review_body_fingerprint(round_one.content_md)
        )
        assert postcheck.promotion_metadata["transformation"] == (
            "deslop_postcheck_patch"
        )
        assert postcheck.llm_run_id is None
        assert postcheck.promotion_state == "candidate"
        assert postcheck.word_count == chapter.current_word_count == 3
        assert round_one.promotion_metadata["candidate_status"] == "superseded_current"


async def test_selection_database_failure_is_visible_and_keeps_old_current() -> None:
    with _draft_database() as (session, sync_session):
        original = _original(sync_session)
        chapter = _chapter_for(original)
        candidate = await _persist_deslop_candidate(
            session,
            chapter=chapter,
            parent_draft=original,
            content="候选正文",
            llm_run_id=uuid4(),
            round_no=1,
        )

        with pytest.raises(DBAPIError, match="write failed"):
            await _select_deslop_candidate(
                _FailingSelectionFacade(sync_session),
                chapter=chapter,
                candidate=candidate,
                selected_reason="existing_deslop_quality_selection",
            )

        assert original.is_current is True
        assert candidate.is_current is False
        assert chapter.metadata_json["current_body_draft_id"] == str(original.id)
