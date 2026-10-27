"""Task invocation status never establishes chapter adoption or book delivery."""

from uuid import uuid4

import pytest

from bestseller.api.routers.tasks import _derive_status_from_events

pytestmark = pytest.mark.unit


def event(stage, event_type="progress", **data):
    return {"message": stage, "event_type": event_type, "data": data}


def terminal(**extra):
    return dict(terminal=True, code="execution_result_unknown", **extra)


@pytest.mark.parametrize("event_type", ["completed", "progress", "failed"])
def test_terminal_payload_wins_over_event_type(event_type):
    assert _derive_status_from_events([event("finished", event_type, **terminal())]) == "failed"


@pytest.mark.parametrize(
    "stage,event_type",
    [
        ("skipped_halted", "skipped_halted"),
        ("execution_cancelled", "cancelled"),
        ("cancelled", "progress"),
    ],
)
def test_halt_and_cancel_are_not_running(stage, event_type):
    assert _derive_status_from_events([event(stage, event_type, status=stage)]) == "cancelled"


def test_late_progress_and_completion_do_not_erase_failure():
    events = [
        event("execution_result_unknown", "failed", **terminal()),
        event("late_heartbeat"),
        event("completed", "completed"),
    ]
    assert _derive_status_from_events(events) == "failed"


def test_explicit_new_attempt_does_not_inherit_old_terminal():
    old, new = str(uuid4()), str(uuid4())
    events = [
        event("chapter_pipeline_started", attempt_id=old),
        event("execution_result_unknown", "failed", **terminal(attempt_id=old)),
        event("chapter_pipeline_started", attempt_id=new),
        event("late_old_failure", "failed", **terminal(attempt_id=old)),
        event("chapter_draft_started", attempt_id=new),
    ]
    assert _derive_status_from_events(events) == "running"


def test_child_start_does_not_create_a_new_root_attempt():
    root, child = str(uuid4()), str(uuid4())
    events = [
        event("project_pipeline_started", workflow_run_id=root),
        event("execution_result_unknown", "failed", **terminal(workflow_run_id=root)),
        event("scene_started", workflow_run_id=child),
        event("completed", "completed", workflow_run_id=child),
    ]
    assert _derive_status_from_events(events) == "failed"


def test_unrelated_job_cannot_override_current_terminal():
    events = [
        event("task_started", job_id="job-a", attempt_id="attempt-a"),
        event("execution_result_unknown", "failed", **terminal(job_id="job-a")),
        event("task_started", job_id="job-b", attempt_id="attempt-b"),
        event("completed", "completed", job_id="job-b"),
    ]
    assert _derive_status_from_events(events) == "failed"


def test_workflow_attempt_boundary_requires_explicit_root_start():
    old, new = str(uuid4()), str(uuid4())
    events = [
        event("chapter_pipeline_started", workflow_run_id=old),
        event("execution_result_unknown", "failed", **terminal(workflow_run_id=old)),
        event("chapter_pipeline_started", workflow_run_id=new),
        event("completed", "completed", workflow_run_id=new),
        event("old_heartbeat", workflow_run_id=old),
    ]
    assert _derive_status_from_events(events) == "completed"


def test_completion_is_only_task_completion_and_does_not_mutate_evidence():
    events = [event("completed", "completed", adopted=False, chapter_number=2)]
    assert _derive_status_from_events(events) == "incomplete"
    assert events[0]["data"]["adopted"] is False
    assert "completed_chapters" not in events[0]["data"]


def test_child_completion_cannot_complete_project_task():
    events = [
        event("project_pipeline_started"),
        event("chapter_pipeline_completed"),
        event("scene_review_completed"),
    ]
    assert _derive_status_from_events(events) == "running"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "receipt",
    [
        event("completed", "completed", **terminal()),
        event("skipped_halted", "skipped_halted"),
        event("cancelled", "cancelled"),
    ],
)
async def test_sse_closes_for_structured_terminal_and_halt(receipt):
    import json

    from bestseller.api.routers.tasks import stream_task_events

    class Redis:
        async def lrange(self, *args):
            return [json.dumps(receipt)]

        def pubsub(self):
            pytest.fail("terminal history must close before subscribing")

    response = await stream_task_events("job-a", object(), Redis(), object())
    frames = [frame async for frame in response.body_iterator]
    assert len(frames) == 2
    assert "stream_end" in frames[-1]


@pytest.mark.asyncio
async def test_sse_replays_old_terminal_and_new_attempt_before_closing_current():
    import json

    from bestseller.api.routers.tasks import stream_task_events

    history = [
        event("task_started", attempt_id="old"),
        event("failed", "failed", **terminal(attempt_id="old")),
        event("task_started", attempt_id="new"),
    ]
    live = [
        event("failed", "failed", **terminal(attempt_id="old")),
        event("completed", "completed", attempt_id="new"),
    ]

    class Pubsub:
        closed = False

        async def subscribe(self, channel):
            pass

        async def get_message(self, **kwargs):
            return {"type": "message", "data": json.dumps(live.pop(0))}

        async def unsubscribe(self, channel):
            pass

        async def aclose(self):
            self.closed = True

    pubsub = Pubsub()

    class Redis:
        async def lrange(self, *args):
            return [json.dumps(row) for row in history]

        def pubsub(self):
            return pubsub

    class Request:
        async def is_disconnected(self):
            return False

    response = await stream_task_events("job-a", Request(), Redis(), object())
    frames = [frame async for frame in response.body_iterator]
    assert len(frames) == 6
    assert '"attempt_id": "new"' in frames[-2]
    assert "stream_end" in frames[-1]
    assert pubsub.closed


def test_same_attempt_can_carry_both_workflow_and_attempt_aliases():
    events = [
        event("task_started", attempt_id="a", workflow_run_id="w"),
        event("failed", "failed", workflow_run_id="w"),
        event("late_progress", attempt_id="a"),
    ]
    assert _derive_status_from_events(events) == "failed"


def test_late_old_root_cannot_reopen_a_retired_attempt():
    events = [
        event("task_started", attempt_id="old"),
        event("failed", "failed", attempt_id="old"),
        event("task_started", attempt_id="new"),
        event("failed", "failed", attempt_id="new"),
        event("task_started", attempt_id="old"),
    ]
    assert _derive_status_from_events(events) == "failed"


@pytest.mark.parametrize(
    "stage,expected",
    [("failed", "failed"), ("cancelled", "cancelled"), ("skipped_halted", "cancelled")],
)
def test_unclassified_terminal_does_not_downgrade_explicit_outcome(stage, expected):
    assert _derive_status_from_events([event(stage, stage, terminal=True)]) == expected
