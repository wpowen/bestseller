from bestseller.services import concept_tournament as ct

def _verdict(**extra):
    base = {
        'seed_fidelity': 10,
        'source_protagonist_name': '陆衡',
        'seed_identity_source_quote': '少年陆衡，能看见未成形灵脉',
        'candidate_identity_quote': '矿奴陆衡',
        'identity_relation': 'same',
    }
    base.update(extra)
    return base


def test_high_score_without_evidence_is_not_verified():
    report = ct._validate_engine_seed_fidelity_evidence(
        {'seed_fidelity': 10},
        authoritative_seed='少年陆衡，能看见未成形灵脉。',
        kernel={'protagonist_identity': '矿奴陆衡'},
    )
    assert report['status'] == 'unknown'
    assert report['reason'] == 'source_protagonist_name_missing'


def test_forged_source_quote_is_invalid():
    report = ct._validate_engine_seed_fidelity_evidence(
        _verdict(seed_identity_source_quote='少年周壑，能看见灵脉'),
        authoritative_seed='少年陆衡，能看见未成形灵脉。',
        kernel={'protagonist_identity': '矿奴陆衡'},
    )
    assert report['status'] == 'invalid'
    assert report['reason'] == 'source_identity_quote_not_in_authoritative_seed'


def test_candidate_seed_cannot_substitute_for_card_identity():
    report = ct._validate_engine_seed_fidelity_evidence(
        _verdict(candidate_identity_quote='少年陆衡，能看见未成形灵脉'),
        authoritative_seed='少年陆衡，能看见未成形灵脉。',
        kernel={'protagonist_identity': '矿奴陆衡'},
    )
    assert report['status'] == 'invalid'
    assert report['reason'] == 'candidate_identity_quote_not_in_protagonist_identity'


def test_replaced_real_card_is_invalid_even_with_high_score():
    report = ct._validate_engine_seed_fidelity_evidence(
        _verdict(
            seed_identity_source_quote='边城矿奴陆衡',
            candidate_identity_quote='边城矿奴周壑，越狱废矿凿工',
            identity_relation='replaced',
        ),
        authoritative_seed='边城矿奴陆衡能看见废矿里的灵脉走向。',
        kernel={'protagonist_identity': '边城矿奴周壑，越狱废矿凿工'},
    )
    assert report['status'] == 'invalid'
    assert report['reason'] == 'protagonist_identity_replaced'


def test_anonymous_seed_preserves_identity_without_inventing_name():
    report = ct._validate_engine_seed_fidelity_evidence(
        {
            'seed_fidelity': 10,
            'source_protagonist_name': None,
            'seed_identity_source_quote': '一个矿奴在封矿前凿出一条生路',
            'candidate_identity_quote': '边城矿奴',
            'identity_relation': 'preserved',
        },
        authoritative_seed='一个矿奴在封矿前凿出一条生路。',
        kernel={'protagonist_identity': '边城矿奴'},
    )
    assert report['status'] == 'verified'


def test_named_seed_requires_explicit_name_consistency():
    report = ct._validate_engine_seed_fidelity_evidence(
        _verdict(seed_identity_source_quote='边城矿奴陆衡', candidate_identity_quote='边城矿奴周壑', identity_relation='same'),
        authoritative_seed='边城矿奴陆衡能看见废矿里的灵脉走向。',
        kernel={'protagonist_identity': '边城矿奴周壑'},
    )
    assert report['status'] == 'invalid'
    assert report['reason'] == 'source_protagonist_name_not_in_protagonist_identity'


def test_missing_name_field_is_not_verified_even_for_anonymous_seed():
    report = ct._validate_engine_seed_fidelity_evidence(
        {
            'seed_identity_source_quote': '一个矿奴在封矿前凿出一条生路',
            'candidate_identity_quote': '边城矿奴',
            'identity_relation': 'preserved',
        },
        authoritative_seed='一个矿奴在封矿前凿出一条生路。',
        kernel={'protagonist_identity': '边城矿奴'},
    )
    assert report['status'] == 'unknown'
    assert report['reason'] == 'source_protagonist_name_missing'


def test_name_ban_yields_to_explicit_user_name():
    _system, prompt = ct._build_engine_kernel_messages(
        genre='玄幻', sub_genre='东方玄幻', lane='user_original#0',
        chapter_count=20, seed_concept='少年陆衡能看见未成形灵脉。',
        authoritative_seed_concept='少年陆衡能看见未成形灵脉。',
    )
    assert '用户上位创意中已经明确写出的主角姓名属于冻结事实' in prompt
    assert '不得为了命名去重改写用户主角' in prompt


def test_batch_prompt_requires_structured_identity_evidence():
    _system, prompt = ct._build_engine_batch_judge_messages(
        cards=[('user_original#0', '少年陆衡能看见未成形灵脉。', {'protagonist_identity': '矿奴陆衡'})],
        genre='玄幻', sub_genre='东方玄幻', chapter_count=20,
        authoritative_seed_concept='少年陆衡能看见未成形灵脉。',
    )
    assert 'seed_identity_source_quote' in prompt
    assert 'candidate_identity_quote' in prompt
    assert 'identity_relation' in prompt
    assert '禁止引用 candidate_seed' in prompt


def test_explicit_blacklisted_name_remains_authoritative_in_kernel_prompt():
    _system, prompt = ct._build_engine_kernel_messages(
        genre='玄幻', sub_genre='东方玄幻', lane='user_original#0',
        chapter_count=20, seed_concept='少年陆沉能看见未成形灵脉。',
        authoritative_seed_concept='少年陆沉能看见未成形灵脉。',
    )
    assert '少年陆沉能看见未成形灵脉' in prompt
    assert '不得为了命名去重改写用户主角' in prompt

import json

import pytest
from types import SimpleNamespace


SEED = '边城矿奴陆衡能看见废矿里的灵脉走向；宗门封矿时他带工友凿出一条生路。'
KERNEL = {
    'protagonist_identity': '边城矿奴陆衡',
    'protagonist_private_desire': '带工友活着离开矿坑',
    'protagonist_flaw': '只相信自己看见的路线',
    'core_abnormality': '能看见废矿里尚未成形的灵脉走向',
    'current_goal': '在封矿前凿出唯一生路',
    'effective_resistance': '宗门要封矿活埋工友',
    'failure_cost': '工友会被活埋',
    'success_cost': '烧毁卖身契后成为宗门追捕对象',
    'irreversible_change': '工友跟随陆衡离开矿坑并卷入古战场争夺',
    'reader_promise': '看底层矿奴靠判断带人破局',
    'difference_point': '灵脉尚未成形时就能被看见',
    'deformable_loop': '辨认灵脉、选择路线、应对宗门封堵',
    'opening_crisis': '宗门要封矿活埋工友',
    'emotional_promise': '陆衡带工友抢出生路',
    'scene_seeds': ['看见断脉', '抢挖生路', '应对封矿', '保护工友', '进入古战场'],
    'post_reveal_scene_seeds': ['各宗争夺', '路线反制', '公开选择'],
    'expansion_axes': ['矿脉路线', '工友队伍', '宗门争夺'],
    'opposing_ecology': ['封矿宗门', '争夺古战场入口的各宗'],
}
ENGINE_AXES = (
    'seed_fidelity', 'freshness', 'click_seed', 'action_conflict',
    'reader_promise', 'character_choice', 'scene_generation',
    'promise_survival', 'deformable_loop', 'post_reveal_engine',
    'genre_fidelity',
)


def _engine_verdict(*, identity='边城矿奴陆衡', relation='same', missing=()):
    payload = {axis: 9 for axis in ENGINE_AXES}
    payload.update({
        'seed_identity_source_quote': '边城矿奴陆衡',
        'candidate_identity_quote': identity,
        'source_protagonist_name': '陆衡',
        'identity_relation': relation,
    })
    for axis in missing:
        payload.pop(axis, None)
    return payload


def _runner_config():
    return {
        'enabled': True, 'n_candidates': 2,
        'candidate_prompt_mode': 'engine_first',
        'raw_idea_pool_multiplier': 1,
        'raw_idea_generation_batch_size': 2,
        'raw_idea_pool_topup_calls': 0,
        'story_layer_judge_enabled': False,
        'premise_card_min_survivors': 0,
        'engine_judge_floor': 7,
        'seriality_finalist_count': 1,
    }


def _runner_generator(kernel=None):
    async def generate(system, user):
        del system
        if '"ideas"' in user:
            return json.dumps({'ideas': [
                {'lane': '用户原始', 'seed': SEED, 'graft': '矿脉×矿坑', 'opening': '封矿',
                 'why_it_keeps_moving': '争夺生路', 'future_situations': ['挖矿', '逃亡', '争夺']},
                {'lane': '职业处境', 'seed': '陆衡带工友凿出矿坑生路。', 'graft': '矿脉×矿坑', 'opening': '封矿',
                 'why_it_keeps_moving': '争夺生路', 'future_situations': ['挖矿', '逃亡', '争夺']},
            ]}, ensure_ascii=False), 'engine-seed-run'
        if 'hooks' in user or '项目卡' in user:
            return '{}', 'hook-run'
        return json.dumps(kernel or KERNEL, ensure_ascii=False), 'kernel-run'
    return generate


def _regular_judge():
    async def judge(system, user):
        del system
        if 'verdicts' in user:
            return json.dumps({'verdicts': []}, ensure_ascii=False), 'unexpected-batch'
        if 'renewability' in user or 'repeatable' in user:
            return json.dumps({axis: 9 for axis in ('renewability', 'escalation', 'anti_reset', 'coherence', 'promise_survival', 'unit_density')}, ensure_ascii=False), 'seriality-run'
        return json.dumps({axis: 9 for axis in ('freshness', 'click', 'predictable', 'character_logic', 'mechanism_causality', 'genre_fidelity', 'plain_language', 'story_motion', 'protagonist_agency')}, ensure_ascii=False), 'concept-judge-run'
    return judge


async def _run_card_runner(*, batch_payload, fallback=False, retry_payload=None, kernel=None):
    premise_calls = []
    generator = _runner_generator(kernel)
    regular = _regular_judge()
    async def premise(system, user):
        del system
        premise_calls.append(user)
        if len(premise_calls) == 1:
            if fallback:
                return '{}', 'batch-run'
            return json.dumps(batch_payload, ensure_ascii=False), 'batch-run'
        if retry_payload is not None and len(premise_calls) >= 2:
            return json.dumps(retry_payload, ensure_ascii=False), 'retry-run'
        # Fallback single-card review calls use the same complete verdict.
        return json.dumps(batch_payload.get('verdicts', [{}])[0], ensure_ascii=False), 'single-run'
    result = await ct.run_concept_tournament(
        None, SimpleNamespace(pipeline=SimpleNamespace(concept_tournament_batch_concurrency=1)),
        genre='东方玄幻', sub_genre='东方玄幻', chapter_count=20,
        seed_concept=SEED, authoritative_seed=True, config=_runner_config(),
        generator=generator, judge=regular, seriality_judge=regular,
        premise_judge=premise,
    )
    return result, premise_calls


@pytest.mark.asyncio
async def test_runner_batch_rejects_high_score_renamed_card_and_persists_receipt():
    result, calls = await _run_card_runner(batch_payload={
        'verdicts': [
            {'index': i, **_engine_verdict(identity='边城矿奴周壑', relation='same')}
            for i in range(2)
        ]
    }, kernel={**KERNEL, 'protagonist_identity': '边城矿奴周壑'})
    assert result.winner is None
    assert len(calls) == 1
    reviews = [item.get('premise_review') for item in result.premise_cards]
    assert all(review and review['run_id'] == 'batch-run' for review in reviews)
    assert all(review['review_mode'] == 'batch' for review in reviews)
    assert len(reviews) == 2
    assert all(review['verdict']['identity_relation'] == 'same' for review in reviews)
    assert all('seed_fidelity_evidence' in review for review in reviews)
    assert any('seed_fidelity_evidence' in item.get('failed_axes', []) for item in result.engine_rejections)


@pytest.mark.asyncio
async def test_runner_single_fallback_rejects_unknown_contract_and_records_single_receipt():
    result, calls = await _run_card_runner(
        batch_payload={}, fallback=True,
    )
    assert result.winner is None
    assert len(calls) >= 3
    reviews = [item.get('premise_review') for item in result.premise_cards]
    assert all(review and review['review_mode'] == 'single_fallback' for review in reviews)
    assert all(review['run_id'] == 'single-run' for review in reviews)
    assert all(review['seed_fidelity_evidence']['status'] == 'unknown' for review in reviews)
    assert all('seed_fidelity_evidence' in item.get('failed_axes', []) for item in result.engine_rejections)


@pytest.mark.asyncio
async def test_runner_batch_missing_axis_retry_uses_retry_receipt_and_keeps_original_batch_verdict():
    incomplete = _engine_verdict(missing=('genre_fidelity',))
    retry = _engine_verdict()
    result, calls = await _run_card_runner(
        batch_payload={'verdicts': [
            {'index': 0, **incomplete}, {'index': 1, **incomplete},
        ]},
        retry_payload=retry,
    )
    assert result.winner is None
    assert len(calls) == 3
    reviews = [item.get('premise_review') for item in result.premise_cards]
    assert all(review and review['review_mode'] == 'batch_retry' for review in reviews)
    assert all(review['run_id'] == 'retry-run' for review in reviews)
    assert all(review['batch_run_id'] == 'batch-run' for review in reviews)
    assert len(reviews) == 2
    assert all(review['seed_fidelity_evidence']['status'] == 'verified' for review in reviews)
    assert all('genre_fidelity' not in review['batch_verdict'] for review in reviews)


def _source_repair_verdict(*, seed_fidelity=6, identity='边城矿奴陆衡', relation='same', fake_restoration=False):
    verdict = {
        **_engine_verdict(identity=identity, relation=relation),
        'seed_fidelity': seed_fidelity,
        'source_change_type': 'supplement',
    }
    if seed_fidelity >= 9:
        verdict['source_restoration_evidence'] = [{
            'source_quote': '宗门封矿时他带工友凿出一条生路',
            'candidate_field': 'opening_crisis',
            'candidate_quote': (
                '伪造的引文' if fake_restoration
                else '宗门封矿时他带工友凿出一条生路'
            ),
        }]
    return verdict


async def _run_source_repair_batch(*, repair_passes=True, failed_axes=None, fake_restoration=False, monkeypatch=None):
    """Run the batch branch with source repair prompts isolated by call shape."""
    calls = []
    base_generator = _runner_generator(KERNEL)

    architecture_calls = []
    if monkeypatch is not None:
        def audit(card, **kwargs):
            architecture_calls.append(dict(card))
            return {'passed': True, 'architecture': {}}
        monkeypatch.setattr(ct, '_story_architecture_audit', audit)

    async def generator(system, user):
        if '仅返回诊断点名字段的完整新值' in user:
            return json.dumps(
                {'opening_crisis': '宗门封矿时他带工友凿出一条生路'},
                ensure_ascii=False,
            ), 'patch-run'
        return await base_generator(system, user)

    regular = _regular_judge()
    initial = _source_repair_verdict()
    if failed_axes:
        initial.update({axis: 6 for axis in failed_axes})

    async def premise(system, user):
        del system
        calls.append(user)
        if len(calls) == 1:
            return json.dumps({'verdicts': [
                {'index': 0, **initial},
                {'index': 1, **initial},
            ]}, ensure_ascii=False), 'batch-run'
        if '只判断能否局部补回遗漏' in user:
            assert '必须且只能包含source_quote和candidate_field两个键' in user
            assert '不得添加解释或关系字段' in user
            diagnosis = {
                'decision': 'missing_only',
                'missing_facts': [{
                    'candidate_field': 'opening_crisis',
                    'source_quote': '宗门封矿时他带工友凿出一条生路',
                }],
            }
            return json.dumps(diagnosis, ensure_ascii=False), 'diagnosis-run'
        if repair_passes and '十一轴0-10' in user:
            return json.dumps(_source_repair_verdict(seed_fidelity=9, fake_restoration=fake_restoration), ensure_ascii=False), 'rejudge-run'
        if '十一轴0-10' in user:
            return json.dumps(_source_repair_verdict(seed_fidelity=6), ensure_ascii=False), 'rejudge-run'
        return json.dumps(initial, ensure_ascii=False), 'unexpected-run'

    config = _runner_config()
    if monkeypatch is not None:
        config['story_architecture_contract_enabled'] = True
    result = await ct.run_concept_tournament(
        None, SimpleNamespace(pipeline=SimpleNamespace(concept_tournament_batch_concurrency=1)),
        genre='东方玄幻', sub_genre='东方玄幻', chapter_count=20,
        seed_concept=SEED, authoritative_seed=True, config=config,
        generator=generator, judge=regular, seriality_judge=regular,
        premise_judge=premise,
    )
    return result, calls, architecture_calls


@pytest.mark.asyncio
async def test_runner_source_repair_promotes_edited_card_and_replaces_review_receipt(monkeypatch):
    result, calls, architecture_calls = await _run_source_repair_batch(monkeypatch=monkeypatch)

    assert calls[0].startswith('题材=东方玄幻（东方玄幻）')
    assert len(calls) == 3
    assert len([call for call in calls if '只判断能否局部补回遗漏' in call]) == 1
    repaired = result.premise_cards[0]
    assert repaired['card']['opening_crisis'] == '宗门封矿时他带工友凿出一条生路'
    assert repaired['premise_review']['review_mode'] == 'source_rejudge'
    assert repaired['premise_review']['run_id'] == 'rejudge-run'
    assert repaired['premise_review']['verdict']['seed_fidelity'] == 9
    assert repaired['source_revision']['status'] == 'repaired'
    assert repaired['source_revision']['original_verdict']['seed_fidelity'] == 6
    assert repaired['source_revision']['final_verdict']['seed_fidelity'] == 9
    assert repaired['source_revision']['repaired_architecture_audit']['passed'] is True
    assert architecture_calls


@pytest.mark.asyncio
async def test_runner_source_repair_failure_keeps_original_card_and_rejects_candidate():
    result, calls, _ = await _run_source_repair_batch(repair_passes=False)

    repaired = result.premise_cards[0]
    assert len([call for call in calls if '只判断能否局部补回遗漏' in call]) == 1
    assert repaired['card']['opening_crisis'] == KERNEL['opening_crisis']
    assert repaired['premise_review']['review_mode'] == 'batch'
    assert repaired['source_revision']['status'] == 'rejected'
    assert repaired['source_revision']['reason'] == 'full_rejudge_failed'
    assert any(item.get('failed_axes') == ['seed_fidelity'] for item in result.engine_rejections)


@pytest.mark.asyncio
async def test_runner_source_repair_is_attempted_once_for_a_batch():
    result, calls, _ = await _run_source_repair_batch()

    assert len([call for call in calls if '只判断能否局部补回遗漏' in call]) == 1
    assert sum(item.get('source_revision', {}).get('status') == 'repaired' for item in result.premise_cards) == 1
    assert sum('source_revision' in item for item in result.premise_cards) == 1


@pytest.mark.asyncio
async def test_runner_source_repair_does_not_run_for_multiple_failed_axes():
    result, calls, _ = await _run_source_repair_batch(failed_axes=('seed_fidelity', 'genre_fidelity'))

    assert not any('只判断能否局部补回遗漏' in call for call in calls)
    assert not any('source_revision' in item for item in result.premise_cards)
    assert all(item.get('failed_axes') == ['seed_fidelity', 'genre_fidelity'] for item in result.engine_rejections)


@pytest.mark.asyncio
async def test_runner_source_repair_rejects_forged_restoration_quote(monkeypatch):
    result, calls, _ = await _run_source_repair_batch(
        fake_restoration=True, monkeypatch=monkeypatch,
    )

    assert len([call for call in calls if '只判断能否局部补回遗漏' in call]) == 1
    repaired = result.premise_cards[0]
    assert repaired['source_revision']['status'] == 'rejected'
    assert repaired['source_revision']['reason'] == 'full_rejudge_failed'
    assert repaired['card']['opening_crisis'] == KERNEL['opening_crisis']
