"""Reader adoption labels require the exact displayed source's SQL evidence."""
from copy import deepcopy
from pathlib import Path

import pytest

from bestseller.web import server
from test_export_numeric_adoption import db  # noqa: F401

pytestmark = pytest.mark.asyncio


async def test_exact_debt_source_is_read_only_and_keeps_its_debt(db):
    session, project, chapter, draft, *_ = db
    before = deepcopy(chapter.metadata_json)
    source = await server._reader_source_adoption(
        session, project.id, chapter.id, draft.id, draft.content_md,
    )
    assert source["adoption_state"] == "accepted_with_debt"
    assert server._reader_chapter_state(
        chapter_status="revision", production_state="quality_debt",
        availability="repair_in_progress", production_intent="pause",
        adoption_state=source["adoption_state"],
    ) == ("accepted_with_debt", "已采用（含质量债）")
    assert chapter.metadata_json == before
    assert draft.promotion_state == "under_review"
    assert not session.dirty
    assert server._reader_state_payload(source) == {
        **source, "reader_state": None, "reader_label": None,
        "pending_rewrite_task_count": None, "production_intent": None,
    }


@pytest.mark.parametrize("invalid", ["body", "flag_only", "old_promoted", "wrong_draft"])
async def test_unverified_sources_keep_candidate_labels(db, invalid):
    session, project, chapter, draft, old, *_ = db
    body = draft.content_md
    if invalid == "body":
        body += "不是审核的正文。"
    elif invalid == "flag_only":
        chapter.metadata_json = {"chapter_quality_debt": True}
        await session.commit()
    elif invalid == "old_promoted":
        draft = old
        body = old.content_md
    else:
        draft.is_current = False
        await session.commit()
    source = await server._reader_source_adoption(
        session, project.id, chapter.id, draft.id, body,
    )
    assert source["adoption_state"] == "not_adopted"
    state, _ = server._reader_chapter_state(
        chapter_status="revision", production_state="quality_debt",
        availability="repair_in_progress", adoption_state=source["adoption_state"],
    )
    assert state not in {"adopted", "accepted_with_debt"}


async def test_promoted_label_also_requires_source_validation(db):
    session, project, chapter, _, old, *_ = db
    chapter.metadata_json = {}
    await session.commit()
    source = await server._reader_source_adoption(
        session, project.id, chapter.id, old.id, old.content_md,
    )
    assert source["adoption_state"] == "promoted"
    assert server._reader_chapter_state(
        chapter_status="complete", production_state="ok", availability="available",
        adoption_state=source["adoption_state"],
    ) == ("adopted", "已采用")


async def test_reader_template_keeps_adopted_and_candidate_notices_separate():
    template = Path(server.__file__).with_name("novel_reader.html").read_text()
    assert 'readerState === "accepted_with_debt"' in template
    assert "下面显示已采用的正文，质量债仍保留" in template
    assert "下面显示未采用的候选草稿" in template
