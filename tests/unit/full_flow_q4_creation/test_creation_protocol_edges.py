"""Additional native-row controls; no reviewed/approved positive fixture.

The checkpoint is an unreviewed proposal from the native checkpoint producer,
not a grade or production-admission receipt. No model/network call is made.
"""
import copy

import pytest

from bestseller.infra.db import models as m
from bestseller.services import conception, conception_checkpoints as checkpoints, projects
from bestseller.services import conception_project_handoff as handoff
from bestseller.services.budget import bind_execution_scope
from bestseller.services.source_bound_planning_content import new_reader_first_binding, reader_first_binding
from bestseller.web import server
from bestseller.worker import self_heal

from .test_creation_project_handoff import RAW, STAGING_KEY, attempt_for, prepare, project_request, quickstart, row_counts

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.mark.parametrize("raw", [RAW, None])
async def test_staging_freezes_the_exact_q4_original_input_algorithm(creation_db, monkeypatch, raw):
    case = creation_db
    _, payload = quickstart(case, monkeypatch, raw=raw)
    attempt = attempt_for(payload)
    ctx = conception._build_genre_context(
        payload["_genre_key"], int(payload["target_chapters"]), genre=payload["genre"],
        sub_genre=payload.get("sub_genre") or "", genre_intent_contract=attempt.contract.genre_intent,
        creation_intent_contract=attempt.contract.model_dump(mode="json"),
    )
    expected = conception._bind_reader_first_conception(ctx, user_hints=payload["user_hints"])
    first = await prepare(case, payload)
    assert reader_first_binding(first.project) == expected
    assert first.project.metadata_json["reader_first_original_input"] == ctx["reader_first_original_input"]
    # Actual native caller now passes that exact raw string before facets or
    # generated proposals can change the context's other fields.
    next_ctx = {**ctx, "story_facets": {"setting": "unreviewed generated proposal"}}
    assert conception._bind_reader_first_conception(next_ctx, user_hints=payload["user_hints"],
        original_user_input=first.project.metadata_json["reader_first_original_input"]) == expected


async def test_direct_native_creation_defaults_reader_first_from_actual_input(creation_db, monkeypatch):
    case = creation_db
    _, payload = quickstart(case, monkeypatch)
    async with case.sessions() as session:
        project = await projects.create_project(session, project_request(payload, metadata={"premise": RAW}), case.settings)
        await session.commit()
        assert reader_first_binding(project) == new_reader_first_binding(
            RAW, protocol="reader-first-planning.v2",
        )
        assert project.metadata_json["reader_first_original_input"] == RAW
        assert STAGING_KEY not in project.metadata_json
        assert server._payload_from_project_model(project)["_run_conception"] is False


async def test_existing_unmarked_database_source_is_not_migrated_by_resume_reads(creation_db, monkeypatch):
    case = creation_db
    _, payload = quickstart(case, monkeypatch)
    # Ordinary pre-protocol persisted source, with no fabricated grade/current.
    async with case.sessions() as session:
        legacy = m.ProjectModel(slug=payload["slug"], title=payload["title"], genre=payload["genre"],
            language="zh-CN", target_word_count=payload["target_words"], target_chapters=payload["target_chapters"],
            project_type="linear", metadata_json={"premise": RAW})
        session.add(legacy)
        await session.commit()
        original = copy.deepcopy(legacy.metadata_json)
        assert reader_first_binding(legacy) is None
        assert handoff.read_conception_project_handoff(legacy) is None
        assert server._payload_from_project_model(legacy)["_run_conception"] is False
        await handoff.staging_resume_payload(session, legacy)
        assert legacy.metadata_json == original


async def test_native_proposal_checkpoint_and_replay_round_trip_on_real_staging_workflow(creation_db, monkeypatch):
    case = creation_db
    _, payload = quickstart(case, monkeypatch)
    first = await prepare(case, payload)
    stage = handoff.read_conception_project_handoff(first.project)
    raw = first.project.metadata_json["reader_first_original_input"]
    ctx = {"reader_first_planning": reader_first_binding(first.project), "reader_first_original_input": raw}
    state = {
        "premise": "Unreviewed ordinary proposal", "title": "Unreviewed proposal", "synopsis": "Still unreviewed",
        "writing_profile": {}, "story_spine": {}, "concept_contract": {}, "ctx": ctx, "tags": [],
        "commercial_brief": {}, "world_model_payload": {}, "conception_log": [], "llm_run_ids": [],
        "chapter_count": int(payload["target_chapters"]), "genre_key": payload["_genre_key"],
    }
    checkpoint = checkpoints.build_conception_checkpoint(state, input_hash=stage["input_hash"])
    record = checkpoints.build_replay_context(ctx, input_hash=stage["input_hash"],
        genre_key=payload["_genre_key"], chapter_count=int(payload["target_chapters"]))
    with bind_execution_scope(stage["execution_scope_id"]):
        async with case.sessions() as session:
            await handoff.save_staging_conception_state(session, project_id=first.project.id,
                workflow_run_id=first.workflow.id, checkpoint=checkpoint, record=record)
            await session.commit()
    await server._close_creation_workflow_after_task_exit_async(case.settings,
        attempt_id=str(payload["_conception_attempt_id"]), status="failed", error_message="offline, before any model send")
    async with case.sessions() as session:
        loaded, replayed = await handoff.load_staging_conception_state(session, project_id=first.project.id,
            workflow_run_id=first.workflow.id, input_hash=stage["input_hash"])
        assert loaded == checkpoint and replayed == record
        project = await session.get(m.ProjectModel, first.project.id)
        before = copy.deepcopy(project.metadata_json)
        assert await self_heal._pending_native_conception_project(session, project) is True
        assert project.status == "planning"
        assert project.metadata_json == before
    assert await row_counts(case) == (1, 1, 1, 0, 0, 0)
