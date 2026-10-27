from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest

from bestseller.infra.db import session as db_sessions
from bestseller.services import reviews
from bestseller.services.budget import ExecutionBudgetError


class NonterminalBusyError(ExecutionBudgetError):
    terminal = False


@pytest.fixture
def harness(monkeypatch):
    state = SimpleNamespace(requests=[], finished=set(), closed=[], entered=0, exit_errors=[])

    @asynccontextmanager
    async def pooled_session():
        state.entered += 1
        index = state.entered
        try:
            yield index
        except BaseException as error:
            state.exit_errors.append(error)
            raise
        finally:
            state.closed.append((index, set(state.finished)))

    monkeypatch.setattr(db_sessions, "get_server_session", pooled_session)
    kwargs = {
        "system_prompt": "same system",
        "user_prompt": "same text",
        "fallback_response": "VERDICT: pass",
        "project": SimpleNamespace(id=uuid4(), slug="sample-identity"),
        "chapter": SimpleNamespace(chapter_number=1),
        "scene": SimpleNamespace(scene_number=1),
        "workflow_run_id": uuid4(),
        "step_run_id": uuid4(),
    }

    async def invoke(samples):
        return await reviews._resample_scene_verdict_votes(
            "shared", SimpleNamespace(), samples=samples, **kwargs
        )

    state.invoke = invoke
    return state


@pytest.mark.asyncio
@pytest.mark.parametrize("samples", [1, 3])
@pytest.mark.parametrize("fallback", [False, True])
async def test_distinct_stable_slots_for_identical_text(monkeypatch, harness, samples, fallback):
    legacy = {0: "quarantined_charged"}
    if fallback:

        @asynccontextmanager
        async def unavailable():
            raise RuntimeError("pool not initialized")
            yield

        monkeypatch.setattr(db_sessions, "get_server_session", unavailable)

    async def complete(session, settings, request):
        assert request.sample_slot not in legacy
        harness.requests.append((session, request))
        return SimpleNamespace(content="VERDICT: rewrite")

    monkeypatch.setattr(reviews, "complete_text", complete)
    for _ in range(2):
        assert await harness.invoke(samples) == ["rewrite"] * samples
    requests = [request for _, request in harness.requests]
    expected_slots = [f"scene-confirm.independent-v2:{index}" for index in range(1, samples + 1)]
    assert [request.sample_slot for request in requests] == expected_slots * 2
    assert {request.user_prompt for request in requests} == {"same text"}
    assert requests[:samples] == requests[samples:]
    assert legacy == {0: "quarantined_charged"}
    assert all(request.project_id and request.workflow_run_id for request in requests)
    if fallback:
        assert all(session == "shared" for session, _ in harness.requests)


@pytest.mark.asyncio
async def test_ordinary_vote_error_abstains(monkeypatch, harness):
    async def complete(session, settings, request):
        if session == 1:
            raise RuntimeError("ordinary provider failure")
        return SimpleNamespace(content="VERDICT: pass")

    monkeypatch.setattr(reviews, "complete_text", complete)
    assert await harness.invoke(2) == [None, "pass"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "terminal_type", [ExecutionBudgetError, NonterminalBusyError, asyncio.CancelledError]
)
async def test_terminal_stop_drains_siblings_before_pool_close(monkeypatch, harness, terminal_type):
    started = asyncio.Event()
    stop = asyncio.Event()
    release = asyncio.Event()
    terminal = terminal_type("stop")
    children = []

    async def complete(session, settings, request):
        children.append(asyncio.current_task())
        harness.requests.append(request)
        if len(children) == 2:
            started.set()
        try:
            if session == 1:
                await stop.wait()
                raise terminal
            await release.wait()
            return SimpleNamespace(content="VERDICT: pass")
        finally:
            harness.finished.add(session)

    monkeypatch.setattr(reviews, "complete_text", complete)
    parent = asyncio.create_task(harness.invoke(2))
    try:
        await asyncio.wait_for(started.wait(), 2)
        stop.set()
        for _ in range(5):
            await asyncio.sleep(0)
        premature = parent.done()
        release.set()
        with pytest.raises(terminal_type) as caught:
            await parent
        assert caught.value is terminal or terminal_type is asyncio.CancelledError
        assert not premature
        assert harness.exit_errors == []
        assert len(harness.requests) == 2
        assert all(finished == {1, 2} for _, finished in harness.closed)
    finally:
        stop.set()
        release.set()
        await asyncio.gather(parent, *children, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "terminal_type", [ExecutionBudgetError, NonterminalBusyError, asyncio.CancelledError]
)
async def test_typed_stop_preferred_to_ordinary_parse_failure(monkeypatch, harness, terminal_type):
    terminal = terminal_type("stop")
    children = []

    async def complete(session, settings, request):
        children.append(asyncio.current_task())
        harness.requests.append(request)
        try:
            await asyncio.sleep(0)
            if session == 2:
                raise terminal
            return SimpleNamespace(content="bad parse" if session == 1 else "VERDICT: pass")
        finally:
            harness.finished.add(session)

    parse = reviews._parse_llm_verdict

    def parse_with_failure(text):
        if text == "bad parse":
            raise ValueError("ordinary parser failure")
        return parse(text)

    monkeypatch.setattr(reviews, "complete_text", complete)
    monkeypatch.setattr(reviews, "_parse_llm_verdict", parse_with_failure)
    try:
        with pytest.raises(terminal_type):
            await harness.invoke(3)
        assert len(harness.requests) == 3
        assert all(finished == {1, 2, 3} for _, finished in harness.closed)
    finally:
        await asyncio.gather(*children, return_exceptions=True)


@pytest.mark.asyncio
async def test_caller_cancellation_drains_even_after_repeated_cancel(monkeypatch, harness):
    started = asyncio.Event()
    release = asyncio.Event()

    async def complete(session, settings, request):
        harness.requests.append(request)
        if len(harness.requests) == 2:
            started.set()
        try:
            await release.wait()
            return SimpleNamespace(content="VERDICT: pass")
        finally:
            harness.finished.add(session)

    monkeypatch.setattr(reviews, "complete_text", complete)
    parent = asyncio.create_task(harness.invoke(2))
    try:
        await asyncio.wait_for(started.wait(), 2)
        for _ in range(2):
            parent.cancel()
            for _ in range(5):
                await asyncio.sleep(0)
        premature = parent.done()
        closed_early = bool(harness.closed)
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await parent
        assert not premature
        assert not closed_early
        assert len(harness.requests) == 2
        assert all(finished == {1, 2} for _, finished in harness.closed)
    finally:
        release.set()
        await asyncio.gather(parent, return_exceptions=True)


@pytest.mark.asyncio
async def test_pool_init_budget_error_never_falls_back(monkeypatch, harness):
    terminal = ExecutionBudgetError("pool_init_stop")

    @asynccontextmanager
    async def unavailable():
        raise terminal
        yield

    async def complete(session, settings, request):
        harness.requests.append(request)
        return SimpleNamespace(content="VERDICT: pass")

    monkeypatch.setattr(db_sessions, "get_server_session", unavailable)
    monkeypatch.setattr(reviews, "complete_text", complete)
    with pytest.raises(ExecutionBudgetError) as caught:
        await harness.invoke(1)
    assert caught.value is terminal
    assert harness.requests == []


@pytest.mark.asyncio
async def test_pool_close_runtime_error_never_resends(monkeypatch, harness):
    @asynccontextmanager
    async def broken_close():
        yield "pooled"
        raise RuntimeError("pool close failed after paid call")

    async def complete(session, settings, request):
        harness.requests.append(request)
        return SimpleNamespace(content="VERDICT: pass")

    monkeypatch.setattr(db_sessions, "get_server_session", broken_close)
    monkeypatch.setattr(reviews, "complete_text", complete)
    with pytest.raises(RuntimeError, match="pool close failed"):
        await harness.invoke(1)
    assert len(harness.requests) == 1
