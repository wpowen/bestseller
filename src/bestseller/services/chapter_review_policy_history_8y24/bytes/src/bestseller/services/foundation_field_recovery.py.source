"""Read-only custody for composite native planning, never a single paid grade."""
from copy import deepcopy
import logging
import re
from uuid import UUID

from sqlalchemy import select

from bestseller.infra.db.models import ExecutionAttemptModel, LlmRunModel, WorkflowRunModel
from bestseller.services import llm, planning_schema_recovery as recovery

SCHEMA='foundation-native-field-recovery.v1'
METHOD='retained_native_plus_model_field_corrections'
logger=logging.getLogger(__name__)


def _need(ok,reason):
    if not ok:raise ValueError('foundation_field_recovery:'+reason)


async def _paid(session,settings,project,run_id):
    from bestseller.services import retained_review_input_proof as proof, retained_planning_generation as physical
    from bestseller.services.foundation_selection import _row_hash
    run=await session.get(LlmRunModel,UUID(str(run_id)))
    _need(run is not None and run.project_id==project.id and run.logical_role=='planner','run_owner')
    request_doc,request_sha=physical._file(settings,run,'request')
    response,response_sha=physical._file(settings,run,'response')
    request=proof._retained_request(settings,run)
    catalog,_=proof._fresh_catalog()
    role=llm._apply_conception_phase_settings(request,proof._fresh_role(settings,project,request,catalog))
    digest=llm._effective_input_hash(request,role)
    scope=(project.metadata_json or {}).get('execution_scope_id')
    completion,key=await proof._physical_return(session,run,scope=scope,expected_hash=digest,expected_model=role.model)
    physical._response(response,completion)
    owner=await session.get(WorkflowRunModel,run.workflow_run_id)
    attempts=list(await session.scalars(select(ExecutionAttemptModel).where(ExecutionAttemptModel.operation_key==key)))
    _need(owner is not None and owner.project_id==project.id
        and (owner.metadata_json or {}).get('execution_scope_id')==scope,'workflow_scope')
    _need(len(attempts)==1 and attempts[0].status=='completed'
        and completion.input_tokens==run.input_tokens and completion.output_tokens==run.output_tokens
        and attempts[0].actual_tokens==run.input_tokens+run.output_tokens,'settlement')
    for receipt in ((run.metadata_json or {}).get('effective_input_receipt'),completion.metadata.get('effective_input_receipt')):
        _need(isinstance(receipt,dict) and receipt.get('schema')=='effective-input.v1'
            and receipt.get('hash')==digest and receipt.get('model')==role.model,'effective_receipt')
    return run,request,completion,{'llm_run_id':str(run.id),'effective_input_sha256':digest,
        'request_file_sha256':request_sha,'response_file_sha256':response_sha,'llm_row_sha256':_row_hash(run),
        'operation_key':key,'attempt_rows_sha256':recovery._sha([_row_hash(a) for a in attempts])}


def _validate(payload,*,project,projection):
    from bestseller.services import planner as p
    from bestseller.services.source_bound_planning_content import validate_source_bound_planning_payload
    clean=p._without_planning_meta(payload)
    p._assert_planner_payload_has_content('volume_plan',clean)
    validate_source_bound_planning_payload(clean,kind='volume_plan',project=project,projection=projection)
    p._volume_plan_validator_for(project)(clean)
    p._validate_planner_creation_intent_payload(clean,project=project,logical_name='volume_plan')


async def verify_recovered_volume(session,settings,*,project,artifacts,projection):
    """Bind both genuine paid returns to the selected approved source cohort."""
    with session.no_autoflush:
        return await _verify(session,settings,project=project,artifacts=artifacts,projection=projection)


async def _verify(session,settings,*,project,artifacts,projection):
    from bestseller.services import planner as p
    from bestseller.services.budget import bind_execution_scope
    from bestseller.services.model_context_capacity import bind_selected_model_capacity
    from bestseller.services.source_bound_planning_content import build_source_bound_planning_prompts,_dependency_projection
    from bestseller.services.planning_field_corrections import apply_corrections
    from bestseller.services.rejected_world_restore import approved_artifact_report
    from bestseller.services.planning_correction_input_binding import (
        decode_correction_input, prove_capacity_audit_equivalence,
    )

    _need(set(artifacts)=={'book_spec','world_spec','cast_spec','volume_plan'},'asset_set')
    for kind,row in artifacts.items():
        _need(row.project_id==project.id and row.artifact_type==kind and row.status=='approved','asset_owner')
    volume=artifacts['volume_plan']
    rows=volume.content
    _need(isinstance(rows,list) and bool(rows),'volume_shape')
    metas=[row.get('_meta') or {} for row in rows]
    receipt=metas[0].get('field_recovery_receipt')
    _need(isinstance(receipt,dict) and receipt.get('schema')==recovery.POLICY,'receipt')
    for meta in metas:
        _need(recovery._sha(meta.get('field_recovery_receipt'))==recovery._sha(receipt) and meta.get('generation_method')==METHOD
            and meta.get('single_provider_payload_claimed') is False
            and meta.get('generation_llm_run_ids')==[receipt.get('original_llm_run_id'),receipt.get('patch_llm_run_id')],
            'composite_lineage')
    base_run,base_request,_,base_paid=await _paid(session,settings,project,receipt.get('original_llm_run_id'))
    _need(base_run.prompt_template=='planner_volume_plan' and base_run.prompt_version=='v4-volume-native','base_protocol')
    old_logical,_,_=recovery.logical_source(base_request.user_prompt)
    book,world,cast=(deepcopy(artifacts[k].content) for k in ('book_spec','world_spec','cast_spec'))
    old_world=(old_logical.get('existing_planning_assets') or {}).get('world_spec') or {}
    old_meta=old_world.get('_meta') or {}
    compatibility=[]
    if 'reviewed_world_reuse_artifact_id' in old_meta or 'paid_current_input_asserted' in old_meta:
        _need(old_meta.get('reviewed_world_reuse_artifact_id')==str(artifacts['world_spec'].id)
            and old_meta.get('paid_current_input_asserted') is False,'world_reuse_identity')
        baseline=p._compile_source_bound_world_spec(project,project.metadata_json['premise'],book)
        report=await approved_artifact_report(session,project,book=book,baseline=baseline,
            candidate=world,artifact_id=str(artifacts['world_spec'].id))
        _need(report is not None,'world_reuse_approval')
        world['_meta']={**(world.get('_meta') or {}),'reviewed_world_reuse_artifact_id':str(artifacts['world_spec'].id),
            'paid_current_input_asserted':False}
        compatibility=['/existing_planning_assets/world_spec/_meta/reviewed_world_reuse_artifact_id',
                       '/existing_planning_assets/world_spec/_meta/paid_current_input_asserted']
    _need(recovery._sha(_dependency_projection(world))==recovery._sha(old_world),'world_source_changed')
    expected_projection=p._compile_source_bound_volume_plan(project,project.metadata_json['premise'],book,world,cast)
    with bind_selected_model_capacity(project.metadata_json),bind_execution_scope(project.metadata_json['execution_scope_id']):
        system,user=build_source_bound_planning_prompts(kind='volume_plan',project=project,
            premise=project.metadata_json['premise'],projection=expected_projection,book_spec=book,world_spec=world,cast_spec=cast)
        source=await recovery.find_retained_source(session,settings,project=project,user=user,source_run_id=base_run.id)
        _need(source is not None and source.proof['original_llm_run_id']==str(base_run.id),'source_binding')
        current,_,_=recovery.logical_source(user)
        first_user,contract,_=recovery.prepare_correction(source,current)
    patch_run,patch_request,paid,patch_proof=await _paid(session,settings,project,receipt.get('patch_llm_run_id'))
    _need(patch_run.prompt_template==recovery.TEMPLATE and patch_run.prompt_version==recovery.PROMPT_VERSION
        and patch_run.workflow_run_id==volume.source_run_id and patch_request.system_prompt==recovery.SYSTEM
        and not patch_request.tools and patch_request.max_tokens_override==recovery.OUTPUT_TOKENS
        and not paid.tool_calls and paid.finish_reason in {'stop','end_turn','completed'},'patch_protocol')
    expected_user=first_user
    retry_proof=None
    input_validation_receipts=[]
    historical_source_proof=deepcopy(source.proof)
    paid_document=None
    if patch_request.user_prompt!=first_user:
        paid_document=decode_correction_input(
            patch_request.user_prompt,wire_instruction=recovery.WIRE_INSTRUCTION)
    # A different serialization or measurement is not evidence of a retry.
    # Only the actual retained producer field may identify a second response.
    if paid_document is not None and 'rejected_patch_for_correction_only' in paid_document['planning_document']:
        document=paid_document['planning_document']
        retry=document['rejected_patch_for_correction_only']
        _need(isinstance(retry,dict) and set(retry)=={'raw_json','error','source_llm_run_id'},'retry_receipt')
        previous,prior_request,prior_paid,retry_proof=await _paid(session,settings,project,retry['source_llm_run_id'])
        _need(previous.workflow_run_id==patch_run.workflow_run_id and previous.prompt_template==recovery.TEMPLATE
            and previous.prompt_version==recovery.PROMPT_VERSION and prior_request.system_prompt==recovery.SYSTEM
            and not prior_request.tools
            and prior_paid.content==retry['raw_json'],'retry_source')
        if prior_request.user_prompt!=first_user:
            proof=prove_capacity_audit_equivalence(prior_request.user_prompt,first_user,
                wire_instruction=recovery.WIRE_INSTRUCTION)
            _need(proof is not None,'retry_source_input')
            input_validation_receipts.append(proof)
        try:
            prior_patch=recovery._strict(prior_paid.content)
            prior_result,_=apply_corrections(source.value,source.schema,contract,prior_patch)
            recovery.assert_representation_only(source.value,prior_patch)
            _validate(prior_result['volumes'],project=project,projection=expected_projection)
        except (ValueError,TypeError) as error:
            _need(str(error)==retry['error'],'retry_error')
        else:raise ValueError('foundation_field_recovery:retry_was_valid')
        with bind_selected_model_capacity(project.metadata_json):
            expected_user,_,_=recovery.prepare_correction(source,current,retry=retry)
    if patch_request.user_prompt!=expected_user:
        proof=prove_capacity_audit_equivalence(patch_request.user_prompt,expected_user,
            wire_instruction=recovery.WIRE_INSTRUCTION)
        _need(proof is not None,'patch_entire_input')
        input_validation_receipts.append(proof)
        # The historical receipt described the historical complete input. Keep
        # that original derived hash; the helper independently recomputes both
        # hashes and proves all source/contract data unchanged.
        historical_source_proof['current_full_logical_sha256']=(
            paid_document['planning_document']['origin']['current_full_logical_sha256'])
    patch=recovery._strict(paid.content)
    corrected,derived=apply_corrections(source.value,source.schema,contract,patch)
    recovery.assert_representation_only(source.value,patch)
    _validate(corrected['volumes'],project=project,projection=expected_projection)
    original_metas=[{'present':'_meta' in row,'value':deepcopy(row.get('_meta'))} for row in corrected['volumes']]
    stored=[{k:deepcopy(v) for k,v in row.items() if k!='_meta'} for row in rows]
    _need(len(stored)==len(original_metas),'volume_count')
    for row,old in zip(stored,original_metas,strict=True):
        if old['present']:row['_meta']=old['value']
    _need(recovery._sha(stored)==recovery._sha(corrected['volumes'])
        and recovery._sha(receipt.get('original_volume_metadata'))==recovery._sha(original_metas),'composite_body')
    for key,value in {**derived,**historical_source_proof,'schema':recovery.POLICY,'patch_origin_verified':True}.items():
        _need(recovery._sha(receipt.get(key))==recovery._sha(value),'derived_receipt:'+key)
    _need(receipt.get('patch_origin_verified') is True and receipt.get('representation_only_verified') is True,'verification_flags')
    expected_hash=p._planner_artifact_input_hash(logical_name='volume_plan',system_prompt=system,
        user_prompt=user.rstrip()+p._STRUCTURED_FUNCTION_SUFFIX,fallback_payload=expected_projection)
    if not all(meta.get('input_hash')==expected_hash for meta in metas):
        # This key is a producer cache identity, not a fresh physical request.
        # Its original paid row is already bound above to exact retained files,
        # the effective input, all charged attempts and the selected source.
        old_key=(base_run.metadata_json or {}).get('input_hash')
        _need(input_validation_receipts and isinstance(old_key,str)
            and re.fullmatch('[0-9a-f]{64}',old_key)
            and all(meta.get('input_hash')==old_key for meta in metas),
            'logical_cache_binding')
        input_validation_receipts.append({
            'schema':'foundation-retained-cache-binding.v1',
            'historical_cache_key':old_key,'current_render_cache_key':expected_hash,
            'original_paid_run_id':str(base_run.id),'original_paid_row_sha256':base_paid['llm_row_sha256'],
            'normalized_source_sha256':source.proof['current_logical_source_sha256'],
            'old_cache_key_rewritten':False,'grade_granted':False,'credit_granted':False,
        })
    _need(all('source_projection_sha256' not in meta
        or meta['source_projection_sha256']==p._approved_payload_hash(expected_projection) for meta in metas),
        'producer_projection_changed')
    selected_projection=p._compile_source_bound_volume_plan(project,project.metadata_json['premise'],
        *(deepcopy(artifacts[k].content) for k in ('book_spec','world_spec','cast_spec')))
    _need(recovery._sha(selected_projection)==recovery._sha(projection),'selected_projection_changed')
    for proof in input_validation_receipts:
        # A separate current validation, never a mutation of the old paid
        # receipt or pinned foundation identity. Probes retain the same proof.
        logger.info('foundation_retained_input_validation: %s',recovery._json(proof))
    return {'schema':SCHEMA,'original_paid':base_paid,'correction_paid':patch_proof,
        'retry_paid':retry_proof,'immutable_composite_receipt_sha256':recovery._sha(receipt),
        'raw_payload_sha256':derived['raw_payload_sha256'],'corrected_payload_sha256':derived['result_payload_sha256'],
        'source_logical_sha256':source.proof['current_logical_source_sha256'],
        'original_projection_sha256':recovery._sha(expected_projection),'selected_projection_sha256':recovery._sha(projection),
        'verified_producer_metadata_paths':compatibility,'author_authority':False,'grade_asserted':False,
        'historical_projection_receipt_missing':all('source_projection_sha256' not in meta for meta in metas),
        'credit_granted':False,'single_provider_payload_claimed':False}
