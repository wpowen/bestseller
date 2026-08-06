"""Ambient attribution so every LLM call says which work it belonged to.

The question "how much did this repair loop cost?" used to be unanswerable
without archaeology: ``llm_runs`` recorded the role and the model, but nothing
tied a call to the chapter, the gate that rejected it, or the rework round it
was paying for. By the time a runaway loop was noticed, the only evidence left
was the bill.

Attribution is ambient rather than threaded through call signatures. A repair
loop wraps itself in ``rework_scope(...)`` once, and every LLM call underneath
it — however deep, through any number of services — is tagged. Threading a
parameter through every call site instead would be a large diff whose failure
mode is silent: one missed call site produces unattributed spend, which is
exactly the state we are trying to leave.

Because it rides on :mod:`contextvars`, the scope is per-task: concurrent
chapters, judges and rewrites each see their own attribution, and a scope
cannot leak into a sibling coroutine.
"""

from __future__ import annotations

import contextlib
import logging
import uuid
from collections.abc import Iterator, Mapping
from contextvars import ContextVar
from typing import Any, Final

logger = logging.getLogger(__name__)

__all__ = [
    "ATTRIBUTION_METADATA_KEY",
    "attribution_scope",
    "current_attribution",
    "rework_scope",
]

#: Key under which attribution is stored inside ``llm_runs.metadata``.
ATTRIBUTION_METADATA_KEY: Final[str] = "attribution"

_ATTRIBUTION: ContextVar[Mapping[str, Any] | None] = ContextVar(
    "bestseller_cost_attribution", default=None
)


def current_attribution() -> dict[str, Any]:
    """Attribution for the work currently in flight (empty when unscoped)."""

    value = _ATTRIBUTION.get()
    return dict(value) if value else {}


@contextlib.contextmanager
def attribution_scope(**fields: Any) -> Iterator[dict[str, Any]]:
    """Tag every LLM call made inside this block.

    Nested scopes merge, inner keys winning, so a chapter-level scope can set
    ``chapter_number`` once and an inner gate scope only needs to add its own
    fields.
    """

    merged = {**current_attribution(), **{k: v for k, v in fields.items() if v is not None}}
    token = _ATTRIBUTION.set(merged)
    try:
        yield merged
    finally:
        _ATTRIBUTION.reset(token)


@contextlib.contextmanager
def rework_scope(
    *,
    chapter_number: int | None = None,
    gate: str | None = None,
    round_index: int | None = None,
    kind: str = "rework",
    event_id: str | None = None,
) -> Iterator[str]:
    """Scope one rework attempt and give it a stable id.

    Yields the ``rework_event_id`` so the caller can log it alongside the
    outcome. Every LLM call inside carries the same id, which turns "what did
    this rework cost" into a single grouped query instead of a reconstruction.
    """

    resolved_id = event_id or uuid.uuid4().hex
    with attribution_scope(
        rework_event_id=resolved_id,
        rework_kind=kind,
        chapter_number=chapter_number,
        gate=gate,
        rework_round=round_index,
    ):
        yield resolved_id


def merge_attribution_into(metadata: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return ``metadata`` with the ambient attribution attached.

    Never raises: attribution is diagnostics, and losing an LLM run record
    because a tag could not be attached would be a far worse trade.
    """

    base: dict[str, Any] = dict(metadata) if isinstance(metadata, Mapping) else {}
    try:
        attribution = current_attribution()
        if attribution:
            existing = base.get(ATTRIBUTION_METADATA_KEY)
            if isinstance(existing, Mapping):
                attribution = {**attribution, **dict(existing)}
            base[ATTRIBUTION_METADATA_KEY] = attribution
    except Exception:  # pragma: no cover — defensive
        logger.debug("could not attach cost attribution", exc_info=True)
    return base
