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

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass
from functools import wraps
import hashlib
import json
from typing import Any, ParamSpec, TypeVar
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
BODY_TEMPLATES = frozenset({
    "chapter_rewrite", "chapter_rewrite_repair", "chapter_rewrite_quality_retrofit_repair",
    "chapter_localized_opening_repair", "chapter_localized_ending_repair", "scene_rewrite",
    "scene_writer_regen", "content_validation", "reader_chapter_polish",
    "reader_chapter_issue_edit", "reader_issue_segment_writer", "reader_issue_segment_compress",
})
_JOURNAL_FIELDS = {
    "schema", "project_id", "chapter_id", "event_key", "frozen_source", "limit", "history",
}
_RESERVATION_FIELDS = {
    "round_no", "operation_key", "operation_scope_id", "operation_input_sha256",
    "source_draft_id", "source_body_sha256",
}

_P = ParamSpec("_P")
_R = TypeVar("_R")
# Immutable values: copied task contexts cannot mutate a parent's selection.
# A selection only locates native rows; it never supplies send/adoption authority.
_revision_source_state: ContextVar[
    tuple[asyncio.Task[Any], tuple[UUID, UUID, UUID] | None] | None
] = ContextVar("chapter_automatic_revision_source", default=None)


class AutomaticRevisionBudgetError(ValueError):
    """Fail closed without embedding source prose or connection details."""

    def __init__(self, reason: str, *, differences: list[dict[str, Any]] | None = None) -> None:
        self.code = "automatic_revision_budget:" + reason
        self.differences = deepcopy(differences) if differences is not None else []
        super().__init__(self.code)


@dataclass(frozen=True)
class RevisionSendBinding:
    """Native IDs and immutable CAS values, never a standalone send permission."""

    project_id: UUID
    chapter_id: UUID
    source_draft_id: UUID
    operation_key: str
    operation_scope_id: str
    operation_owner: str
    operation_input_sha256: str
    event_key: str
    round_no: int
    source_body_sha256: str


def is_automatic_revision_body(template: str | None, version: str | None) -> bool:
    return template in BODY_TEMPLATES or (template == "scene_writer" and version == "deslop_revise")


def diagnostic_summary(value: object) -> dict[str, Any]:
    """Match main's typed-hash receipts without returning any operand text."""
    try:
        encoded = json.dumps(value, sort_keys=True, default=str).encode()
    except (TypeError, ValueError):
        # Mixed non-JSON mapping keys must not turn a refusal into a provider
        # fallback. Preserve insertion order for these malformed caller values.
        encoded = json.dumps(value, default=str).encode()
    return {
        "type": type(value).__name__,
        "length": len(value) if isinstance(value, (str, list, dict, bytes)) else None,
        "sha256": hashlib.sha256(encoded).hexdigest(),
    }


def diagnostic_difference(field: str, expected: object, actual: object) -> dict[str, Any]:
    return {"field": field, "expected": diagnostic_summary(expected),
            "actual": diagnostic_summary(actual)}


def _need(
    condition: object, reason: str, *, field: str | None = None,
    expected: object = None, actual: object = None,
) -> None:
    if not condition:
        differences = [diagnostic_difference(field, expected, actual)] if field else None
        raise AutomaticRevisionBudgetError(reason, differences=differences)


def _history_integer(value: object, expected: int, field: str) -> None:
    """JSON booleans are never ordinals/limits, even though True == 1."""
    if type(value) is not int or value != expected:
        error = AutomaticRevisionBudgetError("history_invalid", differences=[{
            "field": field,
            "expected": {"type": "int", "value": expected},
            "actual": {"type": type(value).__name__,
                       "value": value if type(value) in {int, bool} else None},
        }])
        # Keep the frozen primitive receipt contract; the gateway alone projects
        # its richer send-error form, including non-integer operands faithfully.
        error.send_differences = [diagnostic_difference(field, expected, value)]
        raise error


def _is_hash(value: object) -> bool:
    return (
        type(value) is str and len(value) == 64
        and all(char in "0123456789abcdef" for char in value)
    )


def _native_id(value: object, reason: str, *, field: str | None = None) -> UUID:
    if isinstance(value, UUID):
        return value
    _need(type(value) is str, reason, field=field, expected="canonical UUID", actual=value)
    try:
        parsed = UUID(value)
    except ValueError:
        differences = [diagnostic_difference(field, "canonical UUID", value)] if field else None
        raise AutomaticRevisionBudgetError(reason, differences=differences) from None
    _need(str(parsed) == value, reason, field=field, expected=str(parsed), actual=value)
    return parsed


def revision_source_scope(
    func: Callable[_P, Awaitable[_R]],
) -> Callable[_P, Awaitable[_R]]:
    """Give one async entry an empty, task-bound ID scope; restore its caller."""
    @wraps(func)
    async def wrapped(*args: _P.args, **kwargs: _P.kwargs) -> _R:
        task = asyncio.current_task()
        _need(task is not None, "source_scope_missing")
        token = _revision_source_state.set((task, None))
        try:
            return await func(*args, **kwargs)
        finally:
            _revision_source_state.reset(token)

    return wrapped


def set_revision_source(
    *, project_id: UUID | str, chapter_id: UUID | str, source_draft_id: UUID | str,
) -> None:
    """Select IDs after the caller's native parent guard, without granting rights."""
    state = _revision_source_state.get()
    _need(state is not None, "source_scope_missing")
    _need(state[0] is asyncio.current_task(), "source_scope_task_mismatch")
    ids = (
        _native_id(project_id, "project_id_invalid"),
        _native_id(chapter_id, "chapter_id_invalid"),
        _native_id(source_draft_id, "source_id_invalid"),
    )
    _revision_source_state.set((state[0], ids))


def current_revision_source(project_id: UUID | str) -> dict[str, str] | None:
    """Return a detached selector only to the same task and exact project."""
    state = _revision_source_state.get()
    if state is None:
        return None
    try:
        task = asyncio.current_task()
    except RuntimeError:
        return None
    if state[0] is not task or state[1] is None:
        return None
    pid = _native_id(project_id, "project_id_invalid")
    owner_project, chapter_id, source_draft_id = state[1]
    if owner_project != pid:
        return None
    return {"chapter_id": str(chapter_id), "source_draft_id": str(source_draft_id)}


def _body_hash(body: object, reason: str) -> str:
    _need(type(body) is str and body.strip(), reason, field="chapter_draft_versions.content_md",
          expected="nonempty UTF-8 text", actual=body)
    try:
        return hashlib.sha256(body.encode("utf-8")).hexdigest()
    except UnicodeEncodeError:
        raise AutomaticRevisionBudgetError(reason, differences=[
            diagnostic_difference("chapter_draft_versions.content_md", "UTF-8 text", body),
        ]) from None


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
    _need(type(raw) is dict and raw.keys() == _JOURNAL_FIELDS, reason,
          field=JOURNAL_KEY + ".keys", expected=sorted(_JOURNAL_FIELDS),
          actual=sorted(raw) if type(raw) is dict else raw)
    _need(raw["schema"] == SCHEMA, reason, field=JOURNAL_KEY + ".schema",
          expected=SCHEMA, actual=raw["schema"])
    _need(raw["project_id"] == str(project_id) and raw["chapter_id"] == str(chapter_id), reason,
          field=JOURNAL_KEY + (
              ".project_id" if raw["project_id"] != str(project_id) else ".chapter_id"),
          expected=str(project_id) if raw["project_id"] != str(project_id) else str(chapter_id),
          actual=raw["project_id"] if raw["project_id"] != str(project_id) else raw["chapter_id"])
    _history_integer(raw["limit"], LIMIT, JOURNAL_KEY + ".limit")
    frozen = raw["frozen_source"]
    _need(type(frozen) is dict and frozen.keys() == {"draft_id", "body_sha256"}, reason,
          field=JOURNAL_KEY + ".frozen_source.keys", expected=["body_sha256", "draft_id"],
          actual=sorted(frozen) if type(frozen) is dict else frozen)
    _native_id(frozen["draft_id"], reason, field=JOURNAL_KEY + ".frozen_source.draft_id")
    _need(_is_hash(frozen["body_sha256"]), reason,
          field=JOURNAL_KEY + ".frozen_source.body_sha256",
          expected="lowercase SHA256", actual=frozen["body_sha256"])
    _need(raw["event_key"] == _event_key(project_id, chapter_id, frozen), reason,
          field=JOURNAL_KEY + ".event_key", expected=_event_key(project_id, chapter_id, frozen),
          actual=raw["event_key"])
    history = raw["history"]
    _need(type(history) is list and 1 <= len(history) <= LIMIT, reason,
          field=JOURNAL_KEY + ".history.length", expected=[1, LIMIT],
          actual=len(history) if type(history) is list else history)
    seen: set[str] = set()
    for index, item in enumerate(history, start=1):
        prefix = f"{JOURNAL_KEY}.history[{index - 1}]"
        _need(type(item) is dict and item.keys() == _RESERVATION_FIELDS, reason,
              field=prefix + ".keys", expected=sorted(_RESERVATION_FIELDS),
              actual=sorted(item) if type(item) is dict else item)
        _history_integer(item["round_no"], index,
                         f"{JOURNAL_KEY}.history[{index - 1}].round_no")
        key = item["operation_key"]
        _need(_is_hash(key) and key not in seen, reason, field=prefix + ".operation_key",
              expected="unique lowercase SHA256", actual=key)
        seen.add(key)
        _native_id(item["source_draft_id"], reason, field=prefix + ".source_draft_id")
        _need(item["operation_scope_id"] == scope, reason, field=prefix + ".operation_scope_id",
              expected=scope, actual=item["operation_scope_id"])
        _need(_is_hash(item["operation_input_sha256"]), reason,
              field=prefix + ".operation_input_sha256", expected="lowercase SHA256",
              actual=item["operation_input_sha256"])
        _need(_is_hash(item["source_body_sha256"]), reason, field=prefix + ".source_body_sha256",
              expected="lowercase SHA256", actual=item["source_body_sha256"])
        if item["source_draft_id"] == frozen["draft_id"]:
            _need(item["source_body_sha256"] == frozen["body_sha256"], reason,
                  field=prefix + ".source_body_sha256", expected=frozen["body_sha256"],
                  actual=item["source_body_sha256"])
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
    *, native_claim: tuple[str, str, str] | None = None,
    expected: RevisionSendBinding | None = None,
) -> dict:
    # Match native lock ordering. No financial row is locked or written.
    control = await session.scalar(
        select(BookProductionControlModel)
        .where(BookProductionControlModel.project_id == project_id)
        .with_for_update().execution_options(populate_existing=True)
    )
    _need(control is None or control.desired_state == "run", "control_halted",
          field="book_production_controls.desired_state", expected="run",
          actual=control.desired_state if control is not None else None)
    project = await session.scalar(
        select(ProjectModel).where(ProjectModel.id == project_id)
        .with_for_update().execution_options(populate_existing=True)
    )
    _need(project is not None, "project_missing", field="projects.id",
          expected=project_id, actual=getattr(project, "id", None))
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
        _need(owner is not None, "chapter_missing", field="chapters.id",
              expected=chapter_id, actual=None)
        raise AutomaticRevisionBudgetError("chapter_project_mismatch", differences=[
            diagnostic_difference("chapters.project_id", project_id, owner),
        ])
    metadata = chapter.metadata_json
    _need(type(metadata) is dict, "history_invalid", field="chapters.metadata",
          expected={}, actual=metadata)
    _need(type(project.metadata_json) is dict, "protocol_invalid", field="projects.metadata",
          expected={}, actual=project.metadata_json)
    try:
        marker = reader_first_binding(project)
    except (ValueError, OSError):
        raise AutomaticRevisionBudgetError("protocol_invalid", differences=[
            diagnostic_difference("projects.metadata.reader_first_planning",
                                  "valid native reader-first binding", project.metadata_json),
        ]) from None
    if marker is None:
        _need(JOURNAL_KEY not in metadata and native_claim is None, "protocol_removed",
              field="projects.metadata.reader_first_planning.present", expected=True, actual=False)
        return _receipt("legacy", key, None, None)

    scope = project_execution_scope(project)
    # Do not let a malformed persisted creation scope silently fall back.
    _need(project.metadata_json.get("execution_scope_id", scope) == scope,
          "operation_scope_mismatch", field="projects.metadata.execution_scope_id",
          expected=scope, actual=project.metadata_json.get("execution_scope_id", scope))
    journal = (
        _journal(metadata[JOURNAL_KEY], project_id, chapter_id, scope)
        if JOURNAL_KEY in metadata else None
    )
    if expected is not None:
        _need(journal is not None, "reservation_missing", field=JOURNAL_KEY + ".present",
              expected=True, actual=journal is not None)
        _need(journal["event_key"] == expected.event_key, "reservation_changed",
              field=JOURNAL_KEY + ".event_key", expected=expected.event_key,
              actual=journal["event_key"])
    history = journal["history"] if journal is not None else []
    # Read IDs/ownership first so bad inputs cannot lock another project's
    # source rows. Revalidate the full source under this chapter's Draft locks.
    source_owner = (await session.execute(
        select(ChapterDraftVersionModel.project_id, ChapterDraftVersionModel.chapter_id)
        .where(ChapterDraftVersionModel.id == source_id),
    )).one_or_none()
    _need(source_owner is not None, "source_missing", field="chapter_draft_versions.id",
          expected=source_id, actual=None)
    _need(source_owner[0] == project_id and source_owner[1] == chapter_id, "source_mismatch",
          field="chapter_draft_versions.project_id" if source_owner[0] != project_id
          else "chapter_draft_versions.chapter_id",
          expected=project_id if source_owner[0] != project_id else chapter_id,
          actual=source_owner[0] if source_owner[0] != project_id else source_owner[1])
    if journal is None:
        first = (await session.execute(
            select(ChapterDraftVersionModel.id, ChapterDraftVersionModel.project_id)
            .where(ChapterDraftVersionModel.chapter_id == chapter_id)
            .order_by(ChapterDraftVersionModel.version_no, ChapterDraftVersionModel.id)
            .limit(1),
        )).one_or_none()
        _need(first is not None, "source_missing", field="chapter_draft_versions.earliest.present",
              expected=True, actual=first is not None)
        _need(first[1] == project_id, "source_mismatch",
              field="chapter_draft_versions.earliest.project_id",
              expected=project_id, actual=first[1])
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
    _need(source is not None, "source_missing", field="chapter_draft_versions.id",
          expected=source_id, actual=getattr(source, "id", None))
    _need(source.project_id == project_id and source.chapter_id == chapter_id, "source_mismatch",
          field="chapter_draft_versions.owner", expected=[project_id, chapter_id],
          actual=[source.project_id, source.chapter_id])
    source_hash = _body_hash(source.content_md, "source_empty")
    if expected is not None:
        _need(source_hash == expected.source_body_sha256, "operation_source_changed",
              field="chapter_draft_versions.content_md.sha256",
              expected=expected.source_body_sha256, actual=source_hash)
    frozen = drafts.get(frozen_id)
    frozen_reason = "frozen_source_changed" if journal is not None else "source_mismatch"
    _need(frozen is not None and frozen.project_id == project_id
          and frozen.chapter_id == chapter_id, frozen_reason,
          field="chapter_draft_versions.frozen.owner", expected=[project_id, chapter_id],
          actual=[frozen.project_id, frozen.chapter_id] if frozen is not None else None)
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
        _need(frozen_hash == journal["frozen_source"]["body_sha256"], "frozen_source_changed",
              field="chapter_draft_versions.frozen.content_md.sha256",
              expected=journal["frozen_source"]["body_sha256"], actual=frozen_hash)

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
        error = AutomaticRevisionBudgetError("operation_missing", differences=[{
            "field": "operation_key.native_row",
            "expected": {"type": "ExecutionOperationModel", "present": True},
            "actual": {"type": "NoneType", "present": False},
        }])
        error.send_differences = [diagnostic_difference("execution_operations.key", key, None)]
        raise error
    _need(operation.scope_id == scope, "operation_scope_mismatch",
          field="execution_operations.scope_id", expected=scope, actual=operation.scope_id)
    _need(budget is not None, "operation_budget_missing", field="execution_budgets.scope_id",
          expected=scope, actual=getattr(budget, "scope_id", None))
    _need(_is_hash(operation.input_hash), "operation_binding_changed",
          field="execution_operations.input_hash", expected="lowercase SHA256",
          actual=operation.input_hash)
    if native_claim is not None:
        claim_scope, claim_owner, claim_input = native_claim
        _need(scope == claim_scope, "operation_scope_mismatch",
              field="execution_operations.scope_id", expected=scope, actual=claim_scope)
        _need(operation.owner == claim_owner and operation.status == "pending",
              "operation_claim_changed",
              field="execution_operations.owner" if operation.owner != claim_owner
              else "execution_operations.status",
              expected=claim_owner if operation.owner != claim_owner else "pending",
              actual=operation.owner if operation.owner != claim_owner else operation.status)
        _need(operation.input_hash == claim_input, "operation_binding_changed",
              field="execution_operations.input_hash",
              expected=claim_input, actual=operation.input_hash)
        # Reuse the native read-only status policy; never modify its ledger.
        from bestseller.services.budget import ExecutionBudgetError, execution_scope_block_code
        code = execution_scope_block_code(budget)
        if code is not None:
            raise ExecutionBudgetError(code, scope)
    existing = next((item for item in history if item["operation_key"] == key), None)
    if expected is not None:
        _need(existing is not None, "reservation_missing", field=JOURNAL_KEY + ".operation.present",
              expected=True, actual=existing is not None)
        _need(existing["round_no"] == expected.round_no, "reservation_changed",
              field=JOURNAL_KEY + ".operation.round_no", expected=expected.round_no,
              actual=existing["round_no"])
    if existing is not None:
        _need(existing["source_draft_id"] == str(source_id)
              and existing["source_body_sha256"] == source_hash, "operation_source_changed",
              field=JOURNAL_KEY + ".operation.source",
              expected=[existing["source_draft_id"], existing["source_body_sha256"]],
              actual=[str(source_id), source_hash])
        _need(existing["operation_input_sha256"] == operation.input_hash,
              "operation_binding_changed", field="execution_operations.input_hash",
              expected=existing["operation_input_sha256"], actual=operation.input_hash)
    for index, item in enumerate(history):
        prefix = f"{JOURNAL_KEY}.history[{index}]"
        old_source = drafts.get(UUID(item["source_draft_id"]))
        _need(old_source is not None and old_source.project_id == project_id
              and old_source.chapter_id == chapter_id, "history_invalid",
              field=prefix + ".source_owner", expected=[project_id, chapter_id],
              actual=[old_source.project_id, old_source.chapter_id] if old_source else None)
        old_source_hash = _body_hash(old_source.content_md, "history_source_changed")
        _need(old_source_hash == item["source_body_sha256"], "history_source_changed",
              field=prefix + ".source_body_sha256", expected=item["source_body_sha256"],
              actual=old_source_hash)
        old_op = operations.get(item["operation_key"])
        _need(old_op is not None and old_op.scope_id == item["operation_scope_id"]
              and old_op.input_hash == item["operation_input_sha256"], "history_invalid",
              field=prefix + ".native_operation_binding",
              expected=[item["operation_scope_id"], item["operation_input_sha256"]],
              actual=[old_op.scope_id, old_op.input_hash] if old_op else None)
    if existing is not None:
        # Status/owner/lease changes never release a round or permit a resend.
        return _receipt("already_reserved", key, journal, existing["round_no"])

    _need(operation.status == "pending", "operation_not_pending",
          field="execution_operations.status", expected="pending", actual=operation.status)
    _need(len(history) < LIMIT, "exhausted", field=JOURNAL_KEY + ".rounds_remaining",
          expected=1, actual=LIMIT - len(history))
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
                          for item in other["history"]), "operation_source_changed",
                  field=JOURNAL_KEY + ".operation.single_chapter", expected=True, actual=False)

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


@asynccontextmanager
async def _send_session(
    session_factory: Callable[[], AbstractAsyncContextManager[AsyncSession]], settings: AppSettings,
) -> AsyncIterator[AsyncSession]:
    """Bound fresh-session work; no original caller/financial locks are retained."""
    _need(isinstance(settings, AppSettings), "settings_invalid", field="settings.is_app_settings",
          expected=True, actual=isinstance(settings, AppSettings))
    _need(callable(session_factory), "fresh_session_required", field="session_factory.callable",
          expected=True, actual=callable(session_factory))
    try:
        async with asyncio.timeout(5):
            context = session_factory()
            _need(isinstance(context, AbstractAsyncContextManager), "fresh_session_required",
                  field="session_factory.result.is_async_context", expected=True,
                  actual=isinstance(context, AbstractAsyncContextManager))
            if isinstance(context, AsyncSession):
                _fresh(context)
            async with context as session:
                _fresh(session)
                with session.no_autoflush:
                    async with session.begin():
                        yield session
    except (SQLAlchemyError, TimeoutError) as error:
        raise AutomaticRevisionBudgetError("storage_unavailable", differences=[
            diagnostic_difference("revision_storage.transaction.error_type", None,
                                  type(error).__name__),
        ]) from None


async def requires_revision_source(
    *, session_factory: Callable[[], AbstractAsyncContextManager[AsyncSession]],
    settings: AppSettings, project_id: UUID | None,
) -> bool:
    """SELECT-only protocol discovery, including refusal of a removed marker."""
    if project_id is None:
        return False  # Project-less legacy generation has no reader-first marker.
    pid = _native_id(project_id, "project_id_invalid", field="projects.id")
    async with _send_session(session_factory, settings) as session:
        project = await session.get(ProjectModel, pid)
        _need(project is not None, "project_missing", field="projects.id",
              expected=pid, actual=getattr(project, "id", None))
        _need(type(project.metadata_json) is dict, "protocol_invalid", field="projects.metadata",
              expected={}, actual=project.metadata_json)
        try:
            marked = reader_first_binding(project) is not None
        except (ValueError, OSError):
            raise AutomaticRevisionBudgetError("protocol_invalid", differences=[
                diagnostic_difference("projects.metadata.reader_first_planning",
                                      "valid native reader-first binding", project.metadata_json),
            ]) from None
        if not marked:
            chapter_metadata = await session.scalars(
                select(ChapterModel.metadata_json).where(ChapterModel.project_id == pid),
            )
            _need(not any(type(meta) is dict and JOURNAL_KEY in meta for meta in chapter_metadata),
                  "protocol_removed", field="projects.metadata.reader_first_planning.present",
                  expected=True, actual=False)
        return marked


async def reserve_revision_send(
    *, session_factory: Callable[[], AbstractAsyncContextManager[AsyncSession]],
    settings: AppSettings, project_id: UUID, chapter_id: UUID, source_draft_id: UUID,
    operation_key: str, operation_scope_id: str, operation_owner: str,
    operation_input_sha256: str,
) -> RevisionSendBinding:
    """Commit shared accounting for an actual fresh native claim, before financial reserve."""
    pid = _native_id(project_id, "project_id_invalid", field="projects.id")
    cid = _native_id(chapter_id, "chapter_id_invalid", field="chapters.id")
    sid = _native_id(source_draft_id, "source_id_invalid", field="chapter_draft_versions.id")
    _need(_is_hash(operation_key), "operation_key_invalid", field="execution_operations.key",
          expected="lowercase SHA256", actual=operation_key)
    _need(_is_hash(operation_input_sha256), "operation_binding_changed",
          field="execution_operations.input_hash", expected="lowercase SHA256",
          actual=operation_input_sha256)
    native = (operation_scope_id, operation_owner, operation_input_sha256)
    async with _send_session(session_factory, settings) as session:
        receipt = await _reserve_locked(session, pid, cid, sid, operation_key, native_claim=native)
        chapter = await session.get(ChapterModel, cid)
        history = chapter.metadata_json[JOURNAL_KEY]["history"]
        item = next(record for record in history if record["operation_key"] == operation_key)
        binding = RevisionSendBinding(
            pid, cid, sid, operation_key, operation_scope_id, operation_owner,
            operation_input_sha256, receipt["event_key"], item["round_no"],
            item["source_body_sha256"],
        )
    return binding  # Commit and close completed, even if the caller later rolls back.


async def assert_revision_send(
    *, session_factory: Callable[[], AbstractAsyncContextManager[AsyncSession]],
    settings: AppSettings, binding: RevisionSendBinding,
) -> None:
    """Read/CAS only; a missing/changed reservation can never append during validation."""
    _need(isinstance(binding, RevisionSendBinding), "reservation_missing",
          field="revision_send_binding.valid_type", expected=True,
          actual=isinstance(binding, RevisionSendBinding))
    native = (binding.operation_scope_id, binding.operation_owner, binding.operation_input_sha256)
    async with _send_session(session_factory, settings) as session:
        await _reserve_locked(
            session, binding.project_id, binding.chapter_id, binding.source_draft_id,
            binding.operation_key, native_claim=native, expected=binding,
        )
