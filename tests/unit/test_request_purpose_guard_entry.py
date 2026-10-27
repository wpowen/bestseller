"""Actual SQLite guard/public entry; synthetic retained returns, no providers.

The literal pairs are the independently reviewed producer contracts, not a
runtime prefix exemption. No test patches purpose proof or the production guard.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import UTC, datetime, timedelta
import hashlib
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.schema import CreateTable
from test_adopted_knowledge_lineage import _jsonb  # noqa: F401

from bestseller.infra.db.models import (
    ChapterDraftVersionModel, ChapterModel, ExecutionAttemptModel, ExecutionBudgetModel,
    ExecutionOperationModel, LlmRunModel, ProjectModel, QualityScoreModel,
    ReviewReportModel, WorkflowRunModel,
)
from bestseller.services import commercial_judge_recovery as recovery
from bestseller.services import chapter_llm_quality_judge as judge
from bestseller.services import llm
from bestseller.services.budget import bind_execution_scope
from bestseller.settings import load_settings

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]
SCOPE = "test:request-purpose-entry"
MODELS = (ProjectModel, ChapterModel, ChapterDraftVersionModel, WorkflowRunModel,
          ReviewReportModel, QualityScoreModel, LlmRunModel, ExecutionBudgetModel,
          ExecutionOperationModel, ExecutionAttemptModel)
PAIRS = (('critic', 'blurb_coherence_judge'), ('critic', 'blurb_logic_axis_prosecutor'), ('critic', 'concept_tournament_finalist_judge'), ('critic', 'concept_tournament_finalist_seriality_judge'), ('critic', 'concept_tournament_judge'), ('critic', 'concept_tournament_premise_judge'), ('critic', 'concept_tournament_raw_idea_rank'), ('critic', 'concept_tournament_story_judge'), ('critic', 'conception_cast_reality_audit'), ('critic', 'conception_repair_guard'), ('critic', 'conception_review'), ('critic', 'planner_source_bound_expansion_evidence_repair'), ('critic', 'premise_appeal_judge'), ('editor', 'blurb_copywriter_candidate'), ('editor', 'blurb_copywriter_polish'), ('editor', 'canon_contradiction_repair'), ('editor', 'conception_final_packaging_seed_fidelity_repair'), ('editor', 'conception_finalize'), ('editor', 'conception_intent_repair'), ('editor', 'conception_logline_from_champion'), ('editor', 'conception_logline_regen'), ('editor', 'conception_structure_repair'), ('editor', 'title_tournament_candidates'), ('planner', 'chapter_outline_prefix_surgical_repair'), ('planner', 'concept_methodology'), ('planner', 'concept_tournament_candidate'), ('planner', 'concept_tournament_engine_kernel'), ('planner', 'concept_tournament_raw_idea_pool'), ('planner', 'concept_tournament_seriality'), ('planner', 'conception_character'), ('planner', 'conception_commercial_positioning'), ('planner', 'conception_concept_seed_final_premise_reconciliation'), ('planner', 'conception_market'), ('planner', 'conception_world'), ('planner', 'planner_book_spec'), ('planner', 'planner_cast_spec'), ('planner', 'planner_volume_plan'), ('planner', 'planner_world_spec'), ('planner', 'story_architect_v1'))


def sha(value):
    return hashlib.sha256(value.encode()).hexdigest()


def serialized(model):
    return json.loads(json.dumps({c.key: getattr(model, c.key)
        for c in inspect(type(model)).column_attrs}, default=str))


async def snapshot(rig):
    async with rig.factory() as reader:
        return {m.__tablename__: sorted(
            [serialized(r) for r in await reader.scalars(select(m))],
            key=lambda r: json.dumps(r, sort_keys=True)) for m in MODELS}


@pytest.fixture
async def rig(tmp_path, monkeypatch):
    path = tmp_path / "request_purpose_guard_test.sqlite3"
    assert "test" in path.name
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    async with engine.begin() as connection:
        for model in MODELS:
            ddl = str(CreateTable(model.__table__).compile(dialect=connection.dialect))
            await connection.exec_driver_sql(ddl.replace("::jsonb", ""))
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    async with factory() as setup:
        project = ProjectModel(slug="orbital-gardens", title="潮汐档案", genre="general-fiction",
            target_chapters=4, target_word_count=10000, metadata_json={"authored_unknown":"保留"})
        setup.add(project)
        await setup.flush()
        chapter = ChapterModel(project_id=project.id, chapter_number=2,
            chapter_goal="辨认潮汐变化", metadata_json={"authored_contract":"保留作者字段"})
        setup.add(chapter)
        await setup.flush()
        draft = ChapterDraftVersionModel(project_id=project.id, chapter_id=chapter.id,
            version_no=1, content_md="海员将潮汐表放在窗台上。\n\n她听见新的海浪。",
            is_current=True, promotion_state="candidate")
        owner = WorkflowRunModel(project_id=project.id, workflow_type="chapter_pipeline",
            scope_type="chapter", scope_id=chapter.id, status="running",
            metadata_json={"execution_scope_id":SCOPE, "protected":"old raw and counters"})
        budget = ExecutionBudgetModel(scope_id=SCOPE, max_calls=1000, max_tokens=1000000,
            calls_used=0, tokens_charged=0, deadline_at=datetime.now(UTC)+timedelta(days=1),
            status="active", metadata_json={})
        setup.add_all([draft, owner, budget])
        await setup.commit()
    calls = []
    async def forbidden(*args, **kwargs):
        calls.append("gateway")
        raise AssertionError("provider forbidden in request-purpose entry regression")
    monkeypatch.setattr(llm, "complete_text", forbidden)
    monkeypatch.setattr(judge, "complete_text", forbidden)
    from bestseller.infra.db import session as db_session
    @asynccontextmanager
    async def pool():
        async with factory() as session:
            yield session
    monkeypatch.setattr(db_session, "get_server_session", pool)
    settings = load_settings(env={})
    settings.llm.critic.model = "openai/offline-purpose-fixture"
    settings.llm.critic.model_override = None
    result = SimpleNamespace(factory=factory, engine=engine, project=project, chapter=chapter,
        draft=draft, owner=owner, budget=budget, settings=settings, calls=calls)
    try:
        yield result
        assert calls == []
    finally:
        await engine.dispose()


@pytest.fixture
def proposed_control_guard():
    """All cases exercise the integrated production guard, with no AST patching."""
    import inspect as pyinspect
    assert '"unsent_reclaim_receipt"' in pyinspect.getsource(
        recovery._assert_no_unbound_commercial_spend)
    return "integrated_control_guard"


async def retained(rig, role="planner", template="planner_world_spec", *, stopped=False,
                   with_control=False, malformed=None, unknown_actual=False):
    key, h = sha(role+":"+template), sha("full original input for "+template)
    aid, model = "actual-"+key[:24], "openai/original-unrelated-model"
    origin = {"schema":"paid-call-origin.v1", "status":"recorded", "project_id":None,
        "workflow_run_id":None, "step_run_id":None, "logical_role":role,
        "prompt_template":template, "prompt_version":None, "prompt_hash":h}
    meta = {"paid_call_origin":origin,
        "effective_input_receipt":{"schema":"effective-input.v1", "hash":h,"model":model},
        "execution_receipt":{"scope_id":SCOPE,"operation_key":key,"attempts":[{
            "attempt_id":aid,"status":"completed","model":model,"effective_input_hash":h,
            "reserved_tokens":100,"wire_body_hash":sha("physical wire "+template)}]}}
    paid = {"content":"Original independent book proposal / 潮汐档案", "model_name":model,
            "tool_calls":None,"raw_message":None,"metadata":meta}
    lease = datetime.now(UTC)+timedelta(hours=1)
    async with rig.factory() as setup:
        root = ExecutionOperationModel(key=key,scope_id=SCOPE,input_hash=h,
            status="stopped" if stopped else "completed",owner="new-owner",lease_until=lease,
            result_json=None if stopped else deepcopy(paid))
        setup.add(root)
        if not stopped:
            setup.add(ExecutionAttemptModel(id=aid,operation_key=key,scope_id=SCOPE,
                model_name=model,input_hash=h,status="unknown" if unknown_actual else "completed",
                reserved_tokens=100,actual_tokens=17,created_at=datetime.now(UTC)))
            setup.add(ExecutionOperationModel(key=sha("transport-result.v1\n"+SCOPE+"\n"+aid),
                scope_id=SCOPE,input_hash=h,status="transport_result",owner="transport-owner",
                lease_until=lease,result_json={"schema":"transport-result.v1",
                    "operation_key":key,"attempt_id":aid,"input_hash":h,"result":deepcopy(paid)}))
            setup.add(LlmRunModel(id=uuid4(),project_id=None,workflow_run_id=None,
                logical_role=role,provider="fixture",model_name=model,prompt_template=template,
                prompt_version=None,prompt_hash=h,metadata_json=deepcopy(meta)))
            budget = await setup.get(ExecutionBudgetModel,SCOPE)
            budget.calls_used += 1
            budget.tokens_charged += 17
        if with_control:
            values = dict(key=sha("unsent-reclaim.v1\n"+key+"\nold-owner"),scope_id=SCOPE,
                input_hash=h,status="unsent_reclaim_receipt",owner="old-owner",lease_until=lease,
                result_json={"operation_key":key,"old_owner":"old-owner",
                             "new_owner":"new-owner","credit_released":False})
            if malformed == "owner": values["owner"] = "forged"
            elif malformed == "key": values["key"] = sha("wrong domain")
            elif malformed == "input": values["input_hash"] = sha("foreign input")
            elif malformed == "credit": values["result_json"]["credit_released"] = True
            elif malformed == "schema": values["result_json"]["schema"] = "invented-schema"
            setup.add(ExecutionOperationModel(**values))
        await setup.commit()
    return key


async def guard(rig, session=None):
    if session is None:
        async with rig.factory() as reader:
            return await guard(rig, reader)
    return await recovery._assert_no_unbound_commercial_spend(session,
        project_id=rig.project.id,chapter_id=rig.chapter.id,scope=SCOPE,
        body_hash=sha(rig.draft.content_md))


@pytest.mark.parametrize("role,template", PAIRS)
async def test_exact_producer_null_origin_passes_actual_guard_without_credit(rig, role, template):
    await retained(rig, role, template)
    before = await snapshot(rig)
    await guard(rig)
    assert await snapshot(rig) == before
    assert before["llm_runs"][0]["project_id"] is None
    assert before["execution_operations"][0]["result_json"] is not None


async def test_all_thirty_nine_share_scope_without_owner_backfill(rig):
    for role, template in PAIRS:
        await retained(rig, role, template)
    before = await snapshot(rig)
    await guard(rig)
    assert await snapshot(rig) == before
    assert len(before["llm_runs"]) == len(PAIRS) == 39
    assert all(row["project_id"] is None for row in before["llm_runs"])
    assert before["execution_budgets"][0]["calls_used"] == 39


async def test_valid_control_of_completed_parent_is_skipped_without_side_effect(rig, proposed_control_guard):
    await retained(rig, with_control=True)
    before = await snapshot(rig)
    await guard(rig)
    assert await snapshot(rig) == before


@pytest.mark.parametrize("control", [False, True])
async def test_zeroactual_unknown_root_is_still_blocked_after_control_skip(rig, proposed_control_guard, control):
    await retained(rig, stopped=True, with_control=control)
    before = await snapshot(rig)
    with pytest.raises(ValueError,match="commercial_cohort_opaque_operation_requires_recovery"):
        await guard(rig)
    assert await snapshot(rig) == before


@pytest.mark.parametrize("malformed", ["owner","key","input","credit","schema"])
async def test_malformed_control_fails_before_any_candidate_or_credit_change(rig, proposed_control_guard, malformed):
    await retained(rig, with_control=True, malformed=malformed)
    before = await snapshot(rig)
    with pytest.raises(ValueError):
        await guard(rig)
    assert await snapshot(rig) == before


@pytest.mark.parametrize("template", ["planner_not_registered","chapter_commercial_quality_judge"])
async def test_reclaimed_parent_unknown_or_commercial_route_is_not_skipped(rig, proposed_control_guard, template):
    await retained(rig, template=template, with_control=True)
    before = await snapshot(rig)
    with pytest.raises(ValueError):
        await guard(rig)
    assert await snapshot(rig) == before


async def test_unknown_parent_actual_remains_blocked_with_valid_control(rig, proposed_control_guard):
    await retained(rig, with_control=True, unknown_actual=True)
    before = await snapshot(rig)
    with pytest.raises(ValueError):
        await guard(rig)
    assert await snapshot(rig) == before


@pytest.mark.parametrize("malformed", [None,"credit"])
async def test_public_three_sampler_stable_unknown_is_unavailable_with_zero_dispatch(rig, proposed_control_guard, malformed):
    await retained(rig, stopped=True, with_control=True, malformed=malformed)
    before = await snapshot(rig)
    async with rig.factory() as session:
        with bind_execution_scope(SCOPE):
            result = await judge.judge_chapter_commercial_quality_stable(session,rig.settings,
                chapter_number=2,content_md=rig.draft.content_md,samples=3,
                project_id=rig.project.id,chapter_id=rig.chapter.id,draft_id=rig.draft.id,
                workflow_run_id=rig.owner.id)
    assert result.infrastructure_failure is True
    raw = result.raw_excerpt or ""
    expected = "commercial_cohort_opaque_operation_requires_recovery" if malformed is None else "unsent_reclaim"
    assert expected in raw
    assert rig.calls == []
    assert await snapshot(rig) == before


async def test_readonly_guard_replay_concurrent_sessions_and_rollback_preserve_history(rig, proposed_control_guard):
    import asyncio
    await retained(rig, with_control=True)
    before = await snapshot(rig)
    await asyncio.gather(guard(rig),guard(rig))
    async with rig.factory() as session:
        await guard(rig,session)
        await session.rollback()
    await guard(rig)
    assert await snapshot(rig) == before


async def test_unrelated_reconciled_charged_root_keeps_its_own_closure_without_invalidating_control(
    rig, proposed_control_guard,
):
    """A control-only witness must not reclassify a different quarantined root."""
    await retained(rig, with_control=True)
    other = await retained(rig, role="critic", template="world_law_consistency")
    identifier = str(uuid4())
    request_hash = sha("immutable reconciliation control request")
    async with rig.factory() as setup:
        actual = await setup.scalar(select(ExecutionAttemptModel).where(
            ExecutionAttemptModel.operation_key == other))
        actual.status = "reconciled_charged"
        setup.add(ExecutionOperationModel(
            key=sha("reconciliation.v1\n"+SCOPE+"\n"+identifier), scope_id=SCOPE,
            input_hash=request_hash, status="reconciliation_receipt", owner="original-reconciler",
            lease_until=datetime.now(UTC)+timedelta(hours=1), result_json={
                "reconciliation_id":identifier,"request_hash":request_hash,
                "local_dispatcher_stopped":True,"remote_operation_closed":False,
                "receipt":{"scope_id":SCOPE,"reconciliation_id":identifier,
                    "attempt_id":actual.id,"operation_key":other,"decision":"quarantine_charged",
                    "credit_released":False,"automatic_resume":False,
                    "operation_replay_allowed":False,"remote_outcome":"unknown_quarantined"}}))
        await setup.commit()
    before = await snapshot(rig)
    await guard(rig)
    assert await snapshot(rig) == before
    assert any(a["status"] == "reconciled_charged" for a in before["execution_attempts"])
    assert before["execution_budgets"][0]["calls_used"] == 2
    assert before["execution_budgets"][0]["tokens_charged"] == 34


async def test_control_witness_cannot_exempt_unrelated_unknown_actual(rig, proposed_control_guard):
    await retained(rig, with_control=True)
    await retained(rig, role="critic", template="world_law_consistency", unknown_actual=True)
    before = await snapshot(rig)
    with pytest.raises(ValueError, match="commercial_cohort_opaque_operation_requires_recovery"):
        await guard(rig)
    assert await snapshot(rig) == before
    assert rig.calls == []
