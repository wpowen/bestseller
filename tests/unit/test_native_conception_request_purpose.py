"""Native SQLite paid purpose proofs; only model wire IO is inert.

All eight exact routes use the actual complete_text gateway, physical files,
original NULL ownership, transport/root ledger and settlement validators.
"""

from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import UTC, datetime
import json
from pathlib import Path
import socket
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import JSON, ForeignKeyConstraint, MetaData, select, update
from sqlalchemy.dialects.postgresql import JSONB
from test_conception_logline_recovery import (
    authorize as authorize_logline,
)
from test_conception_logline_recovery import (
    recovered as _recovered_logline,
)
from test_conception_logline_recovery import (
    running_owner as logline_owner,
)
from test_execution_budget_resume import (
    case as logline_case,  # noqa: F401 -- separate native ledger fixture
)
from test_execution_budget_resume import (
    edit as edit_logline,
)
from test_execution_budget_resume import (
    state as logline_state,
)
from test_fanqie_beat_recovery import (
    case,  # noqa: F401 -- native isolated SQLite fixture
    protected,
)
from test_foundation_selection import store  # noqa: F401 -- dependency of case

from bestseller.infra.db.models import (
    ChapterModel,
    ExecutionAttemptModel,
    ExecutionBudgetModel,
    ExecutionOperationModel,
    LlmRunModel,
    ProjectModel,
    WorkflowRunModel,
)
from bestseller.services import commercial_judge_recovery as service
from bestseller.services import conception_logline_recovery as logline
from bestseller.services import llm
from bestseller.services.budget import bind_execution_scope
from bestseller.services.commercial_protocol_transition_runtime import _row
from bestseller.services.commercial_request_purpose import prove_known_noncommercial_request

NATIVE_COMPLETE_TEXT = llm.complete_text
ROUTES = (
    ("critic", "conception_source_effect_contract"),
    ("critic", "conception_explicit_seed_fidelity_judge"),
    ("critic", "conception_explicit_seed_fidelity_arbitration"),
    ("critic", "conception_final_material_seed_fidelity_judge"),
    ("critic", "conception_reconciled_seed_fidelity_judge"),
    ("planner", "conception_story_spine_polish"),
    ("editor", "concept_tournament_winner_seed_repair"),
    ("editor", "conception_explicit_seed_fidelity_repair"),
)
pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


def files(c):
    return {
        str(path): path.read_bytes()
        for path in Path(c.settings.artifact_store.local_dir).rglob("*")
        if path.is_file()
    }


async def snapshot(c):
    return await protected(c), files(c)


@pytest.fixture
async def native(case, monkeypatch, tmp_path):  # noqa: F811
    c = case
    assert c.session.bind.dialect.name == "sqlite"
    c.scope = "conception:" + str(uuid4())
    c.project.metadata_json = {**c.project.metadata_json, "execution_scope_id": c.scope}
    c.budget.scope_id = c.scope
    c.settings.output.base_dir = str(tmp_path / "native-purpose-test-output")
    for role in ("planner", "critic", "editor"):
        setattr(
            c.settings.llm,
            role,
            c.settings.llm.planner.model_copy(
                deep=True,
                update={
                    "model": "openai/native-conception-fixture",
                    "model_override": None,
                    "api_base": "https://native-conception-fixture.invalid/v1",
                    "stream": False,
                },
            ),
        )
    await c.session.commit()
    c.sent = []

    async def wire(request, role):
        c.sent.append(request.model_copy(deep=True))
        content = '{"candidate":{"label":"retained native proposal, no approval"}}'
        return content, 13, 7, "stop", None, {"role": "assistant", "content": content}

    monkeypatch.setattr(llm, "_call_litellm", wire)

    async def paid(role="critic", template="conception_source_effect_contract", *, version=None):
        owner = WorkflowRunModel(
            project_id=None,
            workflow_type="conception_initial",
            scope_type="creation",
            scope_id=None,
            status="running",
            metadata_json={"execution_scope_id": c.scope},
        )
        c.session.add(owner)
        await c.session.commit()
        request = llm.LLMCompletionRequest(
            project_id=None,
            workflow_run_id=owner.id,
            logical_role=role,
            prompt_template=template,
            prompt_version=version,
            system_prompt="Produce one pre-project conception proposal as JSON.",
            user_prompt='{"author_input":"An observatory; preserve its chosen participants."}',
            fallback_response="{}",
        )
        with bind_execution_scope(c.scope):
            result = await NATIVE_COMPLETE_TEXT(c.session, c.settings, request)
        owner.status = "failed"  # Actual pre-project owners may legitimately have failed later.
        owner.current_step = "retained_preproject_failure"
        await c.session.commit()
        run = await c.session.get(LlmRunModel, result.llm_run_id)
        root = await c.session.get(
            ExecutionOperationModel, run.metadata_json["execution_receipt"]["operation_key"]
        )
        attempts = list(
            await c.session.scalars(
                select(ExecutionAttemptModel).where(ExecutionAttemptModel.operation_key == root.key)
            )
        )
        transports = list(
            await c.session.scalars(
                select(ExecutionOperationModel).where(
                    ExecutionOperationModel.status == "transport_result",
                    ExecutionOperationModel.scope_id == c.scope,
                    ExecutionOperationModel.result_json["operation_key"].as_string() == root.key,
                )
            )
        )
        assert run.project_id is None and owner.project_id is None
        assert len(attempts) == len(transports) == 1
        assert root.status == attempts[0].status == "completed"
        assert run.request_payload_ref and run.response_payload_ref
        for reference in (run.request_payload_ref, run.response_payload_ref):
            retained = json.loads(
                (Path(c.settings.artifact_store.local_dir) / reference).read_text()
            )
            assert retained["project_id"] is None  # Native JSON null, never the string "None".
        return SimpleNamespace(
            run=run,
            owner=owner,
            root=root,
            attempts=attempts,
            transports=transports,
            request=request,
            result=result,
        )

    c.paid = paid
    return c


async def prove(c, paid, **overrides):
    args = {
        "project_id": c.project.id,
        "operation": paid.root,
        "actual_attempts": paid.attempts,
        "original_runs": [paid.run],
        "transport_results": paid.transports,
        **overrides,
    }
    with bind_execution_scope(c.scope):
        return await service.prove_native_conception_request(c.session, c.settings, **args)


@pytest.mark.parametrize("role,template", ROUTES)
async def test_all_eight_native_paid_projectless_routes_prove_without_mutation(
    native, role, template
):
    c = native
    paid = await c.paid(role, template)
    before = await snapshot(c)
    first = await prove(c, paid)
    assert isinstance(first, dict)
    assert first["schema"] == "native-conception-request-purpose.v1"
    assert first["operation_key"] == paid.root.key
    assert first["llm_run_id"] == str(paid.run.id)
    assert first["request_project_id"] is first["workflow_project_id"] is None
    assert first["scope_project_id"] == str(c.project.id)
    assert first["execution_scope_id"] == c.scope
    assert (first["logical_role"], first["prompt_template"], first["prompt_version"]) == (
        role,
        template,
        None,
    )
    assert first["provider_calls"] == first["database_writes"] == 0
    assert all(
        first[key] is False
        for key in (
            "new_credit_allowed",
            "dispatch_authorized",
            "grade_granted",
            "budget_reset_allowed",
            "history_rewritten",
        )
    )
    assert await prove(c, paid) == first
    assert await snapshot(c) == before and len(c.sent) == 1


@pytest.mark.parametrize(
    "role,template,version",
    [
        ("critic", "chapter_commercial_quality_judge", "v7"),
        ("critic", "conception_source_effect_contract_extra", None),
        ("critic", "conception_source_effect_contract", "v7"),
        ("editor", "conception_source_effect_contract", None),
        ("critic", "conception_explicit_seed_fidelity_judge", "v1"),
    ],
)
async def test_commercial_prefix_version_and_role_near_misses_remain_unproven(
    native,
    role,
    template,
    version,
):
    c = native
    paid = await c.paid(role, template, version=version)
    before = await snapshot(c)
    assert await prove(c, paid) is None
    assert await snapshot(c) == before


@pytest.mark.parametrize(
    "fault",
    [
        "public_project",
        "public_workflow",
        "wrong_workflow_type",
        "wrong_scope_type",
        "different_scope",
        "unknown_root",
        "non_stop",
        "missing_request",
        "missing_response",
        "raw_mismatch",
        "route_mismatch",
        "missing_attempt",
        "missing_run",
        "missing_transport",
        "literal_none_header",
        "request_tools",
        "effective_parameter_mismatch",
        "incomplete_effective_drift",
        "fee_mismatch",
        "origin_project",
        "origin_workflow",
        "stale_public_row",
    ],
)
async def test_native_proof_rejects_missing_conflicting_or_changed_physical_evidence(native, fault):
    c = native
    paid = await c.paid()
    assert isinstance(await prove(c, paid), dict)  # Reject the fault, not a broken baseline.
    overrides = {}
    if fault == "public_project":
        paid.run.project_id = c.project.id
    elif fault == "public_workflow":
        paid.owner.project_id = c.project.id
    elif fault == "wrong_workflow_type":
        paid.owner.workflow_type = "chapter_pipeline"
    elif fault == "wrong_scope_type":
        paid.owner.scope_type = "project"
    elif fault == "different_scope":
        paid.owner.metadata_json = {"execution_scope_id": "conception:" + str(uuid4())}
    elif fault == "unknown_root":
        paid.root.status = "unknown"
    elif fault == "non_stop":
        paid.run.finish_reason = "length"
    elif fault in {"missing_request", "missing_response"}:
        reference = getattr(
            paid.run,
            "request_payload_ref" if fault == "missing_request" else "response_payload_ref",
        )
        (Path(c.settings.artifact_store.local_dir) / reference).unlink()
    elif fault == "raw_mismatch":
        path = Path(c.settings.artifact_store.local_dir) / paid.run.response_payload_ref
        payload = json.loads(path.read_text())
        payload["content"]["text"] = '{"different":"physical provider content"}'
        path.write_text(json.dumps(payload))
    elif fault == "literal_none_header":
        path = Path(c.settings.artifact_store.local_dir) / paid.run.request_payload_ref
        payload = json.loads(path.read_text())
        payload["project_id"] = "None"
        path.write_text(json.dumps(payload))
    elif fault == "request_tools":
        path = Path(c.settings.artifact_store.local_dir) / paid.run.request_payload_ref
        payload = json.loads(path.read_text())
        payload["tools"] = [{"type": "function", "function": {"name": "submit_chapter_review"}}]
        path.write_text(json.dumps(payload))
    elif fault in {"effective_parameter_mismatch", "incomplete_effective_drift"}:
        path = Path(c.settings.artifact_store.local_dir) / paid.run.request_payload_ref
        payload = json.loads(path.read_text())
        if fault == "effective_parameter_mismatch":
            payload["effective"]["temperature"] += 0.1
        else:
            payload["effective"].pop("temperature")
            c.settings.llm.critic.temperature += 0.1
        path.write_text(json.dumps(payload))
    elif fault == "route_mismatch":
        c.settings.llm.critic.api_base = "https://different-route.invalid/v1"
    elif fault == "missing_attempt":
        overrides["actual_attempts"] = []
    elif fault == "missing_run":
        overrides["original_runs"] = []
    elif fault == "missing_transport":
        overrides["transport_results"] = []
    elif fault == "fee_mismatch":
        paid.attempts[0].actual_tokens += 1
    elif fault in {"origin_project", "origin_workflow"}:
        payload = deepcopy(paid.root.result_json)
        payload["metadata"]["paid_call_origin"][
            "project_id" if fault == "origin_project" else "workflow_run_id"
        ] = str(uuid4())
        paid.root.result_json = payload
    else:
        # Keep the supplied object stale; the helper must independently read
        # the actual owning SQL row rather than trust caller snapshots.
        async with c.factory() as other:
            await other.execute(
                update(LlmRunModel)
                .where(LlmRunModel.id == paid.run.id)
                .values(project_id=c.project.id)
            )
            await other.commit()
    await c.session.commit()
    before = await snapshot(c)
    assert await prove(c, paid, **overrides) is None
    assert await snapshot(c) == before and len(c.sent) == 1


async def test_complete_authenticated_original_effective_parameters_survive_global_defaults_drift(
    native,
):
    c = native
    paid = await c.paid()
    before = await snapshot(c)
    c.settings.llm.critic.temperature += 0.1
    result = await prove(c, paid)
    assert isinstance(result, dict)
    assert result["effective_input_sha256"] == paid.root.input_hash
    assert await snapshot(c) == before and len(c.sent) == 1


async def test_native_original_stream_true_and_max_effort_survive_current_default_false(native):
    c = native
    c.settings.llm.critic.stream = True
    c.settings.llm.critic.reasoning_effort = "max"
    paid = await c.paid()
    document = json.loads(
        (Path(c.settings.artifact_store.local_dir) / paid.run.request_payload_ref).read_text()
    )
    assert document["effective"]["stream"] is True
    assert document["effective"]["reasoning_effort"] == "max"
    c.settings.llm.critic.stream = False
    c.settings.llm.critic.reasoning_effort = None
    before = await snapshot(c)
    result = await prove(c, paid)
    assert isinstance(result, dict)
    assert result["effective_input_sha256"] == paid.root.input_hash
    assert await snapshot(c) == before and len(c.sent) == 1


async def test_future_project_model_override_is_not_original_projectless_role_authority(native):
    from bestseller.services.model_catalog import PROJECT_MODEL_ID_KEY

    c = native
    paid = await c.paid()
    c.project.metadata_json = {
        **c.project.metadata_json,
        PROJECT_MODEL_ID_KEY: "unavailable-future-project-model",
    }
    await c.session.commit()
    before = await snapshot(c)
    result = await prove(c, paid)
    assert isinstance(result, dict) and result["request_project_id"] is None
    assert await snapshot(c) == before and len(c.sent) == 1


@pytest.mark.parametrize(
    "fault",
    [
        "root_content",
        "transport_usage",
        "transport_attempt",
        "transport_hash",
        "root_effective_receipt",
        "transport_effective_receipt",
        "run_effective_receipt",
        "run_execution_receipt",
        "root_origin",
        "transport_nullable_origin",
        "missing_physical_return_receipt",
        "deleted_transport",
    ],
)
async def test_native_nullable_paid_chain_requires_matching_returns_and_complete_receipts(
    native, fault
):
    c = native
    paid = await c.paid()
    assert isinstance(await prove(c, paid), dict)
    root = deepcopy(paid.root.result_json)
    transport = deepcopy(paid.transports[0].result_json)
    if fault == "root_content":
        root["content"] = "different root content"
    elif fault == "transport_usage":
        transport["result"]["output_tokens"] += 1
    elif fault == "transport_attempt":
        transport["attempt_id"] = "unrelated-attempt"
    elif fault == "transport_hash":
        transport["input_hash"] = "0" * 64
    elif fault == "root_effective_receipt":
        root["metadata"].pop("effective_input_receipt")
    elif fault == "transport_effective_receipt":
        transport["result"]["metadata"].pop("effective_input_receipt")
    elif fault in {"run_effective_receipt", "run_execution_receipt"}:
        metadata = deepcopy(paid.run.metadata_json)
        metadata.pop(
            "effective_input_receipt" if fault == "run_effective_receipt" else "execution_receipt"
        )
        paid.run.metadata_json = metadata
    elif fault == "root_origin":
        root["metadata"].pop("paid_call_origin")
    elif fault == "transport_nullable_origin":
        transport["result"]["metadata"]["paid_call_origin"]["project_id"] = "None"
    elif fault == "missing_physical_return_receipt":
        path = Path(c.settings.artifact_store.local_dir) / paid.run.response_payload_ref
        document = json.loads(path.read_text())
        document["structured_payload_receipts"].pop("raw_message")
        path.write_text(json.dumps(document))
    else:
        await c.session.delete(paid.transports[0])
    paid.root.result_json = root
    if fault != "deleted_transport":
        paid.transports[0].result_json = transport
    await c.session.commit()
    before = await snapshot(c)
    assert await prove(c, paid) is None
    assert await snapshot(c) == before and len(c.sent) == 1


async def test_real_orphan_guard_accepts_all_eight_native_routes_and_rejects_physical_drift(native):
    c = native
    table = ChapterModel.__table__.to_metadata(MetaData())
    for column in table.c:
        if isinstance(column.type, JSONB):
            column.type = JSON()
        if column.server_default and "::" in str(column.server_default.arg):
            column.server_default = None
    for constraint in list(table.constraints):
        if isinstance(constraint, ForeignKeyConstraint):
            table.constraints.remove(constraint)
    async with c.session.bind.begin() as connection:
        await connection.run_sync(table.create)
    chapter = ChapterModel(
        project_id=c.project.id,
        chapter_number=1,
        chapter_goal="Observe the changing sky",
        metadata_json={},
    )
    c.session.add(chapter)
    await c.session.commit()
    paid = [await c.paid(role, template) for role, template in ROUTES]

    async def guard():
        with bind_execution_scope(c.scope):
            return await service._assert_no_unbound_commercial_spend(
                c.session,
                project_id=c.project.id,
                chapter_id=chapter.id,
                body_hash="a" * 64,
                scope=c.scope,
                settings=c.settings,
            )

    before = await snapshot(c)
    assert await guard() is None
    assert await guard() is None
    assert await snapshot(c) == before and len(c.sent) == 8
    path = Path(c.settings.artifact_store.local_dir) / paid[0].run.response_payload_ref
    document = json.loads(path.read_text())
    document["content"]["text"] = "changed physical content"
    path.write_text(json.dumps(document))
    before = await snapshot(c)
    with pytest.raises(ValueError, match="commercial_cohort_opaque_operation_requires_recovery"):
        await guard()
    assert await snapshot(c) == before and len(c.sent) == 8


@pytest.fixture
async def consumed_logline(logline_case, monkeypatch):  # noqa: F811
    # Reuse the real producer's native stream failure/quarantine/authorizer.
    # Its fixture deliberately retains an opaque effective hash; settle the
    # inert replacement through the native ledger, with complete telemetry.
    async with asynccontextmanager(_recovered_logline.__wrapped__)(logline_case) as c:

        def forbidden(*args, **kwargs):
            raise AssertionError("consumed-control proof cannot dispatch a model")

        monkeypatch.setattr(llm, "complete_text", forbidden)
        monkeypatch.setattr(llm, "_call_litellm", forbidden)
        monkeypatch.setattr(socket.socket, "connect", forbidden)
        monkeypatch.setattr(socket, "create_connection", forbidden)
        plan = await authorize_logline(c)
        wid = await logline_owner(c)
        with (
            bind_execution_scope(c.scope),
            llm.bind_conception_model("minimax-m3.1-flash", task_id=c.task_id, workflow_run_id=wid),
        ):
            async with c.factory() as session:
                _, request = await logline.prepare_logline_request(
                    session, c.settings, c.request.model_copy(update={"workflow_run_id": wid})
                )
            claim = await c.ledger.claim(request.operation_id, c.input_hash)
            aid = await c.ledger.reserve(
                claim,
                logline.MODEL,
                c.input_hash,
                100,
                endpoint=logline.ENDPOINT,
                workflow_id=str(wid),
                template=request.prompt_template,
                version=request.prompt_version,
                single_dispatch_id=request.metadata["single_dispatch_authorization_id"],
            )
            rid = uuid4()
            metadata = {
                "execution_receipt": {
                    "scope_id": c.scope,
                    "operation_key": claim.key,
                    "attempts": [
                        {
                            "attempt_id": aid,
                            "status": "completed",
                            "model": logline.MODEL,
                            "effective_input_hash": c.input_hash,
                        }
                    ],
                },
                "effective_input_receipt": {
                    "schema": "effective-input.v1",
                    "hash": c.input_hash,
                    "model": logline.MODEL,
                },
                "paid_call_origin": llm._paid_call_origin(request, c.input_hash),
            }
            result = llm.LLMCompletionResult(
                llm_run_id=rid,
                content="native inert retained replacement",
                provider="openai",
                model_name=logline.MODEL,
                finish_reason="stop",
                input_tokens=3,
                output_tokens=2,
                metadata=metadata,
            )
            await c.ledger.settle(
                aid,
                "completed",
                actual_tokens=5,
                claim=claim,
                result=result.model_dump(mode="json"),
            )
        c.pid = uuid4()
        async with c.factory() as session, session.begin():
            session.add(
                ProjectModel(
                    id=c.pid,
                    slug=plan["slug"],
                    title="Native consumed-control fixture",
                    genre="fantasy",
                    language="en",
                    target_chapters=4,
                    target_word_count=8000,
                    metadata_json={"execution_scope_id": c.scope},
                )
            )
            session.add(
                LlmRunModel(
                    id=rid,
                    project_id=None,
                    workflow_run_id=wid,
                    logical_role="critic",
                    prompt_template=request.prompt_template,
                    prompt_version=request.prompt_version,
                    provider="openai",
                    model_name=logline.MODEL,
                    prompt_hash=c.input_hash,
                    finish_reason="stop",
                    input_tokens=3,
                    output_tokens=2,
                    metadata_json=metadata,
                )
            )
        c.plan, c.replacement_attempt, c.replacement_run = plan, aid, rid
        c.control_key = logline._key(logline.SCHEMA, c.scope, plan["original_operation_key"])
        c.grant_id = request.metadata["single_dispatch_authorization_id"]
        # Establish that both native parent chains already satisfy the actual
        # pure library before testing the new control adapter's SQL boundary.
        async with c.factory() as session:
            for key, linked_run, returned, failed in (
                (c.claim.key, None, None, logline._key("transport-failure.v1", c.scope, c.aid)),
                (
                    plan["replacement_operation_key"],
                    rid,
                    logline._key("transport-result.v1", c.scope, aid),
                    None,
                ),
            ):
                parent = await session.get(ExecutionOperationModel, key)
                attempts = list(
                    await session.scalars(
                        select(ExecutionAttemptModel).where(
                            ExecutionAttemptModel.operation_key == key
                        )
                    )
                )
                assert prove_known_noncommercial_request(
                    operation=_row(parent),
                    actual_attempts=[_row(a) for a in attempts],
                    original_runs=[_row(await session.get(LlmRunModel, linked_run))]
                    if linked_run
                    else [],
                    transport_results=[_row(await session.get(ExecutionOperationModel, returned))]
                    if returned
                    else [],
                    failure_receipts=[_row(await session.get(ExecutionOperationModel, failed))]
                    if failed
                    else [],
                )
        yield c


async def prove_consumed_control(c):
    async with c.factory() as session:
        control = await session.get(ExecutionOperationModel, c.control_key)
        with bind_execution_scope(c.scope):
            return await service.prove_native_conception_request(
                session,
                c.settings,
                project_id=c.pid,
                operation=control,
                actual_attempts=[],
                original_runs=[],
                transport_results=[],
            )


async def test_consumed_logline_native_producer_proves_without_reopening_original_unknown(
    consumed_logline,
):
    c = consumed_logline
    async with c.factory() as session:
        control = await session.get(ExecutionOperationModel, c.control_key)
        run = await session.get(LlmRunModel, c.replacement_run)
        assert isinstance(control.lease_until, datetime)
        assert isinstance(run.id, UUID)
    before = await logline_state(c)
    proof = await prove_consumed_control(c)
    assert isinstance(proof, dict)
    assert proof["schema"] == "native-conception-logline-control-purpose.v1"
    assert proof["control_key"] == c.control_key
    assert proof["original_operation_key"] == c.claim.key
    assert proof["replacement_operation_key"] == c.plan["replacement_operation_key"]
    assert proof["provider_calls"] == proof["database_writes"] == 0
    for key in (
        "new_credit_allowed",
        "dispatch_authorized",
        "grade_granted",
        "budget_reset_allowed",
        "history_rewritten",
        "remote_operation_closed",
        "old_operation_replay_allowed",
    ):
        assert proof[key] is False
    assert await prove_consumed_control(c) == proof
    assert await logline_state(c) == before
    async with c.factory() as session:
        old = await session.get(ExecutionAttemptModel, c.aid)
        assert old.status == "reconciled_charged" and old.actual_tokens is None
        assert (await session.get(ExecutionOperationModel, c.claim.key)).status == "stopped"


@pytest.mark.parametrize(
    "fault",
    [
        "control_status",
        "plan_hash",
        "own_send",
        "grant_available",
        "old_record",
        "old_charge",
        "replacement_drift",
        "replacement_route",
    ],
)
async def test_consumed_logline_control_tampering_denies_without_writes(consumed_logline, fault):
    c = consumed_logline
    assert isinstance(await prove_consumed_control(c), dict)
    if fault == "control_status":
        await edit_logline(
            c,
            ExecutionOperationModel,
            c.control_key,
            lambda row: setattr(row, "status", "completed"),
        )
    elif fault == "plan_hash":
        await edit_logline(
            c,
            ExecutionOperationModel,
            c.control_key,
            lambda row: setattr(row, "result_json", {**row.result_json, "plan_sha256": "0" * 64}),
        )
        async with c.factory() as session:
            assert (await session.get(ExecutionOperationModel, c.control_key)).result_json[
                "plan_sha256"
            ] == "0" * 64
    elif fault == "own_send":
        async with c.factory() as session, session.begin():
            session.add(
                ExecutionAttemptModel(
                    id=uuid4().hex,
                    operation_key=c.control_key,
                    scope_id=c.scope,
                    model_name=logline.MODEL,
                    input_hash="0" * 64,
                    reserved_tokens=100,
                    actual_tokens=5,
                    status="completed",
                    created_at=datetime.now(UTC),
                )
            )
    elif fault == "grant_available":
        await edit_logline(
            c,
            ExecutionBudgetModel,
            c.scope,
            lambda row: row.metadata_json["single_dispatch_grants"][c.grant_id].update(
                status="available"
            ),
        )
    elif fault == "old_record":
        key = logline._key("provider-return.v1", c.scope, c.aid)
        await edit_logline(
            c,
            ExecutionOperationModel,
            key,
            lambda row: setattr(row, "result_json", {**row.result_json, "usable": True}),
        )
        async with c.factory() as session:
            assert (await session.get(ExecutionOperationModel, key)).result_json["usable"] is True
    elif fault == "old_charge":
        await edit_logline(
            c, ExecutionAttemptModel, c.aid, lambda row: setattr(row, "actual_tokens", 0)
        )
    elif fault == "replacement_drift":
        await edit_logline(
            c,
            ExecutionOperationModel,
            c.plan["replacement_operation_key"],
            lambda row: setattr(row, "input_hash", "0" * 64),
        )
    else:
        await edit_logline(
            c,
            LlmRunModel,
            c.replacement_run,
            lambda row: setattr(row, "prompt_template", "chapter_commercial_quality_judge"),
        )
    before = await logline_state(c)
    assert await prove_consumed_control(c) is None
    assert await logline_state(c) == before


async def verify_consumed_control(c):
    from bestseller.services.execution_control_purpose import verify_non_dispatch_control

    async with c.factory() as session:
        operations = list(
            await session.scalars(
                select(ExecutionOperationModel).where(ExecutionOperationModel.scope_id == c.scope)
            )
        )
        attempts = list(
            await session.scalars(
                select(ExecutionAttemptModel).where(ExecutionAttemptModel.scope_id == c.scope)
            )
        )
        control = next(row for row in operations if row.key == c.control_key)
        with bind_execution_scope(c.scope):
            return await verify_non_dispatch_control(
                session, operation=control, operations=operations, attempts=attempts
            )


async def read_waiting_ledger(c, *, lock=False):
    from bestseller.services.waiting_chapter_review_resume import _ledger

    async with c.factory() as session:
        with bind_execution_scope(c.scope):
            return await _ledger(session, c.scope, lock=lock)


@pytest.mark.parametrize("lock", [False, True])
async def test_consumed_control_real_verifier_and_waiting_ledger_preserve_all_native_rows(
    consumed_logline, lock
):
    c = consumed_logline
    async with c.factory() as session:
        statuses = set(
            await session.scalars(
                select(ExecutionOperationModel.status).where(
                    ExecutionOperationModel.scope_id == c.scope
                )
            )
        )
    assert {
        "stopped",
        "transport_failure_receipt",
        "unusable_provider_return",
        "reconciliation_receipt",
        "conception_logline_replacement",
        "completed",
        "transport_result",
    } <= statuses  # Retain every producer row, including the old unusable stream.
    before = await logline_state(c)
    proof = await verify_consumed_control(c)
    assert proof["schema"] == "non-dispatch-control-purpose.v1"
    assert proof["control_key"] == c.control_key
    assert proof["control_status"] == "conception_logline_replacement"
    assert proof["own_physical_sends"] == 0
    for key in ("grade_granted", "credit_granted", "unknown_reconciled"):
        assert proof[key] is False
    native_proof = proof["native_purpose_proof"]
    assert native_proof == await prove_consumed_control(c)
    assert native_proof["remote_operation_closed"] is False
    assert native_proof["old_operation_replay_allowed"] is False
    assert proof["parents"] == [
        {"operation_key": c.claim.key, "outcome_still_unknown": True},
        {"operation_key": c.plan["replacement_operation_key"]},
    ]
    ledger = await read_waiting_ledger(c, lock=lock)
    assert c.control_key in ledger["operations"]
    assert c.claim.key in ledger["operations"]
    assert c.plan["replacement_operation_key"] in ledger["operations"]
    assert c.aid in ledger["attempts"] and c.replacement_attempt in ledger["attempts"]
    assert await verify_consumed_control(c) == proof
    assert await read_waiting_ledger(c, lock=lock) == ledger
    assert await logline_state(c) == before


@pytest.mark.parametrize(
    "fault",
    [
        "plan_hash",
        "own_send",
        "grant_available",
        "project_scope",
        "replacement_drift",
        "old_record",
    ],
)
async def test_real_verifier_and_waiting_ledger_reject_changed_control_evidence(
    consumed_logline, fault
):
    c = consumed_logline
    assert isinstance(await verify_consumed_control(c), dict)
    assert c.control_key in (await read_waiting_ledger(c))["operations"]
    expected = "commercial_control:logline_control_native_proof"
    if fault == "plan_hash":
        await edit_logline(
            c,
            ExecutionOperationModel,
            c.control_key,
            lambda row: setattr(row, "result_json", {**row.result_json, "plan_sha256": "0" * 64}),
        )
    elif fault == "own_send":
        async with c.factory() as session, session.begin():
            session.add(
                ExecutionAttemptModel(
                    id=uuid4().hex,
                    operation_key=c.control_key,
                    scope_id=c.scope,
                    model_name=logline.MODEL,
                    input_hash="0" * 64,
                    reserved_tokens=100,
                    actual_tokens=5,
                    status="completed",
                    created_at=datetime.now(UTC),
                )
            )
    elif fault == "grant_available":
        await edit_logline(
            c,
            ExecutionBudgetModel,
            c.scope,
            lambda row: row.metadata_json["single_dispatch_grants"][c.grant_id].update(
                status="available"
            ),
        )
    elif fault == "project_scope":
        expected = "commercial_control:logline_control_project"
        await edit_logline(
            c,
            ProjectModel,
            c.pid,
            lambda row: setattr(
                row, "metadata_json", {"execution_scope_id": "conception:" + str(uuid4())}
            ),
        )
    elif fault == "replacement_drift":
        await edit_logline(
            c,
            ExecutionOperationModel,
            c.plan["replacement_operation_key"],
            lambda row: setattr(row, "input_hash", "0" * 64),
        )
    else:
        expected = "commercial_control:(logline_control_native_proof|partial_identity)"
        key = logline._key("provider-return.v1", c.scope, c.aid)
        await edit_logline(
            c,
            ExecutionOperationModel,
            key,
            lambda row: setattr(row, "result_json", {**row.result_json, "usable": True}),
        )
    before = await logline_state(c)
    with pytest.raises(ValueError, match=expected):
        await verify_consumed_control(c)
    with pytest.raises(ValueError, match=expected):
        await read_waiting_ledger(c)
    assert await logline_state(c) == before
