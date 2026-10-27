import asyncio
from pathlib import Path
import pytest

from bestseller.web import server


def _task(*, status: str, result: dict | None, payload: dict | None = None) -> server.WebTaskState:
    return server.WebTaskState(
        task_id="original-autowrite",
        task_type="autowrite",
        status=status,
        created_at="2026-09-07T00:00:00+00:00",
        updated_at="2026-09-07T00:00:00+00:00",
        project_slug="custom-xuanhuan-1788706886",
        title="custom xuanhuan",
        current_stage="failed",
        result=result,
        payload=payload or {"slug": "custom-xuanhuan-1788706886", "target_chapters": 20},
    )


@pytest.mark.parametrize("status", ["cancelled", "completed"])
def test_synthetic_resume_selects_original_incomplete_autowrite_envelope(
    tmp_path: Path, monkeypatch, status: str
) -> None:
    manager = server.WebTaskManager(persist_path=tmp_path / "tasks.json")
    # Cancellation leaves the WebTaskManager result as None/empty; chapter
    # progress must come from the current project dashboard stats.
    original = _task(status=status, result=None)
    manager._tasks[original.task_id] = original

    async def fake_dashboard(_settings, _slug):
        return _dashboard_stats(8, 20)

    monkeypatch.setattr(server, "_load_project_dashboard_task_payload", fake_dashboard)

    selected = asyncio.run(
        server._load_incomplete_original_autowrite_task(
            object(), manager, "custom-xuanhuan-1788706886"
        )
    )

    assert selected is not None
    assert selected["task_id"] == "original-autowrite"
    assert selected["payload"]["target_chapters"] == 20


def test_synthetic_resume_keeps_complete_or_unknown_task_on_repair_lane(
    tmp_path: Path, monkeypatch
) -> None:
    manager = server.WebTaskManager(persist_path=tmp_path / "tasks.json")
    manager._tasks["complete"] = _task(status="failed", result=None)

    async def fake_complete(_settings, _slug):
        return _dashboard_stats(20, 20)

    monkeypatch.setattr(server, "_load_project_dashboard_task_payload", fake_complete)
    assert (
        asyncio.run(
            server._load_incomplete_original_autowrite_task(
                object(), manager, "custom-xuanhuan-1788706886"
            )
        )
        is None
    )

    async def fake_missing_completed(_settings, _slug):
        return {"chapter_word_stats": {"target_chapters": 20}}

    monkeypatch.setattr(server, "_load_project_dashboard_task_payload", fake_missing_completed)
    assert (
        asyncio.run(
            server._load_incomplete_original_autowrite_task(
                object(), manager, "custom-xuanhuan-1788706886"
            )
        )
        is None
    )

    async def fake_no_dashboard(_settings, _slug):
        return None

    monkeypatch.setattr(server, "_load_project_dashboard_task_payload", fake_no_dashboard)
    assert (
        asyncio.run(
            server._load_incomplete_original_autowrite_task(
                object(), manager, "custom-xuanhuan-1788706886"
            )
        )
        is None
    )


def _dashboard_stats(completed: int, target: int) -> dict[str, object]:
    return {"chapter_word_stats": {"completed_chapters": completed, "target_chapters": target}}


def test_db_repair_route_prefers_full_resume_before_repair_enqueue() -> None:
    class _Manager:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, object], bool, bool]] = []

        def resume_autowrite_task(self, task_id, payload, *, delegate_to_self_heal, heal_owned):
            self.calls.append((task_id, payload, delegate_to_self_heal, heal_owned))
            return {"task_id": task_id, "status": "queued"}

    manager = _Manager()
    payload = {
        "slug": "custom-xuanhuan-1788706886",
        "project_slug": "custom-xuanhuan-1788706886",
        "target_chapters": 20,
        "_run_conception": False,
    }
    resumed = server._resume_original_autowrite_task(
        manager,
        {"task_id": "original-autowrite"},
        payload,
        heal_owned=False,
    )

    assert resumed == {"task_id": "original-autowrite", "status": "queued"}
    assert manager.calls == [("original-autowrite", payload, False, False)]


def test_structural_block_marks_original_task_without_resuming() -> None:
    calls: list[tuple[str, object]] = []

    class _Manager:
        def mark_task_blocked_by_project_gate(self, task_id, *, stage, error):
            calls.append(("gate", (task_id, stage, error)))

        def mark_task_blocked_by_structural_repair(self, task_id, error):
            calls.append(("repair", (task_id, error)))

    server._mark_db_repair_resume_block(
        _Manager(),
        "original-autowrite",
        {
            "blocked_generation_gate": True,
            "current_stage": "benchmark_alignment_missing",
            "error": "missing benchmark evidence",
        },
    )

    assert calls == [
        (
            "gate",
            ("original-autowrite", "benchmark_alignment_missing", "missing benchmark evidence"),
        )
    ]
