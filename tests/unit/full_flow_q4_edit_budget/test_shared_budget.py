"""Behavior RED specification for mainselection; this lane does not run it.

All positive rows below demonstrate accounting only. They are ordinary,
ungraded, noncurrent candidates and confer no native execution/adoption rights.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from copy import deepcopy
import hashlib
import inspect
import itertools
import json
from uuid import uuid4

import pytest
from sqlalchemy import event
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.orm import Session

from bestseller.infra.db.models import (
    BookProductionControlModel,
    ChapterDraftVersionModel,
    ChapterModel,
    ExecutionOperationModel,
    ProjectModel,
    WorkflowRunModel,
)

pytestmark = pytest.mark.unit

KEY = "chapter_automatic_revision_budget"
SCHEMA = "chapter-automatic-revision-budget.v1"
RECEIPT = "chapter-automatic-revision-budget-receipt.v1"
BRANCHES = ("fact", "commercial", "length", "deslop", "internal_edit")


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


async def reserve(api, rows, key, **overrides):
    return await api.reserve_chapter_automatic_revision(**rows.args(key, **overrides))


async def denied(api, rows, key, suffix, **overrides):
    metadata_before = await rows.metadata()
    money_before = await rows.financial_rows()
    with pytest.raises(api.AutomaticRevisionBudgetError) as caught:
        await reserve(api, rows, key, **overrides)
    assert caught.value.code == f"automatic_revision_budget:{suffix}"
    assert await rows.metadata() == metadata_before
    assert await rows.financial_rows() == money_before
    return caught.value


def test_public_api_accepts_native_ids_factory_settings_only(api):
    signature = inspect.signature(api.reserve_chapter_automatic_revision)
    assert set(signature.parameters) == {
        "session_factory", "settings", "project_id", "chapter_id",
        "source_draft_id", "operation_key",
    }
    assert all(p.kind == inspect.Parameter.KEYWORD_ONLY for p in signature.parameters.values())
    assert all(p.default == inspect.Parameter.empty for p in signature.parameters.values())
    assert inspect.iscoroutinefunction(api.reserve_chapter_automatic_revision)


@pytest.mark.parametrize("extra", [
    {"event_id": "another-event"}, {"repair_event_id": "another-event"},
    {"approved": True}, {"approvalflag": True}, {"reset": True},
    {"limit": 20}, {"workflow_run_id": uuid4()}, {"body_sha256": "a" * 64},
    {"source": {"is_current": True}},
])
def test_caller_cannot_supply_permission_reset_event_or_body(api, extra):
    with pytest.raises(TypeError):
        api.reserve_chapter_automatic_revision(
            session_factory=None, settings=None, project_id=uuid4(),
            chapter_id=uuid4(), source_draft_id=uuid4(), operation_key="a" * 64,
            **extra,
        )


async def test_first_reservation_commits_db_bound_copy_on_write_json_only(api, rows):
    await rows.seed()
    key = await rows.operation("fact")
    before = await rows.metadata()
    money = await rows.financial_rows()
    receipt = await reserve(api, rows, key)
    stored = await rows.metadata()  # distinct real session, not the returned mapping
    journal = stored[KEY]
    expected_event = sha(json.dumps(
        [SCHEMA, str(rows.project_id), str(rows.chapter_id), str(rows.source_id), sha(rows.body)],
        ensure_ascii=False, separators=(",", ":"),
    ))
    assert receipt["schema"] == RECEIPT
    assert receipt["mode"] == "reserved"
    assert (receipt["round_no"], receipt["rounds_used"], receipt["rounds_remaining"]) == (1, 1, 1)
    assert receipt["event_key"] == expected_event == journal["event_key"]
    assert journal["schema"] == SCHEMA and journal["limit"] == 2
    assert journal["project_id"] == str(rows.project_id)
    assert journal["chapter_id"] == str(rows.chapter_id)
    assert journal["frozen_source"] == {
        "draft_id": str(rows.source_id), "body_sha256": sha(rows.body),
    }
    assert journal["history"] == [{
        "round_no": 1, "operation_key": key, "operation_scope_id": rows.scope,
        "operation_input_sha256": sha("input:fact"),
        "source_draft_id": str(rows.source_id), "source_body_sha256": sha(rows.body),
    }]
    assert {k: v for k, v in stored.items() if k != KEY} == before
    assert KEY not in before
    assert await rows.financial_rows() == money
    async with rows.sessions() as session:
        chapter = await session.get(ChapterModel, rows.chapter_id)
        draft = await session.get(ChapterDraftVersionModel, rows.source_id)
        assert chapter.production_state == "blocked"
        assert draft.content_md == rows.body
        assert draft.is_current is False and draft.promotion_state == "candidate"
        assert draft.promotion_score is None and draft.promotion_metadata == {}
    # A caller retaining/mutating a JSON receipt cannot mutate the DB journal.
    receipt["event_key"] = "caller mutation"
    assert await rows.metadata() == stored


@pytest.mark.parametrize(("first_branch", "second_branch"), itertools.product(BRANCHES, repeat=2))
async def test_all_body_candidate_branches_share_exactly_two_rounds(
    api, rows, first_branch, second_branch,
):
    await rows.seed()
    first = await rows.operation(f"{first_branch}:candidate-1")
    second = await rows.operation(f"{second_branch}:candidate-2")
    third = await rows.operation("length:another-wrapper:internal-edit:candidate-3")
    one = await reserve(api, rows, first)
    two = await reserve(api, rows, second)
    assert one["event_key"] == two["event_key"]
    assert (two["round_no"], two["rounds_used"], two["rounds_remaining"]) == (2, 2, 0)
    await denied(api, rows, third, "exhausted")


@pytest.mark.parametrize("native_status", ["pending", "completed", "stopped", "unknown"])
async def test_native_same_operation_reentry_is_idempotent_even_when_full(api, rows, native_status):
    await rows.seed()
    first, second = await rows.operation("first"), await rows.operation("second")
    initial = await reserve(api, rows, first)
    await reserve(api, rows, second)
    async with rows.sessions.begin() as session:
        operation = await session.get(ExecutionOperationModel, first)
        operation.status = native_status  # bookkeeping row; no result/adoption fabricated
        operation.owner = "later-native-owner"
    metadata, money = await rows.metadata(), await rows.financial_rows()
    returned = await reserve(api, rows, first)
    assert returned["mode"] == "already_reserved"
    assert returned["round_no"] == initial["round_no"] == 1
    assert returned["rounds_used"] == 2 and returned["rounds_remaining"] == 0
    assert await rows.metadata() == metadata
    assert await rows.financial_rows() == money


@pytest.mark.parametrize("failure", ["stopped", "unknown"])
async def test_failed_unknown_or_outer_rollback_does_not_refund(api, rows, failure):
    await rows.seed()
    first, second, third = [await rows.operation(label) for label in ("one", "two", "three")]
    await reserve(api, rows, first)
    async with rows.sessions.begin() as session:
        operation = await session.get(ExecutionOperationModel, first)
        operation.status = failure
    # A caller transaction is allowed to fail after the fresh committed reserve.
    async with rows.sessions() as outer:
        async with outer.begin():
            outer.add(WorkflowRunModel(
                id=uuid4(), project_id=rows.project_id, workflow_type="q4_budget_fixture",
                status="failed", scope_type="chapter", scope_id=rows.chapter_id,
                metadata_json={"resume": True},
            ))
            await outer.rollback()
    two = await reserve(api, rows, second)
    assert two["round_no"] == 2
    await denied(api, rows, third, "exhausted")


async def test_workflow_resume_factory_and_body_packaging_cannot_open_an_event(api, rows):
    await rows.seed()
    keys = [await rows.operation(label) for label in ("fact", "length", "internal-edit")]
    one = await reserve(api, rows, keys[0])
    wrapped = await rows.source("# 后续包装\n\n" + rows.body)
    async with rows.sessions.begin() as session:
        for status in ("failed", "running"):
            session.add(WorkflowRunModel(
                id=uuid4(), project_id=rows.project_id, workflow_type="q4_budget_fixture",
                status=status, scope_type="chapter", scope_id=rows.chapter_id,
                metadata_json={"resume": True, "repair_event_id": uuid4().hex, "approved": True},
            ))
        chapter = await session.get(ChapterModel, rows.chapter_id)
        chapter.metadata_json = {**chapter.metadata_json,
                                 "approvalflag": True, "repair_event_id": uuid4().hex,
                                 "chapter_review_active_cycle": {"cycle_id": "new-review-wrapper"}}
    restarted_factory = async_sessionmaker(rows.engine, expire_on_commit=False)
    two = await reserve(
        api, rows, keys[1], source_draft_id=wrapped, session_factory=restarted_factory,
    )
    assert one["event_key"] == two["event_key"]
    assert (await rows.metadata())[KEY]["frozen_source"]["draft_id"] == str(rows.source_id)
    await denied(api, rows, keys[2], "exhausted", source_draft_id=wrapped)


async def test_first_call_freezes_earliest_db_draft_not_supplied_later_candidate(api, rows):
    await rows.seed()
    later = await rows.source("后续候选，不是冻结初稿。")
    receipt = await reserve(api, rows, await rows.operation("later"), source_draft_id=later)
    journal = (await rows.metadata())[KEY]
    assert journal["frozen_source"] == {
        "draft_id": str(rows.source_id), "body_sha256": sha(rows.body),
    }
    assert journal["history"][0]["source_draft_id"] == str(later)
    assert receipt["round_no"] == 1


async def test_empty_db_source_cannot_freeze_an_event(api, rows):
    await rows.seed()
    async with rows.sessions.begin() as session:
        source = await session.get(ChapterDraftVersionModel, rows.source_id)
        source.content_md = "   \n\n"
    await denied(api, rows, await rows.operation("empty"), "source_empty")


@pytest.mark.parametrize(("field", "suffix"), [
    ("project_id", "project_missing"), ("chapter_id", "chapter_missing"),
    ("source_draft_id", "source_missing"), ("operation_key", "operation_missing"),
])
async def test_missing_native_identity_rejects_without_mutation(api, rows, field, suffix, request):
    await rows.seed()
    key = await rows.operation("exists")
    missing = sha("missing-operation") if field == "operation_key" else uuid4()
    error = await denied(api, rows, key, suffix, **{field: missing})
    if field == "operation_key":
        assert error.differences == [{
            "field": "operation_key.native_row",
            "expected": {"type": "ExecutionOperationModel", "present": True},
            "actual": {"type": "NoneType", "present": False},
        }]
        request.node.user_properties.append(("automatic_revision_budget_rejection", json.dumps({
            "code": error.code, "differences": error.differences,
        }, sort_keys=True)))


@pytest.mark.parametrize("wrong_field", ["project_id", "chapter_id"])
async def test_real_source_row_in_another_project_or_chapter_is_rejected(api, rows, wrong_field):
    await rows.seed()
    foreign_source = await rows.source("另一个普通来源。", **{wrong_field: uuid4()})
    await denied(api, rows, await rows.operation("foreign-source"), "source_mismatch",
                 source_draft_id=foreign_source)


async def test_real_chapter_row_cannot_be_used_for_another_project(api, rows):
    await rows.seed()
    other = uuid4()
    async with rows.sessions.begin() as session:
        session.add(ProjectModel(
            id=other, slug=f"q4-peer-{other.hex[:12]}", title="Peer", genre="family",
            target_word_count=2000, target_chapters=1, metadata_json={},
        ))
    await denied(api, rows, await rows.operation("wrong-project"), "chapter_project_mismatch",
                 project_id=other)


async def test_native_operation_scope_cannot_be_borrowed_from_other_project(api, rows):
    await rows.seed()
    key = await rows.operation("foreign-scope", scope=f"project:{uuid4()}")
    await denied(api, rows, key, "operation_scope_mismatch")


async def test_native_operation_already_bound_to_other_chapter_cannot_be_borrowed(api, rows):
    await rows.seed()
    key = await rows.operation("one-source-only")
    await reserve(api, rows, key)
    other_chapter, other_source = uuid4(), uuid4()
    async with rows.sessions.begin() as session:
        session.add(ChapterModel(
            id=other_chapter, project_id=rows.project_id, chapter_number=2,
            chapter_goal="另一个选择", metadata_json={"keep": [8, 2]},
        ))
        session.add(ChapterDraftVersionModel(
            id=other_source, project_id=rows.project_id, chapter_id=other_chapter,
            version_no=1, content_md="另一个章节的普通候选。", word_count=15,
            is_current=False, promotion_state="candidate", promotion_metadata={},
        ))
    await denied(api, rows, key, "operation_source_changed",
                 chapter_id=other_chapter, source_draft_id=other_source)
    async with rows.sessions() as session:
        other = await session.get(ChapterModel, other_chapter)
        assert other.metadata_json == {"keep": [8, 2]}


async def test_operation_without_durable_native_budget_parent_cannot_reserve(api, rows):
    await rows.seed()
    key = await rows.operation("orphan")
    from bestseller.infra.db.models import ExecutionBudgetModel

    async with rows.sessions.begin() as session:
        # SQLite's disabled FKs permit this ordinary corruption negative.
        budget = await session.get(ExecutionBudgetModel, rows.scope)
        await session.delete(budget)
    await denied(api, rows, key, "operation_budget_missing")


async def test_creation_scope_is_read_from_persisted_project_not_workflow_wrapper(api, rows):
    await rows.seed(scope=f"conception:{uuid4()}")
    receipt = await reserve(api, rows, await rows.operation("creation-scope"))
    assert receipt["round_no"] == 1
    assert (await rows.metadata())[KEY]["history"][0]["operation_scope_id"] == rows.scope


async def test_same_operation_with_different_source_is_not_a_free_new_candidate(api, rows):
    await rows.seed()
    key = await rows.operation("one")
    await reserve(api, rows, key)
    later = await rows.source("另一个候选。")
    await denied(api, rows, key, "operation_source_changed", source_draft_id=later)


async def test_native_input_hash_changed_after_reservation_is_rejected(api, rows):
    await rows.seed()
    key = await rows.operation("one")
    await reserve(api, rows, key)
    async with rows.sessions.begin() as session:
        operation = await session.get(ExecutionOperationModel, key)
        operation.input_hash = sha("different native request")
    await denied(api, rows, key, "operation_binding_changed")


@pytest.mark.parametrize("status", ["completed", "stopped", "unknown", "unsent_reclaim_receipt"])
async def test_unrecorded_nonpending_operation_cannot_be_backfilled_as_new_attempt(
    api, rows, status,
):
    await rows.seed()
    await denied(api, rows, await rows.operation("not-new", status=status), "operation_not_pending")


@pytest.mark.parametrize("mutation", ["changed", "deleted", "empty"])
async def test_frozen_initial_source_is_requeried_from_db_on_every_reservation(api, rows, mutation):
    await rows.seed()
    first, second = await rows.operation("one"), await rows.operation("two")
    await reserve(api, rows, first)
    later = await rows.source("后续独立候选。")
    async with rows.sessions.begin() as session:
        source = await session.get(ChapterDraftVersionModel, rows.source_id)
        if mutation == "deleted":
            await session.delete(source)
        else:
            source.content_md = "" if mutation == "empty" else "冻结稿被原地修改。"
    await denied(api, rows, second, "frozen_source_changed", source_draft_id=later)


async def test_historical_nonroot_source_hash_is_also_checked(api, rows):
    await rows.seed()
    later = await rows.source("第二个普通稿本。")
    await reserve(api, rows, await rows.operation("one"), source_draft_id=later)
    async with rows.sessions.begin() as session:
        source = await session.get(ChapterDraftVersionModel, later)
        source.content_md += "被改过。"
    await denied(api, rows, await rows.operation("two"), "history_source_changed")


@pytest.mark.parametrize("bad_journal", [None, [], "reset", {}, {"history": []}])
async def test_present_but_invalid_journal_is_not_treated_as_absent(api, rows, bad_journal):
    await rows.seed()
    await rows.set_metadata({**await rows.metadata(), KEY: bad_journal})
    await denied(api, rows, await rows.operation("one"), "history_invalid")


@pytest.mark.parametrize("damage", [
    "history_null", "history_empty", "history_mapping", "duplicate", "gap", "bool_round",
    "too_many", "limit", "schema", "event", "project", "chapter", "input_hash",
    "bool_limit", "scope", "source_hash", "missing_operation", "unknown_history_member",
    "frozen_id", "frozen_hash", "frozen_mapping",
])
async def test_corrupt_history_fails_closed_and_never_resets(api, rows, damage, request):
    await rows.seed()
    await reserve(api, rows, await rows.operation("one"))
    metadata = await rows.metadata()
    journal = metadata[KEY]
    if damage == "history_null":
        journal["history"] = None
    elif damage == "history_empty":
        journal["history"] = []
    elif damage == "history_mapping":
        journal["history"] = {"0": journal["history"][0]}
    elif damage == "duplicate":
        journal["history"].append({**deepcopy(journal["history"][0]), "round_no": 2})
    elif damage in {"gap", "bool_round"}:
        journal["history"][0]["round_no"] = True if damage == "bool_round" else 2
    elif damage == "too_many":
        journal["history"] *= 3
    elif damage in {"limit", "bool_limit"}:
        journal["limit"] = True if damage == "bool_limit" else 3
    elif damage == "schema":
        journal["schema"] = "unknown-version"
    elif damage in {"event", "project", "chapter"}:
        journal[
            {"event": "event_key", "project": "project_id", "chapter": "chapter_id"}[damage]
        ] = uuid4().hex
    elif damage in {"input_hash", "scope", "source_hash", "missing_operation"}:
        field = {"input_hash": "operation_input_sha256", "scope": "operation_scope_id",
                 "source_hash": "source_body_sha256", "missing_operation": "operation_key"}[damage]
        journal["history"][0][field] = sha("tampered")
    elif damage == "frozen_id":
        journal["frozen_source"]["draft_id"] = str(uuid4())
    elif damage == "frozen_hash":
        journal["frozen_source"]["body_sha256"] = sha("tampered")
    elif damage == "frozen_mapping":
        journal["frozen_source"] = None
    else:
        journal["history"][0]["refund"] = True
    await rows.set_metadata(metadata)
    # Compare JSON wire types, not Python's True == 1 mapping equality. This
    # must prove that the corrupt bool reached the real persisted row first.
    stored = await rows.metadata()
    assert json.dumps(stored, sort_keys=True) == json.dumps(metadata, sort_keys=True)
    # Shape and historical identity corruption share one terminal history code.
    error = await denied(api, rows, await rows.operation("two"), "history_invalid")
    if damage in {"bool_round", "bool_limit"}:
        expected_field = (KEY + ".history[0].round_no" if damage == "bool_round"
                          else KEY + ".limit")
        assert error.differences == [{
            "field": expected_field,
            "expected": {"type": "int", "value": 1 if damage == "bool_round" else 2},
            "actual": {"type": "bool", "value": True},
        }]
        request.node.user_properties.append(("automatic_revision_budget_rejection", json.dumps({
            "code": error.code, "differences": error.differences,
        }, sort_keys=True)))


async def test_legacy_project_has_no_new_budget_or_metadata_mutation(api, rows):
    await rows.seed(marked=False)
    before = await rows.metadata()
    for label in ("fact", "commercial", "length"):
        key = await rows.operation(label)
        money = await rows.financial_rows()
        receipt = await reserve(api, rows, key)
        assert receipt["mode"] == "legacy"
        assert receipt["event_key"] is None and receipt["round_no"] is None
        assert receipt["rounds_used"] is None and receipt["rounds_remaining"] is None
        assert await rows.metadata() == before
        assert await rows.financial_rows() == money


@pytest.mark.parametrize("marker", [None, True, {}, {"protocol": "future-reader-first"}])
async def test_invalid_present_protocol_marker_does_not_get_legacy_exemption(api, rows, marker):
    await rows.seed()
    async with rows.sessions.begin() as session:
        project = await session.get(ProjectModel, rows.project_id)
        project.metadata_json = {**project.metadata_json, "reader_first_planning": marker}
    await denied(api, rows, await rows.operation("one"), "protocol_invalid")


async def test_removing_marker_after_consumption_cannot_restore_legacy_allowance(api, rows):
    await rows.seed()
    await reserve(api, rows, await rows.operation("one"))
    async with rows.sessions.begin() as session:
        project = await session.get(ProjectModel, rows.project_id)
        project.metadata_json = {
            k: v for k, v in project.metadata_json.items() if k != "reader_first_planning"
        }
    await denied(api, rows, await rows.operation("two"), "protocol_removed")


@pytest.mark.parametrize("control", ["pause", "stop"])
async def test_operator_control_precedes_new_reservation(api, rows, control):
    await rows.seed()
    async with rows.sessions.begin() as session:
        row = await session.get(BookProductionControlModel, rows.project_id)
        row.desired_state = control
        row.command_serial = 1
    await denied(api, rows, await rows.operation("one"), "control_halted")


async def test_uncommitted_native_source_is_not_replaced_by_caller_snapshot(api, rows):
    await rows.seed()
    key = await rows.operation("one")
    async with rows.sessions() as outer:
        transient_id = uuid4()
        outer.add(ChapterDraftVersionModel(
            id=transient_id, project_id=rows.project_id, chapter_id=rows.chapter_id,
            version_no=2, content_md="未提交候选。", word_count=6,
            is_current=False, promotion_state="candidate", promotion_metadata={},
        ))
        await outer.flush()
        await denied(api, rows, key, "source_missing", source_draft_id=transient_id)
        await outer.rollback()


@pytest.mark.parametrize(
    "contamination", ["transaction", "identity_map", "new", "dirty", "deleted"],
)
async def test_factory_must_return_clean_independent_session(api, rows, contamination):
    await rows.seed()
    key = await rows.operation("one")

    @asynccontextmanager
    async def contaminated_factory():
        async with rows.sessions() as session:
            held = None
            if contamination == "transaction":
                await session.begin()
            elif contamination == "new":
                held = ProjectModel(id=uuid4())
                session.add(held)
            else:
                held = await session.get(ChapterModel, rows.chapter_id)
                if contamination == "identity_map":
                    await session.commit()  # clean transaction, retained ORM identity
                elif contamination == "dirty":
                    held.title = "uncommitted outer change"
                else:
                    await session.delete(held)
            yield session
            # Keep a strong ORM reference until the rejection has occurred.
            assert held is not None or contamination == "transaction"

    await denied(api, rows, key, "fresh_session_required", session_factory=contaminated_factory)


async def test_borrowed_session_rejection_preserves_callers_transaction_and_pending_change(
    api, rows,
):
    await rows.seed()
    key = await rows.operation("one")
    async with rows.sessions() as outer:
        chapter = await outer.get(ChapterModel, rows.chapter_id)
        chapter.title = "pending caller edit"
        transaction = outer.get_transaction()
        await denied(api, rows, key, "fresh_session_required", session_factory=lambda: outer)
        assert outer.get_transaction() is transaction and outer.in_transaction()
        assert chapter in outer.dirty and chapter.title == "pending caller edit"
        await outer.rollback()


async def test_commit_failure_returns_no_receipt_and_does_not_make_a_round_durable(api, rows):
    await rows.seed()
    key = await rows.operation("one")

    def fail_commit(session):
        if session.bind is rows.engine.sync_engine:
            raise SQLAlchemyError("untrusted DB diagnostic must not enter the budget error")

    event.listen(Session, "before_commit", fail_commit)
    try:
        await denied(api, rows, key, "storage_unavailable")
    finally:
        event.remove(Session, "before_commit", fail_commit)
    assert KEY not in await rows.metadata()


@pytest.mark.parametrize("marked", [False, True])
async def test_only_chapter_metadata_can_be_written_and_legacy_has_no_dml(api, rows, marked):
    await rows.seed(marked=marked)
    key = await rows.operation("one")
    writes = []

    def observe(_connection, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().split(None, 1)[0].upper() in {"INSERT", "UPDATE", "DELETE"}:
            writes.append(statement)

    event.listen(rows.engine.sync_engine, "before_cursor_execute", observe)
    try:
        await reserve(api, rows, key)
    finally:
        event.remove(rows.engine.sync_engine, "before_cursor_execute", observe)
    if marked:
        assert len(writes) == 1 and writes[0].startswith("UPDATE chapters SET ")
        assert "metadata=" in writes[0]
    else:
        assert writes == []


async def test_query_construction_requests_control_project_chapter_draft_lock_order(api, rows):
    """Statement evidence only: SQLite does not exercise PostgreSQL row locks."""
    await rows.seed()
    key = await rows.operation("one")
    locking = []
    queries = []

    def observe(state):
        if state.session.bind is not rows.engine.sync_engine or not state.is_select:
            return
        sql = str(state.statement.compile(dialect=postgresql.dialect()))
        queries.append(sql)
        if "FOR UPDATE" in sql:
            locking.append(sql)

    event.listen(Session, "do_orm_execute", observe)
    try:
        await reserve(api, rows, key)
    finally:
        event.remove(Session, "do_orm_execute", observe)
    positions = {}
    for table in ("book_production_control", "projects", "chapters", "chapter_draft_versions"):
        positions[table] = next(
            (i for i, sql in enumerate(locking) if f"FROM {table}" in sql), None,
        )
        assert positions[table] is not None, f"Missing native FOR UPDATE on {table}"
    assert list(positions.values()) == sorted(positions.values())
    for sql in locking:
        if "FROM chapter_draft_versions" in sql:
            where = sql.split("WHERE", 1)[1]
            assert "chapter_draft_versions.project_id =" in where
            assert "chapter_draft_versions.chapter_id =" in where
    draft_reads = [i for i, sql in enumerate(queries) if "FROM chapter_draft_versions" in sql]
    budget_reads = [i for i, sql in enumerate(queries) if "FROM execution_budgets" in sql]
    operation_reads = [i for i, sql in enumerate(queries) if "FROM execution_operations" in sql]
    assert draft_reads and budget_reads and operation_reads
    assert max(draft_reads) < min(budget_reads) < min(operation_reads)
