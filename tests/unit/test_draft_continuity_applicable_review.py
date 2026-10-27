"""Explicit projections and lossless transport; no DB/model calls."""
from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4
import json
import pytest
from bestseller.services import draft_continuity as dc
from bestseller.services import draft_continuity_applicable_review as svc
from bestseller.services.lossless_source_encoding import decode_lossless_source

pytestmark=pytest.mark.unit
BODY='# 城外\n\n她今日没有落笔。\n\n“旧话仍待核实。”\n\n夜里说过的话，不能当作今晨已经发生。'
SOURCE={'chapter_sources':{'chapter':{'chapter_number':4},'unknown_author_clause':'保持过去知情未知。','cache':{'writer':'old'}},'cast':[{'name':'甲','secret':'不能猜成事实'},{'name':'乙'}],'prior_manuscript_continuity':{'sources':[{'body':'她昨日已经离开。'}]}}

def inputs():
 source=deepcopy(SOURCE);exclusions=[{'path':'/chapter_sources/cache','value_sha256':dc.content_hash(source['chapter_sources']['cache']),'reason':'runtime bookkeeping; not authored story facts'},{'path':'/cast/1','value_sha256':dc.content_hash(source['cast'][1]),'reason':'explicit scope: not present or causally implicated in this scene'}]
 projected,manifest=svc.make_projection(source,exclusions=exclusions,approved_chapter={'goal':'本日只看灯，不落阵笔。'},expected_parent_sha256=dc.content_hash(source))
 return source,projected,manifest,exclusions

def test_projection_is_explicit_scoped_typed_and_does_not_mutate_parent():
 source,projected,manifest,_=inputs()
 assert source==SOURCE and projected['cast']==[SOURCE['cast'][0]]
 assert projected['chapter_sources']['unknown_author_clause']==SOURCE['chapter_sources']['unknown_author_clause']
 assert manifest['excluded_sources_assessed'] is False and manifest['projected_source_sha256']==dc.content_hash(projected)
 assert projected['prior_manuscript_continuity']==SOURCE['prior_manuscript_continuity']

@pytest.mark.parametrize('bad',['parent','value','overlap','reason'])
def test_changed_parent_value_or_ambiguous_exclusion_is_rejected(bad):
 source,_,_,exclusions=inputs();digest=dc.content_hash(source)
 if bad=='parent':source['new author rule']='新增'
 if bad=='value':exclusions[0]['value_sha256']='0'*64
 if bad=='overlap':exclusions.append({'path':'/cast','value_sha256':dc.content_hash(source['cast']),'reason':'overlap'})
 if bad=='reason':exclusions[0]['reason']=' '
 with pytest.raises(ValueError):svc.make_projection(source,exclusions=exclusions,approved_chapter={},expected_parent_sha256=digest)


def test_array_exclusions_use_original_indices_not_shifted_indices():
 source={'cast':[{'i':i} for i in range(5)]};ex=[{'path':'/cast/'+str(i),'value_sha256':dc.content_hash(source['cast'][i]),'reason':'explicitly irrelevant'} for i in [1,3]]
 out,_=svc.make_projection(source,exclusions=ex,approved_chapter={},expected_parent_sha256=dc.content_hash(source))
 assert out['cast']==[{'i':0},{'i':2},{'i':4}]


def test_exact_full_anchor_and_nested_position_catalog_roundtrip():
 _,projected,manifest,_=inputs();payload=svc.transport(BODY,projected,manifest)
 anchors,tree=svc.validate_transport(payload)
 assert anchors==list(dc.build_critic_body_anchors(BODY))
 assert tree==dc.source_catalog_index(projected)['tree']
 assert payload['body']==BODY and payload['sources']==projected

@pytest.mark.parametrize('bad',['body','offset','body_id','drop_anchor','catalog','catalog_shape'])
def test_tampered_transport_never_claims_roundtrip(bad):
 _,projected,manifest,_=inputs();payload=svc.transport(BODY,projected,manifest)
 if bad=='body':payload['body']+='新文字'
 if bad=='offset':payload['body_catalog_rows'][-1][2]-=1
 if bad=='body_id':payload['body_catalog_rows'][-1][0]='forged'
 if bad=='drop_anchor':payload['body_catalog_rows'].pop()
 if bad=='catalog':payload['source_catalog_positions'][0][0]='S99999'
 if bad=='catalog_shape':payload['source_catalog_positions'].pop()
 with pytest.raises((ValueError,TypeError)):svc.validate_transport(payload)


def test_physical_request_preserves_all_selected_values_and_only_selected_ids():
 _,projected,manifest,_=inputs();request=svc.request_for(project=SimpleNamespace(id=uuid4()),workflow_id=uuid4(),draft=SimpleNamespace(id=uuid4(),content_md=BODY),sources=projected,manifest=manifest)
 payload=decode_lossless_source(json.loads(request.user_prompt)['value']);svc.validate_transport(payload)
 assert payload['sources']==projected and payload['body']==BODY
 assert request.max_tokens_override==6500 and request.messages_override is None
 ids=request.tools[0]['function']['parameters']['properties']['checks']['items']['properties']['source_ids']['items']['enum']
 assert ids==[a['id'] for a in dc.source_catalog(projected)]
 assert 'excluded_sources_assessed' in payload and payload['excluded_sources_assessed'] is False


def test_prepared_identity_binds_model_protocol_and_complete_effective_input(monkeypatch):
 from bestseller.settings import load_settings
 from bestseller.services.model_catalog import ModelCatalogEntry
 from bestseller.services import retained_review_input_proof as retained
 st=load_settings(env={});st.llm.mock=False
 catalog={key:ModelCatalogEntry(id=key,display_name=key,vendor='fixture',model=model,api_base='https://api.minimaxi.com/v1') for key,model in [('old','openai/MiniMax-M3'),('new','openai/MiniMax-M3.1-Flash')]}
 monkeypatch.setattr(retained,'_fresh_catalog',lambda:(catalog,'fixture-catalog'))
 monkeypatch.setattr('bestseller.services.model_catalog._resolve_availability',lambda e:(True,None))
 project=SimpleNamespace(id=uuid4(),metadata_json={'llm_model_id':'new'});draft=SimpleNamespace(id=uuid4(),content_md=BODY)
 _,source,manifest,_=inputs()
 first=svc.prepare_request(st,project=project,draft=draft,sources=source,manifest=manifest)
 same=svc.prepare_request(st,project=project,draft=draft,sources=source,manifest=manifest)
 assert first[0]==same[0] and first[1]==same[1] and first[3]==same[3]
 assert first[1].operation_id.endswith(first[3]) and first[1].model_catalog_key=='new'
 changed=st.model_copy(deep=True);changed.llm.critic.temperature=.731
 assert svc.prepare_request(changed,project=project,draft=draft,sources=source,manifest=manifest)[0]!=first[0]
 project.metadata_json={'llm_model_id':'old'}
 assert svc.prepare_request(st,project=project,draft=draft,sources=source,manifest=manifest)[0]!=first[0]
 project.metadata_json={}
 with pytest.raises(ValueError,match='model_selection_required'):svc.prepare_request(st,project=project,draft=draft,sources=source,manifest=manifest)


def test_contract_identity_ignores_consumer_only_edits_but_binds_actual_prompt_and_cap():
 from pathlib import Path
 source=Path(svc.__file__).read_text();original=svc._protocol_fingerprint(source)
 consumer=source.replace('applicability_existing_reference_conflict','different_consumer_error_diagnostic')
 assert consumer!=source and svc._protocol_fingerprint(consumer)==original
 assert svc._protocol_fingerprint(source+'\n\ndef unrelated_storage_observer():\n return None\n')==original
 changed_prompt=source.replace('你只审完整当前正文','你重新审完整当前正文',1)
 assert svc._protocol_fingerprint(changed_prompt)!=original
 changed_cap=source.replace('max_tokens_override=6500','max_tokens_override=6501',1)
 assert changed_cap!=source and svc._protocol_fingerprint(changed_cap)!=original
