"""Unit tests for the ``PipelineStuckError`` / ``_persist_stuck_state`` pair.

Covers the chapter-level failure path: when chapter generation exhausts
every retry the pipeline must (a) write a resumable marker onto the
project and (b) raise a ``PipelineStuckError`` cleanly so the self-heal
scanner can pick it up later.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

import pytest

from bestseller.services.if_generation import (
    PipelineStuckError,
    _persist_stuck_state,
)

pytestmark = pytest.mark.unit


@dataclass
class _FakeProject:
    id: Any
    slug: str = "some-book"
    status: str = "writing"
    metadata_json: dict[str, Any] = field(default_factory=dict)


class _FakeSession:
    def __init__(self) -> None:
        self.flushed = False

    async def flush(self) -> None:
        self.flushed = True


@pytest.mark.asyncio
async def test_persist_stuck_state_writes_resumable_marker() -> None:
    """Stuck marker must land on project.metadata_json + flip status to paused."""
    project = _FakeProject(id=uuid4())
    session = _FakeSession()

    await _persist_stuck_state(session, project, chapter_number=42, error_message="boom")

    assert project.status == "paused"
    assert project.metadata_json["stuck_at_chapter"] == 42
    assert project.metadata_json["last_error"] == "boom"
    assert "paused_at" in project.metadata_json
    # paused_at must be ISO-8601 UTC so the self-heal scanner can parse it
    _dt.datetime.fromisoformat(project.metadata_json["paused_at"])
    assert session.flushed is True


@pytest.mark.asyncio
async def test_persist_stuck_state_truncates_long_error_message() -> None:
    """Huge tracebacks must not bloat the JSONB column."""
    project = _FakeProject(id=uuid4())
    session = _FakeSession()
    huge = "x" * 5000

    await _persist_stuck_state(session, project, chapter_number=1, error_message=huge)

    assert len(project.metadata_json["last_error"]) == 2000


@pytest.mark.asyncio
async def test_persist_stuck_state_preserves_other_metadata_keys() -> None:
    """Must not wipe unrelated metadata — it's additive only."""
    project = _FakeProject(
        id=uuid4(),
        metadata_json={"premise": "foo", "volume_plan": [1, 2, 3]},
    )
    session = _FakeSession()

    await _persist_stuck_state(session, project, chapter_number=7, error_message="e")

    assert project.metadata_json["premise"] == "foo"
    assert project.metadata_json["volume_plan"] == [1, 2, 3]
    assert project.metadata_json["stuck_at_chapter"] == 7


def test_pipeline_stuck_error_carries_chapter_number_and_cause() -> None:
    """The stuck error must expose the chapter number so callers can log it."""
    cause = RuntimeError("LLM exhausted")
    err = PipelineStuckError(chapter_number=17, original=cause)

    assert err.chapter_number == 17
    assert err.original is cause
    assert "17" in str(err)
    assert "LLM exhausted" in str(err)
    assert "RuntimeError" in str(err)


def test_pipeline_stuck_error_is_a_runtime_error() -> None:
    """Callers may catch it via ``except RuntimeError`` — verify that works."""
    err = PipelineStuckError(chapter_number=1, original=Exception("x"))
    assert isinstance(err, RuntimeError)
