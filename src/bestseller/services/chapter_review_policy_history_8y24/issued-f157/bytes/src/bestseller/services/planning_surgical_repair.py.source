"""Versioned full-source patch dispatch; selected capacity and exact custody."""
from copy import deepcopy
from dataclasses import dataclass
import json
import math
from uuid import UUID

from bestseller.services import llm
from bestseller.services import planning_surgical_budget as budget
from bestseller.services.planning_surgical_transport import (
    MARKER, build_source_complete_surgical_prompts, decode_source_complete_surgical_prompts,
)
from bestseller.services.source_complete_retention import (
    MAX_WIRE_CHARACTERS, source_complete_retention_metadata,
)


@dataclass(frozen=True)
class RetainedProtocolFailure:
    """A verified original first return; not permission for another first draw."""
    project_id: UUID
    workflow_run_id: UUID
    scope_id: str
    source_context_sha256: str
    candidate_sha256: str
    effective_input_sha256: str
    llm_run_id: UUID
    content: str
    error: str

    def attempt(self):
        return {'attempt':1,'llm_run_id':str(self.llm_run_id),'error':self.error}

    def feedback(self,outline_payload=None):
        return protocol_failure_feedback(self.attempt(),self.content,outline_payload)


def protocol_failure_feedback(item,content,outline_payload):
    """Show every structural target error; per-field hypothetical grades are ignored."""
    from bestseller.services import planner
    from bestseller.services.source_complete_outline_json import parse_strict_outline_json

    feedback={**deepcopy(item),'rejected_response':content,
        'authority':'untrusted_protocol_failure_not_current_candidate'}
    if outline_payload is None:return feedback
    try:document=parse_strict_outline_json(content)
    except ValueError:return feedback
    rows=document.get('patches')
    if set(document)!={'patches'} or not isinstance(rows,list) or any(
            not isinstance(row,dict) for row in rows):return feedback
    if len(rows)>planner._outline_patch_capacity(outline_payload):return feedback
    groups={}
    prefix='Outline surgical repair returned invalid patch 1: '
    for index,row in enumerate(rows):
        try:planner._apply_outline_commercial_patches(outline_payload,[row],strict=True)
        except (planner.PlannerFallbackError,ValueError,TypeError) as error:
            # A one-field candidate can fail semantic gates until the other
            # atomic patches are applied. Only target/type errors are shared.
            message=str(error)
            if message.startswith(prefix):
                groups.setdefault(message[len(prefix):],[]).append(index+1)
    if groups:feedback['structural_errors']=[{'patch_indices':indices,'error':error}
        for error,indices in groups.items()]
    return feedback


def require_surgical_repair_coverage(outline_payload):
    """Never pay to fix a required field that the selected protocol forbids."""
    from bestseller.services import planner

    protocol=planner._outline_patch_protocol(outline_payload)
    editable=set(protocol['fields']['chapter'])
    if 'title' not in editable and any(not isinstance(chapter.get('title'),str)
            or not chapter['title'].strip() for chapter in outline_payload.get('chapters',[])):
        raise ValueError('typed_domain_recovery_required:uneditable_title')


def feedback_estimated_limit(settings,project,*,logical_name,allowance):
    """Packing precheck only; actual whole-request and money admission still run."""
    from bestseller.services.finite_book_budget import current_capacity
    from bestseller.services.llm_capacity import INPUT_SAFETY_FACTOR
    from bestseller.services.model_context_capacity import payload_capacity

    preview=llm.LLMCompletionRequest(logical_role='planner',project_id=project.id,
        system_prompt='Capacity precheck only; never dispatched.',user_prompt='{}',
        fallback_response='{}',prompt_template=f'planner_{logical_name}_commercial_surgical_repair',
        prompt_version=budget.PROMPT_VERSION,max_tokens_override=allowance['total_output_tokens'],
        metadata={'planning_surgical_output_budget':allowance})
    role=budget.resolve_request_role(settings,project,preview)
    payload=llm._effective_input_payload(preview,role)
    limit=math.floor((current_capacity(model_limit=payload_capacity(payload))
        -payload['max_tokens']*payload['n_candidates'])/INPUT_SAFETY_FACTOR)
    if limit<=0:raise ValueError('surgical_feedback:model_has_no_input_capacity')
    return min(1_000_000,limit)


async def validate_native_patch(session,*,project,logical_name,outline_payload,patches,
        validation_context):
    """Validate the exact native edit without silently replacing any other field."""
    from bestseller.services import planner
    from bestseller.services.prompt_source_policy import content_hash

    candidate=planner._apply_outline_commercial_patches(outline_payload,patches,strict=True)
    titles=await planner._fetch_existing_chapter_titles(session,project.id,
        exclude_volume_number=int(validation_context['volume_number']))
    validated=planner._validate_generated_volume_outline_or_raise(deepcopy(candidate),project=project,
        logical_name=logical_name,volume_number=int(validation_context['volume_number']),
        expected_count=int(validation_context['expected_count']),
        chapter_number_offset=int(validation_context['chapter_number_offset']),
        cast_spec=deepcopy(validation_context['cast_spec']),
        volume_entry=deepcopy(validation_context['volume_entry']),existing_titles=titles,
        strict_story_effects=True,strict_field_degeneracy=True)
    if content_hash(planner._without_planning_meta(validated))!=content_hash(
            planner._without_planning_meta(candidate)):
        paths=[]
        def changed(left,right,path=''):
            if len(paths)>=16 or content_hash(left)==content_hash(right):return
            if isinstance(left,dict) and isinstance(right,dict):
                for key in sorted(left.keys()|right.keys()):
                    pointer=path+'/'+key.replace('~','~0').replace('/','~1')
                    if key not in left or key not in right:paths.append(pointer)
                    else:changed(left[key],right[key],pointer)
            elif isinstance(left,list) and isinstance(right,list) and len(left)==len(right):
                for index,(a,b) in enumerate(zip(left,right,strict=True)):changed(a,b,path+'/'+str(index))
            else:paths.append(path or '/')
        changed(planner._without_planning_meta(candidate),planner._without_planning_meta(validated))
        raise planner.PlannerFallbackError(
            'Source-complete patch requires explicit native edits for validator normalization: '
            +', '.join(paths[:16]))
    return candidate


def build_request(settings,*,project,workflow_run_id,logical_name,outline_payload,
        directives,source_context,protocol_feedback=None):
    from bestseller.services import planner
    from bestseller.services.outline_llm_judge import (
        OUTLINE_SOURCE_AUTHORITY_RULES, source_bound_outline_repair_directives,
    )
    brief=deepcopy(source_context['project_source_brief'])
    effective_directives=(source_bound_outline_repair_directives(directives)
        if planner._has_current_world_authority(brief) else list(directives))
    allowance=budget.resolve_output_budget(settings,project,logical_name=logical_name,
        answer_tokens=min(12000,max(5000,planner._outline_patch_capacity(outline_payload)*250)))
    estimated_limit=(feedback_estimated_limit(settings,project,logical_name=logical_name,
        allowance=allowance) if protocol_feedback else 60_000)
    base='你是小说章纲的定点修订编辑。只输出合法 JSON patch，不解释，禁止重写整份章纲。'+OUTLINE_SOURCE_AUTHORITY_RULES
    system,user,transport=build_source_complete_surgical_prompts(system_prompt=base,
        project_brief=brief,directives={'repair_directives':list(effective_directives),
            'protocol_feedback':deepcopy(protocol_feedback or [])},
        candidate_payload=planner._without_planning_meta(deepcopy(outline_payload)),
        patch_protocol=planner._outline_patch_protocol(outline_payload),
        source_context=deepcopy(source_context),max_chars=MAX_WIRE_CHARACTERS,
        max_estimated_tokens=estimated_limit)
    request=llm.LLMCompletionRequest(logical_role='planner',system_prompt=system,user_prompt=user,
        fallback_response='{"patches":[]}',prompt_template=f'planner_{logical_name}_commercial_surgical_repair',
        prompt_version=budget.PROMPT_VERSION,project_id=project.id,workflow_run_id=workflow_run_id,
        max_tokens_override=allowance['total_output_tokens'],metadata={
            'project_slug':project.slug,'artifact':logical_name,
            'repair_kind':'outline_commercial_surgical_repair',
            'planning_surgical_output_budget':allowance,'planning_surgical_source_transport':transport})
    wire=json.loads(user[len(MARKER):])
    packet=json.dumps(wire['payload'],ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
    if user.count(packet)!=1:raise ValueError('surgical_source_packet_ambiguous')
    start=user.index(packet)
    role=budget.resolve_request_role(settings,project,request)
    logical=decode_source_complete_surgical_prompts(system_prompt=system,user_prompt=user,
        transport_receipt=transport,max_chars=MAX_WIRE_CHARACTERS,
        max_estimated_tokens=estimated_limit)
    request.metadata['source_complete_retention']=source_complete_retention_metadata(request,
        role=role,logical_source=logical,packet_start=start,packet_end=start+len(packet))
    return request


async def repair(session,settings,*,project,workflow_run_id,logical_name,outline_payload,
        directives,source_context,validation_context,source_report=None,retained_failure=None):
    from bestseller.services import planner
    from bestseller.services.budget import ExecutionBudgetError

    if not validation_context:raise ValueError('source_complete_patch_validation_required')
    feedback=[];attempts=[];completion=None;start=0
    if retained_failure is not None:
        from bestseller.services.budget import current_execution_scope
        from bestseller.services.prompt_source_policy import content_hash

        if not isinstance(retained_failure,RetainedProtocolFailure):
            raise ValueError('retained_surgical_failure:verified_type_required')
        first=build_request(settings,project=project,workflow_run_id=workflow_run_id,
            logical_name=logical_name,outline_payload=outline_payload,directives=directives,
            source_context=source_context)
        role=budget.resolve_request_role(settings,project,first)
        if not (retained_failure.project_id==project.id
                and retained_failure.workflow_run_id==workflow_run_id
                and retained_failure.scope_id==current_execution_scope()
                and retained_failure.source_context_sha256==content_hash(source_context)
                and retained_failure.candidate_sha256==content_hash(
                    planner._without_planning_meta(outline_payload))
                and retained_failure.effective_input_sha256==llm._effective_input_hash(first,role)
                and isinstance(retained_failure.llm_run_id,UUID)
                and isinstance(retained_failure.content,str)
                and isinstance(retained_failure.error,str) and 0<len(retained_failure.error)<=1200):
            raise ValueError('retained_surgical_failure:current_binding_changed')
        feedback=[retained_failure.feedback(outline_payload)];attempts=[retained_failure.attempt()];start=1
    for index in range(start,2):
        request=build_request(settings,project=project,workflow_run_id=workflow_run_id,
            logical_name=logical_name,outline_payload=outline_payload,directives=directives,
            source_context=source_context,protocol_feedback=feedback)
        completion=await llm.complete_text(session,settings,request)
        if (completion.llm_run_id is None or completion.provider.lower() in
                {'','mock','fallback','placeholder','fixture','test'} or completion.fallback_used):
            raise planner.PlannerFallbackError('Source-complete patch requires a real paid response.')
        if completion.finish_reason=='length':
            # Repeating a saturated allowance with another long request cannot
            # repair JSON. Keep its charge/raw return and stop this protocol.
            raise planner.PlannerFallbackError('Source-complete patch capacity exhausted; no same-cap retry.')
        if completion.finish_reason not in {'stop','end_turn','completed'} or completion.tool_calls:
            raise planner.PlannerFallbackError('Source-complete patch has no complete native JSON return.')
        try:
            from bestseller.services.source_complete_outline_json import parse_strict_outline_json
            patch=parse_strict_outline_json(completion.content)
            if set(patch)!={'patches'}:
                raise planner.PlannerFallbackError('Source-complete patch requires exactly the patches field.')
            rows=patch.get('patches')
            if not isinstance(rows,list) or any(not isinstance(row,dict) for row in rows):
                raise planner.PlannerFallbackError('Patches must be an array of objects.')
            candidate=await validate_native_patch(session,project=project,logical_name=logical_name,
                outline_payload=outline_payload,patches=rows,validation_context=validation_context)
            break
        except ExecutionBudgetError:raise
        except (planner.PlannerFallbackError,ValueError,TypeError) as error:
            item={'attempt':index+1,'llm_run_id':str(completion.llm_run_id),'error':str(error)[:1200]}
            attempts.append(item)
            feedback.append(protocol_failure_feedback(item,completion.content,outline_payload))
            if index==1:raise
    candidate['_meta']={**deepcopy(outline_payload.get('_meta') or {}),
        'surgical_repair':True,'surgical_llm_run_id':str(completion.llm_run_id),
        'workflow_run_id':str(workflow_run_id),'surgical_repair_receipt':{
            'before_candidate':planner._without_planning_meta(deepcopy(outline_payload)),
            'after_candidate_hash':planner._approved_payload_hash(planner._without_planning_meta(candidate)),
            'source_report':deepcopy(source_report or {}),'llm_run_id':str(completion.llm_run_id),
            'protocol_attempts':attempts,'source_transport':request.metadata['planning_surgical_source_transport'],
            'protocol_version':budget.PROMPT_VERSION}}
    return candidate,completion.llm_run_id
