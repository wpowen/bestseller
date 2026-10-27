from types import SimpleNamespace

import pytest

from bestseller.services.reviews import build_chapter_review_prompts


@pytest.mark.parametrize('language', ['zh-CN', 'en'])
@pytest.mark.parametrize('genre', ['现实职场', '古典奇幻'])
def test_review_does_not_turn_craft_templates_into_story_requirements(language, genre, monkeypatch):
    from bestseller.services import methodology_compiler, quality_levers, reviews
    def forbidden(*args, **kwargs):
        raise AssertionError('Automatic craft/pack authority reached reviewer')
    monkeypatch.setattr(methodology_compiler, 'compile_methodology', forbidden)
    monkeypatch.setattr(reviews, 'render_methodology_block', forbidden)
    monkeypatch.setattr(quality_levers, 'build_critic_quality_levers_block', forbidden)
    assert not hasattr(reviews, 'compile_methodology')
    assert not hasattr(reviews, 'build_critic_quality_levers_block')
    project = SimpleNamespace(title='项目原题', genre=genre, sub_genre='', language=language,
        metadata_json={'genre_intent_contract': {'source': 'user', 'tag_source': 'default',
            'tags': ['默认模板要求'], 'default_tags': ['默认模板要求'], 'user_tags': ['无来源标签'],
            'prompt_pack_key': '自动套路'},
            'creation_intent_contract': {'field_sources': {'tone_preference': 'explicit'},
                'tone_preference': '保留作者指定语气'}})
    chapter = SimpleNamespace(chapter_number=1, title='原章名', chapter_goal='原始目标')
    draft = SimpleNamespace(content_md='当事人把钥匙交给同伴，随后离开房间。')
    result = SimpleNamespace(scores=SimpleNamespace(model_dump=lambda **k: {'overall': .8}), findings=[])
    system, user = build_chapter_review_prompts(project, chapter, draft, None, result)
    assert draft.content_md in user
    assert '保留作者指定语气' in user
    assert '默认模板要求' not in user and '自动套路' not in user and '无来源标签' not in user
    assert '黑水' not in system and '四项方法论轴' not in system and '≥ 2 项 FAIL' not in system
    assert 'CURRENT_DRAFT' in system and 'EVIDENCE_ANCHORS:' in system
    assert 'Canon/Timeline' in system


def test_explicit_user_genre_tags_survive_without_default_tags():
    from bestseller.services.creation_contract_authority import select_creation_contracts
    original = {'genre_intent_contract': {'source': 'user', 'tag_source': 'user',
        'user_tags': ['作者自选'], 'tags': ['作者自选', '默认值'], 'default_tags': ['默认值']}}
    from copy import deepcopy
    original['creation_intent_contract'] = {'field_sources': {'genre_intent': 'explicit'},
        'genre_intent': deepcopy(original['genre_intent_contract'])}
    selected = select_creation_contracts(original, source_bound=True)
    assert selected['genre_intent_contract']['tags'] == ['作者自选']
    assert original['genre_intent_contract']['default_tags'] == ['默认值']


async def test_commercial_review_keeps_project_workflow_and_has_no_automatic_corpus(monkeypatch):
    import json
    from uuid import uuid4
    from bestseller.services import chapter_llm_quality_judge as judge
    from bestseller.services.llm import LLMCompletionResult
    from bestseller.settings import load_settings
    seen = []
    async def complete(session, settings, request):
        seen.append(request)
        return LLMCompletionResult(content=json.dumps({'pass': False, 'overall_score': .6,
            'dimension_scores': {}, 'blocking_issues': [], 'audit_issues': [],
            'rewrite_plan': {'scope': 'chapter'}}), provider='mock', model_name='mock')
    monkeypatch.setattr(judge, 'complete_text', complete)
    project, workflow = uuid4(), uuid4()
    await judge.judge_chapter_commercial_quality(object(), load_settings(env={}),
        chapter_number=1, content_md='一段独立正文。', project_id=project, workflow_run_id=workflow,
        reference_corpus_key='action-progression')
    assert seen[0].project_id == project and seen[0].workflow_run_id == workflow
    assert '测灵石第三次没有亮' not in seen[0].system_prompt
    assert '本章须兑现一个打脸' not in seen[0].system_prompt
    assert '没有后果证据的技巧偏好只记 audit' in seen[0].system_prompt


def test_reader_judge_projection_does_not_receive_planner_recipes_or_old_diagnostics():
    from copy import deepcopy
    from bestseller.services.chapter_llm_quality_judge import _commercial_source_projection
    original = {'schema_version': 'chapter-generation-input.v2',
        'chapter': {'chapter_number': 7, 'title': '原题', 'metadata': {'write_safety_hint': '旧报错'}},
        'chapter_contract': {'planning_intent': {'ending': '未经批准的剧情配方'}},
        'acceptance_contract': {'source_authority_channels': {
            'explicit_creator_requirements': {'tone': '作者明确选定语气'},
            'established_facts': {'adopted': '此前已成立事实'},
            'ai_proposals': {'ending': '未经批准的剧情配方'}}},
        'story_bible': {'world_rules': {'identity': '已批准世界身份规则'}, 'identity': '未类型化规划摘要'}}
    before = deepcopy(original)
    projected = _commercial_source_projection(original)
    assert '旧报错' not in str(projected) and '未经批准的剧情配方' not in str(projected)
    assert '作者明确选定语气' in str(projected) and '此前已成立事实' in str(projected)
    assert projected['story_bible'] == {'world_rules': {'identity': '已批准世界身份规则'}}
    assert original == before


@pytest.mark.parametrize('language', ['zh-CN', 'en'])
def test_plan_goal_is_typed_as_intent_and_occurrence_checks_preserve_temporal_scope(language):
    import json
    project = SimpleNamespace(title='Weather observatory', genre='science-fiction', sub_genre='',
        language=language, metadata_json={})
    chapter = SimpleNamespace(chapter_number=4, title='The second instrument',
        chapter_goal='After the first gauge was opened, inspect a different sealed gauge.')
    draft = SimpleNamespace(content_md='The operator checked the old reading, then carried the instrument outside.')
    result = SimpleNamespace(scores=SimpleNamespace(model_dump=lambda **k: {'overall': .8}), findings=[])
    system, user = build_chapter_review_prompts(project, chapter, draft, None, result)
    data = user.split('EDITORIAL_PLAN (', 1)[1].split('):\n', 1)[1]
    goal, _end = json.JSONDecoder().raw_decode(data)
    assert goal == {'source_path': 'chapter.chapter_goal', 'kind': 'editorial_plan', 'goal': chapter.chapter_goal}
    assert user.count(draft.content_md) >= 1
    if language == 'en':
        assert 'same object/event' in system and 'story time' in system
        assert 'later inspection' in system and 'different object' in system
        assert 'Plan nonfulfillment is plan alignment' in system
        assert 'do not invent characters, deadlines' not in system
    else:
        assert '同一对象或事件' in system and '故事时点' in system
        assert '事后检查或确认' in system and '不同对象的新事件' in system
        assert '规划未兑现属于计划对齐问题' in system
        assert '不得新增人物、期限' not in system
