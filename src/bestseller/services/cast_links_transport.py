"""Lossless overflow wire for the complete Cast-links part; no new story data."""
from copy import deepcopy
import hashlib,json
from sqlalchemy import select
from bestseller.services.lossless_source_encoding import encode_lossless_source,decode_lossless_source
from bestseller.services.source_bound_planning_content import assert_source_bound_planning_request_capacity,SourceBoundPlanningCapacityError

POLICY='cast-links-transport.v1'
NOTICE=('CAST LINKS TRANSPORT: Restore every original key and value from the lossless source envelope. '
 'For v3 restore object keys using key_aliases inside value, then expand the indexed JSON Pointer container. '
 'For v1/v2 expand ONLY declared references paths relative to value using their targets; unlisted authored $ref objects remain literal data. '
 'Preserve all six people, array order, every frozen source, candidate field and original retry suffix. '
 'Return normal field names from the current_part_contract, never aliases or reference placeholders. '
 'Encoding grants no source authority, approval, deletion or rewrite permission.')

def _json(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
def _sha(text):return hashlib.sha256(text.encode()).hexdigest()
def _pairs(items):
 result={}
 for key,value in items:
  if key in result:raise ValueError('cast_links_duplicate_json_key')
  result[key]=value
 return result

def split(user):
 start=len(user)-len(user.lstrip());data,end=json.JSONDecoder(object_pairs_hook=_pairs,parse_constant=lambda value:(_ for _ in ()).throw(ValueError('cast_links_nonfinite_json'))).raw_decode(user,start)
 if not isinstance(data,dict):raise ValueError('cast_links_document_required')
 _json(data)
 return data,user[end:]

def decode(user):
 wrapper,suffix=split(user)
 if (set(wrapper)!={'schema','expansion_instruction','logical_document','current_part_contract'}
     or wrapper['schema']!=POLICY or wrapper['expansion_instruction']!=NOTICE):
  raise ValueError('cast_links_envelope_invalid')
 document=decode_lossless_source(wrapper['logical_document'])
 if not isinstance(document,dict) or document.get('part_contract')!=wrapper['current_part_contract']:
  raise ValueError('cast_links_contract_changed')
 return document,suffix

def prepare(system,user,*,schema,output_tokens):
 try:
  capacity=assert_source_bound_planning_request_capacity(system,user,tools=None,output_tokens=output_tokens)
  return user,{'schema':POLICY,'encoding':'identity','logical_user_sha256':_sha(user),'wire_user_sha256':_sha(user),'roundtrip_verified':True,'capacity':capacity}
 except SourceBoundPlanningCapacityError:pass
 document,suffix=split(user)
 if (document.get('schema')!='source-bound-cast-parts.v1' or document.get('part_contract')!=schema
     or not isinstance(document.get('already_generated_people'),list)
     or not isinstance(document.get('frozen_source_packet'),dict)
     or not isinstance(document.get('current_part'),dict)):
  raise ValueError('cast_links_logical_document_invalid')
 for version in (1,2,3):
  encoded=encode_lossless_source(document,version=version)
  if decode_lossless_source(encoded)!=document:raise ValueError('cast_links_roundtrip_mismatch')
  wrapper={'schema':POLICY,'expansion_instruction':NOTICE,'logical_document':encoded,'current_part_contract':deepcopy(schema)}
  # Keep the currently required response shape at the model's decision boundary.
  wire=_json({key:value for key,value in wrapper.items() if key!='current_part_contract'})[:-1]+',"current_part_contract":'+_json(schema)+'}'+suffix
  restored,restored_suffix=decode(wire)
  if restored!=document or restored_suffix!=suffix:raise ValueError('cast_links_roundtrip_mismatch')
  try:capacity=assert_source_bound_planning_request_capacity(system,wire,tools=None,output_tokens=output_tokens)
  except SourceBoundPlanningCapacityError:
   if version==3:raise
   continue
  return wire,{'schema':POLICY,'encoding':encoded['schema'],'logical_user_sha256':_sha(user),'logical_document_sha256':_sha(_json(document)),'wire_user_sha256':_sha(wire),'suffix_sha256':_sha(suffix),'roundtrip_verified':True,'all_sources_and_people_preserved':True,'capacity':capacity}
 raise AssertionError('unreachable finite codec selection')

async def bind_logical_identity(session,settings,*,project,request,original_user,receipt):
 """Wire changes cannot create another paid identity for the same logical unit."""
 if receipt['encoding']=='identity':return request
 from bestseller.services import llm
 from bestseller.services.retained_review_input_proof import _fresh_catalog,_fresh_role
 from bestseller.services.budget import current_execution_scope,execution_operation_id,ExecutionBudgetError
 from bestseller.infra.db.models import ExecutionOperationModel
 original=request.model_copy(update={'user_prompt':original_user})
 role=llm._apply_conception_phase_settings(original,_fresh_role(settings,project,original,_fresh_catalog()[0]))
 logical_hash=llm._effective_input_hash(original,role)
 scope=current_execution_scope()
 if scope:
  legacy=list(await session.scalars(select(ExecutionOperationModel).where(ExecutionOperationModel.scope_id==scope,
   ExecutionOperationModel.input_hash==logical_hash,ExecutionOperationModel.status.in_(['pending','unknown','stopped','completed']))))
  if legacy:raise ExecutionBudgetError('execution_operation_stopped',scope)
 operation_id=execution_operation_id('cast-links-logical-input.v1',subject=str(project.id)+':'+str(request.metadata['cast_recipe_sha256']),revision=logical_hash)
 physical_hash=llm._effective_input_hash(request,role)
 return request.model_copy(update={'operation_id':operation_id,'sample_slot':0,
  'metadata':{**request.metadata,'cast_links_transport':receipt,'logical_effective_input_hash':logical_hash,
   'prepared_effective_input':{'schema':'effective-input.v1','hash':physical_hash}}})
