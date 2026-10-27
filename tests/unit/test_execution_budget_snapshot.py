from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker
import test_execution_budget_core as core_fixtures

from bestseller.infra.db.models import ExecutionBudgetModel
from bestseller.services.budget import ExecutionLedger
from bestseller.services.execution_authorization import authorize_provider_completion
from bestseller.settings import BudgetSettings

ledger_db = core_fixtures.ledger_db

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


def completion_policy(status="active"):
    return {
        "mode": "completion_bound",
        "provider": "minimax",
        "status": status,
        "models": ["openai/MiniMax-M3"],
        "endpoint_hosts": ["api.minimaxi.com"],
        "objective": "complete the authorized objective",
        "authorization_ref": "snapshot-test-authorization",
        "aggregate_max_calls": None,
        "aggregate_max_tokens": None,
        "aggregate_deadline_at": None,
    }


async def read_snapshot(
    engine,
    *,
    status="active",
    policy=None,
    expired=True,
    usage=(315, 3918974),
    limits=(139, 1128666),
):
    """Read twice through the public inventory and verify the SQLite row is unchanged."""
    assert engine.url.get_backend_name() == "sqlite"
    assert "test" in engine.url.database
    scope_id = "snapshot-test-scope"
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    metadata = {
        "operator_limit_terminal_corrections": {
            "prior": {
                "original_fields": {
                    "execution_terminal": {"code": "execution_budget_exhausted", "terminal": True},
                }
            }
        },
        "reconciliations": [{"evidence": "keep-history"}],
    }
    if policy is not None:
        metadata["provider_authorization"] = policy
    async with sessions() as session, session.begin():
        session.add(
            ExecutionBudgetModel(
                scope_id=scope_id,
                status=status,
                max_calls=limits[0],
                max_tokens=limits[1],
                calls_used=usage[0],
                tokens_charged=usage[1],
                deadline_at=datetime.now(UTC) + timedelta(days=-2 if expired else 2),
                metadata_json=metadata,
            )
        )

    async def persisted_state():
        async with sessions() as session:
            row = await session.get(ExecutionBudgetModel, scope_id)
            return deepcopy(
                {column.name: getattr(row, column.name) for column in row.__table__.columns}
            )

    before = await persisted_state()
    ledger = ExecutionLedger(engine, scope_id, BudgetSettings())
    snapshot = await ledger.reconciliation_status()
    assert await ledger.reconciliation_status() == snapshot
    assert await persisted_state() == before
    assert snapshot["status"] == status
    assert (snapshot["calls_used"], snapshot["tokens_charged"]) == usage
    assert (snapshot["max_calls"], snapshot["max_tokens"]) == limits
    deadline = before["deadline_at"].replace(tzinfo=UTC).isoformat()
    assert snapshot["deadline_at"] == deadline
    assert snapshot["attempts"] == []
    assert snapshot["limit"] == 50
    assert snapshot["quarantined_remote_outcomes"] == 0
    assert snapshot["reconciliations"] == metadata["reconciliations"]
    return snapshot


@pytest.mark.parametrize(
    "status,policy_status,block_code",
    [
        ("active", "active", None),
        ("exhausted", "active", None),
        ("active", "paused", "execution_authorization_paused"),
        ("active", "completed", "execution_authorization_closed"),
        ("active", "closed", "execution_authorization_closed"),
        ("active", "unknown", "execution_authorization_closed"),
        ("active", "cancelled", "execution_authorization_closed"),
        ("unknown", "active", "execution_result_unknown"),
        ("cancelled", "active", "execution_cancelled"),
        ("unknown", "paused", "execution_result_unknown"),
        ("cancelled", "completed", "execution_cancelled"),
    ],
)
async def test_completion_snapshot_separates_historical_caps_from_current_permission(
    ledger_db,
    status,
    policy_status,
    block_code,
):
    policy = completion_policy(policy_status)
    snapshot = await read_snapshot(ledger_db, status=status, policy=policy)
    assert snapshot["provider_authorization"] == policy
    assert snapshot["mode"] == "completion_bound"
    assert snapshot["effective_limits"] == {
        "max_calls": None,
        "max_tokens": None,
        "deadline_at": None,
    }
    assert snapshot["block_code"] == block_code
    assert snapshot["execution_open"] is (block_code is None)


async def test_completion_snapshot_has_no_project_or_historical_value_special_case(ledger_db):
    snapshot = await read_snapshot(
        ledger_db,
        policy=completion_policy(),
        usage=(37, 900000),
        limits=(2, 1000),
    )
    assert snapshot["mode"] == "completion_bound"
    assert all(value is None for value in snapshot["effective_limits"].values())
    assert snapshot["execution_open"] is True
    assert snapshot["block_code"] is None


@pytest.mark.parametrize(
    "status,expired,block_code",
    [
        ("active", False, None),
        ("active", True, "execution_deadline_exhausted"),
        ("exhausted", False, "execution_budget_exhausted"),
        ("unknown", False, "execution_result_unknown"),
        ("cancelled", False, "execution_cancelled"),
    ],
)
async def test_bounded_snapshot_keeps_existing_limits_and_stop_precedence(
    ledger_db,
    status,
    expired,
    block_code,
):
    snapshot = await read_snapshot(ledger_db, status=status, expired=expired, usage=(1, 200))
    assert snapshot["mode"] == "bounded"
    assert snapshot["provider_authorization"] is None
    assert snapshot["effective_limits"] == {
        "max_calls": snapshot["max_calls"],
        "max_tokens": snapshot["max_tokens"],
        "deadline_at": snapshot["deadline_at"],
    }
    assert snapshot["block_code"] == block_code
    assert snapshot["execution_open"] is (block_code is None)


@pytest.mark.parametrize("missing", ["authorization_ref", "models", "endpoint_hosts", "objective"])
async def test_incomplete_completion_policy_does_not_project_unlimited_permission(
    ledger_db,
    missing,
):
    policy = completion_policy()
    policy.pop(missing)
    snapshot = await read_snapshot(ledger_db, policy=policy)
    assert snapshot["mode"] == "bounded"
    assert snapshot["provider_authorization"] is None
    assert snapshot["effective_limits"]["max_calls"] == 139
    assert snapshot["execution_open"] is False
    assert snapshot["block_code"] == "execution_deadline_exhausted"


async def test_existing_authorization_updates_snapshot_without_changing_historical_caps(ledger_db):
    await read_snapshot(ledger_db, status="exhausted")
    receipt = await authorize_provider_completion(
        ledger_db,
        scope_id="snapshot-test-scope",
        idempotency_key="new-approval",
        actor="test-operator",
        authorization_ref="test-user-approval",
        objective="complete the authorized objective",
        expected_max_calls=139,
        expected_max_tokens=1128666,
    )
    ledger = ExecutionLedger(ledger_db, "snapshot-test-scope", BudgetSettings())
    snapshot = await ledger.reconciliation_status()
    assert snapshot["provider_authorization"] == receipt["authorization"]
    assert snapshot["mode"] == "completion_bound"
    assert snapshot["effective_limits"] == {
        "max_calls": None,
        "max_tokens": None,
        "deadline_at": None,
    }
    assert snapshot["execution_open"] is True
    assert snapshot["block_code"] is None
    assert (snapshot["max_calls"], snapshot["max_tokens"]) == (139, 1128666)
    assert (snapshot["calls_used"], snapshot["tokens_charged"]) == (315, 3918974)
    assert (
        snapshot["deadline_at"]
        == datetime.fromisoformat(
            receipt["before"]["deadline_at"],
        )
        .replace(tzinfo=UTC)
        .isoformat()
    )
