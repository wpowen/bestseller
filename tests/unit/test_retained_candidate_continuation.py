"""Real SQLite rows/physical files and inert returns; no PG or paid providers."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
import hashlib
import json
from pathlib import Path
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
from bestseller.services import retained_candidate_continuation as continuation
from bestseller.services import retained_candidate_recovery as base
from bestseller.services import retained_review_input_proof as input_proof
from bestseller.services.deterministic_post_write_audit import (
    DeterministicAuditFinding,
    DeterministicAuditReport,
)
from bestseller.services.quality_report_claim import graded_text_fingerprint
from bestseller.services.retained_candidate_patch import TOOL_NAME, prepare_candidate_patch
from bestseller.services.rewrite_patch import text_hash
from bestseller.settings import load_settings

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]
INITIAL = (
    "The keeper opens the copper gate.\n\nA gull calls above the harbor.\n\n"
    "She hands the copper key to the waiting pilot."
)
FIXED = "She braces the key in the lock and pushes the gate aside."
STILL_COPY = "She hands the copper key to the waiting pilot, then steps back."
FINAL = "The pilot receives the key from her open palm."
FIRST = FIXED + "\n\nA gull calls above the harbor.\n\n" + STILL_COPY


def finding(line, code="SCENE_CARD_PROSE_COPIED"):
    return DeterministicAuditFinding(
        code,
        "high",
        "A retained source sentence.",
        line,
        1,
        "Preserve the actual event and deadline with fresh expression.",
    )


@pytest.fixture
async def chain(case, monkeypatch, tmp_path):
    state = case
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
            metadata_json={"old_paid_history": "retained"},
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
    state.original_task = deepcopy(case.task.metadata_json)
    state.second_mode = "success"
    state.inspected_calls = []
    state.second_calls = []
    state.source_revision = 1
    state.late_change = None

    def audit(**kw):
        body = kw["content_md"]
        findings = (
            (finding(1), finding(5), finding(5, "ENDING_HOOK_MISSING"))
            if body == INITIAL
            else (
                (finding(5), finding(5, "ENDING_HOOK_MISSING"))
                if body == FIRST
                else ((finding(5),) if state.second_mode == "nongain" else ())
            )
        )
        return DeterministicAuditReport(2, findings, not findings, {}), type(
            "Band", (), {"hard_min": 1, "hard_max": 10000}
        )()

    monkeypatch.setattr(base.reviews, "_audit_chapter_rewrite_candidate", audit)
    state.audit = audit

    async def paid(request, replacements):
        prepared = prepare_candidate_patch(
            INITIAL if request.prompt_template == "retained_candidate_paragraph_patch" else FIRST,
            [
                f
                for f in audit(
                    content_md=INITIAL
                    if request.prompt_template == "retained_candidate_paragraph_patch"
                    else FIRST
                )[0].findings
                if f.code == "SCENE_CARD_PROSE_COPIED"
            ],
        )
        payload = {
            "schema": prepared["schema"],
            "body_sha256": prepared["body_sha256"],
            "catalog_sha256": prepared["catalog_sha256"],
            "patches": [
                {"target_id": t["target_id"], "replacement": replacement}
                for t, replacement in zip(prepared["targets"], replacements, strict=True)
            ],
        }
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
            content="",
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
            raw_message={"role": "assistant", "content": ""},
            metadata=metadata,
        )
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
                content="",
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
        if state.late_change:
            await state.late_change()
        return result

    async def first_complete(_session, _settings, request):
        return await paid(request, [FIXED, STILL_COPY])

    monkeypatch.setattr(base, "complete_text", first_complete)
    first = await base.repair_retained_candidate(
        case.session, settings, **case.ids, requested_by="first-fixture", execute=True
    )
    assert first["status"] == "failed", first
    state.first = first
    state.args = {
        **case.ids,
        "previous_child_task_id": UUID(first["child_task_id"]),
        "candidate_id": UUID(first["receipt"]["new_draft_id"]),
    }
    state.settings = settings

    async def second_complete(_session, _settings, request):
        state.second_calls.append(request)
        async with state.factory() as observer:
            workflow = await observer.get(WorkflowRunModel, request.workflow_run_id)
            assert workflow.status == "running"
            assert workflow.metadata_json["claimed_batches"] == 2
        if state.second_mode == "interrupted":
            raise KeyboardInterrupt("simulated process death")
        if state.second_mode == "provider_error":
            raise RuntimeError("secret provider error")
        return await paid(
            request, [FINAL if state.second_mode != "nongain" else STILL_COPY + " again"]
        )

    monkeypatch.setattr(llm, "complete_text", second_complete)
    state.writes.clear()
    yield state


async def run(chain, execute=False, **changes):
    return await continuation.continue_retained_candidate_recovery(
        chain.session,
        chain.settings,
        **{**chain.args, **changes},
        requested_by="second-fixture",
        execute=execute,
    )


async def custody(chain):
    rows = []
    for model, key in (
        (RewriteTaskModel, chain.ids["failed_task_id"]),
        (RewriteTaskModel, chain.args["previous_child_task_id"]),
        (WorkflowRunModel, UUID(chain.first["workflow_id"])),
    ):
        row = await base._get(chain.session, model, key)
        rows.append(continuation._row_hash(row))
    return rows


async def test_dryrun_physical_replay_is_exact_readonly(chain):
    before = await custody(chain)
    plan = await run(chain)
    assert plan == await run(chain)
    assert plan["progress"]["initial_targets"] == 2 and plan["progress"]["remaining_targets"] == 1
    assert plan["progress"]["max_supported_batches"] == 2
    assert (
        plan["proof"]["first_paid"]["root_row_sha256"]
        != plan["proof"]["first_paid"]["transport_row_sha256"]
    )
    assert not chain.writes and not chain.second_calls and await custody(chain) == before


async def test_final_batch_success_preserves_first_records_and_only_selects_candidate(chain):
    before = await custody(chain)
    result = await run(chain, True)
    assert result["status"] == "completed", result
    assert len(chain.second_calls) == 1
    assert "restructure" in chain.second_calls[0].system_prompt
    assert "suffix" in chain.second_calls[0].system_prompt
    assert result["receipt"]["third_batch_allowed"] is False
    current = await chain.session.scalar(
        select(ChapterDraftVersionModel).where(ChapterDraftVersionModel.is_current.is_(True))
    )
    assert current.promotion_state == "candidate" and current.promoted_at is None
    assert current.content_md.endswith(FINAL)
    assert await custody(chain) == before
    chain.writes.clear()
    again = await run(chain, True)
    assert again["status"] == "completed" and again["replay"]
    assert not chain.writes and len(chain.second_calls) == 1


@pytest.mark.parametrize(
    "mode", ["nongain", "provider_error", "gate", "effects", "duplicates", "gate_error", "source"]
)
async def test_failed_final_batch_never_gains_third_call(chain, mode):
    before = await custody(chain)
    if mode in {"nongain", "provider_error"}:
        chain.second_mode = mode
    elif mode == "gate":
        chain.gate = "blocked"
    elif mode == "effects":
        pass  # Install the body-specific gate below.
    elif mode == "duplicates":
        chain.duplicate = True
    elif mode == "gate_error":
        chain.gate = "error"
    elif mode == "source":

        async def changed():
            chain.source_revision += 1

        chain.late_change = changed
    if mode == "duplicates":
        # This is a preflight hard failure, with no claim or send.
        with pytest.raises(ValueError, match="target_duplicates"):
            await run(chain, True)
        assert not chain.second_calls
        return
    if mode == "effects":
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(
                base.reviews,
                "audit_persisted_story_effect_realization",
                lambda _p, _c, body, **kw: {
                    "passed": body in {INITIAL, FIRST},
                    "selected_effects": ["action"],
                },
            )
            result = await run(chain, True)
    else:
        result = await run(chain, True)
    assert result["status"] == "failed", result
    chain.writes.clear()
    # A spent claim remains readable even after source loaders become unavailable.
    with pytest.MonkeyPatch.context() as patch:

        async def unavailable(*_args, **_kw):
            raise AssertionError("must not reload sources for failed claim")

        patch.setattr(base, "_sources", unavailable)
        replay = await run(chain, True)
    assert replay["status"] == "failed" and replay["replay"] and len(chain.second_calls) == 1
    assert not chain.writes and await custody(chain) == before


async def test_interrupted_claim_is_durable_and_not_redispatched(chain):
    chain.second_mode = "interrupted"
    with pytest.raises(KeyboardInterrupt):
        await run(chain, True)
    with pytest.MonkeyPatch.context() as patch:

        async def unavailable(*_args, **_kw):
            raise AssertionError("source access after crash")

        patch.setattr(base, "_sources", unavailable)
        replay = await run(chain, True)
    assert replay["status"] == "running" and replay["replay"]
    assert len(chain.second_calls) == 1


@pytest.mark.parametrize(
    "fault",
    [
        "receipt_body",
        "receipt_patch",
        "target_body",
        "first_wf",
        "first_attempt",
        "wrong_first_id",
        "parent",
        "extra_label",
        "extra_workflow",
        "response",
        "transport",
        "scope",
        "template",
    ],
)
async def test_forged_or_changed_first_proof_cannot_buy_batch(chain, fault):
    first = await base._get(chain.session, RewriteTaskModel, chain.args["previous_child_task_id"])
    wf = await base._get(chain.session, WorkflowRunModel, UUID(chain.first["workflow_id"]))
    target = await base._get(chain.session, ChapterDraftVersionModel, chain.args["candidate_id"])
    paid = await base._get(chain.session, LlmRunModel, target.llm_run_id)
    if fault in {"receipt_body", "receipt_patch"}:
        meta = deepcopy(first.metadata_json)
        if fault == "receipt_body":
            meta["receipt"]["result_body_sha256"] = "forged"
        else:
            meta["receipt"]["patch"]["untouched_bytes_verified"] = False
        first.metadata_json = wf.metadata_json = meta
    elif fault == "target_body":
        target.content_md += " forged"
    elif fault == "first_wf":
        wf.scope_id = uuid4()
    elif fault == "first_attempt":
        first.attempts = 0
    elif fault == "wrong_first_id":
        chain.args["previous_child_task_id"] = uuid4()
    elif fault == "parent":
        parent = await base._get(
            chain.session,
            ChapterDraftVersionModel,
            UUID(chain.first["receipt"]["binding"]["parent_id"]),
        )
        parent.content_md += " concurrent change"
    elif fault == "extra_label":
        chain.session.add(
            RewriteTaskModel(
                project_id=chain.ids["project_id"],
                parent_task_id=first.id,
                trigger_source_id=chain.ids["chapter_id"],
                trigger_type="alternate",
                rewrite_strategy="other-label",
                status="failed",
                attempts=1,
                instructions="No extra credit.",
                metadata_json={},
            )
        )
    elif fault == "extra_workflow":
        chain.session.add(
            WorkflowRunModel(
                project_id=chain.ids["project_id"],
                workflow_type="alternate",
                status="failed",
                requested_by="fixture",
                metadata_json={
                    "lineage_root_task_id": chain.first["receipt"]["binding"][
                        "lineage_root_task_id"
                    ]
                },
            )
        )
    elif fault == "response":
        path = Path(chain.settings.artifact_store.local_dir) / paid.response_payload_ref
        value = json.loads(path.read_text())
        value["tool_calls"][0]["function"]["arguments"] += " forged"
        path.write_text(json.dumps(value))
    elif fault == "transport":
        op = (paid.metadata_json or {})["execution_receipt"]["operation_key"]
        row = await chain.session.get(ExecutionOperationModel, op)
        row.result_json = {**row.result_json, "tool_calls": []}
    elif fault == "scope":
        paid.metadata_json = {
            **paid.metadata_json,
            "execution_receipt": {"scope_id": "project:wrong"},
        }
    elif fault == "template":
        paid.prompt_template = "other_template"
    await chain.session.commit()
    chain.writes.clear()
    with pytest.raises((ValueError, AssertionError)):
        await run(chain, True)
    assert not chain.second_calls and not chain.writes


async def test_progress_uses_ranges_not_line_numbers_or_new_target_ids():
    findings = [finding(1), finding(5)]
    prepared = prepare_candidate_patch(INITIAL, findings)
    submission = {
        "schema": prepared["schema"],
        "body_sha256": prepared["body_sha256"],
        "catalog_sha256": prepared["catalog_sha256"],
        "patches": [
            {"target_id": t["target_id"], "replacement": r}
            for t, r in zip(prepared["targets"], [FIXED, STILL_COPY], strict=True)
        ],
    }
    from bestseller.services.retained_candidate_patch import apply_candidate_patch

    body, receipt = apply_candidate_patch(INITIAL, prepared, submission)
    first = {"patch": receipt, "result_body_sha256": text_hash(body), "target_count": 2}
    remaining, proof = continuation._progress(
        INITIAL, prepared, submission, first, body, [finding(5)]
    )
    assert remaining["targets"][0]["target_id"] not in proof["remaining_original_target_ids"]
    with pytest.raises(ValueError, match="remaining_scope_not_original"):
        continuation._progress(INITIAL, prepared, submission, first, body, [finding(3)])
    with pytest.raises(ValueError, match="strict_progress_required"):
        continuation._progress(INITIAL, prepared, submission, first, body, [finding(1), finding(5)])
