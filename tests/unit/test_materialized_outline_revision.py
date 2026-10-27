"""Main full-outline preproof: real SQLite ledger/native returns; no provider."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
import json
import socket

import pytest
from sqlalchemy import event, inspect, select, update
from sqlalchemy.schema import CreateTable
from test_adopted_knowledge_lineage import _jsonb, _uuid_as_text  # noqa: F401
from test_planning_draft_admission import grade_payload
from test_planning_draft_admission import harness as paid_harness  # noqa: F401

from bestseller.infra.db import models as db
from bestseller.services import llm
from bestseller.services import materialized_outline_admission_read as source_read
from bestseller.services import materialized_outline_revision as service
from bestseller.services import planning_draft_admission as admission
from bestseller.services.budget import ExecutionBudgetError, ExecutionLedger, bind_execution_scope
from bestseller.services.conception_snapshots import snapshot_hash
from bestseller.services.prompt_source_policy import content_hash

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.fixture
async def case(paid_harness, monkeypatch, tmp_path, request):  # noqa: F811 - shared fixture
    import bestseller.settings as settings_module

    h = paid_harness
    h.admission_schema = getattr(request, "param", admission.SCHEMA)
    h.settings.artifact_store.local_dir = str(tmp_path / "artifacts")
    monkeypatch.setattr(
        settings_module, "llm_runtime_profile_path", lambda _: tmp_path / "absent.json"
    )

    def forbidden(*args, **kwargs):
        raise AssertionError("No PG or provider network in full-outline preproof tests")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    engine = h.session_factory.kw["bind"]
    assert engine.dialect.name == "sqlite" and "test" in engine.url.database
    async with engine.begin() as connection:
        for model in (
            db.ProjectModel,
            db.PlanningArtifactVersionModel,
            db.WorkflowRunModel,
            db.ChapterModel,
            db.ChapterDraftVersionModel,
            db.CanonFactModel,
            db.TimelineEventModel,
            db.BookProductionControlModel,
        ):
            ddl = str(CreateTable(model.__table__).compile(dialect=engine.dialect))
            await connection.exec_driver_sql(ddl.replace("::jsonb", ""))
    candidate = deepcopy(h.kwargs()["candidate_payload"])
    base_payload = deepcopy(candidate)
    base_payload["chapters"][0]["goal"] = "The prior unchanged harbour source"
    async with h.session_factory() as session:
        h.project.target_word_count = 2000
        h.project.target_chapters = 1
        session.add(h.project)
        canonical = db.PlanningArtifactVersionModel(
            project_id=h.project.id,
            artifact_type="conception_snapshot",
            version_no=1,
            status="canonical",
            schema_version="1.0",
            content={"author_requirement": "Keep the original viewpoint"},
        )
        volume = db.PlanningArtifactVersionModel(
            project_id=h.project.id,
            artifact_type="volume_plan",
            version_no=1,
            status="approved",
            schema_version="1.0",
            content=[{"volume_number": 1, "chapter_count_target": 1, "word_count_target": "2000"}],
        )
        base = db.PlanningArtifactVersionModel(
            project_id=h.project.id,
            artifact_type="chapter_outline_batch",
            version_no=1,
            status="approved",
            schema_version="1.0",
            content=base_payload,
        )
        artifact = db.PlanningArtifactVersionModel(
            project_id=h.project.id,
            artifact_type="chapter_outline_batch",
            version_no=2,
            status="draft",
            schema_version="1.0",
            content=candidate,
        )
        actual_sources = {}
        for kind, source in h.sources.items():
            row = db.PlanningArtifactVersionModel(
                id=source.id,
                project_id=h.project.id,
                artifact_type=kind.value,
                version_no=1,
                status="approved",
                schema_version="1.0",
                content=deepcopy(source.content),
            )
            actual_sources[kind.value] = row
        actual_sources["volume_plan"] = volume
        chapter = db.ChapterModel(
            project_id=h.project.id,
            chapter_number=1,
            chapter_goal="The old projection is retained until normal materialization",
            metadata_json={},
        )
        session.add_all(
            [
                canonical,
                volume,
                base,
                artifact,
                chapter,
                *[s for k, s in actual_sources.items() if k != "volume_plan"],
            ]
        )
        await session.flush()
        h.project.metadata_json = {
            **h.project.metadata_json,
            "execution_scope_id": f"project:{h.project.id}",
            "creation_canonical_snapshot_id": str(canonical.id),
            "creation_canonical_snapshot_hash": snapshot_hash(canonical.content),
        }
        h.workflow.id = source_read.admission_workflow_id(h.project.id, artifact.id)
        artifact.source_run_id = h.workflow.id
        workflow = db.WorkflowRunModel(
            id=h.workflow.id,
            project_id=h.project.id,
            workflow_type=source_read.WORKFLOW_TYPE,
            scope_type="planning_artifact",
            scope_id=artifact.id,
            status="running",
            current_step="assess_current_source",
            metadata_json={"execution_scope_id": f"project:{h.project.id}"},
        )
        session.add(workflow)
        session.add(
            db.BookProductionControlModel(
                project_id=h.project.id, desired_state="run", command_serial=1, detail_json={}
            )
        )
        await session.commit()

    async def native_return(request, role):
        h.sent.append(request)
        calls = [
            {
                "id": "native-admission",
                "type": "function",
                "function": {
                    "name": "submit_planning_admission",
                    "arguments": json.dumps(grade_payload(candidate), ensure_ascii=False),
                },
            }
        ]
        raw_message = {"role": "assistant", "content": "", "tool_calls": calls}
        return "", 200, 150, "tool_calls", calls, raw_message

    monkeypatch.setattr(llm, "_call_litellm", native_return)
    h.scope = f"project:{h.project.id}"
    async with h.session_factory() as actual:
        with bind_execution_scope(h.scope):
            receipt = await admission.assess_planning_draft_admission(
                h.wrap(actual),
                h.settings,
                **h.kwargs(),
                commercial_report=h.commercial,
                admission_schema=h.admission_schema,
            )
        await actual.commit()

    async def selected(*args, **kwargs):
        return actual_sources

    monkeypatch.setattr(source_read, "load_selected_foundation_artifacts", selected)
    state = {
        "schema": source_read.SCHEMA,
        "execution_scope_id": h.scope,
        "source_artifact_id": str(artifact.id),
        "source_content_sha256": content_hash(artifact.content),
        "candidate_hash": content_hash(candidate),
        "canonical_id": str(canonical.id),
        "canonical_hash": snapshot_hash(canonical.content),
        "foundation": {
            kind: {"id": str(row.id), "content_hash": content_hash(row.content)}
            for kind, row in actual_sources.items()
        },
        "source_admission": receipt,
        "protocol": {
            "status": "validated",
            "candidate_hash": receipt["candidate_hash"],
            "graded_input_hash": receipt["graded_input_hash"],
            "attempts": deepcopy(receipt["protocol_attempts"]),
        },
        "full_outline_revision": {
            "schema": service.REVISION_SCHEMA,
            "phase": service.READY,
            "base_artifact_id": str(base.id),
            "base_source_content_sha256": content_hash(base.content),
            "base_candidate_hash": content_hash(base_payload),
        },
    }
    async with h.session_factory() as session:
        await session.execute(
            update(db.WorkflowRunModel)
            .where(db.WorkflowRunModel.id == workflow.id)
            .values(current_step=service.READY, metadata_json=state)
        )
        await session.commit()
    h.engine, h.artifact, h.base, h.chapter, h.canonical = (
        engine,
        artifact,
        base,
        chapter,
        canonical,
    )
    h.actual_sources, h.state, h.receipt = actual_sources, state, receipt
    return h


async def validate(c, artifact=None, scope=None):
    async with c.session_factory() as session:
        with bind_execution_scope(scope or c.scope):
            return await service.validate_full_outline_revision_for_materialization(
                session, c.settings, c.project, artifact or c.artifact
            )


async def mutate(c, model, identity, **values):
    async with c.session_factory() as session:
        await session.execute(update(model).where(model.id == identity).values(**values))
        await session.commit()


async def test_real_paid_native_receipt_is_verified_under_same_running_owner_without_writes(case):
    c = case
    count = len(c.sent)
    writes = []

    def capture(conn, cursor, statement, params, context, executemany):
        if statement.lstrip().upper().startswith(("UPDATE", "INSERT", "DELETE")):
            writes.append(statement)

    event.listen(c.engine.sync_engine, "before_cursor_execute", capture)
    try:
        expected = {
            "schema": service.REVISION_SCHEMA,
            "workflow_run_id": str(c.workflow.id),
            "source_artifact_id": str(c.artifact.id),
            "source_content_sha256": content_hash(c.artifact.content),
            "candidate_hash": content_hash(c.artifact.content),
            "chapter_numbers": [1],
            "source_admission": c.receipt,
        }
        assert await validate(c) == await validate(c) == expected
        async with c.session_factory() as session:
            owner = await session.get(db.WorkflowRunModel, c.workflow.id)
            assert owner.status == "running" and owner.current_step == service.READY
            source = await session.get(db.PlanningArtifactVersionModel, c.artifact.id)
            assert source.status == "draft" and source.source_run_id == c.workflow.id
        assert not writes and len(c.sent) == count == 1
    finally:
        event.remove(c.engine.sync_engine, "before_cursor_execute", capture)


@pytest.mark.parametrize(
    "mutation",
    [
        "owner_completed",
        "owner_type",
        "artifact_source",
        "phase",
        "candidate",
        "base",
        "volume",
        "canonical",
        "false_grade",
        "journal",
        "prefix",
        "physical",
    ],
)
async def test_preproof_rejects_stale_or_unowned_evidence(case, mutation):
    c = case
    if mutation in {"owner_completed", "owner_type", "phase", "false_grade", "journal"}:
        state = deepcopy(c.state)
        changes = {}
        if mutation == "owner_completed":
            changes["status"] = "completed"
        elif mutation == "owner_type":
            changes["workflow_type"] = "approve_chapter_outline_prefix"
        elif mutation == "phase":
            state["full_outline_revision"]["phase"] = "assessment_pending"
        elif mutation == "false_grade":
            state["source_admission"]["draft_eligible"] = False
        else:
            state["protocol"]["attempts"] = []
        await mutate(c, db.WorkflowRunModel, c.workflow.id, **changes, metadata_json=state)
    elif mutation == "artifact_source":
        await mutate(c, db.PlanningArtifactVersionModel, c.artifact.id, source_run_id=c.base.id)
    elif mutation in {"base", "volume", "canonical"}:
        row = {"base": c.base, "volume": c.actual_sources["volume_plan"], "canonical": c.canonical}[
            mutation
        ]
        await mutate(c, db.PlanningArtifactVersionModel, row.id, content={"changed": mutation})
    elif mutation in {"candidate", "prefix"}:
        content = deepcopy(c.artifact.content)
        if mutation == "candidate":
            content["chapters"][0]["scenes"][0]["action"] = "A different actual action"
        else:
            content["_meta"] = {"sourcebound_readiness_receipt": {"forged": True}}
        await mutate(c, db.PlanningArtifactVersionModel, c.artifact.id, content=content)
    else:
        async with c.session_factory() as session:
            attempt = await session.scalar(select(db.ExecutionAttemptModel))
            attempt.model_name = "openai/conflicting-model"
            await session.commit()
    sends = len(c.sent)
    with pytest.raises(ValueError):
        await validate(c)
    assert len(c.sent) == sends


async def test_same_candidate_with_only_metadata_change_is_not_a_revision(case):
    c = case
    await mutate(
        c,
        db.PlanningArtifactVersionModel,
        c.base.id,
        content={**deepcopy(c.artifact.content), "_meta": {"unrelated": "older"}},
    )
    state = deepcopy(c.state)
    base_content = {**deepcopy(c.artifact.content), "_meta": {"unrelated": "older"}}
    state["full_outline_revision"].update(
        base_source_content_sha256=content_hash(base_content),
        base_candidate_hash=content_hash(c.artifact.content),
    )
    await mutate(c, db.WorkflowRunModel, c.workflow.id, metadata_json=state)
    with pytest.raises(ValueError, match="candidate_not_changed"):
        await validate(c)


@pytest.mark.parametrize("kind", ["promoted", "ambiguous_debt", "extra_chapter"])
async def test_revision_cannot_replace_adopted_ambiguous_or_partial_current_scope(case, kind):
    c = case
    async with c.session_factory() as session:
        if kind == "promoted":
            session.add(
                db.ChapterDraftVersionModel(
                    project_id=c.project.id,
                    chapter_id=c.chapter.id,
                    version_no=1,
                    promotion_state="promoted",
                    content_md="This adopted harbour chapter must remain intact.",
                )
            )
        elif kind == "ambiguous_debt":
            await session.execute(
                update(db.ChapterModel)
                .where(db.ChapterModel.id == c.chapter.id)
                .values(metadata_json={"numeric_promotion_debt": {}})
            )
        else:
            session.add(
                db.ChapterModel(
                    project_id=c.project.id,
                    chapter_number=2,
                    chapter_goal="Another current chapter omitted by the proposed scope",
                    metadata_json={},
                )
            )
        await session.commit()
    sends = len(c.sent)
    with pytest.raises(ValueError):
        await validate(c)
    assert len(c.sent) == sends


async def test_dirty_caller_is_not_flushed_or_refreshed(case):
    c = case
    async with c.session_factory() as session:
        project = await session.get(db.ProjectModel, c.project.id)
        title = project.title
        project.title = "Uncommitted caller title"
        with bind_execution_scope(c.scope):
            proof = await service.validate_full_outline_revision_for_materialization(
                session, c.settings, project, c.artifact
            )
        assert proof == {
            "schema": service.REVISION_SCHEMA,
            "workflow_run_id": str(c.workflow.id),
            "source_artifact_id": str(c.artifact.id),
            "source_content_sha256": content_hash(c.artifact.content),
            "candidate_hash": content_hash(c.artifact.content),
            "chapter_numbers": [1],
            "source_admission": c.receipt,
        }
        assert project.title == "Uncommitted caller title" and project in session.dirty
        with session.no_autoflush:
            assert (
                await session.scalar(
                    select(db.ProjectModel.title).where(db.ProjectModel.id == c.project.id)
                )
                == title
            )


async def test_envelope_is_unchanged_after_later_normal_gate_spending(case):
    c = case
    before = await validate(c)
    completion = c.receipt["protocol_attempts"][0]["completion"]
    scope = completion["metadata"]["execution_receipt"]["scope_id"]
    ledger = ExecutionLedger(c.engine, scope, c.settings.budget)
    claim = await ledger.claim("separate-normal-gate-fixture", "separate-input")
    attempt = await ledger.reserve(claim, c.settings.llm.critic.model, "separate-input", 200)
    await ledger.settle(attempt, "completed", actual_tokens=30)
    await ledger.finish(claim, {"content": "separate settled fixture, not an admission grade"})
    assert await validate(c) == before
    assert set(before) == {
        "schema",
        "workflow_run_id",
        "source_artifact_id",
        "source_content_sha256",
        "candidate_hash",
        "chapter_numbers",
        "source_admission",
    }


async def test_v4_schema_label_cannot_relabel_an_old_native_paid_input(case):
    c = case
    state = deepcopy(c.state)
    state["source_admission"]["schema"] = "planning-draft-admission.v4"
    await mutate(c, db.WorkflowRunModel, c.workflow.id, metadata_json=state)
    with pytest.raises(ValueError):
        await validate(c)


@pytest.mark.parametrize("case", ["planning-draft-admission.v4"], indirect=True)
async def test_actual_v4_native_paid_proof_uses_the_shared_contract_input(case):
    c = case
    envelope = await validate(c)
    assert envelope["source_admission"] == c.receipt
    assert c.receipt["schema"] == "planning-draft-admission.v4"
    assert c.sent[0].prompt_version == "planning-draft-admission.v4"
    enum = c.sent[0].tools[0]["function"]["parameters"]["properties"]["hard_violations"]["items"][
        "properties"
    ]["source_refs"]["items"]["enum"]
    assert any(ref.startswith("/candidate_payload/chapters/") for ref in enum)
    async with c.session_factory() as session:
        base = await admission._input(
            session,
            project=c.project,
            workflow_run_id=c.workflow.id,
            **{k: v for k, v in c.kwargs().items() if k not in {"project", "workflow_run_id"}},
            commercial_report=c.commercial,
            check_initial_context=True,
            settings=c.settings,
        )
        adapted = admission._input_for_contract(base, c.receipt["schema"])
        assert content_hash(base) != c.receipt["graded_input_hash"]
        assert content_hash(adapted) == c.receipt["graded_input_hash"]
    assert len(c.sent) == 1


@pytest.mark.parametrize(
    "mutation",
    [
        "project_paused",
        "project_cancelled",
        "project_archived",
        "scope_changed",
        "control_pause",
        "control_cancel",
        "ledger_unknown",
        "ledger_cancelled",
        "ledger_exhausted",
        "deadline",
        "authorization_paused",
        "project_terminal",
    ],
)
async def test_every_call_rechecks_fresh_control_scope_and_financial_admission(case, mutation):
    c = case
    await validate(c)
    if mutation.startswith("project_") and mutation != "project_terminal":
        await mutate(c, db.ProjectModel, c.project.id, status=mutation.removeprefix("project_"))
    elif mutation in {"scope_changed", "project_terminal"}:
        metadata = deepcopy(c.project.metadata_json)
        if mutation == "scope_changed":
            metadata["execution_scope_id"] = "project:another-original-scope"
        else:
            metadata["execution_terminal"] = {"terminal": True, "code": "execution_result_unknown"}
        await mutate(c, db.ProjectModel, c.project.id, metadata_json=metadata)
    else:
        async with c.session_factory() as session:
            if mutation.startswith("control_"):
                control = await session.get(db.BookProductionControlModel, c.project.id)
                control.desired_state = mutation.removeprefix("control_")
            else:
                budget = await session.get(db.ExecutionBudgetModel, c.scope)
                if mutation.startswith("ledger_"):
                    budget.status = mutation.removeprefix("ledger_")
                elif mutation == "deadline":
                    budget.deadline_at = datetime.now(UTC) - timedelta(hours=1)
                else:
                    budget.metadata_json = {
                        "provider_authorization": {
                            "mode": "completion_bound",
                            "status": "paused",
                            "authorization_ref": "fixture",
                            "models": [c.settings.llm.critic.model],
                            "endpoint_hosts": ["api.minimaxi.com"],
                            "objective": "an unrelated finite completion",
                        }
                    }
            await session.commit()
    expected_error = (
        ExecutionBudgetError
        if mutation.startswith("ledger_") or mutation in {"deadline", "authorization_paused"}
        else ValueError
    )
    with pytest.raises(expected_error):
        await validate(c)
    assert len(c.sent) == 1


async def test_ready_owner_cannot_move_with_project_and_ambient_to_an_open_replacement_budget(case):
    c = case
    await validate(c)
    replacement = "project:replacement-ready-scope"
    async with c.session_factory() as session:
        project = await session.get(db.ProjectModel, c.project.id)
        project.metadata_json = {**project.metadata_json, "execution_scope_id": replacement}
        session.add(
            db.ExecutionBudgetModel(
                scope_id=replacement,
                max_calls=100,
                calls_used=7,
                max_tokens=1000000,
                tokens_charged=8910,
                status="active",
                deadline_at=datetime.now(UTC) + timedelta(hours=1),
                metadata_json={"existing_evidence": {"unknown": None}},
            )
        )
        await session.commit()

    async def saved_rows():
        async with c.session_factory() as session:
            owner = await session.get(db.WorkflowRunModel, c.workflow.id)
            return {
                "owner": {
                    field.key: deepcopy(getattr(owner, field.key))
                    for field in inspect(db.WorkflowRunModel).column_attrs
                },
                "budgets": [
                    dict(row)
                    for row in (
                        await session.execute(
                            select(db.ExecutionBudgetModel.__table__).order_by(
                                db.ExecutionBudgetModel.scope_id
                            )
                        )
                    ).mappings()
                ],
            }

    before = deepcopy(await saved_rows())
    with pytest.raises(ValueError, match="full_outline_revision:standard_owner_schema"):
        await validate(c, scope=replacement)
    assert len(c.sent) == 1 and await saved_rows() == before
    async with c.session_factory() as session:
        owner = await session.get(db.WorkflowRunModel, c.workflow.id)
        assert owner.metadata_json["execution_scope_id"] == c.scope
        assert owner.status == "running" and owner.current_step == service.READY


@pytest.mark.parametrize("saved_scope", [None, "", "project:foreign-saved-owner"])
async def test_ready_owner_requires_explicit_original_scope_without_backfill(case, saved_scope):
    c = case
    state = deepcopy(c.state)
    if saved_scope is None:
        state.pop("execution_scope_id")
    else:
        state["execution_scope_id"] = saved_scope
    await mutate(c, db.WorkflowRunModel, c.workflow.id, metadata_json=state)
    with pytest.raises(ValueError, match="full_outline_revision:standard_owner_schema"):
        await validate(c)
    async with c.session_factory() as session:
        owner = await session.get(db.WorkflowRunModel, c.workflow.id)
        assert owner.metadata_json == state
    assert len(c.sent) == 1


async def test_completion_authorization_uses_existing_ledger_policy_without_new_envelope_fields(
    case,
):
    c = case
    before = await validate(c)
    async with c.session_factory() as session:
        budget = await session.get(db.ExecutionBudgetModel, c.scope)
        budget.status = "exhausted"
        budget.deadline_at = datetime.now(UTC) - timedelta(days=1)
        budget.metadata_json = {
            "provider_authorization": {
                "mode": "completion_bound",
                "status": "active",
                "authorization_ref": "fixture",
                "models": [c.settings.llm.critic.model],
                "endpoint_hosts": ["api.minimaxi.com"],
                "objective": "an unrelated completion objective",
            }
        }
        await session.commit()
    assert await validate(c) == before
    with pytest.raises(ValueError, match="original_scope_required"):
        await validate(c, scope="project:foreign-caller")
    assert len(c.sent) == 1
