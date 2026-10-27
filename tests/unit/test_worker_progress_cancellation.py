from __future__ import annotations

import asyncio

import pytest
from gateway_ledger_support import gateway_ledger as gateway_ledger

from bestseller.services.progress_context import (
    TaskCancelledError,
    check_cancellation,
    set_cancellation_checker,
)
from bestseller.worker.progress import make_sync_callback

pytestmark = pytest.mark.unit


class _Reporter:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, object] | None]] = []

    async def emit(
        self,
        message: str,
        data: dict[str, object] | None = None,
        *,
        event_type: str = "progress",
    ) -> None:
        del event_type
        self.events.append((message, data))


@pytest.mark.asyncio
async def test_operator_halt_progress_event_stops_before_next_batch() -> None:
    reporter = _Reporter()
    callback = make_sync_callback(reporter)

    callback("volume_started", {"volume": 1})
    await asyncio.sleep(0)
    assert [message for message, _ in reporter.events] == ["volume_started"]

    with pytest.raises(TaskCancelledError):
        callback("autowrite_halted_by_operator", {"intent": "pause"})
    await asyncio.sleep(0)
    assert [message for message, _ in reporter.events] == [
        "volume_started",
        "autowrite_halted_by_operator",
    ]


@pytest.mark.asyncio
async def test_chapter_halt_event_is_propagated_without_affecting_normal_events() -> None:
    reporter = _Reporter()
    callback = make_sync_callback(reporter)

    callback("chapter_started", {"chapter": 1})
    await asyncio.sleep(0)
    with pytest.raises(TaskCancelledError):
        callback("chapter_loop_halted_by_operator", {"chapter": 1})
    await asyncio.sleep(0)

    assert [message for message, _ in reporter.events] == [
        "chapter_started",
        "chapter_loop_halted_by_operator",
    ]


@pytest.mark.asyncio
async def test_bound_cancellation_checker_raises_at_provider_boundary() -> None:
    async def halted() -> None:
        raise TaskCancelledError("paused")

    set_cancellation_checker(halted)
    with pytest.raises(TaskCancelledError, match="paused"):
        await check_cancellation()
    set_cancellation_checker(None)


@pytest.mark.asyncio
async def test_stop_between_completions_prevents_second_provider_request(monkeypatch, gateway_ledger) -> None:
    from unittest.mock import AsyncMock

    from bestseller.services import llm
    from bestseller.settings import load_settings
    from test_llm_services import FakeSession

    settings = load_settings()
    settings.llm.mock = False
    provider = AsyncMock(return_value=("first draft", 10, 10, "stop", None, None))
    # The existing fixture binds an explicit scope and a real SQLite ledger;
    # only the lowest transport is synthetic, so cancellation keeps its guard.
    monkeypatch.setattr(llm, "_call_litellm", provider)
    paused = False

    async def check_operator_intent() -> None:
        if paused:
            raise TaskCancelledError("paused before next request")

    request = llm.LLMCompletionRequest(
        logical_role="planner",
        system_prompt="Plan the next chapter.",
        user_prompt="Continue the same book.",
        fallback_response="fallback must not hide cancellation",
    )
    set_cancellation_checker(check_operator_intent)
    try:
        await llm.complete_text(FakeSession(), settings, request)
        paused = True
        with pytest.raises(TaskCancelledError, match="paused before next request"):
            await llm.complete_text(FakeSession(), settings, request)
    finally:
        set_cancellation_checker(None)
    assert provider.await_count == 1
