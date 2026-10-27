"""Batch scheduling must save wall time, not skip work or share transactions."""

import asyncio
import json
from types import SimpleNamespace

import pytest

from bestseller.services import concept_tournament as ct
from bestseller.services.progress_context import TaskCancelledError

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


async def test_batches_overlap_but_merge_in_input_order(monkeypatch):
    active = 0
    peak = 0
    both_started = asyncio.Event()
    events = []
    monkeypatch.setattr(ct, "emit_activity", lambda stage, data: events.append(data))

    async def operation(index, item):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        try:
            if index == 0:
                await asyncio.wait_for(both_started.wait(), 1)
            elif index == 1:
                both_started.set()
            return item * 2
        finally:
            active -= 1

    values = await ct._run_tournament_batches(
        [1, 2, 3, 4], operation, concurrency=2, stage="raw_idea_pool"
    )
    assert values == [2, 4, 6, 8]
    assert peak == 2
    completed = [e for e in events if e["status"] == "completed"]
    assert [e["completed"] for e in completed] == [1, 2, 3, 4]
    assert all(e["total"] == 4 and e["elapsed_seconds"] >= 0 for e in events)


async def test_batch_error_cancels_and_drains_siblings():
    sibling_started = asyncio.Event()
    sibling_done = asyncio.Event()

    async def operation(index, item):
        if index == 0:
            await sibling_started.wait()
            raise ValueError("failed batch")
        sibling_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            sibling_done.set()

    with pytest.raises(ValueError, match="failed batch"):
        await ct._run_tournament_batches([0, 1], operation, concurrency=2, stage="rank")
    assert sibling_done.is_set()


async def test_parent_cancellation_drains_all_started_work():
    started = asyncio.Event()
    finished = []

    async def operation(index, item):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            finished.append(index)

    task = asyncio.create_task(
        ct._run_tournament_batches([0, 1, 2], operation, concurrency=1, stage="rank")
    )
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert finished == [0]


async def test_progress_cancellation_stops_batch_before_model_calls(monkeypatch):
    calls: list[int] = []

    def cancel_progress(_stage, _data):
        raise TaskCancelledError("user cancelled")

    monkeypatch.setattr(ct, "emit_activity", cancel_progress)

    async def operation(index, item):
        calls.append(index)
        return item

    with pytest.raises(TaskCancelledError, match="user cancelled"):
        await ct._run_tournament_batches(
            [0, 1, 2], operation, concurrency=2, stage="raw_idea_pool"
        )

    assert calls == []


async def test_default_generator_uses_lane_session_when_isolated(monkeypatch):
    from bestseller.services import llm, planning_concurrency

    parent = object()
    lane = object()
    seen = []

    async def isolated(session, operation):
        assert session is parent
        return await operation(lane)

    async def complete(session, settings, request):
        seen.append(session)
        return SimpleNamespace(content="result", llm_run_id="receipt")

    monkeypatch.setattr(planning_concurrency, "run_in_isolated_session", isolated)
    monkeypatch.setattr(llm, "complete_text", complete)
    generator = await ct._default_generator(parent, None, template="test", isolated_session=True)
    assert await generator("system", "user") == ("result", "receipt")
    assert seen == [lane]


@pytest.mark.parametrize("incomplete", [False, True])
async def test_production_batches_preserve_order_and_require_full_rank_coverage(
    monkeypatch,
    incomplete,
):
    calls = {}
    entered = {}
    active = {}
    peak = {}
    flags = {}

    async def factory(session, settings, *, template, **kwargs):
        flags[template] = kwargs.get("isolated_session", False)
        entered[template] = asyncio.Event()
        calls[template] = active[template] = peak[template] = 0

        async def generate(system, user):
            index = calls[template]
            calls[template] += 1
            active[template] += 1
            peak[template] = max(peak[template], active[template])
            try:
                if index == 0:
                    await asyncio.wait_for(entered[template].wait(), 1)
                elif index == 1:
                    entered[template].set()
                if template.endswith("raw_idea_pool"):
                    payload = {
                        "ideas": [{"seed": f"第{index}批第{i}位少年修行。"} for i in range(2)]
                    }
                elif template.endswith("raw_idea_rank"):
                    if incomplete and index >= 2:
                        return "{}", f"{template}-{index}"
                    payload = {
                        "ranked": [
                            {"index": i, "freshness": 0} for i in range(1 if incomplete else 2)
                        ]
                    }
                else:
                    raise AssertionError(f"Unexpected paid stage: {template}")
                return json.dumps(payload), f"{template}-{index}"
            finally:
                active[template] -= 1

        return generate

    monkeypatch.setattr(ct, "_default_generator", factory)
    result = await ct.run_concept_tournament(
        None,
        SimpleNamespace(pipeline=SimpleNamespace(concept_tournament_batch_concurrency=2)),
        genre="东方玄幻",
        sub_genre="东方玄幻",
        chapter_count=50,
        config={
            "candidate_prompt_mode": "engine_first",
            "n_candidates": 4,
            "raw_idea_pool_multiplier": 1,
            "raw_idea_generation_batch_size": 2,
            "raw_idea_rank_batch_size": 2,
            "raw_idea_pool_topup_calls": 0,
            "raw_idea_judge_model_key": "test-model",
        },
    )
    for stage in ("raw_idea_pool", "raw_idea_rank"):
        template = f"concept_tournament_{stage}"
        assert peak[template] == 2
        assert flags[template] is True
        assert active[template] == 0
    assert [item["seed"] for item in result.raw_ideas] == [
        f"第{batch}批第{i}位少年修行。" for batch in range(2) for i in range(2)
    ]
    assert result.candidate_generation_calls == 2
    assert result.raw_idea_rank_coverage["expected"] == 4
    assert result.raw_idea_rank_coverage["complete"] is not incomplete
    if incomplete:
        assert result.raw_idea_rank_coverage["missing_indexes"] == [1, 3]
        assert any("raw_idea_rank_coverage" in r["failed_axes"] for r in result.engine_rejections)
    assert result.winner is None
