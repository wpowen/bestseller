"""Adopted-source/dependency guards with file SQLite and deterministic local stubs."""

from copy import deepcopy
import json
import socket
from uuid import uuid4

import pytest
from sqlalchemy import event, select, update
from test_adopted_knowledge_lineage import db as db
from test_adopted_knowledge_lineage import snapshot, stub_llm
from test_knowledge_downstream_source_dependencies import _next_chapter, _replace_source

from bestseller.infra.db.models import ChapterDraftVersionModel, ChapterStateSnapshotModel
from bestseller.services import continuity, llm
from bestseller.services.feedback import KNOWLEDGE_SOURCE_KEYS, validate_adopted_knowledge_source

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.fixture(autouse=True)
def offline_only(monkeypatch):
    monkeypatch.setenv("BESTSELLER_LINEAGE_PG", "0")

    def deny(*_args, **_kwargs):
        raise AssertionError("Snapshot dependency tests forbid network/model access")

    monkeypatch.setattr(socket.socket, "connect", deny)
    monkeypatch.setattr(llm, "complete_text", deny)
    monkeypatch.setattr(continuity, "complete_text", deny)


def countdown(value, quote=None):
    return {
        "facts": [
            {
                "name": "剩余时间",
                "value": value,
                "unit": "小时",
                "kind": "countdown",
                "source_quote": quote,
            }
        ]
    }


async def current(db, number):
    return await continuity.snapshot_evidence_current(
        db[0],
        project_id=db[1].id,
        chapter_number=number,
    )


async def load(db, number):
    return await continuity.load_previous_chapter_snapshot(
        db[0],
        project_id=db[1].id,
        current_chapter_number=number + 1,
    )


async def reconcile_epistemic_view(book):
    """Publish a real same-source view, retaining literal raw and dependencies."""
    session, project, chapter, draft = book
    kwargs = {
        "project_id": project.id,
        "chapter_id": chapter.id,
        "source_chapter_draft_version_id": draft.id,
        "chapter_md": draft.content_md,
    }
    proof = await continuity.validate_snapshot_epistemic_view(session, **kwargs)
    return await continuity.apply_snapshot_epistemic_view(
        session,
        **kwargs,
        expected_snapshot_sha256=proof["snapshot_sha256"],
        admission_receipt=proof["admission_receipt"],
        actor="offline-fixture-operator",
        reason="Explicit fixture migration; preserve original paid inputs",
    )


async def chain(db, monkeypatch, length=2):
    first = await _replace_source(db, "记录仪显示剩余二十小时。")
    stub_llm(monkeypatch, continuity, countdown("20", "二十小时"))
    await snapshot(first)
    books = [first]
    for _ in range(1, length):
        later = await _next_chapter(books[-1], "观测员静候通知。")
        stub_llm(monkeypatch, continuity, countdown("20"))
        await snapshot(later)
        books.append(later)
    return books


@pytest.mark.parametrize("path", ["current", "history", "reader"])
@pytest.mark.parametrize("field", [*KNOWLEDGE_SOURCE_KEYS, "is_usable"])
async def test_every_source_field_and_usability_guard_all_cache_paths(db, monkeypatch, path, field):
    stub_llm(monkeypatch, continuity, {"facts": []})
    row = await snapshot(db)
    original_raw = row.raw_extraction
    facts = deepcopy(row.facts)
    if path == "history":
        replacement = await _replace_source(db, "观测员离开控制室。")
        await snapshot(replacement)
        replacement[3].is_current, replacement[3].promotion_state = False, "superseded"
        db[3].is_current, db[3].promotion_state = True, "promoted"
        await db[0].flush()
        facts = deepcopy(row.facts)
        contract = facts["snapshot_history"][0]["facts"]["snapshot_contract"]
    else:
        contract = facts["snapshot_contract"]
    contract[field] = (
        False
        if field == "is_usable"
        else (contract[field] + 1 if field == "source_version_no" else "wrong-binding")
    )
    row.facts = facts
    await db[0].flush()
    if path == "reader":
        with pytest.raises(
            RuntimeError, match="source_context_unavailable: previous_chapter_snapshot"
        ):
            await load(db, 1)
        assert not await current(db, 1)
        return
    calls = stub_llm(monkeypatch, continuity, {"facts": []})
    refreshed = await snapshot(db)
    assert len(calls) == 1  # Never return or restore a falsely bound success.
    assert refreshed.facts["snapshot_contract"]["is_usable"] is True
    assert await current(db, 1)
    assert any(
        entry["raw_extraction"] == original_raw for entry in refreshed.facts["snapshot_history"]
    )


async def test_predecessor_edit_invalidates_reader_then_reextracts_without_losing_raw(
    db, monkeypatch
):
    first, later = await chain(db, monkeypatch)
    old = await snapshot(later)
    original_raw = old.raw_extraction
    original_contract = deepcopy(old.facts["snapshot_contract"])
    changed = await _replace_source(first, "记录仪显示剩余十小时。")
    stub_llm(monkeypatch, continuity, countdown("10", "十小时"))
    await snapshot(changed)
    # Invalidate before anybody retries extraction; consumers cannot omit context.
    with pytest.raises(RuntimeError, match="source_context_unavailable: previous_chapter_snapshot"):
        await load(db, 2)
    assert not await current(db, 2)
    assert old.raw_extraction == original_raw
    calls = stub_llm(monkeypatch, continuity, countdown("10"))
    refreshed = await snapshot(later)
    assert len(calls) == 1 and refreshed.facts["facts"][0]["value"] == "10"
    assert refreshed.facts["snapshot_contract"]["operation_id"] != original_contract["operation_id"]
    history = refreshed.facts["snapshot_history"]
    assert history[-1]["raw_extraction"] == original_raw
    assert history[-1]["facts"]["snapshot_contract"] == original_contract
    assert history[-1]["facts"]["facts"][0]["value"] == "20"
    assert await current(db, 2)
    await snapshot(later)
    assert len(calls) == 1


async def test_ancestor_change_invalidates_the_chain_until_each_input_is_refreshed(db, monkeypatch):
    first, second, third = await chain(db, monkeypatch, length=3)
    changed = await _replace_source(first, "记录仪显示剩余十小时。")
    assert not await current(db, 2) and not await current(db, 3)
    with pytest.raises(RuntimeError, match="source_context_unavailable: previous_chapter_snapshot"):
        await load(db, 3)
    stub_llm(monkeypatch, continuity, countdown("10", "十小时"))
    await snapshot(changed)
    assert not await current(db, 3)
    stub_llm(monkeypatch, continuity, countdown("10"))
    await snapshot(second)
    assert await current(db, 2) and not await current(db, 3)
    refreshed = await snapshot(third)
    assert refreshed.facts["facts"][0]["value"] == "10"
    assert await current(db, 3)


@pytest.mark.parametrize(
    "change", ["facts", "time_anchor", "chapter_time_span", "same_body_new_source"]
)
async def test_predecessor_fact_context_and_adopted_identity_are_dependencies(
    db, monkeypatch, change
):
    first, later = await chain(db, monkeypatch)
    if change == "same_body_new_source":
        replacement = await _replace_source(first, first[3].content_md)
        stub_llm(monkeypatch, continuity, countdown("20", "二十小时"))
        await snapshot(replacement)
    else:
        predecessor = await snapshot(first)
        if change == "facts":
            facts = deepcopy(predecessor.facts)
            facts["facts"][0]["notes"] = "观测员再次核验。"
            predecessor.facts = facts
            raw = json.loads(predecessor.raw_extraction)
            raw["facts"][0]["notes"] = "观测员再次核验。"
        else:
            setattr(predecessor, change, "新的明确时间标记")
            raw = json.loads(predecessor.raw_extraction)
            raw[change] = "新的明确时间标记"
        predecessor.raw_extraction = json.dumps(raw, ensure_ascii=False)
        await db[0].flush()
    with pytest.raises(RuntimeError, match="source_context_unavailable: previous_chapter_snapshot"):
        await load(db, 2)
    assert not await current(db, 2)
    calls = stub_llm(monkeypatch, continuity, countdown("20"))
    if change != "same_body_new_source":
        with pytest.raises(ValueError, match="snapshot_epistemic_predecessor_not_current"):
            await snapshot(later)
        assert not calls
        before = deepcopy(predecessor.facts["snapshot_contract"])
        raw_before = predecessor.raw_extraction
        await reconcile_epistemic_view(first)
        assert predecessor.raw_extraction == raw_before
        assert predecessor.facts["snapshot_contract"] == before
        assert await current(db, 1)
        if change != "facts":
            # An unsupported scalar remains raw evidence, not a hard time anchor.
            assert getattr(await load(db, 1), change) is None
    await snapshot(later)
    assert len(calls) == 1 and await current(db, 2)


async def test_legacy_root_remains_verifiable_but_later_missing_dependencies_are_not_invented(
    db, monkeypatch
):
    first, later = await chain(db, monkeypatch)
    rows = [await snapshot(book) for book in (first, later)]
    for row in rows:
        facts = deepcopy(row.facts)
        facts["snapshot_contract"].pop("snapshot_dependencies")
        row.facts = facts
    await db[0].flush()
    assert not await current(db, 1)
    with pytest.raises(RuntimeError, match="source_context_unavailable: previous_chapter_snapshot"):
        await load(db, 1)
    await reconcile_epistemic_view(first)
    assert await current(db, 1)
    assert not await current(db, 2)
    with pytest.raises(RuntimeError, match="source_context_unavailable: previous_chapter_snapshot"):
        await load(db, 2)
    root = await snapshot(first)
    assert "snapshot_dependencies" not in root.facts["snapshot_contract"]
    calls = stub_llm(monkeypatch, continuity, countdown("20"))
    refreshed = await snapshot(later)
    assert len(calls) == 1 and await current(db, 2)
    legacy = refreshed.facts["snapshot_history"][-1]
    assert "snapshot_dependencies" not in legacy["facts"]["snapshot_contract"]
    assert "snapshot_dependencies" in refreshed.facts["snapshot_contract"]


async def test_absent_predecessor_context_is_bound_and_invalidates_when_it_appears(db, monkeypatch):
    """Absence is bound but cannot authorize extraction; a verified empty view differs."""
    later = await _next_chapter(db, "观测员静候通知。")
    calls = stub_llm(monkeypatch, continuity, {"facts": []})
    previous = await continuity._current_snapshot_input(
        db[0], project_id=db[1].id, chapter_number=1, include_source=True
    )
    dependencies = continuity._snapshot_dependencies(2, previous)
    assert dependencies["previous_source"]["source_chapter_draft_version_id"] == str(db[3].id)
    assert dependencies["previous_context_sha256"] is None
    with pytest.raises(ValueError, match="snapshot_epistemic_predecessor_not_current"):
        await snapshot(later)
    assert not calls and not await current(db, 2)
    assert (
        await db[0].scalar(
            select(ChapterStateSnapshotModel.id).where(
                ChapterStateSnapshotModel.chapter_id == later[2].id
            )
        )
        is None
    )
    await snapshot(db)
    assert len(calls) == 1 and await current(db, 1)
    row = await snapshot(later)
    bound = row.facts["snapshot_contract"]["snapshot_dependencies"]
    assert bound["previous_source"] == dependencies["previous_source"]
    assert bound["previous_context_sha256"] is not None
    assert bound != dependencies  # A verified empty context differs from absent context.
    assert len(calls) == 2 and await current(db, 2)
    assert await load(db, 2) is not None


@pytest.mark.parametrize("parse_error", [False, True])
async def test_dependency_changes_during_extraction_reject_apply_and_skip_stale_repair(
    db, monkeypatch, parse_error
):
    first, later = await chain(db, monkeypatch)
    previous_row = await snapshot(later)
    previous_raw = previous_row.raw_extraction
    # A new adopted body for the later chapter requires extraction, not a cache hit.
    replacement = await _replace_source(later, "观测员起身等待通知。")

    async def change(session):
        first[3].content_md += "记录仪已经更换。"
        await session.flush()

    payload = {"invalid": True} if parse_error else countdown("20")
    calls = stub_llm(monkeypatch, continuity, payload, change)
    row = await snapshot(replacement)
    assert len(calls) == 1
    assert row.extraction_status == "rejected_dependencies"
    assert row.facts["snapshot_contract"]["is_usable"] is False
    assert (
        row.facts["snapshot_contract"]["snapshot_dependencies"]
        == (calls[0].metadata["source_lineage"]["snapshot_dependencies"])
    )
    assert row.facts["snapshot_history"][-1]["raw_extraction"] == previous_raw
    assert row.raw_extraction
    assert not await current(db, 2)
    with pytest.raises(RuntimeError, match="source_context_unavailable: previous_chapter_snapshot"):
        await load(db, 2)


async def test_legacy_later_history_is_reextracted_without_stamping_old_dependencies(
    db,
    monkeypatch,
):
    _first, later = await chain(db, monkeypatch)
    replacement = await _replace_source(later, "观测员走出控制室。")
    stub_llm(monkeypatch, continuity, countdown("20"))
    row = await snapshot(replacement)
    facts = deepcopy(row.facts)
    archived = facts["snapshot_history"][0]
    archived["facts"]["snapshot_contract"].pop("snapshot_dependencies")
    old_archive = deepcopy(archived)
    row.facts = facts
    replacement[3].is_current, replacement[3].promotion_state = False, "superseded"
    later[3].is_current, later[3].promotion_state = True, "promoted"
    await db[0].flush()
    calls = stub_llm(monkeypatch, continuity, countdown("20"))
    refreshed = await snapshot(later)
    assert len(calls) == 1 and await current(db, 2)
    assert refreshed.facts["snapshot_history"][0] == old_archive


@pytest.mark.parametrize("changed", ["source_body", "fact_context"])
async def test_read_guard_refreshes_cached_predecessor_objects_from_sql_truth(
    db,
    monkeypatch,
    changed,
):
    first, _later = await chain(db, monkeypatch)
    predecessor = await snapshot(first)
    if changed == "source_body":
        await db[0].execute(
            update(ChapterDraftVersionModel)
            .where(
                ChapterDraftVersionModel.id == first[3].id,
            )
            .values(content_md=first[3].content_md + "新的观测记录。")
            .execution_options(
                synchronize_session=False,
            )
        )
    else:
        await db[0].execute(
            update(ChapterStateSnapshotModel)
            .where(
                ChapterStateSnapshotModel.id == predecessor.id,
            )
            .values(time_anchor="第二天午后")
            .execution_options(synchronize_session=False)
        )
    with pytest.raises(RuntimeError, match="source_context_unavailable: previous_chapter_snapshot"):
        await load(db, 2)
    assert not await current(db, 2)


async def test_public_guard_binds_expected_source_is_read_only_and_isolates_projects(
    db, monkeypatch
):
    stub_llm(monkeypatch, continuity, {"facts": []})
    row = await snapshot(db)
    before = deepcopy(row.facts)
    source = {key: row.facts["snapshot_contract"][key] for key in KNOWLEDGE_SOURCE_KEYS}
    assert await continuity.snapshot_evidence_current(
        db[0],
        project_id=db[1].id,
        chapter_number=1,
        source_lineage=source,
    )
    source["source_version_no"] += 1
    assert not await continuity.snapshot_evidence_current(
        db[0],
        project_id=db[1].id,
        chapter_number=1,
        source_lineage=source,
    )
    assert not await continuity.snapshot_evidence_current(
        db[0],
        project_id=uuid4(),
        chapter_number=1,
    )
    assert row.facts == before and not db[0].dirty and not db[0].new


@pytest.mark.parametrize("length", [4, 9])
async def test_batch_guards_check_each_prefix_once_and_never_cache_across_readbacks(
    db,
    monkeypatch,
    length,
):
    books = await chain(db, monkeypatch, length=length)
    statements = []

    def record(_conn, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement)

    engine = db[0].bind.engine.sync_engine
    event.listen(engine, "before_cursor_execute", record)
    try:
        result = await continuity.snapshot_evidence_statuses(
            db[0],
            project_id=db[1].id,
            chapter_numbers=range(1, length + 1),
        )
    finally:
        event.remove(engine, "before_cursor_execute", record)
    assert result == dict.fromkeys(range(1, length + 1), True)
    # A full linear source check currently takes six SELECTs/chapter. The
    # generous bound still catches repeating the prefix for every chapter.
    assert len(statements) <= 8 * length
    assert all(statement.lstrip().upper().startswith("SELECT") for statement in statements)
    replacement = await _replace_source(books[0], "记录仪显示剩余十小时。")
    stub_llm(monkeypatch, continuity, countdown("10", "十小时"))
    await snapshot(replacement)
    refreshed = await continuity.snapshot_evidence_statuses(
        db[0],
        project_id=db[1].id,
        chapter_numbers=range(1, length + 1),
    )
    assert refreshed == {1: True, **dict.fromkeys(range(2, length + 1), False)}


async def test_batch_guard_matches_each_expected_source_and_fails_closed_on_missing_binding(
    db,
    monkeypatch,
):
    first, second, third = await chain(db, monkeypatch, length=3)
    expected = {}
    for number, book in enumerate((first, second, third), start=1):
        row = await snapshot(book)
        expected[number] = {
            key: row.facts["snapshot_contract"][key] for key in KNOWLEDGE_SOURCE_KEYS
        }
    expected[2]["project_id"] = str(uuid4())
    expected.pop(3)
    assert await continuity.snapshot_evidence_statuses(
        db[0],
        project_id=db[1].id,
        chapter_numbers=[3, 2, 1, 2],
        source_lineages=expected,
    ) == {1: True, 2: False, 3: False}


async def preloaded_sources(books):
    sources = {}
    for book in books:
        row = await snapshot(book)
        source, rejection = await validate_adopted_knowledge_source(
            book[0],
            project_id=book[1].id,
            chapter_id=book[2].id,
            source_chapter_draft_version_id=book[3].id,
            chapter_md=book[3].content_md,
            lock=False,
        )
        assert rejection is None
        sources[book[2].chapter_number] = (book[2].id, source, row)
    return sources


async def test_pure_preloaded_guard_matches_db_batch_without_sql_or_mutation(db, monkeypatch):
    books = await chain(db, monkeypatch, length=3)
    sources = await preloaded_sources(books)
    bodies = {book[2].chapter_number: book[3].content_md for book in books}
    expected = await continuity.snapshot_evidence_statuses(
        db[0],
        project_id=db[1].id,
        chapter_numbers=sources,
    )
    before = [deepcopy(row.facts) for _, _, row in sources.values()]
    statements = []

    def record(*args):
        statements.append(args)

    engine = db[0].bind.engine.sync_engine
    event.listen(engine, "before_cursor_execute", record)
    try:
        assert (
            continuity.snapshot_evidence_for_sources(
                dict(reversed(list(sources.items()))), source_bodies=bodies
            )
            == expected
        )
        assert continuity.snapshot_evidence_for_sources(sources) == dict.fromkeys(sources, False)
    finally:
        event.remove(engine, "before_cursor_execute", record)
    assert not statements
    assert before == [row.facts for _, _, row in sources.values()]
    assert not db[0].dirty and not db[0].new


@pytest.mark.parametrize("field", [*KNOWLEDGE_SOURCE_KEYS, "is_usable"])
async def test_pure_preloaded_guard_keeps_all_source_checks(db, monkeypatch, field):
    books = await chain(db, monkeypatch)
    sources = await preloaded_sources(books)
    bodies = {book[2].chapter_number: book[3].content_md for book in books}
    assert continuity.snapshot_evidence_for_sources(sources, source_bodies=bodies) == {
        1: True,
        2: True,
    }
    row = sources[1][2]
    facts = deepcopy(row.facts)
    contract = facts["snapshot_contract"]
    contract[field] = (
        False
        if field == "is_usable"
        else (contract[field] + 1 if field == "source_version_no" else "wrong-binding")
    )
    row.facts = facts
    assert continuity.snapshot_evidence_for_sources(sources, source_bodies=bodies) == {
        1: False,
        2: False,
    }


@pytest.mark.parametrize("change", ["source", "context", "gap", "mixed_project"])
async def test_pure_preloaded_guard_rejects_changed_or_missing_predecessor(db, monkeypatch, change):
    books = await chain(db, monkeypatch, length=3)
    sources = await preloaded_sources(books)
    bodies = {book[2].chapter_number: book[3].content_md for book in books}
    assert continuity.snapshot_evidence_for_sources(sources, source_bodies=bodies) == dict.fromkeys(
        sources, True
    )
    if change == "source":
        sources[1][1]["source_chapter_draft_version_id"] = str(uuid4())
    elif change == "context":
        sources[1][2].time_anchor = "重新核验的时间锚点"
    elif change == "gap":
        sources.pop(2)
    else:
        sources[1][1]["project_id"] = str(uuid4())
    result = continuity.snapshot_evidence_for_sources(sources, source_bodies=bodies)
    assert result[3] is False
    if change == "mixed_project":
        assert result == {1: False, 2: False, 3: False}
    elif change != "gap":
        assert result[2] is False
