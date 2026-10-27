"""A typed transport for missing scene budgets; never synthesize source values.

Only an actual deterministic missing-field report activates this route. The
model proposes positive integer allocations; every other story byte is retained.
Ordinary story repair keeps its complete source channels and existing protocol.
"""
from copy import deepcopy
import re
from bestseller.services.prompt_source_policy import content_hash

POLICY='prefix-missing-scene-budget.v1'


def budget_projection(candidate,report):
    violations=report.get('violations',[])
    if report.get('passed') is not False or not violations:return None
    targets=[]
    for row in violations:
        if row.get('code')!='PLAN_SOURCE_FIELD_MISSING' or row.get('metadata',{}).get('accepted_keys')!=['target_word_count']:return None
        match=re.fullmatch(r'/chapters/(\d+)/scenes/(\d+)/target_word_count',row.get('location',''))
        if not match:return None
        ci,si=map(int,match.groups())
        try:chapter=candidate['chapters'][ci];scene=chapter['scenes'][si]
        except (KeyError,IndexError,TypeError):return None
        if 'target_word_count' in scene or type(chapter.get('target_word_count')) is not int or chapter['target_word_count']<=0:return None
        targets.append({'chapter_number':chapter['chapter_number'],'scene_number':scene['scene_number'],'chapter_index':ci,'scene_index':si})
    if len({(t['chapter_index'],t['scene_index']) for t in targets})!=len(targets):return None
    return {'schema':POLICY,'candidate_sha256':content_hash(candidate),'actual_report_sha256':content_hash(report),'allowed_targets':targets,
      'chapters':[{'chapter_number':c['chapter_number'],'target_word_count':c['target_word_count'],'scenes':[{'scene_number':s['scene_number'],**({'target_word_count':s['target_word_count']} if 'target_word_count' in s else {}),'purpose':deepcopy(s.get('purpose')),'action_sequence':deepcopy(s.get('action_sequence'))} for s in c['scenes']]} for c in candidate['chapters']],
      'authority':'Existing chapter budgets are immutable. Only the listed absent scene target_word_count fields may receive new proposed integer allocations. No story edits or source facts are generated.'}


def apply_budget_patches(candidate,patches,projection):
    if content_hash(candidate)!=projection['candidate_sha256']:raise ValueError('prefix_budget_candidate_changed')
    if not isinstance(patches,list) or len(patches)!=len(projection['allowed_targets']):raise ValueError('prefix_budget_exact_inventory')
    allowed={(t['chapter_number'],t['scene_number']):t for t in projection['allowed_targets']};seen=set();result=deepcopy(candidate)
    for patch in patches:
        if not isinstance(patch,dict) or set(patch)!={'chapter_number','scope','scene_number','field','value'} or patch['scope']!='scene' or patch['field']!='target_word_count' or type(patch['value']) is not int or patch['value']<=0:raise ValueError('prefix_budget_typed_patch')
        key=(patch['chapter_number'],patch['scene_number'])
        if key not in allowed or key in seen:raise ValueError('prefix_budget_foreign_or_duplicate')
        seen.add(key);t=allowed[key];result['chapters'][t['chapter_index']]['scenes'][t['scene_index']]['target_word_count']=patch['value']
    for c in result['chapters']:
        if all(type(s.get('target_word_count')) is int for s in c['scenes']) and sum(s['target_word_count'] for s in c['scenes'])!=c['target_word_count']:raise ValueError('prefix_budget_allocation_total')
    return result
