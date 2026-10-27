"""Real Web worker boundary with synthetic retained replay context, zero sends.

The native replay loader/validator/save functions run unchanged. SQL/lease and
the downstream conception pipeline are explicit offline collaborators; these
fixtures establish entry wiring, not a production paid-result certificate.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
import copy
from types import SimpleNamespace
from uuid import uuid4

import pytest

from support.recovery_entry_seams import offline_review_resume, offline_web_owner  # noqa: F401
from bestseller.infra.db.models import WorkflowRunModel
from bestseller.services import conception, conception_checkpoints as checkpoints
from bestseller.services import llm, story_architect, workflows
from bestseller.services.concept_contract import ConceptContractError
from bestseller.services.creation_intent_contract import CreationIntentContract, ConceptionAttemptInput
from bestseller.web import server

pytestmark = pytest.mark.unit


@pytest.fixture
def boundary(tmp_path, monkeypatch):
    manager = server.WebTaskManager(persist_path=tmp_path / "tasks.json")
    captured = []
    monkeypatch.setattr(manager, "create_autowrite_task", lambda payload: captured.append(payload) or {"task_id": "synthetic-resume"})
    manager.create_quickstart_task({"genre_key": "realistic-life", "chapter_count": 4})
    payload = copy.deepcopy(captured[0])
    source_id, next_id = uuid4(), uuid4()
    payload["_conception_attempt_id"] = "synthetic-book:initial"
    payload["_conception_workflow_run_id"] = str(source_id)
    contract = CreationIntentContract.model_validate(payload["creation_intent_contract"])
    original = ConceptionAttemptInput(
        contract=contract, attempt_id=payload["_conception_attempt_id"],
        idempotency_key=payload["_conception_attempt_id"],
        input_payload={"premise": payload.get("premise"), "user_hints": payload.get("user_hints") or {},
                       "genre": payload.get("genre"), "sub_genre": payload.get("sub_genre"),
                       "target_chapters": payload.get("target_chapters")},
    )
    stable = checkpoints.stable_conception_input_hash(original)
    record = checkpoints.build_replay_context(
        {"genre": "现实", "synthetic_fixture": True,
         "original_user_intent": {"seed": "同一场婚宴，同一主人公"},
         "avoid_names": ["既有名字"], "market_competitors": [{"source": "retained-fixture"}]},
        input_hash=stable, genre_key=payload["_genre_key"], chapter_count=4,
    )
    source = WorkflowRunModel(
        id=source_id, project_id=None, workflow_type="conception_initial", status="failed",
        requested_by="web-ui", idempotency_key=payload["_conception_attempt_id"],
        metadata_json={"creation_intent_hash": contract.contract_hash(),
                       "conception_checkpoint_input_hash": stable,
                       "conception_replay_context": copy.deepcopy(record)},
    )
    state = SimpleNamespace(manager=manager, payload=payload, source=source, source_id=source_id,
                            next_id=next_id, record=record, stable=stable, events=[],
                            next_run=None, pipeline_options=None, provider_calls=[],
                            checkpoint=None)
    manager._tasks["synthetic-resume"] = server.WebTaskState(
        task_id="synthetic-resume", task_type="autowrite", status="queued",
        created_at="2026-10-25T00:00:00+00:00", updated_at="2026-10-25T00:00:00+00:00",
        project_slug=payload["slug"], payload=copy.deepcopy(payload),
    )

    class Session:
        async def scalar(self, statement):
            descriptions = getattr(statement, "column_descriptions", [])
            if descriptions and descriptions[0].get("entity") is WorkflowRunModel:
                identifiers = set(str(v) for v in statement.compile().params.values())
                if str(source_id) in identifiers:
                    state.events.append("read_source")
                    return source
                if str(next_id) in identifiers:
                    return state.next_run
                raise AssertionError("unexpected workflow identity")
            # The project does not exist: this is the pre-project boundary.
            return None

        async def execute(self, _statement):
            state.events.append("failure_workflow_projection")
            return SimpleNamespace(rowcount=1)

        async def flush(self):
            state.events.append("save_replay_context")

        async def commit(self):
            # New workflow/context must be durable before the task points to it.
            assert manager._tasks["synthetic-resume"].payload["_conception_workflow_run_id"] == str(source_id)
            state.events.append("commit")

        async def rollback(self):
            pass

    @asynccontextmanager
    async def session_scope(_settings):
        yield Session()

    settings = SimpleNamespace(quality=SimpleNamespace(draft_mode=False),
                               output=SimpleNamespace(base_dir=tmp_path / "output"))
    monkeypatch.setattr(server, "session_scope", session_scope)
    monkeypatch.setattr(server, "load_settings", lambda: settings)

    async def create(session, **kwargs):
        state.events.append("create_workflow")
        assert kwargs["project_id"] is None
        assert kwargs["metadata"]["source_conception_workflow_run_id"] == str(source_id)
        assert kwargs["metadata"]["conception_checkpoint_input_hash"] == stable
        state.next_run = WorkflowRunModel(
            id=next_id, project_id=None, workflow_type="conception_initial", status="running",
            requested_by="web-ui", metadata_json=copy.deepcopy(kwargs["metadata"]),
        )
        return state.next_run

    async def architect(*args, **kwargs):
        state.events.append("architect")
        return story_architect._fallback_facets(payload["_genre_key"], "zh-CN")

    async def pipeline(session, _settings, **kwargs):
        state.events.append("conception_pipeline")
        state.pipeline_options = kwargs
        assert manager._tasks["synthetic-resume"].payload["_conception_workflow_run_id"] == str(next_id)
        assert kwargs["checkpoint_input_hash"] == stable
        if kwargs.get("resume_replay_context") is not None:
            assert kwargs["story_facets"] is None
            restored = checkpoints.validate_replay_context(
                kwargs["resume_replay_context"], input_hash=stable,
                genre_key=payload["_genre_key"], chapter_count=4,
            )
            assert restored == record["context"]
            assert state.next_run.metadata_json["conception_replay_context"] == record
        # Stop at the real handoff. No synthetic candidate or grade is returned.
        raise ConceptContractError(["SYNTHETIC_PRE_TOURNAMENT_HANDOFF_REACHED"])

    async def forbidden(*args, **kwargs):
        state.provider_calls.append(True)
        raise AssertionError("provider/full writing pipeline must not run")

    monkeypatch.setattr(workflows, "create_workflow_run", create)
    monkeypatch.setattr(story_architect, "architect_story_facets", architect)
    monkeypatch.setattr(conception, "run_conception_pipeline", pipeline)
    monkeypatch.setattr(server, "run_autowrite_pipeline", forbidden)
    monkeypatch.setattr(llm, "complete_text", forbidden)
    monkeypatch.setattr(llm, "_call_litellm", forbidden)
    yield state
    assert state.provider_calls == []


def test_replay_without_final_checkpoint_skips_architect_and_carries_exact_context(boundary, caplog):
    state = boundary
    original_metadata = copy.deepcopy(state.source.metadata_json)
    state.manager._run_autowrite_worker("synthetic-resume", state.payload)
    assert "architect" not in state.events
    assert state.events.index("save_replay_context") < state.events.index("commit") < state.events.index("conception_pipeline")
    assert state.pipeline_options["resume_checkpoint"] is None
    assert state.pipeline_options["resume_replay_context"] == state.record
    assert state.source.metadata_json == original_metadata
    task = state.manager.get_task("synthetic-resume")
    assert task["created_at"] == "2026-10-25T00:00:00+00:00"
    assert task["payload"]["_conception_attempt_id"] == "synthetic-book:initial"
    assert task["status"] == "failed" and "SYNTHETIC_PRE_TOURNAMENT_HANDOFF_REACHED" in task["error"]
    assert "Story Architect failed" not in caplog.text


def test_absent_replay_and_absent_final_checkpoint_still_runs_architect(boundary):
    state = boundary
    state.source.metadata_json.pop("conception_replay_context")
    state.manager._run_autowrite_worker("synthetic-resume", state.payload)
    assert state.events.index("architect") < state.events.index("conception_pipeline")
    assert state.pipeline_options["resume_replay_context"] is None
    assert state.pipeline_options["story_facets"] is not None


@pytest.mark.parametrize("mutation", ["context_bytes", "input_hash", "foreign_workflow_type"])
def test_invalid_replay_blocks_before_new_workflow_architect_or_pipeline(boundary, mutation):
    state = boundary
    if mutation == "context_bytes":
        state.source.metadata_json["conception_replay_context"]["context"]["genre"] = "被篡改"
    elif mutation == "input_hash":
        state.source.metadata_json["conception_replay_context"]["input_hash"] = "wrong-input"
    else:
        state.source.workflow_type = "project_pipeline"
    state.manager._run_autowrite_worker("synthetic-resume", state.payload)
    assert "create_workflow" not in state.events
    assert "commit" not in state.events
    assert "architect" not in state.events
    assert "conception_pipeline" not in state.events
    task = state.manager.get_task("synthetic-resume")
    assert task["status"] == "failed"
    assert task["payload"]["_conception_workflow_run_id"] == str(state.source_id)


def test_stable_hash_and_phase_namespace_ignore_new_resume_workflow_attempt_identity(boundary):
    state = boundary
    contract = CreationIntentContract.model_validate(state.payload["creation_intent_contract"])
    inputs = {key: state.payload.get(key) for key in ("premise", "user_hints", "genre", "sub_genre", "target_chapters")}
    original = ConceptionAttemptInput(contract=contract, input_payload=inputs,
                                     attempt_id="book:initial", idempotency_key="book:initial")
    resumed = original.model_copy(update={"attempt_id": "task:resume:" + uuid4().hex,
                                          "idempotency_key": "different-workflow-key"})
    assert original.input_hash() != resumed.input_hash()
    first = checkpoints.stable_conception_input_hash(original)
    second = checkpoints.stable_conception_input_hash(resumed)
    assert first == second == state.stable
    from bestseller.services.budget import execution_operation_id
    assert execution_operation_id("conception", subject="initial", revision=first) == execution_operation_id("conception", subject="initial", revision=second)
