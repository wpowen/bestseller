"""Real SQLite + full physical files; inert paid fixture, no PG/providers."""

from copy import deepcopy
import importlib.util
import json
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from test_migrated_candidate_continuation import chain as chain, physical as physical, case as case

from bestseller.infra.db.models import (
    ChapterDraftVersionModel,
    ChapterQualityReportModel,
    ExecutionAttemptModel,
    ExecutionBudgetModel,
    ExecutionOperationModel,
    LlmRunModel,
    RewriteTaskModel,
    WorkflowRunModel,
)
from bestseller.services import llm
from bestseller.services import migrated_candidate_continuation as continuation
from bestseller.services import retained_candidate_continuation as custody
from bestseller.services import retained_candidate_recovery as base

if importlib.util.find_spec("bestseller.services.derivednative_result_recovery") is None:
    import derivednative_result_recovery as recovery
else:
    from bestseller.services import derivednative_result_recovery as recovery

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]
ROOT = Path(__file__).resolve().parents[2]
OLD = ROOT / "reports/continuous-recovery-2026-10-16/freezes/native-empty-content/retained_candidate_native_patch.py"
FIXED = ROOT / "src/bestseller/services/retained_candidate_native_patch.py"


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
async def paid_result(chain, monkeypatch, request):
    mode = getattr(request, "param", None)
    old_module = load(OLD, "frozen_native_result_original")
    monkeypatch.setattr(continuation, "native", old_module)
    persist = llm._persist_prompt_payloads

    def gateway_factory_boundary(**kwargs):
        kwargs["raw_message"]["content"] = None
        return persist(**kwargs)

    monkeypatch.setattr(llm, "_persist_prompt_payloads", gateway_factory_boundary)
    if mode == "bad_late_patch":
        chain.mode = "invalid"
    result = await continuation.continue_migrated_candidate_recovery(
        chain.session, chain.settings, **chain.args2, execute=True
    )
    assert (
        result["status"] == "failed"
        and result["receipt"]["error_code"] == "native_candidate_patch:raw_content_conflict"
    )
    assert "new_draft_id" not in result["receipt"]
    monkeypatch.setattr(llm, "_persist_prompt_payloads", persist)
    fixed = load(FIXED, "fixed_native_result_parser")
    monkeypatch.setattr(continuation, "native", fixed)
    monkeypatch.setattr(recovery, "native", fixed)
    chain.final = result
    chain.recovery_args = {
        **chain.args2,
        "original_final_child_task_id": UUID(result["child_task_id"]),
        "original_native_source_path": str(OLD),
        "evidence_ref": "fixture:original-final-return",
    }
    chain.raw_files = {
        p: p.read_bytes() for p in chain.settings.artifact_store.local_dir.rglob("*.json")
    }

    async def deny(*_args, **_kwargs):
        pytest.fail("zero-provider recovery reached the gateway")

    monkeypatch.setattr(llm, "complete_text", deny)
    chain.writes.clear()
    yield chain


async def invoke(state, execute=False, plan=None, **changes):
    return await recovery.recover_derived_native_result(
        state.session,
        state.settings,
        **{**state.recovery_args, **changes},
        execute=execute,
        expected_plan_sha256=plan,
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
        ChapterQualityReportModel,
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
            result[(model, key)] = custody._row_hash(row)
    return result


async def unchanged(state, before):
    for (model, key), sha in before.items():
        row = await state.session.get(model, key, populate_existing=True)
        assert custody._row_hash(row) == sha
    assert all(p.read_bytes() == raw for p, raw in state.raw_files.items())


async def test_inspect_strict_physical_source_ast_null_empty_zero_writes(paid_result):
    before = await snapshot(paid_result)
    first = await invoke(paid_result)
    assert first == await invoke(paid_result)
    assert first["status"] == "prepared"
    r = first["receipt"]
    assert r["provider_calls"] == r["database_writes"] == 0 and not paid_result.writes
    assert r["compatibility"]["all_other_ast_equal"]
    assert r["normalization"]["original_raw_content"] is None
    assert r["normalization"]["original_completion_content"] == ""
    assert r["normalization"]["schema_or_patch_payload_changed"] is False
    assert not r["grants_credit"] and not r["adoption_authorized"]
    await unchanged(paid_result, before)


async def test_execute_caller_commits_working_candidate_fresh_review_then_idempotent(paid_result):
    before = await snapshot(paid_result)
    plan = (await invoke(paid_result))["receipt"]
    result = await invoke(paid_result, True, plan["plan_sha256"])
    r = result["receipt"]
    assert result["status"] == "completed" and r["working_pointer_selected"]
    assert r["formal_review_required"] and not r["adoption_authorized"] and not r["grants_credit"]
    async with paid_result.factory() as observer:
        assert await observer.get(WorkflowRunModel, UUID(result["workflow_id"])) is None
    await paid_result.session.commit()
    await unchanged(paid_result, before)
    current = await paid_result.session.scalar(
        select(ChapterDraftVersionModel).where(ChapterDraftVersionModel.is_current.is_(True))
    )
    assert str(current.id) == r["new_draft_id"] and current.promotion_state == "candidate"
    assert current.promoted_at is None
    paid_result.writes.clear()
    replay = await invoke(paid_result, True, plan["plan_sha256"])
    assert replay["replay"] and replay["receipt"]["new_draft_id"] == str(current.id)
    assert not paid_result.writes and len(paid_result.calls) == 1


@pytest.mark.parametrize("mode", ["blocked", "missing", "error"])
async def test_current_gate_failure_keeps_noncurrent_candidate_and_once_only(paid_result, mode):
    before = await snapshot(paid_result)
    plan = (await invoke(paid_result))["receipt"]
    paid_result.gate = mode
    result = await invoke(paid_result, True, plan["plan_sha256"])
    assert result["status"] == "failed" and not result["receipt"]["working_pointer_selected"]
    await paid_result.session.commit()
    draft = await paid_result.session.get(
        ChapterDraftVersionModel, UUID(result["receipt"]["new_draft_id"])
    )
    assert not draft.is_current and draft.promotion_state == "candidate"
    await unchanged(paid_result, before)
    again = await invoke(paid_result, True, plan["plan_sha256"])
    assert again["status"] == "failed" and again["replay"]


@pytest.mark.parametrize("paid_result", ["bad_late_patch"], indirect=True)
async def test_invalid_late_patch_still_rejected_after_null_boundary_proof(paid_result):
    with pytest.raises(ValueError, match="targets_not_exact"):
        await invoke(paid_result, True, "unused")
    assert not paid_result.writes


@pytest.mark.parametrize(
    "fault",
    [
        "source",
        "cross_scope",
        "child",
        "policy_only",
        "other_proof",
        "old_witness",
        "parser_other_change",
        "root",
        "attempt",
        "file",
    ],
)
async def test_proof_fences_or_physical_drift(paid_result, monkeypatch, tmp_path, fault):
    changes = {}
    if fault == "source":
        paid_result.source_revision += 1
    elif fault == "cross_scope":
        changes["project_id"] = uuid4()
    elif fault == "child":
        changes["original_final_child_task_id"] = uuid4()
    elif fault == "policy_only":
        from bestseller.services import pipelines

        monkeypatch.setattr(
            pipelines, "chapter_review_recovery_source_hash", lambda: "new-broad-policy"
        )
        plan = await invoke(paid_result)
        assert plan["receipt"]["compatibility"]["new_policy_sha256"] == "new-broad-policy"
        assert not paid_result.writes
        return
    elif fault == "other_proof":
        child = await paid_result.session.get(
            RewriteTaskModel, UUID(paid_result.final["child_task_id"])
        )
        wf = await paid_result.session.get(WorkflowRunModel, UUID(paid_result.final["workflow_id"]))
        data = deepcopy(child.metadata_json)
        data["proof"]["executor_sha256"] = "forged"
        data["receipt"]["proof"]["executor_sha256"] = "forged"
        child.metadata_json = wf.metadata_json = data
    elif fault in {"old_witness", "parser_other_change"}:
        path = tmp_path / "changed.py"
        path.write_text(
            (OLD if fault == "old_witness" else FIXED).read_text() + "\nUNAUTHORIZED=1\n"
        )
        if fault == "old_witness":
            changes["original_native_source_path"] = str(path)
        else:
            monkeypatch.setattr(recovery.native, "__file__", str(path))
    else:
        run = await paid_result.session.scalar(
            select(LlmRunModel).where(
                LlmRunModel.workflow_run_id == UUID(paid_result.final["workflow_id"])
            )
        )
        key = run.metadata_json["execution_receipt"]["operation_key"]
        if fault == "root":
            root = await paid_result.session.get(ExecutionOperationModel, key)
            root.result_json = {**root.result_json, "content": "not the physical return"}
        elif fault == "attempt":
            paid_result.session.add(
                ExecutionAttemptModel(
                    id=uuid4().hex,
                    operation_key=key,
                    scope_id=f"project:{paid_result.project.id}",
                    model_name=run.model_name,
                    input_hash=run.prompt_hash,
                    status="completed",
                    reserved_tokens=1,
                    actual_tokens=1,
                    created_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
                )
            )
        else:
            p = paid_result.settings.artifact_store.local_dir / run.response_payload_ref
            x = json.loads(p.read_text())
            x["raw_message"]["content"] = " "
            p.write_text(json.dumps(x))
    await paid_result.session.commit()
    paid_result.writes.clear()
    with pytest.raises(ValueError):
        await invoke(paid_result, True, "unused", **changes)
    assert not paid_result.writes


async def test_gate_time_source_drift_rolls_back_derived_rows_only(paid_result, monkeypatch):
    plan = (await invoke(paid_result))["receipt"]
    before = await snapshot(paid_result)
    evaluate = base._evidence

    async def drift(*args):
        result = await evaluate(*args)
        paid_result.source_revision += 1
        return result

    monkeypatch.setattr(base, "_evidence", drift)
    with pytest.raises(ValueError):
        await invoke(paid_result, True, plan["plan_sha256"])
    await unchanged(paid_result, before)
    assert not list(
        await paid_result.session.scalars(
            select(WorkflowRunModel).where(WorkflowRunModel.workflow_type == recovery.STRATEGY)
        )
    )


async def test_actual_paid_native_five_targets_exact_null_boundary_and_copy_reaudit(monkeypatch):
    from types import SimpleNamespace
    from bestseller.services.deterministic_post_write_audit import _scan_scene_card_prose_copy

    root = Path("/Volumes/MACSSD/owen-home/Documents/workspace/bestseller")
    progress_path = (
        root
        / "reports/continuous-recovery-2026-10-16/20261017T230307779572Z-copy-candidate-full-progress.json"
    )
    response_path = (
        root / "artifacts/runtime/llm_payloads/eaddf4bf-79aa-4338-8fdd-6398ec7bbc61.response.json"
    )
    if not progress_path.exists() or not response_path.exists():
        pytest.skip("local physical witness is not a portable fixture")
    raw = response_path.read_bytes()
    value = json.loads(raw)
    saved = json.loads(progress_path.read_text())
    body = sorted(saved["drafts"], key=lambda row: row["version"])[-1]["content_md"]
    scenes = [SimpleNamespace(**row) for row in saved["scene_rows"]]
    fixed = load(FIXED, "actual_fixed_native_return")
    monkeypatch.setattr(recovery, "native", fixed)
    compatibility = recovery._code_compatibility(
        OLD, "5fbe2c679a99162caa974cc541638518700bd23b0d570ad40b20819eb090996f"
    )
    assert compatibility["all_other_ast_equal"]
    prepared = fixed.prepare_native_candidate_patch(body, _scan_scene_card_prose_copy(body, scenes))
    completion = SimpleNamespace(
        content=value["content"]["text"],
        raw_message=value["raw_message"],
        tool_calls=value["tool_calls"],
        finish_reason=value["finish_reason"],
    )
    original = deepcopy(completion.__dict__)
    assert completion.content == "" and completion.raw_message["content"] is None
    result, receipt = fixed.apply_native_candidate_patch(body, prepared, completion)
    assert len(prepared["targets"]) == 5 and result != body
    norm = receipt["submission_receipt"]["content_normalization_receipt"]
    assert norm["original_raw_preserved"] and norm["used_as_proposal"] is False
    assert receipt["legacy_application_receipt"]["untouched_bytes_verified"]
    assert _scan_scene_card_prose_copy(result, scenes) == []
    assert completion.__dict__ == original and response_path.read_bytes() == raw
