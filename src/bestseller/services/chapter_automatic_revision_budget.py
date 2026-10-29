"""Durable two-candidate accounting for the first reader-first repair event.

Call with an already durable native operation, before generation. The caller
must checkpoint/release its original transaction and locks first, then recheck
native source CAS/control/owner/Unknown at send and candidate commit. A receipt
is accounting, never permission to send, replay, grade or adopt a body.

Only Chapter metadata is written. The financial ledger, original drafts and
quality blockers remain untouched. Human-authorized new events and grouping
multiple physical calls into one candidate are intentionally unsupported.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from copy import deepcopy
import hashlib
import json
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from bestseller.infra.db.models import (
    BookProductionControlModel,
    ChapterDraftVersionModel,
    ChapterModel,
    ExecutionBudgetModel,
    ExecutionOperationModel,
    ProjectModel,
)
from bestseller.services.retry_ledger import project_execution_scope
from bestseller.services.source_bound_planning_content import reader_first_binding
from bestseller.settings import AppSettings

JOURNAL_KEY = "chapter_automatic_revision_budget"
SCHEMA = "chapter-automatic-revision-budget.v1"
RECEIPT_SCHEMA = "chapter-automatic-revision-budget-receipt.v1"
LIMIT = 2
_JOURNAL_FIELDS = {
    "schema", "project_id", "chapter_id", "event_key", "frozen_source", "limit", "history",
}
_RESERVATION_FIELDS = {
    "round_no", "operation_key", "operation_scope_id", "operation_input_sha256",
    "source_draft_id", "source_body_sha256",
}


class AutomaticRevisionBudgetError(ValueError):
    """Fail closed without embedding source prose or connection details."""

    def __init__(self, reason: str, *, differences: list[dict[str, Any]] | None = None) -> None:
        self.code = "automatic_revision_budget:" + reason
        self.differences = deepcopy(differences) if differences is not None else []
        super().__init__(self.code)


def _need(condition: object, reason: str) -> None:
    if not condition:
        raise AutomaticRevisionBudgetError(reason)


def _history_integer(value: object, expected: int, field: str) -> None:
    """JSON booleans are never ordinals/limits, even though True == 1."""
    if type(value) is not int or value != expected:
        raise AutomaticRevisionBudgetError("history_invalid", differences=[{
            "field": field,
            "expected": {"type": "int", "value": expected},
            "actual": {"type": type(value).__name__,
                       "value": value if type(value) in {int, bool} else None},
        }])


def _is_hash(value: object) -> bool:
    return (
        type(value) is str and len(value) == 64
        and all(char in "0123456789abcdef" for char in value)
    )


def _native_id(value: object, reason: str) -> UUID:
    if isinstance(value, UUID):
        return value
    _need(type(value) is str, reason)
    try:
        parsed = UUID(value)
    except ValueError:
        raise AutomaticRevisionBudgetError(reason) from None
    _need(str(parsed) == value, reason)
    return parsed


def _body_hash(body: object, reason: str) -> str:
    _need(type(body) is str and body.strip(), reason)
    try:
        return hashlib.sha256(body.encode("utf-8")).hexdigest()
    except UnicodeEncodeError:
        raise AutomaticRevisionBudgetError(reason) from None


def _event_key(project_id: UUID, chapter_id: UUID, frozen: dict[str, str]) -> str:
    wire = json.dumps(
        [SCHEMA, str(project_id), str(chapter_id), frozen["draft_id"], frozen["body_sha256"]],
        ensure_ascii=False, separators=(",", ":"),
    )
    return hashlib.sha256(wire.encode("utf-8")).hexdigest()


def _fresh(session: object) -> None:
    _need(
        isinstance(session, AsyncSession) and isinstance(session.bind, AsyncEngine)
        and not session.in_transaction() and not session.in_nested_transaction()
        and not session.identity_map and not session.new and not session.dirty
        and not session.deleted,
        "fresh_session_required",
    )
    _need(session.bind.dialect.name in {"postgresql", "sqlite"}, "storage_unavailable")


def _journal(
    raw: object, project_id: UUID, chapter_id: UUID, scope: str,
) -> dict[str, Any]:
    """Validate structure/internal bindings before trusting any stored ID."""
    reason = "history_invalid"
    _need(type(raw) is dict and raw.keys() == _JOURNAL_FIELDS, reason)
    _need(raw["schema"] == SCHEMA, reason)
    _need(raw["project_id"] == str(project_id) and raw["chapter_id"] == str(chapter_id), reason)
    _history_integer(raw["limit"], LIMIT, JOURNAL_KEY + ".limit")
    frozen = raw["frozen_source"]
    _need(type(frozen) is dict and frozen.keys() == {"draft_id", "body_sha256"}, reason)
    _native_id(frozen["draft_id"], reason)
    _need(_is_hash(frozen["body_sha256"]), reason)
    _need(raw["event_key"] == _event_key(project_id, chapter_id, frozen), reason)
    history = raw["history"]
    _need(type(history) is list and 1 <= len(history) <= LIMIT, reason)
    seen: set[str] = set()
    for index, item in enumerate(history, start=1):
        _need(type(item) is dict and item.keys() == _RESERVATION_FIELDS, reason)
        _history_integer(item["round_no"], index,
                         f"{JOURNAL_KEY}.history[{index - 1}].round_no")
        key = item["operation_key"]
        _need(_is_hash(key) and key not in seen, reason)
        seen.add(key)
        _native_id(item["source_draft_id"], reason)
        _need(item["operation_scope_id"] == scope, reason)
        _need(_is_hash(item["operation_input_sha256"]), reason)
        _need(_is_hash(item["source_body_sha256"]), reason)
        if item["source_draft_id"] == frozen["draft_id"]:
            _need(item["source_body_sha256"] == frozen["body_sha256"], reason)
    return deepcopy(raw)


def _receipt(mode: str, key: str, journal: dict[str, Any] | None, round_no: int | None) -> dict:
    used = len(journal["history"]) if journal is not None else None
    return {
        "schema": RECEIPT_SCHEMA, "mode": mode, "operation_key": key,
        "event_key": journal["event_key"] if journal is not None else None,
        "round_no": round_no, "rounds_used": used,
        "rounds_remaining": LIMIT - used if used is not None else None,
    }


async def _reserve_locked(
    session: AsyncSession, project_id: UUID, chapter_id: UUID,
    source_id: UUID, key: str,
) -> dict:
    # Match native lock ordering. No financial row is locked or written.
    control = await session.scalar(
        select(BookProductionControlModel)
        .where(BookProductionControlModel.project_id == project_id)
        .with_for_update().execution_options(populate_existing=True)
    )
    _need(control is None or control.desired_state == "run", "control_halted")
    project = await session.scalar(
        select(ProjectModel).where(ProjectModel.id == project_id)
        .with_for_update().execution_options(populate_existing=True)
    )
    _need(project is not None, "project_missing")
    chapter = await session.scalar(
        select(ChapterModel).where(
            ChapterModel.id == chapter_id, ChapterModel.project_id == project_id,
        )
        .with_for_update().execution_options(populate_existing=True)
    )
    if chapter is None:
        owner = await session.scalar(
            select(ChapterModel.project_id).where(ChapterModel.id == chapter_id),
        )
        _need(owner is not None, "chapter_missing")
        raise AutomaticRevisionBudgetError("chapter_project_mismatch")
    metadata = chapter.metadata_json
    _need(type(metadata) is dict, "history_invalid")
    _need(type(project.metadata_json) is dict, "protocol_invalid")
    try:
        marker = reader_first_binding(project)
    except (ValueError, OSError):
        raise AutomaticRevisionBudgetError("protocol_invalid") from None
    if marker is None:
        _need(JOURNAL_KEY not in metadata, "protocol_removed")
        return _receipt("legacy", key, None, None)

    scope = project_execution_scope(project)
    # Do not let a malformed persisted creation scope silently fall back.
    _need(project.metadata_json.get("execution_scope_id", scope) == scope,
          "operation_scope_mismatch")
    journal = (
        _journal(metadata[JOURNAL_KEY], project_id, chapter_id, scope)
        if JOURNAL_KEY in metadata else None
    )
    history = journal["history"] if journal is not None else []
    # Read IDs/ownership first so bad inputs cannot lock another project's
    # source rows. Revalidate the full source under this chapter's Draft locks.
    source_owner = (await session.execute(
        select(ChapterDraftVersionModel.project_id, ChapterDraftVersionModel.chapter_id)
        .where(ChapterDraftVersionModel.id == source_id),
    )).one_or_none()
    _need(source_owner is not None, "source_missing")
    _need(source_owner[0] == project_id and source_owner[1] == chapter_id, "source_mismatch")
    if journal is None:
        first = (await session.execute(
            select(ChapterDraftVersionModel.id, ChapterDraftVersionModel.project_id)
            .where(ChapterDraftVersionModel.chapter_id == chapter_id)
            .order_by(ChapterDraftVersionModel.version_no, ChapterDraftVersionModel.id)
            .limit(1),
        )).one_or_none()
        _need(first is not None, "source_missing")
        _need(first[1] == project_id, "source_mismatch")
        frozen_id = first[0]
    else:
        frozen_id = UUID(journal["frozen_source"]["draft_id"])
    source_ids = {source_id, frozen_id, *(UUID(item["source_draft_id"]) for item in history)}
    drafts = {
        draft.id: draft for draft in await session.scalars(
            select(ChapterDraftVersionModel).where(
                ChapterDraftVersionModel.id.in_(source_ids),
                ChapterDraftVersionModel.project_id == project_id,
                ChapterDraftVersionModel.chapter_id == chapter_id,
            )
            .order_by(ChapterDraftVersionModel.id)
            .with_for_update().execution_options(populate_existing=True)
        )
    }
    source = drafts.get(source_id)
    _need(source is not None, "source_missing")
    _need(source.project_id == project_id and source.chapter_id == chapter_id, "source_mismatch")
    source_hash = _body_hash(source.content_md, "source_empty")
    frozen = drafts.get(frozen_id)
    frozen_reason = "frozen_source_changed" if journal is not None else "source_mismatch"
    _need(frozen is not None and frozen.project_id == project_id
          and frozen.chapter_id == chapter_id, frozen_reason)
    frozen_hash = _body_hash(
        frozen.content_md, "frozen_source_changed" if journal is not None else "source_empty",
    )
    if journal is None:
        binding = {"draft_id": str(frozen_id), "body_sha256": frozen_hash}
        journal = {
            "schema": SCHEMA, "project_id": str(project_id), "chapter_id": str(chapter_id),
            "event_key": _event_key(project_id, chapter_id, binding),
            "frozen_source": binding, "limit": LIMIT, "history": history,
        }
    else:
        _need(frozen_hash == journal["frozen_source"]["body_sha256"], "frozen_source_changed")

    # Draft locks precede all native budget/operation reads. The parent budget
    # must exist even on SQLite where test FKs may be unenforced.
    budget = await session.get(ExecutionBudgetModel, scope, populate_existing=True)
    keys = {key, *(item["operation_key"] for item in history)}
    operations = {
        op.key: op for op in await session.scalars(
            select(ExecutionOperationModel).where(ExecutionOperationModel.key.in_(keys))
            .execution_options(populate_existing=True)
        )
    }
    operation = operations.get(key)
    if operation is None:
        raise AutomaticRevisionBudgetError("operation_missing", differences=[{
            "field": "operation_key.native_row",
            "expected": {"type": "ExecutionOperationModel", "present": True},
            "actual": {"type": "NoneType", "present": False},
        }])
    _need(operation.scope_id == scope, "operation_scope_mismatch")
    _need(budget is not None, "operation_budget_missing")
    _need(_is_hash(operation.input_hash), "operation_binding_changed")
    existing = next((item for item in history if item["operation_key"] == key), None)
    if existing is not None:
        _need(existing["source_draft_id"] == str(source_id)
              and existing["source_body_sha256"] == source_hash, "operation_source_changed")
        _need(existing["operation_input_sha256"] == operation.input_hash,
              "operation_binding_changed")
    for item in history:
        old_source = drafts.get(UUID(item["source_draft_id"]))
        _need(old_source is not None and old_source.project_id == project_id
              and old_source.chapter_id == chapter_id, "history_invalid")
        _need(_body_hash(old_source.content_md, "history_source_changed")
              == item["source_body_sha256"], "history_source_changed")
        old_op = operations.get(item["operation_key"])
        _need(old_op is not None and old_op.scope_id == item["operation_scope_id"]
              and old_op.input_hash == item["operation_input_sha256"], "history_invalid")
    if existing is not None:
        # Status/owner/lease changes never release a round or permit a resend.
        return _receipt("already_reserved", key, journal, existing["round_no"])

    _need(operation.status == "pending", "operation_not_pending")
    _need(len(history) < LIMIT, "exhausted")
    # The Project lock serializes accounting across its chapters. An operation
    # already associated with another source/chapter cannot be repackaged here.
    other_journals = await session.scalars(
        select(ChapterModel.metadata_json[JOURNAL_KEY]).where(
            ChapterModel.project_id == project_id, ChapterModel.id != chapter_id,
        )
    )
    for other in other_journals:
        if type(other) is dict and type(other.get("history")) is list:
            _need(not any(type(item) is dict and item.get("operation_key") == key
                          for item in other["history"]), "operation_source_changed")

    round_no = len(history) + 1
    history.append({
        "round_no": round_no, "operation_key": key,
        "operation_scope_id": operation.scope_id, "operation_input_sha256": operation.input_hash,
        "source_draft_id": str(source_id), "source_body_sha256": source_hash,
    })
    revised = deepcopy(metadata)
    revised[JOURNAL_KEY] = journal
    chapter.metadata_json = revised
    await session.flush()
    return _receipt("reserved", key, journal, round_no)


async def reserve_chapter_automatic_revision(
    *,
    session_factory: Callable[[], AbstractAsyncContextManager[AsyncSession]],
    settings: AppSettings,
    project_id: UUID,
    chapter_id: UUID,
    source_draft_id: UUID,
    operation_key: str,
) -> dict:
    """Commit one append under real Chapter locks, independently of caller rollback.

    Factory/settings/IDs are explicit; no global connection, permission flags,
    caller event ID or refund path exists. Legacy without a journal is read-only.
    Reentry returns the original accounting ordinal, including failed/Unknown
    operations. Only native execution controls decide whether any send is legal.
    """
    _need(isinstance(settings, AppSettings), "settings_invalid")
    _need(callable(session_factory), "fresh_session_required")
    pid = _native_id(project_id, "project_id_invalid")
    cid = _native_id(chapter_id, "chapter_id_invalid")
    sid = _native_id(source_draft_id, "source_id_invalid")
    _need(_is_hash(operation_key), "operation_key_invalid")
    try:
        context = session_factory()
        _need(isinstance(context, AbstractAsyncContextManager), "fresh_session_required")
        if isinstance(context, AsyncSession):
            # Reject a borrowed active session before entering/closing it. Its
            # pending changes and transaction remain owned by the caller.
            _fresh(context)
        async with context as session:
            _fresh(session)
            with session.no_autoflush:
                async with session.begin():
                    receipt = await _reserve_locked(session, pid, cid, sid, operation_key)
        return receipt  # Both transaction and independent session have exited.
    except SQLAlchemyError:
        # A commit failure is not a receipt; recovery must re-read the same key.
        raise AutomaticRevisionBudgetError("storage_unavailable") from None
