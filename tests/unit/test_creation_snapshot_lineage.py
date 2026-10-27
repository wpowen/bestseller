"""Behavioral regression tests for the creation-boundary snapshot handoff."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from inspect import unwrap
from types import SimpleNamespace
from uuid import uuid4

import pytest
from support.recovery_entry_seams import offline_review_resume, offline_web_owner  # noqa: F401

from bestseller.web import server


class _Session:
    def __init__(self) -> None:
        self.commits = 0

    async def scalar(self, _query: object) -> None:
        return None

    def add(self, _value: object) -> None:
        return None

    async def flush(self) -> None:
        return None

    async def commit(self) -> None:
        self.commits += 1


class _SessionScope:
    def __init__(self, session: _Session) -> None:
        self.session = session

    async def __aenter__(self) -> _Session:
        return self.session

    async def __aexit__(self, *_args: object) -> None:
        return None


def _payload(manager: server.WebTaskManager, monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    captured: dict[str, object] = {}

    def capture(_self: server.WebTaskManager, value: dict[str, object]) -> dict[str, object]:
        captured["payload"] = value
        return {"task_id": "captured", "status": "queued"}

    monkeypatch.setattr(server.WebTaskManager, "create_autowrite_task", capture)
    manager.create_quickstart_task(
        {"genre_key": "apocalypse-supply", "chapter_count": 3, "concept_seed": "快照回归"}
    )
    return dict(captured["payload"])


def _conception_result(*, hook_spec: dict[str, object] | None = None) -> object:
    return SimpleNamespace(
        premise="经验证的构思 premise", title="快照回归书", writing_profile={},
        commercial_brief={}, conception_log=[], hook_spec=hook_spec or {}, synopsis="简介", tags=[],
        story_spine={}, concept_methodology={},
        concept_contract=None,
        story_appeal={}, hook_candidates=[], motif_amplification={}, title_tournament={},
        default_family={}, degradation_events=[], degraded=False,
    )


def _install_harness(
    manager: server.WebTaskManager,
    payload: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
    *,
    pipeline: object,
    snapshot_contents: dict[str, object] | None = None,
) -> tuple[_Session, list[str], list[str]]:
    task_id = "snapshot-lineage-task"
    with manager._lock:
        manager._tasks[task_id] = server.WebTaskState(
            task_id=task_id, task_type="autowrite", status="queued",
            created_at="2026-01-01T00:00:00+00:00", updated_at="2026-01-01T00:00:00+00:00",
            project_slug=str(payload["slug"]), title=str(payload["title"]), current_stage="queued",
        )
    session = _Session()
    events: list[str] = []
    snapshots: list[str] = []
    saved_artifacts: dict[str, object] = {}
    project = SimpleNamespace(id=uuid4(), slug=str(payload["slug"]), metadata_json={})
    monkeypatch.setattr(server, "session_scope", lambda _settings: _SessionScope(session))
    monkeypatch.setattr(server, "load_settings", lambda: SimpleNamespace(quality=SimpleNamespace(draft_mode=False)))
    async def get_project(*_args: object) -> None:
        return None

    monkeypatch.setattr(server, "get_project_by_slug", get_project)

    async def create_project(*_args: object, **_kwargs: object) -> object:
        events.append("create_project")
        return project

    monkeypatch.setattr("bestseller.services.projects.create_project", create_project)

    async def create_workflow_run(*_args: object, **_kwargs: object) -> object:
        return SimpleNamespace(id=uuid4(), metadata_json={})

    monkeypatch.setattr("bestseller.services.workflows.create_workflow_run", create_workflow_run)

    async def snapshot(*_args: object, **kwargs: object) -> object:
        snapshots.append(str(kwargs["artifact_type"]))
        if snapshot_contents is not None:
            snapshot_contents[str(kwargs["artifact_type"])] = kwargs["content"]
        artifact = SimpleNamespace(id=uuid4(), status=str(kwargs["status"]), content=kwargs["content"])
        saved_artifacts[str(kwargs["artifact_type"])] = artifact
        return artifact

    monkeypatch.setattr("bestseller.services.conception_snapshots.create_conception_snapshot_artifact", snapshot)

    # This harness owns persistence ordering; the real transactional quality,
    # paid-receipt and canonical checks run in test_reviewed_conception_selection.
    # Keep this collaborator explicit rather than pretending _Session is SQL.
    async def select_reviewed(session_arg: object, _settings: object, **kwargs: object) -> dict:
        from bestseller.services.conception_snapshots import snapshot_hash
        assert session_arg is session
        assert snapshots == ["creation_intent", "conception_snapshot"]
        current = saved_artifacts["conception_snapshot"]
        assert kwargs["project_id"] == project.id
        assert kwargs["snapshot_id"] == current.id
        assert kwargs["expected_hash"] == snapshot_hash(current.content)
        assert session.commits == 1  # selection must precede the handoff commit
        events.append("select_reviewed_conception")
        return {"snapshot_id":str(current.id), "fixture_collaborator":True}

    monkeypatch.setattr("bestseller.services.reviewed_conception_selection.select_reviewed_conception_snapshot", select_reviewed)


    @contextmanager
    def bind(*_args: object, **_kwargs: object):
        yield

    monkeypatch.setattr("bestseller.services.llm.bind_conception_model", bind)
    monkeypatch.setattr(server, "run_autowrite_pipeline", pipeline)
    async def no_continuation(*_args: object, **_kwargs: object) -> None:
        return None
    monkeypatch.setattr("bestseller.services.autonomous_continuation.record_window_continuation", no_continuation)
    return session, events, snapshots


def _async_conception(
    monkeypatch: pytest.MonkeyPatch,
    *,
    hook_spec: dict[str, object] | None = None,
) -> None:
    async def conception(*_args: object, **_kwargs: object) -> object:
        return _conception_result(hook_spec=hook_spec)

    monkeypatch.setattr("bestseller.services.conception.run_conception_pipeline", conception)

    async def architect(*_args: object, **_kwargs: object) -> object:
        return SimpleNamespace(
            tone="高压", narrative_drive="生存", trope_tags=(), setting="城市",
            generation_source="test", model_dump=lambda mode="json": {"mode": mode},
        )

    monkeypatch.setattr("bestseller.services.story_architect.architect_story_facets", architect)


def test_initial_pipeline_sees_committed_snapshots_before_planning(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manager = server.WebTaskManager(persist_path=tmp_path / "tasks.json")
    payload = _payload(manager, monkeypatch)
    payload["_run_conception"] = True
    seen: list[tuple[int, list[str]]] = []

    async def pipeline(**_kwargs: object) -> object:
        seen.append((session.commits, list(snapshots)))
        return SimpleNamespace(project_id=uuid4(), model_dump=lambda mode="json": {"mode": mode})

    session, _events, snapshots = _install_harness(manager, payload, monkeypatch, pipeline=pipeline)
    _async_conception(monkeypatch)
    manager._run_autowrite_worker("snapshot-lineage-task", payload)
    assert seen == [(2, ["creation_intent", "conception_snapshot"])]
    assert manager.get_task("snapshot-lineage-task")["status"] == "completed"


def test_pipeline_failure_keeps_committed_creation_snapshots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manager = server.WebTaskManager(persist_path=tmp_path / "tasks.json")
    payload = _payload(manager, monkeypatch)
    payload["_run_conception"] = True
    observed: list[tuple[int, list[str]]] = []

    async def pipeline(**_kwargs: object) -> object:
        observed.append((session.commits, list(snapshots)))
        raise RuntimeError("planning failed")

    session, _events, snapshots = _install_harness(manager, payload, monkeypatch, pipeline=pipeline)
    _async_conception(monkeypatch)
    manager._run_autowrite_worker("snapshot-lineage-task", payload)
    assert observed == [(2, ["creation_intent", "conception_snapshot"])]
    assert manager.get_task("snapshot-lineage-task")["status"] == "failed"


def test_resume_with_existing_intent_does_not_rebuild_initial_snapshots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    manager = server.WebTaskManager(persist_path=tmp_path / "tasks.json")
    payload = _payload(manager, monkeypatch)
    payload["_run_conception"] = False

    async def pipeline(**_kwargs: object) -> object:
        return SimpleNamespace(project_id=uuid4(), model_dump=lambda mode="json": {"mode": mode})

    session, events, snapshots = _install_harness(manager, payload, monkeypatch, pipeline=pipeline)
    manager._run_autowrite_worker("snapshot-lineage-task", payload)
    assert snapshots == []
    assert events == []
    assert session.commits == 0
    assert manager.get_task("snapshot-lineage-task")["status"] == "completed"


def test_generated_conception_hook_survives_snapshot_without_explicit_user_hook(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = server.WebTaskManager(persist_path=tmp_path / "tasks.json")
    payload = _payload(manager, monkeypatch)
    payload["_run_conception"] = True
    contents: dict[str, object] = {}

    async def pipeline(**_kwargs: object) -> object:
        return SimpleNamespace(project_id=uuid4(), model_dump=lambda mode="json": {"mode": mode})

    _session, _events, _snapshots = _install_harness(
        manager, payload, monkeypatch, pipeline=pipeline, snapshot_contents=contents
    )
    _async_conception(
        monkeypatch,
        hook_spec={"mechanism_key": "generated-hook", "one_liner": "主角必须当场作出选择"},
    )
    manager._run_autowrite_worker("snapshot-lineage-task", payload)
    assert contents["conception_snapshot"]["hook_spec"] == {
        "mechanism_key": "generated-hook",
        "one_liner": "主角必须当场作出选择",
    }


def test_explicit_user_hook_remains_snapshot_fallback_when_conception_hook_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = server.WebTaskManager(persist_path=tmp_path / "tasks.json")
    payload = _payload(manager, monkeypatch)
    payload["_run_conception"] = True
    payload["hook_spec"] = server._public_writing_preset_catalog_payload()[
        "hook_candidates"
    ]["apocalypse-supply"][0]["spec"]
    contents: dict[str, object] = {}

    async def pipeline(**_kwargs: object) -> object:
        return SimpleNamespace(project_id=uuid4(), model_dump=lambda mode="json": {"mode": mode})

    _install_harness(
        manager, payload, monkeypatch, pipeline=pipeline, snapshot_contents=contents
    )
    _async_conception(monkeypatch)
    manager._run_autowrite_worker("snapshot-lineage-task", payload)
    assert contents["conception_snapshot"]["hook_spec"] == payload["hook_spec"]


def test_standard_progressive_and_short_paths_reuse_precreated_projects() -> None:
    from bestseller.services import fanqie_short_pipeline, pipelines

    for module, function_name in (
        (pipelines, "run_autowrite_pipeline"),
        (pipelines, "run_progressive_autowrite_pipeline"),
        (fanqie_short_pipeline, "run_fanqie_short_pipeline"),
    ):
        names = unwrap(getattr(module, function_name)).__code__.co_names
        assert "get_project_by_slug" in names
        assert "create_project" in names
