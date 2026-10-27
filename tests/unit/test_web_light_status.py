"""The frequent status read must not hash the entire mounted source tree."""

from types import SimpleNamespace

from bestseller.settings import load_settings
from bestseller.web import server


def test_light_status_retains_runtime_choices_without_expensive_provenance(monkeypatch):
    def deny(*args, **kwargs):
        raise AssertionError("light status cannot collect process/disk evidence")

    monkeypatch.setattr(server, "initialize_process_runtime", deny)
    monkeypatch.setattr(server, "disk_runtime_provenance", deny)
    cfg = load_settings()
    payload = server._web_status_payload(cfg, SimpleNamespace(_max_concurrent_tasks=3), light=True)
    assert payload["app"] == "BestSeller Web Studio"
    assert payload["database_connected"] == bool(cfg.database.url)
    assert payload["max_concurrent_tasks"] == 3
    assert "active_key" in payload["llm_profile"]
    assert payload["llm_profile"]["source"] in {"settings", "runtime"}
    assert payload["planner_model"] and payload["writer_model"]
    assert payload["runtime_provenance_available"] is True
    assert "runtime_provenance" not in payload


def test_default_status_still_collects_current_disk_and_process_evidence(monkeypatch):
    calls = []

    def process(settings):
        calls.append("process")
        return {"source": "process"}

    def disk(settings):
        calls.append("disk")
        return {"source": "disk_current"}

    monkeypatch.setattr(server, "initialize_process_runtime", process)
    monkeypatch.setattr(server, "disk_runtime_provenance", disk)
    cfg = load_settings()
    payload = server._web_status_payload(cfg, SimpleNamespace(_max_concurrent_tasks=2))
    assert calls == ["process", "disk"]
    assert payload["runtime_provenance"] == {
        "process_runtime": {"source": "process"},
        "disk_runtime": {"source": "disk_current"},
    }
    assert payload["max_concurrent_tasks"] == 2
    assert "runtime_provenance_available" not in payload


async def test_progress_read_never_runs_full_delivery_or_issues_completion(monkeypatch):
    from bestseller.services import book_delivery_readiness

    def deny(*args, **kwargs):
        raise AssertionError("progress list must not reconstruct a delivery proof")

    monkeypatch.setattr(book_delivery_readiness, "read_book_delivery_closure", deny)
    result = await server._read_project_delivery_status(
        object(),
        SimpleNamespace(slug="unrelated-closed-book", status="completed"),
        progress_only=True,
    )
    assert result == {
        "status": "not_checked",
        "is_complete": False,
        "body_complete": False,
        "reason": "delivery_verification_not_requested",
        "reader": "read_book_delivery_closure",
    }


async def test_default_delivery_error_remains_unverified_not_historical_success(monkeypatch):
    from bestseller.services import book_delivery_readiness

    async def unavailable(*args, **kwargs):
        raise ValueError("changed physical source")

    monkeypatch.setattr(book_delivery_readiness, "read_book_delivery_closure", unavailable)
    result = await server._read_project_delivery_status(
        object(), SimpleNamespace(slug="unrelated-closed-book", status="completed")
    )
    assert result["status"] == "unverified"
    assert result["is_complete"] is False
    assert result["reason"] == "delivery_readback_unavailable"


def test_not_checked_is_neither_new_completion_nor_historical_failure():
    from copy import deepcopy

    task = {
        "task_id": "retained-original",
        "project_slug": "some-old-book",
        "status": "completed",
        "current_stage": "project_closed",
        "book_state": "closed_complete",
        "error": None,
        "delivery_closure": {"status": "verified", "is_complete": True},
    }
    before = deepcopy(task)
    deferred = {"status": "not_checked", "is_complete": False, "body_complete": False}
    server._settle_task_cards_from_project_status(
        [task], {"some-old-book": "completed"}, {"some-old-book": deferred}
    )
    for key in ["task_id", "status", "current_stage", "book_state", "error"]:
        assert task[key] == before[key]
    assert task["delivery_closure"] == deferred
    assert task["delivery_verification_pending"] is True
    assert "task_history" not in task


def test_historical_completed_progress_does_not_become_a_new_stale_failure():
    row = {
        "slug": "unrelated-closed-book",
        "status": "completed",
        "book_state": "delivery_pending",
        "delivery_closure": {"status": "not_checked", "is_complete": False},
        "adoption_evidence": "verified",
        "completed_chapters": 4,
        "target_chapters": 4,
        "candidate_chapters": 0,
        "activity_status": "stale",
        "has_active_workflow": False,
        "current_workflow": {"status": "running", "updated_at": "2026-01-01T00:00:00Z"},
    }
    projected = server._project_row_to_dashboard_task(row)
    assert projected["status"] == "project"
    assert projected["delivery_closure"]["is_complete"] is False
    assert projected["activity_status"] == "stale"
    assert projected["current_stage"] != "workflow_activity_stale"


def test_same_request_native_stats_are_not_read_a_second_time(monkeypatch):
    def deny(*args, **kwargs):
        raise AssertionError("duplicate adopted-source reader")

    monkeypatch.setattr(server, "_load_task_chapter_word_stats", deny)
    stats = {
        "one-book": {
            "source": "db",
            "project_title": "Native title",
            "adoption_evidence": "verified",
            "adopted_chapters": 2,
            "candidate_chapters": 1,
            "word_count_total": 4500,
            "knowledge": {"status": "pending"},
        }
    }
    task = {"project_slug": "one-book", "title": "Old title"}
    missing = {"project_slug": "not-materialized", "title": "Pending conception"}
    output = server._attach_task_chapter_word_stats(
        object(), [task, missing], summary_only=True, same_request_stats=stats
    )
    assert output[0]["chapter_word_stats"] == stats["one-book"]
    assert output[0]["chapter_word_stats"] is not stats["one-book"]
    assert "chapter_word_stats" not in output[1]


def test_missing_current_native_row_cannot_recertify_cached_counts(monkeypatch):
    old = {"source": "db", "adoption_evidence": "verified", "adopted_chapters": 4}
    task = {"project_slug": "missing-book", "chapter_word_stats": old}
    server._attach_task_chapter_word_stats(
        object(), [task], summary_only=True, same_request_stats={}
    )
    assert task["chapter_word_stats"]["adoption_evidence"] == "unverified"
    assert task["chapter_word_stats"]["previous_snapshot"] == old
    assert task["chapter_word_stats"]["previous_snapshot"] is not old
