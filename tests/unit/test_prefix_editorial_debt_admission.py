"""Real admission proof helpers behind inert provider/storage boundaries.

Only the physical provider and storage are fixtures. The source-hard assessor,
receipt verifier, prefix publisher and writer admission run their real code.
"""

from __future__ import annotations

import copy
import hashlib
import json
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from bestseller.infra.db.models import (
    CanonFactModel,
    ChapterDraftVersionModel,
    ExecutionAttemptModel,
    ExecutionOperationModel,
    LlmRunModel,
    TimelineEventModel,
    WorkflowRunModel,
)
from bestseller.services import chapter_outline_readiness as prefix
from bestseller.services.llm import LLMCompletionResult
from bestseller.services.planning_draft_admission import (
    assess_planning_draft_admission as _real_assess,
    verify_planning_draft_admission as _real_verify,
)
from bestseller.services.planner import (
    _validate_generated_volume_outline_or_raise as _real_validate_outline,
)
from bestseller.services.prompt_source_policy import content_hash
from bestseller.settings import load_settings

pytestmark = pytest.mark.unit
from test_chapter_outline_readiness import rig as rig  # Reuse the isolated fixture unchanged.
from test_planning_draft_admission import grade_payload


def _workflow(rig):
    return next(row for (model, _), row in rig.store.rows.items() if model == WorkflowRunModel)


async def _approve(rig, **kwargs):
    return await prefix.approve_chapter_outline_prefix(
        rig.store,
        load_settings(),
        project=rig.project,
        source_payload=rig.source,
        through_chapter=1,
        max_repair_rounds=kwargs.pop("max_repair_rounds", 0),
        **kwargs,
    )


@pytest.fixture
def hard_review(rig, monkeypatch):
    from bestseller.services import planning_draft_admission as admission

    # The shared rig replaces these functions; capture them at import time,
    # before any fixture runs, and restore the actual admission contracts.
    monkeypatch.setattr(admission, "assess_planning_draft_admission", _real_assess)
    monkeypatch.setattr(admission, "verify_planning_draft_admission", _real_verify)
    monkeypatch.setattr(prefix.planner, "_validate_generated_volume_outline_or_raise", _real_validate_outline)
    rig.foundation["world_spec"].content["requirement"] = (
        "The approved ability requires physical contact."
    )
    rig.foundation["cast_spec"].content.update(
        protagonist={"name": "Courier", "role": "protagonist", "gender": "male",
                     "pronoun_set_zh": "他", "pronoun_set_en": "he/him"},
        supporting_cast=[{"name": "Guard", "role": "supporting", "gender": "female",
                          "pronoun_set_zh": "她", "pronoun_set_en": "she/her"}],
    )
    monkeypatch.setattr(
        admission,
        "get_latest_approved_planning_artifact",
        prefix.get_latest_approved_planning_artifact,
    )
    original_get = rig.store.get

    async def get(model, identity, **kwargs):
        if model == ExecutionOperationModel:
            return rig.store.rows.get((model, str(identity)))
        return await original_get(model, identity, **kwargs)

    monkeypatch.setattr(rig.store, "get", get)
    attempts = {}

    async def scalar(statement):
        entity = statement.column_descriptions[0]["entity"]
        params = statement.compile().params
        if entity in {CanonFactModel, ChapterDraftVersionModel, TimelineEventModel}:
            assert params["project_id_1"] == rig.project.id
            return None  # This fixture has no accepted prose or historical facts.
        assert entity is ExecutionAttemptModel
        return next(
            (
                attempt
                for attempt in attempts.values()
                if attempt.operation_key == params["operation_key_1"]
                and attempt.status == params["status_1"]
                and attempt.model_name == params["model_name_1"]
            ),
            None,
        )

    monkeypatch.setattr(rig.store, "scalar", scalar, raising=False)
    assessment = {
        "draft_eligible": True,
        "hard_violations": [],
        "editorial_notes": ["Pacing still needs editorial review."],
    }
    calls = []

    async def provider(_session, _settings, request):
        assert request.project_id == rig.project.id and request.workflow_run_id == _workflow(rig).id
        assert request.operation_id and request.prompt_template == admission.TEMPLATE
        # Prefix candidate and original commercial report must be durable first.
        durable = rig.store.commits[-1][str(request.workflow_run_id)]
        assert durable["phase"] == "source_hard_admission"
        assert durable["candidates"][-1]["report"]["pass"] is False
        calls.append(request)
        scope = "test-prefix-editorial-admission"
        key = hashlib.sha256(f"{scope}\n{request.operation_id}".encode()).hexdigest()
        effective_hash = content_hash(
            {"system": request.system_prompt, "user": request.user_prompt,
             "tools": request.tools, "tool_choice": request.tool_choice}
        )
        run_id = uuid4()
        candidate = durable["candidates"][-1]["candidate"]
        for violation in assessment["hard_violations"]:
            # Legacy test verdicts use JSON pointers; the current wire protocol
            # selects the complete chapter or scene projection instead.
            parts = violation["path"].strip("/").split("/")
            if violation["path"].startswith("/"):
                violation["path"] = parts[0] + "".join(
                    f"[{part}]" if part.isdigit() else f".{part}" for part in parts[1:]
                )
            violation.setdefault("source_refs", ["/approved_world_spec/requirement"])
        current_grade = grade_payload(candidate, assessment)
        assessment.update(copy.deepcopy(current_grade))
        for check in assessment["projection_checks"]:
            check.setdefault("related_violation_paths", [])
        raw = json.dumps(current_grade)
        tool_calls = [{"id": "offline-admission", "type": "function", "function": {
            "name": "submit_planning_admission", "arguments": raw,
        }}]
        completion = LLMCompletionResult(
            content="",
            provider="minimax",
            model_name="test-source-hard-model",
            llm_run_id=run_id,
            finish_reason="tool_calls",
            input_tokens=10,
            output_tokens=5,
            tool_calls=tool_calls,
            raw_message={"role": "assistant", "content": "", "tool_calls": copy.deepcopy(tool_calls)},
            metadata={"effective_input_receipt": {"hash": effective_hash}},
        )
        rig.store.rows[(LlmRunModel, run_id)] = SimpleNamespace(
            id=run_id,
            project_id=request.project_id,
            workflow_run_id=request.workflow_run_id,
            prompt_template=admission.TEMPLATE,
            finish_reason=completion.finish_reason,
            prompt_version=request.prompt_version,
            input_tokens=completion.input_tokens,
            output_tokens=completion.output_tokens,
            provider=completion.provider,
            model_name=completion.model_name,
            prompt_hash=effective_hash,
            metadata_json={"execution_receipt": {"scope_id": scope, "operation_key": key}},
        )
        rig.store.rows[(ExecutionOperationModel, key)] = SimpleNamespace(
            scope_id=scope,
            status="completed",
            result_json=completion.model_dump(mode="json"),
        )
        attempt_id = uuid4().hex
        attempts[attempt_id] = SimpleNamespace(
            id=attempt_id,
            scope_id=scope,
            operation_key=key,
            status="completed",
            model_name=completion.model_name,
        )
        transport_key = hashlib.sha256(
            f"transport-result.v1\n{scope}\n{attempt_id}".encode()
        ).hexdigest()
        rig.store.rows[(ExecutionOperationModel, transport_key)] = SimpleNamespace(
            status="transport_result",
            result_json={
                "operation_key": key,
                "input_hash": effective_hash,
                "result": completion.model_dump(mode="json"),
            },
        )
        return completion

    monkeypatch.setattr(admission, "complete_text", provider)
    rig.judges[:] = [False]
    return SimpleNamespace(module=admission, assessment=assessment, calls=calls)


async def test_editorial_debt_requires_explicit_opt_in(rig, hard_review):
    before = copy.deepcopy(rig.project.metadata_json)
    result = await _approve(rig)
    assert result["passed"] is False and result["draft_eligible"] is False
    assert not result["artifact_id"] and not rig.imports and not hard_review.calls
    assert _workflow(rig).metadata_json["phase"] == "rejected"
    assert rig.project.metadata_json == before


async def test_verified_hard_pass_admits_draft_without_flipping_commercial_grade(rig, hard_review):
    before = copy.deepcopy(rig.project.metadata_json)
    result = await _approve(rig, allow_editorial_debt=True)
    assert result["draft_eligible"] is True and result["passed"] is False
    assert result["report"]["pass"] is False and result["report"]["overall_score"] == 0.72
    assert len(hard_review.calls) == 1 and len(rig.imports) == 1
    assert [c["chapter_number"] for c in rig.imports[0].content["chapters"]] == [1]
    workflow = _workflow(rig)
    state = workflow.metadata_json
    report = state["candidates"][-1]["report"]
    assert state["final_report"] == result["report"] == report
    debt = state["editorial_quality_debt"]
    assert debt["commercial_report"] == report
    assert debt["commercial_report_hash"] == content_hash(report)
    assert debt["commercial_report"]["blocking_issues"]
    assert debt["status"] == "open" and debt["due_stage"] == "chapter_adoption_review"
    assert debt["hard_contracts_waived"] is False
    assert debt["source_admission"] == state["candidates"][-1]["source_admission"]
    assert debt in rig.project.metadata_json["outline_quality_debt"]
    receipt = await prefix.verify_outline_prefix_readiness(
        rig.store,
        project=rig.project,
        artifact_id=UUID(result["artifact_id"]),
        chapter_number=1,
    )
    assert receipt["commercial_passed"] is False
    assert receipt["full_window_approved"] is False and receipt["full_volume_complete"] is False
    assert receipt["approval_basis"] == "source_hard_admission_with_editorial_debt"
    for key in (
        "macro_outline_plan",
        "rolling_outline_plan",
        "rolling_outline_windows",
        "rolling_outline_windows_hash",
        "outline_commercial_judge_checkpoints",
    ):
        assert rig.project.metadata_json[key] == before[key]
    # Reuse the exact persisted assessment/report, without a new provider request.
    again = await _approve(rig, allow_editorial_debt=True)
    assert again == result and len(hard_review.calls) == 1


async def test_real_hard_violation_retains_rejection_and_never_publishes(rig, hard_review):
    hard_review.assessment.update(
        draft_eligible=False,
        hard_violations=[
            {
                "code": "ABILITY_RULE_CONFLICT",
                "path": "/chapters/0/scenes/0",
                "evidence": "The sealed ledger is read remotely.",
                "source_requirement": "The approved ability requires physical contact.",
            }
        ],
    )
    result = await _approve(rig, allow_editorial_debt=True)
    assert result["draft_eligible"] is False and result["passed"] is False
    assert not rig.imports and result["artifact_id"] is None
    state = _workflow(rig).metadata_json
    assert state["phase"] == "rejected" and "editorial_quality_debt" not in state
    assert state["candidates"][-1]["source_admission"]["assessment"] == hard_review.assessment


async def test_true_flag_with_actual_hard_violations_cannot_be_a_valid_grade(rig, hard_review):
    hard_review.assessment["hard_violations"] = [
        {
            "code": "IDENTITY_CONFLICT",
            "path": "/chapters/0/scenes/0/participants",
            "evidence": "A foreign identity is used.",
            "source_requirement": "Use the approved identity.",
        }
    ]
    result = await _approve(rig, allow_editorial_debt=True)
    assert result["draft_eligible"] is False and result["passed"] is False
    assert result["admission_status"] == "protocol_unknown"
    assert result["reason"] == "admission_protocol_exhausted"
    state = _workflow(rig).metadata_json
    assert state["phase"] == "admission_protocol_blocked"
    attempts = state["candidates"][-1]["source_admission_protocol"]["attempts"]
    assert len(attempts) == 2
    assert all(item["error"]["detail"] == "admission_inconsistent_grade" for item in attempts)
    assert not rig.imports and "editorial_quality_debt" not in _workflow(rig).metadata_json


@pytest.mark.parametrize("corruption", ["fake_flag", "foreign_project", "unsettled", "paid_grade"])
async def test_unproven_admission_cannot_publish_an_artifact(
    rig, hard_review, monkeypatch, corruption
):
    assess = hard_review.module.assess_planning_draft_admission

    async def corrupt(*args, **kwargs):
        if corruption == "fake_flag":
            return {"draft_eligible": True}
        receipt = await assess(*args, **kwargs)
        run = await rig.store.get(LlmRunModel, receipt["llm_run_id"])
        if corruption == "foreign_project":
            run.project_id = uuid4()
        else:
            key = run.metadata_json["execution_receipt"]["operation_key"]
            operation = await rig.store.get(ExecutionOperationModel, key)
            if corruption == "unsettled":
                operation.status = "unknown"
            else:
                operation.result_json["content"] = '{"draft_eligible":false}'
        return receipt

    monkeypatch.setattr(hard_review.module, "assess_planning_draft_admission", corrupt)
    try:
        result = await _approve(rig, allow_editorial_debt=True)
    except (ValueError, KeyError):
        pass
    else:
        assert result["draft_eligible"] is False
    assert not rig.imports, "Unproven source admission must fail before artifact publication"


async def test_cached_admission_cannot_bypass_disabled_opt_in(rig, hard_review, monkeypatch):
    class InterruptedBeforePublication(BaseException):
        pass

    save = prefix._save

    async def interrupt(session, workflow, state):
        if state["phase"] == "publish":
            raise InterruptedBeforePublication
        await save(session, workflow, state)

    monkeypatch.setattr(prefix, "_save", interrupt)
    with pytest.raises(InterruptedBeforePublication):
        await _approve(rig, allow_editorial_debt=True)
    workflow = _workflow(rig)
    assert workflow.metadata_json["candidates"][-1]["source_admission"]["draft_eligible"] is True
    assert not rig.imports and len(hard_review.calls) == 1
    monkeypatch.setattr(prefix, "_save", save)
    # None resumes persisted permission; an explicit opt-out must fail closed.
    before = copy.deepcopy(workflow.metadata_json)
    with pytest.raises(prefix.OutlinePrefixReadinessError, match="prefix_resume_source_changed"):
        await _approve(rig, workflow_run_id=workflow.id, allow_editorial_debt=False)
    assert not rig.imports and len(hard_review.calls) == 1
    assert workflow.metadata_json == before


async def test_omitted_opt_in_reuses_persisted_same_source_authorization(rig, hard_review):
    first = await _approve(rig, allow_editorial_debt=True)
    workflow = _workflow(rig)
    before = copy.deepcopy(workflow.metadata_json)
    result = await _approve(rig, workflow_run_id=workflow.id)
    assert result == first and result["draft_eligible"] is True
    assert result["passed"] is False
    assert len(hard_review.calls) == len(rig.imports) == 1
    assert workflow.metadata_json == before


@pytest.mark.parametrize(
    "corruption", ["missing_debt", "report_changed", "waiver", "source_grade_changed"]
)
async def test_writer_admission_rechecks_debt_and_source_hard_proof(rig, hard_review, corruption):
    result = await _approve(rig, allow_editorial_debt=True)
    workflow = _workflow(rig)
    state = workflow.metadata_json
    if corruption == "missing_debt":
        state.pop("editorial_quality_debt")
    elif corruption == "report_changed":
        state["editorial_quality_debt"]["commercial_report_hash"] = "a different report"
    elif corruption == "waiver":
        state["editorial_quality_debt"]["hard_contracts_waived"] = True
    else:
        run = await rig.store.get(LlmRunModel, state["receipt"]["source_admission"]["llm_run_id"])
        operation = await rig.store.get(
            ExecutionOperationModel, run.metadata_json["execution_receipt"]["operation_key"]
        )
        operation.result_json["content"] = '{"draft_eligible":false}'
    with pytest.raises(ValueError):
        await prefix.verify_outline_prefix_readiness(
            rig.store,
            project=rig.project,
            artifact_id=UUID(result["artifact_id"]),
            chapter_number=1,
        )


async def test_real_admission_starts_only_after_bounded_repairs_are_retained(rig, hard_review):
    rig.patches[:] = [{"patches": []}, {"patches": []}]
    result = await _approve(rig, max_repair_rounds=2, allow_editorial_debt=True)
    assert result["draft_eligible"] is True and result["passed"] is False
    state = _workflow(rig).metadata_json
    assert len(state["patch_attempts"]) == 2 and len(state["candidates"]) == 1
    assert all(p["consumed"] and p["status"] == "invalid" for p in state["patch_attempts"])
    assert all(p["completion"]["content"] for p in state["patch_attempts"])
    assert len(rig.patch_inputs) == 2 and len(hard_review.calls) == 1
