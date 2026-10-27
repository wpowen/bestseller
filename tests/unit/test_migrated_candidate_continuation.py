"""Staged SQLite/custody tests: no PG, network model or repository writes."""

import asyncio
from copy import deepcopy
from datetime import UTC, datetime, timedelta
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from test_retained_patch_proposal_migration import (
    physical as physical,
    case as case,
    finding,
    INITIAL,
)

if importlib.util.find_spec("bestseller.services.migrated_candidate_continuation") is None:
    import migrated_candidate_continuation as service
else:
    from bestseller.services import migrated_candidate_continuation as service
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
from bestseller.services import retained_candidate_recovery as base
from bestseller.services import retained_candidate_continuation as prior
from bestseller.services import retained_candidate_native_patch as native
from bestseller.services import retained_candidate_patch as legacy
from bestseller.services import retained_patch_proposal_migration as migration
from bestseller.services.deterministic_post_write_audit import (
    DeterministicAuditReport,
    _scan_scene_card_prose_copy,
)

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.fixture
async def chain(physical, monkeypatch):
    state = physical
    state.mode = "success"
    state.calls = []
    state.after_paid = None
    state.cancel_ready = asyncio.Event()
    state.scene = await state.session.scalar(select(base.SceneCardModel))
    state.scene.key_dialogue_beats = ["Actual scene-card sentence."]
    await state.session.commit()
    original_audit = base.reviews._audit_chapter_rewrite_candidate

    def capped(**kwargs):
        if kwargs["content_md"] == INITIAL:
            return original_audit(**kwargs)
        findings = (finding(3), finding(7))
        return DeterministicAuditReport(2, findings, False, {}), SimpleNamespace(
            hard_min=1, hard_max=10000
        )

    monkeypatch.setattr(base.reviews, "_audit_chapter_rewrite_candidate", capped)
    plan = await migration.migrate_untrusted_patch_proposal(
        state.session, state.settings, **state.args
    )
    result = await migration.migrate_untrusted_patch_proposal(
        state.session,
        state.settings,
        **state.args,
        execute=True,
        expected_plan_sha256=plan["plan_sha256"],
    )
    assert result["status"] == "failed" and not result["working_pointer_selected"]
    await state.session.commit()
    state.migration_id = UUID(result["workflow_id"])
    state.target_id = UUID(result["new_draft_id"])
    target = await state.session.get(ChapterDraftVersionModel, state.target_id)
    target_body = target.content_md

    def full(**kwargs):
        body = kwargs["content_md"]
        if body == INITIAL:
            findings = tuple(finding(line) for line in (1, 3, 5, 7, 9))
        elif body == target_body:
            lines = (1, 3, 7) if state.mode == "unresolved" else (3, 7)
            findings = tuple(finding(line) for line in lines)
        else:
            findings = (finding(3),) if state.mode == "nongain" else ()
        return DeterministicAuditReport(2, findings, not findings, {}), SimpleNamespace(
            hard_min=1, hard_max=10000
        )

    monkeypatch.setattr(base.reviews, "_audit_chapter_rewrite_candidate", full)
    state.args2 = {
        **state.ids,
        "previous_child_task_id": state.original_child_id,
        "migration_workflow_id": state.migration_id,
        "candidate_from_migration_id": state.target_id,
        "requested_by": "authorized-stage-test",
    }
    settings, scope = state.settings, f"project:{state.project.id}"

    async def paid(request, replacements):
        prepared = native.prepare_native_candidate_patch(target_body, [finding(3), finding(7)])
        payload = {
            "schema": legacy.POLICY,
            "body_sha256": prepared["body_sha256"],
            "catalog_sha256": prepared["catalog_sha256"],
            "patches": [
                {"target_id": t["target_id"], "replacement": replacement}
                for t, replacement in zip(prepared["targets"], replacements, strict=True)
            ],
        }
        if state.mode == "invalid":
            payload["patches"][-1]["target_id"] = "not-a-current-target"
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
                        "name": native.TOOL_NAME,
                        "arguments": json.dumps(payload),
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
        state.session.add_all(
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
        await state.session.flush()
        return result

    async def complete(_session, _settings, request):
        state.calls.append(request)
        async with state.factory() as observer:
            row = await observer.get(WorkflowRunModel, request.workflow_run_id)
            assert row.status == "running" and row.metadata_json["ordinal"] == 2
            assert row.metadata_json["attempt_claimed"] == 1
            assert row.metadata_json["request"] == request.model_dump(mode="json")
        if state.mode == "interrupted":
            raise KeyboardInterrupt("simulated exit")
        if state.mode == "system_exit":
            raise SystemExit(7)
        if state.mode == "wait_cancel":
            # A provider may have a genuinely unsettled durable operation when
            # the caller is cancelled. The service must not close this ledger.
            scope = f"project:{state.project.id}"
            key = hashlib.sha256(f"{scope}\n{request.operation_id}:sample:0".encode()).hexdigest()
            state.unknown_operation = key
            state.session.add(
                ExecutionOperationModel(
                    key=key,
                    scope_id=scope,
                    input_hash="pending-transport",
                    status="running",
                    owner="fixture-pending-provider",
                    lease_until=datetime.now(UTC) + timedelta(days=1),
                    result_json={"status": "unknown", "no_closure_permission": True},
                )
            )
            await state.session.commit()
            state.unknown_digest = prior._row_hash(
                await state.session.get(ExecutionOperationModel, key)
            )
            state.cancel_ready.set()
            await asyncio.Event().wait()
        if state.mode == "provider_error":
            raise RuntimeError("private provider detail")
        result = await paid(request, ["A gull cries over the harbor water.", "Rain keeps falling."])
        if state.after_paid:
            await state.after_paid()
        return result

    monkeypatch.setattr(llm, "complete_text", complete)
    state.writes.clear()
    binding = await base._binding(state.session, **state.ids)
    check_fence = (await base._prepare(state.session, settings, binding))[-1]
    old_child = await state.session.get(RewriteTaskModel, state.original_child_id)
    old_fence = old_child.metadata_json["fence"]
    assert {k for k in check_fence if check_fence[k] != old_fence[k]} <= {
        "preflight_audit",
        "policy_sha256",
        "patch_catalog_sha256",
    }
    yield state


async def invoke(state, execute=False, **changes):
    return await service.continue_migrated_candidate_recovery(
        state.session, state.settings, **{**state.args2, **changes}, execute=execute
    )


async def snapshot(state):
    result = {}
    for model in (
        RewriteTaskModel,
        WorkflowRunModel,
        ExecutionBudgetModel,
        ExecutionOperationModel,
        ExecutionAttemptModel,
        LlmRunModel,
    ):
        for row in await state.session.scalars(
            select(model).execution_options(populate_existing=True)
        ):
            key = (
                row.key
                if model is ExecutionOperationModel
                else row.scope_id
                if model is ExecutionBudgetModel
                else row.id
            )
            result[(model, key)] = prior._row_hash(row)
    return result


async def unchanged(state, old):
    for (model, key), digest in old.items():
        row = await state.session.get(model, key, populate_existing=True)
        assert prior._row_hash(row) == digest


async def test_inspect_full_progress_readonly_and_shared_ids(chain):
    old = await snapshot(chain)
    first = await invoke(chain)
    assert first == await invoke(chain)
    r = first["receipt"]
    assert first["status"] == "prepared" and not first["replay"]
    assert r["binding"]["parent_id"] == str(chain.parent.id)
    assert (r["progress"]["initial_targets"], r["progress"]["remaining_targets"]) == (5, 2)
    assert r["provider_calls"] == r["database_writes"] == 0
    assert not chain.writes and not chain.calls
    assert prior._ids(r["binding"]) == (UUID(first["workflow_id"]), UUID(first["child_task_id"]))
    assert (
        r["proof"]["original_fence"]["preflight_audit"]
        != r["proof"]["current_full_fence"]["preflight_audit"]
    )
    await unchanged(chain, old)


async def test_one_native_batch_all_custody_retained_then_zero_call_replay(chain):
    old = await snapshot(chain)
    result = await invoke(chain, True)
    assert result["status"] == "completed", result["receipt"].get("error_code")
    r = result["receipt"]
    assert (
        r["formal_review_required"]
        and r["working_pointer_selected"]
        and not r["adoption_authorized"]
    )
    assert r["third_batch_allowed"] is False and len(chain.calls) == 1
    req = chain.calls[0]
    assert req.tools[0]["function"]["name"] == native.TOOL_NAME and req.max_tokens_override == 4096
    assert req.operation_id == base.execution_operation_id(
        "retained_candidate_patch",
        subject=r["binding"]["lineage_root_task_id"],
        revision="ordinal:2",
    )
    await unchanged(chain, old)
    current = await chain.session.scalar(
        select(ChapterDraftVersionModel).where(ChapterDraftVersionModel.is_current.is_(True))
    )
    assert (
        str(current.id) == r["new_draft_id"]
        and current.promotion_state == "candidate"
        and current.promoted_at is None
    )
    chain.writes.clear()
    again = await invoke(chain, True)
    assert (
        again["replay"]
        and again["status"] == "completed"
        and not chain.writes
        and len(chain.calls) == 1
    )


@pytest.mark.parametrize(
    "mode", ["invalid", "nongain", "provider_error", "gate_error", "source_change"]
)
async def test_final_failure_keeps_body_and_never_grants_third_batch(chain, mode):
    old = await snapshot(chain)
    chain.mode = mode
    if mode == "gate_error":
        chain.gate = "error"
    if mode == "source_change":

        async def drift():
            chain.source_revision += 1

        chain.after_paid = drift
    result = await invoke(chain, True)
    assert result["status"] == "failed"
    assert result["receipt"]["third_batch_allowed"] is False
    if mode in {"nongain", "gate_error", "source_change"}:
        draft = await chain.session.get(
            ChapterDraftVersionModel, UUID(result["receipt"]["new_draft_id"])
        )
        assert not draft.is_current and draft.promotion_state == "candidate"
    await unchanged(chain, old)
    if mode == "source_change":
        # Semantically stale must fail closed, not acquire new ordinal credit.
        with pytest.raises(ValueError):
            await invoke(chain, True)
    else:
        again = await invoke(chain, True)
        assert again["replay"] and again["status"] == "failed"
    assert len(chain.calls) == 1


@pytest.mark.parametrize(
    "fault",
    [
        "migration",
        "target",
        "child",
        "project",
        "source",
        "unresolved",
        "raw",
        "paid_attempt",
        "foreign_ordinal",
        "orphan_ordinal",
    ],
)
async def test_pre_dispatch_failures_never_create_claim(chain, fault):
    args = {}
    if fault in {"migration", "target", "child", "project"}:
        args[
            {
                "migration": "migration_workflow_id",
                "target": "candidate_from_migration_id",
                "child": "previous_child_task_id",
                "project": "project_id",
            }[fault]
        ] = uuid4()
    elif fault == "source":
        chain.source_revision += 1
    elif fault == "unresolved":
        chain.mode = "unresolved"
    elif fault == "raw":
        old = await chain.session.get(WorkflowRunModel, chain.migration_id)
        meta = deepcopy(old.metadata_json)
        meta["receipt"]["proposal"]["patches"][-1]["replacement"] = "forged"
        old.metadata_json = meta
    elif fault == "paid_attempt":
        run = await chain.session.scalar(
            select(LlmRunModel).where(LlmRunModel.workflow_run_id == chain.original_wf_id)
        )
        key = run.metadata_json["execution_receipt"]["operation_key"]
        chain.session.add(
            ExecutionAttemptModel(
                id=uuid4().hex,
                operation_key=key,
                scope_id=f"project:{chain.project.id}",
                model_name=run.model_name,
                input_hash=run.prompt_hash,
                status="completed",
                reserved_tokens=1,
                actual_tokens=1,
                created_at=datetime.now(UTC),
            )
        )
    elif fault == "orphan_ordinal":
        binding = await base._binding(chain.session, **chain.ids)
        op = base.execution_operation_id(
            "retained_candidate_patch",
            subject=binding[6]["lineage_root_task_id"],
            revision="ordinal:2",
        )
        scope = binding[6]["scope_id"]
        key = hashlib.sha256(f"{scope}\n{op}:sample:0".encode()).hexdigest()
        chain.session.add(
            ExecutionOperationModel(
                key=key,
                scope_id=scope,
                input_hash="unknown-paid-or-cursor",
                status="running",
                owner="retained-orphan",
                lease_until=datetime.now(UTC) + timedelta(days=1),
                result_json={},
            )
        )
    else:
        binding = await base._binding(chain.session, **chain.ids)
        wid, cid = prior._ids(binding[6])
        chain.session.add_all(
            [
                WorkflowRunModel(
                    id=wid,
                    project_id=chain.project.id,
                    workflow_type=prior.STRATEGY,
                    scope_type="chapter",
                    scope_id=chain.chapter.id,
                    status="failed",
                    requested_by="old-route",
                    metadata_json={"lineage_root_task_id": binding[6]["lineage_root_task_id"]},
                ),
                RewriteTaskModel(
                    id=cid,
                    project_id=chain.project.id,
                    parent_task_id=chain.original_child_id,
                    trigger_type="old-route",
                    trigger_source_id=chain.chapter.id,
                    rewrite_strategy=prior.STRATEGY,
                    status="failed",
                    attempts=1,
                    priority=1,
                    instructions="old ordinal2",
                    context_required=[],
                    metadata_json={"lineage_root_task_id": binding[6]["lineage_root_task_id"]},
                ),
            ]
        )
    await chain.session.commit()
    chain.writes.clear()
    with pytest.raises(ValueError):
        await invoke(chain, True, **args)
    assert not chain.calls and not chain.writes


@pytest.mark.parametrize(
    "mode,exception", [("interrupted", KeyboardInterrupt), ("system_exit", SystemExit)]
)
async def test_caught_exit_finalizes_spent_failed_then_reraises(chain, mode, exception):
    old = await snapshot(chain)
    chain.mode = mode
    with pytest.raises(exception):
        await invoke(chain, True)
    await unchanged(chain, old)
    plan = await invoke(chain)
    assert plan["status"] == "failed" and plan["replay"]
    assert plan["receipt"]["interrupted"]
    assert plan["receipt"]["error_type"] == exception.__name__
    assert plan["receipt"]["uncommitted_candidate_rolled_back"]
    assert not plan["receipt"]["adoption_authorized"]
    async with chain.factory() as observer:
        child = await observer.get(RewriteTaskModel, UUID(plan["child_task_id"]))
        wf = await observer.get(WorkflowRunModel, UUID(plan["workflow_id"]))
        assert child.status == wf.status == "failed" and child.attempts == 1
    again = await invoke(chain, True)
    assert again["status"] == "failed" and again["replay"] and len(chain.calls) == 1


async def test_true_capacity_failure_with_tools_happens_before_any_claim(chain, monkeypatch):
    from bestseller.services.prompt_compiler import PromptBudgetError

    original = native.prepare_native_candidate_patch

    def large(*args):
        value = original(*args)
        value["request"]["tools"][0]["function"]["description"] = "capacity!" * 20000
        return value

    monkeypatch.setattr(native, "prepare_native_candidate_patch", large)
    with pytest.raises(PromptBudgetError):
        await invoke(chain, True)
    assert not chain.writes and not chain.calls


async def test_actual_offline_full_catalog_eight_to_five_and_exact_migration_maps():
    root = Path("/Volumes/MACSSD/owen-home/Documents/workspace/bestseller")
    progress_path = (
        root
        / "reports/continuous-recovery-2026-10-16/20261017T230307779572Z-copy-candidate-full-progress.json"
    )
    old_path = (
        root
        / "reports/continuous-recovery-2026-10-16/20261017T225356895547Z-retained-proposal-migration-operator.json"
    )
    if not progress_path.exists() or not old_path.exists():
        pytest.skip("local offline physical witness is not a portable fixture")
    saved = json.loads(progress_path.read_text())
    mr = json.loads(old_path.read_text())["result"]
    before, after = sorted(saved["drafts"], key=lambda d: d["version"])
    scenes = [SimpleNamespace(**row) for row in saved["scene_rows"]]
    initial = _scan_scene_card_prose_copy(before["content_md"], scenes)
    remaining = _scan_scene_card_prose_copy(after["content_md"], scenes)
    initial_prepared = legacy.prepare_candidate_patch(before["content_md"], initial)
    remaining_prepared = legacy.prepare_candidate_patch(after["content_md"], remaining)
    assert initial_prepared == before["prepared"] and remaining_prepared == after["prepared"]
    applied_ids = {t["target_id"] for t in mr["application"]["targets"]}
    selected_lines = {
        t["start_line"] for t in initial_prepared["targets"] if t["target_id"] in applied_ids
    }
    frozen = legacy.prepare_candidate_patch(
        before["content_md"], [f for f in initial if f.line_number in selected_lines]
    )
    body, app = legacy.apply_candidate_patch(before["content_md"], frozen, mr["proposal"])
    assert body == after["content_md"] and app == mr["application"]
    result = service._progress(
        before["content_md"], after["content_md"], scenes, initial, remaining, frozen, app
    )
    assert (result["initial_targets"], result["remaining_targets"]) == (8, 5)
    assert len(result["resolved_original_pairs"]) == 3
    assert all(p["mapping"] == "untouched" for p in result["residual_mappings"])


async def test_actual_five_target_native_full_source_tools_output_admission():
    from bestseller.services.primary_review_transport import _capacity

    root = Path("/Volumes/MACSSD/owen-home/Documents/workspace/bestseller")
    progress_path = (
        root
        / "reports/continuous-recovery-2026-10-16/20261017T230307779572Z-copy-candidate-full-progress.json"
    )
    request_path = (
        root / "artifacts/runtime/llm_payloads/1a79a9ef-4bfc-4b2e-b3ee-ed7820a11e07.request.json"
    )
    if not progress_path.exists() or not request_path.exists():
        pytest.skip("local offline witness is not a portable fixture")
    saved = json.loads(progress_path.read_text())
    target = sorted(saved["drafts"], key=lambda d: d["version"])[-1]
    scenes = [SimpleNamespace(**row) for row in saved["scene_rows"]]
    prepared = native.prepare_native_candidate_patch(
        target["content_md"], _scan_scene_card_prose_copy(target["content_md"], scenes)
    )
    user = json.loads(request_path.read_text())["messages"]["user"]["text"]
    decoder, offset, sources = json.JSONDecoder(), 0, []
    while (start := user.find("{", offset)) >= 0:
        try:
            value, end = decoder.raw_decode(user, start)
        except ValueError:
            offset = start + 1
            continue
        offset = end
        if (
            isinstance(value, dict)
            and {"adopted_continuity", "current_source_channels", "editable_plan"} <= value.keys()
        ):
            sources.append(value)
    assert len(sources) == 1
    req = prepared["request"]
    system, physical_user, _ = service.prepare_source_complete_prompt(
        system_prompt=req["system_prompt"] + "\n\n" + service.FEEDBACK,
        user_prompt=req["user_prompt"],
        source_context=sources[0],
        output_tokens=4096,
        tools=req["tools"],
    )
    capacity = _capacity(system, physical_user, req["tools"], 4096)
    assert len(prepared["targets"]) == 5 and capacity["budget_passed"]
    assert capacity["tool_schema_estimated_tokens"] > 0
    assert capacity["reserved_input_tokens"] + 4096 <= 65536
    assert capacity["combined_characters"] <= 120000


async def test_caller_dirty_work_refused_without_rollback(chain):
    chain.project.title = "Caller uncommitted work"
    with pytest.raises(ValueError, match="clean_session_required"):
        await invoke(chain, True)
    assert chain.project.title == "Caller uncommitted work" and chain.project in chain.session.dirty


async def test_actual_task_cancellation_finalizes_without_fabricating_provider_closure(chain):
    old = await snapshot(chain)
    chain.mode = "wait_cancel"
    task = asyncio.create_task(invoke(chain, True))
    await asyncio.wait_for(chain.cancel_ready.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert task.cancelled()
    await unchanged(chain, old)
    pending = await chain.session.get(
        ExecutionOperationModel, chain.unknown_operation, populate_existing=True
    )
    assert pending.status == "running" and prior._row_hash(pending) == chain.unknown_digest
    again = await invoke(chain, True)
    assert again["status"] == "failed" and again["replay"] and len(chain.calls) == 1
    assert again["receipt"]["error_type"] == "CancelledError"
    assert again["receipt"]["financial_operations_modified"] is False
    async with chain.factory() as observer:
        child = await observer.get(RewriteTaskModel, UUID(again["child_task_id"]))
        wf = await observer.get(WorkflowRunModel, UUID(again["workflow_id"]))
        assert child.status == wf.status == "failed"
    assert not list(
        await chain.session.scalars(
            select(ChapterDraftVersionModel).where(ChapterDraftVersionModel.version_no > 29)
        )
    )


async def test_cancellation_after_candidate_flush_rolls_back_pointer_but_keeps_paid_return(
    chain, monkeypatch
):
    old = await snapshot(chain)
    save = base._save_draft

    async def cancelled_after_flush(*args, **kwargs):
        await save(*args, **kwargs)
        raise asyncio.CancelledError()

    monkeypatch.setattr(base, "_save_draft", cancelled_after_flush)
    with pytest.raises(asyncio.CancelledError):
        await invoke(chain, True)
    await unchanged(chain, old)
    again = await invoke(chain, True)
    assert again["status"] == "failed" and again["replay"] and len(chain.calls) == 1
    assert again["receipt"]["candidate_persisted"] is False
    assert "new_draft_id" not in again["receipt"]
    run = await chain.session.scalar(
        select(LlmRunModel).where(LlmRunModel.workflow_run_id == UUID(again["workflow_id"]))
    )
    assert run is not None
    key = run.metadata_json["execution_receipt"]["operation_key"]
    paid_operation = await chain.session.get(ExecutionOperationModel, key, populate_existing=True)
    assert paid_operation.status == "completed"
    assert paid_operation.result_json["llm_run_id"] == str(run.id)
    await chain.session.refresh(chain.parent)
    assert chain.parent.is_current
    assert not list(
        await chain.session.scalars(
            select(ChapterDraftVersionModel).where(ChapterDraftVersionModel.version_no > 29)
        )
    )
