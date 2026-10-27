"""Exact opt-in clauses and extent references across unrelated stories."""
from copy import deepcopy
from types import SimpleNamespace
import pytest
from bestseller.services.retained_planning_clauses import selected_clauses,validate_retained_planning_clauses,validate_planning_chapter_references


def project():
 return SimpleNamespace(_source_bound_planning_canonical={'content':{'retained_planning_clauses':[{
  'kind':'world_spec','target_pointer':'/rules/0/description','source_artifact_id':'source',
  'source_content_sha256':'a'*64,'source_pointer':'/rules/2/description',
  'value':'每修复一盏航标，必须先登记一次耗电，领航额度增加一格。'}]}})


def test_opt_in_no_default():
 assert selected_clauses(SimpleNamespace(),'world_spec')==[]
 validate_retained_planning_clauses({'whatever':'kept'},project=SimpleNamespace(),kind='world_spec')

@pytest.mark.parametrize('fault',['quantity','prerequisite','missing','type'])
def test_locked_source_clause_rejects_drift(fault):
 p=project();text=selected_clauses(p,'world_spec')[0]['value']
 payload={'rules':[{'description':text}]}
 if fault=='quantity':payload['rules'][0]['description']=text.replace('一格','五格')
 if fault=='prerequisite':payload['rules'][0]['description']=text.replace('必须先','随后')
 if fault=='missing':payload['rules']=[]
 if fault=='type':payload['rules'][0]['description']=[text]
 with pytest.raises(ValueError):validate_retained_planning_clauses(payload,project=p,kind='world_spec')


def test_exact_source_not_injected_in_unrelated_kind():
 p=project();payload={'rules':[{'description':selected_clauses(p,'world_spec')[0]['value']}],'new_setting':'可自由提出'}
 validate_retained_planning_clauses(payload,project=p,kind='world_spec')
 validate_retained_planning_clauses({},project=p,kind='cast_spec')
 before=deepcopy(p._source_bound_planning_canonical)
 selected_clauses(p,'world_spec')[0]['value']='mutated'
 assert p._source_bound_planning_canonical==before

@pytest.mark.parametrize('text',['第25-30章收口','到第三十章结算','第21章开门','第十五至第二十一章风暴','第20-1章','第二十章后主动发声','第20章之后','第二十章以后','15-25章'])
def test_out_of_extent(text):
 with pytest.raises(ValueError,match='outside_extent'):validate_planning_chapter_references({'nested':[text]},target_chapters=20)

@pytest.mark.parametrize('text',['第1-20章','第十八至第二十章','三十七道主符，三十年经历，二十年前事件','第七主符，第十九章','第十四章后主动发声','第二十章后记'])
def test_valid_counts_not_replaced(text):
 validate_planning_chapter_references({'text':text,'_meta':{'old_paid_response':'第三十章'}},target_chapters=20)
