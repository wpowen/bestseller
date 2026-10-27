"""Invocation-local prompt custody after a production parent resolves its project.

This helper grants no provider, budget or grading authority and changes no
settings. Callers must bind an actual framework-resolved UUID; inherited child
tasks must enter their own decorated invocation before attempting a binding.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from contextlib import ExitStack
from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps
from typing import Any, ParamSpec, TypeVar
from uuid import UUID

from bestseller.services import llm

_P = ParamSpec("_P")
_R = TypeVar("_R")


@dataclass
class _Invocation:
    stack: ExitStack
    owner: asyncio.Task[Any] | None
    project_id: UUID | None = None
    active: bool = True


_invocation: ContextVar[_Invocation | None] = ContextVar(
    "production_project_prompt_audit_invocation", default=None
)


def project_prompt_audit_scope(
    function: Callable[_P, Awaitable[_R]],
) -> Callable[_P, Awaitable[_R]]:
    """Give each awaited invocation its own synchronous audit cleanup stack.

    Nested calls restore the enclosing audit, including on cancellation. The
    decorator itself binds no project and does not infer one from arguments.
    """

    @wraps(function)
    async def wrapped(*args: _P.args, **kwargs: _P.kwargs) -> _R:
        state = _Invocation(ExitStack(), asyncio.current_task())
        token = _invocation.set(state)
        try:
            return await function(*args, **kwargs)
        finally:
            state.active = False
            try:
                state.stack.close()
            finally:
                _invocation.reset(token)

    return wrapped


def bind_resolved_project_prompt_audit(project_id: UUID, *, reason: str) -> None:
    """Bind one resolved project until this decorated invocation finishes.

    Repeating that UUID is a no-op and keeps the first reason. A different UUID
    fails closed. A copied ContextVar must not let another task mutate/close the
    parent's ExitStack or reset a token created in a different context.
    """
    state = _invocation.get()
    if state is None or not state.active or state.owner is not asyncio.current_task():
        raise RuntimeError("production_prompt_audit:active_invocation_required")
    if not isinstance(project_id, UUID):
        raise ValueError("production_prompt_audit:project_uuid_required")
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("production_prompt_audit:reason_required")
    if state.project_id is not None:
        if state.project_id != project_id:
            raise ValueError("production_prompt_audit:project_changed")
        return
    state.stack.enter_context(llm.bind_project_prompt_audit(project_id, reason=reason.strip()))
    state.project_id = project_id
