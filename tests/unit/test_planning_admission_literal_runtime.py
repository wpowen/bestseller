"""Literal admission through the real SQLite gateway, custody and paid proof."""
from copy import deepcopy
import json
from uuid import UUID

import pytest
from sqlalchemy import select
from test_planning_draft_admission import grade_payload
from test_planning_draft_admission import harness as harness  # noqa: F401

from bestseller.infra.db.models import ExecutionAttemptModel, ExecutionBudgetModel, LlmRunModel
from bestseller.services import llm, planning_draft_admission as admission
from bestseller.services.budget import bind_execution_scope
from bestseller.services.planning_admission_ref_protocol import SCHEMA as V4
from bestseller.services.planning_output_budget import LITERAL_FULL_SOURCE_VERSION as WIRE
from bestseller.services.planning_review_literal_transport import decode_literal_planning_review_transport

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]
SCOPE = "audit:admission-fixture"


@pytest.fixture
async def literal(harness, monkeypatch, tmp_path):  # noqa: F811
    h = harness
    h.settings.database.url = str(h.session_factory.kw['bind'].url)
    h.settings.llm.critic = h.settings.llm.critic.model_copy(update={
        'model': 'openai/MiniMax-M3.1-Flash', 'max_tokens': 32768, 'reasoning_effort': None})
    h.settings.budget.execution_max_tokens = 2_000_000
    h.settings.budget.execution_deadline_seconds = 600
    h.settings.artifact_store.mode = 'local'
    h.settings.artifact_store.local_dir = str(tmp_path / 'physical')
    h.settings.artifact_store.retain_prompt_payloads = True
    h.commercial['all_original_diagnostics'] = 'untrusted historical diagnostic ' * 5000
    h.commercial['late_literal'] = {'$ref': 'keep literally', 'raw': None, 'flag': False}
    h.logical, h.native, h.modes = [], [], []

    async def transport(request, role):
        h.sent.append(request)
        assert request.prompt_version == WIRE and role.reasoning_effort == 'low'
        record = request.metadata['source_transport']
        data = decode_literal_planning_review_transport(system=request.system_prompt,
            user=request.user_prompt, receipt=record, max_chars=record['max_chars'],
            max_estimated_tokens=record['max_estimated_tokens'])
        h.logical.append(deepcopy(data))
        wire = grade_payload(h.kwargs()['candidate_payload'])
        mode = h.modes.pop(0) if h.modes else 'pass'
        if mode == 'invalid':
            wire['unexpected'] = {'preserve_invalid_raw': True}
        calls = [{'id': 'literal-fixture-' + str(len(h.sent)), 'type': 'function',
            'function': {'name': 'submit_planning_admission',
                         'arguments': json.dumps(wire, ensure_ascii=False)}}]
        raw = {'role': 'assistant', 'content': None, 'tool_calls': deepcopy(calls),
               'reasoning_content': 'Retained private fixture reasoning, never a grade source'}
        h.native.append(deepcopy(raw))
        return '', 10, 5, 'tool_calls', calls, raw

    monkeypatch.setattr(llm, '_call_litellm', transport)
    return h


async def assess(h, state, *, persist=None, rollback=False):
    async with h.session_factory() as session:
        async def save():
            await session.commit()
            if persist:
                await persist()
        with bind_execution_scope(SCOPE):
            receipt = await admission.assess_planning_draft_admission(h.wrap(session), h.settings,
                **h.kwargs(), commercial_report=h.commercial, admission_schema=V4,
                protocol_state=state, persist_protocol=save)
        await session.rollback() if rollback else await session.commit()
        return receipt


@pytest.mark.parametrize('genre,language', [('mystery', 'en'), ('science_fiction', 'zh-CN')])
async def test_full_actual_gateway_and_physical_proof_reuse_same_slot(literal, genre, language):
    h = literal
    h.project.genre, h.project.language = genre, language
    original = deepcopy((h.kwargs()['candidate_payload'], h.commercial))
    state = {}
    receipt = await assess(h, state)
    assert receipt['schema'] == V4 and receipt['draft_eligible'] is True
    assert receipt['commercial_report']['pass'] is False
    assert state['wire_version'] == WIRE and len(state['attempts']) == 1
    assert h.logical[0]['input']['untrusted_commercial_report'] == original[1]
    assert h.logical[0]['input']['candidate_payload'] == original[0]
    assert len(h.sent[0].user_prompt) > 120_000
    async with h.session_factory() as session:
        run = await session.get(LlmRunModel, UUID(receipt['llm_run_id']))
        request = json.loads((__import__('pathlib').Path(h.settings.artifact_store.local_dir) / run.request_payload_ref).read_text())
        assert request['replayable'] is True
        assert request['messages']['user']['text'] == h.sent[0].user_prompt
        assert request['requested']['tools'] == h.sent[0].tools
        budget = await session.get(ExecutionBudgetModel, SCOPE)
        assert budget.calls_used == 1 and budget.tokens_charged == 15
        reader_settings = h.settings.model_copy(deep=True)
        reader_settings.artifact_store.retain_prompt_payloads = False
        await admission.verify_planning_draft_admission(h.wrap(session), **h.kwargs(),
            receipt=receipt, settings=reader_settings)
    assert await assess(h, deepcopy(state)) == receipt
    assert len(h.sent) == 1 and (h.kwargs()['candidate_payload'], h.commercial) == original


async def test_two_protocol_slots_keep_first_invalid_native_and_full_feedback(literal):
    h = literal
    h.modes = ['invalid', 'pass']
    state = {}
    receipt = await assess(h, state)
    assert receipt['draft_eligible'] is True and len(h.sent) == len(state['attempts']) == 2
    first = state['attempts'][0]
    assert first['status'] == 'protocol_invalid'
    assert 'unexpected' in json.loads(first['completion']['tool_calls'][0]['function']['arguments'])
    assert first['completion']['raw_message'] == h.native[0]
    assert 'protocol_repair' in h.logical[1]
    assert h.logical[0]['input'] == h.logical[1]['input']
    assert set(state['wire_contracts']) == {'0', '1'}
    assert await assess(h, deepcopy(state)) == receipt
    assert len(h.sent) == 2


@pytest.mark.parametrize('target', ['wire_version', 'wire_contract', 'physical_user', 'physical_tools', 'physical_response'])
async def test_changed_wire_or_physical_proof_never_reuses_grade(literal, target):
    h = literal
    state = {}
    receipt = await assess(h, state)
    changed = deepcopy(state)
    if target == 'wire_version':
        changed['attempts'][0]['wire_version'] = 'unapproved'
    elif target == 'wire_contract':
        changed['attempts'][0]['wire_contract']['candidate_hash'] = 'f' * 64
    else:
        async with h.session_factory() as session:
            run = await session.get(LlmRunModel, UUID(receipt['llm_run_id']))
            from pathlib import Path
            path = Path(h.settings.artifact_store.local_dir) / (
                run.response_payload_ref if target == 'physical_response' else run.request_payload_ref)
        value = json.loads(path.read_text())
        if target == 'physical_user':
            value['messages']['user']['text'] += ' changed'
        elif target == 'physical_tools':
            value['requested']['tools'] = []
        else:
            value['raw_message']['reasoning_content'] += ' changed'
        path.write_text(json.dumps(value))
    with pytest.raises((ValueError, AssertionError)):
        await assess(h, changed)
    assert len(h.sent) == 1
    async with h.session_factory() as session:
        budget = await session.get(ExecutionBudgetModel, SCOPE)
        assert budget.calls_used == 1 and budget.tokens_charged == 15


async def test_prepared_checkpoint_crash_resumes_same_request_before_first_paid_slot(literal):
    h = literal
    state = {}
    async def crash():
        if state.get('wire_contracts'):
            raise RuntimeError('offline interruption before dispatch')
    with pytest.raises(RuntimeError, match='offline interruption'):
        await assess(h, state, persist=crash)
    assert len(h.sent) == 0 and state['attempts'] == []
    frozen = deepcopy(state['wire_contracts']['0'])
    receipt = await assess(h, state)
    assert state['attempts'][0]['wire_contract'] == frozen
    assert receipt['draft_eligible'] is True and len(h.sent) == 1


async def test_existing_capacity_block_requires_explicit_migration_marker(literal):
    h = literal
    async with h.session_factory() as session:
        supplied = await admission._input(h.wrap(session), project=h.project,
            workflow_run_id=h.workflow.id, candidate_payload=h.kwargs()['candidate_payload'],
            book_spec=h.kwargs()['book_spec'], world_spec=h.kwargs()['world_spec'],
            cast_spec=h.kwargs()['cast_spec'], commercial_report=h.commercial, check_initial_context=True)
    supplied = admission._input_for_contract(supplied, V4)
    state = {'graded_input_hash': admission.content_hash(supplied),
        'hard_admission_policy': supplied['hard_admission_policy'],
        'candidate_hash': admission.content_hash(h.kwargs()['candidate_payload']),
        'attempts': [], 'status': 'input_budget_blocked'}
    with pytest.raises(admission.PlanningDraftAdmissionProtocolError):
        await assess(h, state)
    assert h.sent == [] and 'wire_version' not in state and state['attempts'] == []


async def test_prepared_configuration_change_stops_without_paid_dispatch(literal):
    h = literal
    state = {}
    async def crash():
        if state.get('wire_contracts'):
            raise RuntimeError('prepared')
    with pytest.raises(RuntimeError, match='prepared'):
        await assess(h, state, persist=crash)
    old = deepcopy(state)
    h.settings.llm.critic.reasoning_effort = 'high'
    with pytest.raises(ValueError, match='admission_literal_prepared_input_changed'):
        await assess(h, state)
    assert h.sent == [] and state == old
    async with h.session_factory() as session:
        assert list(await session.scalars(select(ExecutionAttemptModel))) == []


async def test_readback_ignores_later_finite_capacity_but_new_dispatch_still_obeys_it(literal, monkeypatch):
    h = literal
    state = {}
    receipt = await assess(h, state)
    from bestseller.services import finite_book_budget
    monkeypatch.setattr(finite_book_budget, 'current_capacity', lambda **kwargs: 65536)
    # Existing paid input and its complete physical evidence remain identical.
    assert await assess(h, deepcopy(state)) == receipt
    assert len(h.sent) == 1
    async with h.session_factory() as session:
        supplied = await admission._input(h.wrap(session), project=h.project,
            workflow_run_id=h.workflow.id, candidate_payload=h.kwargs()['candidate_payload'],
            book_spec=h.kwargs()['book_spec'], world_spec=h.kwargs()['world_spec'],
            cast_spec=h.kwargs()['cast_spec'], commercial_report=h.commercial, check_initial_context=True)
    from bestseller.services.llm_capacity import ModelRequestCapacityError
    with pytest.raises(ModelRequestCapacityError):
        admission.build_literal_admission_request(h.settings, project=h.project,
            workflow_run_id=h.workflow.id, supplied=admission._input_for_contract(supplied, V4),
            journal=[], index=0)
    assert len(h.sent) == 1
