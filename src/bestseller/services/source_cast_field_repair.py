"""Model-authored corrections to exact failed typed leaves; no coercion/defaults."""
from copy import deepcopy
import hashlib,json,re

POLICY='source-bound-cast-fields.v1'
TEMPLATE='planner_cast_field_correction'
OUTPUT_TOKENS=8192

def _json(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
def _sha(value):return hashlib.sha256(_json(value).encode()).hexdigest()

def _dereference(schema,root):
 while isinstance(schema,dict) and '$ref' in schema:
  target=root
  for key in schema['$ref'][2:].split('/'):target=target[key.replace('~1','/').replace('~0','~')]
  schema=target
 return schema

def _at_path(schema,root,value,tokens):
 schema=_dereference(schema,root)
 if not tokens:return deepcopy(schema)
 alternatives=schema.get('anyOf') or schema.get('oneOf')
 if alternatives:
  candidates=[_dereference(item,root) for item in alternatives]
  matching=[item for item in candidates if item.get('type')==('object' if isinstance(value,dict) else 'array' if isinstance(value,list) else '')]
  if len(matching)!=1:raise ValueError('cast_field_ambiguous_container')
  return _at_path(matching[0],root,value,tokens)
 token,*rest=tokens
 if isinstance(value,dict) and token in schema.get('properties',{}):
  if token not in value:
   if rest or token not in {'gender','pronoun_set_zh','pronoun_set_en'} or token not in schema.get('required',[]):
    raise ValueError('cast_field_target_not_existing_schema_leaf')
   return deepcopy(schema['properties'][token])
  return _at_path(schema['properties'][token],root,value[token],rest)
 if isinstance(value,list) and token.isdecimal() and int(token)<len(value) and isinstance(schema.get('items'),dict):
  return _at_path(schema['items'],root,value[int(token)],rest)
 raise ValueError('cast_field_target_not_existing_schema_leaf')

def correction_contract(value,schema):
 from bestseller.services.source_bound_planning_submission import wire_diagnostics
 errors=wire_diagnostics(value,schema,schema);properties={}
 if not errors:raise ValueError('cast_field_no_failed_fields')
 for error in errors:
  match=re.fullmatch(r'schema_(?:type|enum):(\$/person/[^:]+)(?::[^:]+)?',error)
  missing=re.fullmatch(r'schema_required:(\$/person):missing=([a-z_,]+)',error)
  if match is None and missing is None:raise ValueError('cast_field_error_not_patchable:'+error)
  if missing is not None:
   keys=missing.group(2).split(',')
   if not keys or len(set(keys))!=len(keys) or not set(keys)<={'gender','pronoun_set_zh','pronoun_set_en'}:
    raise ValueError('cast_field_error_not_patchable:'+error)
   paths=[missing.group(1)+'/'+key for key in keys]
  else:paths=[match.group(1)]
  for path in paths:properties[path]=_at_path(schema,schema,value,path[2:].split('/'))
 paths=sorted(properties)
 if any(other.startswith(path+'/') for path in paths for other in paths if other!=path):
  raise ValueError('cast_field_overlapping_targets')
 return {'type':'object','properties':properties,'required':paths,'additionalProperties':False,'$defs':deepcopy(schema.get('$defs',{}))}

def apply_corrections(value,schema,contract,patch):
 from bestseller.services.source_bound_planning_submission import _validate_wire
 _validate_wire(patch,contract,contract)
 if set(patch)!=set(contract['required']):raise ValueError('cast_field_target_set_changed')
 result=deepcopy(value)
 for path,replacement in patch.items():
  tokens=path[2:].split('/');parent=result
  for token in tokens[:-1]:parent=parent[int(token)] if isinstance(parent,list) else parent[token]
  last=int(tokens[-1]) if isinstance(parent,list) else tokens[-1]
  parent[last]=deepcopy(replacement)
 _validate_wire(result,schema,schema)
 return result

def referenced_definitions_only(contract):
 """Remove unreachable schema definitions from the prompt, never constraints.

 Acceptance still validates the original complete correction contract. This
 projection only avoids repeating unrelated person schemas in model input.
 """
 result=deepcopy(contract)
 definitions=result.pop('$defs',{})
 needed=set();queue=[]
 def visit(value):
  if isinstance(value,dict):
   reference=value.get('$ref')
   if reference is not None:
    if not isinstance(reference,str) or not reference.startswith('#/$defs/'):
     raise ValueError('cast_field_nonlocal_definition_reference')
    name=reference[len('#/$defs/'):].split('/',1)[0].replace('~1','/').replace('~0','~')
    if name not in definitions:raise ValueError('cast_field_missing_definition')
    if name not in needed:needed.add(name);queue.append(name)
   for key,child in value.items():
    if key!='$ref':visit(child)
  elif isinstance(value,list):
   for child in value:visit(child)
 visit(result)
 while queue:visit(definitions[queue.pop()])
 result['$defs']={key:deepcopy(definitions[key]) for key in sorted(needed)}
 return result

def _derived_lineage(value,base_completion,origin):
 from bestseller.services.source_bound_planning_submission import _strict
 if not isinstance(origin,dict) or set(origin)!={'schema','prior_part_receipt'} or origin['schema']!='cast-derived-person-input.v1':
  raise ValueError('cast_field_derived_origin_invalid')
 prior=origin['prior_part_receipt'];base_id=str(base_completion.llm_run_id)
 if not isinstance(prior,dict) or prior.get('llm_run_id')!=base_id or prior.get('payload_sha256')!=_sha(value):
  raise ValueError('cast_field_derived_part_binding')
 raw_json=base_completion.content;raw_origin='provider_text';parsed=None
 try:parsed=_strict(raw_json)
 except ValueError:pass
 if isinstance(parsed,dict) and set(parsed)=={'person'}:
  source_person=parsed
 else:
  from bestseller.services.retained_cast_person import extract
  found=extract(base_completion,name=value['person']['name'])
  if found is None:raise ValueError('cast_field_derived_person_missing')
  source_person,container=found;raw_json=container['container_json'];parsed=_strict(raw_json)
  raw_origin='retained_native_asset_json_not_provider_message'
 if source_person!=value:
  correction=prior.get('field_correction_receipt') or {}
  if (correction.get('schema')!=POLICY or correction.get('base_llm_run_id')!=base_id
      or correction.get('base_payload_sha256')!=_sha(source_person)
      or correction.get('corrected_payload_sha256')!=_sha(value)
      or not correction.get('correction_llm_run_id')):
   raise ValueError('cast_field_derived_correction_chain')
 return {'schema':'cast-derived-person-input.v1','source_base_llm_run_id':base_id,
  'raw_json':raw_json,'raw_json_origin':raw_origin,'raw_payload_sha256':_sha(parsed),
  'original_person_payload_sha256':_sha(source_person),'correction_input_payload_sha256':_sha(value),
  'prior_part_receipt':deepcopy(prior),'not_original_provider_payload':True}

def _lossless_derived_document(document):
 logical_sha=_sha(document);refs=[]
 paths=(
  (('current_part_input','lineage','raw_json'),('retained_part','raw_json')),
  (('current_part_input','lineage','prior_part_receipt'),
   ('identity_completion_contract','prior_part_receipt')),
 )
 def read(path):
  value=document
  for key in path:value=value[key]
  return value
 for location,target in paths:
  try:original=read(location);source=read(target)
  except (KeyError,TypeError):continue
  if original is None or original!=source:continue
  pointer='#/'+('/'.join(target));parent=read(location[:-1])
  parent[location[-1]]={'$ref':pointer}
  refs.append({'at':'#/'+('/'.join(location)),'to':pointer,'value_sha256':_sha(source)})
 if refs:
  document['derived_source_encoding']={
   'schema':'cast-derived-input-local-refs.v1','logical_document_sha256':logical_sha,
   'references':refs,'lossless':True,'truncated':False,
   'instruction':'Resolve these exact local JSON pointers to the complete values already '
                 'included in this document. No source value is omitted or summarized.'}
 return document.get('derived_source_encoding')


async def repair(session,settings,*,project,part_system,original_document,schema,value,base_completion,
                 recipe_hash,part_id,workflow_run_id,before_dispatch,retained_origin=None,derived_input_origin=None):
 from bestseller.services import planner as p
 from bestseller.services.llm import LLMCompletionRequest,complete_text,bind_execution_operation,bind_project_prompt_audit
 from bestseller.services.model_context_capacity import bind_selected_model_capacity
 from bestseller.services.source_bound_planning_content import assert_source_bound_planning_request_capacity
 from bestseller.services.source_bound_planning_submission import _strict,wire_diagnostics
 validation_contract=correction_contract(value,schema)
 contract=referenced_definitions_only(validation_contract) if retained_origin is not None else validation_contract
 document=deepcopy(original_document)
 document.update(schema=POLICY,reference_original_part_contract=deepcopy(schema),part_contract=contract,
  current_part={'part_id':part_id,'instruction':'Return ONLY a JSON object mapping every exact failed path to a correctly typed model-authored replacement. Preserve all non-target fields and story facts. This is a proposal, not an approval.'},
  retained_part={'llm_run_id':str(base_completion.llm_run_id),'raw_json':base_completion.content,'payload_sha256':_sha(value),'approved':False})
 derived=None;derived_encoding=None
 if derived_input_origin is not None:
  if retained_origin is not None:raise ValueError('cast_field_two_input_origins')
  derived=_derived_lineage(value,base_completion,derived_input_origin)
  document['retained_part'].update(raw_json=derived['raw_json'],raw_json_origin=derived['raw_json_origin'],
   payload_sha256=derived['raw_payload_sha256'])
  document['current_part_input']={'payload':deepcopy(value),'payload_sha256':_sha(value),
   'not_original_provider_payload':True,'lineage':deepcopy(derived)}
  derived_encoding=_lossless_derived_document(document)
 if retained_origin is not None:
  # This is an explicit lossless projection of a real native container, never
  # presented as the provider's original single-person text response.
  document['correction_contract_projection']={'original_sha256':_sha(validation_contract),'physical_sha256':_sha(contract),'original_contract_still_validates_return':True,'removed_only_unreachable_definitions':True}
  document['retained_part'].update(raw_json=_json(value),
   raw_json_origin='lossless_json_pointer_projection_not_provider_message',
   projection_receipt=deepcopy(retained_origin['projection_receipt']))
  document['retained_native_container']={
   # Other unreviewed people in the rejected native container are not
   # current edit inputs. Their complete bytes remain in the settled operation,
   # transport receipt and retained payload file; no audit history is removed.
   'container_sha256':hashlib.sha256(retained_origin['container_json'].encode()).hexdigest(),
   'container_operation_key':retained_origin['projection_receipt']['source_operation_key'],
   'projected_person_sha256':_sha(value),
   'prompt_input_selection':'exact_current_person_and_all_frozen_approved_sources',
   'full_native_container_retained_in_audit':True,
   'person_json_pointer':retained_origin['container_pointer'],
   'source_llm_run_id':str(base_completion.llm_run_id),
   'source_finish_reason':base_completion.finish_reason,
   'whole_submission_status':retained_origin['whole_submission_status'],
   'whole_submission_approved':False,'quality_approval_granted':False}
 original_user=_json(document);next_user=original_user
 for attempt in range(2):
  await before_dispatch()
  with bind_selected_model_capacity(project.metadata_json or {}):capacity=assert_source_bound_planning_request_capacity(part_system,next_user,tools=None,output_tokens=OUTPUT_TOKENS)
  await p._heartbeat_planner_workflow(session,workflow_run_id=workflow_run_id,logical_name='cast_fields:'+part_id,phase='llm_started',attempt=attempt+1)
  request=LLMCompletionRequest(logical_role='planner',system_prompt=part_system,user_prompt=next_user,fallback_response='{}',project_id=project.id,workflow_run_id=workflow_run_id,prompt_template=TEMPLATE,prompt_version=POLICY,max_tokens_override=OUTPUT_TOKENS,
   metadata={'project_slug':project.slug,'artifact':'cast_spec','cast_part_id':part_id,'cast_recipe_sha256':recipe_hash,'retained_part_llm_run_id':str(base_completion.llm_run_id),'retained_part_sha256':derived['raw_payload_sha256'] if derived is not None else _sha(value),'field_correction_contract_sha256':_sha(contract),'require_source_complete_capacity':True,**({'derived_person_input_sha256':_sha(value),'not_original_provider_payload':True} if derived is not None else {})})
  with bind_execution_operation('cast-fields:'+str(project.id)+':'+recipe_hash+':'+part_id+':'+_sha(value)),bind_project_prompt_audit(project.id,reason='Model-authored typed field correction; retained paid person proposal and unknown isolation preserved'):
   completion=await complete_text(session,settings,request)
  await p._heartbeat_planner_workflow(session,workflow_run_id=workflow_run_id,logical_name='cast_fields:'+part_id,phase='llm_completed',attempt=attempt+1)
  await session.commit()
  if (completion.provider in {'','mock','fallback','test','fixture','unknown'} or completion.fallback_used or completion.llm_run_id is None or completion.finish_reason not in {'stop','end_turn','completed'} or completion.tool_calls):
   raise ValueError('cast_field_complete_text_receipt_required:'+part_id)
  patch=None
  try:
   patch=_strict(completion.content);result=apply_corrections(value,schema,validation_contract,patch)
  except (ValueError,TypeError) as error:
   if attempt:raise ValueError('cast_field_correction_exhausted:'+part_id) from error
   next_user=original_user+'\n\n'+_json({'correction_only':True,'error':str(error),'wire_diagnostics':wire_diagnostics(patch,contract,contract) if patch is not None else [],'rejected_patch':completion.content,'source_llm_run_id':str(completion.llm_run_id),'instructions':'Correct only the exact patch schema; no extra or omitted paths.'})
   continue
  return result,{'schema':POLICY,'base_llm_run_id':str(base_completion.llm_run_id),'base_payload_sha256':derived['raw_payload_sha256'] if derived is not None else _sha(value),'correction_llm_run_id':str(completion.llm_run_id),'correction_payload_sha256':_sha(patch),'corrected_payload_sha256':_sha(result),'corrected_paths':contract['required'],'physical_capacity':capacity,'quality_approval_granted':False,**({'retained_person_projection':deepcopy(retained_origin['projection_receipt']),'single_provider_payload_claimed':False} if retained_origin is not None else {}),**({'derived_input_lineage':deepcopy(derived),'correction_input_payload_sha256':_sha(value),'not_original_provider_payload':True,'single_provider_payload_claimed':False,'derived_source_encoding':deepcopy(derived_encoding)} if derived is not None else {})}
 raise AssertionError('unreachable bounded Cast field correction')
