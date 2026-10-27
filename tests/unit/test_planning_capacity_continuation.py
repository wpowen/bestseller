"""Real SQLite/gateway receipts for synthetic spent capacity failures; no network.

Only the transport driver is inert. Request retention, original financial
reservation/settlement, physical-return proof and the predecessor verifier run
through their real implementations. Reconstructed inputs are captured before
the original gateway calls, never invented from the clipped request logs.
"""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
import hashlib
import json
from pathlib import Path
import socket
from uuid import uuid4

import pytest
from sqlalchemy import event, inspect, select
from sqlalchemy.schema import CreateTable
from test_adopted_knowledge_lineage import _jsonb, _uuid_as_text  # noqa: F401
from test_planning_draft_admission import harness as paid_harness  # noqa: F401

from bestseller.infra.db import models as db
from bestseller.services import finite_book_budget as finite
from bestseller.services import llm
from bestseller.services import planning_capacity_continuation as service
from bestseller.services.budget import bind_execution_scope
from bestseller.services.prompt_source_policy import content_hash

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]

OLD_VERSION = "3.4-window-complete-repair"
MODEL = "openai/MiniMax-M3.1-Flash"
MARKER = "[FULL REVISION SOURCE CONTEXT]\n"
SOURCE_INTRO = service.LEGACY_SOURCE_CONTEXT_PREFIX
MODELS = (
    db.ProjectModel,
    db.WorkflowRunModel,
    db.BookProductionControlModel,
    db.LlmRunModel,
    db.ExecutionBudgetModel,
    db.ExecutionOperationModel,
    db.ExecutionAttemptModel,
)


def _encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _synthetic_sources(project_id):
    def bound(content):
        return {
            "artifact_id": str(uuid4()),
            "version": 1,
            "content_sha256": content_hash(content),
            "content": content,
        }

    candidate = {"chapters": [{"chapter_number": 1, "goal": "Repair a weather instrument"}]}
    return {
        "schema": "full-outline-revision-proposal.v1",
        "project_id": str(project_id),
        "approved_foundation": {
            "book_spec": bound({"author_requirement": "A kind observatory mystery"}),
            "world_spec": bound({"requirement": "Only mechanical instruments are available"}),
            "cast_spec": bound({"characters": [{"name": "Edda", "role": "apprentice"}]}),
            "volume_plan": bound([{"volume_number": 1, "chapter_count_target": 1}]),
        },
        "current_canonical": bound({"explicit_author_constraints": ["No magical weather"]}),
        "untrusted_retained_beat": {
            **bound({"beats": ["The pressure gauge disagrees with the anemometer"]}),
            "authority": "untrusted_proposal_only",
            "paid_source_proof_asserted": False,
        },
        "legacy_materialized_base": {
            "binding": {"artifact_id": str(uuid4()), "workflow_run_id": str(uuid4())},
            "candidate_payload": deepcopy(candidate),
            "paid_full_source_asserted": False,
        },
        "compiler_candidate": candidate,
        "compiler_receipt": {"grade_asserted": False},
        "readiness_diagnostics": {"blocking_findings": ["Explain the measurement discrepancy"]},
        "project_source_brief": "Keep the observer's cautious viewpoint.",
        "grade_asserted": False,
    }


@pytest.fixture
async def case(paid_harness, monkeypatch, tmp_path):  # noqa: F811
    h = paid_harness
    h.engine = h.session_factory.kw["bind"]
    assert h.engine.dialect.name == "sqlite" and "test" in h.engine.url.database
    h.settings.artifact_store.mode = "local"
    h.settings.artifact_store.local_dir = str(tmp_path / "physical")
    h.settings.artifact_store.retain_prompt_payloads = True
    h.settings.budget.execution_max_calls = 8
    h.settings.budget.execution_max_tokens = 2_000_000
    h.settings.llm.planner = h.settings.llm.planner.model_copy(
        update={
            "model": MODEL,
            "api_base": "https://api.minimaxi.com/v1",
            "model_override": None,
            "rate_limit_fallback_model": None,
            "max_tokens": 32768,
            "reasoning_effort": "medium",
            "stream": True,
        }
    )
    h.scope = f"project:{h.project.id}"
    h.finite_run_id = str(uuid4())
    h.project.target_word_count = 4000
    h.project.target_chapters = 1
    h.project.metadata_json = {"execution_scope_id": h.scope}
    h.context = _synthetic_sources(h.project.id)
    h.target = {
        "prompt_version": "3.5-source-complete-repair",
        "model": MODEL,
        "total_output_tokens": 32768,
        "reasoning_effort": "medium",
        "transport_contract_sha256": "d" * 64,
        "source_context_sha256": content_hash(h.context),
        "grade_asserted": False,
    }
    async with h.engine.begin() as connection:
        for model in (db.ProjectModel, db.WorkflowRunModel, db.BookProductionControlModel):
            ddl = str(CreateTable(model.__table__).compile(dialect=h.engine.dialect))
            await connection.exec_driver_sql(ddl.replace("::jsonb", ""))
    metadata = finite.install(
        {},
        run_id=h.finite_run_id,
        project_id=h.project.id,
        model=MODEL,
        endpoint=h.settings.llm.planner.api_base,
        caps={
            "calls": 8,
            "money_micro_cny": 2_000_000,
            "primary": 2,
            "transport_retry": 2,
            "hard_repair": 4,
        },
        counter_sha256="c" * 64,
        authorization_ref="synthetic-fixture-authority",
        explicit_approval=True,
        capacity_limit=196608,
    )
    async with h.session_factory() as session:
        session.add(h.project)
        session.add(
            db.WorkflowRunModel(
                id=h.workflow.id,
                project_id=h.project.id,
                workflow_type="propose_full_outline_revision",
                scope_type="planning_artifact",
                scope_id=uuid4(),
                status="failed",
                current_step="proposal_requires_recovery",
                error_message="Synthetic known capacity failure, retained without promotion",
                metadata_json={
                    "schema": "full-outline-revision-proposal.v1",
                    "phase": "terminal_error",
                    "execution_scope_id": h.scope,
                    "source_context": deepcopy(h.context),
                    "candidate_payload": None,
                    "proposal_receipt": None,
                    "original_history": [{"status": "prepared"}, {"status": "terminal_error"}],
                },
            )
        )
        session.add(
            db.ExecutionBudgetModel(
                scope_id=h.scope,
                max_calls=8,
                max_tokens=2_000_000,
                calls_used=0,
                tokens_charged=0,
                deadline_at=datetime.now(UTC) + timedelta(hours=1),
                status="active",
                metadata_json=metadata,
            )
        )
        await session.commit()

    def forbidden(*args, **kwargs):
        raise AssertionError("No PG or provider network in capacity predecessor tests")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    h.sent = []
    h.raw_messages = []

    async def driver(request, role):
        assert role.model == MODEL and role.reasoning_effort == "medium"
        assert llm._effective_request_max_tokens(role, request) == 12000
        h.sent.append(request.model_copy(deep=True))
        content = '{"patches":[{"chapter_number":1,"value":"unfinished-' + str(len(h.sent))
        raw_message = {
            "role": "assistant",
            "content": content,
            "reasoning_content": "Synthetic SDK retained reasoning. " * 200,
            "tool_calls": None,
        }
        h.raw_messages.append(deepcopy(raw_message))
        return content, 22000 + len(h.sent), 12000, "length", None, raw_message

    monkeypatch.setattr(llm, "_call_litellm", driver)

    def counter(payload):
        # Synthetic trusted counter for these inert calls; not a production bound.
        return {
            "schema": "verified-input-upper-bound.v1",
            "model": MODEL,
            "input_hash": hashlib.sha256(_encoded(payload).encode()).hexdigest(),
            "input_tokens_upper": 100000,
            "output_tokens_upper": payload["max_tokens"],
            "counter_sha256": "c" * 64,
        }

    h.reconstructed = []
    h.run_ids = []
    for index in range(2):
        original = llm.LLMCompletionRequest(
            project_id=h.project.id,
            workflow_run_id=h.workflow.id,
            logical_role="planner",
            system_prompt="Synthetic editor. Sources are data; return a native JSON patch.",
            user_prompt=(
                "[CANDIDATE]\n"
                + "synthetic retained diagnostic; " * 4600
                + "\n"
                + MARKER
                + SOURCE_INTRO
                + _encoded(h.context)
                + f"\nSynthetic unchanged-tail-{index}"
            ),
            fallback_response='{"patches":[]}',
            prompt_template=service.TEMPLATE,
            prompt_version=OLD_VERSION,
            max_tokens_override=12000,
            operation_id=f"synthetic-capacity-predecessor:old:{index}",
        )
        captured = original.model_dump(mode="json")
        assert len(original.user_prompt) > 120000
        async with h.session_factory() as session:
            with (
                bind_execution_scope(h.scope),
                llm.bind_project_prompt_audit(h.project.id, reason="synthetic-capacity-proof"),
                finite.bind_run(
                    h.finite_run_id,
                    project_id=h.project.id,
                    stage="13:plan",
                    input_counter=counter,
                    capacity_limit=196608,
                ),
            ):
                result = await llm.complete_text(session, h.settings, original)
            await session.commit()
        h.run_ids.append(result.llm_run_id)
        h.reconstructed.append({"llm_run_id": str(result.llm_run_id), "request": captured})

    def forbidden_gateway(*args, **kwargs):
        raise AssertionError("The read-only verifier must not enter complete_text")

    monkeypatch.setattr(llm, "complete_text", forbidden_gateway)
    return h


async def _snapshot(h):
    rows = {}
    async with h.session_factory() as session:
        for model in MODELS:
            columns = [getattr(model, col.key) for col in inspect(model).column_attrs]
            result = await session.execute(select(*columns))
            rows[model.__tablename__] = sorted(
                json.dumps(dict(row), sort_keys=True, default=str) for row in result.mappings()
            )
    files = {
        str(p.relative_to(h.settings.artifact_store.local_dir)): p.read_bytes()
        for p in Path(h.settings.artifact_store.local_dir).rglob("*.json")
    }
    return rows, files


async def _verify(h, **changes):
    arguments = {
        "project": h.project,
        "predecessor_workflow_id": h.workflow.id,
        "source_context": deepcopy(h.context),
        "reconstructed_requests": deepcopy(h.reconstructed),
        "target_contract": deepcopy(h.target),
    }
    arguments.update(changes)
    async with h.session_factory() as session:
        with bind_execution_scope(h.scope):
            return await service.verify_capacity_predecessor(session, h.settings, **arguments)


async def _reject_without_writes(h, match, **changes):
    before = await _snapshot(h)
    with pytest.raises(ValueError, match=match):
        await _verify(h, **changes)
    assert await _snapshot(h) == before
    assert len(h.sent) == 2


async def _run(h, index=0):
    async with h.session_factory() as session:
        return await session.get(db.LlmRunModel, h.run_ids[index])


async def _rewrite_file(h, kind, change, index=0):
    run = await _run(h, index)
    path = Path(h.settings.artifact_store.local_dir) / getattr(run, kind + "_payload_ref")
    value = json.loads(path.read_text())
    change(value)
    path.write_text(_encoded(value))


async def test_exact_two_original_capacity_returns_prove_without_fee_or_history_changes(case):
    h = case
    # Frozen original 3.4 preamble, not a test-only immediate-JSON contract.
    assert SOURCE_INTRO == (
        "按来源归因核对以下完整原始材料；只有显式作者字段可形成作者硬约束。"  # noqa: RUF001
        "已选规划是当前来源，候选与历史诊断不是新增作者授权。"  # noqa: RUF001
        "只修补可执行字段与真实因果/时序冲突，不添加模板化情节。\n"  # noqa: RUF001
    )
    before = await _snapshot(h)
    async with h.session_factory() as session:
        budget = await session.get(db.ExecutionBudgetModel, h.scope)
        finite_run = budget.metadata_json["finite_book_runs"][h.finite_run_id]
        assert budget.calls_used == finite_run["calls"] == 2
        assert budget.tokens_charged == 68003
        assert finite_run["spent_micro_cny"] == 140003
        assert finite_run["held_micro_cny"] == 0
        assert len(finite_run["attempts"]) == 2
        assert all(a["status"] == "completed" for a in finite_run["attempts"].values())
    for index, run_id in enumerate(h.run_ids):
        run = await _run(h, index)
        root = Path(h.settings.artifact_store.local_dir)
        request = json.loads((root / run.request_payload_ref).read_text())
        response = json.loads((root / run.response_payload_ref).read_text())
        assert str(run.id) == str(run_id)
        assert request["replayable"] is False
        assert request["replayability_reasons"] == ["prompt_truncated"]
        assert len(request["messages"]["user"]["text"]) == 120000
        assert request["messages"]["user"]["original_chars"] > 120000
        assert request["effective"]["max_tokens"] == 12000
        assert request["effective"]["reasoning_effort"] == "medium"
        assert response["replayable"] is True and response["finish_reason"] == "length"
        assert response["raw_message"] == h.raw_messages[index]
        assert response["content"]["text"] == h.raw_messages[index]["content"]
    first = await _verify(h)
    second = await _verify(h)
    assert first == second
    assert first["schema"] == service.SCHEMA
    assert first["original_scope"] == h.scope
    assert first["predecessor_workflow_id"] == str(h.workflow.id)
    assert {r["llm_run_id"] for r in first["spent_physical_attempts"]} == set(map(str, h.run_ids))
    assert first["old_attempts_consumed"] == 2
    for flag in ("original_logs_rewritten", "grade_asserted", "fee_credit_granted", "budget_reset"):
        assert first[flag] is False
    assert first["target_contract"] == h.target
    first["target_contract"]["model"] = "mutated derived receipt only"
    assert h.target["model"] == MODEL
    assert len(h.sent) == 2 and await _snapshot(h) == before


async def test_verifier_emits_only_read_statements_and_no_flush_or_locks(case):
    h = case
    statements = []

    def observed(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(h.engine.sync_engine, "before_cursor_execute", observed)
    try:
        await _verify(h)
    finally:
        event.remove(h.engine.sync_engine, "before_cursor_execute", observed)
    assert statements and all(s.lstrip().upper().startswith("SELECT") for s in statements)
    assert not any("FOR UPDATE" in s.upper() for s in statements)
    assert len(h.sent) == 2


@pytest.mark.parametrize(
    "mutation,match",
    [
        ("tail", "effective_route_mismatch"),
        ("prefix", "request_prefix_or_length_changed"),
        ("length", "request_prefix_or_length_changed"),
        ("project", "reconstructed_request_scope"),
        ("workflow", "reconstructed_request_scope"),
        ("cap", "reconstructed_request_scope"),
        ("temperature", "request_parameters"),
        ("tools", "reconstructed_request_scope"),
        ("messages", "reconstructed_request_scope"),
        ("direct_json", "original_source_context_preamble"),
        ("arbitrary_preamble", "original_source_context_preamble"),
        ("missing", "exact_spent_attempt_set"),
        ("duplicate", "reconstructed_run_set"),
        ("foreign_id", "reconstructed_run_set"),
    ],
)
async def test_untrusted_reconstructed_requests_fail_closed(case, mutation, match):
    requests = deepcopy(case.reconstructed)
    request = requests[0]["request"]
    if mutation == "tail":
        old = request["user_prompt"]
        request["user_prompt"] = old[:-1] + "X"
        assert request["user_prompt"][:120000] == old[:120000]
        assert len(request["user_prompt"]) == len(old)
    elif mutation == "prefix":
        request["user_prompt"] = "X" + request["user_prompt"][1:]
    elif mutation == "length":
        request["user_prompt"] += "X"
    elif mutation == "project":
        request["project_id"] = str(uuid4())
    elif mutation == "workflow":
        request["workflow_run_id"] = str(uuid4())
    elif mutation == "cap":
        request["max_tokens_override"] = 32768
    elif mutation == "temperature":
        request["temperature_override"] = 0.4
    elif mutation == "tools":
        request["tools"] = [{"type": "function", "function": {"name": "synthetic_submit"}}]
    elif mutation == "messages":
        request["messages_override"] = [{"role": "user", "content": request["user_prompt"]}]
    elif mutation == "direct_json":
        old = request["user_prompt"]
        request["user_prompt"] = old.replace(SOURCE_INTRO, "", 1) + " " * len(SOURCE_INTRO)
        assert len(request["user_prompt"]) == len(old)
        assert request["user_prompt"][:120000] == old[:120000]
    elif mutation == "arbitrary_preamble":
        request["user_prompt"] = request["user_prompt"].replace(
            SOURCE_INTRO, "X" * len(SOURCE_INTRO), 1
        )
    elif mutation == "missing":
        requests.pop()
    elif mutation == "duplicate":
        requests[1] = deepcopy(requests[0])
    else:
        requests[0]["llm_run_id"] = str(uuid4())
    await _reject_without_writes(case, match, reconstructed_requests=requests)


@pytest.mark.parametrize("mutation", ["row", "target", "fresh_role"])
async def test_model_changes_cannot_prove_the_original_physical_send(case, mutation):
    h = case
    if mutation == "row":
        async with h.session_factory() as session:
            run = await session.get(db.LlmRunModel, h.run_ids[0])
            run.model_name = "openai/other-synthetic-model"
            await session.commit()
    elif mutation == "target":
        h.target["model"] = "openai/other-synthetic-model"
    else:
        h.settings.llm.planner.model = "openai/other-synthetic-model"
    await _reject_without_writes(h, "model_family_changed|effective_route_mismatch")


@pytest.mark.parametrize("mutation", ["project_scope", "owner_scope", "paid_scope"])
async def test_original_scope_is_required_at_every_binding(case, mutation):
    h = case
    async with h.session_factory() as session:
        if mutation == "project_scope":
            row = await session.get(db.ProjectModel, h.project.id)
            row.metadata_json = {**row.metadata_json, "execution_scope_id": f"project:{uuid4()}"}
        elif mutation == "owner_scope":
            row = await session.get(db.WorkflowRunModel, h.workflow.id)
            row.metadata_json = {**row.metadata_json, "execution_scope_id": f"project:{uuid4()}"}
        else:
            row = await session.get(db.LlmRunModel, h.run_ids[0])
            meta = deepcopy(row.metadata_json)
            meta["execution_receipt"]["scope_id"] = f"project:{uuid4()}"
            row.metadata_json = meta
        await session.commit()
    await _reject_without_writes(
        h, "original_scope_required|known_unusable_owner|paid_scope_mismatch"
    )


@pytest.mark.parametrize("mutation", ["current_source", "owner_and_current_source"])
async def test_source_or_reconstructed_context_conflicts_are_not_rebound(case, mutation):
    h = case
    context = deepcopy(h.context)
    context["approved_foundation"]["world_spec"]["content"]["requirement"] = "Different physics"
    if mutation == "owner_and_current_source":
        async with h.session_factory() as session:
            owner = await session.get(db.WorkflowRunModel, h.workflow.id)
            owner.metadata_json = {**owner.metadata_json, "source_context": context}
            await session.commit()
    await _reject_without_writes(
        h, "source_family_changed|reconstructed_source_context", source_context=context
    )


@pytest.mark.parametrize("mutation", ["pending", "candidate", "receipt", "foreign_owner"])
async def test_predecessor_must_be_terminal_failed_and_have_no_eligible_candidate(case, mutation):
    h = case
    async with h.session_factory() as session:
        owner = await session.get(db.WorkflowRunModel, h.workflow.id)
        if mutation == "pending":
            owner.status = "pending"
        elif mutation == "foreign_owner":
            owner.project_id = uuid4()
        else:
            key = "candidate_payload" if mutation == "candidate" else "proposal_receipt"
            owner.metadata_json = {**owner.metadata_json, key: {"available": True}}
        await session.commit()
    await _reject_without_writes(h, "owner|known_unusable_owner")


@pytest.mark.parametrize("mutation", ["run", "transport", "response"])
async def test_false_or_other_finish_cannot_be_a_completed_capacity_failure(case, mutation):
    h = case
    if mutation == "response":
        await _rewrite_file(h, "response", lambda value: value.update(finish_reason=False))
    else:
        async with h.session_factory() as session:
            run = await session.get(db.LlmRunModel, h.run_ids[0])
            if mutation == "run":
                run.finish_reason = "stop"
            else:
                key = run.metadata_json["execution_receipt"]["operation_key"]
                operation = await session.get(db.ExecutionOperationModel, key)
                returned = deepcopy(operation.result_json)
                returned["finish_reason"] = False
                operation.result_json = returned
            await session.commit()
    await _reject_without_writes(
        h,
        "known_capacity_failure|physical_response_field|paid_return_mismatch|"
        "paid_result_mismatch|finish_reason",
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_attempt",
        "extra_attempt",
        "usage",
        "attempt_pending",
        "operation_unknown",
        "missing_transport",
    ],
)
async def test_exact_actual_attempt_set_and_charged_closure_are_required(case, mutation):
    h = case
    async with h.session_factory() as session:
        run = await session.get(db.LlmRunModel, h.run_ids[0])
        key = run.metadata_json["execution_receipt"]["operation_key"]
        attempt = await session.scalar(
            select(db.ExecutionAttemptModel).where(db.ExecutionAttemptModel.operation_key == key)
        )
        if mutation == "missing_attempt":
            await session.delete(attempt)
        elif mutation == "extra_attempt":
            duplicate = {
                col.key: getattr(attempt, col.key)
                for col in inspect(db.ExecutionAttemptModel).column_attrs
            }
            duplicate["id"] = uuid4().hex
            # Another operation with the same original input must still be found.
            other_key = hashlib.sha256(b"synthetic-extra-operation").hexdigest()
            session.add(
                db.ExecutionOperationModel(
                    key=other_key,
                    scope_id=h.scope,
                    input_hash=attempt.input_hash,
                    status="completed",
                    owner="synthetic",
                    lease_until=datetime.now(UTC),
                    result_json={},
                )
            )
            duplicate["operation_key"] = other_key
            session.add(db.ExecutionAttemptModel(**duplicate))
        elif mutation == "usage":
            attempt.actual_tokens -= 1
        elif mutation == "attempt_pending":
            attempt.status = "reserved"
        elif mutation == "operation_unknown":
            operation = await session.get(db.ExecutionOperationModel, key)
            operation.status = "unknown"
        else:
            transport_key = hashlib.sha256(
                f"transport-result.v1\n{h.scope}\n{attempt.id}".encode()
            ).hexdigest()
            transport = await session.get(db.ExecutionOperationModel, transport_key)
            await session.delete(transport)
        await session.commit()
    await _reject_without_writes(
        h,
        "paid_attempt_mismatch|unlisted_matching_attempt|charged_closure|"
        "paid_operation_unavailable|paid_transport_missing",
    )


@pytest.mark.parametrize("mutation", ["prefix", "original_length", "response_raw", "response_text"])
async def test_original_headers_and_full_sdk_response_are_required(case, mutation):
    h = case
    if mutation == "prefix":

        def tamper(value):
            value["messages"]["user"]["text"] = "X" + value["messages"]["user"]["text"][1:]

        kind = "request"
    elif mutation == "original_length":

        def tamper(value):
            value["messages"]["user"]["original_chars"] -= 1

        kind = "request"
    elif mutation == "response_raw":

        def tamper(value):
            value["raw_message"]["reasoning_content"] += "forged tail"

        kind = "response"
    else:

        def tamper(value):
            value["content"]["text"] += "forged tail"

        kind = "response"
    await _rewrite_file(h, kind, tamper)
    await _reject_without_writes(
        h, "request_prefix_or_length_changed|physical_structured_return|physical_content"
    )


@pytest.mark.parametrize(
    "mutation",
    ["missing_entry", "unsettled", "zero_cost", "fee_minus_one", "zero_spent", "spent_minus_one"],
)
async def test_finite_money_journal_must_match_the_actual_old_paid_returns(case, mutation):
    h = case
    async with h.session_factory() as session:
        budget = await session.get(db.ExecutionBudgetModel, h.scope)
        meta = deepcopy(budget.metadata_json)
        journal = meta["finite_book_runs"][h.finite_run_id]
        old_run = await session.get(db.LlmRunModel, h.run_ids[0])
        attempt_id = old_run.metadata_json["execution_receipt"]["attempts"][0]["attempt_id"]
        if mutation == "missing_entry":
            del journal["attempts"][attempt_id]
        elif mutation == "unsettled":
            journal["attempts"][attempt_id]["status"] = "reserved"
        elif mutation == "zero_cost":
            journal["attempts"][attempt_id]["actual_micro_cny"] = 0
        elif mutation == "fee_minus_one":
            assert journal["attempts"][attempt_id]["actual_micro_cny"] == 70001
            journal["attempts"][attempt_id]["actual_micro_cny"] -= 1
        elif mutation == "zero_spent":
            journal["spent_micro_cny"] = 0
        else:
            assert journal["spent_micro_cny"] == 140003
            journal["spent_micro_cny"] -= 1
        budget.metadata_json = meta
        await session.commit()
    # Fixed error vocabulary belongs to the parent core; rejection is the contract.
    await _reject_without_writes(h, "finite|billing|fee|charged|accounting")


@pytest.mark.parametrize("field,expected", [("calls_used", 2), ("tokens_charged", 68003)])
async def test_scope_aggregate_cannot_understate_its_unchanged_actual_attempts(
    case, field, expected
):
    h = case
    async with h.session_factory() as session:
        budget = await session.get(db.ExecutionBudgetModel, h.scope)
        assert getattr(budget, field) == expected
        setattr(budget, field, expected - 1)
        await session.commit()
    await _reject_without_writes(h, "scope_total|budget|billing|fee|charged|accounting")
