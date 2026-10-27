"""Default policy migration selects exact provenance and retains paid slots."""

from copy import deepcopy
from uuid import uuid4

import pytest
from sqlalchemy import select
from test_prefix_runtime_bootstrap_sqlite import (
    ForbiddenProviderError,
    approve,
)
from test_prefix_runtime_bootstrap_sqlite import (
    bootstrap as bootstrap,
)
from test_prefix_runtime_bootstrap_sqlite import (
    rig as rig,
)
from test_prefix_runtime_bootstrap_sqlite import (
    sqlite_identity_store as sqlite_identity_store,
)

from bestseller.infra.db.models import WorkflowRunModel
from bestseller.services import chapter_outline_readiness as service
from bestseller.services import planning_draft_admission as admission
from bestseller.services.prompt_source_policy import content_hash

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


async def seed(case, *, completed=False):
    async with case.factory() as session:
        with pytest.raises(ForbiddenProviderError):
            await approve(case, session, max_repair_rounds=1)
        row = await session.scalar(select(WorkflowRunModel))
        current_identity = deepcopy(row.metadata_json["identity"])
        state = deepcopy(row.metadata_json)
        state["identity"]["hard_admission_policy"] = "retained-old-hard-policy"
        state.update(
            phase="source_hard_admission",
            continuation_rebased=True,
            failure={"code": "old-protocol-exhausted"},
            paid_calls=147,
        )
        state["hard_patch_attempts"] = [
            {
                "status": "validated",
                "consumed": True,
                "completion": {
                    "content": "exact retained paid patch bytes",
                    "finish_reason": "stop",
                },
            }
        ]
        current = state["candidates"][-1]
        current["validated"] = True
        current["source_admission_protocol"] = {
            "hard_admission_policy": "retained-old-hard-policy",
            "status": "protocol_exhausted",
            "attempts": [{"raw": "unaltered old paid admission"}],
        }
        if completed:
            state["artifact_id"] = str(uuid4())
            state["receipt"] = {"raw": "original approval receipt"}
        row.metadata_json = state
        row.idempotency_key = content_hash(state["identity"])
        row.status = "completed" if completed else "failed"
        await session.commit()
        return row.id, deepcopy(state), current_identity


def admission_boundary(case, monkeypatch):
    calls = []

    async def reject(_session, _settings, **kwargs):
        # This is a normal entry readback, with an inert semantic boundary.
        # No provider or new paid patch is allowed in these tests.
        await kwargs["persist_protocol"]()
        async with case.factory() as observer:
            row = await observer.get(WorkflowRunModel, kwargs["workflow_run_id"])
            snapshot = deepcopy(row.metadata_json)
        calls.append((row.id, snapshot))
        return {
            "schema": admission.SCHEMA,
            "hard_admission_policy": admission.HARD_ADMISSION_POLICY,
            "draft_eligible": False,
            "assessment": {
                "draft_eligible": False,
                "hard_violations": [{"code": "CONTINUITY_CONFLICT"}],
                "editorial_notes": [],
            },
        }

    monkeypatch.setattr(admission, "assess_planning_draft_admission", reject)
    return calls


async def test_default_failed_policy_change_reuses_workflow_and_consumed_slot(
    bootstrap, monkeypatch
):
    case = bootstrap
    wid, old, current_identity = await seed(case)
    calls = admission_boundary(case, monkeypatch)
    async with case.factory() as session:
        result = await approve(case, session, max_repair_rounds=1)
    assert result["workflow_run_id"] == str(wid)
    assert not result["draft_eligible"] and len(calls) == 1
    async with case.factory() as observer:
        rows = list(await observer.scalars(select(WorkflowRunModel)))
    assert len(rows) == 1
    state = rows[0].metadata_json
    assert state["identity"] == current_identity
    assert rows[0].idempotency_key == content_hash(current_identity)
    assert state["hard_patch_attempts"] == old["hard_patch_attempts"]
    assert state["paid_calls"] == 147
    assert state["source_payload"] == old["source_payload"]
    assert state["candidates"][-1]["candidate"] == old["candidates"][-1]["candidate"]
    assert state["candidates"][-1]["source_admission_protocol_history"] == [
        old["candidates"][-1]["source_admission_protocol"]
    ]
    assert state["policy_transitions"][0]["previous_identity"] == old["identity"]
    assert len(case.boundaries) == 1  # Bootstrap's forbidden call only.


async def test_default_completed_policy_change_creates_successor_without_mutating_original(
    bootstrap, monkeypatch
):
    case = bootstrap
    wid, old, current_identity = await seed(case, completed=True)
    calls = admission_boundary(case, monkeypatch)
    async with case.factory() as session:
        result = await approve(case, session, max_repair_rounds=1)
    assert result["workflow_run_id"] != str(wid) and len(calls) == 1
    async with case.factory() as observer:
        original = await observer.get(WorkflowRunModel, wid)
        successor = await observer.get(WorkflowRunModel, calls[0][0])
        rows = list(await observer.scalars(select(WorkflowRunModel)))
    assert len(rows) == 2 and original.status == "completed"
    assert original.metadata_json == old
    assert original.idempotency_key == content_hash(old["identity"])
    assert successor.metadata_json["identity"] == current_identity
    assert successor.metadata_json["hard_patch_attempts"] == old["hard_patch_attempts"]
    assert successor.metadata_json["reassessment"]["previous_workflow_run_id"] == str(wid)
    assert successor.metadata_json["reassessment"]["previous_receipt"] == old["receipt"]
    # Once an exact successor exists, older history cannot compete with it.
    async with case.factory() as session:
        exact = await service._find_current_workflow(
            case.wrap(session), project_id=case.rig.project.id, identity=current_identity
        )
        assert exact.id == successor.id


async def test_default_ambiguous_predecessors_fail_closed_without_writes(bootstrap):
    case = bootstrap
    wid, old, _ = await seed(case)
    async with case.factory() as session:
        session.add(
            WorkflowRunModel(
                id=uuid4(),
                project_id=case.rig.project.id,
                workflow_type=service.WORKFLOW_TYPE,
                scope_type="project",
                scope_id=case.rig.project.id,
                status="failed",
                requested_by="offline-test",
                metadata_json=deepcopy(old),
                idempotency_key="other-historical-key",
            )
        )
        await session.commit()
    async with case.factory() as session:
        with pytest.raises(
            service.OutlinePrefixReadinessError, match="policy_predecessor_ambiguous"
        ):
            await approve(case, session, max_repair_rounds=1)
    async with case.factory() as observer:
        rows = list(await observer.scalars(select(WorkflowRunModel)))
        assert len(rows) == 2 and all(row.metadata_json == old for row in rows)
        assert (await observer.get(WorkflowRunModel, wid)).status == "failed"
    assert len(case.boundaries) == 1


async def test_exact_current_wins_even_with_ambiguous_old_predecessors(bootstrap, monkeypatch):
    case = bootstrap
    _, old, identity = await seed(case)
    exact_id = uuid4()
    async with case.factory() as session:
        for row_id, state, key in [
            (uuid4(), deepcopy(old), "other-old-key"),
            (exact_id, {**deepcopy(old), "identity": identity}, content_hash(identity)),
        ]:
            session.add(
                WorkflowRunModel(
                    id=row_id,
                    project_id=case.rig.project.id,
                    workflow_type=service.WORKFLOW_TYPE,
                    scope_type="project",
                    scope_id=case.rig.project.id,
                    status="failed",
                    requested_by="offline-test",
                    metadata_json=state,
                    idempotency_key=key,
                )
            )
        await session.commit()
    calls = admission_boundary(case, monkeypatch)
    async with case.factory() as session:
        result = await approve(case, session, max_repair_rounds=1)
    assert result["workflow_run_id"] == str(exact_id) and calls[0][0] == exact_id
    async with case.factory() as observer:
        assert len(list(await observer.scalars(select(WorkflowRunModel)))) == 3


@pytest.mark.parametrize(
    "field,value",
    [
        ("project_id", uuid4()),
        ("workflow_type", "other-workflow"),
        ("scope_type", "chapter"),
        ("scope_id", uuid4()),
    ],
)
async def test_other_workflow_scopes_never_selected(bootstrap, field, value):
    case = bootstrap
    wid, _, identity = await seed(case)
    async with case.factory() as session:
        row = await session.get(WorkflowRunModel, wid)
        setattr(row, field, value)
        await session.commit()
        assert (
            await service._find_policy_predecessor(
                case.wrap(session), project_id=case.rig.project.id, identity=identity
            )
            is None
        )


@pytest.mark.parametrize(
    "field",
    [
        "source_hash",
        "foundation",
        "binding",
        "from_chapter",
        "through_chapter",
        "continuity_hash",
        "max_repair_rounds",
        "producer_policy",
        "schema",
        "continuation_policy",
        "unknown_effective_config",
        "hard_admission_policy",
        "project_id",
    ],
)
async def test_other_identity_changes_never_alias_predecessor(bootstrap, field):
    case = bootstrap
    _, old, current_identity = await seed(case)
    changed = deepcopy(current_identity)
    if field == "hard_admission_policy":
        changed.pop(field)
    else:
        changed[field] = "different-effective-value"
    async with case.factory() as session:
        assert (
            await service._find_policy_predecessor(
                case.wrap(session), project_id=case.rig.project.id, identity=changed
            )
            is None
        )
    assert old["identity"]["hard_admission_policy"] == "retained-old-hard-policy"


@pytest.mark.parametrize("policy", [None, "", " ", 7])
async def test_old_empty_or_invalid_policy_is_not_a_predecessor(bootstrap, policy):
    case = bootstrap
    wid, old, current_identity = await seed(case)
    old["identity"]["hard_admission_policy"] = policy
    async with case.factory() as session:
        row = await session.get(WorkflowRunModel, wid)
        row.metadata_json = old
        await session.commit()
        assert (
            await service._find_policy_predecessor(
                case.wrap(session), project_id=case.rig.project.id, identity=current_identity
            )
            is None
        )
