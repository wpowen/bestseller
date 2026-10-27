"""Real SQLite fixed-baseline persistence; no provider or budget mutation."""
from copy import deepcopy
from datetime import UTC, datetime, timedelta
import hashlib
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from test_commercial_ambiguity_retirement import case as case, stopped
from test_commercial_protocol_transition_runtime import (
    transition_db as transition_db, protected_rows, workflow_count,
)
from bestseller.infra.db.models import ExecutionOperationModel, ExecutionAttemptModel
from bestseller.services import commercial_protocol_transition_runtime as runtime

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


async def seed(rig):
    snapshots = deepcopy(rig.case['snapshots'])
    stopped({'snapshots': snapshots, 'execution_scope_id': rig.scope})
    for row in snapshots['operations']:
        rig.session.add(ExecutionOperationModel(**row, owner='original-test-owner',
            lease_until=datetime.now(UTC) + timedelta(hours=1)))
    for row in snapshots['attempts']:
        rig.session.add(ExecutionAttemptModel(**row, created_at=datetime.now(UTC)))
    await rig.session.commit()
    return snapshots


async def inspect(rig, session=None, **changes):
    session = session if session is not None else rig.session
    operations = list(await session.scalars(select(ExecutionOperationModel)))
    attempts = list(await session.scalars(select(ExecutionAttemptModel)))
    arguments = dict(project_id=rig.project_id, chapter_id=rig.chapter_id, scope=rig.scope,
        body_hash=rig.case['body_sha256'], evaluation_contract=rig.contract,
        current_input_hash=rig.current_hash, effective_critic=rig.critic,
        roots={o.key for o in operations if o.status in {'completed', 'stopped'}},
        operations=operations, attempts=attempts)
    arguments.update(changes)
    return await runtime.inspect_ambiguity_retirement(session, **arguments)


def authorize_operator(session):
    session.info['commercial_ambiguity_operator_prepare'] = {
        'actor': 'fixture-explicit-operator', 'reason': 'Accept frozen ambiguity for one typed assessment'}


async def test_ordinary_runtime_cannot_create_a_baseline(transition_db):
    rig = transition_db
    await seed(rig)
    before = await protected_rows(rig)
    with pytest.raises(ValueError, match='explicit_operator_migration_required'):
        await inspect(rig)
    assert await runtime.persist_operator_ambiguity_retirement(rig.session) is None
    await rig.session.commit()
    assert await workflow_count(rig) == 0
    assert before == await protected_rows(rig)


async def test_operator_audit_is_durable_and_normal_reread_idempotent(transition_db):
    rig = transition_db
    await seed(rig)
    before = await protected_rows(rig)
    authorize_operator(rig.session)
    result = await inspect(rig)
    audit_id = await runtime.persist_operator_ambiguity_retirement(rig.session)
    assert audit_id
    await rig.session.commit()
    assert await workflow_count(rig) == 1
    assert before == await protected_rows(rig)
    async with rig.factory() as reader:
        assert result == await inspect(rig, reader)
        assert not reader.new and not reader.dirty
        proof = reader.info['commercial_ambiguity_verified']['receipt']
        assert proof['prior_spend_ambiguity_accepted'] is True
        assert proof['different_old_request_asserted'] is False
        assert proof['all_historical_debt_retained'] is True
        assert proof['operation_replay_allowed'] is False
        assert proof['cohort_identity_changed'] is False
        assert len(proof['binding']['allowed_slot_labels']) == 3
        assert await runtime.persist_operator_ambiguity_retirement(reader) is None
    assert before == await protected_rows(rig)


async def test_new_unlisted_ambiguity_never_auto_grows_saved_receipt(transition_db):
    rig = transition_db
    await seed(rig)
    authorize_operator(rig.session)
    await inspect(rig)
    await runtime.persist_operator_ambiguity_retirement(rig.session)
    await rig.session.commit()
    async with rig.factory() as reader:
        operations = list(await reader.scalars(select(ExecutionOperationModel)))
        attempts = list(await reader.scalars(select(ExecutionAttemptModel)))
        root = next(o for o in operations if o.status == 'completed')
        attempt = next(a for a in attempts if a.operation_key == root.key)
        new_key = hashlib.sha256(b'unlisted-root').hexdigest()
        # Original operator input changed, not the persisted authorization.
        row = runtime._row(root)
        row.update(key=new_key)
        row['result_json'] = deepcopy(row['result_json'])
        execution = row['result_json']['metadata']['execution_receipt']
        execution['operation_key'] = new_key
        execution['attempts'][0]['attempt_id'] = 'unlisted-attempt'
        new_root = ExecutionOperationModel(**{**row, 'lease_until': datetime.now(UTC)})
        a = runtime._row(attempt)
        a.update(id='unlisted-attempt', operation_key=new_key, created_at=datetime.now(UTC))
        new_attempt = ExecutionAttemptModel(**a)
        transport = next(o for o in operations if o.status == 'transport_result')
        t = runtime._row(transport)
        t['key'] = hashlib.sha256(f'transport-result.v1\n{rig.scope}\nunlisted-attempt'.encode()).hexdigest()
        t['result_json'] = deepcopy(t['result_json'])
        t['result_json'].update(attempt_id='unlisted-attempt', operation_key=new_key,
                                result=deepcopy(row['result_json']))
        new_transport = ExecutionOperationModel(**{**t, 'lease_until': datetime.now(UTC)})
        with pytest.raises(ValueError, match='receipt_or_snapshot_drift'):
            await inspect(rig, reader, roots={o.key for o in operations if o.status in {'completed','stopped'}} | {new_key},
                operations=operations+[new_root, new_transport], attempts=attempts+[new_attempt])
        assert not reader.new and not reader.dirty
    assert await workflow_count(rig) == 1


async def test_matching_current_request_and_nonterminal_attempt_cannot_be_retired(transition_db):
    rig = transition_db
    await seed(rig)
    authorize_operator(rig.session)
    root = next(o for o in await rig.session.scalars(select(ExecutionOperationModel)) if o.status=='completed')
    with pytest.raises(ValueError, match='matching_current'):
        await inspect(rig, current_input_hash=root.input_hash)
    attempts = list(await rig.session.scalars(select(ExecutionAttemptModel)))
    bad = [SimpleNamespace(**runtime._row(a)) for a in attempts]
    bad[0].status='unknown'
    # Use real unmapped ORM copies so runtime serializes the complete row schema.
    copies = [ExecutionAttemptModel(**{**vars(a), 'created_at': datetime.now(UTC)}) for a in bad]
    with pytest.raises(ValueError, match='nonterminal_attempt'):
        await inspect(rig, attempts=copies)
    assert await workflow_count(rig) == 0
