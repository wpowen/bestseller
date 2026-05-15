"""Unit tests for ``quality_levers.multi_persona_runner``.

The runner is async and talks to :mod:`bestseller.services.llm`.
We patch :func:`bestseller.services.llm.complete_text` to keep the
tests hermetic — no network, no LLM, no rate-limit budget.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from bestseller.services.quality_levers.multi_persona_runner import (
    MultiPersonaCallContext,
    run_async_multi_persona_critique,
)


pytestmark = pytest.mark.unit


class _FakeLLMResult:
    """Mimic the ``LLMCompletionResult.content`` attribute used by the runner."""

    def __init__(self, content: str) -> None:
        self.content = content


def _persona_payload(score: float, *, must_rewrite: bool = False, persona: str = "") -> str:
    """Build a JSON string that the runner can parse back into a PersonaResult."""

    return json.dumps(
        {
            "overall_score": score,
            "must_rewrite": must_rewrite,
            "verdict": "rewrite" if must_rewrite else "accept",
            "top_3_issues": [
                {
                    "issue": f"{persona} sample issue",
                    "severity": "medium",
                    "suggested_cause_id": "weak_attraction",
                }
            ],
            "one_line_takeaway": f"{persona} takeaway",
        },
        ensure_ascii=False,
    )


def _make_patched_complete_text(
    responses: dict[str, str] | None = None,
    *,
    raise_for: set[str] | None = None,
):
    """Return an async stub that mimics ``complete_text`` signatures."""

    responses = responses or {}
    raise_for = raise_for or set()

    async def fake_complete_text(request, **_kwargs: Any) -> _FakeLLMResult:
        persona_id = request.metadata.get("persona_id", "")
        if persona_id in raise_for:
            raise RuntimeError(f"forced error for {persona_id}")
        payload = responses.get(
            persona_id,
            _persona_payload(0.80, persona=persona_id),
        )
        return _FakeLLMResult(payload)

    return fake_complete_text


@pytest.mark.asyncio
async def test_run_async_multi_persona_critique_aggregates_all_four(monkeypatch) -> None:
    fake = _make_patched_complete_text(
        responses={
            "platform_editor": _persona_payload(0.82, persona="platform_editor"),
            "new_reader": _persona_payload(0.78, persona="new_reader"),
            "loyal_reader": _persona_payload(0.80, persona="loyal_reader"),
            "peer_author": _persona_payload(0.76, persona="peer_author"),
        }
    )
    monkeypatch.setattr(
        "bestseller.services.quality_levers.multi_persona_runner.complete_text",
        fake,
    )
    execution = await run_async_multi_persona_critique(
        chapter_text="两个时辰。验尸房的门被锁上时，沈青崖正用银针撬开喉骨。",
    )
    persona_ids = {inv.persona_id for inv in execution.invocations}
    assert {
        "platform_editor",
        "new_reader",
        "loyal_reader",
        "peer_author",
    } <= persona_ids
    aggregate = execution.aggregate
    assert pytest.approx(aggregate.min_score, abs=1e-3) == 0.76
    assert aggregate.must_rewrite is False


@pytest.mark.asyncio
async def test_run_async_multi_persona_critique_records_llm_errors(monkeypatch) -> None:
    fake = _make_patched_complete_text(raise_for={"peer_author"})
    monkeypatch.setattr(
        "bestseller.services.quality_levers.multi_persona_runner.complete_text",
        fake,
    )
    execution = await run_async_multi_persona_critique(chapter_text="…")
    errored = [inv for inv in execution.invocations if inv.error]
    assert any(inv.persona_id == "peer_author" for inv in errored)
    # Other three personas still succeeded
    assert sum(1 for inv in execution.invocations if inv.result is not None) >= 3


@pytest.mark.asyncio
async def test_run_async_multi_persona_critique_handles_unparseable_json(monkeypatch) -> None:
    async def fake_complete_text(request, **_kwargs):
        return _FakeLLMResult("this is not JSON")

    monkeypatch.setattr(
        "bestseller.services.quality_levers.multi_persona_runner.complete_text",
        fake_complete_text,
    )
    execution = await run_async_multi_persona_critique(chapter_text="…")
    assert all(inv.error == "json_parse_failed" for inv in execution.invocations)
    assert execution.aggregate.personas == ()


@pytest.mark.asyncio
async def test_run_async_multi_persona_critique_hard_floor_triggers_rewrite(monkeypatch) -> None:
    fake = _make_patched_complete_text(
        responses={
            "platform_editor": _persona_payload(0.85, persona="platform_editor"),
            "new_reader": _persona_payload(0.85, persona="new_reader"),
            "loyal_reader": _persona_payload(0.85, persona="loyal_reader"),
            "peer_author": _persona_payload(0.60, must_rewrite=True, persona="peer_author"),
        }
    )
    monkeypatch.setattr(
        "bestseller.services.quality_levers.multi_persona_runner.complete_text",
        fake,
    )
    execution = await run_async_multi_persona_critique(chapter_text="…")
    assert execution.aggregate.must_rewrite is True


@pytest.mark.asyncio
async def test_run_async_multi_persona_critique_passes_call_context_metadata(monkeypatch) -> None:
    captured: list[dict[str, Any]] = []

    async def fake_complete_text(request, **_kwargs):
        captured.append(
            {
                "persona_id": request.metadata.get("persona_id"),
                "prompt_template": request.prompt_template,
                "project_id": request.project_id,
            }
        )
        return _FakeLLMResult(_persona_payload(0.80))

    monkeypatch.setattr(
        "bestseller.services.quality_levers.multi_persona_runner.complete_text",
        fake_complete_text,
    )
    from uuid import uuid4

    project_id = uuid4()
    await run_async_multi_persona_critique(
        chapter_text="…",
        call_context=MultiPersonaCallContext(
            project_id=project_id,
            prompt_template="custom_template",
        ),
    )
    assert all(entry["prompt_template"] == "custom_template" for entry in captured)
    assert all(entry["project_id"] == project_id for entry in captured)
    persona_ids = {entry["persona_id"] for entry in captured}
    assert {
        "platform_editor",
        "new_reader",
        "loyal_reader",
        "peer_author",
    } <= persona_ids
