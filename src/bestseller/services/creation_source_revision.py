"""Trusted, versioned empty-book source corrections; never a model tool."""
from copy import deepcopy
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.infra.db.models import ChapterModel, PlanningArtifactVersionModel, ProjectModel, WorkflowRunModel
from bestseller.services.book_design import ensure_project_book_design_snapshot
from bestseller.services.concept_contract import reseal_concept_contract_lineage
from bestseller.services.conception_snapshots import create_conception_snapshot_artifact, promote_conception_snapshot, snapshot_hash
from bestseller.services.retained_planning_clauses import resolve_pointer, validate_planning_chapter_references


async def revise_empty_book_sources(
    session: AsyncSession, *, project_id: UUID, expected_snapshot_sha256: str,
    concept_contract: dict[str, Any], canonical_updates: dict[str, Any],
    clause_selections: list[dict[str, str]], superseded_workflow_ids: list[UUID],
    actor: str, authorization_ref: str, reason: str, explicit_creator_revision: bool = False,
) -> dict[str, Any]:
    """Version inspected same-story corrections with exact CAS and provenance.

    Only explicitly selected clauses are retained. No 'latest asset' fallback,
    old grade reuse, allowance reset, implicit plan compression, or new genre
    content. Caller supplies the reviewed creative proposal and commits.
    """
    if explicit_creator_revision is not True or not all(v.strip() for v in (actor, authorization_ref, reason)):
        raise ValueError('source_revision_authorization_required')
    project = await session.get(ProjectModel, project_id, with_for_update=True, populate_existing=True)
    if project is None:
        raise ValueError('source_revision_project_missing')
    metadata = deepcopy(project.metadata_json or {})
    key = snapshot_hash({'base': expected_snapshot_sha256, 'contract': concept_contract,
        'updates': canonical_updates, 'clauses': clause_selections,
        'superseded_workflows': [str(x) for x in superseded_workflow_ids],
        'actor': actor, 'authorization_ref': authorization_ref, 'reason': reason})
    existing = await session.scalar(select(WorkflowRunModel).where(
        WorkflowRunModel.project_id == project_id,
        WorkflowRunModel.workflow_type == 'creation_source_revision',
        WorkflowRunModel.idempotency_key == key))
    if existing:
        receipt = existing.metadata_json['receipt']
        if (metadata.get('creation_canonical_snapshot_id') != receipt['snapshot_id']
                or metadata.get('creation_canonical_snapshot_hash') != receipt['snapshot_sha256']):
            raise ValueError('source_revision_idempotence_stale')
        return deepcopy(receipt)
    if await session.scalar(select(ChapterModel.id).where(ChapterModel.project_id == project_id).limit(1)):
        raise ValueError('source_revision_requires_empty_book')
    if await session.scalar(select(WorkflowRunModel.id).where(
        WorkflowRunModel.project_id == project_id,
        WorkflowRunModel.status.in_(['running', 'pending', 'queued'])).limit(1)):
        raise ValueError('source_revision_active_workflow')
    old = await session.get(PlanningArtifactVersionModel, UUID(metadata['creation_canonical_snapshot_id']), with_for_update=True)
    if (old is None or old.project_id != project_id or old.status != 'canonical'
            or snapshot_hash(old.content) != expected_snapshot_sha256
            or metadata.get('creation_canonical_snapshot_hash') != expected_snapshot_sha256):
        raise ValueError('source_revision_stale_canonical')
    if set(canonical_updates) - {'premise', 'synopsis', 'hook_spec', 'writing_profile', 'commercial_brief', 'tags'}:
        raise ValueError('source_revision_update_field_forbidden')
    selected = []
    seen = set()
    for choice in clause_selections:
        if set(choice) != {'artifact_id', 'content_sha256', 'source_pointer', 'kind', 'target_pointer'}:
            raise ValueError('source_revision_clause_shape_invalid')
        asset = await session.get(PlanningArtifactVersionModel, UUID(choice['artifact_id']), with_for_update=True)
        if (asset is None or asset.project_id != project_id or asset.status != 'approved'
                or asset.artifact_type != choice['kind']
                or snapshot_hash(asset.content) != choice['content_sha256']):
            raise ValueError('source_revision_clause_origin_invalid')
        pair = (choice['kind'], choice['target_pointer'])
        if pair in seen:
            raise ValueError('source_revision_clause_duplicate')
        seen.add(pair)
        value = resolve_pointer(asset.content, choice['source_pointer'])
        # Verify target syntax before any mutation, including index syntax.
        if not choice['target_pointer'].startswith('/'):
            raise ValueError('source_revision_clause_target_invalid')
        selected.append({'kind': choice['kind'], 'target_pointer': choice['target_pointer'],
            'source_artifact_id': str(asset.id), 'source_content_sha256': snapshot_hash(asset.content),
            'source_pointer': choice['source_pointer'], 'value': value})
    invalidated = []
    for workflow_id in superseded_workflow_ids:
        workflow = await session.get(WorkflowRunModel, workflow_id, with_for_update=True)
        if workflow is None or workflow.project_id != project_id or workflow.status not in {'failed', 'completed', 'cancelled'}:
            raise ValueError('source_revision_superseded_workflow_invalid')
        invalidated.extend(await session.scalars(select(PlanningArtifactVersionModel).where(
            PlanningArtifactVersionModel.project_id == project_id,
            PlanningArtifactVersionModel.source_run_id == workflow_id).with_for_update()))
    revised = reseal_concept_contract_lineage(concept_contract, target_chapters=project.target_chapters)
    # Reusing historical grade as current is expressly forbidden.
    revised['quality_evidence'] = {'schema_version': 'concept-quality-evidence.v1',
        'approved': False, 'review_status': 'requires_review',
        'reconciliation_lineage': {'source_kind': 'creator_source_revision'}}
    validate_planning_chapter_references(
        {k: v for k, v in revised.items() if k not in {'quality_evidence', 'reconciliation_lineage'}},
        target_chapters=project.target_chapters)
    validate_planning_chapter_references(canonical_updates, target_chapters=project.target_chapters)
    archive = WorkflowRunModel(project_id=project_id, workflow_type='creation_source_revision',
        scope_type='project', scope_id=project_id, status='completed', current_step='requires_replan',
        requested_by=actor[:64], idempotency_key=key, metadata_json={
            'actor': actor, 'authorization_ref': authorization_ref, 'reason': reason,
            'before': {'snapshot_id': str(old.id), 'content': deepcopy(old.content),
                'content_sha256': expected_snapshot_sha256, 'metadata': deepcopy(metadata)},
            'invalidated_artifacts': [{'id': str(a.id), 'status': a.status,
                'content_sha256': snapshot_hash(a.content)} for a in invalidated]})
    session.add(archive)
    await session.flush()
    content = {**deepcopy(old.content), **deepcopy(canonical_updates),
        'concept_contract': revised, 'retained_planning_clauses': selected,
        'creator_source_revision': {'revision_id': str(archive.id), 'authorization_ref': authorization_ref,
            'original_snapshot_id': str(old.id), 'original_content_sha256': expected_snapshot_sha256,
            'reason': reason, 'new_model_grade_claimed': False}}
    for field in ('hook_card', 'seriality_proof', 'story_spine'):
        content[field] = deepcopy(revised[field])
        metadata[field] = deepcopy(revised[field])
    snapshot = await create_conception_snapshot_artifact(session, project_id=project_id,
        artifact_type='conception_snapshot', content=content, status='validated',
        idempotency_key='creator-source-revision:' + key, source_run_id=archive.id, created_by=actor,
        notes=reason)
    metadata['concept_contract'] = deepcopy(revised)
    for field in ('premise', 'synopsis', 'writing_profile', 'commercial_brief'):
        if field in canonical_updates:
            metadata[field] = deepcopy(canonical_updates[field])
    metadata['planning_status'] = 'needs_replan'
    project.metadata_json = metadata
    await promote_conception_snapshot(session, project_id, snapshot.id,
        expected_lock_version=project.lock_version, actor=actor, reason=reason)
    ensure_project_book_design_snapshot(project, protagonist_name=metadata.get('creation_protagonist_name'), force_rebuild=True)
    project.metadata_json = {**project.metadata_json, 'book_design_adoption_source': {
        'kind': 'adopted_conception', 'snapshot_id': str(snapshot.id), 'content_hash': snapshot_hash(snapshot.content)}}
    for asset in invalidated:
        asset.status = 'superseded'
        asset.notes = (asset.notes or '') + '\nSuperseded by creator source revision ' + str(archive.id)
    receipt = {'revision_id': str(archive.id), 'snapshot_id': str(snapshot.id),
        'snapshot_sha256': snapshot_hash(snapshot.content), 'clause_count': len(selected),
        'new_model_grade_claimed': False, 'spent_reset': False,
        'superseded_artifact_ids': [str(a.id) for a in invalidated]}
    archive.metadata_json = {**archive.metadata_json, 'receipt': receipt}
    await session.flush()
    return receipt
