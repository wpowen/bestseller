"""Task-local, fail-closed admission for one already paid request.

The domain owner supplies an independently verified binding (including its
claim/source/transport proof). This module only reads fresh paid root and
attempt rows. It never claims, checkpoints, restores telemetry or sends. Wire
the guard after final input/operation preparation and before those mutations;
mark reuse only after cached telemetry restoration AND the final adoption check.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from datetime import datetime
import hashlib
from types import MappingProxyType
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.infra.db.models import ExecutionAttemptModel, ExecutionOperationModel
from bestseller.services.budget import current_execution_scope
from bestseller.services.prompt_source_policy import content_hash

if TYPE_CHECKING:
    from bestseller.services.llm import LLMCompletionRequest
    from bestseller.settings import LLMRoleSettings

_REQUIRED = frozenset(
    {
        "scope_id",
        "project_id",
        "workflow_run_id",
        "logical_role",
        "prompt_template",
        "prompt_version",
        "model",
        "input_hash",
        "operation_key",
        "result_sha256",
        "attempts_sha256",
    }
)
_HASH_FIELDS = ("input_hash", "operation_key", "result_sha256", "attempts_sha256")
_ATTEMPT_FIELDS = tuple(attr.key for attr in ExecutionAttemptModel.__mapper__.column_attrs)


class RetainedRequestRejected(BaseException):
    """Must escape broad rewrite catches; never convert this stop to fallback prose."""

    def __init__(self, code: str, receipt: Mapping[str, Any] | None = None) -> None:
        self.code = "retained_request_guard:" + code
        self.receipt = deepcopy(dict(receipt or {}))
        super().__init__(self.code)


class RetainedRequestPrepared(BaseException):
    """A successful read-only probe, stopped before checkpoint/claim/telemetry."""

    def __init__(self, receipt: Mapping[str, Any]) -> None:
        self.receipt = deepcopy(dict(receipt))
        super().__init__("retained_request_guard:prepared")


def _task() -> asyncio.Task | None:
    try:
        return asyncio.current_task()
    except RuntimeError:
        return None


def _freeze(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (tuple, list)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(_freeze(item) for item in value)
    return deepcopy(value)


def _valid_hash(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(char in "0123456789abcdef" for char in value)
    )


def _binding(value: Mapping[str, Any]) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or not _REQUIRED <= value.keys():
        raise RetainedRequestRejected("binding_incomplete")
    if any(not isinstance(value[key], str) or not value[key].strip() for key in _REQUIRED):
        raise RetainedRequestRejected("binding_types")
    if any(not _valid_hash(value[key]) for key in _HASH_FIELDS):
        raise RetainedRequestRejected("binding_hash")
    for key in ("project_id", "workflow_run_id"):
        try:
            if str(UUID(value[key])) != value[key]:
                raise ValueError(key)
        except (ValueError, TypeError, AttributeError) as error:
            raise RetainedRequestRejected("binding_uuid") from error
    return _freeze(value)


class RetainedRequestState:
    """Read-only public state; successful admission alone is not completed reuse."""

    def __init__(
        self, binding: Mapping[str, Any], *, inspect_only: bool, allow_followup_after_reuse: bool
    ) -> None:
        self._binding = binding
        self._owner = _task()
        self._active = True
        self._inspect_only = inspect_only
        self._allow_followup = allow_followup_after_reuse
        self._reused = False
        self._prepared = False
        self._followup_key: str | None = None

    @property
    def reused(self) -> bool:
        return self._reused


_STATE: ContextVar[RetainedRequestState | None] = ContextVar("retained_request_guard", default=None)


def _current() -> RetainedRequestState | None:
    state = _STATE.get()
    if state is not None and (not state._active or state._owner is not _task()):
        raise RetainedRequestRejected("owner_task_changed")
    return state


def current_retained_binding() -> Mapping[str, Any] | None:
    """Only the active owner task may inspect the immutable domain binding."""
    state = _current()
    return state._binding if state is not None else None


@contextmanager
def bind_retained_request(
    binding: Mapping[str, Any],
    *,
    inspect_only: bool = False,
    allow_followup_after_reuse: bool = False,
) -> Iterator[RetainedRequestState]:
    # Inherited child tasks cannot rebind to erase their parent's guard. Nested
    # bindings in the owner would equally permit escaping a stricter boundary.
    if _current() is not None:
        raise RetainedRequestRejected("nested_binding")
    if type(inspect_only) is not bool or type(allow_followup_after_reuse) is not bool:
        raise RetainedRequestRejected("binding_options")
    state = RetainedRequestState(
        _binding(binding),
        inspect_only=inspect_only,
        allow_followup_after_reuse=allow_followup_after_reuse,
    )
    token = _STATE.set(state)
    try:
        yield state
    finally:
        state._active = False
        _STATE.reset(token)


def retained_attempts_sha256(rows: Sequence[Mapping[str, Any]]) -> str:
    """Hash all mapped attempt columns, with ISO datetime/UUID strings, by id.

    Existing strings are preserved byte-for-byte. A SQLite witness should be
    built from its read-back rows (SQLite returns naive DateTime values).
    """
    normalized = []
    for row in rows:
        if set(row) != set(_ATTEMPT_FIELDS):
            raise RetainedRequestRejected("attempt_columns")
        normalized.append(
            {
                key: (
                    value.isoformat()
                    if isinstance(value, datetime)
                    else str(value)
                    if isinstance(value, UUID)
                    else value
                )
                for key, value in row.items()
            }
        )
    if any(not isinstance(row["id"], str) or not row["id"] for row in normalized):
        raise RetainedRequestRejected("attempt_identity")
    return content_hash(sorted(normalized, key=lambda row: row["id"]))


def _operation_key(
    request: LLMCompletionRequest, digest: str, scope: str
) -> tuple[str, str, str | int | None]:
    operation_id = request.operation_id
    slot = request.sample_slot
    if slot is None:
        slot = next(
            (
                request.metadata[key]
                for key in ("sample_slot", "sample_index", "candidate_index")
                if request.metadata.get(key) is not None
            ),
            None,
        )
    if slot is not None and (type(slot) not in (str, int) or slot == ""):
        raise RetainedRequestRejected("sample_invalid")
    if operation_id is None:
        if slot is None:
            raise RetainedRequestRejected("operation_identity_missing")
        import json

        operation_id = hashlib.sha256(
            json.dumps(
                {
                    "input_hash": digest,
                    "role": request.logical_role,
                    "template": request.prompt_template,
                    "version": request.prompt_version,
                },
                sort_keys=True,
                ensure_ascii=False,
                default=str,
            ).encode()
        ).hexdigest()
    if not isinstance(operation_id, str) or not operation_id:
        raise RetainedRequestRejected("operation_identity_missing")
    effective_id = operation_id + (f":sample:{slot}" if slot is not None else "")
    return hashlib.sha256(f"{scope}\n{effective_id}".encode()).hexdigest(), operation_id, slot


async def guard_prepared_request(
    session: AsyncSession, request: LLMCompletionRequest, role: LLMRoleSettings, digest: str
) -> None:
    """Read fresh columns without autoflush; no binding means normal gateway use."""
    state = _current()
    if state is None:
        return
    binding = state._binding
    state._prepared = False
    state._followup_key = None
    receipt = {
        "schema": "retained-request-prepared.v1",
        "matched": False,
        "scope_id": current_execution_scope(),
        "input_hash": digest,
        "project_id": str(request.project_id),
        "workflow_run_id": str(request.workflow_run_id),
        "logical_role": request.logical_role,
        "prompt_template": request.prompt_template,
        "prompt_version": request.prompt_version,
        "model": role.model,
        "provider_dispatches": 0,
    }

    def require(condition: object, code: str) -> None:
        if not condition:
            raise RetainedRequestRejected(code, receipt)

    require(current_execution_scope() == binding["scope_id"], "scope_changed")
    require(str(request.project_id) == binding["project_id"], "project_changed")
    require(str(request.workflow_run_id) == binding["workflow_run_id"], "workflow_changed")
    require(_valid_hash(digest), "input_hash_invalid")
    exact_input = digest == binding["input_hash"]
    if not exact_input:
        require(
            not state._inspect_only and state._allow_followup and state._reused,
            "unpaid_request_forbidden",
        )
        try:
            key, _, _ = _operation_key(request, digest, binding["scope_id"])
        except RetainedRequestRejected as error:
            raise RetainedRequestRejected(
                error.code.removeprefix("retained_request_guard:"), receipt
            ) from error
        require(key != binding["operation_key"], "paid_input_changed")
        state._followup_key = key
        return  # Ordinary gateway controls still authorize/check this followup.
    require(request.logical_role == binding["logical_role"], "role_changed")
    require(request.prompt_template == binding["prompt_template"], "template_changed")
    require(request.prompt_version == binding["prompt_version"], "version_changed")
    require(role.model == binding["model"], "model_changed")
    try:
        key, operation_id, slot = _operation_key(request, digest, binding["scope_id"])
    except RetainedRequestRejected as error:
        raise RetainedRequestRejected(
            error.code.removeprefix("retained_request_guard:"), receipt
        ) from error
    receipt.update(operation_key=key, operation_id=operation_id, sample_slot=slot)
    require(key == binding["operation_key"], "operation_changed")

    # Column projections bypass an already-loaded/stale ORM identity map. This
    # is a preflight, not a locking claim: the gateway must still reassert the
    # cached immutable result before and after restoring telemetry.
    try:
        with session.no_autoflush:
            root = (
                (
                    await session.execute(
                        select(
                            ExecutionOperationModel.scope_id,
                            ExecutionOperationModel.input_hash,
                            ExecutionOperationModel.status,
                            ExecutionOperationModel.result_json,
                        ).where(ExecutionOperationModel.key == key)
                    )
                )
                .mappings()
                .one_or_none()
            )
            columns = [getattr(ExecutionAttemptModel, field) for field in _ATTEMPT_FIELDS]
            attempts = [
                dict(row)
                for row in (
                    await session.execute(
                        select(*columns).where(ExecutionAttemptModel.operation_key == key)
                    )
                )
                .mappings()
                .all()
            ]
    except Exception as error:
        raise RetainedRequestRejected("fresh_read_failed", receipt) from error
    require(root is not None, "paid_root_missing")
    require(
        root["scope_id"] == binding["scope_id"] and root["input_hash"] == digest,
        "paid_root_binding_changed",
    )
    require(root["status"] == "completed", "paid_root_not_completed")
    result = root["result_json"]
    require(
        isinstance(result, dict) and content_hash(result) == binding["result_sha256"],
        "paid_result_changed",
    )
    require(
        bool(attempts) and retained_attempts_sha256(attempts) == binding["attempts_sha256"],
        "paid_attempts_changed",
    )
    require(
        all(
            row["scope_id"] == binding["scope_id"]
            and row["input_hash"] == digest
            and row["model_name"] == binding["model"]
            and row["status"] == "completed"
            and type(row["actual_tokens"]) is int
            and row["actual_tokens"] >= 0
            for row in attempts
        ),
        "paid_attempts_unsettled",
    )
    metadata = result.get("metadata")
    require(isinstance(metadata, dict), "paid_metadata_missing")
    origin = metadata.get("paid_call_origin")
    require(
        isinstance(origin, dict)
        and origin.get("schema") == "paid-call-origin.v1"
        and origin.get("status") == "recorded"
        and all(
            origin.get(field) == binding[field]
            for field in (
                "project_id",
                "workflow_run_id",
                "logical_role",
                "prompt_template",
                "prompt_version",
            )
        )
        and origin.get("prompt_hash") == digest
        and "step_run_id" in origin
        and origin["step_run_id"] == (str(request.step_run_id) if request.step_run_id else None),
        "paid_origin_changed",
    )
    effective = metadata.get("effective_input_receipt")
    require(
        isinstance(effective, dict)
        and effective.get("schema") == "effective-input.v1"
        and effective.get("hash") == digest
        and effective.get("model") == binding["model"],
        "effective_receipt_changed",
    )
    require(
        result.get("model_name") == binding["model"]
        and result.get("fallback_used") is False
        and result.get("provider") not in (None, "mock", "fallback", "test"),
        "paid_model_changed",
    )
    receipt.update(
        matched=True,
        result_sha256=binding["result_sha256"],
        attempts_sha256=binding["attempts_sha256"],
        attempt_ids=sorted(row["id"] for row in attempts),
    )
    if state._inspect_only:
        raise RetainedRequestPrepared(receipt)
    state._prepared = True


def mark_retained_reused(operation_key: str) -> None:
    """Gateway hook AFTER actual cached restore + final assert, never on a probe."""
    state = _current()
    if state is None:
        return
    if (
        state._reused
        and state._allow_followup
        and not state._inspect_only
        and operation_key != state._binding["operation_key"]
        and operation_key == state._followup_key
    ):
        state._followup_key = None
        return  # A subsequent cache hit does not re-mark the initial paid request.
    if (
        state._inspect_only
        or not state._prepared
        or operation_key != state._binding["operation_key"]
    ):
        raise RetainedRequestRejected("reuse_not_prepared")
    state._reused = True
    state._prepared = False
