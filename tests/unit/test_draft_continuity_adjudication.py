"""Source actions preserve original model Unknowns; no paid calls or grade edits."""
from copy import deepcopy
import pytest
from bestseller.services import draft_continuity as dc,draft_continuity_adjudication as adjudicate
from test_draft_continuity import BODY,response

pytestmark=pytest.mark.unit

def case():
    sources={'foundations':{'book_spec':{'content':{'series_engine':{'first_three_chapter_hook':'Previously unknown readers remain unknown; do not assert a universal prior witness history.'}}}},
        'chapter_sources':{'chapter':{'chapter_number':1,'title':'A harmless title is not an epistemic constraint'}},'prior_manuscript_continuity':{'sources':[]}}
    raw=response(sources=sources);raw['checked_body_ids'].append(raw['checked_body_ids'][0]);raw['blocking_issues']=['']
    next(x for x in raw['checks'] if x['axis']=='prior_continuity')['status']='not_applicable'
    raw['unknown_issues']=['The manuscript explicitly preserves an unresolved historical reader scope.']
    anchors=dc.build_critic_body_anchors(BODY);catalog=dc.source_catalog(sources)
    clause=next(x for x in catalog if x['path'].endswith('/first_three_chapter_hook'))
    decisions=[{'index':0,'raw_issue_sha256':dc.body_hash(raw['unknown_issues'][0]),'category':'preserved_epistemic_boundary',
        'reason':'Current requirement explicitly prohibits inventing prior witnesses; the cited prose preserves that boundary, rather than asserting a new past fact.',
        'body_evidence':[{'id':anchors[1]['id'],'quote':'旧日谁曾读过，仍待核实。'}],
        'source_evidence':[{'id':clause['id'],'quote':'Previously unknown readers remain unknown'}]}]
    return sources,raw,decisions

def test_independent_source_actions_keep_original_report_unknown_and_all_evidence():
    sources,raw,decisions=case();original=deepcopy(raw)
    result=adjudicate.derive_axis_evidence(raw,body=BODY,sources=sources,decisions=decisions)
    assert raw==original and result['raw_unknown_issues']==original['unknown_issues'] and result['raw_blocking_issues']==['']
    assert result['raw_axis_assessments']==raw['checks'] and result['raw_model_grade_modified'] is False
    assert result['coverage']['complete'] and result['coverage']['raw_rows']==3 and result['coverage']['catalog_rows']==2
    assert result['coverage']['counts_as_grade_votes'] is False
    quote=result['diagnostic_actions'][0]['body_evidence'][0]
    assert BODY[quote['start']:quote['end']]==quote['quote']

@pytest.mark.parametrize('fault',['unknown_axis','failed_axis','missing_body','foreign_body','foreign_source','real_blocker','missing_decision','wrong_issue_hash','body_quote','source_quote','extra_axis','extra_top','scene_title'])
def test_source_adjudication_never_fills_missing_safety_or_waives_real_blockers(fault):
    sources,raw,decisions=case()
    if fault=='unknown_axis':raw['checks'][0]['status']='unknown'
    if fault=='failed_axis':raw['checks'][0]['status']='fail'
    if fault=='missing_body':raw['checked_body_ids']=raw['checked_body_ids'][:1]
    if fault=='foreign_body':raw['checked_body_ids'].append('Dother:P0010')
    if fault=='foreign_source':raw['checks'][0]['source_ids']=['Sfromanotherbook']
    if fault=='real_blocker':raw['blocking_issues']=['A source-proven nonempty hard blocker.']
    if fault=='missing_decision':decisions=[]
    if fault=='wrong_issue_hash':decisions[0]['raw_issue_sha256']='0'*64
    if fault=='body_quote':decisions[0]['body_evidence'][0]['quote']='Not present anywhere in this body.'
    if fault=='source_quote':decisions[0]['source_evidence'][0]['quote']='Invented source permission.'
    if fault=='extra_axis':raw['checks'][0]['hard_uncertainty']='unresolved'
    if fault=='extra_top':raw['hard_uncertainty']='unresolved'
    if fault=='scene_title':
        title=next(x for x in dc.source_catalog(sources) if x['path'].endswith('/title'))
        decisions[0]['source_evidence']=[{'id':title['id'],'quote':title['text']}]
    with pytest.raises(ValueError):adjudicate.derive_axis_evidence(raw,body=BODY,sources=sources,decisions=decisions)

def test_duplicate_json_key_cannot_overwrite_unknown_with_later_pass():
    with pytest.raises(ValueError,match='duplicate_json_key'):
        adjudicate.strict_json('{"status":"unknown","status":"pass"}')

@pytest.mark.parametrize('path',['/chapter_sources/scenes/0/title','/chapter_sources/scenes/0/sensory_anchors/light',
    '/foundations/book_spec/content/protagonist/name','/chapter_sources/chapter/metadata/deterministic_audit_history'])
def test_titles_names_and_runtime_flags_cannot_authorize_source_actions(path):
    assert not adjudicate.applicable_action_source(path)
