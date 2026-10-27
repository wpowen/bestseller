"""The real outline compiler keeps facts and task within its unchanged budget."""
import json
from uuid import uuid4

import pytest

from bestseller.infra.db.models import ProjectModel
from bestseller.services import planner
from bestseller.services.lossless_source_encoding import decode_lossless_source
from bestseller.services.prompt_compiler import PromptBudgetError
from bestseller.services.book_design import ensure_project_book_design_snapshot
from bestseller.services.source_bound_rolling_outline_prompt import POLICY
from bestseller.services.rolling_outline_source_transport import verify_source_complete_outline_transport
from bestseller.settings import load_settings

pytestmark=pytest.mark.unit


def project():
    return ProjectModel(id=uuid4(),slug='transport-observatory',title='Tide Observatory',
        genre='fantasy',language='en',target_chapters=30,target_word_count=78000,
        metadata_json={'premise':'Mira repairs an observatory clock.',
            'creation_protagonist_name':'Mira','creation_protagonist_source':'user'})


def prompt(source,task='请仅生成第1卷的指定章节。 Keep the chronometer intact.'):
    return 'Context facts.\n[SOURCE AUTHORITY]\n'+json.dumps(source,ensure_ascii=False,indent=2)+'\n[/SOURCE AUTHORITY]\n\n[ROLLING OUTLINE TASK]\n'+task


def test_actual_compiler_admits_reversible_source_and_retains_full_task():
    p=project();s=load_settings(env={});s.llm.planner_prompt_budget_tokens=2000
    repeated={'body':'Tide readings stay uncertain until the chronometer is repaired. '*70,
        'unknown_authored':{'$ref':'/literal','sequence':[0,False,None]}}
    source={'approved':[repeated for _ in range(12)]}
    task='请仅生成第1卷的指定章节。 REPAIR_ONLY_THE_CLOCK.\nPreserve every approved uncertainty.'
    system,user=planner._compile_volume_outline_prompt(p,s,system_prompt='Return JSON.',user_prompt=prompt(source,task))
    report=p.metadata_json['planner_prompt_compiler_latest']
    assert report['required_complete'] and report['total_tokens']<=report['usable_budget_tokens']==1800
    assert s.llm.planner_prompt_budget_tokens==2000 and task in user
    assert report['source_transport']['source_complete']
    encoded=json.loads(user.split('\n[SOURCE AUTHORITY]\n',1)[1].split('\n[/SOURCE AUTHORITY]',1)[0])
    decoded=decode_lossless_source(encoded) if 'source_context' not in encoded and encoded.get('schema','').startswith('lossless-') else encoded
    assert decoded==source
    assert system.startswith('Return JSON.')


def test_fitting_original_stays_byte_exact_and_no_transport_is_claimed():
    p=project();s=load_settings(env={});original=prompt({'fact':'The clock is not repaired.'})
    system,user=planner._compile_volume_outline_prompt(p,s,system_prompt='Return JSON.',user_prompt=original)
    assert system=='Return JSON.' and user==original
    assert 'source_transport' not in p.metadata_json['planner_prompt_compiler_latest']


def test_uncompressible_required_task_still_blocks_without_larger_budget():
    p=project();s=load_settings(env={});s.llm.planner_prompt_budget_tokens=1000
    with pytest.raises(PromptBudgetError):
        planner._compile_volume_outline_prompt(p,s,system_prompt='Return JSON.',
            user_prompt=prompt({'fact':'All source facts retained.'},'请仅生成第1卷。 '+ 'Current repair obligations. '*3000))
    assert s.llm.planner_prompt_budget_tokens==1000


@pytest.mark.parametrize('configured,wrong_project', [(32768,False),(None,False),(32768,True)])
def test_versioned_source_complete_ceiling_is_explicit_and_scope_bound(configured,wrong_project):
    p=project();ensure_project_book_design_snapshot(p)
    assert planner._source_bound_cast_enabled(p)
    s=load_settings(env={});s.llm.planner_source_complete_input_budget_tokens=configured
    source={'schema':POLICY,'project':{'id':str(uuid4() if wrong_project else p.id)},
        'notes':[str(i)+': '+('Each observation keeps its recorded date and uncertainty. '*65)
                 for i in range(21)]}
    if configured is None or wrong_project:
        with pytest.raises(PromptBudgetError):
            planner._compile_volume_outline_prompt(p,s,system_prompt='Return JSON.',user_prompt=prompt(source))
    else:
        wire_system,wire_user=planner._compile_volume_outline_prompt(p,s,system_prompt='Return JSON.',user_prompt=prompt(source))
        report=p.metadata_json['planner_prompt_compiler_latest']
        capacity=report['source_complete_capacity']
        assert capacity['preferred_input_budget']==16000
        assert capacity['source_complete_input_ceiling']==32768
        assert capacity['physical_request']['budget_passed']
        assert report['usable_budget_tokens']==29491 and report['required_complete']
        verify_source_complete_outline_transport('Return JSON.',prompt(source),wire_system,wire_user,
            report['source_transport'])
    assert s.llm.planner_prompt_budget_tokens==16000
