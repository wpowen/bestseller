"""Real, explicitly isolated SQLite quarantine proof; no PG/Docker/provider."""

from copy import deepcopy
from datetime import timedelta
from hashlib import sha256
import socket
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import JSON, ForeignKeyConstraint, MetaData, event
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm.attributes import flag_modified

from bestseller.infra.db import models as db
from bestseller.services import llm
from bestseller.services import planning_admission_id_protocol as ids
from bestseller.services import planning_admission_ref_protocol as refs
from bestseller.services import planning_admission_spent_transport as service
from bestseller.services import planning_draft_admission as admission
from bestseller.services.budget import ExecutionLedger, ExecutionReconciliation
from bestseller.services.planning_surgical_budget import resolve_request_role
from bestseller.services.prompt_source_policy import content_hash
from bestseller.services.retained_planning_generation import _Reader
from bestseller.settings import load_settings

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]
MODELS = (
    db.ExecutionBudgetModel,
    db.ExecutionAttemptModel,
    db.ExecutionOperationModel,
    db.LlmRunModel,
)


@pytest.fixture(autouse=True)
def no_external_io(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Only the explicitly isolated SQLite test DB is allowed")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(llm, "complete_text", forbidden)
    monkeypatch.setattr(llm, "_call_litellm", forbidden)
    monkeypatch.setattr(admission, "complete_text", forbidden)


def key(domain, scope, identifier):
    return sha256(f"{domain}\n{scope}\n{identifier}".encode()).hexdigest()


@pytest.fixture
async def case(tmp_path):
    path = tmp_path / "planning_admission_spent_transport_test.sqlite3"
    assert "test" in path.name
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    assert engine.dialect.name == "sqlite" and "test" in engine.url.database
    factory = async_sessionmaker(engine, expire_on_commit=False)
    table = db.LlmRunModel.__table__.to_metadata(MetaData())
    for column in table.columns:
        if isinstance(column.type, JSONB):
            column.type = JSON()
    for constraint in list(table.constraints):
        if isinstance(constraint, ForeignKeyConstraint):
            table.constraints.remove(constraint)
    async with engine.begin() as connection:
        for model in (
            db.ExecutionBudgetModel,
            db.ExecutionOperationModel,
            db.ExecutionAttemptModel,
        ):
            await connection.run_sync(model.__table__.create)
        await connection.run_sync(table.create)
    try:
        settings = load_settings(env={"BESTSELLER__DATABASE__URL": str(engine.url)})
        settings.llm.critic = settings.llm.critic.model_copy(
            update={
                "model": "openai/MiniMax-M3.1-Flash",
                "max_tokens": 32768,
                "reasoning_effort": "low",
                "api_base": "https://api.minimaxi.com/v1",
            }
        )
        settings.budget.execution_max_calls = 10
        settings.budget.execution_max_tokens = 2_000_000
        settings.budget.execution_deadline_seconds = 3600
        scope = "project:" + str(uuid4())
        project = SimpleNamespace(
            id=uuid4(),
            metadata_json={"execution_scope_id": scope},
            target_chapters=9,
            genre="mystery",
            language="en",
        )
        wid = uuid4()
        candidate = {
            "chapters": [
                {
                    "chapter_number": 3,
                    "goal": "Read the tide gauge",
                    "scenes": [
                        {
                            "action_sequence": ["The visitor reads the tide gauge"],
                            "unknown": {
                                "literal/~": "$ref preserved",
                                "flag": False,
                                "n": 0,
                                "unset": None,
                            },
                        }
                    ],
                }
            ]
        }
        supplied = {
            "schema": admission.SCHEMA,
            "hard_admission_policy": admission.HARD_ADMISSION_POLICY,
            "project_id": str(project.id),
            "workflow_run_id": str(wid),
            "candidate_payload": candidate,
            "projection_audit_targets": admission.projection_audit_targets(candidate),
            "approved_creation_contracts": {"selected_clause": "Preserve the coastal setting"},
            "approved_book_spec": {"logline": "An unrelated coastal investigation"},
            "approved_world_spec": {"requirement": "The gauge measures only local tides"},
            "approved_cast_spec": {"people": [{"name": "Visitor", "arc_trajectory": "Optional"}]},
            "untrusted_commercial_report": {"pass": False, "raw": "Retain historical negative"},
        }
        supplied["source_authority"] = admission._source_authority(supplied)
        supplied["source_authority_allowed_codes"] = refs._permissions(supplied["source_authority"])
        supplied = ids.v5_input(supplied)
        request, contract = admission.build_literal_admission_request(
            settings,
            project=project,
            workflow_run_id=wid,
            supplied=supplied,
            journal=[],
            index=0,
            verify_retention=False,
        )
        role = resolve_request_role(settings, project, request)
        payload = llm._effective_input_payload(request, role)
        ledger = ExecutionLedger(engine, scope, settings.budget)
        claim = await ledger.claim(request.operation_id, contract["effective_input_sha256"])
        aid = await ledger.reserve(claim, role.model, contract["effective_input_sha256"], 900)
        await ledger.record_transport_failure(
            aid,
            {
                "schema": "transport-failure.v1",
                "physical_sends": 1,
                "exception_type": "Timeout",
                "http_status": 408,
                "prompt_template": admission.TEMPLATE,
                "logical_role": "critic",
                "project_id": str(project.id),
                "workflow_run_id": str(wid),
                "operator_interruption": False,
                "elapsed_ms": 123456,
                "timeout_seconds": 120,
                "stream": False,
                "partial_stream_observed": False,
                "error_chain": [
                    {"type": "Timeout", "frames": []},
                    {"type": "ReadTimeout", "frames": []},
                ],
            },
        )
        await ledger.settle(aid, "unknown")
        await ledger.finish(claim, None)
        rid = uuid4().hex
        reconciliation = ExecutionReconciliation(
            reconciliation_id=rid,
            attempt_id=aid,
            expected_input_hash=contract["effective_input_sha256"],
            decision="quarantine_charged",
            evidence_kind="dispatcher_exit",
            evidence_sha256=content_hash({"dispatcher": "stopped"}),
            actor="offline test operator",
            remote_operation_closed=False,
            local_dispatcher_stopped=True,
        )
        receipt = await ledger.reconcile(reconciliation)
        assert receipt["credit_released"] is False
        assert receipt["operation_replay_allowed"] is False
        c = SimpleNamespace(
            engine=engine,
            factory=factory,
            settings=settings,
            project=project,
            wid=wid,
            scope=scope,
            aid=aid,
            rid=rid,
            root_key=claim.key,
            payload=payload,
            failure_key=key("transport-failure.v1", scope, aid),
            reconciliation_key=key("reconciliation.v1", scope, rid),
            supplied=supplied,
        )
        c.item = {
            "status": "transport_quarantined",
            "wire_version": request.prompt_version,
            "wire_contract": contract,
            "transport_quarantine": await rebuild(c),
        }
        c.journal = [c.item]
        yield c
    finally:
        await engine.dispose()


async def rebuild(c):
    async with c.factory() as session:
        reader = _Reader(session)
        rows = {
            name: await reader.get(model, identifier)
            for name, model, identifier in (
                ("attempt", db.ExecutionAttemptModel, c.aid),
                ("root", db.ExecutionOperationModel, c.root_key),
                ("failure", db.ExecutionOperationModel, c.failure_key),
                ("reconciliation", db.ExecutionOperationModel, c.reconciliation_key),
            )
        }
    return service.build_transport_quarantine_receipt(
        scope_id=c.scope, effective_input=c.payload, **rows
    )


async def snapshot(c):
    async with c.factory() as session:
        reader = _Reader(session)
        return {
            model.__tablename__: content_hash(
                sorted((vars(row) for row in await reader.rows(model)), key=str)
            )
            for model in MODELS
        }


async def verify(c, session=None, **overrides):
    kwargs = {
        "project": c.project,
        "workflow_run_id": c.wid,
        "supplied": c.supplied,
        "item": c.item,
        "journal": c.journal,
        "index": 0,
        "schema": ids.SCHEMA,
    }
    kwargs.update(overrides)
    if session is not None:
        return await service.verify_quarantined_transport_slot(session, c.settings, **kwargs)
    async with c.factory() as fresh:
        return await service.verify_quarantined_transport_slot(fresh, c.settings, **kwargs)


async def test_real_sqlite_reconciliation_proves_only_scope_and_is_read_only(case):
    c = case
    original = deepcopy((c.item, c.supplied, vars(c.project)))
    before = await snapshot(c)
    statements = []

    def record(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement.lstrip().split()[0].upper())

    event.listen(c.engine.sync_engine, "before_cursor_execute", record)
    try:
        assert await verify(c) == c.scope
        assert await verify(c) == c.scope
    finally:
        event.remove(c.engine.sync_engine, "before_cursor_execute", record)
    assert statements and set(statements) == {"SELECT"}
    assert await snapshot(c) == before
    assert (c.item, c.supplied, vars(c.project)) == original
    async with c.factory() as session:
        budget = await session.get(db.ExecutionBudgetModel, c.scope)
        attempt = await session.get(db.ExecutionAttemptModel, c.aid)
        root = await session.get(db.ExecutionOperationModel, c.root_key)
        assert (budget.calls_used, budget.tokens_charged) == (1, 900)
        assert (attempt.status, attempt.actual_tokens, attempt.reserved_tokens) == (
            "reconciled_charged",
            None,
            900,
        )
        assert root.status == "stopped" and root.result_json is None
    assert set(c.item) == {"status", "wire_version", "wire_contract", "transport_quarantine"}


@pytest.mark.parametrize(
    "field", ["completion", "raw", "raw_grade", "grade", "draft_eligible", "result"]
)
async def test_item_cannot_smuggle_a_completion_or_grade(case, field):
    case.item[field] = None
    with pytest.raises(ValueError, match="journal_item"):
        await verify(case)


@pytest.mark.parametrize(
    "field",
    [
        "scope_id",
        "operation_key",
        "attempt_ids",
        "reconciliation_id",
        "effective_input",
        "row_hashes",
        "schema",
        "extra",
    ],
)
async def test_closed_receipt_keys_and_bindings(case, field):
    case.item["transport_quarantine"][field] = "tampered"
    with pytest.raises(ValueError):
        await verify(case)


@pytest.mark.parametrize(
    "field",
    [
        "wire_version",
        "contract_policy",
        "contract_candidate",
        "contract_digest",
        "contract_effective",
        "contract_model",
        "contract_unknown",
        "payload_message",
        "payload_tools",
        "payload_boolean",
        "supplied_policy",
        "supplied_source",
        "schema",
        "index",
        "workflow",
        "project_scope",
    ],
)
async def test_original_literal_request_identity_is_not_reinterpreted(case, field):
    c, kwargs = case, {}
    proof = c.item["transport_quarantine"]
    if field == "wire_version":
        c.item["wire_version"] = "another-wire"
    elif field.startswith("contract_"):
        selected = {
            "policy": "hard_admission_policy",
            "candidate": "candidate_hash",
            "digest": "graded_input_hash",
            "effective": "effective_input_sha256",
            "model": "model",
            "unknown": "unknown",
        }[field.removeprefix("contract_")]
        c.item["wire_contract"][selected] = "changed"
    elif field == "payload_message":
        proof["effective_input"]["messages"][0]["content"] += "changed"
    elif field == "payload_tools":
        proof["effective_input"]["tools"] = []
    elif field == "payload_boolean":
        proof["effective_input"]["stream"] = 0
    elif field == "supplied_policy":
        c.supplied["hard_admission_policy"] = "changed"
    elif field == "supplied_source":
        c.supplied["approved_world_spec"]["requirement"] += "changed"
    elif field == "schema":
        kwargs["schema"] = admission.SCHEMA
    elif field == "index":
        kwargs["index"] = 1
    elif field == "workflow":
        kwargs["workflow_run_id"] = uuid4()
    else:
        c.project.metadata_json["execution_scope_id"] = "project:" + str(uuid4())
    with pytest.raises(ValueError):
        await verify(c, **kwargs)


@pytest.mark.parametrize(
    "row_name,field,value",
    [
        ("attempt", "created_at", None),
        ("root", "owner", "another-owner"),
        ("failure", "elapsed_ms", 1),
        ("reconciliation", "at", "changed"),
    ],
)
async def test_every_post_quarantine_row_column_is_bound(case, row_name, field, value):
    c = case
    model, identifier = (
        (db.ExecutionAttemptModel, c.aid)
        if row_name == "attempt"
        else (db.ExecutionOperationModel, getattr(c, row_name + "_key"))
    )
    async with c.factory() as session:
        row = await session.get(model, identifier)
        if row_name == "attempt":
            row.created_at += timedelta(seconds=1)
        elif row_name == "root":
            row.owner = value
        else:
            row.result_json = {**row.result_json, field: value}
            flag_modified(row, "result_json")
        await session.commit()
    before = await snapshot(c)
    with pytest.raises(ValueError):
        await verify(c)
    assert await snapshot(c) == before


@pytest.mark.parametrize(
    "field,value",
    [
        ("physical_sends", True),
        ("physical_sends", 0),
        ("physical_sends", 2),
        ("exception_type", "CancelledError"),
        ("exception_type", "ConnectionError"),
        ("error_chain", [{"type": "ConnectionError"}]),
        ("http_status", 429),
        ("operator_interruption", True),
        ("partial_stream_observed", True),
        ("partial_stream_observed", 0),
        ("project_id", "unrelated"),
        ("workflow_run_id", "unrelated"),
        ("prompt_template", "different"),
        ("logical_role", "planner"),
        ("attempt_id", "unrelated"),
        ("operation_key", "f" * 64),
        ("response", {"partial": True}),
    ],
)
async def test_actual_failure_must_be_single_initial_timeout_even_if_resealed(case, field, value):
    c = case
    async with c.factory() as session:
        row = await session.get(db.ExecutionOperationModel, c.failure_key)
        row.result_json = {**row.result_json, field: value}
        flag_modified(row, "result_json")
        await session.commit()
    c.item["transport_quarantine"] = await rebuild(c)
    with pytest.raises(ValueError):
        await verify(c)


@pytest.mark.parametrize(
    "target,field,value",
    [
        ("attempt", "status", "unknown"),
        ("attempt", "status", "completed"),
        ("attempt", "actual_tokens", 0),
        ("attempt", "reserved_tokens", 0),
        ("attempt", "model_name", "different-model"),
        ("attempt", "input_hash", "f" * 64),
        ("attempt", "scope_id", "project:unrelated"),
        ("root", "status", "completed"),
        ("root", "result_json", {"content": "partial"}),
        ("root", "input_hash", "f" * 64),
        ("root", "scope_id", "project:unrelated"),
        ("failure", "status", "stopped"),
        ("failure", "input_hash", "f" * 64),
        ("reconciliation", "status", "stopped"),
    ],
)
async def test_actual_ledger_columns_dominate_receipt_claims(case, target, field, value):
    c = case
    model, identifier = (
        (db.ExecutionAttemptModel, c.aid)
        if target == "attempt"
        else (db.ExecutionOperationModel, getattr(c, target + "_key"))
    )
    async with c.factory() as session:
        row = await session.get(model, identifier)
        setattr(row, field, value)
        await session.commit()
    c.item["transport_quarantine"] = await rebuild(c)
    with pytest.raises(ValueError):
        await verify(c)


@pytest.mark.parametrize(
    "field,value",
    [
        ("remote_operation_closed", True),
        ("local_dispatcher_stopped", False),
        ("local_dispatcher_stopped", 1),
        ("evidence_kind", "transport_log"),
        ("request_hash", "f" * 64),
        ("actor", "forged actor"),
        ("receipt.decision", "abandon_charged"),
        ("receipt.credit_released", True),
        ("receipt.operation_replay_allowed", True),
        ("receipt.remote_outcome", "closed"),
        ("receipt.automatic_resume", True),
        ("receipt.attempt_id", "unrelated"),
        ("receipt.tokens_charged", 0),
        ("receipt.calls_used", True),
    ],
)
async def test_actual_durable_reconciliation_is_required_even_if_resealed(case, field, value):
    c = case
    async with c.factory() as session:
        row = await session.get(db.ExecutionOperationModel, c.reconciliation_key)
        data = deepcopy(row.result_json)
        if field.startswith("receipt."):
            data["receipt"][field.split(".")[1]] = value
        else:
            data[field] = value
        row.result_json = data
        flag_modified(row, "result_json")
        await session.commit()
    c.item["transport_quarantine"] = await rebuild(c)
    with pytest.raises(ValueError):
        await verify(c)


@pytest.mark.parametrize(
    "status", ["unknown", "reserved", "not_sent", "rejected", "completed", "reconciled_charged"]
)
async def test_all_actual_attempts_for_root_must_be_exactly_listed_one(case, status):
    c = case
    async with c.factory() as session:
        original = await _Reader(session).get(db.ExecutionAttemptModel, c.aid)
        session.add(
            db.ExecutionAttemptModel(
                **{
                    **vars(original),
                    "id": uuid4().hex,
                    "scope_id": "project:another-scope",
                    "status": status,
                }
            )
        )
        await session.commit()
    with pytest.raises(ValueError, match="actual_attempt_set"):
        await verify(c)


@pytest.mark.parametrize(
    "kind", ["transport-result.v1", "provider-return.v1", "native-linked", "second-failure"]
)
async def test_old_slot_has_no_native_or_transport_result_or_extra_failure(case, kind):
    c = case
    async with c.factory() as session:
        original = await _Reader(session).get(db.ExecutionOperationModel, c.failure_key)
        data = (
            {}
            if kind in {"transport-result.v1", "provider-return.v1"}
            else {"operation_key": c.root_key}
        )
        session.add(
            db.ExecutionOperationModel(
                **{
                    **vars(original),
                    "key": key(kind, c.scope, c.aid),
                    "status": "unusable_provider_return",
                    "result_json": data,
                }
            )
        )
        await session.commit()
    with pytest.raises(ValueError, match="unexpected_slot_record"):
        await verify(c)


@pytest.mark.parametrize("binding", ["input_hash", "operation", "attempt"])
async def test_old_slot_cannot_have_an_llm_run(case, binding):
    c = case
    async with c.factory() as session:
        session.add(
            db.LlmRunModel(
                project_id=uuid4(),
                workflow_run_id=uuid4(),
                logical_role="critic",
                provider="offline",
                model_name="unrelated-model",
                prompt_template="unrelated",
                prompt_hash=(
                    c.item["wire_contract"]["effective_input_sha256"]
                    if binding == "input_hash"
                    else "f" * 64
                ),
                metadata_json={
                    "execution_receipt": (
                        {"operation_key": c.root_key}
                        if binding == "operation"
                        else {"attempt_id": c.aid}
                        if binding == "attempt"
                        else {}
                    )
                },
            )
        )
        await session.commit()
    with pytest.raises(ValueError, match="old_slot_llm_run"):
        await verify(c)


@pytest.mark.parametrize("field", ["tokens_charged", "calls_used"])
async def test_no_released_credit_can_hide_behind_unchanged_reservation(case, field):
    c = case
    async with c.factory() as session:
        row = await session.get(db.ExecutionBudgetModel, c.scope)
        setattr(row, field, 0)
        await session.commit()
    with pytest.raises(ValueError, match="retained_charges"):
        await verify(c)


async def test_dirty_cached_rows_are_not_authority_and_are_never_flushed(case):
    c = case
    before = await snapshot(c)
    async with c.factory() as session:
        root = await session.get(db.ExecutionOperationModel, c.root_key)
        root.status, root.result_json = "completed", {"forged": True}
        assert await verify(c, session=session) == c.scope
        assert root in session.dirty and root.status == "completed"
    assert await snapshot(c) == before


async def test_external_committed_tamper_is_seen_despite_clean_stale_identity_map(case):
    c = case
    async with c.factory() as session:
        cached = await session.get(db.ExecutionOperationModel, c.root_key)
        async with c.factory() as writer:
            row = await writer.get(db.ExecutionOperationModel, c.root_key)
            row.status = "completed"
            await writer.commit()
        assert cached.status == "stopped"
        with pytest.raises(ValueError, match="stopped_root"):
            await verify(c, session=session)
        assert cached.status == "stopped"


async def test_optional_deadline_diagnostic_is_allowed_but_entirely_hash_bound(case):
    c = case
    async with c.factory() as session:
        row = await session.get(db.ExecutionOperationModel, c.failure_key)
        row.result_json = {
            **row.result_json,
            "native_deadline_policy": {"sdk": 120, "hard": 150, "configured": True},
        }
        await session.commit()
    c.item["transport_quarantine"] = await rebuild(c)
    assert await verify(c) == c.scope
    async with c.factory() as session:
        row = await session.get(db.ExecutionOperationModel, c.failure_key)
        row.result_json = {**row.result_json, "native_deadline_policy": {"hard": 999}}
        await session.commit()
    with pytest.raises(ValueError, match="post_quarantine_rows_changed"):
        await verify(c)
