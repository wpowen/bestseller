"""RED: native stop-only approval must remain resumable on the same project.

SQLite protocol/component evidence, not a complete paid/model-quality positive.
Only architect/model IO supplies an ordinary pending proposal. Contract building,
Web WF completion, staging finalization, snapshot storage and explicit approval
are native. No approved/passed/canonical value or paid receipt is seeded by hand.
Main executes these cases; the authoring worker does not run tests or database IO.
"""

import asyncio
from copy import deepcopy
from types import SimpleNamespace
from uuid import UUID

import pytest
from sqlalchemy import select

from bestseller.infra.db import models as m
from bestseller.services import conception, conception_project_handoff as handoff, story_architect
from bestseller.services.budget import ExecutionLedger
from bestseller.services.concept_contract import build_concept_contract, validate_concept_contract
from bestseller.services.conception_approval_bridge import approve_pending_conception, build_conception_options
from bestseller.services.conception_snapshots import snapshot_hash
from bestseller.services.source_bound_planning_content import reader_first_binding
from bestseller.web import server

from .test_creation_project_handoff import STAGING_KEY, quickstart
from .test_quickstart_native_binding import install_task

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


async def durable_state(case):
    """Observe actual rows and budgets, without inferring any paid authority."""
    async with case.sessions() as session:
        projects = list(await session.scalars(select(m.ProjectModel)))
        styles = list(await session.scalars(select(m.StyleGuideModel)))
        workflows = list(await session.scalars(select(m.WorkflowRunModel)))
        artifacts = list(await session.scalars(select(m.PlanningArtifactVersionModel)))
        budgets = list(await session.scalars(select(m.ExecutionBudgetModel)))
        runs = list(await session.scalars(select(m.LlmRunModel)))
        return {
            "projects": {str(row.id): (row.slug, row.created_at, row.title, row.lock_version,
                deepcopy(row.metadata_json)) for row in projects},
            "styles": {str(row.project_id): {
                column.key: deepcopy(getattr(row, column.key))
                for column in m.StyleGuideModel.__table__.columns
            } for row in styles},
            "workflows": {str(row.id): (str(row.project_id), row.status, row.created_at,
                deepcopy(row.metadata_json)) for row in workflows},
            "artifacts": {str(row.id): (str(row.project_id), row.artifact_type, row.status,
                str(row.source_run_id), deepcopy(row.content)) for row in artifacts},
            "budgets": {row.scope_id: (row.max_calls, row.max_tokens, row.calls_used, row.tokens_charged,
                row.deadline_at, row.status, deepcopy(row.metadata_json)) for row in budgets},
            "llm_ids": [str(row.id) for row in runs],
        }


@pytest.fixture
async def native_stop_approval(creation_db, monkeypatch):
    case = creation_db
    case.settings.pipeline.enable_market_validation = False
    task_id = "q4-native-stop-approval"
    manager, payload = quickstart(case, monkeypatch, stop=True, task_id=task_id)
    install_task(manager, payload, task_id)
    scope = f"conception:{task_id}"
    # The native ceiling is not a provider/spending authorization.
    await ExecutionLedger(case.engine, scope, case.settings.budget).check()
    before_budget = (await durable_state(case))["budgets"]

    async def unavailable_architect(*args, **kwargs):
        raise RuntimeError("offline advisory architect unavailable")

    async def pending_proposal(session, settings, **kwargs):
        project = await session.get(m.ProjectModel, kwargs["project_id"])
        assert project is not None
        profile = deepcopy(project.metadata_json["writing_profile"])
        assert reader_first_binding(profile) == reader_first_binding(project)
        # Use the native contract producer. Keep its quality evidence explicitly
        # awaiting review, with no judge scores, raw verdict or invented LLM run.
        winner = {
            "concept": "姐姐放弃卖屋的便利，与妹妹共同修复旧屋和彼此的关系。",
            "mechanism": "姐妹每次处理旧屋的一件旧物，都必须面对一段未说清的往事。",
            "protagonist_identity": "姐姐林秋", "protagonist_private_desire": "与妹妹重新建立信任",
            "core_abnormality": "妹妹坚持保留姐姐准备丢弃的旧物",
            "opening_crisis": "姐妹必须在买家到来前决定旧屋去留",
            "opponent_system": "姐妹对旧屋和往事的不同理解",
            "decision_proof": "姐姐放弃卖屋定金，邀请妹妹共同修屋",
            "emotional_promise": "姐妹在共同选择中化解误解",
            "hook_question": "姐妹能否听懂彼此真正想留下的东西",
            "repeatable_story_unit": "面对旧物、表达旧怨、作出共同选择",
            "endgame_direction": "姐妹选择彼此并共同修复旧屋",
            "reconciliation_lineage": {"source": "ordinary pending proposal; no model evaluation"},
        }
        spine = {"who": winner["protagonist_identity"], "wants": winner["protagonist_private_desire"],
            "why_now": winner["opening_crisis"], "against": winner["opponent_system"],
            "question": winner["hook_question"]}
        contract = build_concept_contract(winner=winner, story_spine=spine,
            target_chapters=project.target_chapters, genre=project.genre, sub_genre=project.sub_genre or "")
        assert validate_concept_contract(contract, target_chapters=project.target_chapters) == []
        assert contract["quality_evidence"]["review_status"] == "requires_review"
        return conception.ConceptionResult(
            writing_profile=profile, premise=winner["concept"], title="留在旧屋里的姐妹",
            conception_log=[], llm_run_ids=[], synopsis="姐妹共同整理旧屋，在旧物与往事之间重新选择彼此。",
            tags=[], concept_contract=contract, story_spine=contract["story_spine"],
        )

    monkeypatch.setattr(story_architect, "architect_story_facets", unavailable_architect)
    monkeypatch.setattr(conception, "run_conception_pipeline", pending_proposal)
    await asyncio.to_thread(manager._run_with_slot, task_id, manager._run_autowrite_worker, payload)
    task = manager._tasks[task_id]
    assert task.status == "completed", task.error
    assert task.result["mode"] == "conception_only"
    project_id = UUID(task.result["project_id"])

    async with case.sessions() as session:
        project = await session.get(m.ProjectModel, project_id)
        stage = deepcopy(project.metadata_json[STAGING_KEY])
        assert stage["state"] == "complete"
        snapshot_id = UUID(stage["conception_snapshot_id"])
        snapshot = await session.get(m.PlanningArtifactVersionModel, snapshot_id)
        pending_hash = snapshot_hash(snapshot.content)
        assert snapshot.status == "pending_user_approval"
        assert stage["snapshot_hash"] == pending_hash
        options = build_conception_options(project={"slug": project.slug,
            "lock_version": project.lock_version, "target_chapters": project.target_chapters},
            snapshot={"id": str(snapshot.id), "status": snapshot.status, "content": snapshot.content})
        receipt = await approve_pending_conception(session, project_id=project.id, snapshot_id=snapshot.id,
            expected_lock_version=project.lock_version, source_hash=pending_hash,
            option_id=options["canonical"]["option_id"], idempotency_key=f"{task_id}:native-approval",
            actor="isolated protocol test creator", reason="Explicitly select the existing pending snapshot")
        await session.commit()
        assert receipt["snapshot_id"] == str(snapshot.id)
    async with case.sessions() as session:
        project = await session.get(m.ProjectModel, project_id)
        snapshot = await session.get(m.PlanningArtifactVersionModel, snapshot_id)
        assert snapshot.status == "canonical"
        assert snapshot_hash(snapshot.content) != pending_hash
        assert project.metadata_json["creation_canonical_snapshot_hash"] == snapshot_hash(snapshot.content)
        assert project.metadata_json[STAGING_KEY] == stage, "approval must preserve the initial handoff anchor"
    current = await durable_state(case)
    assert current["budgets"] == before_budget
    assert current["llm_ids"] == []
    assert len(current["projects"]) == len(current["styles"]) == len(current["workflows"]) == 1
    return SimpleNamespace(case=case, manager=manager, payload=payload, task_id=task_id,
        project_id=project_id, snapshot_id=snapshot_id, stage=stage, pending_hash=pending_hash, scope=scope)


async def test_native_approved_stop_only_helper_and_db_resume_accept_canonical_without_writes(native_stop_approval):
    c = native_stop_approval
    before = await durable_state(c.case)
    async with c.case.sessions() as session:
        project = await session.get(m.ProjectModel, c.project_id)
        assert await handoff.staging_resume_payload(session, project) is None
    rebuilt = await server._rebuild_payload_from_db(c.case.settings, str(c.payload["slug"]))
    assert rebuilt["_run_conception"] is False
    assert rebuilt["planning_handoff"] is True
    assert rebuilt["approved_conception_snapshot_id"] == str(c.snapshot_id)
    assert await c.manager._task_execution_scope(c.task_id) == c.scope
    assert await durable_state(c.case) == before


async def test_native_web_reentry_after_explicit_approval_reaches_planning_preparation(native_stop_approval, monkeypatch):
    c = native_stop_approval
    before = await durable_state(c.case)
    entries = []

    async def no_regeneration(*args, **kwargs):
        pytest.fail("approved stop-only resume must not regenerate architect or conception")

    async def observe_preparation(session, *, project_id, requested_by):
        project = await session.get(m.ProjectModel, project_id)
        assert project_id == c.project_id
        assert project.metadata_json["creation_canonical_snapshot_id"] == str(c.snapshot_id)
        entries.append((project_id, requested_by))
        # Stop BEFORE admission or planning/model work. This case observes the
        # real Web caller's routing, and returns no fake admission receipt.
        raise RuntimeError("offline planning preparation boundary reached")

    monkeypatch.setattr(conception, "run_conception_pipeline", no_regeneration)
    monkeypatch.setattr(story_architect, "architect_story_facets", no_regeneration)
    monkeypatch.setattr("bestseller.services.project_style_invariant_boundary.prepare_creation_style_invariant_boundary",
        observe_preparation)
    install_task(c.manager, deepcopy(c.payload), c.task_id)
    await asyncio.to_thread(c.manager._run_with_slot, c.task_id, c.manager._run_autowrite_worker, deepcopy(c.payload))
    assert entries == [(c.project_id, "web-ui")]
    assert c.manager._tasks[c.task_id].status == "failed"
    assert "offline planning preparation boundary reached" in c.manager._tasks[c.task_id].error
    assert await durable_state(c.case) == before


@pytest.mark.parametrize("fault", ["changed_snapshot_body", "wrong_current_pointer", "changed_canonical_hash"])
async def test_native_approved_stop_only_resume_still_rejects_source_tampering(native_stop_approval, fault):
    c = native_stop_approval
    async with c.case.sessions() as session:
        project = await session.get(m.ProjectModel, c.project_id)
        snapshot = await session.get(m.PlanningArtifactVersionModel, c.snapshot_id)
        if fault == "changed_snapshot_body":
            snapshot.content = {**snapshot.content, "premise": "a different body without a native approval"}
        elif fault == "wrong_current_pointer":
            project.metadata_json = {**project.metadata_json,
                "creation_canonical_snapshot_id": c.stage["creation_intent_snapshot_id"]}
        else:
            project.metadata_json = {**project.metadata_json, "creation_canonical_snapshot_hash": "0" * 64}
        await session.commit()
    before = await durable_state(c.case)
    with pytest.raises(ValueError):
        await server._rebuild_payload_from_db(c.case.settings, str(c.payload["slug"]))
    assert await durable_state(c.case) == before
