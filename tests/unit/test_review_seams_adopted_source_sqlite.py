"""Real SQLite predecessor selection; no providers or production database.

Both review signals must consume only the existing formal adoption selector.
These tests establish source selection, not literary continuity acceptance.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import event, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.schema import CreateTable

from bestseller.infra.db.models import ChapterDraftVersionModel, ChapterModel, ProjectModel
from bestseller.services import adopted_chapter_source, feedback, llm, reviews

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@compiles(JSONB, "sqlite")
def _sqlite_jsonb(_type, _compiler, **_kwargs):
    return "JSON"


@compiles(PGUUID, "sqlite")
def _sqlite_uuid(_type, _compiler, **_kwargs):
    return "TEXT"


@pytest.fixture
async def seam_db(tmp_path, monkeypatch):
    path = tmp_path / "review_seams_adopted_source_test.sqlite3"
    assert path.stem.endswith("_test")
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    assert engine.url.get_backend_name() == "sqlite"
    assert engine.url.database == str(path)
    statements = []

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def observe(_connection, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement)

    async with engine.begin() as connection:
        for model in (ProjectModel, ChapterModel, ChapterDraftVersionModel):
            ddl = str(CreateTable(model.__table__).compile(dialect=connection.dialect))
            await connection.exec_driver_sql(ddl.replace("::jsonb", ""))
        await connection.exec_driver_sql(
            "CREATE UNIQUE INDEX seam_test_current ON chapter_draft_versions(chapter_id) "
            "WHERE is_current = 1"
        )

    async def forbidden(*_args, **_kwargs):
        pytest.fail("Seam source-selection tests must never call a provider")

    for module in (reviews, feedback, llm):
        monkeypatch.setattr(module, "complete_text", forbidden)
    calls = []
    real_selector = adopted_chapter_source.select_adopted_chapter_source

    async def observed_selector(session, *, chapter, lock=True):
        assert lock is False
        calls.append((chapter.project_id, chapter.id))
        return await real_selector(session, chapter=chapter, lock=lock)

    monkeypatch.setattr(adopted_chapter_source, "select_adopted_chapter_source", observed_selector)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            yield SimpleNamespace(session=session, statements=statements, calls=calls)
    finally:
        await engine.dispose()


def _draft(chapter, version, body, *, state="candidate", current=False):
    return ChapterDraftVersionModel(
        project_id=chapter.project_id, chapter_id=chapter.id,
        version_no=version, content_md=body, word_count=len(body),
        promotion_state=state, is_current=current,
    )


async def _book(session, label, formal_body, working_body, current_body):
    project = ProjectModel(
        slug=f"seam-source-test-{label}-{uuid4().hex[:8]}", title=f"Fixture {label}",
        genre="test", target_word_count=10000, target_chapters=3,
    )
    session.add(project)
    await session.flush()
    previous = ChapterModel(
        project_id=project.id, chapter_number=1, chapter_goal="Prior state", metadata_json={},
    )
    current = ChapterModel(
        project_id=project.id, chapter_number=2, chapter_goal="Continue state", metadata_json={},
    )
    session.add_all([previous, current])
    await session.flush()
    adopted = _draft(previous, 1, formal_body, state="promoted")
    working = _draft(previous, 2, working_body, current=True)
    target = _draft(current, 1, current_body, current=True)
    session.add_all([adopted, working, target])
    await session.commit()
    return SimpleNamespace(
        project=project, previous=previous, current=current,
        adopted=adopted, working=working, target=target,
    )


def _signal(kind):
    if kind == "chapter_seam":
        return reviews._compute_chapter_seam_signal
    return reviews._compute_reader_logic_signal


async def _run(db, book, kind):
    before = deepcopy(book.previous.metadata_json)
    db.statements.clear()
    findings, evidence = await _signal(kind)(
        session=db.session, project=book.project, chapter=book.current, draft=book.target,
    )
    assert book.previous.metadata_json == before
    assert not db.session.dirty
    assert db.statements
    assert all(statement.lstrip().upper().startswith("SELECT") for statement in db.statements)
    return findings, evidence


@pytest.mark.parametrize("kind", ["chapter_seam", "reader_logic"])
async def test_crossbook_formal_body_survives_working_pointer_movement(seam_db, kind):
    db = seam_db
    if kind == "chapter_seam":
        risky, calm, current_body = "守卫持刀围住码头。", "木屋内炭火明亮。", "木屋内炭火明亮。"
    else:
        risky, calm, current_body = "我推开门,走进303。", "只有雨声。", "我没有打开303。"
    # Identical chapter numbers and opposite predecessor states in two books.
    books = [
        await _book(db.session, "alpha", risky, calm, current_body),
        await _book(db.session, "beta", calm, risky, current_body),
    ]
    for expected_hard, book in zip((True, False), books, strict=True):
        initial = None
        for step in range(2):
            findings, evidence = await _run(db, book, kind)
            assert any(f.severity == "critical" for f in findings) is expected_hard
            source = evidence[f"{kind}_source"]
            assert evidence[f"{kind}_evaluation_status"] == "checked"
            assert source == {
                "status": "selected_adopted", "project_id": str(book.project.id),
                "chapter_id": str(book.previous.id), "draft_id": str(book.adopted.id),
                "body_sha256": hashlib.sha256(book.adopted.content_md.encode()).hexdigest(),
                "adoption_receipt": None,
            }
            comparable = ([f.model_dump() for f in findings], evidence)
            if initial is None:
                initial = comparable
            else:
                assert comparable == initial
            if step == 0:
                book.working.is_current = False
                await db.session.flush()
                book.working = _draft(
                    book.previous, 3, "新工作稿。" + risky + calm, current=True,
                )
                db.session.add(book.working)
                await db.session.commit()
        assert db.calls.count((book.project.id, book.previous.id)) == 2
    assert books[0].adopted.id != books[1].adopted.id


@pytest.mark.parametrize("kind", ["chapter_seam", "reader_logic"])
@pytest.mark.parametrize("mode", ["candidate", "under_review", "ambiguous", "empty", "blocked"])
async def test_unknown_formal_source_never_falls_back_to_current(seam_db, kind, mode):
    db = seam_db
    book = await _book(
        db.session, mode, "守卫持刀围住码头。我走进303。", "只有雨声。", "只有雨声。",
    )
    if mode in {"candidate", "under_review"}:
        book.adopted.promotion_state = mode
    elif mode == "ambiguous":
        db.session.add(_draft(book.previous, 4, "另一份历史采用正文。", state="promoted"))
    elif mode == "empty":
        book.adopted.content_md = " \n\t"
    else:
        book.previous.metadata_json = {"continuity_admission": {
            "draft_id": str(book.adopted.id),
            "body_sha256": hashlib.sha256(book.adopted.content_md.encode()).hexdigest(),
            "passed": False,
        }}
    await db.session.commit()
    findings, evidence = await _run(db, book, kind)
    assert evidence[f"{kind}_evaluation_status"] == "unknown"
    assert evidence[f"{kind}_source"]["status"] == "unknown"
    assert evidence[f"{kind}_source"]["reason"] in {
        "adopted_previous_chapter_unavailable", "adopted_previous_body_empty",
    }
    assert all(f.category == "continuity_source" and f.severity == "info" for f in findings)
    assert findings  # Persist an honest diagnostic through the existing finding-gated caller.
    assert not any(key.endswith(("_score", "_repair_prompt")) for key in evidence)
    assert db.calls == [(book.project.id, book.previous.id)]


@pytest.mark.parametrize("kind", ["chapter_seam", "reader_logic"])
async def test_other_book_predecessor_is_not_a_formal_source(seam_db, kind):
    db = seam_db
    foreign = await _book(
        db.session, "foreign", "我走进303。守卫持刀围住码头。", "工作稿。", "只有雨声。",
    )
    local = await _book(db.session, "local", "本书前章。", "本书工作稿。", "只有雨声。")
    # Remove only the local predecessor from the lookup interval. A same-number
    # predecessor in another book must not fill that missing formal source.
    local.previous.chapter_number = 3
    await db.session.commit()
    findings, evidence = await _run(db, local, kind)
    assert evidence[f"{kind}_source"] == {
        "status": "unknown", "reason": "previous_chapter_missing",
    }
    assert all(f.severity == "info" for f in findings)
    assert db.calls == []
    assert foreign.previous.chapter_number == 1


@pytest.mark.parametrize("kind", ["chapter_seam", "reader_logic"])
async def test_no_current_pointer_still_reads_the_formal_source(seam_db, kind):
    db = seam_db
    book = await _book(
        db.session, "no-current", "守卫持刀围住码头。我走进303。", "工作稿。", "我没有打开303。",
    )
    book.working.is_current = False
    await db.session.commit()
    findings, evidence = await _run(db, book, kind)
    assert any(f.severity == "critical" for f in findings)
    assert evidence[f"{kind}_source"]["draft_id"] == str(book.adopted.id)


@pytest.mark.parametrize("kind", ["chapter_seam", "reader_logic"])
async def test_foreign_owned_draft_cannot_be_selected_for_local_chapter(seam_db, kind):
    db = seam_db
    foreign = await _book(db.session, "owner", "采用正文。", "工作稿。", "只有雨声。")
    local = await _book(db.session, "target", "旧候选。", "工作稿。", "只有雨声。")
    local.adopted.promotion_state = "candidate"
    db.session.add(ChapterDraftVersionModel(
        project_id=foreign.project.id, chapter_id=local.previous.id, version_no=7,
        content_md="守卫持刀围住码头。我走进303。", promotion_state="promoted", is_current=False,
    ))
    await db.session.commit()
    findings, evidence = await _run(db, local, kind)
    assert evidence[f"{kind}_source"]["status"] == "unknown"
    assert evidence[f"{kind}_source"]["reason"] == "adopted_previous_chapter_unavailable"
    assert all(f.severity == "info" for f in findings)


@pytest.mark.parametrize("kind", ["chapter_seam", "reader_logic"])
async def test_signal_does_not_flush_unrelated_pending_work(seam_db, kind):
    db = seam_db
    book = await _book(db.session, "pending", "只有雨声。", "工作稿。", "只有雨声。")
    unrelated = ChapterModel(
        project_id=book.project.id, chapter_number=9,
        chapter_goal="Pending local work", metadata_json={},
    )
    db.session.add(unrelated)
    db.statements.clear()
    _, evidence = await _signal(kind)(
        session=db.session, project=book.project, chapter=book.current, draft=book.target,
    )
    assert evidence[f"{kind}_source"]["draft_id"] == str(book.adopted.id)
    assert unrelated in db.session.new
    assert all(statement.lstrip().upper().startswith("SELECT") for statement in db.statements)
    with db.session.no_autoflush:
        persisted = await db.session.scalar(
            select(ChapterModel.id).where(ChapterModel.chapter_number == 9),
        )
        assert persisted is None
