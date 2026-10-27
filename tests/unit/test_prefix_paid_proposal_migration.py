"""SQLite + retained physical files, inert gateway; no PG/providers.

Narrative validation uses the existing prefix fixture's typed contract adapter;
strict patch registry, source bindings, physical custody and DB lifecycle are real.
"""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
import hashlib
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.schema import CreateTable
from test_adopted_knowledge_lineage import _jsonb  # noqa: F401
from test_chapter_outline_readiness import rig  # noqa: F401
from bestseller.domain.workflow import ChapterOutlineBatchInput
from bestseller.infra.db.models import (
    ProjectModel,
    WorkflowRunModel,
    ChapterModel,
    ChapterDraftVersionModel,
    PlanningArtifactVersionModel,
    ExecutionBudgetModel,
    ExecutionOperationModel,
    ExecutionAttemptModel,
    LlmRunModel,
)
from bestseller.services import llm, retained_review_input_proof as proof, workflows
from bestseller.services.prompt_source_policy import content_hash
from bestseller.settings import load_settings
from bestseller.services import prefix_paid_proposal_migration as migration

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


def proposed(monkeypatch):
    return migration.prefix


@pytest.fixture
async def paid_case(rig, tmp_path, monkeypatch, request):  # noqa: F811
    prefix = migration.prefix
    mode = getattr(request, "param", None)
    settings = load_settings(env={}).model_copy(deep=True)
    settings.llm.mock = False
    settings.llm.planner.model = "openai/offline-prefix-fixture"
    settings.llm.planner.model_override = None
    settings.artifact_store.mode = "local"
    settings.artifact_store.local_dir = tmp_path / "physical"
    settings.artifact_store.retain_prompt_payloads = True
    monkeypatch.setattr(proof, "apply_runtime_llm_profile", lambda s: s)
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'prefix_migration_test.sqlite3'}"
    )
    models = (
        ProjectModel,
        WorkflowRunModel,
        ChapterModel,
        ChapterDraftVersionModel,
        PlanningArtifactVersionModel,
        ExecutionBudgetModel,
        ExecutionOperationModel,
        ExecutionAttemptModel,
        LlmRunModel,
    )
    async with engine.begin() as c:
        for model in models:
            ddl = str(CreateTable(model.__table__).compile(dialect=c.dialect))
            await c.exec_driver_sql(ddl.replace("::jsonb", ""))
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        project = ProjectModel(
            id=rig.project.id,
            slug=rig.project.slug,
            title=rig.project.title,
            genre=rig.project.genre,
            language="en",
            target_chapters=50,
            target_word_count=130000,
            metadata_json=deepcopy(rig.project.metadata_json),
        )
        session.add(project)
        await session.flush()
        for kind, a in rig.foundation.items():
            session.add(
                PlanningArtifactVersionModel(
                    id=a.id,
                    project_id=project.id,
                    artifact_type=kind,
                    source_run_id=a.source_run_id,
                    version_no=1,
                    status="approved",
                    schema_version="1.0",
                    content=deepcopy(a.content),
                )
            )
        await session.commit()
        monkeypatch.setattr(
            prefix,
            "get_latest_approved_planning_artifact",
            workflows.get_latest_approved_planning_artifact,
        )
        foundation, contents = await prefix._foundation(session, project)
        source = deepcopy(rig.source)
        record, volume = prefix._source_record(project, source)
        binding = prefix._window_binding(project, source, volume)
        parent = ChapterOutlineBatchInput.model_validate(
            {"chapters": [deepcopy(source["chapters"][0])]}
        ).model_dump(mode="json", by_alias=True)
        parent_hash = content_hash(parent)
        grade = prefix.LLMQualityJudgeResult(
            scope="outline",
            passed=False,
            overall_score=0.6,
            blocking_issues=(
                {
                    "code": "VISIBLE_ACTION",
                    "severity": "critical",
                    "evidence": "fixture diagnostic",
                    "required_fix": "show action",
                },
            ),
        )
        report = grade.model_dump(mode="json", by_alias=True)
        report["graded_outline_hash"] = parent_hash
        state = {
            "schema": prefix.SCHEMA,
            "operation_binding_version": "workflow-candidate-v1",
            "identity": {
                "schema": prefix.SCHEMA,
                "project_id": str(project.id),
                "binding": binding,
                "source_hash": content_hash(source),
                "foundation": foundation,
                "through_chapter": 1,
                "max_repair_rounds": 2,
            },
            "binding": binding,
            "foundation": foundation,
            "source_record": record,
            "source_payload": source,
            "chapter_numbers": [1],
            "phase_scope": {
                "full_window": binding["window"],
                "approved_scope_only": [1],
                "window_effect_distribution": "deferred",
                "full_window_approved": False,
                "full_volume_complete": False,
            },
            "candidates": [
                {
                    "candidate": parent,
                    "candidate_hash": parent_hash,
                    "validated": True,
                    "report": report,
                }
            ],
            "patch_attempts": [],
            "phase": "rejected",
            "reason": "prefix_judge_rejected",
            "final_report": deepcopy(report),
        }
        wf = WorkflowRunModel(
            project_id=project.id,
            workflow_type=prefix.WORKFLOW_TYPE,
            scope_type="project",
            scope_id=project.id,
            status="machine_blocked",
            current_step="prefix_judge_rejected",
            metadata_json=deepcopy(state),
            idempotency_key=content_hash(state["identity"]),
        )
        session.add(wf)
        await session.flush()
        scope = f"project:{project.id}"
        until = datetime.now(UTC) + timedelta(days=1)
        budget = ExecutionBudgetModel(
            scope_id=scope,
            max_calls=50,
            max_tokens=100000,
            calls_used=2,
            tokens_charged=40,
            deadline_at=until,
            status="active",
            metadata_json={},
        )
        session.add(budget)
        semantic = prefix.evaluate_outline_semantic_gate(parent)
        brief = migration.planner._outline_judge_project_brief(
            project,
            metadata=project.metadata_json,
            semantic_candidates=[f.to_dict() for f in prefix.llm_adjudication_candidates(semantic)],
            world_spec=contents["world_spec"],
        )
        brief["prefix_evaluation_scope"] = {
            **state["phase_scope"],
            "approved_scope_only": [1],
            "full_window": binding["window"],
            "full_source_hash": state["identity"]["source_hash"],
            "tail_status": "unapproved; no complete-volume claim",
        }
        fields = [
            "goal",
            "opening_situation",
            "opening_pressure",
            "main_conflict",
            "hook_description",
            "protagonist_inner_state",
            "information_gap_mode",
            "world_rule_landing",
            "protagonist_flaw",
            "protagonist_desire",
            "required_payoff",
            "tail_hook",
            "chapter_event_role",
            "tail_hook_description",
            "information_control_mode",
        ]
        runs = []
        for index in (1, 2):
            patches = [
                dict(
                    chapter_number=1,
                    scope="chapter",
                    field=f,
                    value=f"Changed {f} version {index}.",
                )
                for f in fields
            ]
            if mode == "bad_late_target" and index == 2:
                patches[-1]["field"] = "unregistered_foreign_field"
            raw = json.dumps({"patches": patches}, ensure_ascii=False)
            protocol = deepcopy(prefix._projection_patch_protocol(parent))
            protocol.pop("capacity_policy")
            protocol["max_patches"] = 12
            request = llm.LLMCompletionRequest(
                project_id=project.id,
                workflow_run_id=wf.id,
                logical_role="planner",
                system_prompt="你是章纲定点修订编辑，只输出JSON补丁。"
                + prefix.OUTLINE_SOURCE_AUTHORITY_RULES
                + prefix._PROJECTION_REPAIR_RULES,
                user_prompt=migration._render_original_user(
                    state,
                    parent,
                    prefix.build_outline_repair_directives(grade, source_authority=True),
                    None,
                    brief,
                    protocol,
                ),
                prompt_template="chapter_outline_prefix_surgical_repair",
                prompt_version="2",
                max_tokens_override=12000,
                fallback_response="{}",
            )
            role = settings.llm.planner
            digest = llm._effective_input_hash(request, role)
            namespace = migration._namespace(state, wf.id, parent_hash, index)
            opid = migration.execution_operation_id(
                namespace,
                subject="planner:chapter_outline_prefix_surgical_repair:2",
                revision=digest,
            )
            key = hashlib.sha256(f"{scope}\n{opid}:sample:0".encode()).hexdigest()
            aid = uuid4().hex
            rid = uuid4()
            origin = dict(
                project_id=str(project.id),
                workflow_run_id=str(wf.id),
                logical_role="planner",
                prompt_template=request.prompt_template,
                prompt_version="2",
                prompt_hash=digest,
            )
            metadata = {"paid_call_origin": origin, "effective_input_receipt": {"hash": digest}}
            value = llm.LLMCompletionResult(
                content=raw,
                provider="openai",
                model_name=role.model,
                llm_run_id=rid,
                finish_reason="stop",
                input_tokens=12,
                output_tokens=8,
                metadata=metadata,
                raw_message={"role": "assistant", "content": raw},
            )
            with llm.bind_project_prompt_audit(project.id, reason="offline-prefix-fixture"):
                req, resp, reason, replayable = llm._persist_prompt_payloads(
                    settings=settings,
                    request=request,
                    llm_run_id=rid,
                    configured_model=role.model,
                    effective_model=role.model,
                    provider="openai",
                    effective_temperature=role.temperature,
                    effective_max_tokens=12000,
                    effective_timeout_seconds=role.timeout_seconds,
                    effective_stream=False,
                    effective_thinking_type=llm._effective_thinking_type(role),
                    effective_reasoning_effort=role.reasoning_effort,
                    content=raw,
                    tool_call_count=0,
                    finish_reason="stop",
                    fallback_used=False,
                    fallback_source=None,
                    input_tokens=12,
                    output_tokens=8,
                    tool_calls=None,
                    raw_message=value.raw_message,
                )
            assert replayable, reason
            rawreturn = value.model_dump(mode="json")
            tkey = hashlib.sha256(f"transport-result.v1\n{scope}\n{aid}".encode()).hexdigest()
            run = LlmRunModel(
                id=rid,
                project_id=project.id,
                workflow_run_id=wf.id,
                logical_role="planner",
                provider="openai",
                model_name=role.model,
                prompt_template=request.prompt_template,
                prompt_version="2",
                prompt_hash=digest,
                finish_reason="stop",
                input_tokens=12,
                output_tokens=8,
                request_payload_ref=req,
                response_payload_ref=resp,
                metadata_json={
                    **metadata,
                    "execution_receipt": {
                        "scope_id": scope,
                        "operation_key": key,
                        "attempts": [{"attempt_id": aid, "status": "completed"}],
                    },
                },
            )
            session.add_all(
                [
                    run,
                    ExecutionOperationModel(
                        key=key,
                        scope_id=scope,
                        input_hash=digest,
                        status="completed",
                        owner="fixture",
                        lease_until=until,
                        result_json=rawreturn,
                    ),
                    ExecutionOperationModel(
                        key=tkey,
                        scope_id=scope,
                        input_hash=digest,
                        status="transport_result",
                        owner="fixture",
                        lease_until=until,
                        result_json={
                            "operation_key": key,
                            "attempt_id": aid,
                            "input_hash": digest,
                            "result": rawreturn,
                        },
                    ),
                    ExecutionAttemptModel(
                        id=aid,
                        operation_key=key,
                        scope_id=scope,
                        input_hash=digest,
                        status="completed",
                        model_name=role.model,
                        reserved_tokens=12000,
                        actual_tokens=20,
                        created_at=datetime.now(UTC),
                    ),
                ]
            )
            parsed, parse = prefix._parse_prefix_json(raw, allow_internal_closers=False)
            parse["finish_reason"] = "stop"
            state["patch_attempts"].append(
                {
                    "parent_hash": parent_hash,
                    "status": "invalid",
                    "consumed": True,
                    "error": {
                        "type": "OutlinePrefixReadinessError",
                        "code": "prefix_patch_protocol_or_validation_failed",
                        "message": "prefix_patch_capacity_exceeded",
                    },
                    "completion": value.model_dump(mode="json"),
                    "json_parse_receipt": parse,
                }
            )
            runs.append(run)
        wf.metadata_json = deepcopy(state)
        await session.commit()

        async def forbidden(*args, **kwargs):
            raise AssertionError("zero-provider migration reached gateway")

        monkeypatch.setattr(llm, "complete_text", forbidden)
        monkeypatch.setattr(prefix, "complete_text", forbidden)
        yield SimpleNamespace(
            session=session,
            settings=settings,
            project=project,
            wf=wf,
            state=deepcopy(state),
            runs=runs,
            budget=budget,
            models=models,
            args=dict(
                project_id=project.id,
                workflow_run_id=wf.id,
                attempt_index=2,
                llm_run_id=runs[1].id,
                requested_by="authorized-fixture",
                evidence_ref="fixture-extraction",
            ),
        )
    await engine.dispose()


async def snapshot(c):
    return {
        m.__tablename__: {
            str(getattr(r, "id", getattr(r, "key", getattr(r, "scope_id", "")))): migration._row(r)
            for r in await c.session.scalars(select(m).execution_options(populate_existing=True))
        }
        for m in c.models
    }


async def invoke(c, **changes):
    return await migration.migrate_paid_prefix_proposal(
        c.session, c.settings, **{**c.args, **changes}
    )


async def test_inspect_apply_audit_same_wf_and_lineage_idempotent(paid_case, monkeypatch):
    c = paid_case
    before = await snapshot(c)
    plan = await invoke(c)
    assert plan["new_patch_rounds"] == plan["provider_calls"] == 0
    assert await snapshot(c) == before
    result = await invoke(c, execute=True, expected_plan_sha256=plan["plan_sha256"])
    assert result["status"] == "applied"
    await c.session.commit()
    state = c.wf.metadata_json
    assert (
        state["phase"] == "judge"
        and state["patch_attempts"] == c.state["patch_attempts"]
        and len(state["candidates"]) == 2
    )
    assert (
        state["candidates"][0] == c.state["candidates"][0]
        and "report" not in state["candidates"][1]
    )
    assert state["terminal_history"][0]["final_report"] == c.state["final_report"]
    audit = await c.session.get(WorkflowRunModel, migration.UUID(result["audit_workflow_id"]))
    assert audit.metadata_json["original_state"] == c.state and audit.status == "completed"
    assert proposed(monkeypatch).prefix_json_lineage_status(state)["status"] == "current"
    unwitnessed = deepcopy(state)
    unwitnessed["candidates"][-1].pop("derived_proposal")
    assert migration.prefix.prefix_json_lineage_status(unwitnessed)["status"] == "non_fresh"
    after = await snapshot(c)
    for table in before:
        if table != "workflow_runs":
            assert after[table] == before[table]
    replay = await invoke(c, execute=True)
    assert replay["status"] == "already_applied" and await snapshot(c) == after


async def test_outer_rollback_keeps_original_invalid_and_no_audit(paid_case):
    c = paid_case
    before = await snapshot(c)
    plan = await invoke(c)
    await invoke(c, execute=True, expected_plan_sha256=plan["plan_sha256"])
    await c.session.rollback()
    assert await snapshot(c) == before


@pytest.mark.parametrize(
    "fault",
    [
        "wrong_run",
        "wrong_parent",
        "other_error",
        "unknown_finance",
        "rawfile",
        "source",
        "bad_plan",
        "bad_late_patch",
    ],
)
async def test_rejects_incompatible_proof_without_new_candidate(paid_case, fault):
    c = paid_case
    kwargs = {}
    if fault == "wrong_run":
        kwargs["llm_run_id"] = c.runs[0].id
    elif fault == "wrong_parent":
        state = deepcopy(c.wf.metadata_json)
        state["candidates"][0]["candidate"]["chapters"][0]["goal"] = "other"
        c.wf.metadata_json = state
    elif fault == "other_error":
        state = deepcopy(c.wf.metadata_json)
        state["patch_attempts"][1]["error"]["message"] = "not-capacity"
        c.wf.metadata_json = state
    elif fault == "unknown_finance":
        c.budget.status = "unknown"
    elif fault == "rawfile":
        path = c.settings.artifact_store.local_dir / c.runs[1].response_payload_ref
        d = json.loads(path.read_text())
        d["content"]["text"] += " "
        path.write_text(json.dumps(d))
    elif fault == "source":
        c.project.metadata_json = {**c.project.metadata_json, "book_design_snapshot_hash": "b" * 64}
    elif fault == "bad_plan":
        kwargs.update(execute=True, expected_plan_sha256="0" * 64)
    else:
        state = deepcopy(c.wf.metadata_json)
        state["patch_attempts"][1]["completion"]["content"] += " "
        c.wf.metadata_json = state
    await c.session.commit()
    before = await snapshot(c)
    with pytest.raises((ValueError, KeyError)):
        await invoke(c, **kwargs)
    assert await snapshot(c) == before


@pytest.mark.parametrize("field", ["candidate", "attempt", "receipt", "drop_ref", "drop_run"])
async def test_proposed_lineage_fails_closed_on_all_derived_tampering(
    paid_case, monkeypatch, field
):
    c = paid_case
    plan = await invoke(c)
    await invoke(c, execute=True, expected_plan_sha256=plan["plan_sha256"])
    await c.session.commit()
    state = deepcopy(c.wf.metadata_json)
    if field == "candidate":
        state["candidates"][-1]["candidate"]["chapters"][0]["goal"] = "changed"
    elif field == "attempt":
        state["patch_attempts"][1]["status"] = "validated"
    elif field == "receipt":
        state["paid_proposal_migrations"][0]["new_patch_rounds"] = 1
    elif field == "drop_ref":
        state["candidates"][-1].pop("derived_proposal")
    else:
        state["candidates"][-1].pop("patch_llm_run_id")
    assert proposed(monkeypatch).prefix_json_lineage_status(state)["status"] == "non_fresh"


async def test_normal_same_wf_rejudge_rejects_without_third_repair(paid_case, monkeypatch):
    c = paid_case
    plan = await invoke(c)
    await invoke(c, execute=True, expected_plan_sha256=plan["plan_sha256"])
    await c.session.commit()
    prefix = migration.prefix
    patched = proposed(monkeypatch)
    monkeypatch.setattr(prefix, "prefix_json_lineage_status", patched.prefix_json_lineage_status)
    calls = []

    async def judge(session, settings, **kwargs):
        assert kwargs["workflow_run_id"] == c.wf.id
        assert content_hash(kwargs["outline_payload"]) == plan["candidate_hash"]
        calls.append(kwargs)
        run = LlmRunModel(
            project_id=c.project.id,
            workflow_run_id=c.wf.id,
            logical_role="critic",
            provider="openai",
            model_name="offline-fixture",
            finish_reason="stop",
            prompt_template="outline_commercial_judge",
            prompt_version="fixture",
            prompt_hash="new-body-judge-fixture",
            metadata_json={},
        )
        session.add(run)
        await session.flush()
        return prefix.LLMQualityJudgeResult(
            scope="outline", passed=False, overall_score=0.5, llm_run_id=str(run.id)
        )

    monkeypatch.setattr(prefix, "judge_outline_commercial_readiness", judge)
    result = await prefix.approve_chapter_outline_prefix(
        c.session,
        c.settings,
        project=c.project,
        source_payload=c.state["source_payload"],
        through_chapter=1,
        max_repair_rounds=2,
        workflow_run_id=c.wf.id,
        allow_editorial_debt=False,
        requested_by="authorized-fixture",
    )
    assert len(calls) == 1 and not result["passed"] and not result["draft_eligible"]
    assert c.wf.metadata_json["phase"] == "rejected"
    assert c.wf.metadata_json["patch_attempts"] == c.state["patch_attempts"]
    assert len(c.wf.metadata_json["candidates"]) == 2
    assert c.budget.calls_used == 2 and c.budget.tokens_charged == 40


async def test_readonly_inspect_never_flushes_or_compiles_for_update(paid_case, monkeypatch):
    from sqlalchemy.dialects import postgresql

    c = paid_case
    scalar, scalars = c.session.scalar, c.session.scalars

    async def guard_scalar(statement, *args, **kwargs):
        assert "FOR UPDATE" not in str(statement.compile(dialect=postgresql.dialect()))
        return await scalar(statement, *args, **kwargs)

    async def guard_scalars(statement, *args, **kwargs):
        assert "FOR UPDATE" not in str(statement.compile(dialect=postgresql.dialect()))
        return await scalars(statement, *args, **kwargs)

    async def no_flush(*args, **kwargs):
        raise AssertionError("readonly inspect flushed")

    monkeypatch.setattr(c.session, "scalar", guard_scalar)
    monkeypatch.setattr(c.session, "scalars", guard_scalars)
    monkeypatch.setattr(c.session, "flush", no_flush)
    assert (await invoke(c))["status"] == "prepared"


async def test_unselected_original_paid_attempt_must_also_have_full_custody(paid_case):
    c = paid_case
    path = c.settings.artifact_store.local_dir / c.runs[0].response_payload_ref
    data = json.loads(path.read_text())
    data["content"]["text"] += " "
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="physical_response_content"):
        await invoke(c)


@pytest.mark.parametrize("paid_case", ["bad_late_target"], indirect=True)
async def test_full_paid_bad_last_target_rejected_without_partial_candidate(paid_case):
    c = paid_case
    before = await snapshot(c)
    with pytest.raises((ValueError, migration.planner.PlannerFallbackError)):
        await invoke(c)
    assert await snapshot(c) == before
    assert len(c.wf.metadata_json["candidates"]) == 1


async def test_extra_settled_attempt_is_not_hidden_by_selected_return(paid_case):
    c = paid_case
    execution = c.runs[1].metadata_json["execution_receipt"]
    row = ExecutionAttemptModel(
        id=uuid4().hex,
        operation_key=execution["operation_key"],
        scope_id=c.budget.scope_id,
        input_hash=c.runs[1].prompt_hash,
        status="completed",
        model_name=c.runs[1].model_name,
        reserved_tokens=12000,
        actual_tokens=20,
        created_at=datetime.now(UTC),
    )
    c.session.add(row)
    await c.session.commit()
    before = await snapshot(c)
    with pytest.raises(ValueError, match="paid_full_attempt_set"):
        await invoke(c)
    assert await snapshot(c) == before


async def test_actual_adopted_prefix_cannot_receive_derived_candidate(paid_case):
    c = paid_case
    chapter = ChapterModel(
        project_id=c.project.id, chapter_number=1, chapter_goal="Protected source", metadata_json={}
    )
    c.session.add(chapter)
    await c.session.flush()
    c.session.add(
        ChapterDraftVersionModel(
            project_id=c.project.id,
            chapter_id=chapter.id,
            version_no=1,
            content_md="Protected adopted body.",
            is_current=True,
            promotion_state="promoted",
        )
    )
    await c.session.commit()
    before = await snapshot(c)
    with pytest.raises(ValueError, match="prefix_adopted_range_immutable"):
        await invoke(c)
    assert await snapshot(c) == before


@pytest.mark.parametrize("alias", [False, True])
async def test_actual_current_source_bound_validator_is_readonly_and_only_exact_alias(alias):
    from test_source_bound_outline_integrity import source, project

    raw = source()
    if alias:
        raw["chapters"][1]["scenes"][0]["participants"] = ["M"]
    original = deepcopy(raw)
    p = project()
    metadata = deepcopy(p.metadata_json)
    cast = {
        "protagonist": {
            "name": "Mira",
            "role": "protagonist",
            "aliases": ["M"],
            "gender": "unknown",
        }
    }
    state = {"binding": {"volume_number": 1, "window": [1, 4]}, "chapter_numbers": [1, 2, 3, 4]}
    result, normalization = migration._validate_current_proposal(
        raw, project=p, state=state, cast=cast
    )
    expected = deepcopy(original)
    if alias:
        expected["chapters"][1]["scenes"][0]["participants"] = ["Mira"]
    assert result == expected and raw == original and p.metadata_json == metadata
    receipt = normalization["receipt"]
    assert normalization["mode"] == migration.integrity.POLICY
    assert len(receipt["aliases"]) == int(alias)
    assert (
        receipt["defaults_inserted"]
        is receipt["semantic_repair_performed"]
        is receipt["author_authority"]
        is False
    )
    assert result["chapters"][0]["unknown_authored"] == original["chapters"][0]["unknown_authored"]
    assert "hook_type" not in result["chapters"][0]


@pytest.mark.parametrize("fault", ["missing_time", "fuzzy_identity", "missing_cause"])
async def test_current_source_normalizer_does_not_repair_missing_or_unverified_source(fault):
    from test_source_bound_outline_integrity import source, project

    raw = source()
    if fault == "missing_time":
        del raw["chapters"][0]["scenes"][0]["time_label"]
    elif fault == "fuzzy_identity":
        raw["chapters"][0]["scenes"][0]["participants"] = ["Miraa"]
    else:
        del raw["chapters"][0]["causal_contract"]
    original = deepcopy(raw)
    p = project()
    metadata = deepcopy(p.metadata_json)
    state = {"binding": {"volume_number": 1, "window": [1, 4]}, "chapter_numbers": [1, 2, 3, 4]}
    with pytest.raises((ValueError, migration.planner.PlannerFallbackError)):
        migration._validate_current_proposal(
            raw,
            project=p,
            state=state,
            cast={"protagonist": {"name": "Mira", "aliases": ["M"], "gender": "unknown"}},
        )
    assert raw == original and p.metadata_json == metadata


def _order_fixture():
    candidate = {
        "chapters": [
            {
                "chapter_number": 1,
                "goal": "上 text",
                "n": 1,
                "later": {"first": False, "second": ["a", "b"]},
            }
        ],
        "unknown": {"x": 1.0, "y": None},
    }
    scope = {"approved_scope_only": [1], "full_window": [1, 8], "source_hash": "a" * 64}
    errors = [
        {
            "error": {"type": "OutlinePrefixReadinessError", "message": "capacity"},
            "candidate_hash": None,
        }
    ]
    prefix = 'immutable instructions\n已采用前文：null\nsummary exact\n\n[SOURCE AUTHORITY]\n{"canon": "full exact source"}\n[/SOURCE AUTHORITY]\n[EVALUATION SCOPE]\n'
    suffix = (
        "\n此前无效补丁诊断："
        + json.dumps(errors, ensure_ascii=False)
        + '\n返回 {"patches":[...]} exact footer'
    )
    user = (
        prefix
        + json.dumps(scope, ensure_ascii=False)
        + "\nfull scope footer\n[/EVALUATION SCOPE]\n当前候选："
        + json.dumps(candidate, ensure_ascii=False)
    )
    user += '\n来源约束诊断：{"exact": "diagnostics"}\n严格补丁协议：{"max_patches": 12}' + suffix
    return user


def _reverse_objects(value):
    if isinstance(value, dict):
        return {k: _reverse_objects(v) for k, v in reversed(list(value.items()))}
    if isinstance(value, list):
        return [_reverse_objects(v) for v in value]
    return value


def _replace_order_block(user, marker, transform):
    # Independent fixture parser: only locate the explicitly named test JSON block.
    start = user.index(marker) + len(marker)
    value, end = json.JSONDecoder().raw_decode(user[start:])
    raw = json.dumps(transform(value), ensure_ascii=False)
    return user[:start] + raw + user[start + end :]


async def test_prompt_order_adapter_three_complete_blocks_only():
    original = _order_fixture()
    current = original
    for marker in ("\n[EVALUATION SCOPE]\n", "\n当前候选：", "\n此前无效补丁诊断："):
        current = _replace_order_block(current, marker, _reverse_objects)
    proof = migration._prompt_object_order_proof(original, current)
    assert proof["schema"] == "prefix-prompt-object-order-proof.v1"
    assert proof["original_prompt_sha256"] == hashlib.sha256(original.encode()).hexdigest()
    assert proof["current_prompt_sha256"] == hashlib.sha256(current.encode()).hexdigest()
    assert proof["input_bytes_equal"] is False and proof["outside_bytes_equal"] is True
    assert proof["full_source_authority_bytes_equal"] and proof["untrusted_proposal_only"]
    assert (
        not proof["old_request_cache_eligible"]
        and not proof["historical_effective_input_hash_rewritten"]
    )
    assert set(proof["blocks"]) == {
        "candidate",
        "evaluation_scope_json",
        "prior_invalid_diagnostics",
    }
    assert all(
        p["original_canonical_sha256"] == p["current_canonical_sha256"]
        for p in proof["blocks"].values()
    )
    assert migration._prompt_object_order_proof(original, original) is None


@pytest.mark.parametrize(
    "fault",
    [
        "late_object",
        "list_order",
        "string",
        "int_to_float",
        "int_to_bool",
        "scope_value",
        "duplicate",
        "unicode_escape",
        "numeric_lexeme",
        "whitespace",
        "nonfinite",
        "extra_marker",
        "outside",
        "source_authority",
        "summary",
        "protocol",
    ],
)
async def test_prompt_order_adapter_never_masks_any_other_change(fault):
    expected = _order_fixture()
    original = expected
    if fault in {"late_object", "list_order", "string", "int_to_float", "int_to_bool"}:

        def change(value):
            chapter = value["chapters"][0]
            if fault == "late_object":
                chapter["later"]["first"] = True
            elif fault == "list_order":
                chapter["later"]["second"].reverse()
            elif fault == "string":
                chapter["goal"] += " "
            elif fault == "int_to_float":
                chapter["n"] = 1.0
            else:
                chapter["n"] = True
            return value

        original = _replace_order_block(original, "\n当前候选：", change)
    elif fault == "scope_value":
        original = _replace_order_block(
            original, "\n[EVALUATION SCOPE]\n", lambda v: {**v, "approved_scope_only": [1, 2]}
        )
    elif fault == "duplicate":
        original = original.replace('"n": 1,', '"n": 1, "n": 1,', 1)
    elif fault == "unicode_escape":
        original = original.replace("上", "\\u4e0a", 1)
    elif fault == "numeric_lexeme":
        original = original.replace('"n": 1,', '"n": 1e0,', 1)
    elif fault == "whitespace":
        original = original.replace('"n": 1,', '"n":1,', 1)
    elif fault == "nonfinite":
        original = original.replace('"n": 1,', '"n": NaN,', 1)
    elif fault == "extra_marker":
        original += "\n当前候选：{}"
    elif fault == "outside":
        original += "extra"
    elif fault == "source_authority":
        original = original.replace("full exact source", "another source")
    elif fault == "summary":
        original = original.replace("summary exact", "summary changed")
    else:
        original = original.replace('"max_patches": 12', '"max_patches": 13')
    with pytest.raises(ValueError):
        migration._prompt_object_order_proof(original, expected)


async def test_actual_sqlite_jsonb_order_only_preserves_original_physical_and_spent_rows(paid_case):
    c = paid_case
    state = deepcopy(c.wf.metadata_json)
    state["candidates"][0]["candidate"] = _reverse_objects(state["candidates"][0]["candidate"])
    state["phase_scope"] = _reverse_objects(state["phase_scope"])
    state["patch_attempts"][0]["error"] = _reverse_objects(state["patch_attempts"][0]["error"])
    c.wf.metadata_json = state
    # SQLite has no JSONB normalization; force the order-only storage roundtrip.
    from sqlalchemy.orm.attributes import flag_modified

    flag_modified(c.wf, "metadata_json")
    await c.session.commit()
    before = await snapshot(c)
    files = {p: p.read_bytes() for p in c.settings.artifact_store.local_dir.rglob("*.json")}
    plan = await invoke(c)
    for paid_proof in plan["historical_paid_proofs"].values():
        order = paid_proof["prompt_object_order_proof"]
        assert order["schema"] == "prefix-prompt-object-order-proof.v1"
        assert order["original_prompt_sha256"] != order["current_prompt_sha256"]
        assert (
            order["outside_bytes_equal"] and not order["historical_effective_input_hash_rewritten"]
        )
    await invoke(c, execute=True, expected_plan_sha256=plan["plan_sha256"])
    await c.session.commit()
    after = await snapshot(c)
    for table in before:
        if table != "workflow_runs":
            assert after[table] == before[table]
    assert c.wf.metadata_json["patch_attempts"] == state["patch_attempts"]
    assert all(p.read_bytes() == raw for p, raw in files.items())
    assert (await invoke(c, execute=True))["status"] == "already_applied"


async def test_literal_equal_history_proof_shape_remains_unchanged(paid_case):
    plan = await invoke(paid_case)
    assert "prompt_object_order_proof" not in plan["physical"]
    assert all(
        "prompt_object_order_proof" not in p for p in plan["historical_paid_proofs"].values()
    )
