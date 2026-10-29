"""Proposed two-phase API: real native projects/WFs, never forged approval.

These cases establish identity, replay and negative finalization boundaries.
They do not fabricate a reviewed conception or claim a complete native positive
production prefix. That positive completion remains Unknown until available.
"""

import copy
from importlib import import_module

import pytest
from sqlalchemy import func, select

from bestseller.domain.project import ProjectCreate
from bestseller.infra.db import models as m
from bestseller.services import projects
from bestseller.services.budget import ExecutionLedger, bind_execution_scope
from bestseller.services.concept_contract import ConceptContractError
from bestseller.services.conception_checkpoints import stable_conception_input_hash
from bestseller.services.creation_intent_contract import ConceptionAttemptInput, contract_from_payload
from bestseller.web import server

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]
STAGING_KEY = "conception_project_handoff"
PROTOCOL = "conception-project-handoff.v1"
RAW = "  姐妹共同守住旧屋，最后选择彼此。\n保留原输入空白。  "


def api():
    try:
        return import_module("bestseller.services.conception_project_handoff")
    except ModuleNotFoundError as error:
        if error.name != "bestseller.services.conception_project_handoff":
            raise
        pytest.fail("missing native conception_project_handoff API; no synthetic project identity allowed")


def quickstart(case, monkeypatch, *, stop=False, task_id="q4-native-creation", raw=RAW):
    manager = server.WebTaskManager(persist_path=case.root / f"{task_id}.json")
    captured = []
    with monkeypatch.context() as patch:
        patch.setattr(manager, "create_autowrite_task", lambda payload: captured.append(payload) or {})
        manager.create_quickstart_task({
            "genre_key": "apocalypse-supply", "chapter_count": 5,
            "concept_seed": raw, "stop_after_conception": stop,
        })
    payload = copy.deepcopy(captured[0])
    payload["_conception_attempt_id"] = f"{task_id}:initial"
    return manager, payload


def attempt_for(payload, *, attempt_id=None):
    identity = attempt_id or str(payload["_conception_attempt_id"])
    return ConceptionAttemptInput(
        contract=contract_from_payload(payload),
        input_payload={
            "premise": payload.get("premise"), "user_hints": payload.get("user_hints") or {},
            "genre": payload.get("genre"), "sub_genre": payload.get("sub_genre"),
            "target_chapters": payload.get("target_chapters"),
        }, attempt_id=identity, idempotency_key=identity,
    )


def project_request(payload, *, metadata=None):
    return ProjectCreate(
        slug=str(payload["slug"]), title=str(payload["title"]), genre=str(payload["genre"]),
        sub_genre=payload.get("sub_genre"), audience=payload.get("audience"),
        language=str(payload.get("language") or "zh-CN"),
        target_word_count=int(payload["target_words"]), target_chapters=int(payload["target_chapters"]),
        project_type=payload.get("project_type") or "linear", metadata=metadata or {},
        writing_profile=payload.get("writing_profile"),
    )


async def prepare(case, payload, *, task_id="q4-native-creation", attempt=None, source=None):
    with bind_execution_scope(f"conception:{task_id}"):
        async with case.sessions() as session:
            binding = await api().prepare_conception_project_handoff(
                session, case.settings, task_id=task_id, request_payload=copy.deepcopy(payload),
                attempt=attempt or attempt_for(payload), source_workflow_run_id=source,
            )
            await session.commit()
            return binding


async def row_counts(case):
    async with case.sessions() as session:
        return tuple([await session.scalar(select(func.count()).select_from(model)) for model in (
            m.ProjectModel, m.StyleGuideModel, m.WorkflowRunModel, m.PlanningArtifactVersionModel,
            m.LlmRunModel, m.BookPackagingContractModel,
        )])


async def test_preparation_uses_native_create_and_commits_real_project_workflow_before_send(creation_db, monkeypatch):
    case = creation_db
    _, payload = quickstart(case, monkeypatch)
    called = []
    native_create = projects.create_project

    async def observe_create(session, request, settings):
        project = await native_create(session, request, settings)
        called.append(project.id)
        return project

    monkeypatch.setattr(projects, "create_project", observe_create)
    binding = await prepare(case, payload)
    async with case.sessions() as session:
        project = await session.get(m.ProjectModel, binding.project.id)
        workflow = await session.get(m.WorkflowRunModel, binding.workflow.id)
        assert called == [project.id]
        assert project.target_chapters == int(payload["target_chapters"])
        assert project.target_word_count == int(payload["target_words"])
        assert workflow.project_id == project.id
        assert workflow.workflow_type == "conception_initial"
        assert (workflow.scope_type, workflow.scope_id) == ("creation", None)
        assert workflow.metadata_json["execution_scope_id"] == "conception:q4-native-creation"
        assert project.metadata_json["execution_scope_id"] == workflow.metadata_json["execution_scope_id"]
        stage = project.metadata_json[STAGING_KEY]
        assert stage["schema_version"] == PROTOCOL
        assert stage["state"] == "staging"
        assert stage["task_id"] == "q4-native-creation"
        assert stage["creation_intent_hash"] == attempt_for(payload).contract.contract_hash()
        assert stage["input_hash"] == stable_conception_input_hash(attempt_for(payload))
        assert stage["workflow_run_id"] == str(workflow.id)
        assert stage["request_payload"] == payload
        assert not project.metadata_json.get("conception_approved")
        assert not project.metadata_json.get("creation_canonical_snapshot_id")
        assert not project.metadata_json.get("active_packaging_contract_id")
    assert await row_counts(case) == (1, 1, 1, 0, 0, 0)


async def test_exact_reentry_reuses_native_row_and_workflow_without_reinitialization(creation_db, monkeypatch):
    case = creation_db
    _, payload = quickstart(case, monkeypatch)
    first = await prepare(case, payload)
    created_at = first.project.created_at
    second = await prepare(case, payload)
    assert second.project.id == first.project.id
    assert second.project.created_at == created_at
    assert second.workflow.id == first.workflow.id
    assert await row_counts(case) == (1, 1, 1, 0, 0, 0)


async def test_failed_attempt_resume_keeps_project_and_native_execution_budget(creation_db, monkeypatch):
    case = creation_db
    _, payload = quickstart(case, monkeypatch)
    scope = "conception:q4-native-creation"
    await ExecutionLedger(case.engine, scope, case.settings.budget).check()
    first = await prepare(case, payload)
    async with case.sessions() as session:
        workflow = await session.get(m.WorkflowRunModel, first.workflow.id)
        workflow.status, workflow.current_step, workflow.error_message = "failed", "failed", "before model send"
        await session.commit()
        budget = await session.get(m.ExecutionBudgetModel, scope)
        before = {column.key: copy.deepcopy(getattr(budget, column.key)) for column in m.ExecutionBudgetModel.__table__.columns
                  if column.key != "metadata"}
        before_meta = copy.deepcopy(budget.metadata_json)
    retry = attempt_for(payload, attempt_id="q4-native-creation:resume:second")
    second = await prepare(case, payload, attempt=retry, source=first.workflow.id)
    assert second.project.id == first.project.id
    async with case.sessions() as session:
        retained = await session.get(m.WorkflowRunModel, first.workflow.id)
        assert (retained.status, retained.error_message) == ("failed", "before model send")
        assert second.workflow.project_id == retained.project_id
        assert second.workflow.metadata_json["source_conception_workflow_run_id"] == str(retained.id)
        budget = await session.get(m.ExecutionBudgetModel, scope)
        assert {key: getattr(budget, key) for key in before} == before
        assert budget.metadata_json == before_meta
        assert list(await session.scalars(select(m.ExecutionBudgetModel.scope_id))) == [scope]
    assert (await row_counts(case))[:2] == (1, 1)


@pytest.mark.parametrize("conflict", ["task", "input", "scope"])
async def test_staging_identity_conflict_fails_without_rebinding(creation_db, monkeypatch, conflict):
    case = creation_db
    _, payload = quickstart(case, monkeypatch)
    first = await prepare(case, payload)
    request = copy.deepcopy(payload)
    task_id = "another-task" if conflict == "task" else "q4-native-creation"
    attempt = attempt_for(request)
    if conflict == "input":
        attempt = attempt.model_copy(update={"input_payload": {**attempt.input_payload, "premise": "a changed input"}})
    scope = "foreign-scope" if conflict == "scope" else f"conception:{task_id}"
    before = copy.deepcopy(first.project.metadata_json)
    with bind_execution_scope(scope):
        async with case.sessions() as session:
            with pytest.raises(ValueError):
                await api().prepare_conception_project_handoff(
                    session, case.settings, task_id=task_id, request_payload=request, attempt=attempt,
                    source_workflow_run_id=first.workflow.id,
                )
            await session.rollback()
    async with case.sessions() as session:
        project = await session.get(m.ProjectModel, first.project.id)
        assert project.metadata_json == before
    assert await row_counts(case) == (1, 1, 1, 0, 0, 0)


async def test_unmarked_native_project_is_not_reclassified_or_overwritten(creation_db, monkeypatch):
    case = creation_db
    _, payload = quickstart(case, monkeypatch)
    async with case.sessions() as session:
        project = await projects.create_project(session, project_request(payload, metadata={"legacy_note": "retain"}), case.settings)
        await session.commit()
        before = copy.deepcopy(project.metadata_json)
        original = (project.id, project.created_at, project.title)
    with pytest.raises(ValueError):
        await prepare(case, payload)
    async with case.sessions() as session:
        project = await session.get(m.ProjectModel, original[0])
        assert (project.id, project.created_at, project.title) == original
        assert project.metadata_json == before
        resume = server._payload_from_project_model(project)
        assert resume["_run_conception"] is False
        assert STAGING_KEY not in project.metadata_json
    assert await row_counts(case) == (1, 1, 0, 0, 0, 0)


async def test_caller_metadata_cannot_forge_a_native_staging_marker(creation_db, monkeypatch):
    case = creation_db
    _, payload = quickstart(case, monkeypatch)
    payload.setdefault("metadata", {})[STAGING_KEY] = {"schema_version": PROTOCOL, "state": "complete"}
    with pytest.raises(ValueError):
        await prepare(case, payload)
    assert await row_counts(case) == (0, 0, 0, 0, 0, 0)


@pytest.mark.parametrize("stop", [False, True])
async def test_completion_without_native_reviewed_conception_cannot_update_staging_or_grant_authority(
    creation_db, monkeypatch, stop,
):
    case = creation_db
    _, payload = quickstart(case, monkeypatch, stop=stop)
    first = await prepare(case, payload)
    before = copy.deepcopy(first.project.metadata_json)
    # An invalid source is deliberately NOT promoted or given a synthetic grade.
    invalid = project_request(payload, metadata={"concept_contract_version": "2", "concept_contract": {}})
    invalid = invalid.model_copy(update={"title": "must not become the materialized title"})
    with bind_execution_scope("conception:q4-native-creation"):
        async with case.sessions() as session:
            with pytest.raises((ValueError, ConceptContractError)):
                await api().finalize_conception_project_handoff(
                    session, case.settings, project_id=first.project.id, workflow_run_id=first.workflow.id,
                    project_payload=invalid, attempt=attempt_for(payload), conception_result=None,
                )
            await session.rollback()
    async with case.sessions() as session:
        project = await session.get(m.ProjectModel, first.project.id)
        assert project.title == first.project.title
        assert project.metadata_json == before
    assert await row_counts(case) == (1, 1, 1, 0, 0, 0)


async def test_database_resume_rebuild_keeps_staging_inputs_and_actual_workflow(creation_db, monkeypatch):
    case = creation_db
    _, payload = quickstart(case, monkeypatch)
    first = await prepare(case, payload)
    rebuilt = await server._rebuild_payload_from_db(case.settings, first.project.slug)
    assert rebuilt["_run_conception"] is True
    assert rebuilt["creation_intent_contract"] == payload["creation_intent_contract"]
    assert rebuilt["premise"] == payload["premise"]
    assert rebuilt["user_hints"] == payload["user_hints"]
    assert rebuilt["_conception_workflow_run_id"] == str(first.workflow.id)
    assert server._merge_resume_payload_with_project_truth(payload, rebuilt)["_run_conception"] is True


async def test_actual_staging_workflow_blocks_ordinary_worker_continuation_admission(creation_db, monkeypatch):
    """Known cross-owner seam: Main must wire the actual worker guard too."""
    from bestseller.worker import self_heal

    case = creation_db
    _, payload = quickstart(case, monkeypatch)
    first = await prepare(case, payload)
    async with case.sessions() as session:
        assert await self_heal._has_active_continuation_pipeline_run(session, first.project.id) is True
