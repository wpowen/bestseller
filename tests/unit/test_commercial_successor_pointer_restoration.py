"""Real SQLite pointer-only restoration; synthetic retained transport, no provider."""

# ruff: noqa: F811 - imported pytest fixtures are injected by parameter name
from copy import deepcopy
from pathlib import Path

import pytest
from sqlalchemy import select
from test_commercial_protocol_successor_runtime import (  # noqa: F401
    SCOPE,
    cohort,
    persist,
    successor_rig,
)

from bestseller.infra.db.models import (
    ExecutionAttemptModel,
    ExecutionOperationModel,
    LlmRunModel,
    WorkflowRunModel,
)
from bestseller.services import chapter_llm_quality_judge as judge
from bestseller.services import commercial_protocol_successor_runtime as runtime
from bestseller.services import commercial_successor_pointer_restoration as service
from bestseller.services import llm
from bestseller.services.budget import bind_execution_scope

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.fixture
async def reverted(successor_rig):
    case = successor_rig
    authorization = await persist(case)
    offset = len(case.rig.calls)
    for number in (0, 2):
        with bind_execution_scope(SCOPE):
            await judge.judge_chapter_commercial_quality(
                case.rig.session,
                case.rig.settings,
                chapter_number=2,
                content_md=case.rig.draft.content_md,
                project_id=case.rig.project.id,
                chapter_id=case.rig.chapter.id,
                draft_id=case.rig.draft.id,
                workflow_run_id=case.new_owner.id,
                sample_slot=f"chapter-quality.independent-v2:{number}",
                commercial_cohort_samples=3,
            )
    runs = list(
        await case.rig.session.scalars(
            select(LlmRunModel).where(LlmRunModel.workflow_run_id == case.new_owner.id)
        )
    )
    for run, request in zip(runs, case.rig.calls[offset:], strict=True):
        run.metadata_json = {
            **run.metadata_json,
            "effective_input_receipt": {
                "schema": "effective-input.v1",
                "hash": run.prompt_hash,
                "model": run.model_name,
            },
        }
        root = await case.rig.session.get(
            ExecutionOperationModel, run.metadata_json["execution_receipt"]["operation_key"]
        )
        paid = root.result_json
        with llm.bind_project_prompt_audit(
            case.rig.project.id, reason="SQLite pointer restoration fixture"
        ):
            req, resp, error, replayable = llm._persist_prompt_payloads(
                settings=case.rig.settings,
                request=request,
                llm_run_id=run.id,
                configured_model=run.model_name,
                effective_model=run.model_name,
                provider=run.provider,
                effective_temperature=case.rig.settings.llm.critic.temperature,
                effective_max_tokens=8192,
                effective_timeout_seconds=90,
                effective_stream=False,
                effective_thinking_type=None,
                effective_reasoning_effort=None,
                content=paid["content"],
                tool_call_count=len(paid["tool_calls"]),
                finish_reason=paid["finish_reason"],
                fallback_used=False,
                fallback_source=None,
                input_tokens=paid["input_tokens"],
                output_tokens=paid["output_tokens"],
                tool_calls=paid["tool_calls"],
                raw_message=paid["raw_message"],
            )
        assert error is None and replayable
        run.request_payload_ref, run.response_payload_ref = req, resp
    await case.rig.session.commit()
    await case.rig.session.refresh(case.rig.chapter)
    pointers = deepcopy(case.rig.chapter.metadata_json[runtime.RUNTIME_KEY])
    pointers[case.args["key"]] = case.args["pointer"]
    case.rig.chapter.metadata_json = {
        **case.rig.chapter.metadata_json,
        runtime.RUNTIME_KEY: pointers,
    }
    await case.rig.session.commit()
    case.restore_args = {
        "chapter": case.rig.chapter,
        "authorization_id": authorization.id,
        "sources": case.args["sources"],
        "effective_critic": case.args["effective_critic"],
        "actor": "explicit-operator",
        "reason": "Restore independently committed pointer overwritten by stale caller metadata",
    }
    return case


async def test_restore_existing_partial_cohort_only_and_repeat_without_writes(reverted):
    case = reverted
    session = case.rig.session
    models = (LlmRunModel, ExecutionAttemptModel, ExecutionOperationModel)
    before = {
        model.__name__: [runtime._row(r) for r in await session.scalars(select(model))]
        for model in models
    }
    owner_before = deepcopy(case.new_owner.metadata_json)
    old_before = deepcopy(case.rig.owner.metadata_json)
    auth = await session.get(
        WorkflowRunModel, case.restore_args["authorization_id"], populate_existing=True
    )
    auth_before = deepcopy(auth.metadata_json)
    calls = len(case.rig.calls)
    with bind_execution_scope(SCOPE):
        proof = await service.prepare_successor_pointer_restoration(session, **case.restore_args)
        assert len(proof["unsent_slots"]) == 1
        assert proof["unsent_slots"][0]["sample_slot"] == "chapter-quality.independent-v2:1"
        assert proof["unsent_slots"][0]["actual_attempts"] == 0
        assert not session.new and not session.dirty
        await service.restore_successor_pointer(
            session, expected_receipt=proof, **case.restore_args
        )
        await session.commit()
        assert (
            await service.restore_successor_pointer(
                session, expected_receipt=proof, **case.restore_args
            )
            == proof
        )
        assert not session.new and not session.dirty
    await session.refresh(case.rig.chapter)
    assert (
        case.rig.chapter.metadata_json[runtime.RUNTIME_KEY][case.args["key"]]
        == proof["new_pointer"]
    )
    await session.refresh(case.new_owner)
    await session.refresh(case.rig.owner)
    assert case.new_owner.metadata_json == owner_before
    assert case.rig.owner.metadata_json == old_before
    await session.refresh(auth)
    assert auth.metadata_json == {**auth_before, service.HISTORY_KEY: [proof]}
    for model in models:
        assert before[model.__name__] == [
            runtime._row(r) for r in await session.scalars(select(model))
        ]
    assert len(case.rig.calls) == calls


@pytest.mark.parametrize(
    "problem",
    [
        "wrong_pointer",
        "old_journal",
        "new_binding",
        "unknown",
        "extra_actual",
        "missing_physical",
        "unlisted_run",
    ],
)
async def test_pointer_restoration_rejects_changed_or_incomplete_proof(reverted, problem):
    case = reverted
    session = case.rig.session
    if problem == "wrong_pointer":
        data = deepcopy(case.rig.chapter.metadata_json)
        data[runtime.RUNTIME_KEY][case.args["key"]]["binding_sha256"] = "0" * 64
        case.rig.chapter.metadata_json = data
    elif problem in {"old_journal", "new_binding"}:
        owner = case.rig.owner if problem == "old_journal" else case.new_owner
        data = deepcopy(owner.metadata_json)
        journal = data[runtime.RUNTIME_KEY][case.args["key"]]
        if problem == "old_journal":
            journal["changed"] = True
        else:
            journal["binding"]["samples"] = 2
        owner.metadata_json = data
    elif problem in {"unknown", "extra_actual"}:
        actual = await session.scalar(select(ExecutionAttemptModel))
        if problem == "unknown":
            actual.status = "unknown"
        else:
            data = runtime._row(actual)
            data["id"] = "extra-actual"
            data["created_at"] = actual.created_at
            session.add(ExecutionAttemptModel(**data))
    else:
        run = await session.scalar(
            select(LlmRunModel).where(LlmRunModel.workflow_run_id == case.new_owner.id)
        )
        if problem == "missing_physical":
            Path(case.rig.settings.artifact_store.local_dir, run.request_payload_ref).unlink()
        else:
            data = {
                field.key: getattr(run, field.key)
                for field in __import__("sqlalchemy").inspect(LlmRunModel).column_attrs
            }
            from uuid import uuid4

            data["id"] = uuid4()
            session.add(LlmRunModel(**data))
    await session.commit()
    pointer_before = deepcopy(case.rig.chapter.metadata_json[runtime.RUNTIME_KEY])
    with bind_execution_scope(SCOPE), pytest.raises((ValueError, FileNotFoundError)):
        await service.prepare_successor_pointer_restoration(session, **case.restore_args)
    await session.refresh(case.rig.chapter)
    assert case.rig.chapter.metadata_json[runtime.RUNTIME_KEY] == pointer_before


async def test_missing_slot_actual_send_is_not_claimed_unsent(reverted):
    case = reverted
    session = case.rig.session
    with bind_execution_scope(SCOPE):
        proof = await service.prepare_successor_pointer_restoration(session, **case.restore_args)
    missing = proof["unsent_slots"][0]
    template = await session.scalar(
        select(ExecutionOperationModel).where(ExecutionOperationModel.status == "completed")
    )
    session.add(
        ExecutionOperationModel(
            key=missing["expected_root"],
            scope_id=SCOPE,
            input_hash=missing["input_hash"],
            status="completed",
            owner="retained",
            lease_until=template.lease_until,
            result_json=None,
        )
    )
    actual = await session.scalar(select(ExecutionAttemptModel))
    session.add(
        ExecutionAttemptModel(
            id="missing-slot-actual",
            operation_key=missing["expected_root"],
            scope_id=SCOPE,
            input_hash=missing["input_hash"],
            model_name=actual.model_name,
            reserved_tokens=7,
            actual_tokens=7,
            status="completed",
            created_at=actual.created_at,
        )
    )
    await session.commit()
    with bind_execution_scope(SCOPE), pytest.raises(ValueError, match="missing_slot_has_send"):
        await service.prepare_successor_pointer_restoration(session, **case.restore_args)


async def test_prepared_unsent_slot_is_preserved_without_counter_reset(reverted):
    case = reverted
    metadata = deepcopy(case.new_owner.metadata_json)
    journal = metadata[runtime.RUNTIME_KEY][case.args["key"]]
    prepared = {"state": "prepared", "used_attempts": 1}
    journal["slots"]["chapter-quality.independent-v2:1"] = prepared
    case.new_owner.metadata_json = metadata
    await case.rig.session.commit()
    with bind_execution_scope(SCOPE):
        proof = await service.prepare_successor_pointer_restoration(
            case.rig.session, **case.restore_args
        )
        await service.restore_successor_pointer(
            case.rig.session, expected_receipt=proof, **case.restore_args
        )
        await case.rig.session.commit()
    await case.rig.session.refresh(case.new_owner)
    assert case.new_owner.metadata_json == metadata
    assert (
        case.new_owner.metadata_json[runtime.RUNTIME_KEY][case.args["key"]]["slots"][
            "chapter-quality.independent-v2:1"
        ]
        == prepared
    )
