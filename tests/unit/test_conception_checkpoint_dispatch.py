"""Conception checkpoint contract tests without provider calls."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest
from support.recovery_entry_seams import offline_review_resume, offline_web_owner  # noqa: F401

from bestseller.services.conception_checkpoints import (
    SCHEMA_VERSION,
    load_conception_checkpoint,
    stable_conception_input_hash,
)

pytestmark = pytest.mark.unit


def test_stable_hash_excludes_attempt_identity() -> None:
    first = SimpleNamespace(
        model_dump=lambda *, mode: {
            "contract": {"genre_key": "x"},
            "input_payload": {"premise": "p"},
            "attempt_id": "one",
            "idempotency_key": "one",
        }
    )
    second = SimpleNamespace(
        model_dump=lambda *, mode: {
            "contract": {"genre_key": "x"},
            "input_payload": {"premise": "p"},
            "attempt_id": "two",
            "idempotency_key": "two",
        }
    )
    assert stable_conception_input_hash(first) == stable_conception_input_hash(second)


def test_checkpoint_hash_mismatch_is_not_silent() -> None:
    checkpoint = {
        "schema": SCHEMA_VERSION,
        "stage": "final_candidate_review",
        "state": {"winner": {"premise": "p"}},
        "state_sha256": "wrong",
        "input_hash": "old",
    }
    run = SimpleNamespace(
        id=uuid4(),
        metadata_json={"conception_checkpoint": checkpoint},
        workflow_type="conception_initial",
        project_id=None,
    )

    class Session:
        async def scalar(self, _query):
            return run

    with pytest.raises(ValueError, match="input_hash"):
        asyncio.run(load_conception_checkpoint(Session(), workflow_run_id=run.id, input_hash="new"))


def test_worker_restores_checkpoint_and_persists_new_run_before_pointer(
    tmp_path, monkeypatch, caplog
):
    from contextlib import asynccontextmanager
    import copy

    from bestseller.services import conception, conception_checkpoints, story_architect, workflows
    from bestseller.services.concept_contract import ConceptContractError
    from bestseller.web import server

    manager = server.WebTaskManager(persist_path=tmp_path / "tasks.json")
    payloads = []

    def capture(payload):
        payloads.append(payload)
        return {
            "task_id": "resume-test",
            "task_type": "autowrite",
            "status": "queued",
            "project_slug": payload["slug"],
            "title": payload["title"],
        }

    monkeypatch.setattr(manager, "create_autowrite_task", capture)
    manager.create_quickstart_task({"genre_key": "apocalypse-supply", "chapter_count": 12})
    payload = payloads[0]
    old_id, new_id = uuid4(), uuid4()
    payload["_conception_workflow_run_id"] = str(old_id)
    manager._tasks["resume-test"] = server.WebTaskState(
        task_id="resume-test",
        task_type="autowrite",
        status="queued",
        created_at="",
        updated_at="",
        payload=copy.deepcopy(payload),
    )
    checkpoint = {"schema": "test", "state": {"title": "retained"}}
    events, sessions = [], []

    class Session:
        async def scalar(self, _):
            return None

        async def execute(self, _):
            return SimpleNamespace()

        async def commit(self):
            events.append(("commit", self))
            if self is sessions[0]:
                assert events[-2][0] == "save"
                assert manager._tasks["resume-test"].payload["_conception_workflow_run_id"] == str(
                    old_id
                )

        async def rollback(self):
            pass

    @asynccontextmanager
    async def scope(_settings):
        session = Session()
        sessions.append(session)
        yield session

    async def load(session, *, workflow_run_id, input_hash, expected_project_id, expected_intent_hash):
        assert workflow_run_id == old_id and input_hash
        assert expected_project_id is None and expected_intent_hash
        events.append(("load", session))
        return checkpoint

    async def create(session, **kwargs):
        assert kwargs["metadata"]["source_conception_workflow_run_id"] == str(old_id)
        assert ":resume:" in kwargs["metadata"]["attempt_id"]
        events.append(("create", session))
        return SimpleNamespace(id=new_id)

    async def save(session, *, workflow_run_id, checkpoint):
        assert workflow_run_id == new_id
        events.append(("save", session))

    async def forbidden(*args, **kwargs):
        raise AssertionError("architect must be skipped")

    async def pipeline(session, settings, **kwargs):
        assert kwargs["resume_checkpoint"] is checkpoint
        assert kwargs["checkpoint_input_hash"]
        assert manager._tasks["resume-test"].payload["_conception_workflow_run_id"] == str(new_id)
        events.append(("pipeline", session))
        await kwargs["checkpoint_callback"](checkpoint)
        assert events[-1] == ("save", sessions[1])
        assert sessions[1] is not session
        raise ConceptContractError(["TEST_COMMON_REVIEW_BLOCK"])

    monkeypatch.setattr(server, "session_scope", scope)
    monkeypatch.setattr(
        server, "load_settings", lambda: SimpleNamespace(quality=SimpleNamespace(draft_mode=False))
    )
    monkeypatch.setattr(conception_checkpoints, "load_conception_checkpoint", load)
    monkeypatch.setattr(conception_checkpoints, "save_conception_checkpoint", save)
    monkeypatch.setattr(workflows, "create_workflow_run", create)
    monkeypatch.setattr(story_architect, "architect_story_facets", forbidden)
    monkeypatch.setattr(conception, "run_conception_pipeline", pipeline)
    monkeypatch.setattr(server, "run_autowrite_pipeline", forbidden)
    manager._run_autowrite_worker("resume-test", payload)
    assert [event[0] for event in events[:6]] == [
        "load",
        "create",
        "save",
        "commit",
        "pipeline",
        "save",
    ]
    assert "Story Architect failed" not in caplog.text
    task = manager.get_task("resume-test")
    assert task["status"] == "failed"
    assert "TEST_COMMON_REVIEW_BLOCK" in task["error"]
