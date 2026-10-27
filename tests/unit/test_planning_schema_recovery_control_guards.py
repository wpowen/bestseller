"""Three independently reported recovery blockers; SQLite and inert completions.

The shared fixture retains actual request/response files and settled ledger rows.
These tests own no source files and never call a provider.
"""

from copy import deepcopy
from uuid import uuid4

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker
from test_planning_schema_recovery import PATCH, canonical, files
from test_planning_schema_recovery import case as case
from test_planning_schema_recovery import native as native
from test_planning_schema_recovery import offline as offline

from bestseller.infra.db.models import LlmRunModel, PlanningArtifactVersionModel, WorkflowRunModel
from bestseller.services import llm, planner
from bestseller.services import planning_schema_recovery as recovery
from bestseller.services.budget import (
    ExecutionBudgetError,
    bind_execution_scope,
    record_execution_stop,
)
from bestseller.services.planning_field_corrections import apply_corrections, correction_contract

pytestmark = [
    pytest.mark.unit,
    pytest.mark.asyncio,
    pytest.mark.parametrize("case", ["volume_plan"], indirect=True),
]


async def invoke(case, *, before_dispatch=None):
    async def canonical_guard():
        await planner._assert_bound_planning_canonical(case.session, case.project)

    return await recovery.recover(
        case.session,
        case.settings,
        project=case.project,
        system=case.system,
        user=case.user,
        projection=case.projection,
        workflow_run_id=case.consumer.id,
        validate=case.args["validator"],
        before_dispatch=canonical_guard if before_dispatch is None else before_dispatch,
    )


def install_inert_completion(case, monkeypatch):
    calls, run_ids = [], []

    async def complete(session, settings, request):
        assert session is case.session and session.bind.dialect.name == "sqlite"
        assert not request.tools and request.max_tokens_override == 8192
        calls.append(request)
        run = LlmRunModel(
            id=uuid4(),
            project_id=request.project_id,
            workflow_run_id=request.workflow_run_id,
            logical_role="planner",
            provider="openai",
            model_name=case.role.model,
            prompt_template=request.prompt_template,
            prompt_version=request.prompt_version,
            finish_reason="stop",
            input_tokens=7,
            output_tokens=11,
            metadata_json={"inert_test_completion": True},
        )
        session.add(run)
        await session.flush()
        run_ids.append(run.id)
        return llm.LLMCompletionResult(
            content=canonical(PATCH),
            provider=run.provider,
            model_name=run.model_name,
            llm_run_id=run.id,
            finish_reason="stop",
            input_tokens=7,
            output_tokens=11,
        )

    monkeypatch.setattr(llm, "complete_text", complete)
    return calls, run_ids


async def test_short_request_version_reaches_inert_completion_without_changing_receipt(
    native, monkeypatch
):
    case = native
    physical = files(case)
    calls, run_ids = install_inert_completion(case, monkeypatch)
    with bind_execution_scope(case.budget.scope_id):
        payload, run_id = await invoke(case)
    assert len(calls) == len(run_ids) == 1 and run_id is None
    assert 0 < len(calls[0].prompt_version) <= 32
    assert calls[0].prompt_version != recovery.POLICY
    assert payload[0]["_meta"]["field_recovery_receipt"]["schema"] == recovery.POLICY
    assert files(case) == physical and not case.denied


async def test_approved_cache_cannot_skip_terminal_stop_or_canonical_guard(native):
    case = native
    physical = files(case)
    with bind_execution_scope(case.budget.scope_id):
        source = await recovery.find_retained_source(
            case.session, case.settings, project=case.project, user=case.user
        )
    corrected, receipt = apply_corrections(
        source.value, source.schema, correction_contract(source.value, source.schema), PATCH
    )
    rows = corrected["volumes"]
    receipt = {
        **receipt,
        **source.proof,
        "schema": recovery.POLICY,
        "original_volume_metadata": [
            {"present": "_meta" in row, "value": deepcopy(row.get("_meta"))}
            for row in rows
        ],
    }
    for row in rows:
        row["_meta"] = {**(row.get("_meta") or {}), "field_recovery_receipt": deepcopy(receipt)}
    case.session.add(PlanningArtifactVersionModel(
        project_id=case.project.id,
        artifact_type="volume_plan",
        version_no=1,
        schema_version="control-guard-fixture.v1",
        status="approved",
        content=rows,
    ))
    await case.session.commit()
    with bind_execution_scope(case.budget.scope_id):
        record_execution_stop(
            ExecutionBudgetError("independent_terminal_stop", case.budget.scope_id)
        )
        with pytest.raises(ExecutionBudgetError, match="independent_terminal_stop"):
            await invoke(case)

    guard_calls = []

    async def changed_canonical():
        guard_calls.append(True)
        raise ValueError("independent_canonical_changed")

    with bind_execution_scope(case.budget.scope_id):
        with pytest.raises(ValueError, match="independent_canonical_changed"):
            await invoke(case, before_dispatch=changed_canonical)
    assert guard_calls == [True]
    assert files(case) == physical and not case.denied


async def test_owner_revoked_after_durable_patch_commit_cannot_publish(native, monkeypatch):
    case = native
    physical = files(case)
    calls, run_ids = install_inert_completion(case, monkeypatch)
    original_request = llm.LLMCompletionRequest

    def version_compatible_request(**kwargs):
        # Isolate the ownership bug when replaying the pre-fix module in memory.
        # The separate first test exercises the original 33-character failure.
        if len(kwargs.get("prompt_version", "")) > 32:
            kwargs["prompt_version"] = "v1-native-field-recovery"
        return original_request(**kwargs)

    monkeypatch.setattr(llm, "LLMCompletionRequest", version_compatible_request)
    owner_id = case.consumer.id
    revoked_metadata = {**case.consumer.metadata_json, "execution_scope_id": "revoked-owner-scope"}
    factory = async_sessionmaker(case.session.bind, expire_on_commit=False)
    original_commit = case.session.commit
    revoked = []

    async def commit_then_revoke():
        await original_commit()
        if not revoked:
            async with factory() as other:
                await other.execute(update(WorkflowRunModel).where(
                    WorkflowRunModel.id == owner_id
                ).values(status="cancelled", metadata_json=revoked_metadata))
                await other.commit()
            revoked.append(True)

    monkeypatch.setattr(case.session, "commit", commit_then_revoke)
    with bind_execution_scope(case.budget.scope_id):
        with pytest.raises(ValueError, match="planning_field_recovery:current_owner"):
            await invoke(case)
    assert len(calls) == len(run_ids) == 1 and revoked == [True]
    async with factory() as other:
        owner = await other.get(WorkflowRunModel, owner_id)
        assert owner.status == "cancelled" and owner.metadata_json == revoked_metadata
        assert not owner.metadata_json.get("planning_field_recoveries")
        assert await other.scalar(select(LlmRunModel.id).where(LlmRunModel.id == run_ids[0]))
    assert files(case) == physical and not case.denied
