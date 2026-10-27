from __future__ import annotations

import socket
from uuid import uuid4

import pytest
from test_adopted_knowledge_lineage import db as db  # noqa: F401
from test_adopted_knowledge_lineage import snapshot as extract_snapshot
from test_adopted_knowledge_lineage import stub_llm

from bestseller.services import continuity, llm

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def offline_only(monkeypatch):
    monkeypatch.setenv("BESTSELLER_LINEAGE_PG", "0")

    def forbidden(*_args, **_kwargs):
        raise AssertionError("Exact snapshot tests forbid network/provider access")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(llm, "_call_litellm", forbidden)


class _Session:
    def __init__(self, result: object | None) -> None:
        self.result = result
        self.statements: list[object] = []

    async def scalar(self, statement: object) -> object | None:
        self.statements.append(statement)
        return self.result


@pytest.mark.asyncio
async def test_previous_snapshot_requires_exact_promoted_predecessor() -> None:
    project_id = uuid4()
    session = _Session(None)

    snapshot = await continuity.load_previous_chapter_snapshot(
        session,  # type: ignore[arg-type]
        project_id=project_id,
        current_chapter_number=8,
    )

    assert snapshot is None
    # The first read selects usable authorities; a separate exact-ID probe
    # detects retained rows that require epistemic reconciliation.
    assert session.statements
    for statement in session.statements:
        where = str(statement).split("WHERE", maxsplit=1)[1]
        assert "project_id =" in where
        assert "chapter_number =" in where
        assert "chapter_number <" not in where
        assert statement.compile().params["chapter_number_1"] == 7
        assert statement.compile().params["project_id_1"] == project_id
    assert "extraction_status" in str(session.statements[0])


@pytest.mark.asyncio
async def test_exact_promoted_snapshot_is_returned_as_continuity_authority(db, monkeypatch) -> None:
    session, project, chapter, draft = db
    assert session.bind.dialect.name == "sqlite"
    chapter.chapter_number = 7
    draft.content_md = "第七天夜，计时器显示20小时。两小时过去，读数没有变化。"
    await session.commit()
    calls = stub_llm(monkeypatch, continuity, {
        "facts": [{"name": "倒计时", "value": "20", "unit": "小时", "kind": "countdown",
                   "source_quote": "计时器显示20小时"}],
        "time_anchor": "第七天夜",
        "chapter_time_span": "两小时",
    })
    row = await extract_snapshot(db)
    await session.commit()
    await session.refresh(row)
    assert len(calls) == 1 and row.extraction_status == "ok_promoted"
    contract = row.facts["snapshot_contract"]
    assert contract["project_id"] == str(project.id)
    assert contract["chapter_id"] == str(chapter.id)
    assert contract["source_chapter_draft_version_id"] == str(draft.id)
    assert row.facts[continuity._EPISTEMIC_VIEW]["view_sha256"]

    snapshot = await continuity.load_previous_chapter_snapshot(
        session,
        project_id=project.id,
        current_chapter_number=8,
    )

    assert snapshot is not None
    assert snapshot.chapter_number == 7
    assert snapshot.facts[0].value == "20"
    assert snapshot.epistemic_view_sha256
    assert await continuity.load_previous_chapter_snapshot(
        session, project_id=project.id, current_chapter_number=9,
    ) is None  # Chapter 7 cannot stand in for a missing chapter 8.
    assert await continuity.load_previous_chapter_snapshot(
        session, project_id=uuid4(), current_chapter_number=8,
    ) is None
