"""Path front coding preserves complete logical sources and original list order."""
from copy import deepcopy
import json
import pytest
from bestseller.services import planning_catalog_transport as codec
from bestseller.services.lossless_source_encoding import decode_lossless_source,encode_lossless_source
pytestmark=pytest.mark.unit


def example():
 return {'input':{'source_authority':{'advisory':{'/approved_cast_spec':['/team/10/history/晚期~1条件','/team/2/history/先期~0条件','/team/2/history/unknown','/team/2/history/unknown','/another']},'world_rule':{'/approved_world_spec':[]}},
 'approved_cast_spec':{'team':[{'literal':'完整角色字段及尾部否定：这只是候选，尚未发生。','unknown':{'$ref':'/literal'},'types':[None,False,0,1,1.0,[],{}]}]},
 'candidate_payload':{'chapters':[{'chapter_number':8,'unknown_authored':['保留原顺序乙','甲']}]},'unknown_extension':{'§0':'§literal','$ref':0}},'schema':'existing-assessment','graded_input_hash':'original'}


def test_exact_roundtrip_including_unordered_duplicates_escaped_paths_and_full_unknown_values():
 data=example();before=deepcopy(data);wire=codec.encode(data)
 assert codec.decode(wire)==before==data
 assert codec.canonical(codec.decode(wire))==codec.canonical(before)
 assert wire['original_sha256']==codec.digest(before)
 packed=decode_lossless_source(wire['value'])
 assert packed['input']['approved_cast_spec']==before['input']['approved_cast_spec']
 assert packed['input']['candidate_payload']==before['input']['candidate_payload']
 assert packed['input']['source_authority']!=before['input']['source_authority']
 assert codec.encode(data)==wire

@pytest.mark.parametrize('fault',['bool','negative','past_end','suffix','role','order','type','hash','extra'])
def test_invalid_catalog_or_tampered_repaired_inner_hash_never_passes(fault):
 wire=codec.encode(example());packed=decode_lossless_source(wire['value']);rows=packed['input']['source_authority']['advisory']['/approved_cast_spec']
 if fault=='bool':rows[0][0]=True
 if fault=='negative':rows[0][0]=-1
 if fault=='past_end':rows[0][0]=100
 if fault=='suffix':rows[0][1]+='foreign'
 if fault=='role':packed['input']['source_authority']['world_rule']['/approved_cast_spec']=packed['input']['source_authority']['advisory'].pop('/approved_cast_spec')
 if fault=='order':rows.reverse()
 if fault=='type':rows[0][1]=None
 if fault=='hash':wire['original_sha256']='0'*64
 if fault=='extra':wire['unreviewed']='extra'
 wire['value']=encode_lossless_source(packed,version=3)
 with pytest.raises(ValueError):codec.decode(wire)


def test_repeated_structural_prefixes_are_compressed_without_truncation():
 data=example();paths=[f'/supporting_cast/{i//20}/unknown_authored_history/complete_clause_{i%20}' for i in range(400)]
 data['input']['source_authority']['advisory']['/approved_cast_spec']=paths
 wire=codec.encode(data)
 assert codec.decode(wire)['input']['source_authority']['advisory']['/approved_cast_spec']==paths
 assert len(json.dumps(wire,ensure_ascii=False))<len(json.dumps(data,ensure_ascii=False))


def test_real_transport_falls_back_to_front_coding_with_full_opaque_sources_and_output():
 from bestseller.services.planning_review_transport import prepare_planning_review_transport
 data=example()
 data['input']['approved_cast_spec']['opaque_unknown_clause']=''.join(chr(0x4e00+i) for i in range(18000))
 data['input']['source_authority']['advisory']['/approved_cast_spec']=[f'/supporting_cast/{i//20:04}/unknown_authored_history/complete_condition_for_actual_prior_state/{i%20:02}' for i in range(1000)]
 block=json.dumps(data,ensure_ascii=False,separators=(',',':'));user='KEEP BEFORE\n'+block+'\nKEEP AFTER'
 rendered,receipt=prepare_planning_review_transport('Audit full sources only.',user,data=data,data_block=block,tools=[{'description':'The complete native submission schema.'}],output_tokens=6000)
 assert receipt['schema']=='planning-review-transport.v4' and receipt['encoding']=='segment-front-coded-authority-pointers'
 assert rendered.startswith('KEEP BEFORE\n') and rendered.endswith('\nKEEP AFTER')
 encoded=rendered.split('source_review_data=',1)[1].removesuffix('\nKEEP AFTER')
 assert codec.decode(json.loads(encoded))==data
 assert receipt['request_capacity']['effective_max_output_tokens']==6000
 assert receipt['request_capacity']['budget_passed'] and receipt['request_capacity']['total_budget']==65536
 assert receipt['source_classification_and_order_preserved'] and receipt['roundtrip_verified']


def test_indexed_segment_catalog_keeps_all_original_order_and_v1_decoder():
 data=example();old=codec.encode(data);new=codec.encode(data,version=2)
 assert new['schema']==codec.INDEXED_POLICY and codec.decode(new)==data
 assert codec.decode(old)==data and codec.encode(data)==old
 assert new['original_sha256']==old['original_sha256']
 packed=decode_lossless_source(new['value']);rows=packed['input']['source_authority']['advisory']['/approved_cast_spec']
 rows[0][1]=[True];new['value']=encode_lossless_source(packed,version=3)
 with pytest.raises(ValueError,match='segment_indices'):codec.decode(new)
