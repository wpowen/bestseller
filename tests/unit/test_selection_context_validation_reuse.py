"""In-memory proof reuse guards only; fixtures are not native book acceptance."""
import asyncio
from copy import deepcopy
import gc

import pytest

from bestseller.services import fanqie_short_selection_readiness as readiness
from bestseller.services.fanqie_short_semantic_selection_adapter import SelectionContext

pytestmark = pytest.mark.unit


@pytest.fixture
def harness(monkeypatch):
    readiness._VALIDATED_CONTEXTS.clear()
    state = {'calls': 0, 'code': 'fixture-code', 'reject': False}
    view = {'semantic_derived_review': {'model_grade_asserted': False,
        'human_attraction_acceptance': 'not_performed', 'open_debt': [{'status': 'open', 'score': 52}]}}
    def derive(context):
        state['calls'] += 1
        if state['reject']:
            raise ValueError('complete_proof_rejected_changed_evidence')
        return deepcopy(view)
    monkeypatch.setattr(readiness, 'derive_selection_readiness', derive)
    monkeypatch.setattr(readiness, '_validation_code_fence', lambda: state['code'])
    def context():
        return SelectionContext(
            {'full_text': 'source body', 'title': 'source title', 'manifest': {'source': 'draft-a'}},
            {'native': {'operation': {'status': 'completed', 'owner': 'source-owner'}},
             'physical_files': {'source.request.json': b'original bytes'}, 'artifact_inventory': ['one']})
    async def reader(*args, **kwargs):
        return state['reader_context']
    monkeypatch.setattr(readiness.adapter, 'load_current_selection_context', reader)
    async def load(ctx):
        state['reader_context'] = ctx
        try:
            return await readiness.load_current_selection_context(None, None, project=None,
                assembly=None, original_report=None, current_inputs_factory=None)
        finally:
            state.pop('reader_context', None)
    yield state, context, load
    readiness._VALIDATED_CONTEXTS.clear()


def test_fresh_native_load_always_runs_complete_proof_but_same_object_does_not_duplicate(harness):
    state, context, load = harness
    first = context()
    asyncio.run(load(first))
    assert state['calls'] == 1
    readiness.selection_readiness_view(first)
    readiness.selection_readiness_view(first)
    assert state['calls'] == 1
    # Equal content in another native read is still a fresh proof boundary.
    second = context()
    asyncio.run(load(second))
    assert state['calls'] == 2
    readiness.selection_readiness_view(second)
    assert state['calls'] == 2


def test_caller_json_flags_and_equal_lookalikes_cannot_mint_native_loader_seal(harness):
    state, context, load = harness
    original = context()
    asyncio.run(load(original))
    forged = context()
    forged.evidence.update(verified=True, passed=True, validation_seal=True)
    state['reject'] = True
    with pytest.raises(ValueError, match='complete_proof_rejected'):
        readiness.selection_readiness_view(forged)
    assert state['calls'] == 2


@pytest.mark.parametrize('mutation', [
    lambda c: c.current_inputs.update(full_text='changed body'),
    lambda c: c.current_inputs.update(title='changed title'),
    lambda c: c.current_inputs['manifest'].update(source='foreign draft'),
    lambda c: c.evidence['native']['operation'].update(status='stopped'),
    lambda c: c.evidence['native']['operation'].update(owner='foreign owner'),
    lambda c: c.evidence['physical_files'].update({'source.request.json': b'changed bytes'}),
    lambda c: c.evidence['artifact_inventory'].append('duplicate paid leaf'),
    lambda c: c.evidence.update(raw_response='changed original return'),
])
def test_any_evidence_or_source_change_reenters_complete_proof(harness, mutation):
    state, context, load = harness
    ctx = context()
    asyncio.run(load(ctx))
    mutation(ctx)
    state['reject'] = True
    with pytest.raises(ValueError, match='complete_proof_rejected'):
        readiness.selection_readiness_view(ctx)
    assert state['calls'] == 2


def test_bytes_marker_cannot_be_forged_as_plain_json(harness):
    import hashlib
    state, context, load = harness
    ctx = context()
    asyncio.run(load(ctx))
    ctx.evidence['physical_files']['source.request.json'] = {
        readiness._BYTES_MARKER: hashlib.sha256(b'original bytes').hexdigest(),
        'length': len(b'original bytes')}
    state['reject'] = True
    with pytest.raises(ValueError, match='complete_proof_rejected'):
        readiness.selection_readiness_view(ctx)


def test_hot_code_or_configuration_change_invalidates_reuse(harness):
    state, context, load = harness
    ctx = context()
    asyncio.run(load(ctx))
    state.update(code='new physical source bytes', reject=True)
    with pytest.raises(ValueError, match='complete_proof_rejected'):
        readiness.selection_readiness_view(ctx)


def test_unavailable_runtime_fence_cannot_return_previous_positive(harness, monkeypatch):
    state, context, load = harness
    ctx = context()
    asyncio.run(load(ctx))
    def missing():
        raise FileNotFoundError('runtime source missing')
    monkeypatch.setattr(readiness, '_validation_code_fence', missing)
    state['reject'] = True
    with pytest.raises(ValueError, match='complete_proof_rejected'):
        readiness.selection_readiness_view(ctx)


def test_returned_view_mutation_cannot_clear_debt_or_invent_human_acceptance(harness):
    _, context, load = harness
    ctx = context()
    asyncio.run(load(ctx))
    view = readiness.selection_readiness_view(ctx)
    view['semantic_derived_review'].update(open_debt=[], human_attraction_acceptance='passed')
    unchanged = readiness.selection_readiness_view(ctx)['semantic_derived_review']
    assert unchanged['open_debt'] == [{'status': 'open', 'score': 52}]
    assert unchanged['human_attraction_acceptance'] == 'not_performed'
    assert unchanged['model_grade_asserted'] is False


@pytest.mark.parametrize('moving_part', ['evidence', 'code'])
def test_context_or_code_change_during_full_proof_never_gets_sealed(harness, monkeypatch, moving_part):
    state, context, load = harness
    ctx = context()
    original = readiness.derive_selection_readiness
    def moving(current):
        view = original(current)
        if moving_part == 'evidence':
            current.evidence.update(mutated_during_proof=True)
        else:
            state['code'] = 'changed during proof'
        return view
    monkeypatch.setattr(readiness, 'derive_selection_readiness', moving)
    asyncio.run(load(ctx))
    state['reject'] = True
    with pytest.raises(ValueError, match='complete_proof_rejected'):
        readiness.selection_readiness_view(ctx)


def test_seal_does_not_keep_native_context_alive_across_requests(harness, monkeypatch):
    _, context, load = harness
    ctx = context()
    asyncio.run(load(ctx))
    key = id(ctx)
    assert key in readiness._VALIDATED_CONTEXTS
    # Release the test reader closure, which deliberately captured this object.
    monkeypatch.setattr(readiness.adapter, 'load_current_selection_context', None)
    del ctx
    gc.collect()
    assert key not in readiness._VALIDATED_CONTEXTS
