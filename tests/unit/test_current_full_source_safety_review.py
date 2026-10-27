"""First review must preserve complete sources without multiplying the tool schema."""
from copy import deepcopy
import json
from types import SimpleNamespace
from uuid import uuid4
import pytest
from bestseller.services import draft_continuity as dc, draft_continuity_source_review as review
from bestseller.services import draft_continuity_supplement as source
from bestseller.services.lossless_source_encoding import decode_lossless_source
from bestseller.services.prompt_source_policy import content_hash

pytestmark=pytest.mark.unit

def case():
 body='航海员在冰海边清点玻璃标本。\n\n昨夜谁曾翻过档案，仍待核实。'
 sources={'chapter_sources':{'chapter':{'chapter_number':4}},'foundations':{'book_spec':{'content':{'unknown':'昨夜的档案访问者保持Unknown，不推断已知或未知。'}}},'prior_manuscript_continuity':{'sources':[{'body':'同伴带着封好的标本来到海港。'}]}}
 plan={'scenes':[{'action_sequence':['在同伴见证下清点玻璃标本。']}]}
 ba=dc.build_critic_body_anchors(body);sa=dc.source_catalog(sources)
 report={'schema':review.POLICY,'body_sha256':dc.body_hash(body),'source_sha256':content_hash(sources),'approved_plan_sha256':content_hash(plan),'checked_body_ids':[a['id'] for a in ba],
 'checks':[{'axis':axis,'status':'pass','coverage':'complete','body_evidence':[{'id':ba[-1]['id'],'quote':ba[-1]['text']}],'source_evidence':[{'id':sa[0]['id'],'quote':sa[0]['text']}],'explanation':'按完整当前来源核对；过去访问者仍为Unknown。'} for axis in dc.AXES],
 'blocking_issues':[],'unknown_issues':[],'submission_complete':'complete_current_body_and_applicable_sources_examined'}
 return body,sources,plan,report

def test_axes_named_unique_and_full_source_tool_inventory_only_once():
 body,sources,plan,value=case();before=deepcopy(value)
 assert review.passed(review.parse(json.dumps(value,ensure_ascii=False),body=body,sources=sources,plan=plan))
 assert value==before
 schema=review.tools(body,sources,plan)[0]['function']['parameters']
 assert schema['properties']['checks']['type']=='array'
 serialized=json.dumps(schema)
 for row in dc.source_catalog(sources)+source.plan_catalog(plan):assert serialized.count('"'+row['id']+'"')==1
 assert schema['properties']['checks']['items']['properties']['axis']['enum']==list(dc.AXES)

@pytest.mark.parametrize('fault',['missing','duplicate','old_numeric','bad_late_quote','bad_source','plan_proves_past','coverage','incomplete','false_na','body_hash'])
def test_bad_named_or_quote_evidence_rejects_entire_report(fault):
 body,sources,plan,value=case()
 if fault=='missing':value['checks'].pop()
 if fault=='duplicate':value['checks'][-1]['axis']=value['checks'][0]['axis']
 if fault=='old_numeric':value['checks'][-1]['axis']='5'
 if fault=='bad_late_quote':value['checks'][-1]['body_evidence'][0]['quote']='不在本文的引句'
 if fault=='bad_source':value['checks'][-1]['source_evidence'][0]['id']='S99999'
 if fault=='plan_proves_past':
  row=source.plan_catalog(plan)[0];value['checks'][0]['source_evidence']=[{'id':row['id'],'quote':row['text']}]
 if fault=='coverage':value['checked_body_ids'].pop()
 if fault=='incomplete':value['submission_complete']='almost'
 if fault=='false_na':value['checks'][0]['status']='not_applicable'
 if fault=='body_hash':value['body_sha256']='foreign'
 with pytest.raises(ValueError):review.parse(json.dumps(value,ensure_ascii=False),body=body,sources=sources,plan=plan)

def test_wire_reopens_full_sources_plan_and_body_without_repeating_text_catalogs():
 body,sources,plan,_=case()
 request=review.request_for(project=SimpleNamespace(id=uuid4()),workflow_id=uuid4(),draft=SimpleNamespace(id=uuid4(),content_md=body),sources=sources,plan=plan)
 logical=decode_lossless_source(json.loads(request.user_prompt)['value'])
 assert logical['sources']==sources and logical['approved_current_chapter_task']==plan and logical['body']==body
 assert all('text' not in a for a in logical['body_catalog']+logical['approved_plan_catalog'])
 assert logical['source_catalog']==dc.source_catalog_index(sources)
 for a in logical['body_catalog']:assert body[a['start']:a['end']]
 assert request.max_tokens_override==4000
