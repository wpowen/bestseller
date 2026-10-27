"""Actual entry, gateway, SQLite paid/transport receipts; simulated provider only."""
from copy import deepcopy
from pathlib import Path
import json
from uuid import UUID
import pytest
from test_planning_draft_admission import harness as harness  # noqa:F401
from test_volume_source_admission import volume as volume  # noqa:F401
from bestseller.services import planning_draft_admission as a, llm
from bestseller.services.budget import bind_execution_scope
from bestseller.services.lossless_source_encoding import decode_lossless_source
from bestseller.services.planning_quote_evidence import quote_contract,quote_selection_view
from bestseller.infra.db.models import LlmRunModel, ExecutionOperationModel

SCOPE = 'audit:admission-fixture'


def request_data(request):
    if 'source_review_data=' in request.user_prompt:
        encoded, _ = json.JSONDecoder().raw_decode(request.user_prompt.split('source_review_data=', 1)[1])
        return decode_lossless_source(encoded)
    return json.loads(request.user_prompt)


@pytest.fixture
async def quoted(volume, monkeypatch):
    h = volume
    h.mode = 'pass'
    async def transport(request, role):
        h.requests.append(request)
        assert request.prompt_version == a.VOLUME_QUOTE_SCHEMA
        data = request_data(request); supplied = data['input']
        c = quote_contract(supplied)
        assert data['quote_selection'] == quote_selection_view(c)
        assert supplied['schema'] == a.VOLUME_QUOTE_SCHEMA
        fields = request.tools[0]['function']['parameters']['properties']['hard_violations']['items']['properties']
        assert 'candidate_quote_id' in fields and 'evidence' not in fields
        wire = {key: c[key] for key in ('schema', 'input_sha256', 'candidate_sha256', 'catalog_sha256')}
        wire.update(draft_eligible=h.mode == 'pass', hard_violations=[], editorial_notes=[],
                    projection_checks=[{**t, 'status': 'consistent', 'explanation': 'Full fixture projection checked.'}
                                       for t in supplied['projection_audit_targets']])
        if h.mode in {'reject', 'bad_id', 'bad_conflict'}:
            ids = {item['pointer']: key for key, item in c['catalog'].items()}
            path = 'volumes[0].information_flow_scene_cards[0]'
            wire['hard_violations'] = [{'code': 'ABILITY_RULE_CONFLICT', 'path': path,
                'candidate_quote_id': ids['/candidate_payload/volumes/0/information_flow_scene_cards/0/actual_action'] if h.mode in {'reject', 'bad_conflict'} else 'bad-id',
                'source_quote_ids': [ids['/approved_world_spec/requirement']]}]
            for check in wire['projection_checks']:
                check.update(status='conflict', related_violation_paths=[path])
        elif h.mode == 'old_wire':
            for key in ('schema', 'input_sha256', 'candidate_sha256', 'catalog_sha256'): wire.pop(key)
            wire['draft_eligible'] = True
        if h.mode == 'bad_consistent':
            wire['draft_eligible'] = True
            wire['projection_checks'][0]['related_violation_paths'] = []
        if h.mode == 'bad_conflict':
            wire['projection_checks'][0].pop('related_violation_paths')
        h.wire = deepcopy(wire)
        raw = json.dumps(wire, ensure_ascii=False)
        if h.mode == 'actual_misnested':
            raw = (Path(__file__).parents[1]/'fixtures/planning_admission/misnested_editorial_notes_2040da20.json').read_text()
            h.wire = json.loads(raw)
        calls = [{'id': 'fixture-quote', 'type': 'function', 'function': {
            'name': 'submit_planning_admission', 'arguments': raw}}]
        return '', 10, 5, 'tool_calls', calls, {'role': 'assistant', 'content': None, 'tool_calls': deepcopy(calls)}
    monkeypatch.setattr(llm, '_call_litellm', transport)
    return h


async def assess(h, session, **extra):
    with bind_execution_scope(SCOPE):
        return await a.assess_planning_draft_admission(h.wrap(session), h.settings,
            **h.volume_kwargs(), commercial_report=h.commercial,
            admission_schema=a.VOLUME_QUOTE_SCHEMA, **extra)


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['pass', 'reject'])
async def test_full_entry_resolves_ids_and_checks_actual_physical_receipts(quoted, mode):
    h = quoted; h.mode = mode
    async with h.session_factory() as session:
        receipt = await assess(h, session); await session.commit()
    assert receipt['draft_eligible'] is (mode == 'pass')
    assert receipt['schema'] == a.VOLUME_QUOTE_SCHEMA
    assert receipt['submission_receipt']['parsed_payload'] == h.wire
    assert 'candidate_quote_id' not in receipt['raw_grade']
    assert len(h.requests) == 1
    async with h.session_factory() as session:
        run = await session.get(LlmRunModel, UUID(receipt['llm_run_id']))
        assert run.prompt_version == a.VOLUME_QUOTE_SCHEMA
        if mode == 'pass':
            await a.verify_volume_source_admission(h.wrap(session), settings=h.settings, **h.volume_kwargs(), receipt=receipt)
        else:
            with pytest.raises(a.PlanningDraftAdmissionError, match='hard_rejected'):
                await a.verify_volume_source_admission(h.wrap(session), settings=h.settings, **h.volume_kwargs(), receipt=receipt)
        # Physical mutation cannot be laundered through normalized text.
        operation = await session.get(ExecutionOperationModel, run.metadata_json['execution_receipt']['operation_key'])
        operation.result_json = {**operation.result_json, 'tool_calls': []}
        await session.flush()
        with pytest.raises(a.PlanningDraftAdmissionError, match='paid_grade_mismatch'):
            await a.verify_volume_source_admission(h.wrap(session), settings=h.settings, **h.volume_kwargs(), receipt=receipt)


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['bad_id', 'old_wire', 'bad_consistent', 'bad_conflict', 'actual_misnested'])
async def test_wrong_id_or_old_wire_exhausts_once_without_grade_or_retry(quoted, mode):
    h = quoted; h.mode = mode; state = {}
    async with h.session_factory() as session:
        with pytest.raises(a.PlanningDraftAdmissionProtocolError):
            await assess(h, session, protocol_state=state)
        assert state['status'] == 'protocol_exhausted' and len(state['attempts']) == 1
        assert state['attempts'][0]['completion']['tool_calls']
        if mode == 'actual_misnested':
            assert state['attempts'][0]['error']['detail'] == 'planning_admission_submission:quote_contract_stale'
            assert json.loads(state['attempts'][0]['completion']['tool_calls'][0]['function']['arguments']) == h.wire
        await session.commit()
    assert len(h.requests) == 1
    # Same exhausted state reuses its single result, never dispatches again.
    async with h.session_factory() as session:
        with pytest.raises(a.PlanningDraftAdmissionProtocolError):
            await assess(h, session, protocol_state=state)
    assert len(h.requests) == 1


@pytest.mark.asyncio
async def test_old_protocol_state_cannot_be_relabelled_as_quote_version(quoted):
    h = quoted
    state = {'graded_input_hash': 'old-input', 'hard_admission_policy': a.VOLUME_HARD_ADMISSION_POLICY,
             'attempts': [], 'status': 'protocol_exhausted'}
    before = deepcopy(state)
    async with h.session_factory() as session:
        with pytest.raises(a.PlanningDraftAdmissionError, match='protocol_input_changed'):
            await assess(h, session, protocol_state=state)
    assert not h.requests and state == before
