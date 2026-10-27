"""Original native return witnesses and explicit full-charge closure on SQLite."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from hashlib import sha256
import json
import socket
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import event, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy.schema import CreateTable
from test_adopted_knowledge_lineage import _jsonb, _uuid_as_text  # noqa: F401

from bestseller.infra.db import models as db
from bestseller.services import known_terminal_planning_attempt as terminal
from bestseller.services import llm
from bestseller.services import planning_draft_admission as admission
from bestseller.services.budget import ExecutionLedger, ExecutionReconciliation
from bestseller.services.model_context_capacity import bind_selected_model_capacity
from bestseller.services.planning_admission_submission import planning_admission_tool
from bestseller.services.planning_review_transport import prepare_planning_review_transport
from bestseller.services.prompt_source_policy import content_hash
from bestseller.services.retained_planning_generation import _Reader
from bestseller.services.retained_review_input_proof import _fresh_catalog, _fresh_role
from bestseller.settings import LLMRoleSettings, load_settings

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]
MODELS = (
    db.ProjectModel,
    db.WorkflowRunModel,
    db.ExecutionBudgetModel,
    db.ExecutionOperationModel,
    db.ExecutionAttemptModel,
)


def key(domain, scope, identifier):
    return sha256(f"{domain}\n{scope}\n{identifier}".encode()).hexdigest()


def response_hash(raw):
    return sha256(
        json.dumps(raw, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()
    ).hexdigest()


def original_request(settings, project, workflow_id, supplied):
    """The dispatch construction before versioned output budgets were introduced."""
    tools = [
        planning_admission_tool(
            admission._Grade.model_json_schema(),
            supplied["projection_audit_targets"],
            projection_kind="chapters",
        )
    ]
    user = admission._admission_prompt(supplied, content_hash(supplied), [])
    with bind_selected_model_capacity(project.metadata_json):
        user, transport = prepare_planning_review_transport(
            admission.SYSTEM,
            user,
            data=json.loads(user),
            data_block=user,
            tools=tools,
            output_tokens=6000,
        )
        capacity = admission._input_capacity_receipt(
            user, tools, system=admission.SYSTEM, output_tokens=6000
        )
    request = llm.LLMCompletionRequest(
        logical_role="critic",
        project_id=project.id,
        workflow_run_id=workflow_id,
        operation_id=admission._protocol_operation(
            project, workflow_id, content_hash(supplied), [], 0
        ),
        system_prompt=admission.SYSTEM,
        user_prompt=user,
        prompt_template=admission.TEMPLATE,
        prompt_version="planning-draft-admission.v3",
        max_tokens_override=6000,
        tools=tools,
        tool_choice={"type": "function", "function": {"name": "submit_planning_admission"}},
        metadata={"planning_admission_input_receipt": capacity, "source_transport": transport},
        fallback_response="{}",
    )
    catalog, _ = _fresh_catalog()
    return request, _fresh_role(settings, project, request, catalog)


@pytest.fixture
async def terminal_case(tmp_path, monkeypatch):
    """Reusable actual-row fixture; inspect()/close()/verify() never send a model call."""
    import bestseller.settings as settings_module

    def forbidden(*args, **kwargs):
        raise AssertionError("terminal tests forbid provider/network")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(llm, "complete_text", forbidden)
    monkeypatch.setattr(llm, "_call_litellm", forbidden)
    monkeypatch.setattr(
        settings_module, "llm_runtime_profile_path", lambda _: tmp_path / "absent_profile.json"
    )
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'terminal_planning_test.sqlite3'}"
    )
    assert engine.url.drivername == "sqlite+aiosqlite" and "test" in engine.url.database
    async with engine.begin() as connection:
        for model in MODELS:
            ddl = str(CreateTable(model.__table__).compile(dialect=engine.dialect))
            await connection.exec_driver_sql(ddl.replace("::jsonb", ""))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        settings = load_settings(env={})
        settings.llm.mock = False
        settings.budget.execution_max_calls = 10
        settings.budget.execution_max_tokens = 20_000_000
        settings.budget.max_tokens_per_project = 0
        settings.llm.critic = LLMRoleSettings(
            model="openai/MiniMax-M3.1-Flash",
            api_base="https://api.minimaxi.com/v1",
            temperature=0.2,
            max_tokens=32768,
            timeout_seconds=180,
            reasoning_effort=None,
            stream=False,
        )
        scope = "project:" + str(uuid4())
        project = db.ProjectModel(
            slug="quay-account",
            title="The harbour account",
            genre="historical",
            target_chapters=4,
            target_word_count=8000,
            status="paused",
            metadata_json={"execution_scope_id": scope, "production_profile": "complete"},
        )
        session.add(project)
        await session.flush()
        candidate = {
            "chapters": [
                {
                    "chapter_number": 1,
                    "goal": "Record the brass seal",
                    "scenes": [
                        {"scene_number": 1, "action_sequence": ["Lena signs the cargo receipt"]}
                    ],
                }
            ]
        }
        workflow = db.WorkflowRunModel(
            project_id=project.id,
            workflow_type="materialized_outline_source_admission",
            status="failed",
            scope_type="planning_artifact",
            scope_id=uuid4(),
            current_step="execution_result_unknown",
            error_message="execution_result_unknown",
            metadata_json={"schema": "materialized-outline-source-admission.v1"},
        )
        session.add(workflow)
        await session.flush()
        supplied = {
            "schema": admission.SCHEMA,
            "project_id": str(project.id),
            "workflow_run_id": str(workflow.id),
            "hard_admission_policy": admission.HARD_ADMISSION_POLICY,
            "candidate_payload": candidate,
            "projection_audit_targets": admission.projection_audit_targets(candidate),
            "foundation": {},
            "approved_book_spec": {},
            "approved_world_spec": {},
            "approved_cast_spec": {},
            "approved_creation_contracts": {},
            "adopted_continuity": [],
        }
        workflow.metadata_json = {
            **workflow.metadata_json,
            "candidate_hash": content_hash(candidate),
            "protocol": {
                "graded_input_hash": content_hash(supplied),
                "hard_admission_policy": admission.HARD_ADMISSION_POLICY,
                "candidate_hash": content_hash(candidate),
                "attempts": [],
                "status": "pending",
            },
        }
        request, role = original_request(settings, project, workflow.id, supplied)
        effective = llm._effective_input_hash(request, role)
        root_key = sha256(f"{scope}\n{request.operation_id}".encode()).hexdigest()
        deadline = datetime.now(UTC) + timedelta(hours=2)
        budget = db.ExecutionBudgetModel(
            scope_id=scope,
            max_calls=10,
            max_tokens=20_000_000,
            calls_used=3,
            tokens_charged=1_006_222,
            deadline_at=deadline,
            status="unknown",
            metadata_json={"cancel_requested": False, "protected": "old control"},
        )
        root = db.ExecutionOperationModel(
            key=root_key,
            scope_id=scope,
            input_hash=effective,
            status="unknown",
            owner="old-dispatcher",
            lease_until=deadline,
            result_json=None,
        )
        attempt = db.ExecutionAttemptModel(
            id=uuid4().hex,
            scope_id=scope,
            operation_key=root_key,
            model_name=role.model,
            input_hash=effective,
            reserved_tokens=1_006_000,
            actual_tokens=None,
            status="unknown",
            created_at=datetime.now(UTC),
        )
        raw = {
            "id": "native-complete-fixture",
            "object": "chat.completion",
            "model": "MiniMax-M3.1-Flash",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "length",
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": None,
                        "function_call": None,
                        "reasoning_content": "Original reasoning evidence only. No grade." * 300,
                    },
                }
            ],
            "usage": {
                "prompt_tokens": 54665,
                "completion_tokens": 6000,
                "total_tokens": 60665,
                "completion_tokens_details": {"reasoning_tokens": 6000},
            },
        }
        provider = db.ExecutionOperationModel(
            key=key("provider-return.v1", scope, attempt.id),
            scope_id=scope,
            input_hash=effective,
            status="unusable_provider_return",
            owner=root.owner,
            lease_until=deadline,
            result_json={
                "schema": "provider-return.v1",
                "attempt_id": attempt.id,
                "operation_key": root_key,
                "input_hash": effective,
                "model_name": role.model,
                "wire_body_hash": "e" * 64,
                "response_sha256": response_hash(raw),
                "response": raw,
                "usable": False,
                "adoption_authorized": False,
                "credit_released": False,
            },
        )
        failure = db.ExecutionOperationModel(
            key=key("transport-failure.v1", scope, attempt.id),
            scope_id=scope,
            input_hash=effective,
            status="transport_failure_receipt",
            owner=root.owner,
            lease_until=deadline,
            result_json={
                "schema": "transport-failure.v1",
                "attempt_id": attempt.id,
                "operation_key": root_key,
                "physical_sends": 1,
                "stream": False,
                "operator_interruption": False,
                "partial_stream_observed": False,
                "exception_type": "UnusableProviderResponse",
                "http_status": None,
                "prompt_template": admission.TEMPLATE,
                "logical_role": "critic",
                "project_id": str(project.id),
                "workflow_run_id": str(workflow.id),
                "error_chain": [
                    {
                        "type": "ValueError",
                        "frames": [
                            {"function": "_parse_complete_provider_response", "lineno": 999}
                        ],
                    }
                ],
            },
        )
        session.add_all([budget, root, attempt, provider, failure])
        old = []
        for index in range(2):
            op = db.ExecutionOperationModel(
                key=content_hash(f"old-root-{index}"),
                scope_id=scope,
                input_hash=content_hash(f"old-input-{index}"),
                status="unknown",
                owner="old-owner",
                lease_until=deadline,
                result_json=None,
            )
            hold = db.ExecutionAttemptModel(
                id=uuid4().hex,
                scope_id=scope,
                operation_key=op.key,
                model_name="openai/old",
                input_hash=op.input_hash,
                reserved_tokens=111,
                actual_tokens=None,
                status="unknown",
                created_at=datetime.now(UTC),
            )
            old.extend([op, hold])
        session.add_all(old)
        await session.commit()
        case = SimpleNamespace(
            engine=engine,
            session=session,
            factory=factory,
            settings=settings,
            project=project,
            workflow=workflow,
            supplied=supplied,
            scope=scope,
            budget=budget,
            root=root,
            attempt=attempt,
            provider=provider,
            failure=failure,
            raw=raw,
            request=request,
            role=role,
            old=old,
        )

        async def inspect():
            return await terminal.inspect_terminal_planning_return(
                session, settings, project, workflow.id, supplied, attempt.id
            )

        async def verify(item, **kwargs):
            return await terminal.verify_terminal_planning_slot(
                session,
                settings,
                project=project,
                workflow_id=workflow.id,
                supplied=supplied,
                item=item,
                **kwargs,
            )

        async def close(item=None):
            item = deepcopy(await inspect() if item is None else item)
            evidence = ExecutionReconciliation(
                reconciliation_id=uuid4().hex,
                attempt_id=attempt.id,
                expected_input_hash=attempt.input_hash,
                decision="abandon_charged",
                evidence_kind="provider_receipt",
                evidence_sha256=item["evidence_sha256"],
                actor="SQLite operator",
                remote_operation_closed=True,
            )
            await session.commit()
            ledger = ExecutionLedger(engine, scope, settings.budget)
            receipt = await ledger.reconcile(evidence)
            item["reconciliation_id"] = evidence.reconciliation_id
            return item, receipt, evidence

        case.inspect, case.verify, case.close = inspect, verify, close
        yield case
    await engine.dispose()


async def snapshot(case):
    reader = _Reader(case.session)
    return {
        model.__tablename__: [terminal._row(row) for row in await reader.rows(model)]
        for model in MODELS
    }


async def test_original_request_and_inspect_are_readonly(terminal_case):
    c = terminal_case
    before = await snapshot(c)
    first, second = await c.inspect(), await c.inspect()
    assert first == second and await snapshot(c) == before
    rebuilt, role, _ = terminal._request(c.settings, c.project, c.workflow.id, c.supplied)
    assert rebuilt.model_dump(mode="json") == c.request.model_dump(mode="json")
    assert llm._effective_input_payload(rebuilt, role) == llm._effective_input_payload(
        c.request, c.role
    )
    assert rebuilt.max_tokens_override == 6000 and "planning_output_budget" not in rebuilt.metadata
    assert first["completion"]["llm_run_id"] is None
    assert first["completion"]["raw_message"] == c.raw["choices"][0]["message"]
    assert first["completion"]["content"] == "" and first["wire_reconstructed"] is False
    assert first["error"]["detail"] == "admission_incomplete_completion"
    assert admission.MAX_PROTOCOL_REPAIRS == 1  # Original slot0 leaves exactly one slot1.


async def test_fullcharge_closure_preserves_two_unknown_holds_and_is_idempotent(terminal_case):
    c = terminal_case
    before = await snapshot(c)
    item, receipt, evidence = await c.close()
    assert receipt["remaining_unknown"] == 2 and receipt["status"] == "unknown"
    assert receipt["credit_released"] is False and receipt["automatic_resume"] is False
    assert await c.verify(item) == await c.verify(item) == c.scope
    after = await snapshot(c)
    assert (
        before["projects"] == after["projects"]
        and before["workflow_runs"] == after["workflow_runs"]
    )
    for table in ("execution_attempts", "execution_operations"):
        for row in before[table]:
            if row.get("id") != c.attempt.id and row.get("key") != c.root.key:
                assert row in after[table]
    await c.session.rollback()
    assert (
        await ExecutionLedger(c.engine, c.scope, c.settings.budget).reconcile(evidence) == receipt
    )


async def test_later_honest_settled_slot_does_not_invalidate_spent_proof(terminal_case):
    c = terminal_case
    item, _, _ = await c.close()
    # Close the fixture's separate unknowns explicitly, without releasing credit,
    # so a later honest dispatch can exercise the real Ledger reserve/settle path.
    ledger = ExecutionLedger(c.engine, c.scope, c.settings.budget)
    for hold in c.old[1::2]:
        await ledger.reconcile(
            ExecutionReconciliation(
                reconciliation_id=uuid4().hex,
                attempt_id=hold.id,
                expected_input_hash=hold.input_hash,
                decision="abandon_charged",
                evidence_kind="provider_receipt",
                evidence_sha256="f" * 64,
                actor="separate fixture operator",
                remote_operation_closed=True,
            )
        )
    claim = await ledger.claim("honest-slot1", "honest-input")
    attempt_id = await ledger.reserve(claim, c.role.model, "honest-input", 200)
    await ledger.settle(attempt_id, "completed", actual_tokens=150)
    await ledger.finish(claim, {"content": "separate valid return"})
    assert await c.verify(item) == c.scope
    current = await _Reader(c.session).get(db.ExecutionBudgetModel, c.scope)
    assert current.calls_used == item["budget_before"]["calls_used"] + 1
    assert current.tokens_charged == item["budget_before"]["tokens_charged"] + 150


@pytest.mark.parametrize(
    "mutation", ["calls_reset", "tokens_decrease", "cap", "deadline", "cancel"]
)
async def test_current_financial_rewinds_and_control_changes_rejected(terminal_case, mutation):
    c = terminal_case
    item, _, _ = await c.close()
    before = item["budget_before"]
    updates = {
        "calls_reset": {"calls_used": 0},
        "tokens_decrease": {"tokens_charged": 1},
        "cap": {"max_calls": before["max_calls"] + 1},
        "deadline": {"deadline_at": datetime.now(UTC) + timedelta(days=1)},
        "cancel": {"metadata_json": {"cancel_requested": True}},
    }[mutation]
    await c.session.execute(
        update(db.ExecutionBudgetModel)
        .where(db.ExecutionBudgetModel.scope_id == c.scope)
        .values(**updates)
    )
    await c.session.commit()
    with pytest.raises(ValueError, match="financial_snapshot_changed"):
        await c.verify(item)


@pytest.mark.parametrize(
    "mutation",
    [
        "raw",
        "usage_bool",
        "usage_total",
        "usage_cap",
        "empty_reasoning",
        "content",
        "tools",
        "function",
        "finish",
        "wire",
        "sends_bool",
        "extra_attempt",
        "foreign_owner",
        "missing_root",
        "effective",
        "model",
        "scope",
        "failure_workflow",
        "provider_model",
        "usable",
    ],
)
async def test_physical_and_request_conflicts_rejected(terminal_case, mutation):
    c = terminal_case
    if mutation in {
        "raw",
        "usage_bool",
        "usage_total",
        "usage_cap",
        "empty_reasoning",
        "content",
        "tools",
        "function",
        "finish",
        "provider_model",
    }:
        record = deepcopy(c.provider.result_json)
        raw = record["response"]
        msg = raw["choices"][0]["message"]
        if mutation == "raw":
            msg["reasoning_content"] += "tampered"
        elif mutation == "usage_bool":
            raw["usage"]["prompt_tokens"] = True
        elif mutation == "usage_total":
            raw["usage"]["total_tokens"] += 1
        elif mutation == "usage_cap":
            raw["usage"]["completion_tokens"] = 8000
        elif mutation == "empty_reasoning":
            msg["reasoning_content"] = ""
        elif mutation == "content":
            msg["content"] = "not an empty native return"
        elif mutation == "tools":
            msg["tool_calls"] = [{"function": {"name": "submit_planning_admission"}}]
        elif mutation == "function":
            msg["function_call"] = {"name": "old function"}
        elif mutation == "finish":
            raw["choices"][0]["finish_reason"] = "stop"
        else:
            raw["model"] = "other"
        if mutation != "raw":
            record["response_sha256"] = response_hash(raw)
        c.provider.result_json = record
    elif mutation in {"wire", "usable"}:
        c.provider.result_json = {
            **c.provider.result_json,
            **({"wire_body_hash": "z" * 64} if mutation == "wire" else {"usable": True}),
        }
    elif mutation in {"sends_bool", "failure_workflow"}:
        c.failure.result_json = {
            **c.failure.result_json,
            **(
                {"physical_sends": True}
                if mutation == "sends_bool"
                else {"workflow_run_id": str(uuid4())}
            ),
        }
        # ORM equality treats True == 1; persist the actual malformed JSON type.
        flag_modified(c.failure, "result_json")
    elif mutation == "extra_attempt":
        c.session.add(
            db.ExecutionAttemptModel(
                id=uuid4().hex,
                operation_key=c.root.key,
                scope_id=c.scope,
                input_hash=c.attempt.input_hash,
                model_name=c.attempt.model_name,
                reserved_tokens=1,
                actual_tokens=None,
                status="unknown",
                created_at=datetime.now(UTC),
            )
        )
    elif mutation == "foreign_owner":
        c.provider.owner = "foreign"
    elif mutation == "missing_root":
        await c.session.delete(c.root)
    elif mutation == "effective":
        c.supplied["candidate_payload"]["chapters"][0]["goal"] = "Changed source"
    elif mutation == "model":
        c.settings.llm.critic = c.role.model_copy(update={"model": "openai/other"})
    elif mutation == "scope":
        c.project.metadata_json = {"execution_scope_id": "project:foreign"}
    await c.session.commit()
    with pytest.raises(ValueError, match="known_terminal_planning_attempt:"):
        await c.inspect()


@pytest.mark.parametrize(
    "mutation",
    [
        "completion",
        "run",
        "origin",
        "error",
        "index",
        "index_bool",
        "receipt_kind",
        "receipt_remote",
        "receipt_decision",
        "receipt_credit",
        "receipt_hash",
    ],
)
async def test_postclosure_tampering_rejected(terminal_case, mutation):
    c = terminal_case
    item, _, _ = await c.close()
    kwargs = {}
    if mutation in {"index", "index_bool"}:
        kwargs["index"] = 1 if mutation == "index" else True
    elif mutation.startswith("receipt_"):
        receipt_key = key("reconciliation.v1", c.scope, item["reconciliation_id"])
        row = await c.session.get(db.ExecutionOperationModel, receipt_key)
        record = deepcopy(row.result_json)
        if mutation == "receipt_kind":
            record["evidence_kind"] = "billing_record"
        elif mutation == "receipt_remote":
            record["remote_operation_closed"] = False
        elif mutation == "receipt_decision":
            record["receipt"]["decision"] = "recover_completed"
        elif mutation == "receipt_credit":
            record["receipt"]["credit_released"] = True
        else:
            record["evidence_sha256"] = "f" * 64
        row.result_json = record
        await c.session.commit()
    else:
        if mutation == "completion":
            item["completion"]["raw_message"]["reasoning_content"] += "tamper"
        elif mutation == "run":
            item["completion"]["llm_run_id"] = str(uuid4())
        elif mutation == "origin":
            item["completion"]["metadata"]["paid_call_origin"] = {"invented": True}
        else:
            item["error"]["detail"] = "invented grade"
        # Even an attacker recomputing the journal seal cannot change PG witnesses.
        item["evidence_sha256"] = terminal._hash(
            terminal._without(item, "evidence_sha256", "reconciliation_id")
        )
    with pytest.raises(ValueError, match="known_terminal_planning_attempt:"):
        await c.verify(item, **kwargs)


async def test_dirty_caller_is_not_flushed_or_refreshed(terminal_case):
    c = terminal_case
    original_title = c.project.title
    c.project.title = "Caller pending change"
    writes = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith(("UPDATE", "INSERT", "DELETE")):
            writes.append(statement)

    event.listen(c.engine.sync_engine, "before_cursor_execute", capture)
    try:
        await c.inspect()
        assert c.project.title == "Caller pending change" and c.project in c.session.dirty
        assert not writes
        with c.session.no_autoflush:
            saved = await c.session.scalar(
                select(db.ProjectModel.title).where(db.ProjectModel.id == c.project.id)
            )
    finally:
        event.remove(c.engine.sync_engine, "before_cursor_execute", capture)
    assert saved == original_title


async def test_record_empty_legacy_protocol_is_idempotent_and_preserves_owner(terminal_case):
    from bestseller.services.terminal_planning_recovery import record_terminal_planning_slot

    c = terminal_case
    c.workflow.metadata_json = {**c.workflow.metadata_json, "protocol": {}}
    await c.session.commit()
    item, _, _ = await c.close()
    before = await snapshot(c)
    arguments = {
        "project": c.project,
        "workflow_id": c.workflow.id,
        "supplied": c.supplied,
        "item": item,
    }
    receipt = await record_terminal_planning_slot(c.session, c.settings, **arguments)
    await c.session.commit()
    assert await record_terminal_planning_slot(c.session, c.settings, **arguments) == receipt
    assert receipt["spent_slot"] == 0 and receipt["protocol_attempt_limit"] == 2
    assert receipt["grade_granted"] is False and receipt["new_allowance_granted"] is False
    after = await snapshot(c)
    for table in ("projects", "execution_budgets", "execution_attempts", "execution_operations"):
        assert before[table] == after[table]
    workflow = await _Reader(c.session).get(db.WorkflowRunModel, c.workflow.id)
    assert workflow.status == "failed" and workflow.current_step == "execution_result_unknown"
    assert workflow.error_message == "execution_result_unknown"
    assert workflow.metadata_json["protocol"]["attempts"] == [item]


@pytest.mark.parametrize("mutation", ["occupied", "failed_owner", "candidate"])
async def test_record_rejects_occupied_or_changed_owner(terminal_case, mutation):
    from bestseller.services.terminal_planning_recovery import record_terminal_planning_slot

    c = terminal_case
    item, _, _ = await c.close()
    state = deepcopy(c.workflow.metadata_json)
    if mutation == "occupied":
        state["protocol"]["attempts"] = [{"old": "evidence"}]
    elif mutation == "failed_owner":
        c.workflow.status = "running"
    else:
        state["protocol"] = {}
        state["candidate_hash"] = "0" * 64
    c.workflow.metadata_json = state
    await c.session.commit()
    before = await snapshot(c)
    with pytest.raises(ValueError, match="terminal_planning_migration:"):
        await record_terminal_planning_slot(
            c.session,
            c.settings,
            project=c.project,
            workflow_id=c.workflow.id,
            supplied=c.supplied,
            item=item,
        )
    assert await snapshot(c) == before
