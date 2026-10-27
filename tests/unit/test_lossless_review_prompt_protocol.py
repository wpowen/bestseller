"""Lossless proposals remain data; new prompts preserve adopted past and creative freedom."""
import json
from types import SimpleNamespace

import pytest
from test_chapter_rewrite_sourcebound_prompt import inputs as inputs
from bestseller.services import reviews

pytestmark=pytest.mark.unit


@pytest.mark.parametrize('language',['zh-CN','en'])
def test_new_task_protocol_clarifies_future_creation_without_changing_legacy_input(inputs,language):
    project,chapter,draft,task,context,_=inputs
    project.language=language
    old=reviews.build_chapter_rewrite_prompts(project,chapter,draft,task,context)
    task.metadata_json={'editor_prompt_protocol':'3.1-lossless-source-time'}
    new=reviews.build_chapter_rewrite_prompts(project,chapter,draft,task,context)
    assert new[0]!=old[0] and new[1]==old[1]
    assert draft.content_md in new[1]
    assert ('New fictional actions' in new[0]) if language=='en' else ('允许在已选故事范围内创作后续行动' in new[0])
    assert ('retrospective' in new[0]) if language=='en' else ('不得补造过去事实或改写已采用的事件' in new[0])
    task.metadata_json={'editor_prompt_protocol':'unrecognized-old-task'}
    assert reviews.build_chapter_rewrite_prompts(project,chapter,draft,task,context)==old


def test_sanitizer_preserves_full_quote_and_plan_when_evidence_contains_a_forbidden_term():
    payload={'pass':False,'rewrite_plan':{'scope':'chapter','instructions':'Apply the changes above.',
        'change':['撤去与既定事实冲突的段落，衔接后果。'],'preserve':['无关段落。']},
        'blocking_issues':[{'code':'SOURCE_CONFLICT','evidence':'原稿写了机甲。','required_fix':'修复该引用段落。'}]}
    merged=reviews.merge_judge_rewrite_direction('修复有据的冲突。',judge_payload=payload)
    chapter=SimpleNamespace(metadata_json={'object_signal_contract':{'forbidden_terms':['机甲']}},
                            chapter_number=12,foreshadowing_actions={})
    cleaned=reviews._sanitize_chapter_review_rewrite_instructions(merged,chapter=chapter,scenes=[])
    marker='REVIEW_REPAIR_HANDOFF_PROPOSAL_JSON:\n'
    assert cleaned.count(marker)==1
    result=json.loads(cleaned.split(marker,1)[1])
    assert result['rewrite_plan']==payload['rewrite_plan']
    assert result['blocking_issues']==payload['blocking_issues']
    assert result['authority']=='proposal_only'
