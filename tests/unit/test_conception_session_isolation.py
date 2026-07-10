from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from bestseller.services import conception as conception_services
from bestseller.services import planning_concurrency
from bestseller.services.degradation_tracker import DegradationEvent, DegradationTracker


class _FakeSession:
    def __init__(self, name: str) -> None:
        self.name = name
        self.commits = 0
        self.rollbacks = 0
        self.closed = 0

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1

    async def close(self) -> None:
        self.closed += 1


class _FakeSessionContext:
    def __init__(self, session: _FakeSession) -> None:
        self.session = session

    async def __aenter__(self) -> _FakeSession:
        return self.session

    async def __aexit__(self, *_args: object) -> None:
        await self.session.close()


class _FakeSessionFactory:
    def __init__(self) -> None:
        self.sessions: list[_FakeSession] = []

    def __call__(self) -> _FakeSessionContext:
        session = _FakeSession(f"lane-{len(self.sessions) + 1}")
        self.sessions.append(session)
        return _FakeSessionContext(session)


def _settings(*, quality_mode: str = "closure", fallback_model: str | None = None) -> Any:
    role = SimpleNamespace(
        model="primary-model",
        rate_limit_fallback_model=fallback_model,
    )
    return SimpleNamespace(
        pipeline=SimpleNamespace(quality_mode=quality_mode),
        llm=SimpleNamespace(planner=role, critic=role),
    )


@pytest.mark.asyncio
async def test_parallel_conception_lanes_receive_distinct_sessions() -> None:
    parent_session = object()
    factory = _FakeSessionFactory()
    barrier = asyncio.Event()
    entered: list[_FakeSession] = []

    async def lane(session: Any) -> str:
        assert session is not parent_session
        entered.append(session)
        if len(entered) == 3:
            barrier.set()
        await asyncio.wait_for(barrier.wait(), timeout=1)
        return session.name

    results = await asyncio.gather(
        *(
            planning_concurrency.run_in_isolated_session(
                parent_session,
                lane,
                session_factory=factory,
            )
            for _ in range(3)
        )
    )

    assert results == ["lane-1", "lane-2", "lane-3"]
    assert len({id(session) for session in entered}) == 3
    assert all(session.commits == 1 for session in factory.sessions)
    assert all(session.rollbacks == 0 for session in factory.sessions)
    assert all(session.closed == 1 for session in factory.sessions)


@pytest.mark.asyncio
async def test_failed_conception_lane_rolls_back_only_its_session() -> None:
    factory = _FakeSessionFactory()

    async def fail(_session: Any) -> None:
        raise RuntimeError("lane failed")

    with pytest.raises(RuntimeError, match="lane failed"):
        await planning_concurrency.run_in_isolated_session(
            object(),
            fail,
            session_factory=factory,
        )

    assert len(factory.sessions) == 1
    assert factory.sessions[0].commits == 0
    assert factory.sessions[0].rollbacks == 1
    assert factory.sessions[0].closed == 1


@pytest.mark.asyncio
async def test_isolated_session_rejects_non_engine_parent_bind() -> None:
    parent = SimpleNamespace(bind=object())

    async def noop(_session: Any) -> None:
        return None

    with pytest.raises(RuntimeError, match="AsyncEngine"):
        await planning_concurrency.run_in_isolated_session(parent, noop)


def test_degradation_tracker_uses_shared_structured_events() -> None:
    tracker = DegradationTracker()
    tracker.record(
        stage="conception.market",
        component="market_strategist",
        reason="model_fallback",
        severity="error",
        fallback=True,
        model="backup-model",
        metadata={"primary_model": "primary-model"},
    )

    assert tracker.events == (
        DegradationEvent(
            stage="conception.market",
            component="market_strategist",
            reason="model_fallback",
            severity="error",
            fallback=True,
            model="backup-model",
            metadata={"primary_model": "primary-model"},
        ),
    )


@pytest.mark.asyncio
async def test_llm_json_repair_records_structured_nonfallback_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = iter([("not-json", None), ('{"proposal": "repaired"}', None)])

    async def fake_llm_call(*_args: object, **_kwargs: object) -> tuple[str, None]:
        return next(responses)

    monkeypatch.setattr(conception_services, "_llm_call", fake_llm_call)
    tracker = DegradationTracker()

    payload, _ids = await conception_services._llm_call_json(
        object(),
        _settings(),
        role="planner",
        system_prompt="system",
        user_prompt="user",
        fallback='{"proposal": "fallback"}',
        template="conception_market",
        stage="conception.market",
        degradation_tracker=tracker,
        degradation_component="market_strategist",
    )

    assert payload == {"proposal": "repaired"}
    assert len(tracker.events) == 1
    assert tracker.events[0].reason == "json_repair"
    assert tracker.events[0].fallback is False


@pytest.mark.asyncio
async def test_configured_backup_model_switch_records_degradation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_complete_text(*_args: object, **_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(
            content='{"proposal": "backup"}',
            provider="openai",
            finish_reason="stop",
            model_name="backup-model",
            metadata={"rate_limit_fallback_active": True},
            llm_run_id=None,
        )

    monkeypatch.setattr(conception_services, "complete_text", fake_complete_text)
    tracker = DegradationTracker()

    payload, _ids = await conception_services._llm_call_json(
        object(),
        _settings(fallback_model="backup-model"),
        role="planner",
        system_prompt="system",
        user_prompt="user",
        fallback='{"proposal": "static"}',
        template="conception_market",
        stage="conception.market",
        degradation_tracker=tracker,
        degradation_component="market_strategist",
    )

    assert payload == {"proposal": "backup"}
    assert tracker.events[0].reason == "model_fallback"
    assert tracker.events[0].model == "backup-model"
    assert tracker.events[0].metadata["primary_model"] == "primary-model"


@pytest.mark.asyncio
async def test_selected_primary_equal_to_configured_backup_is_not_false_degradation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_complete_text(*_args: object, **_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(
            content='{"proposal": "selected-primary"}',
            provider="openai",
            finish_reason="stop",
            model_name="backup-model",
            effective_primary_model="backup-model",
            fallback_used=False,
            fallback_source=None,
            metadata={},
            llm_run_id=None,
        )

    monkeypatch.setattr(conception_services, "complete_text", fake_complete_text)
    tracker = DegradationTracker()

    payload, _ids = await conception_services._llm_call_json(
        object(),
        _settings(fallback_model="backup-model"),
        role="planner",
        system_prompt="system",
        user_prompt="user",
        fallback='{"proposal": "static"}',
        template="conception_market",
        stage="conception.market",
        degradation_tracker=tracker,
        degradation_component="market_strategist",
    )

    assert payload == {"proposal": "selected-primary"}
    assert tracker.events == ()


@pytest.mark.asyncio
async def test_cast_audit_structural_damage_is_recorded_and_original_is_kept(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = {"protagonist_archetype": "doctor", "name": "Lin"}

    async def fake_json(*_args: object, **_kwargs: object) -> tuple[dict[str, Any], list[Any]]:
        return {"unexpected": "shape"}, []

    monkeypatch.setattr(conception_services, "_llm_call_json", fake_json)
    tracker = DegradationTracker()

    audited, _ids = await conception_services._audit_cast_reality(
        object(),
        _settings(),
        character_proposal=original,
        ctx={"genre": "现实", "language": "zh-CN"},
        is_en=False,
        degradation_tracker=tracker,
    )

    assert audited is original
    assert tracker.events[0].stage == "conception.cast_reality_audit"
    assert tracker.events[0].reason == "structure_invalid"
    assert tracker.events[0].fallback is True


async def _round_one_lane(
    value: str,
    tracker: DegradationTracker | None = None,
) -> dict[str, str]:
    if tracker is not None:
        tracker.record(
            stage=f"conception.{value}",
            component=value,
            reason="static_fallback",
            severity="error",
            fallback=True,
        )
    return {value: "ok"}


@pytest.mark.asyncio
async def test_real_round_one_wiring_closure_preserves_successful_lanes() -> None:
    tracker = DegradationTracker()

    async def fail() -> str:
        raise RuntimeError("market unavailable")

    outcomes = await conception_services._run_required_conception_lanes(
        market_lane=fail,
        character_lane=lambda: _round_one_lane("character_architect"),
        world_lane=lambda: _round_one_lane("world_builder"),
        fallbacks={"market_strategist": "market-fallback"},
        tracker=tracker,
        quality_mode="closure",
    )

    assert outcomes["market_strategist"] == "market-fallback"
    assert outcomes["character_architect"] == {"character_architect": "ok"}
    assert outcomes["world_builder"] == {"world_builder": "ok"}
    assert {event.component for event in tracker.events} == {"market_strategist"}


@pytest.mark.asyncio
async def test_real_round_one_wiring_strict_blocks_required_lane_fallback() -> None:
    tracker = DegradationTracker()

    async def market() -> str:
        return await _round_one_lane("market_strategist", tracker)

    with pytest.raises(conception_services.ConceptionRequiredLaneError, match="blocked"):
        await conception_services._run_required_conception_lanes(
            market_lane=market,
            character_lane=lambda: _round_one_lane("character_architect"),
            world_lane=lambda: _round_one_lane("world_builder"),
            fallbacks={},
            tracker=tracker,
            quality_mode="strict",
        )


@pytest.mark.asyncio
async def test_strict_blocks_required_lane_that_returns_empty_json() -> None:
    tracker = DegradationTracker()

    with pytest.raises(conception_services.ConceptionRequiredLaneError, match="blocked"):
        await conception_services._run_required_conception_lanes(
            market_lane=lambda: asyncio.sleep(0, result=({}, [])),
            character_lane=lambda: asyncio.sleep(0, result=({"protagonist_archetype": "A"}, [], [])),
            world_lane=lambda: asyncio.sleep(0, result=({"world_premise": "B"}, [])),
            fallbacks={"market_strategist": ({"platform": "fallback"}, [])},
            tracker=tracker,
            quality_mode="strict",
        )

    assert any(
        event.component == "market_strategist" and event.reason == "empty_result"
        for event in tracker.events
    )


@pytest.mark.asyncio
async def test_strict_blocks_cast_reality_auditor_fallback() -> None:
    tracker = DegradationTracker()

    async def character_lane() -> tuple[dict[str, str], list[object], list[object]]:
        tracker.record(
            stage="conception.cast_reality_audit",
            component="cast_reality_auditor",
            reason="structure_invalid",
            severity="error",
            fallback=True,
        )
        return {"protagonist_archetype": "A"}, [], []

    with pytest.raises(conception_services.ConceptionRequiredLaneError, match="blocked"):
        await conception_services._run_required_conception_lanes(
            market_lane=lambda: asyncio.sleep(0, result=({"platform": "P"}, [])),
            character_lane=character_lane,
            world_lane=lambda: asyncio.sleep(0, result=({"world_premise": "B"}, [])),
            fallbacks={},
            tracker=tracker,
            quality_mode="strict",
        )


@pytest.mark.asyncio
async def test_strict_error_carries_all_blockers_in_stable_component_order() -> None:
    tracker = DegradationTracker()

    async def degraded(component: str) -> dict[str, str]:
        tracker.record(
            stage=f"conception.{component}",
            component=component,
            reason="fallback",
            severity="error",
            fallback=True,
        )
        return {component: "fallback"}

    with pytest.raises(conception_services.ConceptionRequiredLaneError) as exc_info:
        await conception_services._run_required_conception_lanes(
            market_lane=lambda: degraded("market_strategist"),
            character_lane=lambda: degraded("character_architect"),
            world_lane=lambda: degraded("world_builder"),
            fallbacks={},
            tracker=tracker,
            quality_mode="strict",
        )

    assert [event.component for event in exc_info.value.blocking_events] == [
        "market_strategist",
        "character_architect",
        "world_builder",
    ]


@pytest.mark.asyncio
async def test_real_round_one_wiring_cancels_and_awaits_siblings() -> None:
    tracker = DegradationTracker()
    started = asyncio.Event()
    cancelled = {"market": False, "character": False, "world": False}

    async def wait_forever(name: str) -> str:
        started.set()
        try:
            await asyncio.Future()
        finally:
            cancelled[name] = True

    task = asyncio.create_task(
        conception_services._run_required_conception_lanes(
            market_lane=lambda: wait_forever("market"),
            character_lane=lambda: wait_forever("character"),
            world_lane=lambda: wait_forever("world"),
            fallbacks={},
            tracker=tracker,
            quality_mode="closure",
        )
    )
    await started.wait()
    await asyncio.sleep(0)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert cancelled == {"market": True, "character": True, "world": True}


def test_pipeline_quality_mode_defaults_to_closure() -> None:
    from bestseller.settings import PipelineSettings

    assert PipelineSettings().quality_mode == "closure"
