"""Explicit spent-slot migration with actual SQLite journal and ledger reads."""
from copy import deepcopy

import pytest

from bestseller.infra.db.models import WorkflowRunModel
from bestseller.services import planning_draft_admission as admission
from bestseller.services.prompt_source_policy import content_hash
from bestseller.services.retained_planning_generation import _Reader
from bestseller.services.terminal_planning_recovery import record_terminal_planning_slot
from test_known_terminal_planning_attempt import terminal_case as terminal_case  # noqa: F401

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


async def prepare(c, *, protocol=None):
    item, _, _ = await c.close()
    c.workflow.status = 'failed'
    c.workflow.current_step = c.workflow.error_message = 'execution_result_unknown'
    c.workflow.metadata_json = {'protocol': {} if protocol is None else protocol,
        'candidate_hash': content_hash(c.supplied['candidate_payload']),
        'execution_failure_receipts': [{'code': 'execution_result_unknown', 'original': True}]}
    await c.session.commit()
    return item


async def record(c, item):
    return await record_terminal_planning_slot(c.session, c.settings, project=c.project,
        workflow_id=c.workflow.id, supplied=c.supplied, item=item)


async def test_empty_legacy_protocol_migration_is_idempotent_and_preserves_original_failure(terminal_case):
    c = terminal_case
    item = await prepare(c)
    before = deepcopy((await _Reader(c.session).get(WorkflowRunModel,c.workflow.id)).metadata_json)
    receipt = await record(c,item)
    await c.session.commit()
    assert await record(c,item) == receipt
    saved = await _Reader(c.session).get(WorkflowRunModel,c.workflow.id)
    assert saved.status == 'failed' and saved.error_message == saved.current_step == 'execution_result_unknown'
    assert saved.metadata_json['execution_failure_receipts'] == before['execution_failure_receipts']
    protocol = saved.metadata_json['protocol']
    assert protocol['attempts'] == [item] and protocol['execution_scope'] == c.scope
    assert protocol['graded_input_hash'] == content_hash(c.supplied)
    assert receipt['spent_slot'] == 0 and receipt['protocol_attempt_limit'] == 2
    assert receipt['provider_dispatches'] == 0 and receipt['grade_granted'] is False
    assert not receipt['automatic_resume'] and not receipt['new_allowance_granted']
    assert await admission._prove_protocol_slot(c.session,c.settings,project=c.project,
        workflow_run_id=c.workflow.id,supplied=c.supplied,item=item,
        journal=[item],index=0,schema=admission.SCHEMA) == c.scope
    assert admission._protocol_error(item['completion'],c.supplied['candidate_payload'],c.supplied,
        schema=admission.SCHEMA)['detail'] == 'admission_incomplete_completion'


@pytest.mark.parametrize('fault', ['occupied','candidate','owner','policy'])
async def test_migration_refuses_existing_slots_changed_binding_or_other_owner(terminal_case,fault):
    c = terminal_case
    item = await prepare(c)
    meta = deepcopy(c.workflow.metadata_json)
    if fault == 'candidate':meta['candidate_hash'] = 'f'*64
    if fault in {'occupied','policy'}:
        meta['protocol'] = {'graded_input_hash':content_hash(c.supplied),
            'candidate_hash':content_hash(c.supplied['candidate_payload']),
            'hard_admission_policy':admission.HARD_ADMISSION_POLICY,
            'attempts':[{'old_rejected_evidence':True}] if fault=='occupied' else [],'status':'pending'}
    if fault == 'policy':meta['protocol']['hard_admission_policy'] = 'obsolete'
    if fault == 'owner':c.workflow.current_step = 'operator_cancelled'
    c.workflow.metadata_json = meta
    await c.session.commit()
    before = deepcopy((await _Reader(c.session).get(WorkflowRunModel,c.workflow.id)).metadata_json)
    with pytest.raises(ValueError,match='terminal_planning_migration:'):
        await record(c,item)
    assert (await _Reader(c.session).get(WorkflowRunModel,c.workflow.id)).metadata_json == before
