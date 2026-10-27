"""Unrelated compact examples exercise exact source-bound supplement protocol."""
from copy import deepcopy
import json
import pytest
from bestseller.services.foundation_revision_delta import AXES,validate_delta_verdict
from bestseller.services.conception_snapshots import snapshot_hash
pytestmark=pytest.mark.unit

def sample():
 context={'original':{'rule':'每修复一个航标必须先登记耗电。'},'candidate':{'plan':'先登记耗电，再修复第二航标。'},'authorization':{'kind':'new_plan'}}
 raw={'decision':'pass','input_sha256':snapshot_hash(context),'checks':[{'axis':axis,'verdict':'pass','source_pointer':'/original/rule','source_quote':'必须先登记耗电','candidate_pointer':'/candidate/plan','candidate_quote':'先登记耗电','reason':'依据逐字保留'} for axis in AXES],'feedback':'仅新规划审查'}
 return context,raw

def test_exact_quoted_delta_pass_is_not_generation_credit():
 c,r=sample();before=deepcopy(c);got=validate_delta_verdict(json.dumps(r,ensure_ascii=False),c)
 assert got['passed'] and not got['new_generation_credit'] and c==before

@pytest.mark.parametrize('fault',['digest','source_quote','candidate_quote','duplicate_axis','candidate_scope','source_scope','contradictory_pass','duplicate_json','trailing'])
def test_unbound_or_ambiguous_delta_rejected(fault):
 c,r=sample()
 if fault=='digest':r['input_sha256']='a'*64
 if fault=='source_quote':r['checks'][0]['source_quote']='先通过才补记'
 if fault=='candidate_quote':r['checks'][0]['candidate_quote']='不存在的原文'
 if fault=='duplicate_axis':r['checks'][0]['axis']=r['checks'][1]['axis']
 if fault=='candidate_scope':r['checks'][0]['candidate_pointer']='/original/rule'
 if fault=='source_scope':r['checks'][0]['source_pointer']='/candidate/plan'
 if fault=='contradictory_pass':r['checks'][0]['verdict']='unknown'
 raw=json.dumps(r,ensure_ascii=False)
 if fault=='duplicate_json':raw=raw[:-1]+',"decision":"pass"}'
 if fault=='trailing':raw+=' pass'
 with pytest.raises(ValueError):validate_delta_verdict(raw,c)

def test_unknown_is_retained_not_passed():
 c,r=sample();r['decision']='unknown';r['checks'][2]['verdict']='unknown'
 result=validate_delta_verdict(json.dumps(r),c)
 assert result['decision']=='unknown' and not result['passed']


def test_catalog_only_resolves_real_quotes_without_changing_verdict():
 from bestseller.services.foundation_revision_delta import delta_quote_catalog,validate_catalog_delta_verdict
 c={'original':{'assets':{'book_spec':{'series_engine':{'core_serial_engine':'先记耗电再修航标。','endgame_direction':'修复港口当夜危机。'}},'world_spec':{'rules':[]},'cast_spec':{'supporting_cast':[]}}},'candidate':{'assets':{'cast_spec':{'supporting_cast':[]},'volume_plan':[{'volume_climax':'先记耗电再修航标。港口当夜危机已解。','volume_resolution':{'cost_paid':'先登记耗电。'}}]}},'authorization':{'kind':'new_plan'},'delivery':{'purpose':'complete_story'}}
 cat=delta_quote_catalog(c)
 source=next(k for k,v in cat.items() if v['pointer'].startswith('/original/'))
 candidate=next(k for k,v in cat.items() if v['pointer'].startswith('/candidate/'))
 r={'decision':'pass','input_sha256':snapshot_hash(c),'checks':[{'axis':a,'verdict':'pass','source_quote_id':source,'candidate_quote_id':candidate,'reason':'原句绑定'} for a in AXES],'feedback':'仅新依赖'}
 result=validate_catalog_delta_verdict(json.dumps(r),c)
 assert result['passed'] and result['raw_verdict_unchanged']
 assert result['checks'][0]['source_quote']==cat[source]['quote']
 r['decision']='revise';r['checks'][1]['verdict']='revise'
 assert not validate_catalog_delta_verdict(json.dumps(r),c)['passed']
 r['checks'][0]['candidate_quote_id']='invented'
 with pytest.raises(ValueError):validate_catalog_delta_verdict(json.dumps(r),c)


def test_existing_codec_array_sections_restore_all_authored_fields():
 from bestseller.services.foundation_revision_delta import delta_source_sections,restore_delta_sections
 from bestseller.services.lossless_source_encoding import encode_lossless_source,decode_lossless_source
 context={'original':{'canonical':{'authored':{'$ref':'literal','o':'not a transport alias'}},'assets':{'book_spec':{'text':'完整原文'}}},'candidate':{'canonical':{'future/constraint':'未知字段仍保留'},'assets':{'book_spec':{'text':'完整新文'}}},'delivery':{'purpose':'complete_story'}}
 before=deepcopy(context);catalog={'q0':{'pointer':'/original/assets/book_spec/text','quote':'完整原文'}}
 values,paths=delta_source_sections(context,catalog)
 decoded=decode_lossless_source(encode_lossless_source(values,version=3))
 restored,q=restore_delta_sections(decoded,paths)
 assert restored==before==context and q==catalog
 assert snapshot_hash(restored)==snapshot_hash(context)
 with pytest.raises(ValueError):restore_delta_sections(values,paths[:-1])


@pytest.mark.asyncio
async def test_complete_request_reserve_rejects_before_gateway(monkeypatch):
 from types import SimpleNamespace
 from bestseller.services import foundation_revision_delta as service, llm
 from bestseller.services.source_bound_planning_content import SourceBoundPlanningCapacityError
 context,_=sample()
 async def fake_context(*args):return context
 # Unique non-ASCII text fits the input-only ceiling but fails the physical
 # request reserve. No DB writes or gateway dispatch may follow admission.
 def sections(*args):return [''.join(chr(0x4e00+i%20000) for i in range(85000))], []
 async def forbidden(*args):pytest.fail('gateway called before full request admission')
 monkeypatch.setattr(service,'delta_context',fake_context)
 monkeypatch.setattr(service,'delta_quote_catalog',lambda c:{})
 monkeypatch.setattr(service,'delta_source_sections',sections)
 monkeypatch.setattr(llm,'complete_text',forbidden)
 with pytest.raises(SourceBoundPlanningCapacityError) as error:
  await service.review_creative_delta(None,None,project=SimpleNamespace(id=__import__('uuid').uuid4()),
   revision=SimpleNamespace(id=__import__('uuid').uuid4()),workflow=SimpleNamespace(id=__import__('uuid').uuid4()))
 assert error.value.receipt['output_reserve_tokens']==5000
 assert error.value.receipt['budget_passed'] is False


def test_scoped_before_values_restore_and_unknown_authored_fields_survive():
 from bestseller.services.foundation_dependency_scope import dependency_review_view,restore_original,REFINEMENT_AXES
 old={'canonical':{'conception_artifacts':{'story_appeal':{'old_evaluation':'retain externally'},'commercial_brief':{'power_system':'one cost'}},'unknown_authored':{'chapter_review_instruction':'a real clause'},'_snapshot_audit':[{'state':'old'}]},'assets':{'cast_spec':{},'volume_plan':[{'ending':'old','remove':'before'}]}}
 new=deepcopy(old);new['assets']['volume_plan'][0]={'ending':'new','add':'after'}
 c={'original':old,'candidate':new,'review_axes':list(REFINEMENT_AXES),'prior_dependency_review':{'real_verdict':'revise'}};before=deepcopy(c)
 v=dependency_review_view(c)
 restored=restore_original(v['current_candidate'],v['original_before_patches'])
 assert snapshot_hash(restored)==v['scope_receipt']['scoped_original_sha256']
 assert v['current_candidate']['canonical']['unknown_authored']==old['canonical']['unknown_authored']
 assert v['current_candidate']['canonical']['conception_artifacts']['commercial_brief']=={'power_system':'one cost'}
 assert len(v['source_archives'])==4 and c==before
 assert not v['scope_receipt']['whole_book_review']

@pytest.mark.parametrize('changed',['canonical','book_spec','world_spec','cast_spec','progression_events'])
def test_prior_passes_cannot_cover_changed_dependency(changed):
 from bestseller.services.foundation_dependency_scope import bind_prior_dependency_review,UNCHANGED_AXES,REFINEMENT_AXES
 current={'candidate':{'canonical':{},'assets':{k:{} for k in ('book_spec','world_spec','cast_spec')}},**{k:{} for k in ('protected_clauses','progression_events','new_character_cards','authorization','delivery','current_authority_snapshot')}}
 previous=deepcopy(current)
 verdict={'input_sha256':snapshot_hash(previous),'decision':'revise','raw_verdict_unchanged':True,'checks':[{'axis':a,'verdict':'pass' if a in UNCHANGED_AXES else 'revise'} for a in UNCHANGED_AXES+REFINEMENT_AXES]}
 if changed=='canonical':current['candidate']['canonical']['different']='new'
 elif changed=='progression_events':current[changed]['different']='new'
 else:current['candidate']['assets'][changed]['different']='new'
 with pytest.raises(ValueError,match='source_changed'):
  bind_prior_dependency_review(current,previous,verdict,report_id='report',run_id='run')


def test_psychological_and_style_facets_cannot_hide_historical_knowledge():
 from bestseller.services.foundation_dependency_scope import dependency_review_view,REFINEMENT_AXES
 actor={'knowledge_state':{'known':[]},'psych_profile':{'prior_event':'曾亲眼看见航标熄灭'},'voice_profile':{'secret_phrase':'我听过密室回声'},'family_imprint':{'memory':'家中有阵图'},'unknown_authored_fact':'另有目击者'}
 source={'canonical':{},'assets':{'cast_spec':{'protagonist':actor}}}
 c={'original':deepcopy(source),'candidate':deepcopy(source),'review_axes':list(REFINEMENT_AXES),'prior_dependency_review':{'checks':[]}}
 v=dependency_review_view(c)
 assert v['current_candidate']['assets']['cast_spec']['protagonist']==actor


def _dependency_fixture():
 from copy import deepcopy
 def render(cards):
  return 'Harbor ending.\n收尾实际信息流：'+'\n'.join(f"第{c['chapter']}章｜{c['present']}｜{c['actual_action']}｜{c['actual_information']}｜{c['trigger_account']}" for c in cards)
 cards=[{'chapter':8,'location':'harbor office','present':'Mira and Ivo','actual_action':'Hand over ordinary inventory.','actual_information':'No seal truth is disclosed.','trigger_account':'No new rune stroke.'}]
 volume={key:'' for key in ('volume_goal','opening_state','key_reveals','volume_resolution','reader_hook_to_next','foreshadowing_paid_off','foreshadowing_planted','volume_obstacle')}
 volume.update(information_flow_scene_cards=cards,volume_climax=render(cards))
 canonical={'premise':'Repair the harbor.','synopsis':'Keep the records.','hook_card':{},'story_spine':{},'seriality_proof':{},'writing_profile':{'serialization':{'opening_mandate':'前三章1变3，与当前计划有冲突'}},'commercial_brief':{},'conception_artifacts':{'commercial_brief':{}},'concept_contract':{'story_architecture':{}},'new_explicit_clause':'Unknown authored source survives.'}
 sources={'canonical':canonical,'assets':{'book_spec':{'series_engine':{'core_serial_engine':'Pay first.','endgame_direction':'Close the harbor crisis.'}},'world_spec':{'rules':['A real cost before every stroke.']},'cast_spec':{'protagonist':{'name':'Mira','knowledge_state':{'old':'Unknown'},'psych_profile':{'memory':'Saw the harbor light.'}}},'volume_plan':[volume]}}
 c={'original':deepcopy(sources),'candidate':deepcopy(sources),'review_axes':['no_extra_knowledge_triggers','ending_and_source_consistency'],'prior_dependency_review':{'checks':[]},'protected_clauses':[],'authorization':{},'delivery':{},'progression_events':[],'new_character_cards':[],'current_authority_snapshot':{'creation_intent':{},'entity_registry':{}},'review_disagreement_and_corrections':[]}
 c['candidate']['assets']['volume_plan'][0]['information_flow_scene_cards'][0]['actual_action']='Deliver the sealed box, then read the family letter.'
 c['candidate']['assets']['volume_plan'][0]['volume_climax']=render(c['candidate']['assets']['volume_plan'][0]['information_flow_scene_cards'])
 return c


def test_dependency_fragments_preserve_rules_full_cast_and_conflicting_profile():
 from bestseller.services.foundation_dependency_scope import dependency_review_fragments,REFINEMENT_AXES
 c=_dependency_fixture();before=deepcopy(c);packets=[]
 for axis in REFINEMENT_AXES:
  c['review_axes']=[axis];view=dependency_review_fragments(c);sources={v['pointer']:v['value'] for v in view['source_fragments']}
  for key in ('cast_spec','world_spec'):
   assert sources['/candidate/assets/'+key]==c['candidate']['assets'][key]
  assert sources['/candidate/canonical/writing_profile']==c['candidate']['canonical']['writing_profile']
  assert sources['/candidate/canonical/new_explicit_clause']=='Unknown authored source survives.'
  for side in ('original','candidate'):
   volume=c[side]['assets']['volume_plan'][0];prefix='/'+side+'/assets/volume_plan/0/'
   if axis=='no_extra_knowledge_triggers':
    assert sources[prefix+'information_flow_scene_cards']==volume['information_flow_scene_cards']
   else:
    assert sources[prefix+'volume_climax']==volume['volume_climax']
    for i,card in enumerate(volume['information_flow_scene_cards']):
     assert sources[prefix+f'information_flow_scene_cards/{i}/location']==card['location']
     assert all(card[key] in sources[prefix+'volume_climax'] for key in ('present','actual_action','actual_information','trigger_account'))
  packets.append(set(sources))
 assert packets[0]!=packets[1]
 c['review_axes']=before['review_axes'];assert c==before


def test_ending_fragment_factoring_cannot_hide_unrendered_scene_values():
 from bestseller.services.foundation_dependency_scope import dependency_review_fragments
 c=_dependency_fixture();c['review_axes']=['ending_and_source_consistency']
 c['candidate']['assets']['volume_plan'][0]['volume_climax']='A different ending without the actual scene.'
 with pytest.raises(ValueError,match='scene_rendering_not_exact'):dependency_review_fragments(c)
