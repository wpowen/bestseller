"""SQLite and retained physical proof; no PG, gateway or financial writes in migration."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import JSON, MetaData, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.schema import CreateTable
from test_retained_candidate_recovery import case as case

from bestseller.infra.db.models import (
    ChapterDraftVersionModel,
    ExecutionAttemptModel,
    ExecutionBudgetModel,
    ExecutionOperationModel,
    LlmRunModel,
    RewriteTaskModel,
    WorkflowRunModel,
)
from bestseller.services import llm
from bestseller.services import retained_candidate_continuation as custody
from bestseller.services import retained_candidate_recovery as base
from bestseller.services import retained_patch_proposal_migration as migration
from bestseller.services import retained_review_input_proof as input_proof
from bestseller.services.deterministic_post_write_audit import (
    DeterministicAuditFinding,
    DeterministicAuditReport,
)
from bestseller.services.lossless_rewrite_prompt import prepare_source_complete_prompt
from bestseller.services.quality_report_claim import graded_text_fingerprint
from bestseller.services.retained_candidate_patch import TOOL_NAME, prepare_candidate_patch
from bestseller.services.rewrite_patch import text_hash
from bestseller.settings import load_settings

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]
SOURCE_SCHEMA = "review-source-context.v3"
INITIAL = (
    "The keeper opens the copper gate.\n\nA gull calls above the harbor.\n\n"
    "She hands the copper key to the waiting pilot.\n\nThe rain continues.\n\n"
    "The pilot boards the boat at dawn."
)
REPLACEMENTS = [
    "She braces the key in the lock and pushes the gate aside.",
    "The pilot receives the key from her open palm.",
    "At dawn the boat takes the pilot aboard.",
]


def finding(line):
    return DeterministicAuditFinding(
        "SCENE_CARD_PROSE_COPIED",
        "high",
        "Actual scene-card sentence.",
        line,
        1,
        "Preserve the event with fresh syntax.",
    )


@pytest.fixture
async def physical(case, monkeypatch, tmp_path, request):
    state = case
    state.bad_payload = None
    state.bad_raw = False
    state.commentary = ""
    mode = getattr(request, "param", None)
    if mode == "bad_late_patch":
        state.bad_payload = lambda value: value["patches"][-1].update(target_id="foreign")
    elif mode == "foreign_schema":
        state.bad_payload = lambda value: value.update(schema="arbitrary-invalid-schema")
    elif mode == "competing_raw":
        state.bad_raw = True
    elif mode == "plain_commentary":
        state.commentary = "The three listed paragraphs require fresh expression. Use the tool."
    elif mode == "structured_commentary":
        state.commentary = 'Another proposal: {"schema":"other", "patches":[]}'
    settings = load_settings(env={}).model_copy(deep=True)
    settings.llm.mock = False
    settings.llm.editor.model = "openai/MiniMax-fixture"
    settings.llm.editor.model_override = None
    settings.artifact_store.mode = "local"
    settings.artifact_store.local_dir = tmp_path / "physical"
    settings.artifact_store.retain_prompt_payloads = True
    monkeypatch.setattr(input_proof, "apply_runtime_llm_profile", lambda value: value)

    def ddl(connection):
        for model in (ExecutionBudgetModel, ExecutionOperationModel, ExecutionAttemptModel):
            table = model.__table__.to_metadata(MetaData())
            for column in table.c:
                if isinstance(column.type, JSONB):
                    column.type = JSON()
            connection.execute(CreateTable(table, include_foreign_key_constraints=[]))

    async with case.session.bind.begin() as connection:
        await connection.run_sync(ddl)
    scope = f"project:{case.ids['project_id']}"
    case.session.add(
        ExecutionBudgetModel(
            scope_id=scope,
            max_calls=300,
            max_tokens=900000,
            calls_used=147,
            tokens_charged=100000,
            deadline_at=datetime.now(UTC) + timedelta(days=1),
            status="active",
            metadata_json={"old_paid_history": "unchanged"},
        )
    )
    case.candidate.content_md = INITIAL
    case.candidate.word_count = base.reviews.count_words(INITIAL)
    case.task.metadata_json = {
        **case.task.metadata_json,
        "candidate_body_sha256": text_hash(INITIAL),
        "candidate_gate_graded": graded_text_fingerprint(INITIAL),
    }
    case.original_report.report_json = {
        **case.original_report.report_json,
        "graded": graded_text_fingerprint(INITIAL),
    }
    await case.session.commit()
    source_loader = base.load_review_source_context

    async def sources(*args, **kwargs):
        return {"schema": SOURCE_SCHEMA, **await source_loader(*args, **kwargs)}

    monkeypatch.setattr(base, "load_review_source_context", sources)
    monkeypatch.setattr(base, "prepare_source_complete_prompt", prepare_source_complete_prompt)

    def audit(**kw):
        findings = (finding(1), finding(5), finding(9)) if kw["content_md"] == INITIAL else ()
        return DeterministicAuditReport(2, findings, not findings, {}), type(
            "Band", (), {"hard_min": 1, "hard_max": 10000}
        )()

    monkeypatch.setattr(base.reviews, "_audit_chapter_rewrite_candidate", audit)

    async def paid(request, replacements):
        prepared = prepare_candidate_patch(INITIAL, [finding(1), finding(5), finding(9)])
        payload = {
            "schema": SOURCE_SCHEMA,
            "body_sha256": prepared["body_sha256"],
            "catalog_sha256": prepared["catalog_sha256"],
            "patches": [
                {"target_id": t["target_id"], "replacement": replacement}
                for t, replacement in zip(prepared["targets"], replacements, strict=True)
            ],
        }
        if state.bad_payload:
            state.bad_payload(payload)
        rid, attempt = uuid4(), uuid4().hex
        role = settings.llm.editor
        digest = llm._effective_input_hash(request, role)
        op_key = hashlib.sha256(f"{scope}\n{request.operation_id}:sample:0".encode()).hexdigest()
        origin = {
            "project_id": str(request.project_id),
            "workflow_run_id": str(request.workflow_run_id),
            "logical_role": "editor",
            "prompt_template": request.prompt_template,
            "prompt_version": request.prompt_version,
            "prompt_hash": digest,
        }
        metadata = {"paid_call_origin": origin, "effective_input_receipt": {"hash": digest}}
        result = llm.LLMCompletionResult(
            content=state.commentary,
            provider="openai",
            model_name=role.model,
            finish_reason="tool_calls",
            input_tokens=91,
            output_tokens=17,
            llm_run_id=rid,
            tool_calls=[
                {
                    "id": "call-fixture",
                    "type": "function",
                    "function": {
                        "name": TOOL_NAME,
                        "arguments": json.dumps({"patch_json": json.dumps(payload)}),
                    },
                }
            ],
            raw_message={"role": "assistant", "content": state.commentary},
            metadata=metadata,
        )
        result.raw_message["tool_calls"] = deepcopy(result.tool_calls)
        if state.bad_raw:
            result.raw_message["tool_calls"][-1]["function"]["arguments"] += " "
        with llm.bind_project_prompt_audit(request.project_id, reason="offline-custody-fixture"):
            req_ref, resp_ref, reason, replayable = llm._persist_prompt_payloads(
                settings=settings,
                request=request,
                llm_run_id=rid,
                configured_model=role.model,
                effective_model=role.model,
                provider="openai",
                effective_temperature=role.temperature,
                effective_max_tokens=4096,
                effective_timeout_seconds=role.timeout_seconds,
                effective_stream=False,
                effective_thinking_type=llm._effective_thinking_type(role),
                effective_reasoning_effort=role.reasoning_effort,
                content=state.commentary,
                tool_call_count=1,
                finish_reason="tool_calls",
                fallback_used=False,
                fallback_source=None,
                input_tokens=91,
                output_tokens=17,
                tool_calls=result.tool_calls,
                raw_message=result.raw_message,
            )
        assert replayable, reason
        until = datetime.now(UTC) + timedelta(days=1)
        transport_key = hashlib.sha256(
            f"transport-result.v1\n{scope}\n{attempt}".encode()
        ).hexdigest()
        raw = result.model_dump(mode="json")
        root = {
            **deepcopy(raw),
            "latency_ms": 888,
            "metadata": {
                **deepcopy(metadata),
                "runtime_provenance": "distinct retained root envelope",
            },
        }
        case.session.add_all(
            [
                ExecutionOperationModel(
                    key=op_key,
                    scope_id=scope,
                    input_hash=digest,
                    status="completed",
                    owner="fixture",
                    lease_until=until,
                    result_json=root,
                ),
                ExecutionOperationModel(
                    key=transport_key,
                    scope_id=scope,
                    input_hash=digest,
                    status="transport_result",
                    owner="fixture",
                    lease_until=until,
                    result_json={
                        "operation_key": op_key,
                        "attempt_id": attempt,
                        "input_hash": digest,
                        "result": raw,
                    },
                ),
                ExecutionAttemptModel(
                    id=attempt,
                    operation_key=op_key,
                    scope_id=scope,
                    model_name=role.model,
                    input_hash=digest,
                    reserved_tokens=4096,
                    actual_tokens=108,
                    status="completed",
                    created_at=datetime.now(UTC),
                ),
                LlmRunModel(
                    id=rid,
                    project_id=request.project_id,
                    workflow_run_id=request.workflow_run_id,
                    logical_role="editor",
                    provider="openai",
                    model_name=role.model,
                    prompt_template=request.prompt_template,
                    prompt_version=request.prompt_version,
                    prompt_hash=digest,
                    finish_reason="tool_calls",
                    request_payload_ref=req_ref,
                    response_payload_ref=resp_ref,
                    metadata_json={
                        **metadata,
                        "execution_receipt": {
                            "scope_id": scope,
                            "operation_key": op_key,
                            "attempts": [{"attempt_id": attempt, "status": "completed"}],
                        },
                    },
                ),
            ]
        )
        await case.session.flush()
        return result

    async def old_complete(_session, _settings, original_request):
        return await paid(original_request, REPLACEMENTS)

    monkeypatch.setattr(base, "complete_text", old_complete)
    failed = await base.repair_retained_candidate(
        case.session, settings, **case.ids, requested_by="original-fixture", execute=True
    )
    assert (
        failed["status"] == "failed"
        and failed["receipt"]["error_code"] == "candidate_patch_envelope_invalid"
    )
    assert "new_draft_id" not in failed["receipt"]
    state.original_child_id = UUID(failed["child_task_id"])
    state.original_wf_id = UUID(failed["workflow_id"])
    state.settings = settings
    state.args = dict(
        project_id=case.project.id,
        chapter_id=case.chapter.id,
        original_child_task_id=state.original_child_id,
        requested_by="authorized-operator",
        evidence_ref="local:physical-proof-fixture",
    )
    state.files = {p: p.read_bytes() for p in settings.artifact_store.local_dir.rglob("*.json")}

    async def forbidden(*_args, **_kwargs):
        pytest.fail("migration must not reach a provider")

    monkeypatch.setattr(base, "complete_text", forbidden)
    monkeypatch.setattr(llm, "complete_text", forbidden)
    state.writes.clear()
    yield state


async def protected(state):
    records = {}
    for model in (
        ExecutionBudgetModel,
        ExecutionOperationModel,
        ExecutionAttemptModel,
        LlmRunModel,
        RewriteTaskModel,
    ):
        records[model.__tablename__] = [
            custody._row_hash(row)
            for row in await state.session.scalars(
                select(model)
                .order_by(list(model.__table__.primary_key.columns)[0])
                .execution_options(populate_existing=True)
            )
        ]
    old = await base._get(state.session, WorkflowRunModel, state.original_wf_id)
    records["original_workflow"] = custody._row_hash(old)
    return records


async def invoke(state, *, execute=False, plan=None, **changes):
    return await migration.migrate_untrusted_patch_proposal(
        state.session,
        state.settings,
        **{**state.args, **changes},
        execute=execute,
        expected_plan_sha256=plan,
    )


async def test_inspect_is_exact_readonly_and_old_submission_stays_invalid(physical):
    before = await protected(physical)
    plan = await invoke(physical)
    assert plan == await invoke(physical)
    assert plan["database_writes"] == plan["provider_calls"] == 0
    assert len(plan["proposal"]["patches"]) == 3
    assert plan["extraction"]["original_submission_still_invalid"]
    assert plan["extraction"]["original_invalid_schema"] == SOURCE_SCHEMA
    assert plan["proposal"]["schema"] == "retained-candidate-patch.v1"
    assert not physical.writes and await protected(physical) == before
    assert all(path.read_bytes() == raw for path, raw in physical.files.items())


async def test_execute_preserves_old_custody_and_finance_caller_commits_then_idempotent(physical):
    before = await protected(physical)
    old_chapter_meta = deepcopy(physical.chapter.metadata_json)
    plan = await invoke(physical)
    result = await invoke(physical, execute=True, plan=plan["plan_sha256"])
    assert result["status"] == "completed" and not result["replay"]
    assert result["formal_review_required"] and not result["adoption_authorized"]
    assert not result["grants_credit"] and result["provider_calls"] == 0
    # Savepoint does not own commit: an observer cannot see the staged workflow.
    async with physical.factory() as observer:
        assert await observer.get(WorkflowRunModel, UUID(result["workflow_id"])) is None
    await physical.session.commit()
    assert await protected(physical) == before
    assert all(path.read_bytes() == raw for path, raw in physical.files.items())
    await physical.session.refresh(physical.chapter)
    assert physical.chapter.metadata_json == old_chapter_meta
    current = await physical.session.scalar(
        select(ChapterDraftVersionModel).where(ChapterDraftVersionModel.is_current.is_(True))
    )
    assert str(current.id) == result["new_draft_id"] and current.promoted_at is None
    assert current.promotion_state == "candidate" and current.content_md != INITIAL
    assert "A gull calls above the harbor.\n\n" in current.content_md
    assert result["proposal"]["patches"] == plan["proposal"]["patches"]
    physical.writes.clear()
    repeated = await invoke(physical, execute=True, plan=plan["plan_sha256"])
    assert repeated["replay"] and repeated["new_draft_id"] == result["new_draft_id"]
    assert not physical.writes


@pytest.mark.parametrize("gate", ["blocked", "missing", "error"])
async def test_gate_failure_retains_noncurrent_candidate_without_reopening(physical, gate):
    before = await protected(physical)
    plan = await invoke(physical)
    physical.gate = gate
    result = await invoke(physical, execute=True, plan=plan["plan_sha256"])
    assert result["status"] == "failed" and not result["working_pointer_selected"]
    await physical.session.commit()
    assert await protected(physical) == before
    draft = await physical.session.get(ChapterDraftVersionModel, UUID(result["new_draft_id"]))
    assert not draft.is_current and draft.promotion_state == "candidate"
    await physical.session.refresh(physical.parent)
    assert physical.parent.is_current
    again = await invoke(physical, execute=True, plan=plan["plan_sha256"])
    assert again["replay"] and again["status"] == "failed"


@pytest.mark.parametrize(
    "physical", ["bad_late_patch", "foreign_schema", "competing_raw"], indirect=True
)
async def test_paid_but_invalid_other_domain_never_extracts(physical):
    before = await protected(physical)
    with pytest.raises(ValueError):
        await invoke(physical)
    assert not physical.writes and await protected(physical) == before


@pytest.mark.parametrize(
    "fault",
    [
        "source",
        "chapter",
        "project",
        "plan",
        "executor",
        "raw_task",
        "old_error",
        "paid_root",
        "physical_response",
    ],
)
async def test_binding_or_proof_drift_failclosed_before_migration(physical, fault):
    plan = await invoke(physical)
    changes = {}
    if fault == "source":
        physical.source_revision += 1
    elif fault in {"chapter", "project"}:
        changes[fault + "_id"] = uuid4()
    elif fault == "plan":
        pass
    elif fault in {"executor", "old_error"}:
        child = await physical.session.get(RewriteTaskModel, physical.original_child_id)
        wf = await physical.session.get(WorkflowRunModel, physical.original_wf_id)
        meta = deepcopy(child.metadata_json)
        if fault == "executor":
            meta["fence"]["executor_source_sha256"] = "changed"
            meta["receipt"]["fence"]["executor_source_sha256"] = "changed"
        else:
            meta["receipt"]["error_code"] = "candidate_patch_item_invalid"
        child.metadata_json = wf.metadata_json = meta
    elif fault == "raw_task":
        physical.task.instructions += " changed"
    elif fault == "paid_root":
        run = await physical.session.scalar(
            select(LlmRunModel).where(LlmRunModel.workflow_run_id == physical.original_wf_id)
        )
        key = run.metadata_json["execution_receipt"]["operation_key"]
        row = await physical.session.get(ExecutionOperationModel, key)
        row.result_json = {**row.result_json, "content": "competing grade"}
    else:
        run = await physical.session.scalar(
            select(LlmRunModel).where(LlmRunModel.workflow_run_id == physical.original_wf_id)
        )
        path = physical.settings.artifact_store.local_dir / run.response_payload_ref
        value = json.loads(path.read_text())
        value["tool_calls"][0]["function"]["arguments"] += " "
        path.write_text(json.dumps(value))
    await physical.session.commit()
    before = await protected(physical)
    physical.writes.clear()
    with pytest.raises(ValueError):
        await invoke(
            physical,
            execute=True,
            plan="wrong" if fault == "plan" else plan["plan_sha256"],
            **changes,
        )
    assert not physical.writes and await protected(physical) == before


async def test_only_broad_policy_delta_allowed_no_grade_alias(physical, monkeypatch):
    from bestseller.services import pipelines

    monkeypatch.setattr(
        pipelines, "chapter_review_recovery_source_hash", lambda: "new-broad-policy"
    )
    plan = await invoke(physical)
    assert plan["original_fence"]["policy_sha256"] == "fixture-policy"
    assert plan["fence"]["policy_sha256"] == "new-broad-policy"
    assert all(
        plan["original_fence"][key] == plan["fence"][key]
        for key in plan["fence"]
        if key != "policy_sha256"
    )


async def test_gate_time_source_change_rolls_back_own_writes(physical, monkeypatch):
    plan = await invoke(physical)
    before = await protected(physical)
    old = base._evidence

    async def evidence(*args):
        value = await old(*args)
        physical.source_revision += 1
        return value

    monkeypatch.setattr(base, "_evidence", evidence)
    with pytest.raises(ValueError, match="source_or_executor_changed|source_or_parent_changed"):
        await invoke(physical, execute=True, plan=plan["plan_sha256"])
    assert await protected(physical) == before
    assert not list(
        await physical.session.scalars(
            select(WorkflowRunModel).where(WorkflowRunModel.workflow_type == migration.STRATEGY)
        )
    )


async def test_caller_pending_work_refused_not_rolled_back(physical):
    physical.project.title = "Caller owned change"
    with pytest.raises(ValueError, match="clean_session_required"):
        await invoke(physical)
    assert (
        physical.project.title == "Caller owned change"
        and physical.project in physical.session.dirty
    )


@pytest.mark.parametrize("physical", ["plain_commentary"], indirect=True)
async def test_paid_prose_discarded_exact_raw_and_custody_preserved(physical):
    before = await protected(physical)
    plan = await invoke(physical)
    comment = plan["extraction"]["commentary"]
    assert comment["status"] == "non_authoritative"
    assert comment["content_chars"] == len(physical.commentary)
    assert comment["content_sha256"] == text_hash(physical.commentary)
    assert comment["used_as_proposal"] is False
    assert comment["selected_proposal_origin"] == "unique_native_tool_arguments"
    result = await invoke(physical, execute=True, plan=plan["plan_sha256"])
    assert result["status"] == "completed" and result["proposal"] == plan["proposal"]
    await physical.session.commit()
    assert await protected(physical) == before
    assert all(path.read_bytes() == raw for path, raw in physical.files.items())
    again = await invoke(physical, execute=True, plan=plan["plan_sha256"])
    assert again["replay"]


@pytest.mark.parametrize("physical", ["structured_commentary"], indirect=True)
async def test_paid_alternative_structure_rejected_before_write(physical):
    before = await protected(physical)
    with pytest.raises(ValueError, match="competing_structured_content"):
        await invoke(physical)
    assert not physical.writes and await protected(physical) == before


@pytest.mark.parametrize(
    "text",
    [
        '{"schema":"other","patches":[]}',
        'Comment before {"patch_json":"another"} after',
        '[{"target_id":"foreign","replacement":"another"}]',
        "```json\n{}\n```",
        "~~~\nreplacement\n~~~",
        "schema: other",
        '"body_sha256" = "other"',
        "catalog_sha256: other",
        "target_id: other\nreplacement: another",
        "<patches>another</patches>",
        "CP-" + "a" * 64 + ": an alternative replacement",
        '"A JSON string"',
    ],
)
async def test_commentary_classifier_rejects_ambiguous_submissions(text):
    with pytest.raises(ValueError, match="competing_structured_content"):
        migration._commentary(text)


async def test_commentary_cannot_hide_content_or_tools_conflict(physical):
    plan = await invoke(physical)
    child = await physical.session.get(RewriteTaskModel, physical.original_child_id)
    completion = child.metadata_json["receipt"]["completion"]
    paid = SimpleNamespace(**deepcopy(completion))
    paid.content = "Non-authoritative prose."
    # Exact retained raw still says empty content: it is not silently repaired.
    source = migration._paid_source(
        child.metadata_json["request"]["user_prompt"],
        await base.load_review_source_context(
            None,
            project=physical.project,
            chapter=physical.chapter,
            context_packet=await base.build_chapter_writer_context(),
        ),
    )
    with pytest.raises(ValueError, match="raw_message_conflict"):
        migration._extract(
            paid,
            source,
            child.metadata_json["request"]["user_prompt"],
            child.metadata_json["prepared_patch"],
            INITIAL,
        )
    assert plan["extraction"]["commentary"]["status"] == "absent"


async def test_actual_retained_return_commentary_is_only_audited_not_proposal_input():
    root = Path(__file__).resolve().parents[2]
    physical = (
        root / "artifacts/runtime/llm_payloads/1a79a9ef-4bfc-4b2e-b3ee-ed7820a11e07.response.json"
    )
    request_path = physical.with_name(physical.name.replace(".response.", ".request."))
    if not physical.exists() or not request_path.exists():
        pytest.skip("local retained physical witness is not part of the portable fixture suite")
    original_bytes = physical.read_bytes()
    response = json.loads(original_bytes)
    user = json.loads(request_path.read_text())["messages"]["user"]["text"]
    objects, pos = [], 0
    decoder = json.JSONDecoder()
    while (start := user.find("{", pos)) >= 0:
        try:
            value, end = decoder.raw_decode(user, start)
        except ValueError:
            pos = start + 1
            continue
        objects.append(value)
        pos = end
    source = next(
        value
        for value in objects
        if isinstance(value, dict)
        and value.get("schema") == "review-rewrite-adopted-source.v3-editable-plan"
    )
    task = next(
        value
        for value in objects
        if isinstance(value, dict) and value.get("schema") == "retained-candidate-patch.v1"
    )
    prepared = prepare_candidate_patch(task["content_md"], task["findings"])
    assert prepared["targets"] == task["targets"]
    paid = SimpleNamespace(
        content=response["content"]["text"],
        tool_calls=response["tool_calls"],
        raw_message=response["raw_message"],
    )
    body, proposal, extraction, application = migration._extract(
        paid, source, user, prepared, task["content_md"]
    )
    original_payload = json.loads(
        json.loads(paid.tool_calls[0]["function"]["arguments"])["patch_json"]
    )
    assert extraction["commentary"]["content_chars"] == 2497
    assert (
        extraction["commentary"]["content_sha256"]
        == "de8e8c55510bbdb749bd0379f0a5e8fa33712ebd8cf9bacc178726ffa2449237"
    )
    assert proposal["patches"] == original_payload["patches"] and len(proposal["patches"]) == 3
    assert extraction["original_submission_still_invalid"] and not extraction["grants_credit"]
    assert body != task["content_md"] and application["untouched_bytes_verified"]
    assert physical.read_bytes() == original_bytes
