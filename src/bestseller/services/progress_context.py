"""Ambient progress emitter backed by a :class:`~contextvars.ContextVar`.

Many deep pipeline actions (quality gates, repair sub-steps, concept/heat-search
agents) historically only wrote to ``logger`` and never surfaced to the web
progress panel, because threading a ``progress`` callback through every
intermediate function signature would touch dozens of call sites and pollute
otherwise-pure gate functions.

Instead, a *top-level* orchestrator binds the active progress callback once via
:func:`bind_progress`; nested code anywhere on the same async call chain reports
progress through :func:`emit_activity` / :func:`emit_milestone` without taking a
``progress`` parameter.

Threading model: the web worker runs each pipeline inside
``asyncio.run(runner())`` on a daemon thread, and the ARQ worker runs inside its
own event loop. A ``ContextVar`` set on that chain is visible to every awaited
coroutine on the same chain and is isolated per task. The one caveat is that
work dispatched to a *separate* thread (``run_in_executor`` or a bare
``threading.Thread``) starts with an empty context and will not see the bound
emitter — such call sites must keep an explicit ``progress`` callback.

Every emit helper is a no-op when no callback is bound, so importing and calling
them is always safe and never breaks the pipeline.
"""

from __future__ import annotations

import contextlib
import logging
from contextvars import ContextVar
from typing import Any, Callable, Iterator

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[str, dict[str, Any] | None], None]

#: Payload key carrying the milestone/activity tier hint to the progress sink.
#: The sink (e.g. ``WebTaskManager._push_progress``) reads this to decide whether
#: an event belongs on the persistent milestone axis or the activity stream,
#: without changing the ``(stage, payload)`` callback signature.
TIER_KEY = "_tier"
TIER_MILESTONE = "milestone"
TIER_ACTIVITY = "activity"


class ProgressEmitter:
    """Wraps a progress callback and tags emitted events with a tier."""

    __slots__ = ("_callback",)

    def __init__(self, callback: ProgressCallback) -> None:
        self._callback = callback

    def _emit(self, stage: str, payload: dict[str, Any] | None, tier: str) -> None:
        data = dict(payload or {})
        data.setdefault(TIER_KEY, tier)
        try:
            self._callback(stage, data)
        except Exception:  # best-effort: progress reporting must never break the pipeline
            logger.debug("progress emit failed for stage %s", stage, exc_info=True)

    def activity(self, stage: str, payload: dict[str, Any] | None = None) -> None:
        self._emit(stage, payload, TIER_ACTIVITY)

    def milestone(self, stage: str, payload: dict[str, Any] | None = None) -> None:
        self._emit(stage, payload, TIER_MILESTONE)


_AMBIENT: ContextVar[ProgressEmitter | None] = ContextVar(
    "bestseller_progress_emitter", default=None
)


@contextlib.contextmanager
def bind_progress(callback: ProgressCallback | None) -> Iterator[ProgressEmitter | None]:
    """Bind *callback* as the ambient progress emitter for the current context.

    A ``None`` callback binds ``None`` (helpers become no-ops). The previous
    binding is always restored on exit, so nested/sequential tasks stay isolated.
    """

    emitter = ProgressEmitter(callback) if callback is not None else None
    token = _AMBIENT.set(emitter)
    try:
        yield emitter
    finally:
        _AMBIENT.reset(token)


def set_ambient(callback: ProgressCallback | None) -> ProgressEmitter | None:
    """Set the ambient emitter without a paired reset.

    For job-local contexts (e.g. ARQ worker tasks) where each job already runs
    in its own copied context, so there is no risk of leaking into a sibling
    job. Each job calls this once at entry, overwriting any stale binding.
    Prefer :func:`bind_progress` (scoped set + reset) on the web/in-process path.
    """

    emitter = ProgressEmitter(callback) if callback is not None else None
    _AMBIENT.set(emitter)
    return emitter


def current_emitter() -> ProgressEmitter | None:
    """Return the ambient emitter, or ``None`` when nothing is bound."""

    return _AMBIENT.get()


def emit_activity(stage: str, payload: dict[str, Any] | None = None) -> None:
    """Report a fine-grained activity event through the ambient emitter (no-op if unbound)."""

    emitter = _AMBIENT.get()
    if emitter is not None:
        emitter.activity(stage, payload)


def emit_milestone(stage: str, payload: dict[str, Any] | None = None) -> None:
    """Report a milestone event through the ambient emitter (no-op if unbound)."""

    emitter = _AMBIENT.get()
    if emitter is not None:
        emitter.milestone(stage, payload)


#: Verdict strings that mean a gate blocked / rejected the work. A blocking
#: result is reported as a milestone (durable axis + red tone in the UI); a
#: passing result is a low-value high-frequency activity event.
_BLOCKING_VERDICTS = frozenset(
    {
        "blocked",
        "block",
        "fail",
        "failed",
        "reject",
        "rejected",
        "rewrite",
        "revise",
        "needs_rewrite",
        "attention",
        "needs_attention",
        "machine_repair_required",
        "requires_machine_repair",
        "requires_human_review",
    }
)


def emit_gate_result(
    gate: str,
    *,
    verdict: str,
    severity: str | None = None,
    score: float | int | None = None,
    reasons: object = None,
    target: str | None = None,
    chapter: int | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    """Report a quality-gate outcome as one event with a stable payload shape.

    Blocking verdicts are emitted as milestones (so they survive the activity
    ring buffer and render with a red tone); passing verdicts are activity
    events. The stage is ``f"{gate}_evaluated"``. ``reasons`` is coerced to a
    list of up to 3 strings. No-op when no emitter is bound.
    """

    blocked = str(verdict).strip().lower() in _BLOCKING_VERDICTS
    payload: dict[str, Any] = {"verdict": str(verdict), "blocked": blocked}
    if target is not None:
        payload["target"] = target
    elif chapter is not None:
        payload["target"] = f"ch:{chapter}"
    if chapter is not None:
        payload["chapter_number"] = chapter
    if severity is not None:
        payload["severity"] = str(severity)
    if score is not None:
        payload["score"] = round(float(score), 1) if isinstance(score, (int, float)) else score
    if reasons:
        try:
            payload["reasons"] = [str(r) for r in list(reasons)[:3]]
        except TypeError:
            payload["reasons"] = [str(reasons)]
    if extra:
        payload.update(extra)

    stage = f"{gate}_evaluated"
    if blocked:
        emit_milestone(stage, payload)
    else:
        emit_activity(stage, payload)
