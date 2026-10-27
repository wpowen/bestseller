"""Real SQLite and gateway custody, with only the external wire made inert."""

from copy import deepcopy
import json
import math
import socket
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import JSON, ForeignKeyConstraint, MetaData, event
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import JSONB
from test_outline_domain_transport_recovery import (  # noqa: F401 - pytest fixture dependencies
    domain_case,
    install_provider,
    migration,
    source_case,
    source_fixture,
    store,
)
from test_planning_draft_admission import grade_payload

from bestseller.infra.db import models as db
from bestseller.services import llm
from bestseller.services import outline_domain_transport_recovery as prior
from bestseller.services import outline_projection_consistency_recovery as service
from bestseller.services import outline_revision_lifecycle as lifecycle
from bestseller.services import planning_draft_admission as admission
from bestseller.services import planning_projection_consistency as pure
from bestseller.services.budget import bind_execution_scope
from bestseller.services.lossless_source_encoding import decode_lossless_source
from bestseller.services.prompt_source_policy import content_hash

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]
REAL_COMPLETE = llm.complete_text
REAL_ASSESS = admission.assess_planning_draft_admission
AUTH = {
    "actor": "offline operator",
    "authorization_ref": "explicit projection repair fixture",
    "explicit_user_approval": True,
}


@pytest.fixture
async def case(migration, monkeypatch, request):  # noqa: F811 - imported fixture
    c = migration
    source_schema = getattr(request, "param", "planning-draft-admission.v4")
    from bestseller.services.planning_admission_id_protocol import POLICY as ID_POLICY
    assert c.factory.kw["bind"].dialect.name == "sqlite"
    assert "test" in c.factory.kw["bind"].url.database
    install_provider(c, monkeypatch)
    async with c.factory() as session:
        with bind_execution_scope(c.scope):
            result = await prior.recover_outline_domain_transport(
                session,
                c.settings,
                **c.args,
                parent_domain_workflow_id=c.parent_id,
                actor="offline operator",
                authorization_ref="existing completion fixture",
                explicit_user_approval=True,
            )
    for model in (db.CanonFactModel, db.TimelineEventModel):
        table = model.__table__.to_metadata(MetaData())
        for column in table.c:
            if isinstance(column.type, JSONB):
                column.type = JSON()
            if column.server_default is not None and "::" in str(column.server_default.arg):
                column.server_default = None
        for constraint in list(table.constraints):
            if isinstance(constraint, ForeignKeyConstraint):
                table.constraints.remove(constraint)
        async with c.factory.kw["bind"].begin() as connection:
            await connection.run_sync(lambda conn, table=table: table.create(conn, checkfirst=True))
    async with c.factory() as session:
        base_id = service.admission_workflow_id(c.project.id, c.outline.id)
        if await session.get(db.WorkflowRunModel, base_id) is None:
            session.add(
                db.WorkflowRunModel(
                    id=base_id,
                    project_id=c.project.id,
                    workflow_type="inert_source_input_owner",
                    status="completed",
                    metadata_json={},
                )
            )
            await session.commit()
        with bind_execution_scope(c.scope):
            staged = await lifecycle.stage_full_outline_revision(
                session,
                c.settings,
                project_slug=c.project.slug,
                parent_artifact_id=c.outline.id,
                parent_materialization_workflow_id=c.owner.id,
                candidate_payload=result["candidate_payload"],
                proposal_receipt=result["proposal_receipt"],
                admission_schema=source_schema,
                **AUTH,
            )
    c.failed_id, c.failed_aid = UUID(staged["workflow_run_id"]), UUID(staged["artifact_id"])
    c.settings.llm.mock = False
    c.settings.llm.critic = c.settings.llm.planner.model_copy(deep=True)
    c.settings.llm.critic.reasoning_effort = "low"
    c.new_calls, c.old_calls, c.after, c.on_wire = [], [], None, None
    c.mode = "change"

    async def no_override(*args, **kwargs):
        return None

    async def wire(request, role):
        if request.prompt_template == admission.TEMPLATE:
            c.old_calls.append(request.model_dump(mode="json"))
            raw = grade_payload(
                c.candidate,
                {
                    "draft_eligible": False,
                    "hard_violations": [
                        {
                            "code": "ABILITY_RULE_CONFLICT",
                            "path": "chapters[0]",
                            "evidence": "The station reading has incompatible descriptions.",
                            "source_requirement": "Describe one compatible source-supported event.",
                            "source_refs": [
                                "/candidate_payload/chapters/0/chapter_goal"
                                if len(c.old_calls) == 1
                                else "/candidate_payload/chapters/0/nonexistent_alias"
                            ],
                        }
                    ],
                    "editorial_notes": [],
                },
            )
            if source_schema == "planning-draft-admission.v5":
                from bestseller.services.planning_review_literal_transport import decode_literal_planning_review_transport
                from bestseller.services.planning_admission_id_protocol import reference_contract
                record = request.metadata["source_transport"]
                data = decode_literal_planning_review_transport(system=request.system_prompt,
                    user=request.user_prompt, receipt=record, max_chars=record["max_chars"],
                    max_estimated_tokens=record["max_estimated_tokens"])
                catalog = reference_contract(data["input"])["catalog"]
                identifier = next(key for key, row in catalog.items()
                    if row["pointer"] == "/candidate_payload/chapters/0/chapter_goal")
                goal = c.candidate["chapters"][0]["chapter_goal"]
                raw["hard_violations"] = [{"code": "CONTINUITY_CONFLICT", "path": "chapters[0]",
                    "evidence": goal + " This is untrusted commentary, not an exact quotation.",
                    "source_requirement": goal, "source_refs": [identifier],
                    "evidence_refs": [identifier]}]
            tool = "submit_planning_admission"
        else:
            assert (
                request.prompt_template == service.TEMPLATE
                and request.prompt_version == service.VERSION
            )
            c.new_calls.append(request.model_dump(mode="json"))
            if c.on_wire is not None:
                c.on_wire()
            async with c.factory() as other:
                owner = await other.get(db.WorkflowRunModel, request.workflow_run_id)
                assert owner is not None and owner.status == "running"
                state = owner.metadata_json
                assert state["phase"] == "dispatching"
                b = await other.get(db.ExecutionBudgetModel, c.scope)
                grant = b.metadata_json["single_dispatch_grants"][
                    request.metadata["single_dispatch_authorization_id"]
                ]
                assert grant["status"] == "spent" and grant["physical_attempt_cap"] == 1
            contract = state["prepared"]["field_contract"]
            raw = {
                key: deepcopy(pure._at(c.candidate, path))
                for key, path in contract["id_to_path"].items()
            }
            if c.mode == "change":
                target = next(
                    key
                    for key, path in contract["id_to_path"].items()
                    if path.endswith("/chapter_goal")
                )
                raw[target] += " Mira checks the original register before leaving."
            elif c.mode == "missing":
                raw.pop(next(iter(raw)))
            elif c.mode == "incomplete":
                return "", 10, 5, "length", None, {"role": "assistant", "content": ""}
            elif c.mode == "transport_error":
                raise TimeoutError("Inert transport failed after its one reservation")
            if c.after is not None:
                await c.after(request)
            tool = service.TOOL
        calls = [
            {
                "id": "native-offline-" + str(len(c.old_calls) + len(c.new_calls)),
                "type": "function",
                "function": {"name": tool, "arguments": json.dumps(raw, ensure_ascii=False)},
            }
        ]
        return (
            "",
            10,
            5,
            "tool_calls",
            calls,
            {
                "role": "assistant",
                "content": None,
                "tool_calls": deepcopy(calls),
                "reasoning_content": "Private physical fixture debug only.",
            },
        )

    monkeypatch.setattr(llm, "complete_text", REAL_COMPLETE)
    monkeypatch.setattr(admission, "complete_text", REAL_COMPLETE)
    monkeypatch.setattr(llm, "_call_litellm", wire)
    monkeypatch.setattr(llm, "_resolve_project_model_override", no_override)
    async with c.factory() as session:
        project = await session.get(db.ProjectModel, c.project.id)
        artifact = await session.get(db.PlanningArtifactVersionModel, c.failed_aid)
        owner = await session.get(db.WorkflowRunModel, c.failed_id)
        state = deepcopy(owner.metadata_json)
        c.candidate = deepcopy(service._without_planning_meta(artifact.content))
        state["protocol"] = {
            "wire_version": service.LITERAL_ID_SOURCE_VERSION if source_schema.endswith(".v5") else service.LITERAL_FULL_SOURCE_VERSION,
            "graded_input_hash": "pending",
            "hard_admission_policy": ID_POLICY if source_schema.endswith(".v5") else service.POLICY,
            "candidate_hash": content_hash(c.candidate),
            "attempts": [],
            "status": "pending",
        }
        with bind_execution_scope(c.scope):
            supplied = await admission._input(
                session,
                project=project,
                workflow_run_id=c.failed_id,
                candidate_payload=c.candidate,
                book_spec=c.assets["book_spec"].content,
                world_spec=c.assets["world_spec"].content,
                cast_spec=c.assets["cast_spec"].content,
                commercial_report={
                    "schema": "historical-plan-review-observations.v1",
                    **state["full_outline_revision"]["base_binding"][
                        "historical_editorial_observations"
                    ],
                },
                check_initial_context=True,
            )
        supplied = admission._input_for_contract(supplied, source_schema)
        state["protocol"]["graded_input_hash"] = content_hash(supplied)
        owner.metadata_json = deepcopy(state)
        await session.commit()
        with (
            bind_execution_scope(c.scope),
            pytest.raises(admission.PlanningDraftAdmissionProtocolError),
        ):
            await REAL_ASSESS(
                session,
                c.settings,
                project=project,
                workflow_run_id=c.failed_id,
                candidate_payload=c.candidate,
                book_spec=c.assets["book_spec"].content,
                world_spec=c.assets["world_spec"].content,
                cast_spec=c.assets["cast_spec"].content,
                commercial_report={
                    "schema": "historical-plan-review-observations.v1",
                    **state["full_outline_revision"]["base_binding"][
                        "historical_editorial_observations"
                    ],
                },
                admission_schema=source_schema,
                protocol_state=state["protocol"],
            )
        assert len(state["protocol"]["attempts"]) == 2
        state["full_outline_revision"]["phase"] = "protocol_unknown"
        owner.metadata_json, owner.status, owner.current_step = (
            state,
            "machine_blocked",
            "admission_protocol_exhausted",
        )
        await session.commit()
        c.old_state = deepcopy(state)
        c.baseline = await service._accounting(service._Reader(session), c.scope, {})
        c.anchor = {
            model: service._rows(await service._Reader(session).rows(model), model)
            for model in (
                db.WorkflowRunModel,
                db.PlanningArtifactVersionModel,
                db.ExecutionOperationModel,
                db.ExecutionAttemptModel,
                db.LlmRunModel,
                db.ChapterModel,
                db.SceneCardModel,
                db.ChapterDraftVersionModel,
                db.ProjectModel,
            )
        }
    return c


async def invoke(c, *, prepare=False, **overrides):
    kwargs = {**AUTH, **overrides, "failed_admission_workflow_id": c.failed_id}
    async with c.factory() as session:
        with bind_execution_scope(c.scope):
            method = (
                service.prepare_source_projection_consistency_repair
                if prepare
                else service.propose_source_projection_consistency_repair
            )
            return await method(session, c.settings, **kwargs)


async def assert_old_unchanged(c):
    async with c.factory() as session:
        reader = service._Reader(session)
        for model, before in c.anchor.items():
            now = service._rows(await reader.rows(model), model)
            assert all(now.get(key) == value for key, value in before.items())
        owner = await session.get(db.WorkflowRunModel, c.failed_id)
        assert owner.metadata_json == c.old_state and owner.status == "machine_blocked"


async def test_readonly_prepare_binds_full_sources_two_untrusted_native_returns_and_no_locks(case):
    c = case

    def check(conn, cursor, statement, parameters, context, executemany):
        assert not statement.lstrip().lower().startswith(("insert", "update", "delete"))
        if context.compiled is not None:
            pg = str(context.compiled.statement.compile(dialect=postgresql.dialect()))
            assert "FOR UPDATE" not in pg and "FOR SHARE" not in pg

    engine = c.factory.kw["bind"].sync_engine
    event.listen(engine, "before_cursor_execute", check)
    try:
        result = await invoke(c, prepare=True)
    finally:
        event.remove(engine, "before_cursor_execute", check)
    request = result["request"]
    logical = decode_lossless_source(json.loads(request["user_prompt"][len(service.MARKER) :]))
    assert logical["candidate_payload"] == c.candidate
    assert len(logical["untrusted_native_observations"]) == 2
    assert (
        logical["approved_story_sources"]["cast_spec"]["content"] == c.assets["cast_spec"].content
    )
    assert "reasoning_content" not in json.dumps(logical)
    full = result["prepared"]["field_contract"]
    projected = logical["correction_contract"]
    audit = projected["wire_projection_audit"]
    assert projected["schema"] == service.WIRE_SCHEMA
    assert full["observations"] == logical["untrusted_native_observations"]
    assert full["source_authority"]["approved_sources"] == logical["approved_story_sources"]
    assert "observations" not in projected
    assert "approved_sources" not in projected["source_authority"]
    assert "catalog" not in projected["source_authority"]["reference_contract"]
    assert audit == result["request_contract"]["wire_projection_audit"]
    assert audit["full_contract_sha256"] == content_hash(full)
    assert audit["historical_reference_catalog_sha256"] == content_hash(
        full["source_authority"]["reference_contract"]["catalog"]
    )
    for key in ("source_authority", "source_authority_allowed_codes"):
        assert projected["source_authority"][key] == full["source_authority"][key]
    for key in ("paths", "id_to_path", "bindings", "parameters", "projection_groups"):
        assert projected[key] == full[key]
    assert result["grant_binding"]["dispatch_scope"] == "workflow" and not c.new_calls
    await assert_old_unchanged(c)


async def test_real_gateway_once_completed_reuse_and_historical_data_preserved(case):
    c = case
    held = set()
    engine = c.factory.kw["bind"].sync_engine

    def track(conn, cursor, statement, parameters, context, executemany):
        if context.compiled is not None:
            pg = str(context.compiled.statement.compile(dialect=postgresql.dialect())).upper()
            if "FOR UPDATE" in pg or pg.startswith(
                ("UPDATE WORKFLOW_RUNS", "INSERT INTO WORKFLOW_RUNS")
            ):
                held.add(id(conn))

    def release(conn):
        held.discard(id(conn))

    def unlocked():
        assert not held, "No source/WF SELECT FOR UPDATE or WF mutation spans the external call"

    c.on_wire = unlocked
    event.listen(engine, "before_cursor_execute", track)
    event.listen(engine, "commit", release)
    event.listen(engine, "rollback", release)
    try:
        first = await invoke(c)
    finally:
        event.remove(engine, "before_cursor_execute", track)
        event.remove(engine, "commit", release)
        event.remove(engine, "rollback", release)
    second = await invoke(c)
    assert second == {**first, "reused": True} and len(c.new_calls) == 1
    assert first["candidate_payload"] != c.candidate
    assert first["base_artifact_id"] == str(c.outline.id)
    assert first["base_materialization_workflow_id"] == str(c.owner.id)
    assert not first["grade_asserted"] and not first["adoption_granted"]
    async with c.factory() as session:
        b = await session.get(db.ExecutionBudgetModel, c.scope)
        assert b.calls_used == c.baseline["calls_used"] + 1
        assert b.tokens_charged == c.baseline["tokens_charged"] + 15
        owner = await session.get(db.WorkflowRunModel, UUID(first["workflow_run_id"]))
        assert (
            owner.metadata_json["prepared"]["field_contract"]
            == first["proposal_receipt"]["field_contract"]
        )
        assert (
            "catalog"
            in owner.metadata_json["prepared"]["field_contract"]["source_authority"][
                "reference_contract"
            ]
        )
        run = await session.get(
            db.LlmRunModel, UUID(owner.metadata_json["paid_attempt"]["llm_run_id"])
        )
        assert (
            run.metadata_json[service.WITNESS]
            == owner.metadata_json["request_contract"]["grant_binding_sha256"]
        )
    await assert_old_unchanged(c)


@pytest.mark.parametrize("mode", ["missing", "unchanged", "incomplete"])
async def test_invalid_paid_return_is_retained_and_cannot_renew_a_slot(case, mode):
    c = case
    c.mode = mode
    with pytest.raises(ValueError):
        await invoke(c)
    assert len(c.new_calls) == 1
    with pytest.raises(ValueError, match="terminal_or_inflight"):
        await invoke(c, authorization_ref="a new nonce cannot renew this purpose")
    assert len(c.new_calls) == 1
    async with c.factory() as session:
        identity = service._identity(c.project, c.failed_id)
        owner = await session.get(db.WorkflowRunModel, identity)
        assert owner.status == "failed" and owner.metadata_json.get("paid_attempt")
        assert owner.metadata_json.get("result") is None
    await assert_old_unchanged(c)


@pytest.mark.parametrize("change", ["candidate", "journal", "ledger", "control", "other_scope"])
async def test_source_financial_and_control_fences_precede_any_new_dispatch(case, change):
    c = case
    async with c.factory() as session:
        if change == "candidate":
            artifact = await session.get(db.PlanningArtifactVersionModel, c.failed_aid)
            artifact.content = {**artifact.content, "unknown_changed": None}
        elif change == "journal":
            owner = await session.get(db.WorkflowRunModel, c.failed_id)
            state = deepcopy(owner.metadata_json)
            state["protocol"]["attempts"].pop()
            owner.metadata_json = state
        elif change == "ledger":
            (await session.get(db.ExecutionBudgetModel, c.scope)).calls_used -= 1
        elif change == "control":
            (await session.get(db.BookProductionControlModel, c.project.id)).desired_state = "pause"
        else:
            project = await session.get(db.ProjectModel, c.project.id)
            project.metadata_json = {**project.metadata_json, "execution_scope_id": "other-scope"}
        await session.commit()
    with pytest.raises(ValueError):
        await invoke(c)
    assert c.new_calls == []


async def test_change_during_provider_retains_paid_return_without_promoting_candidate(case):
    c = case

    async def change(request):
        async with c.factory() as other:
            (await other.get(db.BookProductionControlModel, c.project.id)).desired_state = "pause"
            await other.commit()

    c.after = change
    with pytest.raises(ValueError):
        await invoke(c)
    assert len(c.new_calls) == 1
    async with c.factory() as session:
        owner = await session.get(db.WorkflowRunModel, service._identity(c.project, c.failed_id))
        assert owner.metadata_json["paid_attempt"] and owner.metadata_json.get("result") is None
        assert (await session.get(db.PlanningArtifactVersionModel, c.failed_aid)).status == "draft"
    await assert_old_unchanged(c)


@pytest.mark.parametrize("change", ["state_and_grant", "telemetry", "response_file"])
async def test_completed_reuse_rebuilds_independent_physical_witness(case, change):
    c = case
    result = await invoke(c)
    async with c.factory() as session:
        owner = await session.get(db.WorkflowRunModel, UUID(result["workflow_run_id"]))
        state = deepcopy(owner.metadata_json)
        run = await session.get(db.LlmRunModel, UUID(state["paid_attempt"]["llm_run_id"]))
        if change == "telemetry":
            run.metadata_json = {**run.metadata_json, service.WITNESS: "f" * 64}
        elif change == "response_file":
            from pathlib import Path

            path = Path(c.settings.artifact_store.local_dir) / run.response_payload_ref
            path.write_text("{}")
        else:
            state["prepared"]["source_evidence"]["baseline"]["calls_used"] -= 1
            # Rebuild ALL mutually dependent state and grant fields, so only
            # the original physical request/independent telemetry can refute it.
            _, rebuilt, binding = service._build_request(
                c.settings,
                c.project,
                owner.id,
                state["prepared"],
                actor=AUTH["actor"],
                authorization_ref=AUTH["authorization_ref"],
                verify_retention=False,
            )
            state["grant_binding"], state["request_contract"] = binding, rebuilt
            state["result"]["proposal_receipt"]["request_contract"] = deepcopy(rebuilt)
            state["result"]["proposal_receipt"]["source_evidence"] = deepcopy(
                state["prepared"]["source_evidence"]
            )
            assert rebuilt["grant_binding_sha256"] != run.metadata_json[service.WITNESS]
            b = await session.get(db.ExecutionBudgetModel, c.scope)
            metadata = deepcopy(b.metadata_json)
            gid = state["request_contract"]["grant_binding_seed"]
            grant_id = service.authorization_id(gid["user_message_id"], gid["prior_workflow_id"])
            metadata["single_dispatch_grants"][grant_id]["binding"] = deepcopy(
                state["grant_binding"]
            )
            b.metadata_json, owner.metadata_json = metadata, state
        await session.commit()
    with pytest.raises(ValueError):
        await invoke(c)
    assert len(c.new_calls) == 1


@pytest.mark.parametrize("status", ["running", "failed"])
async def test_existing_owner_blocks_new_nonce_and_wire_version(case, monkeypatch, status):
    c = case
    async with c.factory() as session:
        session.add(
            db.WorkflowRunModel(
                id=service._identity(c.project, c.failed_id),
                project_id=c.project.id,
                workflow_type=service.WORKFLOW,
                scope_type="workflow_run",
                scope_id=c.failed_id,
                status=status,
                metadata_json={"purpose": service.PURPOSE},
            )
        )
        await session.commit()
    monkeypatch.setattr(service, "VERSION", "cosmetic-version-change")
    with pytest.raises(ValueError, match="terminal_or_inflight"):
        await invoke(c, authorization_ref="a different authorization string")
    assert not c.new_calls


async def test_transport_failure_cannot_retry_another_physical_call(case):
    c = case
    c.mode = "transport_error"
    with pytest.raises((TimeoutError, RuntimeError)):
        await invoke(c)
    assert len(c.new_calls) == 1
    with pytest.raises(ValueError, match="terminal_or_inflight"):
        await invoke(c)
    assert len(c.new_calls) == 1
    await assert_old_unchanged(c)


async def test_old_grant_cannot_be_rewritten_during_new_paid_return(case):
    c = case

    async def change(request):
        async with c.factory() as other:
            row = await other.get(db.ExecutionBudgetModel, c.scope)
            metadata = deepcopy(row.metadata_json)
            own = request.metadata["single_dispatch_authorization_id"]
            old_id = next(key for key in metadata["single_dispatch_grants"] if key != own)
            metadata["single_dispatch_grants"][old_id]["status"] = "available"
            row.metadata_json = metadata
            await other.commit()

    c.after = change
    with pytest.raises(ValueError, match="historical_grant_changed"):
        await invoke(c)
    assert len(c.new_calls) == 1


async def test_matching_finite_aggregate_cannot_hide_wrong_old_fee(case):
    c = case
    async with c.factory() as session:
        row = await session.get(db.ExecutionBudgetModel, c.scope)
        entries = {}
        for item in c.old_state["protocol"]["attempts"]:
            run = await session.get(db.LlmRunModel, UUID(item["completion"]["llm_run_id"]))
            paid = run.metadata_json["execution_receipt"]
            aid = paid["attempts"][0]["attempt_id"]
            entries[aid] = {
                "category": "hard_repair",
                "status": "completed",
                "operation_key": paid["operation_key"],
                "input_hash": run.prompt_hash,
                "model": run.model_name,
                "actual_micro_cny": run.input_tokens + 4 * run.output_tokens,
            }
        entries[next(iter(entries))]["actual_micro_cny"] -= 1
        journal = {
            "calls": 2,
            "counts": {"primary": 0, "transport_retry": 0, "hard_repair": 2},
            "spent_micro_cny": sum(item["actual_micro_cny"] for item in entries.values()),
            "held_micro_cny": 0,
            "attempts": entries,
        }
        row.metadata_json = {
            **row.metadata_json,
            "active_finite_book_run": "wrong-fee",
            "finite_book_runs": {"wrong-fee": journal},
        }
        await session.commit()
    with pytest.raises(ValueError, match="finite_charged_closure"):
        await invoke(c, prepare=True)
    assert not c.new_calls


@pytest.fixture
def output_case(tmp_path, monkeypatch):
    from bestseller.settings import LLMRoleSettings, load_settings

    def forbidden(*args, **kwargs):
        raise AssertionError("Output preflight has no database/network/provider")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(llm, "complete_text", forbidden)
    settings = load_settings(env={})
    settings.llm.planner = LLMRoleSettings(
        model="openai/MiniMax-M3.1-Flash",
        temperature=0.25,
        timeout_seconds=900,
        max_tokens=32768,
        api_base="https://api.minimaxi.com/v1",
        reasoning_effort="medium",
    )
    settings.artifact_store.mode = "local"
    settings.artifact_store.local_dir = str(tmp_path)
    settings.artifact_store.retain_prompt_payloads = True
    project = db.ProjectModel(
        id=uuid4(),
        slug="scratch-output-capacity",
        title="Independent fixture",
        genre="fantasy",
        language="zh-CN",
        target_chapters=1,
        metadata_json={"execution_scope_id": "output:test"},
    )
    # A full all-field scalar echo of unrelated fixture text, not production data.
    line = "观察员按记录核对设备状态并在公共区域说明交接结果。" * 2
    candidate = {
        "chapters": [
            {
                "chapter_number": 1,
                "chapter_goal": line,
                "chapter_concrete_actions": [line + str(i) for i in range(324)],
            }
        ]
    }
    observations = [
        {
            "hard_violations": [
                {
                    "path": "chapters[0]",
                    "source_refs": ["/candidate_payload/chapters/0/chapter_goal"],
                }
            ]
        }
    ]
    approved = {
        "book_spec": {
            "content": {
                "author_constraint": "No secret abilities; keep the public evidence visible.",
                "catalog": {"$ref": "literal authored object, never a substitution"},
                "optional": None,
                "approved": False,
            }
        }
    }
    catalog = {
        "/approved_book_spec/author_constraint": {
            "role": "canonical_author",
            "allowed_codes": ["ABILITY_RULE_CONFLICT"],
            "value_sha256": content_hash(approved["book_spec"]["content"]["author_constraint"]),
        }
    }
    authority = {
        "approved_sources": approved,
        "source_authority": {"canonical_author": {"/approved_book_spec": ["/author_constraint"]}},
        "source_authority_allowed_codes": {"canonical_author": ["ABILITY_RULE_CONFLICT"]},
        "reference_contract": {
            "schema": "planning-admission-exact-leaf-refs.v1",
            "catalog": catalog,
            "catalog_sha256": content_hash(catalog),
        },
    }
    field_contract = pure.build_projection_consistency_contract(
        candidate, observations, source_authority=authority
    )
    assert len(field_contract["id_to_path"]) == 325
    prepared = {
        "candidate": candidate,
        "field_contract": field_contract,
        "tools": pure.tools_for_projection_consistency(field_contract),
        "approved_sources": approved,
        "observations": observations,
        "source_evidence": {"unchanged_original_scope": True},
        "family": {"failed_admission_workflow_id": str(uuid4())},
    }
    return SimpleNamespace(settings=settings, project=project, prepared=prepared, identity=uuid4())


def output_request(c, *, verify_retention=True):
    return service._build_request(
        c.settings,
        c.project,
        c.identity,
        c.prepared,
        actor=AUTH["actor"],
        authorization_ref=AUTH["authorization_ref"],
        verify_retention=verify_retention,
    )


async def test_complete_325_field_calibrated_echo_reserves_answer_growth_and_thinking(output_case):
    c = output_case
    request, contract, binding = output_request(c)
    receipt = contract["output_capacity"]
    allowance = contract["output_budget"]
    assert receipt["required_field_count"] == 325 and receipt["answer_tokens"] > 27000
    expected = math.ceil(receipt["answer_tokens"] * 1.25) + 16384
    assert request.max_tokens_override == allowance["total_output_tokens"] == max(32768, expected)
    assert request.max_tokens_override > 32768
    assert contract["effective_input"]["max_tokens"] == request.max_tokens_override
    assert contract["capacity"]["output_reserve_tokens"] == request.max_tokens_override
    assert binding["authority_evidence"]["output_capacity_sha256"] == content_hash(receipt)
    assert request.metadata["planning_consistency_output_capacity"] == receipt
    assert request.metadata[service.WITNESS] == content_hash(binding)
    assert c.settings.llm.planner.max_tokens == 32768
    assert c.settings.llm.planner.reasoning_effort == "medium"
    assert not receipt["provider_usage_claimed"]


async def test_output_reserve_uses_canonical_model_bound_and_still_enforces_current_finite_capacity(
    output_case,
):
    from bestseller.services import finite_book_budget as finite
    from bestseller.services.llm_capacity import ModelRequestCapacityError

    c = output_case
    request, contract, _ = output_request(c)
    with finite.bind_run(
        "inert-output-preflight", project_id=c.project.id, stage="planning", capacity_limit=65536
    ):
        rebuilt, read_contract, _ = output_request(c, verify_retention=False)
        assert rebuilt == request and read_contract == contract
        with pytest.raises(ModelRequestCapacityError):
            output_request(c)


async def test_unknown_output_ceiling_fails_closed_before_dispatch(output_case):
    c = output_case
    c.settings.llm.planner.model = "openai/unknown-output-capacity-fixture"
    with pytest.raises(ValueError, match="unknown_model_output_capacity"):
        output_request(c)


async def test_output_echo_cannot_omit_a_required_field(output_case):
    c = output_case
    contract = deepcopy(c.prepared["field_contract"])
    contract["id_to_path"].pop(next(iter(contract["id_to_path"])))
    with pytest.raises(ValueError, match="complete_output_echo_required"):
        service._answer_capacity(c.prepared["candidate"], contract)


async def test_wire_projection_is_invertible_without_losing_authored_values_or_contract_fields(
    output_case,
):
    c = output_case
    before = deepcopy(c.prepared)
    request, contract, _ = output_request(c)
    packet = json.loads(request.user_prompt[len(service.MARKER) :])
    logical = decode_lossless_source(packet)
    assert packet["references"] == []
    assert logical["approved_story_sources"] == before["approved_sources"]
    assert logical["candidate_payload"] == before["candidate"]
    assert logical["untrusted_native_observations"] == before["observations"]
    projected = logical["correction_contract"]
    full = before["field_contract"]
    audit = projected["wire_projection_audit"]
    assert not audit["hash_reference_expansion_allowed"] and not audit["source_authority_granted"]
    assert audit["historical_catalog_role"] == "audit_only_not_story_source_or_reference_authority"
    assert audit["approved_sources_sha256"] == content_hash(logical["approved_story_sources"])
    assert audit["observations_sha256"] == content_hash(logical["untrusted_native_observations"])
    assert audit == contract["wire_projection_audit"]
    assert "never expand them" in request.system_prompt
    # Reinsert only the audited duplicate sections and historical technical
    # index: every other original field/value must already be on the wire.
    restored = deepcopy(projected)
    del restored["wire_projection_audit"]
    for key in ("schema", "observations", "source_authority_sha256", "contract_sha256"):
        restored[key] = deepcopy(full[key])
    restored["source_authority"]["approved_sources"] = deepcopy(
        full["source_authority"]["approved_sources"]
    )
    restored["source_authority"]["reference_contract"]["catalog"] = deepcopy(
        full["source_authority"]["reference_contract"]["catalog"]
    )
    assert restored == full and c.prepared == before
    assert request.tools == pure.tools_for_projection_consistency(full)


@pytest.mark.parametrize("change", ["sources", "observations", "catalog"])
async def test_wire_projection_rejects_mismatched_duplicate_or_historical_catalog(
    output_case, change
):
    c = output_case
    if change == "sources":
        c.prepared["approved_sources"]["book_spec"] = {"content": {"author_constraint": "changed"}}
        # Break aliasing explicitly: the full contract is an independent snapshot.
    elif change == "observations":
        c.prepared["observations"] = [{"untrusted": "different native return"}]
    else:
        full = c.prepared["field_contract"]
        full["source_authority"]["reference_contract"]["catalog"] = {"tampered": {}}
        full["source_authority_sha256"] = content_hash(full["source_authority"])
        full["contract_sha256"] = content_hash(
            {k: v for k, v in full.items() if k != "contract_sha256"}
        )
    with pytest.raises(
        ValueError,
        match=r"wire_approved_sources_mismatch|wire_observations_mismatch|wire_historical_catalog_changed",
    ):
        output_request(c)


@pytest.mark.parametrize("root", [None, 2])
async def test_validation_context_selects_actual_volume_two_without_rebinding_numbers(root):
    candidate = {
        "chapters": [
            {"chapter_number": 1, "volume_number": 2},
            {"chapter_number": 2, "volume_number": 2},
        ]
    }
    if root is not None:
        candidate["volume_number"] = root
    sources = {
        "cast_spec": SimpleNamespace(content={"locked": "Mira"}),
        "volume_plan": SimpleNamespace(
            content=[
                {"volume_number": 1, "marker": "unrelated"},
                {"volume_number": 2, "marker": "the selected approved source"},
            ]
        ),
    }
    before = deepcopy(candidate)
    context = service._validation_context(candidate, sources)
    assert context["volume_number"] == context["volume_entry"]["volume_number"] == 2
    assert context["chapter_number_offset"] == 1 and context["expected_count"] == 2
    assert context["volume_entry"]["marker"] == "the selected approved source"
    assert candidate == before


@pytest.mark.parametrize("volumes,root", [([1, 2], None), ([2, 2], 1), ([None, None], None)])
async def test_ambiguous_or_mixed_volume_is_explicitly_unsupported_before_provider(volumes, root):
    candidate = {
        "chapters": [
            {"chapter_number": index + 1, **({"volume_number": value} if value is not None else {})}
            for index, value in enumerate(volumes)
        ]
    }
    if root is not None:
        candidate["volume_number"] = root
    sources = {
        "cast_spec": SimpleNamespace(content={}),
        "volume_plan": SimpleNamespace(content=[{"volume_number": 1}, {"volume_number": 2}]),
    }
    with pytest.raises(ValueError, match=r"mixed_volume|volume_identity_required"):
        service._validation_context(candidate, sources)
