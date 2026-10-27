"""Verify a reviewed source-revision consumer without aliasing paid generation.

Book/World/Cast are explicitly derived assets. Only Volume is newly generated.
The original foundation workflow and every original physical result stay intact.
"""
from copy import deepcopy
import hashlib
import json
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.infra.db.models import LlmRunModel, PlanningArtifactVersionModel, ProjectModel, WorkflowRunModel
from bestseller.services.conception_snapshots import snapshot_hash
from bestseller.settings import AppSettings

WORKFLOW_TYPE = 'complete_foundation_revision'


def _require(value: Any, code: str) -> None:
    if not value:
        raise ValueError('foundation_revision_' + code)


async def validate_revision_review(
    session: AsyncSession, settings: AppSettings, *, project: ProjectModel,
    revision: WorkflowRunModel, review_id: UUID, part: str,
    candidates: dict[str, PlanningArtifactVersionModel],
) -> dict[str, Any]:
    from bestseller.services import planner, llm
    from bestseller.services.foundation_selection import _physical_file
    from bestseller.services import retained_review_input_proof as proof

    _require(part in {'world', 'cast'}, 'review_scope')
    review = await session.get(WorkflowRunModel, review_id)
    _require(review is not None and review.project_id == project.id
        and review.workflow_type == 'review_revised_foundation_sources'
        and review.status == 'completed', 'review_workflow')
    meta = review.metadata_json
    _require(meta.get('creator_revision_id') == str(revision.id)
        and meta.get('candidate_scope') == part
        and meta.get('canonical_snapshot_sha256') == project.metadata_json.get('creation_canonical_snapshot_hash'),
        'review_source')
    for kind, candidate in candidates.items():
        _require(meta.get('candidate_ids', {}).get(kind) == str(candidate.id)
            and meta.get('candidate_content_sha256', {}).get(kind) == snapshot_hash(candidate.content),
            'review_candidate_changed')
    report = meta.get('source_bound_review') or {}
    _require(report.get('decision') == 'pass' and report.get('reason') == 'reviewed'
        and report.get('core_preserved') is True and report.get('core_conflicts') == []
        and planner._source_bound_report_is_draft_eligible(report), 'review_not_passed')
    expected = planner._approved_payload_hash({
        'world_spec': planner._without_planning_meta(candidates['world_spec'].content),
        'cast_spec': {} if part == 'world' else planner._without_planning_meta(candidates['cast_spec'].content),
    })
    _require(report.get('candidate_hash') == report.get('candidate_hash_echo') == expected,
        'review_body_binding')
    _require(report.get('authority_snapshot_hash') == planner._approved_payload_hash(
        planner._source_bound_design_snapshot(project)), 'review_authority_changed')
    run = await session.get(LlmRunModel, UUID(report['llm_run_id']))
    _require(run is not None and run.project_id == project.id and run.workflow_run_id == review.id
        and run.prompt_template == 'planner_source_bound_expansion_semantic_review', 'review_run_binding')
    request_json, request_sha = _physical_file(settings, run, 'request')
    response, response_sha = _physical_file(settings, run, 'response')
    text = response.get('content') or {}
    _require(text.get('truncated') is False and isinstance(text.get('text'), str)
        and text.get('original_chars') == len(text['text'])
        and hashlib.sha256(text['text'].encode()).hexdigest() == report['raw_response_hash'],
        'review_physical_response')
    request = proof._retained_request(settings, run)
    # The physical request, not a writable workflow label, binds which source
    # and candidate were actually judged. Decode only the existing codec.
    if '\nsource_review_data=' in request.user_prompt:
        from bestseller.services.lossless_source_encoding import decode_lossless_source
        marker = '\nsource_review_data='
        _require(request.user_prompt.count(marker) == 1, 'review_data_ambiguous')
        encoded, _ = json.JSONDecoder().raw_decode(request.user_prompt.split(marker, 1)[1])
        logical = decode_lossless_source(encoded)
        base = logical['authority_and_baseline']
        judged = logical['derived_candidates']
    else:
        values = {}
        for key in ('authority_and_baseline', 'derived_candidates', 'source_quote_options',
                    'candidate_quote_options', 'changed_fields'):
            marker = '\n' + key + '='
            _require(request.user_prompt.count(marker) == 1, 'review_data_ambiguous')
            values[key], _ = json.JSONDecoder().raw_decode(request.user_prompt.split(marker, 1)[1])
        base, judged = values['authority_and_baseline'], values['derived_candidates']
        logical = values
    _require(planner._approved_payload_hash(judged) == expected
        and planner._approved_payload_hash(base) == report.get('canonical_baseline_hash')
        and base.get('book_spec') == planner._without_planning_meta(candidates['book_spec'].content),
        'review_physical_input_binding')
    canonical = base.get('canonical_source') or {}
    _require(canonical.get('id') == project.metadata_json.get('creation_canonical_snapshot_id')
        and canonical.get('content_hash') == project.metadata_json.get('creation_canonical_snapshot_hash')
        and snapshot_hash(canonical.get('content')) == canonical.get('content_hash'),
        'review_physical_canonical_binding')
    catalog, catalog_sha = proof._fresh_catalog()
    role = proof._fresh_role(settings, project, request, catalog)
    digest = llm._effective_input_hash(request, role)
    paid, operation = await proof._physical_return(session, run,
        scope=project.metadata_json['execution_scope_id'], expected_hash=digest, expected_model=role.model)
    _require(paid.content == text['text'] and paid.fallback_used is False
        and paid.finish_reason in {'stop', 'end_turn', 'completed'}, 'review_physical_return')
    parsed = planner._parse_source_bound_review_json(paid.content)
    for field in ('decision', 'core_preserved', 'authority_snapshot_hash', 'candidate_hash'):
        _require(parsed.get(field) == report.get(field), 'review_raw_decision_binding')
    _require(parsed.get('core_conflicts', []) == report.get('core_conflicts'), 'review_raw_conflicts')
    source_options, candidate_options = logical['source_quote_options'], logical['candidate_quote_options']
    source_evidence, _ = planner._source_bound_resolve_evidence(
        parsed.get('source_evidence', []), source_options, candidate=False, payload=base)
    candidate_evidence, _ = planner._source_bound_resolve_evidence(
        parsed.get('candidate_evidence', []), candidate_options, candidate=True, payload=judged)
    repair_proofs = []
    for repair in report.get('evidence_repair_attempts', []):
        _require(repair.get('reason') == 'evidence_repair_passed', 'repair_not_passed')
        rr = await session.get(LlmRunModel, UUID(repair['llm_run_id']))
        _require(rr is not None and rr.project_id == project.id and rr.workflow_run_id == review.id
            and rr.prompt_template == 'planner_source_bound_expansion_evidence_repair', 'repair_run')
        _, repair_request_sha = _physical_file(settings, rr, 'request')
        response, repair_response_sha = _physical_file(settings, rr, 'response')
        req = proof._retained_request(settings, rr)
        for key, expected_value in (('source_quote_options', source_options),
                ('candidate_quote_options', candidate_options), ('candidate_payload', judged)):
            marker = '\n' + key + '='
            _require(req.user_prompt.count(marker) == 1, 'repair_request_ambiguous')
            actual, _ = json.JSONDecoder().raw_decode(req.user_prompt.split(marker, 1)[1])
            _require(actual == expected_value, 'repair_request_changed')
        rr_role = proof._fresh_role(settings, project, req, catalog)
        rr_hash = llm._effective_input_hash(req, rr_role)
        rr_paid, _ = await proof._physical_return(session, rr,
            scope=project.metadata_json['execution_scope_id'], expected_hash=rr_hash, expected_model=rr_role.model)
        _require(response.get('content', {}).get('truncated') is False
            and response['content'].get('text') == rr_paid.content
            and hashlib.sha256(rr_paid.content.encode()).hexdigest() == repair['raw_response_hash'], 'repair_response')
        fragment = planner._parse_source_bound_review_json(rr_paid.content)
        _require(set(fragment) == {'source_evidence', 'candidate_evidence'}, 'repair_semantic_change')
        source_evidence, source_ok = planner._source_bound_resolve_evidence(
            [*source_evidence, *fragment['source_evidence']], source_options, candidate=False, payload=base)
        candidate_evidence, candidate_ok = planner._source_bound_resolve_evidence(
            [*candidate_evidence, *fragment['candidate_evidence']], candidate_options, candidate=True, payload=judged)
        _require(source_ok and candidate_ok, 'repair_evidence_invalid')
        repair_proofs.append({'run_id': str(rr.id), 'request_sha256': repair_request_sha,
            'response_sha256': repair_response_sha})
    _require(source_evidence == report.get('source_evidence')
        and candidate_evidence == report.get('candidate_evidence')
        and logical['changed_fields'] == report.get('changed_fields'), 'review_evidence_changed')
    return {'workflow_id': str(review.id), 'llm_run_id': str(run.id), 'operation_key': operation,
        'effective_input_sha256': digest, 'request_sha256': request_sha,
        'response_sha256': response_sha, 'catalog_sha256': catalog_sha,
        'report_sha256': snapshot_hash(report), 'report': deepcopy(report), 'evidence_repairs': repair_proofs}


async def validate_reviewed_foundation_revision(
    session: AsyncSession, settings: AppSettings, *, project: ProjectModel,
    workflow: WorkflowRunModel, artifacts: dict[str, PlanningArtifactVersionModel],
) -> dict[str, Any]:
    """Revalidate a new real consumer, never retrofit an original generation."""
    from bestseller.services import planner
    from bestseller.services.foundation_selection import _volume_paid_proof, _asset_meta, _strict_decoder, _expand_projection
    from bestseller.services import source_bound_planning_content as content_policy

    meta = workflow.metadata_json
    revision = await session.get(WorkflowRunModel, UUID(meta['creator_revision_id']))
    _require(revision is not None and revision.project_id == project.id
        and revision.workflow_type == 'creation_source_revision' and revision.status == 'completed', 'source_revision')
    source = revision.metadata_json.get('receipt') or {}
    _require(source.get('snapshot_id') == project.metadata_json.get('creation_canonical_snapshot_id')
        and source.get('snapshot_sha256') == project.metadata_json.get('creation_canonical_snapshot_hash'),
        'source_revision_changed')
    candidates = {}
    for kind in ('book_spec', 'world_spec', 'cast_spec'):
        original = await session.get(PlanningArtifactVersionModel, UUID(meta['source_candidate_ids'][kind]))
        asset = artifacts[kind]
        _require(original is not None and original.project_id == project.id
            and original.artifact_type == kind and original.source_run_id == revision.id
            and original.status == 'candidate' and asset.source_run_id == workflow.id
            and planner._without_planning_meta(asset.content) == planner._without_planning_meta(original.content),
            'derived_asset_binding')
        candidates[kind] = original
    receipts = {}
    for part in ('world', 'cast'):
        receipts[part] = await validate_revision_review(session, settings, project=project,
            revision=revision, review_id=UUID(meta['source_review_workflow_ids'][part]),
            part=part, candidates=candidates)
    saved = meta.get('verified_source_reviews') or {}
    _require(saved == receipts, 'review_receipt_changed')
    _require(artifacts['volume_plan'].source_run_id == workflow.id, 'volume_owner')
    request, volume = await _volume_paid_proof(session, settings, project,
        _asset_meta(artifacts['volume_plan'].content, 'volume_plan'), artifacts['volume_plan'])
    outer, _ = _strict_decoder().raw_decode(request.user_prompt.lstrip())
    logical = content_policy.decode_source_bound_planning_document(outer)
    source_row = await session.get(PlanningArtifactVersionModel, UUID(source['snapshot_id']))
    _require(logical.get('protocol') == content_policy.SOURCE_BOUND_PLANNING_PROTOCOL
        and logical.get('kind') == 'volume_plan'
        and logical.get('approved_sources', {}).get('canonical_snapshot') == {
            'id': str(source_row.id), 'content_hash': snapshot_hash(source_row.content), 'content': source_row.content}
        and logical.get('delivery_scope', {}).get('target_chapters') == project.target_chapters
        and logical.get('delivery_scope', {}).get('target_word_count') == project.target_word_count,
        'volume_actual_source_binding')
    dependencies = {k: content_policy._dependency_projection(artifacts[k].content) for k in candidates}
    _require(logical.get('existing_planning_assets') == dependencies, 'volume_actual_dependency_binding')
    projection = planner._compile_source_bound_volume_plan(project, project.metadata_json['premise'],
        *(artifacts[k].content for k in ('book_spec', 'world_spec', 'cast_spec')))
    _require(planner._approved_payload_hash(_expand_projection(logical.get('projection'), logical))
        == planner._approved_payload_hash(projection), 'volume_actual_projection_binding')
    return {'schema': 'reviewed-foundation-revision.v1', 'revision_id': str(revision.id),
        'review_receipt_sha256': snapshot_hash(receipts), 'volume_paid_proof': volume,
        'original_paid_generation_aliased': False, 'new_grade_or_credit_granted': False}


async def validate_creative_review_axes(session, settings, *, project, report_id, required_axes):
    """Verify paid literal-source evidence for specified axes, not the old overall grade."""
    from bestseller.infra.db.models import ReviewReportModel
    from bestseller.services import llm, retained_review_input_proof as proof
    from bestseller.services.foundation_selection import _physical_file
    from bestseller.services.foundation_revision_delta import (
        restore_delta_sections, delta_quote_catalog, validate_catalog_delta_verdict,
    )
    from bestseller.services.lossless_source_encoding import decode_lossless_source
    report = await session.get(ReviewReportModel, UUID(str(report_id)))
    _require(report is not None and report.project_id == project.id
        and report.reviewer_type == 'foundation_creative_delta', 'partial_review_report')
    run = await session.get(LlmRunModel, report.llm_run_id)
    owner = await session.get(WorkflowRunModel, run.workflow_run_id) if run else None
    _require(run is not None and owner is not None and run.project_id == project.id
        and owner.project_id == project.id and owner.status == 'completed'
        and owner.metadata_json.get('review_report_id') == str(report.id)
        and run.prompt_template == 'foundation_creative_delta_review', 'partial_review_run')
    context = owner.metadata_json['review_context']
    _require(snapshot_hash(context) == report.structured_output['input_sha256'], 'partial_review_context')
    request_json, request_sha = _physical_file(settings, run, 'request')
    response, response_sha = _physical_file(settings, run, 'response')
    request = proof._retained_request(settings, run)
    marker = 'source_review_data='
    _require(request.user_prompt.count(marker) == 1, 'partial_review_physical_input')
    encoded, _ = json.JSONDecoder().raw_decode(request.user_prompt.split(marker,1)[1])
    data = decode_lossless_source(encoded)
    if isinstance(data, list) and len(data) == 2 and isinstance(data[0], dict) and 'source_fragments' in data[0]:
        from bestseller.services.retained_planning_clauses import resolve_pointer
        _require(data[0]['source_binding']['full_retained_context_sha256'] == snapshot_hash(context), 'partial_review_scope')
        for fragment in data[0]['source_fragments']:
            _require(resolve_pointer(context, fragment['pointer']) == fragment['value'], 'partial_review_fragment')
        _require(data[1] == delta_quote_catalog(context), 'partial_review_catalog')
    else:
        prefix = 'section_paths='
        _require(request.user_prompt.count(prefix) == 1, 'partial_review_sections')
        paths, _ = json.JSONDecoder().raw_decode(request.user_prompt.split(prefix,1)[1])
        restored, catalog = restore_delta_sections(data, paths)
        _require(restored == context and catalog == delta_quote_catalog(context), 'partial_review_sections')
    catalog, catalog_sha = proof._fresh_catalog()
    role = proof._fresh_role(settings, project, request, catalog)
    digest = llm._effective_input_hash(request, role)
    paid, operation = await proof._physical_return(session, run,
        scope=project.metadata_json['execution_scope_id'], expected_hash=digest, expected_model=role.model)
    text = response.get('content') or {}
    _require(text.get('truncated') is False and text.get('text') == paid.content
        and text.get('original_chars') == len(paid.content) and not paid.fallback_used
        and paid.finish_reason in {'stop','completed','end_turn'}, 'partial_review_return')
    parsed = validate_catalog_delta_verdict(paid.content, context)
    for key in ('decision','input_sha256','checks','feedback','passed','catalog_sha256'):
        _require(parsed.get(key) == report.structured_output.get(key), 'partial_review_verdict_changed')
    checks = {c['axis']: c for c in parsed['checks']}
    _require(set(required_axes) <= checks.keys()
        and all(checks[axis]['verdict'] == 'pass' for axis in required_axes), 'partial_review_axis_not_passed')
    return {'schema':'verified-creative-review-axes.v1','report_id':str(report.id),
        'run_id':str(run.id),'workflow_id':str(owner.id),'original_decision':parsed['decision'],
        'passed_axes':list(required_axes),'context_sha256':snapshot_hash(context),
        'report_sha256':snapshot_hash(report.structured_output),'request_sha256':request_sha,
        'response_sha256':response_sha,'effective_input_sha256':digest,'operation_key':operation,
        'catalog_sha256':catalog_sha,'whole_review_pass_asserted':False,
        'context':deepcopy(context)}


async def validate_volume_foundation_components(session, settings, *, project, revision, volume):
    """Current source components from exact historical + changed-source evidence.

    This does not approve the VolumePlan or turn its negative review into pass.
    It only establishes the source inputs for the existing hard-admission gate.
    """
    from types import SimpleNamespace
    from bestseller.services import planner
    from bestseller.services.foundation_revision_delta import delta_context
    from bestseller.services.foundation_dependency_scope import UNCHANGED_AXES
    _require(revision.project_id == project.id and revision.status == 'completed'
        and revision.workflow_type == 'creation_source_dependency_revision'
        and volume.project_id == project.id and volume.artifact_type == 'volume_plan'
        and volume.source_run_id == revision.id and volume.status in {'candidate','approved'}
        and revision.metadata_json['candidate_ids']['volume_plan'] == str(volume.id), 'volume_source_revision')
    context = await delta_context(session, project, revision)
    _require(context['candidate']['assets']['volume_plan'] == [planner._without_planning_meta(v) for v in volume.content],
        'volume_source_body')
    partial = await validate_creative_review_axes(session, settings, project=project,
        report_id=revision.metadata_json['prior_valid_review_report_id'], required_axes=UNCHANGED_AXES)
    previous = partial.pop('context')
    _require(previous['candidate']['canonical'] == context['candidate']['canonical']
        and all(previous['candidate']['assets'][k] == context['candidate']['assets'][k]
            for k in ('book_spec','world_spec','cast_spec')), 'volume_components_changed')
    roots = {revision.metadata_json['candidate_origin_revision_ids'][k] for k in ('book_spec','world_spec','cast_spec')}
    _require(len(roots) == 1, 'volume_components_origin')
    root = await session.get(WorkflowRunModel, UUID(next(iter(roots))))
    _require(root is not None and root.project_id == project.id and root.status == 'completed'
        and root.workflow_type == 'creation_source_revision', 'volume_components_origin')
    legacy_book = await session.get(PlanningArtifactVersionModel, UUID(root.metadata_json['base_artifacts']['book_spec']['id']))
    legacy_consumer = await session.get(WorkflowRunModel, legacy_book.source_run_id) if legacy_book else None
    _require(legacy_consumer is not None and legacy_consumer.project_id == project.id
        and legacy_consumer.workflow_type == WORKFLOW_TYPE, 'volume_legacy_consumer')
    legacy_revision = await session.get(WorkflowRunModel, UUID(legacy_consumer.metadata_json['creator_revision_id']))
    historical = SimpleNamespace(**{k:deepcopy(getattr(project,k,None)) for k in (
        'id','slug','title','genre','sub_genre','language','target_chapters','target_word_count','audience')},
        metadata_json=deepcopy(root.metadata_json['before']['metadata']))
    original_candidates = {k:await session.get(PlanningArtifactVersionModel, UUID(v))
        for k,v in legacy_consumer.metadata_json['source_candidate_ids'].items()}
    legacy_proofs = {}
    for part in ('world','cast'):
        legacy_proofs[part] = await validate_revision_review(session, settings, project=historical,
            revision=legacy_revision, review_id=UUID(legacy_consumer.metadata_json['source_review_workflow_ids'][part]),
            part=part, candidates=original_candidates)
    # Exact current world and old Cast prefix were already checked by
    # delta_context; include full current source IDs/hashes for readback.
    sources = {}
    for kind in ('book_spec','world_spec','cast_spec'):
        row = await session.get(PlanningArtifactVersionModel, UUID(revision.metadata_json['candidate_ids'][kind]))
        _require(row is not None and row.project_id == project.id and row.source_run_id == root.id
            and planner._without_planning_meta(row.content) == context['candidate']['assets'][kind], 'volume_component_binding')
        sources[kind] = {'artifact_id':str(row.id),'version':row.version_no,
            'content_hash':snapshot_hash(row.content),'source_run_id':str(row.source_run_id),'status':row.status}
    return {'schema':'verified-volume-source-components.v1','project_id':str(project.id),
        'revision_id':str(revision.id),'canonical_snapshot_id':project.metadata_json['creation_canonical_snapshot_id'],
        'canonical_sha256':project.metadata_json['creation_canonical_snapshot_hash'],
        'volume_id':str(volume.id),'volume_sha256':snapshot_hash(volume.content),
        'sources':sources,'legacy_source_reviews':legacy_proofs,'unchanged_component_axes':partial,
        'volume_approved':False,'negative_review_overwritten':False,'new_grade_or_credit_granted':False}
