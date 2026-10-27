"""Prepared successor slots survive actual commits before request freezing."""

# ruff: noqa: F811 - imported pytest fixtures are injected by parameter name
from copy import deepcopy
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker
from test_commercial_protocol_successor_runtime import (
    SCOPE,
    cohort,  # noqa: F401
    persist,
    successor_rig,  # noqa: F401
)

from bestseller.infra.db.models import WorkflowRunModel
from bestseller.services import commercial_judge_recovery as recovery
from bestseller.services import commercial_protocol_successor_runtime as runtime
from bestseller.services.budget import bind_execution_scope

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


async def test_three_successor_slots_commit_read_and_freeze_in_independent_sessions(successor_rig):
    case = successor_rig
    authorization = await persist(case)
    original_authorization = deepcopy(authorization.metadata_json)
    factory = async_sessionmaker(case.rig.engine, expire_on_commit=False)
    before_budget = (case.rig.budget.calls_used, case.rig.budget.tokens_charged)
    saved_slots = {}
    for number in range(3):
        slot = f"chapter-quality.independent-v2:{number}"
        with bind_execution_scope(SCOPE):
            async with factory() as session:
                result, call = await recovery.prepare_commercial_component_slot(
                    session,
                    project_id=case.rig.project.id,
                    chapter_id=case.rig.chapter.id,
                    draft_id=case.rig.draft.id,
                    workflow_run_id=case.new_owner.id,
                    content_md=case.rig.draft.content_md,
                    sources=case.args["sources"],
                    evaluation_contract=case.args["binding"]["evaluation_contract"],
                    sample_slot=slot,
                    samples=3,
                    retry_context=None,
                    effective_critic=case.args["effective_critic"],
                    current_input_hash=case.args["current_input_hash"],
                )
                assert result is None and call["attempt"] == 1
            # Independent reader sees the committed slot before freeze reloads it.
            async with factory() as reader:
                owner = await reader.get(WorkflowRunModel, UUID(call["owner_id"]))
                slots = owner.metadata_json[runtime.RUNTIME_KEY][call["key"]]["slots"]
                assert slots[slot] == {"state": "prepared", "used_attempts": 1}
                assert all(slots[name] == value for name, value in saved_slots.items())
            request = case.rig.calls[0].model_copy(
                update={
                    "workflow_run_id": case.new_owner.id,
                    "sample_slot": slot,
                    "prompt_version": "v12-source-authority",
                    "user_prompt": case.args["sources"].text,
                }
            )
            async with factory() as session:
                frozen, receipt = await recovery.freeze_commercial_component_request(
                    session, call, request, case.args["sources"].receipt
                )
                assert frozen.model_dump(mode="json") == request.model_dump(mode="json")
                assert receipt == case.args["sources"].receipt
            async with factory() as session:
                # Re-entry cannot replace already frozen bytes or use another draw.
                frozen_again, receipt_again = await recovery.freeze_commercial_component_request(
                    session,
                    call,
                    request.model_copy(update={"user_prompt": "must not replace"}),
                    {"must_not_replace": True},
                )
                assert frozen_again == frozen and receipt_again == receipt
            async with factory() as reader:
                owner = await reader.get(WorkflowRunModel, case.new_owner.id)
                saved_slots[slot] = deepcopy(
                    owner.metadata_json[runtime.RUNTIME_KEY][call["key"]]["slots"][slot]
                )
                assert saved_slots[slot]["request"] == request.model_dump(mode="json")
                auth = await reader.get(WorkflowRunModel, authorization.id)
                assert auth.metadata_json == {
                    **original_authorization,
                    "consumed_owner_id": str(case.new_owner.id),
                }
                old = await reader.get(WorkflowRunModel, case.rig.owner.id)
                assert old.metadata_json == case.old_metadata
    await case.rig.session.refresh(case.rig.budget)
    assert before_budget == (case.rig.budget.calls_used, case.rig.budget.tokens_charged)
    assert len(case.rig.calls) == case.old_calls
    async with factory() as reader:
        owners = list(
            await reader.scalars(
                select(WorkflowRunModel).where(WorkflowRunModel.id == case.new_owner.id)
            )
        )
        assert len(owners) == 1
        slots = owners[0].metadata_json[runtime.RUNTIME_KEY][case.args["key"]]["slots"]
        assert slots == saved_slots and len(slots) == 3
