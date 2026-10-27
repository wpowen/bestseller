"""Exact legacy purpose witnesses and real SQLite wrapper; no PG/provider."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
import hashlib
import socket
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import JSON, MetaData, event, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.schema import CreateTable
from test_foundation_selection import case as foundation_case  # noqa: F401
from test_foundation_selection import store  # noqa: F401

from bestseller.infra.db.models import (
    ExecutionAttemptModel,
    ExecutionBudgetModel,
    ExecutionOperationModel,
    LlmRunModel,
    PlanningArtifactVersionModel,
    ProjectModel,
    WorkflowRunModel,
)
from bestseller.services import fanqie_short_planner as producer
from bestseller.services import foundation_selection, llm, model_catalog
from bestseller.services import legacy_request_purpose as legacy
from bestseller.services import retained_review_input_proof as retained
from bestseller.services.legacy_fanqie_beat_request import build_legacy_request
from bestseller.services.prompt_source_policy import content_hash
from bestseller.settings import LLMRoleSettings, load_settings

pytestmark = pytest.mark.unit
ROW_MODELS = (
    ProjectModel,
    PlanningArtifactVersionModel,
    WorkflowRunModel,
    LlmRunModel,
    ExecutionBudgetModel,
    ExecutionOperationModel,
    ExecutionAttemptModel,
)


def sha(value):
    return hashlib.sha256(value.encode()).hexdigest()


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("legacy purpose tests forbid network/PG/provider")

    async def paid_forbidden(*args, **kwargs):
        forbidden()

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(llm, "complete_text", paid_forbidden)
    monkeypatch.setattr(llm, "_complete_text_impl", paid_forbidden)
    monkeypatch.setattr(producer, "complete_text", paid_forbidden)
    monkeypatch.setattr(model_catalog, "_runtime_dead_models", {})
    import litellm

    monkeypatch.setattr(litellm, "acompletion", paid_forbidden)
    token = retained._HISTORICAL_ROUTE_WITNESSES.set(None)
    try:
        yield
    finally:
        retained._HISTORICAL_ROUTE_WITNESSES.reset(token)


def project_stub():
    return SimpleNamespace(
        id=uuid4(),
        title="The tidal observatory",
        genre="fantasy",
        sub_genre=None,
        target_chapters=4,
        target_word_count=12000,
        metadata_json={},
    )


def role_settings():
    return LLMRoleSettings(
        model="openai/legacy-purpose-fixture",
        temperature=0.7,
        max_tokens=8192,
        timeout_seconds=30,
        api_base="https://legacy-purpose.invalid/v1",
    )


def witnesses(request, role):
    h, key, aid = llm._effective_input_hash(request, role), sha("paid beat root"), "paid-send"
    scope = "conception:" + str(request.project_id)
    origin = {
        "schema": "paid-call-origin.v1",
        "status": "recorded",
        "project_id": str(request.project_id),
        "workflow_run_id": None,
        "step_run_id": None,
        "logical_role": "planner",
        "prompt_template": None,
        "prompt_version": None,
        "prompt_hash": h,
    }
    metadata = {
        "task": "fanqie_beat_sheet",
        "paid_call_origin": origin,
        "effective_input_receipt": {"schema": "effective-input.v1", "hash": h, "model": role.model},
        "execution_receipt": {
            "scope_id": scope,
            "operation_key": key,
            "attempts": [{"attempt_id": aid, "model": role.model, "effective_input_hash": h}],
        },
    }
    result = {
        "content": '{"title":"Retained beat","beats":[]}',
        "provider": "openai",
        "model_name": role.model,
        "input_tokens": 1,
        "output_tokens": 2,
        "finish_reason": "stop",
        "tool_calls": None,
        "raw_message": None,
        "fallback_used": False,
        "fallback_source": None,
        "metadata": deepcopy(metadata),
    }
    return {
        "request": request,
        "role": role,
        "operation": {
            "scope_id": scope,
            "key": key,
            "input_hash": h,
            "status": "completed",
            "result_json": result,
        },
        "actual_attempts": [
            {
                "id": aid,
                "scope_id": scope,
                "operation_key": key,
                "input_hash": h,
                "model_name": role.model,
                "status": "completed",
                "actual_tokens": 3,
            }
        ],
        "original_runs": [
            {
                "id": str(uuid4()),
                "project_id": str(request.project_id),
                "workflow_run_id": None,
                "step_run_id": None,
                "logical_role": "planner",
                "prompt_template": None,
                "prompt_version": None,
                "prompt_hash": h,
                "provider": "openai",
                "model_name": role.model,
                "finish_reason": "stop",
                "input_tokens": 1,
                "output_tokens": 2,
                "request_payload_ref": None,
                "metadata_json": deepcopy(metadata),
            }
        ],
        "transport_results": [
            {
                "scope_id": scope,
                "key": sha(f"transport-result.v1\n{scope}\n{aid}"),
                "input_hash": h,
                "status": "transport_result",
                "result_json": {
                    "schema": "transport-result.v1",
                    "operation_key": key,
                    "attempt_id": aid,
                    "input_hash": h,
                    "result": deepcopy(result),
                },
            }
        ],
        "source_artifacts": {
            "book_spec": {"id": str(uuid4()), "content_sha256": sha("book")},
            "cast_spec": {"id": str(uuid4()), "content_sha256": sha("cast")},
        },
    }


@pytest.fixture
def case():
    project = project_stub()
    request = build_legacy_request(
        project=project,
        premise="Two observers discover a falsified tide.",
        book_spec={"theme_statement": "Publish the evidence"},
        cast_spec={"protagonist": {"name": "Mira"}},
    )
    return witnesses(request, role_settings())


def verify(case):
    return legacy.verify_witnesses(**case)


def result(case):
    return case["operation"]["result_json"]


def returned(case):
    return case["transport_results"][0]["result_json"]["result"]


def destination(case, name):
    return {
        "root": case["operation"],
        "attempt": case["actual_attempts"][0],
        "run": case["original_runs"][0],
        "transport": case["transport_results"][0],
        "envelope": case["transport_results"][0]["result_json"],
        "paid": result(case),
        "returned": returned(case),
    }[name]


# Pre-8B local fixture captured from the actual old producer on 2026-10-22,
# with only gateway/storage boundaries stubbed. Full effective payload matched
# build_legacy_request exactly. Original producer function AST SHA256:
# f946d78795fd27c886cc157bb99fcd06e39fc96ef361103a7a514a5347b6fda6
# This historical oracle must not follow the phase8B live producer or reroll.
FROZEN_LEGACY_EFFECTIVE_INPUT_SHA256 = (
    "692728f42773907a30237884a82ee3163b8ef88282e8c84964e929fd18ba616b"
)


def test_forensic_renderer_preserves_frozen_pre8b_original_producer_payload(case):
    request, role = case["request"], case["role"]
    assert llm._effective_input_hash(request, role) == FROZEN_LEGACY_EFFECTIVE_INPUT_SHA256
    assert case["operation"]["input_hash"] == FROZEN_LEGACY_EFFECTIVE_INPUT_SHA256
    assert request.logical_role == "planner"
    assert request.metadata == {"task": "fanqie_beat_sheet"}
    assert request.workflow_run_id is request.step_run_id is None
    assert request.prompt_template is request.prompt_version is request.tools is None
    proof = verify(case)
    assert proof["input_sha256"] == FROZEN_LEGACY_EFFECTIVE_INPUT_SHA256
    assert proof["original_null_attribution_preserved"] is True
    assert proof["physical_request_file_retained"] is False


def test_exact_full_request_proves_only_purpose_preserving_nulls_and_all_witnesses(case):
    before = deepcopy(case)
    proof = verify(case)
    assert proof["schema"] == legacy.SCHEMA
    assert proof["purpose"] == "known_legacy_beat_sheet_planning"
    assert proof["input_sha256"] == case["operation"]["input_hash"]
    assert proof["effective_payload_sha256"] == content_hash(
        llm._effective_input_payload(case["request"], case["role"])
    )
    assert proof["original_rows_sha256"] == {
        "operation": content_hash(case["operation"]),
        "attempt": content_hash(case["actual_attempts"][0]),
        "transport": content_hash(case["transport_results"][0]),
        "run": content_hash(case["original_runs"][0]),
    }
    assert proof["request_reconstructed"] is True
    assert proof["physical_request_file_retained"] is False
    assert proof["original_null_attribution_preserved"] is True
    for field in (
        "source_authority_granted",
        "grade_granted",
        "unused_credit_asserted",
        "credit_granted",
    ):
        assert proof[field] is False
    proof["reconstruction_sources"]["book_spec"]["id"] = "caller mutation"
    assert case == before
    assert case["original_runs"][0]["request_payload_ref"] is None
    assert all(
        case["original_runs"][0][key] is None
        for key in ("workflow_run_id", "step_run_id", "prompt_template", "prompt_version")
    )
    assert verify(case) == verify(case)


@pytest.mark.parametrize("inventory", ["actual_attempts", "original_runs", "transport_results"])
@pytest.mark.parametrize("mode", ["missing", "extra"])
def test_all_single_original_inventories_required(case, inventory, mode):
    if mode == "missing":
        case[inventory].clear()
    else:
        case[inventory].append(deepcopy(case[inventory][0]))
    with pytest.raises(ValueError, match="complete_single_send"):
        verify(case)


@pytest.mark.parametrize(
    "where,field,value",
    [
        ("root", "key", sha("other root")),
        ("root", "scope_id", "other scope"),
        ("root", "input_hash", sha("different from both old and current")),
        ("root", "status", "unknown"),
        ("attempt", "status", "reconciled_charged"),
        ("attempt", "operation_key", sha("foreign")),
        ("attempt", "scope_id", "foreign"),
        ("attempt", "input_hash", sha("foreign")),
        ("attempt", "model_name", "openai/foreign"),
        ("attempt", "actual_tokens", 4),
        ("attempt", "actual_tokens", True),
        ("run", "project_id", str(uuid4())),
        ("run", "logical_role", "critic"),
        ("run", "workflow_run_id", str(uuid4())),
        ("run", "step_run_id", str(uuid4())),
        ("run", "prompt_template", "planner_book_spec"),
        ("run", "prompt_version", "v1"),
        ("run", "prompt_hash", sha("foreign")),
        ("run", "model_name", "openai/foreign"),
        ("run", "provider", "anthropic"),
        ("run", "finish_reason", "length"),
        ("run", "input_tokens", True),
        ("run", "output_tokens", False),
        ("transport", "key", sha("foreign")),
        ("transport", "scope_id", "foreign"),
        ("transport", "input_hash", sha("foreign")),
        ("transport", "status", "completed"),
        ("envelope", "schema", "transport-result.v0"),
        ("envelope", "operation_key", sha("foreign")),
        ("envelope", "attempt_id", "foreign"),
        ("envelope", "input_hash", sha("foreign")),
        ("paid", "fallback_used", True),
        ("paid", "model_name", "openai/foreign"),
        ("paid", "content", "different paid response"),
        ("returned", "output_tokens", 9),
    ],
)
def test_conflicting_original_witness_rejected(case, where, field, value):
    destination(case, where)[field] = value
    with pytest.raises(ValueError):
        verify(case)


@pytest.mark.parametrize(
    "where,field",
    [
        ("root", "key"),
        ("root", "result_json"),
        ("attempt", "input_hash"),
        ("attempt", "model_name"),
        ("transport", "result_json"),
        ("envelope", "input_hash"),
        ("run", "model_name"),
    ],
)
def test_missing_original_witness_rejected(case, where, field):
    del destination(case, where)[field]
    with pytest.raises((ValueError, KeyError, TypeError)):
        verify(case)


@pytest.mark.parametrize("holder", ["paid", "returned", "run"])
@pytest.mark.parametrize(
    "receipt,field,value",
    [
        ("effective_input_receipt", "schema", "effective-input.v0"),
        ("effective_input_receipt", "hash", sha("unrelated current hash")),
        ("effective_input_receipt", "model", "openai/foreign"),
        ("execution_receipt", "scope_id", "foreign"),
        ("execution_receipt", "operation_key", sha("foreign")),
        ("paid_call_origin", "workflow_run_id", str(uuid4())),
        ("paid_call_origin", "prompt_template", "planner_book_spec"),
        ("paid_call_origin", "prompt_version", "invented"),
        ("paid_call_origin", "prompt_hash", sha("foreign")),
        ("paid_call_origin", "project_id", str(uuid4())),
    ],
)
def test_each_metadata_witness_must_agree(case, holder, receipt, field, value):
    row = destination(case, holder)
    row["metadata_json" if holder == "run" else "metadata"][receipt][field] = value
    with pytest.raises(ValueError):
        verify(case)


@pytest.mark.parametrize("holder", ["paid", "returned", "run"])
@pytest.mark.parametrize(
    "receipt", ["effective_input_receipt", "execution_receipt", "paid_call_origin"]
)
def test_missing_metadata_is_not_reconstructed_attribution(case, holder, receipt):
    row = destination(case, holder)
    del row["metadata_json" if holder == "run" else "metadata"][receipt]
    with pytest.raises(ValueError):
        verify(case)


@pytest.mark.parametrize("mode", ["missing_run_task", "wrong_run_task", "missing_request_task"])
def test_task_context_is_required_not_guessed_from_prompt_text(case, mode):
    if mode == "missing_request_task":
        case["request"] = case["request"].model_copy(update={"metadata": {}})
    elif mode == "missing_run_task":
        case["original_runs"][0]["metadata_json"].pop("task")
    else:
        case["original_runs"][0]["metadata_json"]["task"] = "other planner task"
    with pytest.raises(ValueError):
        verify(case)


@pytest.mark.parametrize(
    "field,value",
    [
        ("user_prompt", "current replacement prompt"),
        ("system_prompt", "other instructions"),
        ("messages_override", [{"role": "user", "content": "different wire messages"}]),
        ("temperature_override", 0.1),
        ("max_tokens_override", 4096),
        ("tools", [{"type": "function", "function": {"name": "submit_chapter_review"}}]),
    ],
)
def test_hash_difference_from_current_never_proves_old_request_purpose(case, field, value):
    old_hash = case["operation"]["input_hash"]
    current = case["request"].model_copy(update={field: value})
    assert llm._effective_input_hash(current, case["role"]) != old_hash
    assert verify(case)["input_sha256"] == old_hash  # Exact original still works.
    case["request"] = current
    with pytest.raises(ValueError):
        verify(case)


@pytest.mark.parametrize(
    "field,value",
    [
        ("model", "openai/foreign"),
        ("api_base", "https://other.invalid/v1"),
        ("stream", True),
        ("reasoning_effort", "low"),
        ("max_tokens", 4096),
    ],
)
def test_entire_effective_role_is_bound(case, field, value):
    case["role"] = case["role"].model_copy(update={field: value})
    with pytest.raises(ValueError):
        verify(case)


@pytest.mark.parametrize("holder", ["paid", "returned", "run"])
def test_commercial_provenance_conflict_blocks_purpose(case, holder):
    row = destination(case, holder)
    row["metadata_json" if holder == "run" else "metadata"]["commercial_source_receipt"] = {}
    with pytest.raises(ValueError):
        verify(case)


def test_json_bool_does_not_equal_integer_in_physical_comparison(case):
    returned(case)["input_tokens"] = True
    with pytest.raises(ValueError, match="physical_return:input_tokens"):
        verify(case)


def test_matching_boolean_physical_tokens_cannot_borrow_integer_run_usage(case):
    assert result(case)["input_tokens"] == case["original_runs"][0]["input_tokens"] == 1
    result(case)["input_tokens"] = returned(case)["input_tokens"] = True
    with pytest.raises(ValueError, match="legacy_request_purpose:"):
        verify(case)


@pytest.mark.parametrize(
    "field,value", [("workflow_run_id", 0), ("step_run_id", False), ("prompt_version", False)]
)
def test_origin_null_is_exact_json_null(case, field, value):
    result(case)["metadata"]["paid_call_origin"][field] = value
    with pytest.raises(ValueError, match="origin"):
        verify(case)


@pytest.fixture
async def sqlite_case(foundation_case, tmp_path, monkeypatch):  # noqa: F811
    f = foundation_case
    async with f.engine.begin() as connection:
        for model in ROW_MODELS[3:]:
            table = model.__table__.to_metadata(MetaData())
            for column in table.c:
                if isinstance(column.type, JSONB):
                    column.type = JSON()
                if column.server_default and "::jsonb" in str(column.server_default.arg):
                    column.server_default = None
            await connection.run_sync(
                lambda c, t=table: c.execute(CreateTable(t, include_foreign_key_constraints=[]))
            )
    settings = load_settings().model_copy(deep=True)
    settings.llm.mock = False
    settings.llm.planner = role_settings()
    catalog = tmp_path / "purpose-catalog.json"
    catalog.write_text('{"models": []}')
    monkeypatch.setattr(model_catalog, "_CATALOG_PATH", catalog)
    async with f.factory() as session:
        project = await session.get(ProjectModel, f.project.id)
        await foundation_selection.pin_completed_foundation(
            session,
            project=project,
            workflow_id=f.workflow.id,
            settings=settings,
        )
        await session.commit()
        sources = await foundation_selection.load_selected_foundation_artifacts(
            session,
            project=project,
            settings=settings,
        )
        request = build_legacy_request(
            project=project,
            premise=project.metadata_json["premise"],
            book_spec=sources["book_spec"].content,
            cast_spec=sources["cast_spec"].content,
        )
        fresh_catalog, _ = retained._fresh_catalog()
        role = retained._fresh_role(settings, project, request, fresh_catalog)
        w = witnesses(request, role)
        now = datetime.now(UTC)
        budget = ExecutionBudgetModel(
            scope_id=w["operation"]["scope_id"],
            max_calls=100,
            max_tokens=100000,
            calls_used=54,
            tokens_charged=12345,
            status="active",
            deadline_at=now + timedelta(days=1),
            metadata_json={"old_holds": ["preserved"]},
        )
        root = ExecutionOperationModel(**w["operation"], owner="original", lease_until=now)
        attempt = ExecutionAttemptModel(
            **w["actual_attempts"][0], reserved_tokens=100, created_at=now
        )
        transport = ExecutionOperationModel(
            **w["transport_results"][0], owner="original", lease_until=now
        )
        run_data = deepcopy(w["original_runs"][0])
        run_data.update(id=UUID(run_data["id"]), project_id=project.id)
        run = LlmRunModel(**run_data)
        session.add_all([budget, root, attempt, transport, run])
        await session.commit()
        yield SimpleNamespace(
            session=session,
            settings=settings,
            project=project,
            root=root,
            run=run,
            attempt=attempt,
            transport=transport,
            sources=sources,
            engine=f.engine,
        )


async def snapshot(session):
    return {
        model.__tablename__: sorted(
            [legacy._row(row) for row in await session.scalars(select(model))],
            key=content_hash,
        )
        for model in ROW_MODELS
    }


async def prove(r, **overrides):
    return await legacy.prove_unlabelled_beatsheet_request(
        r.session,
        r.settings,
        **{
            "operation": r.root,
            "actual_attempts": [r.attempt],
            "original_runs": [r.run],
            "transport_results": [r.transport],
            **overrides,
        },
    )


@pytest.mark.asyncio
async def test_real_sqlite_wrapper_exact_selection_readonly_info_proof_and_idempotence(
    sqlite_case, monkeypatch
):
    r = sqlite_case
    before = await snapshot(r.session)
    writes = []

    def track(_conn, _cursor, statement, *_):
        if statement.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE", "COMMIT")):
            writes.append(statement)

    async def forbidden(*args, **kwargs):
        raise AssertionError("purpose wrapper must not flush/commit")

    monkeypatch.setattr(r.session, "flush", forbidden)
    monkeypatch.setattr(r.session, "commit", forbidden)
    event.listen(r.engine.sync_engine, "before_cursor_execute", track)
    try:
        proof = await prove(r)
        assert proof == await prove(r)
        assert r.session.info[legacy.INFO] == {r.root.key: proof}
        assert proof["reconstruction_sources"] == {
            kind: {"id": str(row.id), "content_sha256": content_hash(row.content)}
            for kind, row in r.sources.items()
        }
        assert proof["physical_request_file_retained"] is False
        assert await snapshot(r.session) == before
        assert not r.session.new and not r.session.dirty and not r.session.deleted
        assert writes == []
    finally:
        event.remove(r.engine.sync_engine, "before_cursor_execute", track)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode", ["no_settings", "no_run", "extra_run", "no_task", "labelled", "no_project"]
)
async def test_wrapper_nonlegacy_or_insufficient_context_stays_none(sqlite_case, mode):
    r = sqlite_case
    settings, runs = r.settings, [r.run]
    if mode == "no_settings":
        settings = None
    elif mode == "no_run":
        runs = []
    elif mode == "extra_run":
        runs *= 2
    elif mode == "no_task":
        r.run.metadata_json = {k: v for k, v in r.run.metadata_json.items() if k != "task"}
    elif mode == "labelled":
        r.run.prompt_template = "planner_book_spec"
    else:
        r.run.project_id = None
    result = await legacy.prove_unlabelled_beatsheet_request(
        r.session,
        settings,
        operation=r.root,
        actual_attempts=[r.attempt],
        original_runs=runs,
        transport_results=[r.transport],
    )
    assert result is None and legacy.INFO not in r.session.info


@pytest.mark.asyncio
async def test_wrapper_current_source_change_cannot_prove_different_old_hash(sqlite_case):
    r = sqlite_case
    r.project.title += " changed current source"
    with pytest.raises(ValueError, match="entire_effective_request"):
        await prove(r)
    assert legacy.INFO not in r.session.info
    assert r.project in r.session.dirty  # no implicit flush or repair of old source


@pytest.mark.asyncio
async def test_wrapper_requires_real_selected_foundation(sqlite_case):
    r = sqlite_case
    metadata = deepcopy(r.project.metadata_json)
    metadata.pop(foundation_selection.POINTER)
    r.project.metadata_json = metadata
    await r.session.commit()
    before = await snapshot(r.session)
    with pytest.raises(ValueError, match="selected_sources"):
        await prove(r)
    assert await snapshot(r.session) == before and legacy.INFO not in r.session.info


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["extra_actual_send", "conflicting_transport"])
async def test_wrapper_real_financial_or_transport_conflict_cannot_save_proof(sqlite_case, mode):
    r = sqlite_case
    if mode == "extra_actual_send":
        r.session.add(
            ExecutionAttemptModel(
                id="second-actual-send",
                operation_key=r.root.key,
                scope_id=r.root.scope_id,
                model_name=r.attempt.model_name,
                input_hash=r.root.input_hash,
                reserved_tokens=100,
                actual_tokens=3,
                status="completed",
                created_at=datetime.now(UTC),
            )
        )
    else:
        changed = deepcopy(r.transport.result_json)
        changed["result"]["content"] = "conflicting actual physical return"
        r.transport.result_json = changed
    await r.session.commit()
    actual_attempts = list(
        await r.session.scalars(
            select(ExecutionAttemptModel).where(
                ExecutionAttemptModel.operation_key == r.root.key,
            )
        )
    )
    before = await snapshot(r.session)
    with pytest.raises(ValueError):
        await prove(r, actual_attempts=actual_attempts)
    assert await snapshot(r.session) == before and legacy.INFO not in r.session.info
    assert not r.session.dirty and not r.session.new
