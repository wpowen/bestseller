"""Source input adapter for the existing hard-admission protocol, real volumes."""
from copy import deepcopy
from uuid import UUID
from sqlalchemy import select
from bestseller.infra.db.models import (
    WorkflowRunModel,PlanningArtifactVersionModel,ChapterDraftVersionModel,CanonFactModel,TimelineEventModel,
)
from bestseller.services.prompt_source_policy import content_hash


async def build_volume_admission_input(session, *, project, workflow_run_id, candidate_payload,
        book_spec, world_spec, cast_spec, commercial_report, check_initial_context=False, settings=None):
    from bestseller.services import planning_draft_admission as admission, planner
    from bestseller.services.foundation_revision_selection import validate_volume_foundation_components
    from bestseller.services.creation_contract_authority import select_creation_contracts
    need = admission._need
    workflow = await session.get(WorkflowRunModel, workflow_run_id)
    need(workflow is not None and workflow.project_id == project.id
        and workflow.workflow_type == 'review_volume_source_admission', 'admission_volume_workflow_scope')
    meta = workflow.metadata_json
    need(meta.get("source_boundary_policy")=="reference-only.v1" and meta.get("single_dispatch_authorization_id"), "admission_volume_limited_authorization_required")
    revision = await session.get(WorkflowRunModel, UUID(meta['source_dependency_revision_id']))
    volume = await session.get(PlanningArtifactVersionModel, UUID(meta['volume_candidate_id']))
    need(revision is not None and volume is not None, 'admission_volume_source_missing')
    need(settings is not None, 'admission_volume_effective_settings_required')
    components = await validate_volume_foundation_components(session, settings,
        project=project,revision=revision,volume=volume)
    from bestseller.services.foundation_catalog_transition import verified_component_identity
    component_identity = await verified_component_identity(session,
        project=project, review=workflow, components=components)
    need(component_identity == meta['source_component_receipt_hash'], 'admission_volume_component_receipt_changed')
    volumes = [planner._without_planning_meta(v) for v in volume.content]
    need(set(candidate_payload) == {'volumes'} and candidate_payload['volumes'] == volumes,
        'admission_volume_candidate_changed')
    need(content_hash(volume.content) == meta['volume_content_hash'], 'admission_volume_candidate_changed')
    sources = {}
    for kind, supplied in (('book_spec',book_spec),('world_spec',world_spec),('cast_spec',cast_spec)):
        row = await session.get(PlanningArtifactVersionModel, UUID(components['sources'][kind]['artifact_id']))
        need(row is not None and row.project_id == project.id and row.content == supplied,
            'admission_volume_component_changed')
        sources[kind] = components['sources'][kind]
    if check_initial_context:
        for model, extra in ((ChapterDraftVersionModel,[ChapterDraftVersionModel.promotion_state.in_(['promoted','accepted_with_debt'])]),
                (CanonFactModel,[CanonFactModel.is_current.is_(True)]),(TimelineEventModel,[])):
            need(await session.scalar(select(model.id).where(model.project_id==project.id,*extra).limit(1)) is None,
                'admission_volume_initial_context_changed')
    canonical = await session.get(PlanningArtifactVersionModel, UUID(components['canonical_snapshot_id']))
    source_root_id = revision.metadata_json['candidate_origin_revision_ids']['cast_spec']
    source_root = await session.get(WorkflowRunModel, UUID(source_root_id))
    controls = select_creation_contracts(project.metadata_json, source_bound=True)
    # This is the explicit epistemic boundary carried by the creator revision;
    # never promote its AI chapter schedule or a commercial repair to hard law.
    controls['creator_revision_knowledge_boundary'] = {
        'prior_knowledge_states':deepcopy(source_root.metadata_json['existing_prior_unknown_retained']),
        'requirement':'Unresolved old knowledge remains Unknown, not false or automatically a known member.',
        'state_at_revision':'unknown'}
    need(content_hash(commercial_report) == meta['prior_diagnostics_hash'], 'admission_volume_diagnostics_changed')
    result = {'schema':admission.VOLUME_SCHEMA,'hard_admission_policy':admission.VOLUME_HARD_ADMISSION_POLICY,
        'project_id':str(project.id),'workflow_run_id':str(workflow_run_id),
        'candidate_payload':deepcopy(candidate_payload),
        'projection_audit_targets':admission.projection_audit_targets(candidate_payload),
        'temporal_positions':admission.temporal_positions(candidate_payload),
        'foundation':deepcopy(sources),'verified_source_components':{
            'schema':components['schema'],'receipt_sha256':component_identity,
            'revision_id':components['revision_id'],'canonical_sha256':components['canonical_sha256'],
            'sources':deepcopy(sources),'world_cast_original_reviews':{
                kind:{k:proof[k] for k in ('workflow_id','llm_run_id','report_sha256')}
                for kind,proof in components['legacy_source_reviews'].items()},
            'partial_component_review':{k:components['unchanged_component_axes'][k] for k in (
                'report_id','run_id','passed_axes','original_decision','report_sha256')},
            'negative_volume_review_not_overwritten':True},
        'approved_book_spec':planner._without_planning_meta(book_spec),
        'approved_world_spec':planner._without_planning_meta(world_spec),
        'approved_cast_spec':planner._without_planning_meta(cast_spec),
        'approved_creation_contracts':controls,
        'retained_protected_source_clauses':deepcopy(canonical.content['retained_planning_clauses']),
        'source_snapshot_hash':content_hash(project.metadata_json['book_design_snapshot']),
        'continuity_scope':'Initial complete VolumePlan; proposed scenes are not adopted occurrences.',
        'untrusted_commercial_report':deepcopy(commercial_report),
        'untrusted_legacy_design_context':{
            'writing_profile':deepcopy(canonical.content.get('writing_profile')),
            'story_architecture':deepcopy(canonical.content.get('concept_contract',{}).get('story_architecture')),
            'commercial_brief':deepcopy(canonical.content.get('commercial_brief')),
            'archived_commercial_brief':deepcopy(canonical.content.get('conception_artifacts',{}).get('commercial_brief')),
            'hook_card':deepcopy(canonical.content.get('hook_card')),
            'story_spine':deepcopy(canonical.content.get('story_spine')),
            'authority':'All original design contradictions remain visible. These AI plans/evaluations are not occurred history or new author hard rules.'},
        'foundation_source_selection':{'schema':'verified-volume-source-components.v1',
            'story_fields_complete':True,'producer_metadata_is_authority':False,
            'full_sources':deepcopy(sources),'full_volume_content_hash':content_hash(volume.content),
            'scope':'Source-hard eligibility for foundation/rolling planning only; not prose adoption or a replacement semantic score.'}}
    result['source_authority'] = admission._source_authority(result)
    result['source_authority_allowed_codes'] = {role:sorted(code for code,roles in admission._SOURCE_ROLES_BY_CODE.items() if role in roles)
        for role in result['source_authority']}
    result, _ = preview_reference_only_review_input(result)
    return result


def preview_reference_only_review_input(supplied):
    """Unwired, zero-dispatch source-boundary proposal; not an admission result.

    The active protocol/journal is deliberately unchanged. A future policy
    decision is required before this preview can be used for assessment.
    Full diagnostics remain in the original receipt, not as story instructions.
    """
    original = deepcopy(supplied)
    diagnostics = original.get('untrusted_commercial_report')
    preview = deepcopy(original)
    preview['untrusted_commercial_report'] = {
        'schema':'source-hard-diagnostic-audit-reference.v1',
        'original_diagnostics_sha256':content_hash(diagnostics),
        'old_review_refs':[{key:deepcopy(row[key]) for key in (
            'id','verdict','content_hash','llm_run_id') if key in row}
            for row in diagnostics.get('old_review_refs', [])] if isinstance(diagnostics, dict) else [],
        'raw_diagnostics_retained_outside_model_input':True,
        'role':'audit_reference_only_not_story_source_or_instruction',
    }
    if 'verified_source_components' in original:
        component = original['verified_source_components']
        preview['verified_source_components'] = {key:deepcopy(component[key]) for key in (
            'schema','receipt_sha256','revision_id','canonical_sha256','sources') if key in component}
        preview['verified_source_components']['original_component_view_sha256'] = content_hash(component)
    preserved = {key:content_hash(value) for key,value in original.items()
        if key not in {'untrusted_commercial_report','verified_source_components'}}
    if any(content_hash(preview[key]) != digest for key,digest in preserved.items()):
        raise ValueError('volume_boundary_preview_changed_story_source')
    return preview, {'schema':'volume-source-boundary-preview-proof.v1',
        'preview_only':True,'dispatch_allowed':False,'original_input_sha256':content_hash(original),
        'preview_input_sha256':content_hash(preview),'preserved_subtree_hashes':preserved,
        'original_diagnostics_sha256':content_hash(diagnostics),
        'existing_grade_or_journal_changed':False,'old_false_upgraded':False}
