"""Rolling windows and terminal paid-execution errors must survive planner entry."""

import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from bestseller.services import planner
from bestseller.services.budget import ExecutionBudgetError
from bestseller.services.pipelines import (
    _expand_volume_plan_into_rolling_windows,
    _volume_plan_for_rolling_window,
)
from bestseller.services.rolling_outline import rolling_window_schedule_hash

pytestmark = pytest.mark.unit


class WorkflowBoundaryReachedError(RuntimeError):
    pass


def _book(genre="fantasy", language="zh-CN", counts=(50,)):
    project = SimpleNamespace(
        id=uuid4(), slug=f"boundary-{uuid4().hex}", genre=genre, sub_genre=None, language=language,
        target_chapters=sum(counts), metadata_json={},
    )
    canonical = [
        {"volume_number": i, "chapter_count_target": count, "volume_goal": "Approved goal"}
        for i, count in enumerate(counts, 1)
    ]
    windows = _expand_volume_plan_into_rolling_windows(canonical, window_size=8)
    schedule = [
        {"window_start": item["start_chapter_number"], "window_end": item["end_chapter_number"],
         "volume_number": item["volume_number"], "window_index": item["rolling_window_index"]}
        for item in windows
    ]
    project.metadata_json = {
        "rolling_outline_windows": schedule,
        "rolling_outline_windows_hash": rolling_window_schedule_hash(schedule),
        "approved_source": "untouched",
    }
    return project, canonical, windows


def _entry_boundary(monkeypatch, project):
    monkeypatch.setattr(planner, "get_project_by_slug", AsyncMock(return_value=project))
    sweep = AsyncMock()
    monkeypatch.setattr(
        "bestseller.services.planning_concurrency.cancel_stale_planning_workflows", sweep,
    )
    monkeypatch.setattr(planner, "resolve_novel_category", lambda *args: None)
    workflow = AsyncMock(side_effect=WorkflowBoundaryReachedError)
    monkeypatch.setattr(planner, "create_workflow_run", workflow)
    paid = AsyncMock()
    monkeypatch.setattr(planner, "_generate_structured_artifact", paid)
    return sweep, workflow, paid


async def _enter(project, plan, *, rolling=True, volume=1):
    return await planner.generate_volume_plan(
        None, SimpleNamespace(pipeline=SimpleNamespace(
            enable_rolling_outline=rolling, rolling_outline_window_size=8,
        )), project.slug, volume, book_spec={}, world_spec={}, cast_spec={}, volume_plan=plan,
    )


@pytest.mark.parametrize("genre,language,counts", [
    ("fantasy", "zh-CN", (50,)), ("romance", "en", (50, 50)),
])
@pytest.mark.parametrize("corruption", [
    "whole_volume", "missing_schedule", "bad_hash", "changed_schedule",
    "index", "start", "count", "end", "range", "duplicate_entry",
    "oversize_schedule", "schedule_gap", "incomplete_schedule", "foreign_book",
])
async def test_bad_window_rejected_before_workflow_sweep_or_model(
    monkeypatch, genre, language, counts, corruption,
):
    project, canonical, windows = _book(genre, language, counts)
    entry = copy.deepcopy(windows[0])
    schedule = project.metadata_json["rolling_outline_windows"]
    if corruption == "whole_volume":
        entry = copy.deepcopy(canonical[0])
    elif corruption == "missing_schedule":
        project.metadata_json.pop("rolling_outline_windows")
    elif corruption == "bad_hash":
        project.metadata_json["rolling_outline_windows_hash"] = "wrong"
    elif corruption == "changed_schedule":
        schedule[0]["window_end"] += 1  # original persisted hash cannot authorize edits
    elif corruption in {"index", "start", "count", "end"}:
        field = {"index": "rolling_window_index", "start": "start_chapter_number",
                 "count": "chapter_count_target", "end": "end_chapter_number"}[corruption]
        entry[field] += 1
    elif corruption == "range":
        entry["chapter_range"] = None
    elif corruption == "oversize_schedule":
        schedule[:] = [{"window_start": 1, "window_end": project.target_chapters,
                        "volume_number": 1, "window_index": 1}]
        project.metadata_json["rolling_outline_windows_hash"] = (
            rolling_window_schedule_hash(schedule)
        )
    elif corruption == "schedule_gap":
        schedule[1]["window_start"] += 1
        project.metadata_json["rolling_outline_windows_hash"] = (
            rolling_window_schedule_hash(schedule)
        )
    elif corruption == "incomplete_schedule":
        schedule.pop()
        project.metadata_json["rolling_outline_windows_hash"] = (
            rolling_window_schedule_hash(schedule)
        )
    elif corruption == "foreign_book":
        _, _, other_windows = _book("science-fiction", "en", (60,))
        entry = other_windows[0]

    plan = _volume_plan_for_rolling_window(canonical, entry)
    if corruption == "duplicate_entry":
        plan.append(copy.deepcopy(entry))
    metadata_before = copy.deepcopy(project.metadata_json)
    plan_before = copy.deepcopy(plan)
    sweep, workflow, paid = _entry_boundary(monkeypatch, project)
    with pytest.raises(ValueError, match="rolling_outline_entry_invalid"):
        await _enter(project, plan)
    sweep.assert_not_awaited()
    workflow.assert_not_awaited()
    paid.assert_not_awaited()
    assert project.metadata_json == metadata_before
    assert plan == plan_before


@pytest.mark.parametrize("counts,position", [((50,), 0), ((50,), 1), ((50, 50), -1), ((11,), -1)])
async def test_persisted_helper_window_reaches_normal_workflow(monkeypatch, counts, position):
    project, canonical, windows = _book(counts=counts)
    window = windows[position]
    plan = _volume_plan_for_rolling_window(canonical, window)
    sweep, workflow, paid = _entry_boundary(monkeypatch, project)
    with pytest.raises(WorkflowBoundaryReachedError):
        await _enter(project, plan, volume=window["volume_number"])
    sweep.assert_awaited_once()
    workflow.assert_awaited_once()
    paid.assert_not_awaited()


@pytest.mark.parametrize("rolling,target", [(False, 50), (True, 8)])
async def test_explicit_nonrolling_or_one_window_book_allows_full_volume(
    monkeypatch, rolling, target,
):
    project, canonical, _ = _book(counts=(target,))
    project.metadata_json = {}
    sweep, workflow, _paid = _entry_boundary(monkeypatch, project)
    with pytest.raises(WorkflowBoundaryReachedError):
        await _enter(project, canonical, rolling=rolling)
    sweep.assert_awaited_once()
    workflow.assert_awaited_once()


@pytest.mark.parametrize("code", [
    "execution_authorization_closed", "execution_cancelled", "execution_result_unknown",
])
@pytest.mark.parametrize("batched", [False, True])
async def test_terminal_execution_error_crosses_outline_boundary_once(monkeypatch, code, batched):
    project, canonical, windows = _book()
    error = ExecutionBudgetError(code, f"project:{project.id}")
    paid = AsyncMock(side_effect=error)
    monkeypatch.setattr(planner, "_generate_structured_artifact", paid)
    monkeypatch.setattr(planner, "_fetch_existing_chapter_titles", AsyncMock(return_value=[]))
    monkeypatch.setattr(planner, "_volume_outline_prompts", lambda *a, **k: ("system", "user"))
    monkeypatch.setattr(planner, "attach_planner_methodology", lambda prompt, **kwargs: prompt)
    monkeypatch.setattr(
        planner, "_compile_volume_outline_prompt", lambda *a, **k: ("system", "user"),
    )
    monkeypatch.setattr(planner, "is_strict_prewrite_project", lambda *a: False)
    diagnostics = Mock()
    monkeypatch.setattr(planner, "_outline_repair_directives_from_error", diagnostics)
    settings = SimpleNamespace(pipeline=SimpleNamespace(
        chapter_outline_repair_attempts=3, chapter_outline_batch_size=4,
    ))
    events = []
    function = (planner._generate_volume_outline_batched if batched
                else planner._generate_volume_outline_with_repair_loop)
    with pytest.raises(ExecutionBudgetError) as caught:
        await function(
            None, settings, project=project, workflow_run_id=uuid4(),
            logical_name="volume_1_chapter_outline", book_spec={}, cast_spec={},
            volume_plan=canonical, volume_entry=windows[0], fallback_payload={"chapters": []},
            volume_number=1, expected_count=16 if batched else 8, chapter_number_offset=1,
            revealed_ledger_block=None, base_constraints=[],
            progress=lambda stage, payload: events.append(stage),
        )
    assert caught.value is error
    paid.assert_awaited_once()
    diagnostics.assert_not_called()
    assert events.count("planning_outline_attempt_started") == 1
    assert "planning_outline_attempt_failed" not in events
