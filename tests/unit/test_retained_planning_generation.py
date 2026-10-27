"""Real isolated SQLite + physical receipts; all provider entrypoints denied."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import JSON, MetaData, delete, event
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.schema import CreateTable

from bestseller.infra.db.models import (
    ExecutionAttemptModel,
    ExecutionBudgetModel,
    ExecutionOperationModel,
    LlmRunModel,
    PlanningArtifactVersionModel,
    ProjectModel,
    WorkflowRunModel,
)
from bestseller.services import llm, model_catalog
from bestseller.services import retained_planning_generation as reader
from bestseller.services import retained_review_input_proof as retained
from bestseller.services.conception_snapshots import snapshot_hash
from bestseller.services.source_bound_planning_content import (
    PLANNING_PROMPT_VERSION,
    build_source_bound_planning_prompts,
    validate_source_bound_planning_payload,
)
from bestseller.services.source_bound_planning_submission import (
    parse_planning_submission,
    planning_submission_tool,
)
from bestseller.settings import load_settings

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]
MODELS = (
    ProjectModel,
    PlanningArtifactVersionModel,
    WorkflowRunModel,
    LlmRunModel,
    ExecutionBudgetModel,
    ExecutionOperationModel,
    ExecutionAttemptModel,
)


@pytest.fixture
async def case(tmp_path, monkeypatch, request):
    kind = getattr(request, "param", "world_spec")
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'retained_planning_test.sqlite3'}"
    )

    def ddl(connection):
        for model in MODELS:
            table = model.__table__.to_metadata(MetaData())
            for column in table.c:
                if isinstance(column.type, JSONB):
                    column.type = JSON()
                if column.server_default and "::jsonb" in str(column.server_default.arg):
                    column.server_default = None
            connection.execute(CreateTable(table, include_foreign_key_constraints=[]))

    async with engine.begin() as connection:
        await connection.run_sync(ddl)
    writes, denied = [], []

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def sql(_connection, _cursor, statement, *_):
        if statement.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")):
            writes.append(statement)

    async def provider_denied(*args, **kwargs):
        denied.append(True)
        raise AssertionError("provider forbidden")

    monkeypatch.setattr(llm, "complete_text", provider_denied)
    monkeypatch.setattr(llm, "_complete_text_impl", provider_denied)
    import litellm

    monkeypatch.setattr(litellm, "acompletion", provider_denied)
    if hasattr(llm, "acompletion"):
        monkeypatch.setattr(llm, "acompletion", provider_denied)
    settings = load_settings().model_copy(deep=True)
    settings.llm.mock = False
    settings.artifact_store.mode = "local"
    settings.artifact_store.local_dir = str(tmp_path / "physical")
    settings.artifact_store.retain_prompt_payloads = True
    entry = model_catalog.ModelCatalogEntry(
        id="retained-planner",
        display_name="inert",
        vendor="fixture",
        model="openai/retained-fixture",
        api_base="https://fixture.invalid/v1",
    )
    catalog_path = tmp_path / "catalog.json"
    catalog_path.write_text(json.dumps({"models": [entry.model_dump()]}))
    monkeypatch.setattr(model_catalog, "_CATALOG_PATH", catalog_path)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        project = ProjectModel(
            id=uuid4(),
            slug="observatory-retained",
            title="Observatory",
            genre="mystery",
            language="en",
            target_chapters=30,
            target_word_count=78000,
            metadata_json={},
        )
        scope = "conception:" + str(uuid4())
        canonical = PlanningArtifactVersionModel(
            id=uuid4(),
            project_id=project.id,
            artifact_type="conception_snapshot",
            status="canonical",
            version_no=1,
            schema_version="v1",
            content={
                "protagonist": {"name": "Mara"},
                "premise": "Scientists inspect measurement errors",
            },
        )
        binding = {
            "id": str(canonical.id),
            "content_hash": snapshot_hash(canonical.content),
            "content": deepcopy(canonical.content),
        }
        project.metadata_json = {
            "execution_scope_id": scope,
            "llm_model_id": entry.id,
            "conception_approved": True,
            "creation_canonical_snapshot_id": str(canonical.id),
            "creation_canonical_snapshot_hash": binding["content_hash"],
        }
        project._source_bound_planning_canonical = binding
        original = WorkflowRunModel(
            id=uuid4(),
            project_id=project.id,
            workflow_type="generate_foundation_plan",
            status="failed",
            current_step="generate_world_spec",
            error_message="later semantic request capacity",
            metadata_json={"execution_scope_id": scope},
        )
        consumer = WorkflowRunModel(
            id=uuid4(),
            project_id=project.id,
            workflow_type="generate_foundation_plan",
            status="running",
            metadata_json={"execution_scope_id": scope},
        )
        projection = (
            [{"volume_number": 1, "chapter_count_target": 30}] if kind == "volume_plan" else {}
        )
        system, user = build_source_bound_planning_prompts(
            kind=kind,
            project=project,
            premise="Scientists inspect measurement errors",
            projection=projection,
            book_spec={"theme_statement": "Publish the error", "protagonist": {"name": "Mara"}},
            world_spec={"world_name": "Observatory", "world_premise": "Scientists inspect errors"},
            cast_spec={"protagonist": {"name": "Mara", "goal": "Find the error"}},
        )
        user = user.rstrip() + (
            "\nIf the provider offers the supplied structured function, "
            "prefer exactly one call to that function; "
            "otherwise return one complete strict JSON object. Do not emit both formats.\n"
        )
        input_hash = __import__(
            "bestseller.services.planner", fromlist=["_planner_artifact_input_hash"]
        )._planner_artifact_input_hash(
            logical_name=kind, system_prompt=system, user_prompt=user, fallback_payload=projection
        )
        request = llm.LLMCompletionRequest(
            logical_role="planner",
            system_prompt=system,
            user_prompt=user,
            fallback_response="never used",
            project_id=project.id,
            workflow_run_id=original.id,
            prompt_template="planner_" + kind,
            prompt_version=PLANNING_PROMPT_VERSION,
            max_tokens_override=4096,
            tools=[planning_submission_tool(kind)],
            tool_choice={"type": "function", "function": {"name": "submit_" + kind}},
            metadata={"input_hash": input_hash, "artifact": kind, "fail_closed": True},
        )
        catalog, _ = retained._fresh_catalog()
        role = retained._fresh_role(settings, project, request, catalog)
        effective_hash = llm._effective_input_hash(request, role)
        run_id, operation_key, attempt_id = uuid4(), uuid4().hex, uuid4().hex
        payload = {
            "book_spec": {
                "logline": "An observer admits a measurement error",
                "theme_statement": "Publish the error",
                "dramatic_question": "Will they recheck it?",
                "protagonist": {"name": "Mara", "external_goal": "Recheck the error"},
                "series_engine": {"core_serial_engine": "Independent observations"},
            },
            "world_spec": {
                "world_name": "Observatory",
                "world_premise": "Scientists inspect measurement errors",
                "hard_rules": [],
            },
            "cast_spec": {"protagonist": {"name": "Mara", "goal": "Recheck the error"}},
            "volume_plan": [
                {
                    "volume_number": 1,
                    "volume_title": "Measurement season",
                    "volume_goal": "Recheck the error",
                    "volume_obstacle": "Cloud cover",
                    "volume_climax": "They publish the revised measurement",
                    "chapter_count_target": 30,
                }
            ],
        }[kind]
        inner = json.dumps(payload, ensure_ascii=False)
        arguments = (
            inner if kind in {"world_spec", "cast_spec"} else json.dumps({"asset_json": inner})
        )
        calls = [
            {
                "id": "physical-submission",
                "type": "function",
                "function": {"name": "submit_" + kind, "arguments": arguments},
            }
        ]
        raw = {"role": "assistant", "content": "", "tool_calls": deepcopy(calls)}
        execution = {
            "scope_id": scope,
            "operation_key": operation_key,
            "attempts": [
                {
                    "attempt_id": attempt_id,
                    "status": "completed",
                    "effective_input_hash": effective_hash,
                }
            ],
        }
        effective_receipt = {"schema": "effective-input.v1", "hash": effective_hash}
        paid = llm.LLMCompletionResult(
            content="",
            provider="openai",
            model_name=role.model,
            finish_reason="tool_calls",
            tool_calls=calls,
            raw_message=raw,
            input_tokens=13,
            output_tokens=17,
            metadata={
                "paid_call_origin": llm._paid_call_origin(request, effective_hash),
                "execution_receipt": execution,
                "effective_input_receipt": effective_receipt,
            },
        )
        _, _, submission = parse_planning_submission(paid, kind=kind)
        original.metadata_json = {
            **original.metadata_json,
            "planning_submissions": {str(run_id): submission},
        }
        with llm.bind_project_prompt_audit(project.id, reason="isolated fixture"):
            request_ref, response_ref, _, replayable = llm._persist_prompt_payloads(
                settings=settings,
                request=request,
                llm_run_id=run_id,
                configured_model=role.model,
                effective_model=role.model,
                provider="openai",
                effective_temperature=role.temperature,
                effective_max_tokens=4096,
                effective_timeout_seconds=role.timeout_seconds,
                effective_stream=role.stream,
                effective_thinking_type=llm._effective_thinking_type(role),
                effective_reasoning_effort=role.reasoning_effort,
                content="",
                tool_call_count=1,
                tool_calls=calls,
                raw_message=raw,
                finish_reason="tool_calls",
                fallback_used=False,
                fallback_source=None,
                input_tokens=13,
                output_tokens=17,
            )
        assert replayable
        run = LlmRunModel(
            id=run_id,
            project_id=project.id,
            workflow_run_id=original.id,
            logical_role="planner",
            provider="openai",
            model_name=role.model,
            prompt_template="planner_" + kind,
            prompt_version=PLANNING_PROMPT_VERSION,
            prompt_hash=effective_hash,
            finish_reason="tool_calls",
            input_tokens=13,
            output_tokens=17,
            request_payload_ref=request_ref,
            response_payload_ref=response_ref,
            metadata_json={
                "execution_receipt": execution,
                "effective_input_receipt": effective_receipt,
                "input_hash": input_hash,
            },
        )
        deadline = datetime.now(UTC) + timedelta(hours=1)
        budget = ExecutionBudgetModel(
            scope_id=scope,
            max_calls=100,
            max_tokens=100000,
            calls_used=1,
            tokens_charged=30,
            status="active",
            deadline_at=deadline,
            metadata_json={},
        )
        operation = ExecutionOperationModel(
            key=operation_key,
            scope_id=scope,
            input_hash=effective_hash,
            status="completed",
            owner="fixture",
            lease_until=deadline,
            result_json=paid.model_dump(mode="json"),
        )
        attempt = ExecutionAttemptModel(
            id=attempt_id,
            scope_id=scope,
            operation_key=operation_key,
            input_hash=effective_hash,
            model_name=role.model,
            status="completed",
            reserved_tokens=30,
            actual_tokens=30,
            created_at=datetime.now(UTC),
        )
        transport_key = hashlib.sha256(
            f"transport-result.v1\n{scope}\n{attempt_id}".encode()
        ).hexdigest()
        transport = ExecutionOperationModel(
            key=transport_key,
            scope_id=scope,
            input_hash=effective_hash,
            status="transport_result",
            owner="fixture",
            lease_until=deadline,
            result_json={
                "schema": "transport-result.v1",
                "attempt_id": attempt_id,
                "operation_key": operation_key,
                "input_hash": effective_hash,
                "result": paid.model_dump(mode="json"),
            },
        )
        session.add_all(
            [project, canonical, original, consumer, run, budget, operation, attempt, transport]
        )
        await session.commit()

        def validator(value):
            validate_source_bound_planning_payload(
                value, kind=kind, project=project, projection=projection
            )

        args = {
            "project": project,
            "kind": kind,
            "current_request": request.model_copy(
                deep=True, update={"workflow_run_id": consumer.id}
            ),
            "planner_input_hash": input_hash,
            "canonical_binding": binding,
            "validator": validator,
        }
        yield SimpleNamespace(
            session=session,
            engine=engine,
            settings=settings,
            project=project,
            canonical=canonical,
            original=original,
            consumer=consumer,
            run=run,
            budget=budget,
            operation=operation,
            attempt=attempt,
            transport=transport,
            args=args,
            payload=payload,
            submission=submission,
            paid=paid,
            writes=writes,
            denied=denied,
            catalog_path=catalog_path,
            paths={
                kind: Path(settings.artifact_store.local_dir) / ref
                for kind, ref in (("request", request_ref), ("response", response_ref))
            },
        )
    await engine.dispose()


async def snapshot(case):
    ro = reader._Reader(case.session)
    return {model.__tablename__: [vars(row) for row in await ro.rows(model)] for model in MODELS}


async def invoke(case, **overrides):
    return await reader.find_retained_planning_generation(
        case.session, case.settings, **{**case.args, **overrides}
    )


async def test_exact_failed_workflow_generation_survives_without_approved_asset(case):
    before = await snapshot(case)
    files = {key: path.read_bytes() for key, path in case.paths.items()}
    writes = len(case.writes)
    result = await invoke(case)
    assert isinstance(result, reader.RetainedPlanningGeneration)
    assert result.payload == case.payload and "_meta" not in result.payload
    assert result.original_workflow_run_id == case.original.id != case.consumer.id
    assert result.original_llm_run_id == result.completion.llm_run_id == case.run.id
    assert result.submission_receipt == case.submission
    assert result.proof["provider_dispatches"] == 0
    assert result.proof["grade_reused"] is result.proof["approval_asserted"] is False
    assert result.proof["semantic_review_required"] is result.proof["charges_retained"] is True
    assert case.original.status == "failed"
    assert not any(
        row["artifact_type"] == "world_spec" for row in before["planning_artifact_versions"]
    )
    assert await snapshot(case) == before
    assert len(case.writes) == writes and case.denied == []
    assert files == {key: path.read_bytes() for key, path in case.paths.items()}
    # Caller-owned copies cannot mutate retained rows or the next read.
    result.payload["world_name"] = "edited"
    result.submission_receipt["status"] = "edited"
    assert (await invoke(case)).payload == case.payload


async def test_dirty_caller_state_and_validator_mutation_are_preserved_without_flush(case):
    case.consumer.metadata_json = {"pending_caller_edit": True}
    case.budget.metadata_json = {"pending_caller_edit": True}
    writes = len(case.writes)

    def validator(value):
        value.clear()

    result = await invoke(case, validator=validator)
    assert result.payload == case.payload
    assert case.consumer.metadata_json == case.budget.metadata_json == {"pending_caller_edit": True}
    assert case.consumer in case.session.dirty and case.budget in case.session.dirty
    assert len(case.writes) == writes


@pytest.mark.parametrize(
    "fault",
    [
        "system",
        "user",
        "tools",
        "tool_choice",
        "tokens",
        "planner_hash",
        "canonical_content",
        "canonical_status",
        "canonical_selection",
        "owner_foreign",
        "owner_running",
        "owner_kind",
        "owner_scope",
        "missing_submission",
        "submission_hash",
        "wrong_llm_model",
        "telemetry_usage",
        "root_origin",
        "paid_hash_domain",
        "root_unknown",
        "attempt_unknown",
        "extra_attempt",
        "orphan_root",
        "telemetry_missing",
        "request_unreplayable",
        "request_truncated",
        "request_tools",
        "response_missing",
        "response_unreplayable",
        "response_truncated",
        "response_tools",
        "response_raw",
        "return_mismatch",
        "catalog_endpoint",
        "validator_reject",
        "ambiguous",
    ],
)
async def test_matching_evidence_failure_never_returns_cache_miss_or_dispatches(case, fault):
    args = deepcopy(
        {key: value for key, value in case.args.items() if key not in {"project", "validator"}}
    )
    if fault in {"system", "user", "tools", "tool_choice", "tokens"}:
        request = args["current_request"]
        if fault in {"system", "user"}:
            key = fault + "_prompt"
            setattr(request, key, getattr(request, key) + "\nchanged")
        elif fault == "tools":
            request.tools[0]["function"]["description"] += " changed"
        elif fault == "tool_choice":
            request.tool_choice = "auto"
        else:
            request.max_tokens_override += 1
    elif fault == "planner_hash":
        args["planner_input_hash"] = "f" * 64
    elif fault == "canonical_content":
        case.canonical.content = {"different": True}
    elif fault == "canonical_status":
        case.canonical.status = "superseded"
    elif fault == "canonical_selection":
        case.project.metadata_json = {
            **case.project.metadata_json,
            "creation_canonical_snapshot_id": str(uuid4()),
        }
    elif fault in {
        "owner_foreign",
        "owner_running",
        "owner_kind",
        "owner_scope",
        "missing_submission",
        "submission_hash",
    }:
        if fault == "owner_foreign":
            case.original.project_id = uuid4()
        elif fault == "owner_running":
            case.original.status = "running"
        elif fault == "owner_kind":
            case.original.workflow_type = "chapter_pipeline"
        else:
            meta = deepcopy(case.original.metadata_json)
            if fault == "owner_scope":
                meta["execution_scope_id"] = "foreign"
            elif fault == "missing_submission":
                meta["planning_submissions"] = {}
            else:
                meta["planning_submissions"][str(case.run.id)]["payload_sha256"] = "f" * 64
            case.original.metadata_json = meta
    elif fault == "wrong_llm_model":
        case.run.model_name = "openai/foreign-model"
    elif fault == "telemetry_usage":
        case.run.output_tokens += 1
    elif fault == "root_origin":
        value = deepcopy(case.operation.result_json)
        value["metadata"]["paid_call_origin"]["workflow_run_id"] = str(uuid4())
        case.operation.result_json = value
    elif fault == "paid_hash_domain":
        value = deepcopy(case.transport.result_json)
        value["result"]["metadata"]["effective_input_receipt"]["schema"] = "unknown"
        case.transport.result_json = value
    elif fault == "root_unknown":
        case.operation.status = "unknown"
    elif fault == "attempt_unknown":
        case.attempt.status = "unknown"
    elif fault == "extra_attempt":
        case.session.add(
            ExecutionAttemptModel(
                id=uuid4().hex,
                scope_id=case.budget.scope_id,
                operation_key=case.operation.key,
                input_hash=case.run.prompt_hash,
                model_name=case.run.model_name,
                status="reserved",
                reserved_tokens=20,
                created_at=datetime.now(UTC),
            )
        )
    elif fault == "orphan_root":
        case.session.add(
            ExecutionOperationModel(
                key=uuid4().hex,
                scope_id=case.budget.scope_id,
                input_hash=case.run.prompt_hash,
                status="pending",
                owner="other",
                lease_until=datetime.now(UTC),
            )
        )
    elif fault == "telemetry_missing":
        await case.session.delete(case.run)
    elif fault == "catalog_endpoint":
        catalog = json.loads(case.catalog_path.read_text())
        catalog["models"][0]["api_base"] = "https://changed.invalid/v1"
        case.catalog_path.write_text(json.dumps(catalog))
    elif fault == "validator_reject":
        args["validator"] = lambda _: False
    elif fault == "ambiguous":
        clone = {key: value for key, value in vars(case.run).items() if not key.startswith("_")}
        clone["id"] = uuid4()
        case.session.add(LlmRunModel(**deepcopy(clone)))
        case.original.metadata_json = {
            **case.original.metadata_json,
            "planning_submissions": {
                **case.original.metadata_json["planning_submissions"],
                str(clone["id"]): deepcopy(case.submission),
            },
        }
    elif fault == "response_missing":
        case.paths["response"].unlink()
    elif fault == "return_mismatch":
        value = deepcopy(case.transport.result_json)
        value["result"]["content"] = "different"
        case.transport.result_json = value
    else:
        kind = "request" if fault.startswith("request_") else "response"
        path = case.paths[kind]
        value = json.loads(path.read_text())
        if fault.endswith("unreplayable"):
            value["replayable"] = False
        elif fault == "request_truncated":
            value["messages"]["user"]["truncated"] = True
        elif fault == "request_tools":
            value["requested"]["tools"] = []
        elif fault == "response_truncated":
            value["content"]["truncated"] = True
        else:
            value["tool_calls" if fault == "response_tools" else "raw_message"] = None
        path.write_text(json.dumps(value))
    await case.session.commit()
    before = await snapshot(case)
    writes = len(case.writes)
    with pytest.raises(reader.RetainedPlanningGenerationError):
        await invoke(case, **args)
    assert await snapshot(case) == before
    assert len(case.writes) == writes and case.denied == []


async def test_true_absence_is_a_miss_but_explicit_missing_id_is_not(case):
    for model in (LlmRunModel, ExecutionAttemptModel, ExecutionOperationModel):
        await case.session.execute(delete(model))
    await case.session.commit()
    writes = len(case.writes)
    assert await invoke(case) is None
    with pytest.raises(
        reader.RetainedPlanningGenerationError, match="requested_generation_missing"
    ):
        await invoke(case, source_llm_run_id=uuid4())
    assert len(case.writes) == writes and case.denied == []


async def test_unrelated_scope_and_different_input_do_not_globally_block(case):
    for scope, input_hash in (
        ("foreign-scope", case.run.prompt_hash),
        (case.budget.scope_id, "f" * 64),
    ):
        case.session.add(
            ExecutionOperationModel(
                key=uuid4().hex,
                scope_id=scope,
                input_hash=input_hash,
                status="unknown",
                owner="unrelated",
                lease_until=datetime.now(UTC),
            )
        )
    await case.session.commit()
    assert (await invoke(case)).original_llm_run_id == case.run.id
    assert case.denied == []


async def test_files_replaced_during_validator_fail_closed(case):
    def validator(value):
        case.paths["response"].write_text(case.paths["response"].read_text() + "\n")

    with pytest.raises(reader.RetainedPlanningGenerationError, match="physical_files_changed"):
        await invoke(case, validator=validator)
    assert case.denied == []


async def test_route_replaced_during_validator_is_not_reused(case):
    def validator(value):
        catalog = json.loads(case.catalog_path.read_text())
        catalog["models"][0]["api_base"] = "https://replaced.invalid/v1"
        case.catalog_path.write_text(json.dumps(catalog))

    with pytest.raises(reader.RetainedPlanningGenerationError, match="route_changed_during_proof"):
        await invoke(case, validator=validator)
    assert case.denied == []


async def test_new_duplicate_telemetry_during_proof_is_not_silently_selected(case, monkeypatch):
    original_rows = reader._Reader.rows
    run_reads = []

    async def rows(self, model, *conditions):
        result = await original_rows(self, model, *conditions)
        if model is LlmRunModel:
            run_reads.append(True)
            if len(run_reads) == 2:
                # Concurrent external arrival, represented only in the read
                # snapshot: the helper itself must never write the row.
                duplicate = deepcopy(vars(result[0]))
                duplicate["id"] = uuid4()
                result.append(SimpleNamespace(**duplicate))
        return result

    monkeypatch.setattr(reader._Reader, "rows", rows)
    writes = len(case.writes)
    with pytest.raises(
        reader.RetainedPlanningGenerationError, match="evidence_changed_during_proof"
    ):
        await invoke(case)
    assert len(case.writes) == writes and case.denied == []


async def test_async_validator_is_not_silently_skipped(case):
    async def validator(value):
        return None

    with pytest.raises(
        reader.RetainedPlanningGenerationError, match="synchronous_validator_required"
    ):
        await invoke(case, validator=validator)
    assert case.denied == []


@pytest.mark.parametrize(
    "case", ["book_spec", "world_spec", "cast_spec", "volume_plan"], indirect=True
)
async def test_each_kind_retains_complete_original_payload(case):
    result = await invoke(case)
    assert result.payload == case.payload
    assert result.original_workflow_run_id == case.original.id
    assert result.proof["kind"] == case.args["kind"]
    assert case.denied == []
