"""New ID review through the real SQLite gateway; old native returns stay raw."""
from copy import deepcopy
import json

import pytest
from test_planning_draft_admission import grade_payload
from test_planning_draft_admission import harness as harness  # noqa: F401
from test_planning_admission_literal_runtime import literal as literal  # noqa: F401

from bestseller.services import llm, planning_draft_admission as admission
from bestseller.services import planning_admission_id_protocol as ids
from bestseller.services.budget import bind_execution_scope
from bestseller.services.planning_output_budget import LITERAL_ID_SOURCE_VERSION
from bestseller.services.planning_review_literal_transport import decode_literal_planning_review_transport

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.fixture
async def id_review(literal, monkeypatch):  # noqa: F811
    h = literal
    h.id_modes = []

    async def transport(request, role):
        h.sent.append(request)
        assert request.prompt_version == LITERAL_ID_SOURCE_VERSION
        record = request.metadata['source_transport']
        data = decode_literal_planning_review_transport(system=request.system_prompt,
            user=request.user_prompt, receipt=record, max_chars=record['max_chars'],
            max_estimated_tokens=record['max_estimated_tokens'])
        h.logical.append(deepcopy(data))
        supplied = data['input']
        assert supplied['schema'] == ids.SCHEMA
        catalog = ids.reference_contract(supplied)['catalog']
        by_pointer = {r['pointer']: k for k, r in catalog.items()}
        wire = grade_payload(h.kwargs()['candidate_payload'])
        mode = h.id_modes.pop(0) if h.id_modes else 'pass'
        if mode != 'pass':
            path = 'chapters[0].scenes[0]'
            violation = {'code': 'ABILITY_RULE_CONFLICT', 'path': path,
                'evidence': supplied['candidate_payload']['chapters'][0]['scenes'][0]['action'],
                'source_requirement': supplied['approved_world_spec']['requirement'],
                'source_refs': [by_pointer['/approved_world_spec/requirement']],
                'evidence_refs': [by_pointer['/candidate_payload/chapters/0/scenes/0/action']]}
            if mode == 'bad_id':
                violation['evidence_refs'] = ['r999999']
            wire.update(draft_eligible=False, hard_violations=[violation])
            for row in wire['projection_checks']:
                row.update(status='conflict', related_violation_paths=[path])
        calls = [{'id': 'id-protocol-fixture-' + str(len(h.sent)), 'type': 'function',
            'function': {'name': 'submit_planning_admission', 'arguments': json.dumps(wire, ensure_ascii=False)}}]
        raw = {'role': 'assistant', 'content': None, 'tool_calls': deepcopy(calls),
            'reasoning_content': 'Offline reasoning retained, never normalized into a grade'}
        h.native.append(deepcopy(raw))
        return '', 10, 5, 'tool_calls', calls, raw

    monkeypatch.setattr(llm, '_call_litellm', transport)
    return h


async def assess(h, state):
    async with h.session_factory() as session:
        async def save():
            await session.commit()
        with bind_execution_scope('audit:admission-fixture'):
            result = await admission.assess_planning_draft_admission(h.wrap(session), h.settings,
                **h.kwargs(), commercial_report=h.commercial, admission_schema=ids.SCHEMA,
                protocol_state=state, persist_protocol=save)
        await session.commit()
        return result


async def test_id_pass_reuses_physical_native_without_regrading_or_changing_sources(id_review):
    h = id_review
    before = deepcopy((h.kwargs()['candidate_payload'], h.commercial))
    state = {}
    receipt = await assess(h, state)
    assert receipt['schema'] == ids.SCHEMA and receipt['draft_eligible'] is True
    assert state['wire_version'] == LITERAL_ID_SOURCE_VERSION
    assert receipt['submission_receipt']['issues_dropped'] == 0
    async with h.session_factory() as session:
        await admission.verify_planning_draft_admission(h.wrap(session), **h.kwargs(),
            receipt=receipt, settings=h.settings)
    assert await assess(h, deepcopy(state)) == receipt
    assert len(h.sent) == 1 and (h.kwargs()['candidate_payload'], h.commercial) == before


async def test_valid_native_hard_rejection_preserves_ids_and_every_source_evidence_binding(id_review):
    h = id_review
    h.id_modes = ['reject']
    receipt = await assess(h, {})
    assert receipt['draft_eligible'] is False
    native = json.loads(h.native[0]['tool_calls'][0]['function']['arguments'])
    derived = receipt['submission_receipt']
    assert derived['original_payload'] == native
    issue = derived['resolved_violations'][0]
    assert issue['evidence_refs'][0]['pointer'] == '/candidate_payload/chapters/0/scenes/0/action'
    assert issue['source_refs'][0]['pointer'] == '/approved_world_spec/requirement'
    normalized = json.loads(receipt['raw_grade'])['hard_violations'][0]
    assert normalized['evidence'] == native['hard_violations'][0]['evidence']
    assert normalized['source_refs'] == ['/approved_world_spec/requirement']
    async with h.session_factory() as session:
        with pytest.raises(ValueError, match='admission_hard_rejected'):
            await admission.verify_planning_draft_admission(h.wrap(session), **h.kwargs(),
                receipt=receipt, settings=h.settings)
    assert len(h.sent) == 1


async def test_bad_id_uses_only_original_second_slot_with_id_specific_feedback(id_review):
    h = id_review
    h.id_modes = ['bad_id', 'pass']
    state = {}
    receipt = await assess(h, state)
    assert receipt['draft_eligible'] is True and len(h.sent) == len(state['attempts']) == 2
    old = state['attempts'][0]
    assert old['status'] == 'protocol_invalid' and old['completion']['raw_message'] == h.native[0]
    assert 'r999999' in old['completion']['tool_calls'][0]['function']['arguments']
    feedback = json.dumps(old['error'].get('feedback'), ensure_ascii=False)
    assert 'r999999' in feedback and 'absolute_json_pointer_required' not in feedback
    assert h.logical[0]['input'] == h.logical[1]['input']
    assert await assess(h, deepcopy(state)) == receipt
    assert len(h.sent) == 2
