from copy import deepcopy
import pytest
from bestseller.services.prefix_budget_fields import budget_projection,apply_budget_patches
pytestmark=pytest.mark.unit

def case():
 c={'chapters':[{'chapter_number':8,'target_word_count':2400,'scenes':[{'scene_number':1,'purpose':{'story':'A telescope changes custody.'},'action_sequence':['Check the seals.']}]}]}
 r={'passed':False,'violations':[{'code':'PLAN_SOURCE_FIELD_MISSING','severity':'critical','location':'/chapters/0/scenes/0/target_word_count','metadata':{'accepted_keys':['target_word_count']}}]}
 p=[{'chapter_number':8,'scope':'scene','scene_number':1,'field':'target_word_count','value':2400}]
 return c,r,p

def test_only_missing_budget_is_proposed_and_every_story_byte_stays_exact():
 c,r,p=case();before=deepcopy(c);projection=budget_projection(c,r);changed=apply_budget_patches(c,p,projection)
 assert c==before and changed['chapters'][0]['scenes'][0].pop('target_word_count')==2400
 assert changed==before and projection['candidate_sha256']

@pytest.mark.parametrize('fault',['different_field','different_code','mixed','already_present','bad_path','missing_chapter_budget','duplicate'])
def test_unrelated_or_ambiguous_report_cannot_drop_full_sources(fault):
 c,r,_=case();v=r['violations'][0]
 if fault=='different_field':v['metadata']['accepted_keys']=['purpose']
 if fault=='different_code':v['code']='CAST_IDENTITY_MISMATCH'
 if fault=='mixed':r['violations'].append({'code':'HARD_CONTRADICTION'})
 if fault=='already_present':c['chapters'][0]['scenes'][0]['target_word_count']=1
 if fault=='bad_path':v['location']='/chapters/3/scenes/0/target_word_count'
 if fault=='missing_chapter_budget':del c['chapters'][0]['target_word_count']
 if fault=='duplicate':r['violations']*=2
 assert budget_projection(c,r) is None

@pytest.mark.parametrize('fault',['story','foreign','empty','duplicate','bool','string','zero','total','changed_source'])
def test_only_exact_positive_integer_inventory_can_be_applied(fault):
 c,r,p=case();projection=budget_projection(c,r)
 if fault=='story':p[0]['field']='purpose'
 if fault=='foreign':p[0]['chapter_number']=99
 if fault=='empty':p=[]
 if fault=='duplicate':p*=2
 if fault=='bool':p[0]['value']=True
 if fault=='string':p[0]['value']='2400'
 if fault=='zero':p[0]['value']=0
 if fault=='total':p[0]['value']=2300
 if fault=='changed_source':c['chapters'][0]['target_word_count']=2500
 with pytest.raises(ValueError):apply_budget_patches(c,p,projection)
