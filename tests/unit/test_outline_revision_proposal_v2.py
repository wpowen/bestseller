"""V2 real SQLite/physical/settlement tests; dispatch and literary validation are inert.

The actual shared native validator, source readers, request renderer, physical
files and _paid financial proof run. Only its literary/domain boundary returns
an unchanged copy: these lifecycle fixtures issue no grade or adoption proof.
"""

from copy import deepcopy
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import JSON, ForeignKeyConstraint, MetaData, select
from sqlalchemy.dialects.postgresql import JSONB
from test_foundation_selection import store  # noqa: F401 - fixture dependency
from test_materialized_outline_source_recovery import case as source_case  # noqa: F401

from bestseller.infra.db import models as db
from bestseller.infra.db.models import ExecutionAttemptModel, ExecutionOperationModel, LlmRunModel
from bestseller.services import foundation_selection, llm, model_catalog, planner
from bestseller.services import outline_revision_proposal_v2 as service
from bestseller.services import planning_surgical_budget as budget
from bestseller.services import planning_surgical_repair as surgical
from bestseller.services import retained_review_input_proof as proof
from bestseller.services import retained_review_input_proof as retained
from bestseller.services.budget import bind_execution_scope
from bestseller.services.conception_snapshots import snapshot_hash
from bestseller.services.prompt_source_policy import content_hash

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


async def record_completion(
    case,
    request,
    *,
    content,
    calls=None,
    run=None,
    finish_reason=None,
    input_tokens=None,
    output_tokens=None,
):
    """Seed a complete settled fixture return, never a reader/pass-proof stub."""
    catalog, _ = proof._fresh_catalog()
    role = llm._apply_conception_phase_settings(
        request, proof._fresh_role(case.settings, case.project, request, catalog)
    )
    effective = llm._effective_input_hash(request, role)
    scope = case.budget.scope_id
    if run is None:
        run = LlmRunModel(id=uuid4())
        operation_key, attempt_id = uuid4().hex, uuid4().hex
        operation = ExecutionOperationModel(key=operation_key)
        attempt = ExecutionAttemptModel(id=attempt_id, created_at=datetime.now(UTC))
        transport = ExecutionOperationModel(
            key=hashlib.sha256(f"transport-result.v1\n{scope}\n{attempt_id}".encode()).hexdigest()
        )
        case.session.add_all([run, operation, attempt, transport])
    else:
        operation_key = run.metadata_json["execution_receipt"]["operation_key"]
        operation = await case.session.get(ExecutionOperationModel, operation_key)
        attempt_id = run.metadata_json["execution_receipt"]["attempts"][0]["attempt_id"]
        attempt = await case.session.get(ExecutionAttemptModel, attempt_id)
        transport = await case.session.get(
            ExecutionOperationModel,
            hashlib.sha256(f"transport-result.v1\n{scope}\n{attempt_id}".encode()).hexdigest(),
        )
    execution = {
        "scope_id": scope,
        "operation_key": operation_key,
        "attempts": [
            {"attempt_id": attempt_id, "status": "completed", "effective_input_hash": effective}
        ],
    }
    effective_receipt = {
        "schema": "effective-input.v1",
        "hash": effective,
        "model": role.model,
        "messages_source": "system_user",
        "tools_included": bool(request.tools),
        "legacy_prompt_hash": llm._hash_prompt(request.system_prompt, request.user_prompt),
    }
    input_tokens = (13 if calls else 7) if input_tokens is None else input_tokens
    output_tokens = (17 if calls else 11) if output_tokens is None else output_tokens
    raw_message = {"role": "assistant", "content": content}
    if calls:
        raw_message["tool_calls"] = deepcopy(calls)
    paid = llm.LLMCompletionResult(
        llm_run_id=run.id,
        content=content,
        tool_calls=deepcopy(calls),
        raw_message=raw_message,
        provider=llm._provider_from_model(role.model),
        model_name=role.model,
        finish_reason=finish_reason or ("tool_calls" if calls else "stop"),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        metadata={
            "execution_receipt": execution,
            "effective_input_receipt": effective_receipt,
            "paid_call_origin": llm._paid_call_origin(request, effective),
        },
    )
    with llm.bind_project_prompt_audit(case.project.id, reason="SQLite composite custody fixture"):
        request_ref, response_ref, reason, replayable = llm._persist_prompt_payloads(
            settings=case.settings,
            request=request,
            llm_run_id=run.id,
            configured_model=role.model,
            effective_model=role.model,
            effective_role_settings=role,
            provider=paid.provider,
            effective_temperature=role.temperature,
            effective_max_tokens=request.max_tokens_override,
            effective_timeout_seconds=role.timeout_seconds,
            effective_stream=role.stream,
            effective_thinking_type=llm._effective_thinking_type(role),
            effective_reasoning_effort=role.reasoning_effort,
            content=content,
            tool_call_count=len(calls or []),
            tool_calls=paid.tool_calls,
            raw_message=raw_message,
            finish_reason=paid.finish_reason,
            fallback_used=False,
            fallback_source=None,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
    assert reason is None
    assert replayable or request.prompt_version == "3.4-window-complete-repair"
    for key, value in {
        "project_id": request.project_id,
        "workflow_run_id": request.workflow_run_id,
        "logical_role": request.logical_role,
        "prompt_template": request.prompt_template,
        "prompt_version": request.prompt_version,
        "provider": paid.provider,
        "model_name": role.model,
        "prompt_hash": effective,
        "finish_reason": paid.finish_reason,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "request_payload_ref": request_ref,
        "response_payload_ref": response_ref,
        "metadata_json": {
            "execution_receipt": execution,
            "effective_input_receipt": effective_receipt,
            "input_hash": request.metadata.get("input_hash"),
        },
    }.items():
        setattr(run, key, value)
    for item in (operation, transport):
        item.scope_id, item.input_hash = scope, effective
        item.owner, item.lease_until = "offline-fixture", case.budget.deadline_at
    operation.status, operation.result_json = "completed", paid.model_dump(mode="json")
    transport.status = "transport_result"
    transport.result_json = {
        "schema": "transport-result.v1",
        "attempt_id": attempt_id,
        "operation_key": operation_key,
        "input_hash": effective,
        "result": paid.model_dump(mode="json"),
    }
    attempt.scope_id, attempt.operation_key, attempt.input_hash = scope, operation_key, effective
    attempt.model_name, attempt.status = role.model, "completed"
    attempt.reserved_tokens = attempt.actual_tokens = input_tokens + output_tokens
    for kind, ref in (("request", request_ref), ("response", response_ref)):
        case.paths[f"{run.id}:{kind}"] = Path(case.settings.artifact_store.local_dir) / ref
    await case.session.flush()
    if getattr(case, "accounting", False):
        attempts = list(
            await case.session.scalars(
                select(db.ExecutionAttemptModel).where(db.ExecutionAttemptModel.scope_id == scope)
            )
        )
        current_budget = await case.session.get(db.ExecutionBudgetModel, scope)
        current_budget.calls_used = len(attempts)
        current_budget.tokens_charged = sum(a.actual_tokens for a in attempts)
        await case.session.flush()
    return paid, run


@pytest.fixture
async def proposal(source_case, store, tmp_path, monkeypatch):  # noqa: F811 - shared SQLite fixture
    c = source_case
    table = db.VolumeModel.__table__.to_metadata(MetaData())
    for column in table.c:
        if isinstance(column.type, JSONB):
            column.type = JSON()
        if column.server_default is not None and "::" in str(column.server_default.arg):
            column.server_default = None
    for constraint in list(table.constraints):
        if isinstance(constraint, ForeignKeyConstraint):
            table.constraints.remove(constraint)
    async with store[1].begin() as connection:
        await connection.run_sync(table.create)
    c.settings.llm.planner.model = "openai/offline-outline-patch"
    c.settings.llm.planner.model_override = None
    c.settings.llm.planner.max_tokens = 32768
    c.settings.llm.planner.reasoning_effort = "medium"
    c.settings.artifact_store.local_dir = str(tmp_path / "physical")
    c.settings.artifact_store.retain_prompt_payloads = True
    c.settings.artifact_store.mode = "local"
    catalog = tmp_path / "catalog.json"
    catalog.write_text('{"models":[]}')
    monkeypatch.setattr(model_catalog, "_CATALOG_PATH", catalog)
    monkeypatch.setattr(retained, "apply_runtime_llm_profile", lambda settings: settings)
    c.project.target_chapters = 4
    c.project.target_word_count = 9600
    detached = SimpleNamespace(
        **{
            key: deepcopy(getattr(c.project, key))
            for key in ("id", "target_chapters", "target_word_count", "metadata_json", "language")
        }
    )
    detached._source_bound_planning_canonical = {
        "id": str(c.canonical.id),
        "content_hash": snapshot_hash(c.canonical.content),
        "content": deepcopy(c.canonical.content),
    }
    projection = planner._compile_source_bound_volume_plan(
        detached,
        c.project.metadata_json["premise"],
        *(c.assets[kind].content for kind in foundation_selection.KINDS[:3]),
    )
    volume = deepcopy(c.assets["volume_plan"].content)
    volume[0]["_meta"]["source_projection_sha256"] = planner._approved_payload_hash(projection)
    c.assets["volume_plan"].content = volume
    c.beat = db.PlanningArtifactVersionModel(
        project_id=c.project.id,
        artifact_type="fanqie_beat_sheet",
        status="approved",
        version_no=1,
        schema_version="1.0",
        source_run_id=None,
        content={
            "title": "Unrelated weather stations",
            "logline": "Measure a changing wind",
            "unlock_milestone_segment": 2,
            "unknown_authored": {"unset": None, "flag": False},
            "beats": [
                {
                    "segment_number": number,
                    "title": f"Station {number} observations",
                    "beat_role": "observation",
                    "purpose": f"Observe station {number}",
                }
                for number in range(1, 5)
            ],
        },
    )
    async with c.factory() as session:
        session.add_all([c.project, c.assets["volume_plan"], c.beat])
        # This scratch fixture has no physical sends yet. The recovery entry
        # now seals its real initial ledger rather than illustrative counters.
        assert not list(await session.scalars(select(db.ExecutionAttemptModel)))
        initial_budget=await session.get(db.ExecutionBudgetModel,c.scope)
        initial_budget.calls_used=0
        initial_budget.tokens_charged=0
        await session.commit()
        await foundation_selection.pin_completed_foundation(
            session,
            project=c.project,
            workflow_id=c.assets["volume_plan"].source_run_id,
            settings=c.settings,
        )
        await session.commit()
    c.patch_calls, c.paths = [], {}
    c.after_patch = None
    c.patch_error = None
    c.protocol_retry = False
    # These state-machine tests start from a repairable, explicitly titled
    # source packet. The real legacy Beat compiler deliberately has no titles
    # and is covered separately by the zero-send coverage guard below.
    real_inputs=service._inputs
    async def titled_source(*args,**kwargs):
        project,context,validation=await real_inputs(*args,**kwargs)
        titles={b['segment_number']:b['title'] for b in
            context['untrusted_retained_beat']['content']['beats']}
        for chapter in context['compiler_candidate']['chapters']:
            chapter['title']=titles[chapter['chapter_number']]
        return project,context,validation
    monkeypatch.setattr(service,'_inputs',titled_source)
    c.failed_raw = "[]"

    async def repair(session, settings, **kwargs):
        c.patch_calls.append(
            deepcopy({key: value for key, value in kwargs.items() if key != "project"})
        )
        async with c.factory() as other:
            owner = await other.get(db.WorkflowRunModel, kwargs["workflow_run_id"])
            assert owner.status == "running" and owner.metadata_json["phase"] == "dispatching"
            assert owner.metadata_json["execution_scope_id"] == c.scope
        assert kwargs["validation_context"] == {
            "volume_number": 1,
            "expected_count": 4,
            "chapter_number_offset": 1,
            "cast_spec": c.assets["cast_spec"].content,
            "volume_entry": c.assets["volume_plan"].content[0],
        }
        assert 1 <= len(kwargs["directives"]) <= 4
        context = kwargs["source_context"]
        before = kwargs["outline_payload"]
        patches = [
            {
                "chapter_number": 1,
                "scope": "chapter",
                "field": "opening_situation",
                "value": "Observe a verifiable change in the weather station",
            }
        ]
        raw = json.dumps({"patches": patches}, ensure_ascii=False)
        request = surgical.build_request(
            settings,
            project=kwargs["project"],
            workflow_run_id=kwargs["workflow_run_id"],
            logical_name=service.LOGICAL_NAME,
            outline_payload=before,
            directives=kwargs["directives"],
            source_context=context,
        )
        async with c.factory() as other:
            owner = await other.get(db.WorkflowRunModel, kwargs["workflow_run_id"])
            role = budget.resolve_request_role(settings, kwargs["project"], request)
            assert owner.metadata_json["request_contract"]["effective_input_sha256"] == (
                llm._effective_input_hash(request, role)
            )
            assert owner.metadata_json["request_contract"]["model"] == role.model
        c.session = session
        failed = []
        if c.protocol_retry:
            bad, first_run = await record_completion(c, request, content=c.failed_raw)
            error = await service._failed_error(
                session, kwargs["project"], before, kwargs["validation_context"], bad
            )
            item = {"attempt": 1, "llm_run_id": str(first_run.id), "error": error}
            failed.append(item)
            request = surgical.build_request(
                settings,
                project=kwargs["project"],
                workflow_run_id=kwargs["workflow_run_id"],
                logical_name=service.LOGICAL_NAME,
                outline_payload=before,
                directives=kwargs["directives"],
                source_context=context,
                protocol_feedback=[
                    {
                        **item,
                        "rejected_response": bad.content,
                        "authority": "untrusted_protocol_failure_not_current_candidate",
                    }
                ],
            )
        completion, run = await record_completion(c, request, content=raw)
        result = planner._apply_outline_commercial_patches(before, patches, strict=True)
        result["_meta"] = {
            "surgical_repair_receipt": {
                "before_candidate": deepcopy(before),
                "llm_run_id": str(run.id),
                "protocol_attempts": failed,
                "after_candidate_hash": planner._approved_payload_hash(
                    planner._without_planning_meta(result)
                ),
                "protocol_version": budget.PROMPT_VERSION,
                "source_transport": request.metadata["planning_surgical_source_transport"],
            }
        }
        if c.after_patch:
            await c.after_patch(session, kwargs["workflow_run_id"])
        if c.patch_error:
            raise c.patch_error
        return result, completion.llm_run_id

    monkeypatch.setattr(
        planner,
        "_validate_generated_volume_outline_or_raise",
        lambda value, **kwargs: deepcopy(value),
    )
    monkeypatch.setattr(planner, "_repair_outline_commercial_findings_surgically", repair)
    return c


async def call(c, **changes):
    async with c.factory() as session:
        kwargs = {
            "project_slug": c.project.slug,
            "parent_artifact_id": c.outline.id,
            "parent_materialization_workflow_id": c.owner.id,
            "beat_artifact_id": c.beat.id,
            "actor": "fixture operator",
            "authorization_ref": "explicit-offline-fixture",
            "explicit_user_approval": True,
        }
        kwargs.update(changes)
        with bind_execution_scope(c.scope):
            return await service.propose_full_outline_revision(session, c.settings, **kwargs)


async def protected(c):
    async with c.factory() as session:
        return [
            deepcopy(list((await session.execute(select(*model.__table__.columns))).mappings()))
            for model in (
                db.ProjectModel,
                db.PlanningArtifactVersionModel,
                db.ChapterModel,
                db.SceneCardModel,
                db.BookProductionControlModel,
                db.ExecutionBudgetModel,
            )
        ]


async def test_exact_legacy_base_compiler_patch_replays_without_grade_projection_or_budget_reset(
    proposal,
):
    c = proposal
    before = await protected(c)
    first = await call(c)
    assert len(c.patch_calls) == 1
    context = c.patch_calls[0]["source_context"]
    assert context["untrusted_retained_beat"]["source_run_id"] is None
    assert context["untrusted_retained_beat"]["content"] == c.beat.content
    assert context["untrusted_retained_beat"]["paid_source_proof_asserted"] is False
    assert set(context["approved_foundation"]) == set(foundation_selection.KINDS)
    for kind, source in c.assets.items():
        assert context["approved_foundation"][kind]["content"] == source.content
    assert context["current_canonical"]["content"] == c.canonical.content
    assert context["legacy_materialized_base"]["candidate_payload"]["unknown_author_tree"] == {
        "unset": None
    }
    receipt = first["proposal_receipt"]
    assert receipt["grade_asserted"] is False and receipt["adoption_granted"] is False
    assert receipt["legacy_beat_paid_full_source_asserted"] is False
    assert len(receipt["paid_patch_attempts"]) == 1
    assert receipt["native_patch"][0]["field"] == "opening_situation"
    assert await call(c) == first and len(c.patch_calls) == 1
    assert await protected(c) == before


async def test_real_untitled_compiler_is_rejected_before_an_impossible_paid_repair(proposal,monkeypatch):
    c=proposal
    monkeypatch.setattr(service,'_inputs',service.legacy._inputs)
    before=await protected(c)
    with pytest.raises(ValueError,match='typed_domain_recovery_required:uneditable_title'):
        await call(c)
    assert c.patch_calls==[]
    assert await protected(c)==before
    assert not await owners(c)


@pytest.mark.parametrize("change", ["approval", "actor", "reason", "foreign_beat", "budget"])
async def test_entry_guards_never_dispatch(proposal, change):
    c = proposal
    changes = {
        "approval": {"explicit_user_approval": False},
        "actor": {"actor": ""},
        "reason": {"authorization_ref": ""},
        "foreign_beat": {"beat_artifact_id": uuid4()},
    }.get(change, {})
    if change == "budget":
        async with c.factory() as session:
            budget = await session.get(db.ExecutionBudgetModel, c.scope)
            budget.status = "unknown"
            await session.commit()
    with pytest.raises(ValueError):
        await call(c, **changes)
    assert c.patch_calls == []


@pytest.mark.parametrize("change", ["beat", "pause", "cancel_owner", "canonical"])
async def test_postreturn_drift_cannot_publish_and_original_paid_rows_survive(proposal, change):
    c = proposal

    async def after(session, identity):
        # Core row mutation emulates an external writer without touching stale ORM objects.
        from sqlalchemy import update

        if change == "beat":
            await session.execute(
                update(db.PlanningArtifactVersionModel)
                .where(db.PlanningArtifactVersionModel.id == c.beat.id)
                .values(content={**c.beat.content, "unknown": "changed"})
            )
        elif change == "pause":
            await session.execute(
                update(db.ProjectModel)
                .where(db.ProjectModel.id == c.project.id)
                .values(status="paused")
            )
        elif change == "cancel_owner":
            await session.execute(
                update(db.WorkflowRunModel)
                .where(db.WorkflowRunModel.id == identity)
                .values(status="cancelled")
            )
        else:
            await session.execute(
                update(db.PlanningArtifactVersionModel)
                .where(db.PlanningArtifactVersionModel.id == c.canonical.id)
                .values(content={"changed": True})
            )
        await session.commit()

    c.after_patch = after
    with pytest.raises(ValueError):
        await call(c)
    assert len(c.patch_calls) == 1
    async with c.factory() as session:
        assert len(list(await session.scalars(select(db.LlmRunModel)))) == 1
        owner = (
            await session.scalars(
                select(db.WorkflowRunModel).where(
                    db.WorkflowRunModel.workflow_type == service.WORKFLOW
                )
            )
        ).one()
        assert owner.status != "completed"
        if change == "cancel_owner":
            assert owner.status == "cancelled"


@pytest.mark.parametrize("error", [RuntimeError("paid invalid patch"), KeyboardInterrupt()])
async def test_terminal_failure_and_interrupt_never_buy_another_protocol(proposal, error):
    c = proposal
    c.patch_error = error
    with pytest.raises(type(error)):
        await call(c)
    with pytest.raises(ValueError, match="terminal_or_inflight"):
        await call(c)
    assert len(c.patch_calls) == 1


async def test_completed_physical_file_tamper_fails_closed_without_call(proposal):
    c = proposal
    await call(c)
    path = next(path for key, path in c.paths.items() if key.endswith(":request"))
    assert isinstance(path, Path)
    path.write_text("{}")
    with pytest.raises(ValueError):
        await call(c)
    assert len(c.patch_calls) == 1


async def test_all_32_findings_remain_addressable_in_at_most_four_chapter_directives():
    context = {
        "compiler_candidate": {"chapters": [{"chapter_number": n} for n in range(1, 5)]},
        "readiness_diagnostics": {
            "blocking_findings": [
                {
                    "path": f"chapter_outlines[{n // 8}].field",
                    "code": f"MISSING_{n}",
                    "unknown": None,
                }
                for n in range(32)
            ],
            "audit_findings": [],
        },
    }
    before = deepcopy(context)
    directives = [json.loads(item) for item in service._directives(context)]
    indices = [
        index
        for item in directives
        for group in item["chapter_findings"].values()
        for index in group
    ]
    assert len(directives) == 4 and sorted(indices) == list(range(32))
    assert context == before


async def test_two_patch_attempts_are_both_physically_bound_and_settled(proposal):
    c = proposal
    c.protocol_retry = True
    result = await call(c)
    assert len(result["proposal_receipt"]["paid_patch_attempts"]) == 2
    async with c.factory() as session:
        runs = list(await session.scalars(select(db.LlmRunModel)))
        attempts = list(await session.scalars(select(db.ExecutionAttemptModel)))
        assert len(runs) == len(attempts) == 2
        assert all(
            attempt.status == "completed" and attempt.actual_tokens == 18 for attempt in attempts
        )
    assert await call(c) == result and len(c.patch_calls) == 1


async def test_inflight_without_published_proposal_requires_recovery_without_dispatch(proposal):
    from sqlalchemy import update

    c = proposal
    result = await call(c)
    async with c.factory() as session:
        owner = (
            await session.scalars(
                select(db.WorkflowRunModel).where(
                    db.WorkflowRunModel.workflow_type == service.WORKFLOW
                )
            )
        ).one()
        state = deepcopy(owner.metadata_json)
        state["phase"] = "dispatching"
        state.pop("candidate_payload")
        state.pop("proposal_receipt")
        await session.execute(
            update(db.WorkflowRunModel)
            .where(db.WorkflowRunModel.id == owner.id)
            .values(status="running", metadata_json=state)
        )
        await session.commit()
    with pytest.raises(ValueError, match="terminal_or_inflight"):
        await call(c)
    assert len(c.patch_calls) == 1
    assert result["proposal_receipt"]["grade_asserted"] is False


@pytest.mark.parametrize("phase", ["prepared", "completed"])
async def test_existing_proposal_cannot_move_to_another_open_project_and_ambient_budget(
    proposal, monkeypatch, phase
):
    from sqlalchemy import update

    from bestseller.services import pipelines

    c = proposal
    if phase == "prepared":
        checkpoint = pipelines._checkpoint_commit

        async def stop_before_first_provider(session):
            await checkpoint(session)
            raise RuntimeError("prepared checkpoint, no provider yet")

        monkeypatch.setattr(pipelines, "_checkpoint_commit", stop_before_first_provider)
        with pytest.raises(RuntimeError, match="prepared checkpoint"):
            await call(c)
        monkeypatch.setattr(pipelines, "_checkpoint_commit", checkpoint)
        assert c.patch_calls == []
    else:
        await call(c)
        assert len(c.patch_calls) == 1

    scope_b = "project:alternate-open-budget:" + str(uuid4())
    async with c.factory() as session:
        owner = (
            await session.scalars(
                select(db.WorkflowRunModel).where(
                    db.WorkflowRunModel.workflow_type == service.WORKFLOW
                )
            )
        ).one()
        assert owner.status == ("running" if phase == "prepared" else "completed")
        assert owner.metadata_json["phase"] == (
            "prepared" if phase == "prepared" else "proposal_ready"
        )
        assert owner.metadata_json["execution_scope_id"] == c.scope
        original_context = deepcopy(owner.metadata_json["source_context"])
        project = await session.get(db.ProjectModel, c.project.id)
        await session.execute(
            update(db.ProjectModel)
            .where(db.ProjectModel.id == c.project.id)
            .values(metadata_json={**project.metadata_json, "execution_scope_id": scope_b})
        )
        session.add(
            db.ExecutionBudgetModel(
                scope_id=scope_b,
                max_calls=c.budget.max_calls,
                max_tokens=c.budget.max_tokens,
                calls_used=0,
                tokens_charged=0,
                status="active",
                deadline_at=c.budget.deadline_at,
                metadata_json={"separate_open_budget": True},
            )
        )
        await session.commit()

    # This is the dangerous reachable case: the source/candidate digest stays
    # unchanged while both the fresh project and ambient context point to B.
    async with c.factory() as session:
        with bind_execution_scope(scope_b):
            fresh, current_context, _ = await service._inputs(
                session,
                c.settings,
                project_slug=c.project.slug,
                parent_artifact_id=c.outline.id,
                parent_materialization_workflow_id=c.owner.id,
                beat_artifact_id=c.beat.id,
            )
            assert fresh.metadata_json["execution_scope_id"] == scope_b
            assert content_hash(current_context) == content_hash(original_context)
            from bestseller.services.budget import execution_scope_block_code

            assert (
                execution_scope_block_code(await session.get(db.ExecutionBudgetModel, scope_b))
                is None
            )

    async def frozen_rows():
        async with c.factory() as session:
            return [
                deepcopy(list((await session.execute(select(*model.__table__.columns))).mappings()))
                for model in (
                    db.ProjectModel,
                    db.PlanningArtifactVersionModel,
                    db.ChapterModel,
                    db.SceneCardModel,
                    db.BookProductionControlModel,
                    db.ExecutionBudgetModel,
                    db.WorkflowRunModel,
                    db.LlmRunModel,
                    db.ExecutionOperationModel,
                    db.ExecutionAttemptModel,
                )
            ]

    before = await frozen_rows()
    dispatches_before = len(c.patch_calls)
    async with c.factory() as session:
        with bind_execution_scope(scope_b):
            with pytest.raises(ValueError, match="original_scope_changed"):
                await service.propose_full_outline_revision(
                    session,
                    c.settings,
                    project_slug=c.project.slug,
                    parent_artifact_id=c.outline.id,
                    parent_materialization_workflow_id=c.owner.id,
                    beat_artifact_id=c.beat.id,
                    actor="fixture operator",
                    authorization_ref="explicit-offline-fixture",
                    explicit_user_approval=True,
                )
    assert len(c.patch_calls) == dispatches_before
    assert await frozen_rows() == before


async def owners(c):
    async with c.factory() as session:
        return list(
            await session.scalars(
                select(db.WorkflowRunModel).where(
                    db.WorkflowRunModel.workflow_type == service.WORKFLOW
                )
            )
        )


async def test_source_code_wrapper_change_never_creates_another_owner(proposal, monkeypatch):
    from bestseller.services import fanqie_short_planner

    c = proposal
    first = await call(c)
    actual = fanqie_short_planner.build_fanqie_segment_outline_batch

    def new_code_wrapper(*args, **kwargs):
        return actual(*args, **kwargs)

    monkeypatch.setattr(
        fanqie_short_planner, "build_fanqie_segment_outline_batch", new_code_wrapper
    )
    assert await call(c) == first
    assert len(c.patch_calls) == len(await owners(c)) == 1


@pytest.mark.parametrize("change", ["wire", "validation", "compiler_audit"])
async def test_same_family_changed_wire_or_validation_fails_without_a_new_owner(
    proposal, monkeypatch, change
):
    c = proposal
    first = await call(c)
    if change == "wire":
        from bestseller.services.source_complete_retention import source_complete_retention_metadata

        actual = surgical.build_request

        def changed(*args, **kwargs):
            req = actual(*args, **kwargs)
            old = req.metadata["source_complete_retention"]
            raw = req.user_prompt[old["packet_start"] : old["packet_end"]]
            from bestseller.services.lossless_source_encoding import decode_lossless_source

            logical = decode_lossless_source(json.loads(raw))
            req.user_prompt += "\nChanged framework instructions."
            req.metadata["source_complete_retention"] = source_complete_retention_metadata(
                req,
                role=budget.resolve_request_role(c.settings, kwargs["project"], req),
                logical_source=logical,
                packet_start=old["packet_start"],
                packet_end=old["packet_end"],
            )
            return req

        monkeypatch.setattr(surgical, "build_request", changed)
    else:
        actual = service._inputs

        async def changed(*args, **kwargs):
            project, context, validation = await actual(*args, **kwargs)
            if change == "validation":
                validation = {**validation, "new_validator_contract": True}
            else:
                context = {**context, "compiler_receipt": {"new_audit_only": True}}
            return project, context, validation

        monkeypatch.setattr(service, "_inputs", changed)
    with pytest.raises(ValueError, match="existing_wire_or_validation_changed"):
        await call(c)
    rows = await owners(c)
    assert len(rows) == len(c.patch_calls) == 1 and str(rows[0].id) == first["workflow_run_id"]
    assert rows[0].status == "completed"


async def test_completed_jsonb_recursive_key_reorder_keeps_wire_and_owner(proposal):
    from sqlalchemy import update

    c = proposal
    first = await call(c)
    row = (await owners(c))[0]
    reordered = json.loads(json.dumps(row.metadata_json, sort_keys=True))
    async with c.factory() as session:
        await session.execute(
            update(db.WorkflowRunModel)
            .where(db.WorkflowRunModel.id == row.id)
            .values(metadata_json=reordered)
        )
        await session.commit()
    assert await call(c) == first and len(c.patch_calls) == 1


@pytest.mark.parametrize("change", ["pause", "canonical"])
async def test_source_or_control_change_after_initial_commit_prevents_dispatch(
    proposal, monkeypatch, change
):
    from sqlalchemy import update

    from bestseller.services import pipelines

    c = proposal
    actual = pipelines._checkpoint_commit
    fired = False

    async def checkpoint(session):
        nonlocal fired
        await actual(session)
        if fired:
            return
        fired = True
        async with c.factory() as other:
            if change == "pause":
                await other.execute(
                    update(db.BookProductionControlModel)
                    .where(db.BookProductionControlModel.project_id == c.project.id)
                    .values(desired_state="pause")
                )
            else:
                await other.execute(
                    update(db.PlanningArtifactVersionModel)
                    .where(db.PlanningArtifactVersionModel.id == c.canonical.id)
                    .values(content={"changed": True})
                )
            await other.commit()

    monkeypatch.setattr(pipelines, "_checkpoint_commit", checkpoint)
    with pytest.raises(ValueError):
        await call(c)
    assert c.patch_calls == []
    assert (await owners(c))[0].status == "failed"


async def seed_capacity_predecessor(c):
    from bestseller.services import outline_revision_proposal as legacy
    from bestseller.services.planning_capacity_continuation import LEGACY_SOURCE_CONTEXT_PREFIX

    c.accounting = True
    async with c.factory() as session:
        with bind_execution_scope(c.scope):
            _, context, _ = await service._inputs(
                session,
                c.settings,
                project_slug=c.project.slug,
                parent_artifact_id=c.outline.id,
                parent_materialization_workflow_id=c.owner.id,
                beat_artifact_id=c.beat.id,
            )
        owner = db.WorkflowRunModel(
            id=uuid4(),
            project_id=c.project.id,
            workflow_type=service.WORKFLOW,
            scope_type="planning_artifact",
            scope_id=c.outline.id,
            status="failed",
            current_step="proposal_requires_recovery",
            metadata_json={
                "schema": legacy.SCHEMA,
                "phase": "terminal_error",
                "source_context": context,
                "execution_scope_id": c.scope,
            },
        )
        session.add(owner)
        await session.flush()
        c.session = session
        reconstructed = []
        for i in range(2):
            request = llm.LLMCompletionRequest(
                logical_role="planner",
                project_id=c.project.id,
                workflow_run_id=owner.id,
                system_prompt="Original complete prior system.",
                user_prompt=("original-" + str(i)) * 14000
                + "\n[FULL REVISION SOURCE CONTEXT]\n"
                + LEGACY_SOURCE_CONTEXT_PREFIX
                + json.dumps(context, ensure_ascii=False),
                fallback_response="{}",
                prompt_template=f"planner_{service.LOGICAL_NAME}_commercial_surgical_repair",
                prompt_version="3.4-window-complete-repair",
                max_tokens_override=12000,
            )
            _, run = await record_completion(
                c,
                request,
                content='{"patches":[',
                finish_reason="length",
                input_tokens=7,
                output_tokens=12000,
            )
            reconstructed.append(
                {"llm_run_id": str(run.id), "request": request.model_dump(mode="json")}
            )
        await session.commit()
    return owner.id, reconstructed


@pytest.mark.parametrize("proof", ["missing", "wrong_owner", "missing_requests"])
async def test_any_legacy_spent_requires_the_exact_explicit_core_predecessor(proposal, proof):
    c = proposal
    identity, requests = await seed_capacity_predecessor(c)
    changes = {
        "wrong_owner": {
            "capacity_predecessor_workflow_id": uuid4(),
            "reconstructed_capacity_requests": requests,
        },
        "missing_requests": {"capacity_predecessor_workflow_id": identity},
    }.get(proof, {})
    with pytest.raises(ValueError, match=r"capacity_predecessor|required"):
        await call(c, **changes)
    assert c.patch_calls == [] and len(await owners(c)) == 1


async def test_capacity_successor_reproves_core_and_keeps_initial_accounting_after_new_spend(
    proposal,
):
    c = proposal
    identity, requests = await seed_capacity_predecessor(c)
    old_row = deepcopy((await owners(c))[0].metadata_json)
    old_files = {key: path.read_bytes() for key, path in c.paths.items()}
    first = await call(
        c, capacity_predecessor_workflow_id=identity, reconstructed_capacity_requests=requests
    )
    baseline = first["proposal_receipt"]["capacity_predecessor"]
    assert baseline["existing_accounting"]["calls_used"] == 2
    assert baseline["old_attempts_consumed"] == 2
    assert await call(c) == first and len(c.patch_calls) == 1
    async with c.factory() as session:
        current = await session.get(db.ExecutionBudgetModel, c.scope)
        assert current.calls_used == 3 and current.tokens_charged == 24032
        assert (await session.get(db.WorkflowRunModel, identity)).metadata_json == old_row
    assert all(c.paths[key].read_bytes() == value for key, value in old_files.items())


@pytest.mark.parametrize("change", ["limit", "deadline", "source_file"])
async def test_capacity_baseline_limits_and_old_physical_proof_cannot_change(proposal, change):
    from datetime import timedelta

    from sqlalchemy import update

    c = proposal
    identity, requests = await seed_capacity_predecessor(c)
    await call(
        c, capacity_predecessor_workflow_id=identity, reconstructed_capacity_requests=requests
    )
    if change == "source_file":
        next(iter(c.paths.values())).write_bytes(b"{}")
    else:
        async with c.factory() as session:
            old = await session.get(db.ExecutionBudgetModel, c.scope)
            value = (
                {"max_calls": old.max_calls + 1}
                if change == "limit"
                else {"deadline_at": old.deadline_at + timedelta(seconds=1)}
            )
            await session.execute(
                update(db.ExecutionBudgetModel)
                .where(db.ExecutionBudgetModel.scope_id == c.scope)
                .values(**value)
            )
            await session.commit()
    with pytest.raises(ValueError):
        await call(c)
    assert len(c.patch_calls) == 1


async def test_final_proof_calls_shared_validator_and_rejects_implicit_normalization(
    proposal, monkeypatch
):
    c = proposal

    def normalize(value, **kwargs):
        result = deepcopy(value)
        result["chapters"][0]["scenes"][0]["participants"] = ["Not a native patch edit"]
        return result

    monkeypatch.setattr(planner, "_validate_generated_volume_outline_or_raise", normalize)
    with pytest.raises(planner.PlannerFallbackError, match="explicit native edits"):
        await call(c)
    assert len(c.patch_calls) == 1 and (await owners(c))[0].status == "failed"


async def test_extra_patch_root_reproduces_producer_error_then_replays_second_paid_input(proposal):
    c = proposal
    c.protocol_retry = True
    c.failed_raw = '{"patches":[],"extra":true}'
    result = await call(c)
    failed = result["candidate_payload"]["_meta"]["surgical_repair_receipt"]["protocol_attempts"]
    assert failed[0]["error"] == "Source-complete patch requires exactly the patches field."
    assert len(result["proposal_receipt"]["paid_patch_attempts"]) == 2
    assert await call(c) == result and len(c.patch_calls) == 1


@pytest.mark.parametrize("target", ["root", "transport", "attempt"])
async def test_completed_cached_financial_rows_cannot_mask_current_column_tampering(
    proposal, target
):
    from sqlalchemy import update

    c = proposal
    result = await call(c)
    async with c.factory() as session:
        operations = list(await session.scalars(select(db.ExecutionOperationModel)))
        attempts = list(await session.scalars(select(db.ExecutionAttemptModel)))
        root = next(row for row in operations if row.status == "completed")
        transport = next(row for row in operations if row.status == "transport_result")
        if target == "attempt":
            await session.execute(
                update(db.ExecutionAttemptModel)
                .where(db.ExecutionAttemptModel.id == attempts[0].id)
                .values(actual_tokens=0)
                .execution_options(synchronize_session=False)
            )
            assert attempts[0].actual_tokens == 18
        else:
            row = root if target == "root" else transport
            value = deepcopy(row.result_json)
            body = value if target == "root" else value["result"]
            body["content"] = "Tampered current physical return"
            await session.execute(
                update(db.ExecutionOperationModel)
                .where(db.ExecutionOperationModel.key == row.key)
                .values(result_json=value)
                .execution_options(synchronize_session=False)
            )
            assert row.result_json != value
        await session.commit()
        with bind_execution_scope(c.scope), pytest.raises(ValueError):
            await service.propose_full_outline_revision(
                session,
                c.settings,
                project_slug=c.project.slug,
                parent_artifact_id=c.outline.id,
                parent_materialization_workflow_id=c.owner.id,
                beat_artifact_id=c.beat.id,
                actor="fixture operator",
                authorization_ref="explicit-offline-fixture",
                explicit_user_approval=True,
            )
        assert not session.dirty
    assert len(c.patch_calls) == 1
    assert (await owners(c))[0].status == "completed"
    assert result["proposal_receipt"]["grade_asserted"] is False
