"""Real SQLite provenance checks; no pipeline dispatch or provider calls."""
from copy import deepcopy
from datetime import UTC, datetime, timedelta
import json
from uuid import uuid4

import pytest
from sqlalchemy import event, inspect, text
from sqlalchemy.schema import CreateTable
from test_adopted_knowledge_lineage import db as db, make_accepted_debt

from bestseller.infra.db.models import (
    BookProductionControlModel, ChapterDraftVersionModel, ExecutionAttemptModel,
    ExecutionBudgetModel, ExecutionOperationModel, WorkflowRunModel,
)
from bestseller.services import pipelines
from bestseller.services.chapter_cancelled_review_recovery import validate_cancelled_review_policy_source

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.fixture
async def retained(db, monkeypatch):
    session, project, chapter, draft = db
    for model in (BookProductionControlModel, ExecutionBudgetModel,
                  ExecutionOperationModel, ExecutionAttemptModel, WorkflowRunModel):
        ddl = str(CreateTable(model.__table__).compile(dialect=session.bind.dialect))
        await session.execute(text(ddl.replace("::jsonb", "")))
    scope = "conception:" + str(uuid4())
    project.metadata_json = {"execution_scope_id": scope}
    project.title = "潮汐观测站"
    draft.content_md = "The surveyor closes the pressure valve.\nThe diver retains the sealed instrument."
    draft.promotion_state = "candidate"
    previous = WorkflowRunModel(project_id=project.id, scope_type="chapter", scope_id=chapter.id,
        workflow_type="chapter_pipeline", status="cancelled", current_step="review",
        error_message="operator pause; retained paid samples", metadata_json={"paid_history": ["unchanged"]})
    control = BookProductionControlModel(project_id=project.id, desired_state="run", command_serial=7)
    budget = ExecutionBudgetModel(scope_id=scope, status="active", max_calls=64,
        max_tokens=1000000, calls_used=13, tokens_charged=42000,
        deadline_at=datetime.now(UTC) - timedelta(days=1), metadata_json={"provider_authorization": {
            "mode": "completion_bound", "status": "active", "provider": "minimax",
            "authorization_ref": "original-human-goal", "objective": "Finish inspected repair",
            "models": ["openai/MiniMax-M3"], "endpoint_hosts": ["api.minimaxi.com"],
            "aggregate_max_calls": None, "aggregate_max_tokens": None, "aggregate_deadline_at": None}})
    session.add_all([previous, control, budget])
    await session.flush()
    body = pipelines.chapter_review_body_fingerprint(draft.content_md)
    cycle_id = pipelines._chapter_critic_cycle_id(project.id, chapter.id, body["digest"])
    cycle = {"schema_version": "chapter-critic-cycle.v1", "cycle_id": cycle_id,
        "source_draft_id": str(draft.id), "source_body_hash": body["digest"],
        "review_workflow_run_id": str(previous.id), "policy_source_hash": "1" * 64,
        "semantic_dependencies_sha256": "2" * 64, "state": "running",
        "attempts": [{"attempt": 1, "state": "running",
            "operation_namespace": f"chapter-critic:{cycle_id}:1"}]}
    chapter.metadata_json = {"chapter_critic_cycles": {cycle_id: cycle},
        "chapter_review_attempts_active": 9, "numeric_promotion_debt": None}
    await session.commit()

    async def forbidden(*args, **kwargs):
        raise AssertionError("guard attempted provider/review dispatch")

    from bestseller.services import llm
    monkeypatch.setattr(llm, "complete_text", forbidden)
    monkeypatch.setattr(pipelines, "run_chapter_pipeline", forbidden)
    return session, project, chapter, draft, previous, control, budget, scope


def change_cycle(chapter, **updates):
    metadata = deepcopy(chapter.metadata_json)
    key = next(iter(metadata["chapter_critic_cycles"]))
    metadata["chapter_critic_cycles"][key].update(updates)
    chapter.metadata_json = metadata


def columns(row):
    return deepcopy({field.key: getattr(row, field.key) for field in inspect(type(row)).column_attrs})


async def checked(retained, *, policy="3" * 64, scope=None):
    session, project, chapter, draft, previous, control, budget, original_scope = retained
    # SQLite expires server-onupdate timestamps independently of expire_on_commit.
    # Load fixture columns explicitly before installing the read-only SQL monitor.
    with session.no_autoflush:
        for row in (project, chapter, draft, previous, control, budget):
            if inspect(row).persistent and row not in session.dirty:
                await session.refresh(row)
    before = [columns(row) for row in (project, chapter, draft, previous, control, budget)]
    state = (set(session.new), set(session.dirty), set(session.deleted))
    writes = []

    def track(_conn, _cursor, statement, _parameters, _context, _many):
        if not statement.lstrip().upper().startswith("SELECT"):
            writes.append(statement.split()[0])

    event.listen(session.bind.sync_connection, "before_cursor_execute", track)
    try:
        proof = await validate_cancelled_review_policy_source(session, project=project, chapter=chapter,
            draft=draft, previous=previous, execution_scope_id=scope or original_scope, current_policy_hash=policy)
        assert not session.dirty and session.in_transaction()
        json.dumps(proof, allow_nan=False)
        return proof
    finally:
        event.remove(session.bind.sync_connection, "before_cursor_execute", track)
        assert not writes
        assert before == [columns(row) for row in (project, chapter, draft, previous, control, budget)]
        assert state == (set(session.new), set(session.dirty), set(session.deleted))


async def test_exact_cancelled_source_proves_without_mutation_or_extra_credit(retained):
    proof = await checked(retained)
    assert proof == await checked(retained)
    assert proof["previous_workflow_run_id"] == str(retained[4].id)
    assert proof["previous_status"] == retained[4].status == "cancelled"
    assert proof["control_command_serial"] == 7
    assert proof["budget_usage"] == {"calls_used": 13, "tokens_charged": 42000}
    assert proof["source_cycle"]["attempts"][0]["state"] == "running"
    assert all(proof[key] is False for key in ("grade_reuse_allowed", "adoption_allowed",
        "attempt_reset_allowed", "new_credit_allowed", "dispatch_allowed"))


@pytest.mark.parametrize("state", ["prepared", "running", "completed"])
async def test_all_real_durable_states_and_unknown_evidence_retained(retained, state):
    session, _project, chapter, draft, *_ = retained
    body = pipelines.chapter_review_body_fingerprint(draft.content_md)
    key = next(iter(chapter.metadata_json["chapter_critic_cycles"]))
    report, quality = str(uuid4()), str(uuid4())
    entry = {"attempt": 1, "state": state, "operation_namespace": f"chapter-critic:{key}:1"}
    if state == "completed":
        entry.update(draft_id=str(draft.id), body_fingerprint=body, review_report_id=report,
                     quality_score_id=quality, outcome="unknown", rewrite_task_id=None)
    change_cycle(chapter, state=state, attempts=[entry])
    await session.commit()
    proof = await checked(retained)
    assert proof["source_cycle"]["attempts"] == [entry]
    if state == "completed":
        assert proof["evidence_ids"][0]["review_report_id"] == report


@pytest.mark.parametrize("change", ["schema", "state", "same_policy", "missing_policy", "bad_policy",
    "wrong_draft", "wrong_body", "wrong_workflow", "earlier_workflow", "missing_cycles", "multiple_cycles",
    "noncanonical_cycle", "too_many_attempts", "bad_ordinal", "bad_namespace", "bad_attempt_state", "bad_result"])
async def test_invalid_cycle_or_false_previous_origin_rejected(retained, change):
    session, _project, chapter, _draft, previous, *_ = retained
    changes = {"schema": {"schema_version": "legacy"}, "state": {"state": "unknown"},
        "same_policy": {"policy_source_hash": "3" * 64}, "missing_policy": {"policy_source_hash": None},
        "bad_policy": {"policy_source_hash": "unknown"}, "wrong_draft": {"source_draft_id": str(uuid4())},
        "wrong_body": {"source_body_hash": "0" * 64}, "wrong_workflow": {"review_workflow_run_id": str(uuid4())},
        "too_many_attempts": {"attempts": [{}, {}, {}]}, "bad_ordinal": {"attempts": [{"attempt": True}]},
        "bad_namespace": {"attempts": [{"attempt": 1, "state": "running", "operation_namespace": "other"}]},
        "bad_attempt_state": {"attempts": [{"attempt": 1, "state": "unknown"}]},
        "bad_result": {"state": "completed", "attempts": []}}
    if change in changes:
        change_cycle(chapter, **changes[change])
    elif change == "earlier_workflow":
        # Same chapter/current body is insufficient without the real reviewer WF.
        previous.metadata_json = {"unrelated_earlier_workflow": True}
        change_cycle(chapter, review_workflow_run_id=str(uuid4()))
    else:
        metadata = deepcopy(chapter.metadata_json)
        cycles = metadata["chapter_critic_cycles"]
        key = next(iter(cycles))
        if change == "missing_cycles":
            metadata.pop("chapter_critic_cycles")
        elif change == "multiple_cycles":
            other = deepcopy(cycles[key])
            other["cycle_id"] = "4" * 64
            cycles[other["cycle_id"]] = other
        else:
            cycles["4" * 64] = cycles.pop(key)
            cycles["4" * 64]["cycle_id"] = "4" * 64
        chapter.metadata_json = metadata
    await session.commit()
    with pytest.raises(ValueError):
        await checked(retained)


@pytest.mark.parametrize("change", ["not_cancelled", "wrong_scope_type", "wrong_type", "wrong_chapter",
    "not_current", "second_current", "promoted", "dated_adoption", "pause", "stop", "absent_run", "implicit_run",
    "scope_changed", "scope_missing", "authorization_missing", "authorization_paused", "budget_unknown",
    "pending", "unknown", "reserved_attempt", "bad_numeric", "ambiguous_promoted"])
async def test_current_control_finance_and_adoption_boundaries(retained, change):
    session, project, chapter, draft, previous, control, budget, scope = retained
    if change == "not_cancelled": previous.status = "completed"
    elif change == "wrong_scope_type": previous.scope_type = "project"
    elif change == "wrong_type": previous.workflow_type = "other"
    elif change == "wrong_chapter": previous.scope_id = uuid4()
    elif change == "not_current": draft.is_current = False
    elif change == "promoted": draft.promotion_state = "promoted"
    elif change == "dated_adoption": draft.promoted_at = datetime.now(UTC)
    elif change in {"pause", "stop"}: control.desired_state = change
    elif change == "absent_run": await session.delete(control)
    elif change == "implicit_run": control.command_serial = 0
    elif change == "scope_changed": project.metadata_json = {"execution_scope_id": "project:" + str(uuid4())}
    elif change == "scope_missing": project.metadata_json = {}
    elif change == "authorization_missing": budget.metadata_json = {}
    elif change == "authorization_paused":
        metadata = deepcopy(budget.metadata_json)
        metadata["provider_authorization"]["status"] = "paused"
        budget.metadata_json = metadata
    elif change == "budget_unknown": budget.status = "unknown"
    elif change in {"pending", "unknown", "reserved_attempt"}:
        operation = ExecutionOperationModel(key="5" * 64, scope_id=scope, input_hash="6" * 64,
            status="completed" if change == "reserved_attempt" else change,
            owner="other", lease_until=datetime.now(UTC) + timedelta(minutes=5))
        session.add(operation)
        if change == "reserved_attempt":
            session.add(ExecutionAttemptModel(id="reserved", operation_key=operation.key, scope_id=scope,
                input_hash="6" * 64, status="reserved", model_name="model", reserved_tokens=100,
                created_at=datetime.now(UTC)))
    elif change == "bad_numeric":
        chapter.metadata_json = {**chapter.metadata_json, "numeric_promotion_debt": {}}
    else:
        for version in range(2, 4 if change == "ambiguous_promoted" else 3):
            session.add(ChapterDraftVersionModel(project_id=project.id, chapter_id=chapter.id,
                version_no=version, content_md="An older instrument record.",
                is_current=change == "second_current", promotion_state="promoted" if change == "ambiguous_promoted" else "candidate"))
    await session.commit()
    with pytest.raises(ValueError):
        await checked(retained)


async def test_valid_numeric_adoption_of_candidate_is_protected(retained):
    session, project, chapter, draft, *_ = retained
    cycle_meta = deepcopy(chapter.metadata_json)
    await make_accepted_debt((session, project, chapter, draft))
    chapter.metadata_json = {**cycle_meta, **chapter.metadata_json}
    await session.commit()
    with pytest.raises(ValueError, match="target_already_adopted"):
        await checked(retained)


async def test_dirty_source_rejected_without_autoflush(retained):
    retained[3].content_md += " Uncommitted change."
    with pytest.raises(ValueError, match="uncommitted_source"):
        await checked(retained)


async def test_different_scope_argument_rejected(retained):
    with pytest.raises(ValueError, match="project_scope"):
        await checked(retained, scope="conception:" + str(uuid4()))
