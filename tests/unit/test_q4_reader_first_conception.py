"""Offline Q4 seam tests: synthetic readers, no DB or provider connections."""
from __future__ import annotations

import copy
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from bestseller.services import conception as c, concept_tournament as t
from bestseller.services import source_bound_planning_content as p
from bestseller.services.conception_checkpoints import build_conception_checkpoint, validate_conception_checkpoint
from unit.test_conception_candidate_resume import review_state

pytestmark = pytest.mark.unit
RAW = "  姐妹争夺旧屋，最后选择彼此。\n保留原输入空白。  "


def tagged_state():
    state = review_state()
    state['ctx']['reader_first_planning'] = p.new_reader_first_binding(RAW)
    state['ctx']['reader_first_original_input'] = RAW
    state['reader_first_request_context'] = {'project_id': str(uuid4()), 'workflow_run_id': str(uuid4())}
    return state


def response(request, score=.9):
    data = json.loads(request.user_prompt)
    return json.dumps({
        'schema': 'reader-first-review.v1', 'checkpoint': 'skeleton',
        'source_sha256': data['source_sha256'], 'config_sha256': data['config_sha256'],
        'dimensions': {name: {'score': score, 'reason': name + ': sisters must choose',
                              'evidence': [{'path': '/original_user_input', 'quote': RAW}]}
                       for name in p.READER_FIRST_DIMENSIONS},
        'issues': [], 'options': ['保留姐妹，重排最后一次选择。', '先明确旧屋与姐妹哪个承诺应兑现。'],
    }, ensure_ascii=False)


def test_new_binding_uses_exact_raw_input_and_can_disable_before_generation():
    ctx = {'genre': '现实', 'chapter_count': 5}
    marker = c._bind_reader_first_conception(ctx, original_user_input=RAW)
    assert marker == p.new_reader_first_binding(RAW, protocol='reader-first-planning.v2')
    assert ctx['reader_first_original_input'] == RAW
    disabled = {}
    assert c._bind_reader_first_conception(disabled, user_hints={'reader_first_planning': False}, original_user_input=RAW) is None
    assert 'reader_first_planning' not in disabled


def test_resume_legacy_stays_unmarked_and_tagged_assets_cannot_downgrade():
    legacy = review_state()
    assert c._restore_reader_first_conception(legacy) is None
    assert 'reader_first_planning' not in legacy['writing_profile']
    state = tagged_state()
    marker = c._restore_reader_first_conception(state)
    assert state['writing_profile']['reader_first_planning'] == marker
    assert state['reader_first_planning'] == marker
    with pytest.raises(ValueError):
        c._restore_reader_first_conception(state, disable=True)
    state['writing_profile']['reader_first_planning']['protocol'] = 'future'
    with pytest.raises(ValueError):
        c._restore_reader_first_conception(state)


@pytest.mark.parametrize('language', ['zh-CN', 'en'])
def test_finalize_uses_shared_instruction_without_rewriting_legacy(monkeypatch, language):
    ctx = c._build_genre_context('urban', 5)
    fn = c._finalize_user_prompt_en if language == 'en' else c._finalize_user_prompt
    old = fn(ctx, {}, {}, {}, {})
    ctx['reader_first_planning'] = p.new_reader_first_binding(RAW)
    seen = []
    def instruction(binding, **kwargs):
        seen.append((binding, kwargs))
        return '\nSYNTHETIC-READER-INSTRUCTION\n'
    monkeypatch.setattr(p, 'reader_first_instruction', instruction)
    new = fn(ctx, {}, {}, {}, {})
    assert new == '\nSYNTHETIC-READER-INSTRUCTION\n' + old
    assert seen[0][0] == ctx['reader_first_planning']
    assert seen[0][1]['language'].startswith(language[:2])


BUILDERS = [
    ('_build_raw_idea_pool_messages', {'genre': '现实', 'sub_genre': '家庭', 'count': 2}),
    ('_build_raw_idea_rank_messages', {'genre': '现实', 'sub_genre': '家庭', 'ideas': [('a', RAW)]}),
    ('_build_engine_kernel_messages', {'genre': '现实', 'sub_genre': '家庭', 'lane': 'a', 'chapter_count': 5}),
    ('_build_engine_judge_messages', {'kernel': {}, 'genre': '现实', 'sub_genre': '家庭', 'chapter_count': 5, 'seed_concept': RAW}),
    ('_build_engine_batch_judge_messages', {'cards': [], 'genre': '现实', 'sub_genre': '家庭', 'chapter_count': 5}),
    ('_build_story_layer_judge_messages', {'card': {}, 'genre': '现实', 'sub_genre': '家庭'}),
    ('_build_expansion_editor_messages', {'card': {}, 'genre': '现实', 'sub_genre': '家庭'}),
    ('_build_judge_messages', {'candidate': t.ConceptCandidate('a'), 'genre': '现实', 'references': []}),
]


@pytest.mark.parametrize('name,kwargs', BUILDERS)
@pytest.mark.parametrize('language', ['zh', 'en'])
def test_tournament_generator_and_judge_share_binding(monkeypatch, name, kwargs, language):
    fn = getattr(t, name)
    legacy = fn(**kwargs)
    seen = []
    def instruction(binding, **options):
        seen.append((binding, options))
        return 'SYNTHETIC RULE'
    monkeypatch.setattr(p, 'reader_first_instruction', instruction)
    binding = p.new_reader_first_binding(RAW)
    system, user = fn(**kwargs, reader_first_planning=binding, language=language)
    assert (system, user) == ('SYNTHETIC RULE' + legacy[0], legacy[1])
    assert seen == [(binding, {'language': language, 'stage': 'concept_tournament'})]


@pytest.mark.asyncio
async def test_review_sees_full_final_candidate_ids_and_freshness(monkeypatch):
    state = tagged_state()
    state['world_model_payload'] = {'rules': ['second rule', 'last rule']}
    state['writing_profile']['character']['cast'] = [{'name': 'older'}, {'name': 'younger'}]
    state['concept_contract'] = {'reader_promise': 'reconciliation'}
    state['custom_ctx_key'] = {'retained': True}
    sent, saved = [], []
    async def save():
        saved.append(copy.deepcopy(state))
    async def complete(session, settings, request):
        sent.append(request)
        assert saved, 'persist candidate before sending'
        source = json.loads(request.user_prompt)['source']
        assert source['original_user_input'] == RAW
        assert source['candidate']['world_model'] == state['world_model_payload']
        assert source['candidate']['writing_profile']['character']['cast'][-1]['name'] == 'younger'
        assert source['candidate']['concept_contract'] == state['concept_contract']
        return SimpleNamespace(content=response(request), llm_run_id=uuid4(), fallback_used=False, finish_reason='stop')
    monkeypatch.setattr(c, 'complete_text', complete)
    result = await c._review_reader_first_skeleton(None, None, state=state, checkpoint_save=save)
    assert result['status'] == 'passed'
    assert str(sent[0].project_id) == state['reader_first_request_context']['project_id']
    assert str(sent[0].workflow_run_id) == state['reader_first_request_context']['workflow_run_id']
    assert sent[0].max_tokens_override == 4096
    assert saved[-1]['reader_first_skeleton_review']['receipt'] == result
    # Same exact candidate can reuse the grounded receipt; changed tail cannot.
    await c._review_reader_first_skeleton(None, None, state=state, checkpoint_save=save)
    assert len(sent) == 1
    state['world_model_payload']['rules'][-1] = 'changed final rule'
    await c._review_reader_first_skeleton(None, None, state=state, checkpoint_save=save)
    assert len(sent) == 2
    assert state['custom_ctx_key'] == {'retained': True}


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['failed', 'fallback', 'transport', 'malformed', 'missing_ids', 'capacity', 'truncated'])
async def test_stop_is_structured_preserves_bundle_and_does_not_retry_on_resume(monkeypatch, mode):
    state = tagged_state()
    state['repair_quotas'] = {'reader_first_skeleton': 1, 'other': 3}
    if mode == 'capacity':
        state['world_model_payload'] = {'complete': '界' * 60000}
    if mode == 'missing_ids':
        state.pop('reader_first_request_context')
    original = copy.deepcopy(state)
    sends = []
    async def complete(session, settings, request):
        sends.append(request)
        if mode == 'transport':
            raise RuntimeError('synthetic unavailable')
        return SimpleNamespace(content='{}' if mode == 'malformed' else response(request, .4 if mode == 'failed' else .9),
                               llm_run_id=uuid4(), fallback_used=mode == 'fallback',
                               finish_reason='length' if mode == 'truncated' else 'stop')
    monkeypatch.setattr(c, 'complete_text', complete)
    saves = []
    async def save():
        saves.append(build_conception_checkpoint(state, input_hash='same'))
    with pytest.raises(p.ReaderFirstDirectionRequired) as failure:
        await c._review_reader_first_skeleton(None, None, state=state, checkpoint_save=save)
    payload = failure.value.payload
    assert payload['status'] == ('failed' if mode == 'failed' else 'unknown')
    assert set(payload['scores']) == set(p.READER_FIRST_DIMENSIONS)
    assert len(payload['options']) in {2, 3}
    assert payload['reasons']
    if mode in {'fallback', 'truncated'}:
        assert all(value == .9 for value in payload['scores'].values())
        assert payload['mean_score'] is None
        assert payload['options'] == ['保留姐妹，重排最后一次选择。', '先明确旧屋与姐妹哪个承诺应兑现。']
    if mode == 'failed':
        assert all(value == .4 for value in payload['scores'].values())
        assert all('sisters must choose' in reason['reason'] for reason in payload['reasons'] if 'dimension' in reason)
    for key in ['premise', 'synopsis', 'world_model_payload', 'story_spine', 'concept_contract']:
        assert state[key] == original[key]
    assert state['repair_quotas'] == original['repair_quotas']
    resumed = validate_conception_checkpoint(saves[-1], input_hash='same')
    before = len(sends)
    with pytest.raises(p.ReaderFirstDirectionRequired):
        await c._review_reader_first_skeleton(None, None, state=resumed, checkpoint_save=save)
    assert len(sends) == before
    assert len(sends) == (0 if mode in {'capacity', 'missing_ids'} else 1)


@pytest.mark.asyncio
async def test_real_resume_runs_reader_gate_after_existing_final_repairs(monkeypatch):
    state = tagged_state()
    sequence = []
    async def audit(*args, **kwargs):
        sequence.append('existing_final_review')
        return ({'passed': True, 'quality_passed': True,
                 'audit_identity': c._final_packaging_audit_identity(title=kwargs['title'], synopsis=kwargs['synopsis'])}, {}, {}, '', [])
    async def complete(session, settings, request):
        sequence.append('reader')
        return SimpleNamespace(content=response(request), llm_run_id=uuid4(), fallback_used=False, finish_reason='stop')
    monkeypatch.setattr(c, '_audit_final_packaging_candidate', audit)
    monkeypatch.setattr(c, 'complete_text', complete)
    result = await c.run_conception_pipeline(None, None, genre_key='urban', chapter_count=5,
        resume_checkpoint=build_conception_checkpoint(state, input_hash='same'), checkpoint_input_hash='same')
    assert sequence == ['existing_final_review', 'reader']
    assert result.writing_profile['reader_first_planning'] == state['ctx']['reader_first_planning']
    assert result.story_appeal['reader_first_skeleton']['status'] == 'passed'
    assert len(result.llm_run_ids) == 2


@pytest.mark.asyncio
async def test_cached_pass_without_raw_review_is_never_authority(monkeypatch):
    state = tagged_state()
    async def complete(session, settings, request):
        return SimpleNamespace(content=response(request), llm_run_id=uuid4(), fallback_used=False, finish_reason='stop')
    monkeypatch.setattr(c, 'complete_text', complete)
    await c._review_reader_first_skeleton(None, None, state=state)
    state['reader_first_skeleton_review']['raw'] = None
    with pytest.raises(p.ReaderFirstDirectionRequired) as failure:
        await c._review_reader_first_skeleton(None, None, state=state)
    assert failure.value.payload['status'] == 'unknown'


def repair_response(request, corrupt=None):
    data = json.loads(request.user_prompt)
    candidate = copy.deepcopy(data['source']['candidate'])
    candidate['premise'] = '姐妹一起决定离开旧屋，彼此重新靠近。'
    candidate['synopsis'] = '姐姐放下旧屋，妹妹选择留下共同记忆，两人再次同行。'
    candidate['story_spine']['wants'] = '与妹妹一起告别旧屋'
    candidate['world_model'] = {'rule': '旧屋将拆除，姐妹必须选择去留'}
    candidate['writing_profile']['character']['relationship_tension'] = '姐姐想留，妹妹想走'
    frozen = {key: copy.deepcopy(data['source'][key]) for key in ('original_user_input', 'creation_inputs')}
    payload = {'schema': 'reader-first-conception-repair.v1',
               'source_sha256': data['source_sha256'], 'config_sha256': data['config_sha256'],
               'user_input_sha256': data['binding']['user_input_sha256'],
               'frozen_controls': frozen, 'candidate': candidate}
    if corrupt == 'missing_world':
        candidate.pop('world_model')
    elif corrupt == 'wrong_type':
        candidate['writing_profile'] = []
    elif corrupt == 'source_identity':
        payload['source_sha256'] = '0' * 64
    elif corrupt == 'frozen_input':
        payload['frozen_controls']['original_user_input'] = 'changed author input'
    elif corrupt == 'downgrade':
        candidate['writing_profile'].pop('reader_first_planning')
    elif corrupt == 'spine_only':
        payload['candidate'] = {'story_spine': candidate['story_spine']}
    elif corrupt == 'missing_cast':
        candidate['writing_profile'].pop('character')
    return json.dumps(payload, ensure_ascii=False)


@pytest.mark.asyncio
@pytest.mark.parametrize('second_score', [.9, .4])
async def test_one_coherent_repair_persisted_before_send_and_native_rechecks(monkeypatch, second_score):
    state = tagged_state()
    state['ctx']['opaque_frozen'] = {'keep': ['all', 'values']}
    sequence, saved, requests = [], [], []
    initial_ctx = copy.deepcopy(state['ctx'])
    async def save(checkpoint):
        saved.append(copy.deepcopy(checkpoint))
    async def audit(*args, **kwargs):
        sequence.append('native')
        return ({'passed': True, 'quality_passed': True,
                 'audit_identity': c._final_packaging_audit_identity(title=kwargs['title'], synopsis=kwargs['synopsis'])}, {}, {}, '', [])
    async def complete(session, settings, request):
        requests.append(request)
        if request.prompt_template == 'conception_reader_first_repair_v1':
            sequence.append('repair')
            assert saved[-1]['state']['repair_quotas']['reader_first_skeleton'] == 1
            content = repair_response(request)
        else:
            sequence.append('reader')
            content = response(request, .4 if len(requests) == 1 else second_score)
        return SimpleNamespace(content=content, llm_run_id=uuid4(), fallback_used=False, finish_reason='stop')
    monkeypatch.setattr(c, '_audit_final_packaging_candidate', audit)
    monkeypatch.setattr(c, 'complete_text', complete)
    call = dict(state=state, checkpoint_callback=save, checkpoint_input_hash='same')
    if second_score == .9:
        result = await c._complete_conception_candidate(None, None, **call)
        assert result.premise == '姐妹一起决定离开旧屋，彼此重新靠近。'
        assert result.world_model['rule'] == '旧屋将拆除，姐妹必须选择去留'
        assert result.writing_profile['character']['relationship_tension'] == '姐姐想留，妹妹想走'
        assert len(result.llm_run_ids) == 4
    else:
        with pytest.raises(p.ReaderFirstDirectionRequired) as failure:
            await c._complete_conception_candidate(None, None, **call)
        assert failure.value.payload['repair']['used'] == 1
        assert len(state['llm_run_ids']) == 4
        count = len(requests)
        resumed = validate_conception_checkpoint(saved[-1], input_hash='same')
        with pytest.raises(p.ReaderFirstDirectionRequired):
            await c._complete_conception_candidate(None, None, state=resumed, checkpoint_callback=save, checkpoint_input_hash='same')
        assert len(requests) == count
    assert sequence[:5] == ['native', 'reader', 'repair', 'native', 'reader']
    assert state['ctx'] == initial_ctx
    assert state['repair_quotas']['reader_first_skeleton'] == 1
    assert state['reader_first_skeleton_repair']['status'] == 'pending_native_review'
    assert len(requests) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize('corrupt', ['missing_world', 'wrong_type', 'source_identity', 'frozen_input', 'downgrade', 'spine_only', 'missing_cast'])
async def test_invalid_full_repair_is_retained_without_adoption_or_second_credit(monkeypatch, corrupt):
    state = tagged_state()
    original = copy.deepcopy(state)
    saved, requests = [], []
    async def save(item):
        saved.append(copy.deepcopy(item))
    async def audit(*args, **kwargs):
        return ({'passed': True, 'quality_passed': True,
                 'audit_identity': c._final_packaging_audit_identity(title=kwargs['title'], synopsis=kwargs['synopsis'])}, {}, {}, '', [])
    async def complete(session, settings, request):
        requests.append(request)
        content = repair_response(request, corrupt) if request.prompt_template == 'conception_reader_first_repair_v1' else response(request, .4)
        return SimpleNamespace(content=content, llm_run_id=uuid4(), fallback_used=False, finish_reason='stop')
    monkeypatch.setattr(c, 'complete_text', complete)
    monkeypatch.setattr(c, '_audit_final_packaging_candidate', audit)
    with pytest.raises(p.ReaderFirstDirectionRequired) as failure:
        await c._complete_conception_candidate(None, None, state=state, checkpoint_callback=save, checkpoint_input_hash='same')
    assert len(requests) == 2
    assert state['repair_quotas']['reader_first_skeleton'] == 1
    assert state['reader_first_skeleton_repair']['status'] == 'rejected'
    assert failure.value.payload['reasons'][-1]['code'] == 'reader_first_full_candidate_repair_rejected'
    for key in ('ctx', 'premise', 'synopsis', 'story_spine', 'world_model_payload', 'concept_contract'):
        assert state[key] == original[key]


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['explicit', 'audit_context', 'disabled', 'missing'])
async def test_creation_preflight_happens_before_any_generation(monkeypatch, mode):
    from contextlib import ExitStack
    from bestseller.services import llm
    ids = {'project_id': uuid4(), 'workflow_run_id': uuid4()}
    captured = []
    class BeforeFirstModel(Exception):
        pass
    async def intercept(session, settings, ctx, **kwargs):
        captured.append(copy.deepcopy(ctx))
        raise BeforeFirstModel()
    monkeypatch.setattr(c, '_attach_concept_methodology', intercept)
    options = dict(genre_key='urban', chapter_count=5, original_user_input=RAW)
    if mode == 'explicit':
        options.update(ids)
    elif mode == 'disabled':
        options['user_hints'] = {'reader_first_planning': False}
    with ExitStack() as stack:
        if mode == 'audit_context':
            stack.enter_context(llm.bind_project_prompt_audit(ids['project_id'], reason='offline fixture'))
            stack.enter_context(llm.bind_conception_model(None, workflow_run_id=ids['workflow_run_id']))
        with pytest.raises(ValueError if mode == 'missing' else BeforeFirstModel):
            await c.run_conception_pipeline(None, None, **options)
    if mode == 'missing':
        assert captured == []
    elif mode == 'disabled':
        assert 'reader_first_planning' not in captured[0]
    else:
        assert captured[0]['reader_first_planning'] == p.new_reader_first_binding(
            RAW, protocol='reader-first-planning.v2',
        )
        assert captured[0]['reader_first_request_context'] == {key: str(value) for key, value in ids.items()}


@pytest.mark.asyncio
@pytest.mark.parametrize('bad_result', ['missing_run_id', 'nonfinal', 'tools'])
async def test_only_attributable_completed_review_can_pass(monkeypatch, bad_result):
    state = tagged_state()
    async def complete(session, settings, request):
        return SimpleNamespace(content=response(request), llm_run_id=None if bad_result == 'missing_run_id' else uuid4(),
                               fallback_used=False, finish_reason='content_filter' if bad_result == 'nonfinal' else 'stop',
                               tool_calls=[{'name': 'other'}] if bad_result == 'tools' else None)
    monkeypatch.setattr(c, 'complete_text', complete)
    with pytest.raises(p.ReaderFirstDirectionRequired) as failure:
        await c._review_reader_first_skeleton(None, None, state=state)
    assert failure.value.payload['status'] == 'unknown'


@pytest.mark.asyncio
async def test_single_send_settings_copy_is_used_for_critic_and_repair(monkeypatch):
    from bestseller.settings import load_settings
    settings = load_settings(env={'BESTSELLER__DATABASE__URL': json.dumps('sqlite:///:memory:')})
    settings.llm.retry.max_attempts_per_class = {'rate_limit': 17, 'timeout': 5}
    settings.llm.critic.n_candidates = 3
    settings.llm.editor.n_candidates = 2
    original = settings.model_dump()
    seen = []
    state = tagged_state()
    async def complete(session, bounded, request):
        seen.append(request)
        assert bounded is not settings
        assert bounded.llm.retry.max_attempts == 1
        assert bounded.llm.retry.rate_limit_max_attempts == 1
        assert bounded.llm.retry.rate_limit_fallback_enabled is False
        assert all(value == 1 for value in bounded.llm.retry.max_attempts_per_class.values())
        role = getattr(bounded.llm, request.logical_role)
        assert role.n_candidates == 1 and role.rate_limit_fallback_model is None
        text = repair_response(request) if request.logical_role == 'editor' else response(request, .4)
        return SimpleNamespace(content=text, llm_run_id=uuid4(), finish_reason='stop', fallback_used=False)
    monkeypatch.setattr(c, 'complete_text', complete)
    with pytest.raises(p.ReaderFirstDirectionRequired):
        await c._review_reader_first_skeleton(None, settings, state=state)
    async def save():
        pass
    await c._repair_reader_first_candidate(None, settings, state=state, checkpoint_save=save)
    assert len(seen) == 2
    assert settings.model_dump() == original


@pytest.mark.asyncio
async def test_repair_unknown_consumes_credit_and_cannot_replay_after_resume(monkeypatch):
    state = tagged_state()
    saved, requests = [], []
    async def save(item):
        saved.append(copy.deepcopy(item))
    async def audit(*args, **kwargs):
        return ({'passed': True, 'quality_passed': True,
                 'audit_identity': c._final_packaging_audit_identity(title=kwargs['title'], synopsis=kwargs['synopsis'])}, {}, {}, '', [])
    async def complete(session, settings, request):
        requests.append(request)
        if request.logical_role == 'editor':
            assert saved[-1]['state']['repair_quotas']['reader_first_skeleton'] == 1
            raise TimeoutError('synthetic unknown repair send')
        return SimpleNamespace(content=response(request, .4), llm_run_id=uuid4(), finish_reason='stop', fallback_used=False)
    monkeypatch.setattr(c, 'complete_text', complete)
    monkeypatch.setattr(c, '_audit_final_packaging_candidate', audit)
    with pytest.raises(p.ReaderFirstDirectionRequired):
        await c._complete_conception_candidate(None, None, state=state, checkpoint_callback=save, checkpoint_input_hash='same')
    resumed = validate_conception_checkpoint(saved[-1], input_hash='same')
    with pytest.raises(p.ReaderFirstDirectionRequired):
        await c._complete_conception_candidate(None, None, state=resumed, checkpoint_callback=save, checkpoint_input_hash='same')
    assert len(requests) == 2
    assert resumed['repair_quotas']['reader_first_skeleton'] == 1


@pytest.mark.asyncio
async def test_tournament_binding_scope_is_reset_even_on_failure(monkeypatch):
    binding = p.new_reader_first_binding(RAW)
    observed = []
    def config():
        observed.append(t._build_raw_idea_pool_messages(genre='现实', sub_genre='家庭', count=2))
        raise RuntimeError('synthetic early stop')
    monkeypatch.setattr(t, 'load_concept_tournament_config', config)
    legacy = t._build_raw_idea_pool_messages(genre='现实', sub_genre='家庭', count=2)
    with pytest.raises(RuntimeError):
        await t.run_concept_tournament(None, None, genre='现实', sub_genre='家庭', chapter_count=5,
                                       reader_first_planning=binding, language='en')
    assert observed[0][0] == p.reader_first_instruction(binding, language='en', stage='concept_tournament') + legacy[0]
    assert t._build_raw_idea_pool_messages(genre='现实', sub_genre='家庭', count=2) == legacy


@pytest.mark.asyncio
async def test_native_failure_after_full_repair_cannot_launch_spine_only_repair(monkeypatch):
    from bestseller.services.concept_contract import ConceptContractError
    state = tagged_state()
    reviews, partial_repairs = [], []
    async def save(_):
        pass
    async def audit(*args, **kwargs):
        reviews.append(kwargs)
        if len(reviews) == 2:
            raise ConceptContractError(['SYNTHETIC_NATIVE_FINAL_GATE_FAILED'])
        return ({'passed': True, 'quality_passed': True,
                 'audit_identity': c._final_packaging_audit_identity(title=kwargs['title'], synopsis=kwargs['synopsis'])}, {}, {}, '', [])
    async def complete(session, settings, request):
        text = repair_response(request) if request.logical_role == 'editor' else response(request, .4)
        return SimpleNamespace(content=text, llm_run_id=uuid4(), finish_reason='stop', fallback_used=False)
    async def partial(*args, **kwargs):
        partial_repairs.append(kwargs)
        raise AssertionError('spine-only repair should not follow the full-bundle repair')
    monkeypatch.setattr(c, '_audit_final_packaging_candidate', audit)
    monkeypatch.setattr(c, 'complete_text', complete)
    monkeypatch.setattr(c, '_conception_structure_diagnosis', lambda _: {'scope': 'story'})
    monkeypatch.setattr(c, '_repair_conception_candidate_structure', partial)
    with pytest.raises(ConceptContractError, match='SYNTHETIC_NATIVE_FINAL_GATE_FAILED'):
        await c._complete_conception_candidate(None, None, state=state, checkpoint_callback=save, checkpoint_input_hash='same')
    assert not partial_repairs
    assert state['repair_quotas']['reader_first_skeleton'] == 1


@pytest.mark.asyncio
async def test_interrupted_repair_keeps_reserved_credit_across_checkpoint(monkeypatch):
    class SimulatedProcessExit(BaseException):
        pass
    state, saved, requests = tagged_state(), [], []
    async def save(item):
        saved.append(copy.deepcopy(item))
    async def audit(*args, **kwargs):
        return ({'passed': True, 'quality_passed': True,
                 'audit_identity': c._final_packaging_audit_identity(title=kwargs['title'], synopsis=kwargs['synopsis'])}, {}, {}, '', [])
    async def complete(session, settings, request):
        requests.append(request)
        if request.logical_role == 'editor':
            raise SimulatedProcessExit()
        return SimpleNamespace(content=response(request, .4), llm_run_id=uuid4(), finish_reason='stop', fallback_used=False)
    monkeypatch.setattr(c, 'complete_text', complete)
    monkeypatch.setattr(c, '_audit_final_packaging_candidate', audit)
    with pytest.raises(SimulatedProcessExit):
        await c._complete_conception_candidate(None, None, state=state, checkpoint_callback=save, checkpoint_input_hash='same')
    resumed = validate_conception_checkpoint(saved[-1], input_hash='same')
    assert resumed['reader_first_skeleton_repair']['status'] == 'reserved'
    assert resumed['repair_quotas']['reader_first_skeleton'] == 1
    with pytest.raises(p.ReaderFirstDirectionRequired):
        await c._complete_conception_candidate(None, None, state=resumed, checkpoint_callback=save, checkpoint_input_hash='same')
    assert len(requests) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize('language', ['zh-CN', 'en'])
@pytest.mark.parametrize('tagged', [False, True])
async def test_spine_polish_propagates_version_and_language_without_touching_source(monkeypatch, language, tagged):
    from bestseller.services.story_spine import SPINE_FIELDS
    spine = {key: 'original-' + key for key in SPINE_FIELDS}
    original = copy.deepcopy(spine)
    ctx = {'genre': '现实', 'sub_genre': '家庭', 'language': language}
    if tagged:
        ctx['reader_first_planning'] = p.new_reader_first_binding(RAW)
    seen, calls = [], []
    def instruction(binding, **kwargs):
        seen.append((binding, kwargs))
        return 'SYNTHETIC-RULE' if binding else ''
    async def complete(session, settings, **kwargs):
        calls.append(kwargs)
        return copy.deepcopy(spine), []
    monkeypatch.setattr(p, 'reader_first_instruction', instruction)
    monkeypatch.setattr(c, '_llm_call_json', complete)
    fixed, _ = await c._polish_story_spine(None, None, spine=spine, violations=['synthetic'], premise=RAW, synopsis=RAW, ctx=ctx)
    assert fixed == original and spine == original
    assert calls[0]['language'] == language
    assert calls[0]['template'] == ('conception_story_spine_polish_reader_first_v1' if tagged else 'conception_story_spine_polish')
    assert seen == [(ctx.get('reader_first_planning'), {'language': language, 'stage': 'conception_story_spine_polish'})]
    assert RAW in calls[0]['user_prompt']
    assert calls[0]['user_prompt'].startswith('SYNTHETIC-RULE') is tagged
