import pytest
import json
from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

from bestseller.services import planner
from bestseller.services.conception_snapshots import snapshot_hash

pytestmark = pytest.mark.unit


def baseline(gender='unknown', source='许宁走进院子。他把药箱放下。'):
    return {
        'protagonist': {'name': '许宁', 'gender': gender,
                        'metadata': {'identity_source_text': source}},
        'supporting_cast': [{'name': '陈明', 'gender': 'male', 'pronoun_set_zh': '他', 'pronoun_set_en': 'he/him'}],
    }


def test_missing_protagonist_identity_triggers_expansion_with_existing_roster():
    assert planner._source_bound_cast_needs_expansion(baseline())


def test_identity_completion_prompt_is_small_and_does_not_request_social_cast():
    class Project:
        language = 'zh-CN'
        metadata_json = {}

    system, user = planner._source_bound_identity_completion_prompts(
        Project(), {'title': '测试'}, {}, baseline()
    )
    assert 'identity_quote_candidates_not_attribution' in user
    assert 'supporting_cast' not in user
    assert '只补齐当前 unknown 的已批准主角身份' in user
    assert '【源约束身份补全】' in system


def test_identity_completion_boundary_accepts_only_verified_candidate_shape():
    source = baseline()
    option = planner._source_bound_identity_quote_options(source['protagonist'])[0]
    candidate = {
        'protagonist': {
            'name': '许宁', 'gender': 'male',
            'identity_evidence_quote_id': option['quote_id'],
        },
    }
    planner._validate_source_bound_identity_completion(candidate, fallback=source)
    with pytest.raises(ValueError, match='may only return protagonist'):
        planner._validate_source_bound_identity_completion(
            {**candidate, 'supporting_cast': []}, fallback=source
        )
    with pytest.raises(ValueError, match='may only return identity fields'):
        planner._validate_source_bound_identity_completion(
            {**candidate, 'protagonist': {**candidate['protagonist'], 'goal': '新增目标'}},
            fallback=source,
        )


def test_unknown_identity_with_single_valid_quote_becomes_review_only_hypothesis():
    source = baseline()
    option = planner._source_bound_identity_quote_options(source['protagonist'])[0]
    pending = planner._source_bound_pending_identity_candidate(
        source['protagonist'],
        {'name': '许宁', 'gender': 'unknown', 'identity_evidence_quote_id': option['quote_id']},
    )
    assert pending is not None
    assert pending['gender'] == 'male'
    assert pending['identity_evidence'] == option['quote']


def test_source_bound_structured_output_accepts_legacy_text_json():
    completion = SimpleNamespace(content='{"protagonist":{"name":"许宁"}}', tool_calls=None)
    payload, channel = planner._extract_source_bound_structured_payload(
        completion, 'cast_spec_source_bound_identity_completion'
    )
    assert payload['protagonist']['name'] == '许宁'
    assert channel == 'text_json'


def test_source_bound_structured_output_accepts_one_target_tool_call():
    completion = SimpleNamespace(
        content='',
        tool_calls=[{
            'type': 'function',
            'function': {
                'name': 'source_bound_identity_completion',
                'arguments': json.dumps({'protagonist': {'name': '许宁', 'gender': 'unknown', 'identity_evidence_quote_id': 'iq_x'}}),
            },
        }],
    )
    payload, channel = planner._extract_source_bound_structured_payload(
        completion, 'cast_spec_source_bound_identity_completion'
    )
    assert payload['protagonist']['gender'] == 'unknown'
    assert channel == 'tool_call'


def test_source_bound_cast_tool_schema_keeps_optional_person_fields_optional():
    tool = planner._source_bound_structured_output_tool('cast_spec_source_bound_expansion')
    assert tool['function']['name'] == 'source_bound_cast_expansion'
    person = tool['function']['parameters']['properties']['supporting_cast']['items']
    assert person['required'] == ['name']
    assert person['additionalProperties'] is True


@pytest.mark.parametrize('calls', [
    [{'type': 'function', 'function': {'name': 'wrong_function', 'arguments': '{}'}}],
    [
        {'type': 'function', 'function': {'name': 'source_bound_identity_completion', 'arguments': '{}'}},
        {'type': 'function', 'function': {'name': 'source_bound_identity_completion', 'arguments': '{}'}},
    ],
    [{'type': 'function', 'function': {'name': 'source_bound_identity_completion', 'arguments': '{bad'}}],
])
def test_source_bound_structured_output_rejects_wrong_multiple_or_invalid_tool_calls(calls):
    with pytest.raises((ValueError, json.JSONDecodeError)):
        planner._extract_source_bound_structured_payload(
            SimpleNamespace(content='', tool_calls=calls),
            'cast_spec_source_bound_identity_completion',
        )


@pytest.mark.parametrize('source', [
    '许宁走进院子。',
    '许宁走进院子。他和她一起放下药箱。',
])
def test_unknown_identity_without_unique_cue_cannot_become_pending_candidate(source):
    base = baseline(source=source)
    options = planner._source_bound_identity_quote_options(base['protagonist'])
    candidate = {'name': '许宁', 'gender': 'unknown'}
    if options:
        candidate['identity_evidence_quote_id'] = options[0]['quote_id']
    assert planner._source_bound_pending_identity_candidate(base['protagonist'], candidate) is None


@pytest.mark.asyncio
async def test_identity_phase_sends_pending_gender_to_independent_review(monkeypatch):
    source = baseline()
    content = {"cast_spec": deepcopy(source)}
    source_id, source_hash = str(uuid4()), snapshot_hash(content)
    project = SimpleNamespace(language='zh-CN', metadata_json={
        'conception_approved': True,
        'creation_canonical_snapshot_id': source_id,
        'creation_canonical_snapshot_hash': source_hash,
        'book_design_snapshot_status': 'locked',
        'book_design_snapshot': {'snapshot_id': source_id, 'source_hash': source_hash,
            'protagonist': {'name': source['protagonist']['name']}},
    })
    project._source_bound_planning_canonical = {
        'id': source_id, 'content_hash': source_hash, 'content': content,
    }
    assert planner._source_bound_cast_enabled(project)
    canon = planner._source_bound_canonical_protagonist(project, '')
    assert canon['name'] == '许宁' and canon['gender'] == 'unknown'
    option = planner._source_bound_identity_quote_options(source['protagonist'])[0]
    seen = {}

    async def generate(*args, **kwargs):
        kwargs['validator']({'protagonist': {
            'name': '许宁', 'gender': 'unknown',
            'identity_evidence_quote_id': option['quote_id'],
        }})
        return ({'protagonist': {
            'name': '许宁', 'gender': 'unknown',
            'identity_evidence_quote_id': option['quote_id'],
        }}, uuid4())

    async def review(*args, **kwargs):
        seen['gender'] = kwargs['cast_derived']['protagonist']['gender']
        return {
            'draft_eligible': True, 'evidence_verified': True,
            'llm_run_id': str(uuid4()), 'raw_response_hash': 'hash',
            'authority_snapshot_hash': 'authority', 'candidate_hash': 'candidate',
            'authority_snapshot_hash_echo': 'authority', 'candidate_hash_echo': 'candidate',
            'changed_fields': ['protagonist'], 'source_evidence': [{}],
            'candidate_evidence': [{}], 'core_preserved': True, 'core_conflicts': [],
            'protagonist_identity_supported': True,
        }

    monkeypatch.setattr(planner, '_generate_structured_artifact', generate)
    monkeypatch.setattr(planner, '_review_source_bound_expansion', review)
    completed, _ = await planner._complete_source_bound_cast_identity(
        None, SimpleNamespace(), project=project,
        book_spec={}, world_spec={}, cast_baseline=source, workflow_run_id=uuid4(),
    )
    assert seen['gender'] == 'male'
    assert completed['protagonist']['gender'] == 'male'


@pytest.mark.asyncio
async def test_identity_only_review_reaches_provider_when_cast_assets_are_unavailable(monkeypatch):
    calls = []

    async def fake_complete(*args, **kwargs):
        calls.append(args[2] if len(args) > 2 else kwargs.get('request'))
        return SimpleNamespace(
            content='{}', provider='fixture', model_name='fixture', finish_reason='stop',
            llm_run_id=uuid4(), fallback_used=False, input_tokens=1, output_tokens=1,
        )

    monkeypatch.setattr(planner, 'complete_text', fake_complete)
    cast_base = baseline()
    cast_base['_meta'] = {'source_bound_expansion': 'unavailable'}
    cast_candidate = {
        **cast_base,
        'protagonist': {
            **cast_base['protagonist'], 'gender': 'male',
            'pronoun_set_zh': '他', 'pronoun_set_en': 'he/him',
            'identity_evidence': '他把药箱放下。',
        },
    }
    report = await planner._review_source_bound_expansion(
        None,
        SimpleNamespace(llm=SimpleNamespace(mock=False)),
        project=SimpleNamespace(language='zh-CN', metadata_json={}, id=uuid4()),
        book_spec={}, world_baseline={}, cast_baseline=cast_base,
        world_derived={}, cast_derived=cast_candidate, workflow_run_id=uuid4(),
        identity_only=True,
    )
    assert calls, 'identity-only review must reach the independent reviewer'
    assert 'FINAL IDENTITY SCOPE' in calls[0].user_prompt
    assert 'IDENTITY RESPONSE CONTRACT' in calls[0].user_prompt
    assert 'protagonist_identity_supported' in calls[0].user_prompt
    assert '许宁' in calls[0].user_prompt
    derived_payload = calls[0].user_prompt.split('derived_candidates=', 1)[1].split(
        '\nidentity_review_context=', 1
    )[0]
    assert 'supporting_cast' not in derived_payload
    assert 'antagonist' not in derived_payload
    assert report['reason'] != 'sparse_asset_expansion_unavailable'


@pytest.mark.asyncio
async def test_identity_only_review_rejects_non_identity_protagonist_delta(monkeypatch):
    async def unexpected(*args, **kwargs):
        pytest.fail('non-identity identity-only delta must be blocked before provider review')

    monkeypatch.setattr(planner, 'complete_text', unexpected)
    cast_base = baseline()
    cast_base['_meta'] = {'source_bound_expansion': 'unavailable'}
    cast_candidate = {
        **cast_base,
        'protagonist': {
            **cast_base['protagonist'], 'gender': 'male',
            'pronoun_set_zh': '他', 'pronoun_set_en': 'he/him',
            'identity_evidence': '他把药箱放下。', 'goal': '不应在身份阶段修改',
        },
    }
    report = await planner._review_source_bound_expansion(
        None,
        SimpleNamespace(llm=SimpleNamespace(mock=False)),
        project=SimpleNamespace(language='zh-CN', metadata_json={}, id=uuid4()),
        book_spec={}, world_baseline={}, cast_baseline=cast_base,
        world_derived={}, cast_derived=cast_candidate, workflow_run_id=uuid4(),
        identity_only=True,
    )
    assert report['reason'] == 'identity_review_scope_violation'


def test_social_cast_gap_cannot_pass_with_identity_only_response():
    source = {
        'protagonist': {
            'name': '许宁', 'gender': 'unknown',
            'metadata': {'identity_source_text': '许宁走进院子。他把药箱放下。'},
        },
        'antagonist': None,
        'supporting_cast': [],
    }
    with pytest.raises(ValueError, match='at least one named antagonist or supporting character'):
        planner._validate_source_bound_cast_expansion_payload(
            {
                'protagonist': {
                    'name': '许宁', 'gender': 'male',
                    'identity_evidence': '他把药箱放下。',
                },
                'antagonist': None,
                'supporting_cast': [],
                'conflict_map': [],
            },
            fallback=source,
        )


def test_evidence_backed_completion_preserves_name_and_derives_pronouns():
    source = baseline()
    candidate = {'protagonist': {'name': '许宁', 'gender': 'male', 'identity_evidence': '他把药箱放下。'}}
    result = planner._rebind_source_bound_cast_expansion(source, candidate)
    hero = result['protagonist']
    assert hero['gender'] == 'male'
    assert hero['pronoun_set_zh'] == '他'
    assert hero['pronoun_set_en'] == 'he/him'
    assert 'protagonist' in result['_meta']['source_bound_expansion_fields']
    planner._validate_source_bound_cast_expansion_payload(candidate, fallback=source)


@pytest.mark.parametrize('candidate', [
    {'name': '许宁', 'gender': 'male'},
    {'name': '许宁', 'gender': 'male', 'identity_evidence': '他是男性。'},
    {'name': '许宁', 'gender': 'male', 'identity_evidence': '许宁走进院子。'},
    {'name': '其他人', 'gender': 'male', 'identity_evidence': '他把药箱放下。'},
])
def test_unsupported_or_wrong_person_completion_fails_at_structured_boundary(candidate):
    source = baseline()
    assert planner._rebind_source_bound_cast_expansion(source, {'protagonist': candidate})['protagonist']['gender'] == 'unknown'
    with pytest.raises(ValueError, match='merged protagonist'):
        planner._validate_source_bound_cast_expansion_payload({'protagonist': candidate}, fallback=source)


def test_explicit_female_lock_cannot_be_overwritten_by_male_generation():
    source = baseline('female')
    result = planner._rebind_source_bound_cast_expansion(source, {
        'protagonist': {'name': '许宁', 'gender': 'male', 'pronoun_set_zh': '他', 'identity_evidence': '他把药箱放下。'},
    })
    assert result['protagonist']['gender'] == 'female'
    assert result['protagonist']['pronoun_set_zh'] == '她'
    assert result['protagonist']['pronoun_set_en'] == 'she/her'


def test_source_compiler_does_not_use_name_gender_guess():
    assert planner._infer_premise_character_gender('李雪经营一间店铺。', '李雪', allow_name_guess=False) == 'unknown'


def test_retry_feedback_identifies_the_evidence_failure_not_a_missing_gender():
    source = baseline(source='许宁四十岁，经营药店。他把药箱放下。')
    with pytest.raises(ValueError) as error:
        planner._validate_source_bound_cast_expansion_payload({
            'protagonist': {'name': '许宁', 'gender': 'male', 'identity_evidence': '许宁四十岁，经营药店。'},
        }, fallback=source)
    message = str(error.value)
    assert 'identity_evidence has no gender/pronoun cue' in message
    assert 'name, age and occupation are not gender evidence' in message
    assert 'Correct identity_evidence, not merely the gender field' in message
    assert 'another character is invalid' in message


def test_missing_protagonist_is_not_reported_as_a_name_mismatch():
    source = baseline()
    with pytest.raises(ValueError) as error:
        planner._validate_source_bound_cast_expansion_payload(
            {
                'supporting_cast': [{
                    'name': '周执', 'gender': 'male',
                    'pronoun_set_zh': '他', 'pronoun_set_en': 'he/him',
                    'motivation_now': '抢先登记灵田簿',
                    'relationship_to_protagonist': '查田执行人',
                }],
            },
            fallback={
                'protagonist': source['protagonist'],
                'supporting_cast': [],
            },
        )
    message = str(error.value)
    assert 'identity completion is missing' in message
    assert 'canonical baseline name' in message
    assert 'name must equal' not in message


def test_source_bound_retry_contract_keeps_unknown_identity_and_social_gap_together():
    contract = planner._source_bound_cast_retry_contract({
        'protagonist': {'name': '许宁', 'gender': 'unknown'},
        'antagonist': None,
        'supporting_cast': [],
    })
    assert 'canonical protagonist name' in contract
    assert 'verifiable identity quote' in contract
    assert 'at least one named antagonist or supporting character' in contract
    assert 'same JSON response' in contract


def test_source_bound_retry_contract_is_not_added_for_locked_identity():
    assert planner._source_bound_cast_retry_contract({
        'protagonist': {'name': '许宁', 'gender': 'female'},
        'supporting_cast': [],
    }) == ''


def test_locked_baseline_identity_is_inherited_when_candidate_only_adds_social_cast():
    source = baseline('female')
    candidate = {
        'supporting_cast': [{
            'name': '周执', 'gender': 'male',
            'pronoun_set_zh': '他', 'pronoun_set_en': 'he/him',
            'motivation_now': '抢先登记灵田簿',
            'relationship_to_protagonist': '查田执行人',
        }],
    }
    planner._validate_source_bound_cast_expansion_payload(candidate, fallback=source)
    rebound = planner._rebind_source_bound_cast_expansion(source, candidate)
    assert rebound['protagonist']['name'] == '许宁'
    assert rebound['protagonist']['gender'] == 'female'
    assert any(item['name'] == '周执' for item in rebound['supporting_cast'])


def test_social_role_aliases_are_canonicalized_without_bypassing_content_gate():
    source = {
        'protagonist': {
            'name': '许宁', 'gender': 'female',
            'pronoun_set_zh': '她', 'pronoun_set_en': 'she/her',
        },
        'antagonist': None,
        'supporting_cast': [],
    }
    candidate = {
        'supporting_cast': [{
            'name': '周执', 'gender': 'male',
            'pronoun_set_zh': '他', 'pronoun_set_en': 'he/him',
            'current_motive': '抢先登记灵田簿',
            'relation_to_protagonist': '查田执行人',
        }],
    }
    planner._validate_source_bound_cast_expansion_payload(candidate, fallback=source)
    rebound = planner._rebind_source_bound_cast_expansion(source, candidate)
    member = rebound['supporting_cast'][0]
    assert member['motivation'] == '抢先登记灵田簿'
    assert member['relationship_to_protagonist'] == '查田执行人'


def test_social_role_aliases_without_content_still_fail():
    source = {
        'protagonist': {'name': '许宁', 'gender': 'female'},
        'antagonist': None,
        'supporting_cast': [],
    }
    with pytest.raises(ValueError, match='present-tense goal'):
        planner._validate_source_bound_cast_expansion_payload(
            {
                'supporting_cast': [{
                    'name': '周执', 'gender': 'male',
                    'pronoun_set_zh': '他', 'pronoun_set_en': 'he/him',
                    'current_motive': '', 'relation_to_protagonist': '',
                }],
            },
            fallback=source,
        )


def test_retry_feedback_explains_person_fields_must_be_nested():
    with pytest.raises(ValueError) as error:
        planner._validate_source_bound_cast_expansion_payload({'decision_policy': {}})
    assert 'Top-level keys must be protagonist, antagonist, supporting_cast, conflict_map only' in str(error.value)
    assert 'inside the relevant named character object' in str(error.value)


def test_quote_id_resolves_exact_source_without_guessing_gender():
    source = baseline()
    option = planner._source_bound_identity_quote_options(source['protagonist'])[0]
    candidate = {'protagonist': {'name': '许宁', 'gender': 'male', 'identity_evidence_quote_id': option['quote_id']}}
    result = planner._rebind_source_bound_cast_expansion(source, candidate)
    assert result['protagonist']['identity_evidence'] == option['quote']
    assert result['protagonist']['gender'] == 'male'
    planner._validate_source_bound_cast_expansion_payload(candidate, fallback=source)


@pytest.mark.parametrize('extra', [
    {'identity_evidence_quote_id': 'unknown'},
    {'identity_evidence': '许宁走进院子。'},
])
def test_invalid_quote_id_or_conflicting_literal_does_not_complete_identity(extra):
    source = baseline()
    option = planner._source_bound_identity_quote_options(source['protagonist'])[0]
    hero = {'name': '许宁', 'gender': 'male', 'identity_evidence_quote_id': option['quote_id'], **extra}
    with pytest.raises(ValueError, match='quote_id'):
        planner._validate_source_bound_cast_expansion_payload({'protagonist': hero}, fallback=source)


def test_pronoun_objects_use_existing_normalizer_and_persist_strings():
    candidate = {'supporting_cast': [{
        'name': '陆明', 'gender': 'male',
        'pronoun_set_zh': {'subject': '他', 'object': '他'},
        'pronoun_set_en': {'subject': 'he', 'object': 'him'},
    }]}
    planner._validate_source_bound_cast_expansion_payload(candidate)
    result = planner._rebind_source_bound_cast_expansion(baseline('male'), candidate)
    member = next(item for item in result['supporting_cast'] if item['name'] == '陆明')
    assert member['pronoun_set_zh'] == '他'
    assert member['pronoun_set_en'] == 'he/him'


@pytest.mark.parametrize(
    ('gender', 'pronoun_set_zh', 'pronoun_set_en', 'expected'),
    [
        ('male', '他', 'he/him/his', 'he/him'),
        ('female', '她', 'she/her/her', 'she/her'),
    ],
)
def test_compatible_possessive_english_pronouns_are_canonicalized(
    gender, pronoun_set_zh, pronoun_set_en, expected
):
    candidate = {
        'supporting_cast': [{
            'name': '具名角色',
            'gender': gender,
            'pronoun_set_zh': pronoun_set_zh,
            'pronoun_set_en': pronoun_set_en,
        }]
    }
    planner._validate_source_bound_cast_expansion_payload(candidate)
    rebound = planner._rebind_source_bound_cast_expansion(baseline('male'), candidate)
    member = next(item for item in rebound['supporting_cast'] if item['name'] == '具名角色')
    assert member['pronoun_set_en'] == expected


def test_pronoun_object_conflict_is_still_rejected():
    with pytest.raises(ValueError, match='must use pronouns'):
        planner._validate_source_bound_cast_expansion_payload({'supporting_cast': [{
            'name': '陆明', 'gender': 'male',
            'pronoun_set_zh': {'subject': '她'},
            'pronoun_set_en': {'subject': 'she', 'object': 'her'},
        }]})


def test_late_long_identity_evidence_has_verifiable_untruncated_references():
    quote = '许宁走过长街。' + '沿途店铺已经打烊，街道逐渐安静。' * 15 + '他放下自己的药箱。'
    hero = {'name': '许宁', 'gender': 'unknown', 'metadata': {'identity_source_text': quote}}
    base = {'book_spec': {f'field{i}': f'approved filler {i}' for i in range(40)}, 'cast_spec': {'protagonist': hero}}
    candidate_hero = {**{f'field{i}': f'candidate filler {i}' for i in range(20)}, 'name': '许宁', 'gender': 'male', 'identity_evidence': quote}
    candidate = {'cast_spec': {'protagonist': candidate_hero}}
    sources, candidates = planner._source_bound_evidence_options(base, candidate, ['cast.protagonist'])
    assert len(quote) > 160
    for options, quote_id, is_candidate, payload in (
        (sources, 's_identity', False, base),
        (candidates, 'c_identity', True, candidate),
    ):
        resolved, ok = planner._source_bound_resolve_evidence(
            [{'field': 'cast.protagonist', 'quote_id': quote_id, 'source_relation': 'identity evidence'}],
            options, candidate=is_candidate,
        )
        assert ok
        assert resolved[0]['quote'] == quote
        assert planner._source_bound_quote_in_payload(resolved[0]['quote'], payload)


def test_dedicated_source_reference_cannot_be_created_for_invented_evidence():
    source = {'cast_spec': {'protagonist': baseline()['protagonist']}}
    candidate = {'cast_spec': {'protagonist': {'name': '许宁', 'gender': 'male', 'identity_evidence': '源中没有这个引文。'}}}
    sources, _ = planner._source_bound_evidence_options(source, candidate, ['cast.protagonist'])
    assert not any(row['quote_id'] == 's_identity' for row in sources)
