"""Consume a physically proved review of an edited, unadopted foundation.

Original candidates and paid generation records are immutable inputs. Four new
assets are derived, never claimed as model generation. Caller owns commit.
"""
from copy import deepcopy
from types import SimpleNamespace
from uuid import UUID, NAMESPACE_URL, uuid5

from sqlalchemy import func, select, inspect

from bestseller.domain.enums import ArtifactType
from bestseller.infra.db.models import (
    ProjectModel, WorkflowRunModel, PlanningArtifactVersionModel,
    ChapterModel, ChapterDraftVersionModel, CanonFactModel, TimelineEventModel,
)
from bestseller.services.prompt_source_policy import content_hash
from bestseller.services.conception_snapshots import snapshot_hash

CONSUMER_KIND = 'reviewed_volume_edit.v1'
KINDS = ('book_spec', 'world_spec', 'cast_spec', 'volume_plan')


def _need(ok, code):
    if not ok:
        raise ValueError('foundation_volume_edit_' + code)


def _source_record(row):
    return {'id': str(row.id), 'content_sha256': content_hash(row.content),
            'version': row.version_no, 'status': row.status, 'source_run_id': str(row.source_run_id)}


async def _fresh_row(session, model, identity, *, lock=False):
    identity = UUID(str(identity))
    _need(not any(isinstance(row, model) and row.id == identity for row in session.dirty),
          'unflushed_source_change')
    return await session.get(model, identity, populate_existing=True,
                             **({'with_for_update': True} if lock else {}))


class _FreshProofSession:
    """Read proof rows from DB without refreshing caller-held ORM objects."""
    def __init__(self, session):
        self._session = session

    def __getattr__(self, name):
        return getattr(self._session, name)

    def _clone(self, model, values):
        mapper = inspect(model)
        row = model(**{prop.key:deepcopy(values[prop.columns[0].name]) for prop in mapper.column_attrs})
        pk_fields = [mapper.get_property_by_column(column).key for column in mapper.primary_key]
        _need(not any(isinstance(dirty, model) and all(getattr(dirty,key) == getattr(row,key)
                  for key in pk_fields) for dirty in self._session.dirty), 'unflushed_source_change')
        return row

    async def get(self, model, identity, **kwargs):
        mapper = inspect(model)
        _need(len(mapper.primary_key) == 1, 'unsupported_proof_identity')
        query = select(model.__table__).where(mapper.primary_key[0] == identity)
        if kwargs.get('with_for_update'):
            query = query.with_for_update()
        with self._session.no_autoflush:
            values = (await self._session.execute(query)).mappings().one_or_none()
        return self._clone(model,values) if values is not None else None

    async def scalar(self, statement, **kwargs):
        descriptions = getattr(statement,'column_descriptions',[])
        if len(descriptions) == 1:
            model = descriptions[0].get('entity')
            if model is not None and descriptions[0].get('expr') is model:
                query = statement.with_only_columns(*model.__table__.c, maintain_column_froms=True)
                with self._session.no_autoflush:
                    values = (await self._session.execute(query,**kwargs)).mappings().first()
                return self._clone(model,values) if values is not None else None
        with self._session.no_autoflush:
            return await self._session.scalar(statement,**kwargs)


async def _binding(session, settings, *, project, revision_id, admission_workflow_id, lock=False):
    from bestseller.services import planner
    from bestseller.services.foundation_revision_selection import validate_volume_foundation_components
    from bestseller.services.planning_draft_admission import verify_volume_source_admission

    revision = await _fresh_row(session,WorkflowRunModel,revision_id,lock=lock)
    review = await _fresh_row(session,WorkflowRunModel,admission_workflow_id,lock=lock)
    for row, kind in ((revision, 'creation_source_dependency_revision'), (review, 'review_volume_source_admission')):
        # An original protocol failure stays failed. Only a separately verified
        # parser reconciliation can consume its unchanged physical return.
        reconciled_failure = (row is review and row is not None and row.status == 'failed'
            and ((row.metadata_json.get('source_hard_admission_receipt') or {})
                 .get('parser_reconciliation') or {}).get('schema')
                == 'planning-empty-notes-parser-reconciliation.v1')
        _need(row is not None and row.project_id == project.id and row.workflow_type == kind
              and (row.status == 'completed' or reconciled_failure) and row.scope_type == 'project'
              and row.scope_id == project.id, 'workflow_scope_or_status')
    ids = revision.metadata_json.get('candidate_ids') or {}
    _need(all(kind in ids for kind in KINDS), 'candidate_ids')
    candidates = {}
    for kind in KINDS:
        row = await _fresh_row(session,PlanningArtifactVersionModel,ids[kind],lock=lock)
        _need(row is not None and row.project_id == project.id and row.artifact_type == kind
              and row.scope_ref_id is None and row.status in {'candidate', 'approved'}, 'candidate_binding')
        candidates[kind] = row
    _need(len({row.id for row in candidates.values()}) == 4, 'candidate_ids')
    volume = candidates['volume_plan']
    _need(volume.source_run_id == revision.id and volume.status == 'candidate', 'original_volume_candidate')
    meta = review.metadata_json
    _need(meta.get('source_dependency_revision_id') == str(revision.id)
          and meta.get('volume_candidate_id') == str(volume.id)
          and meta.get('volume_content_hash') == content_hash(volume.content), 'review_target_changed')
    receipt = meta.get('source_hard_admission_receipt')
    _need(isinstance(receipt, dict) and receipt.get('project_id') == str(project.id)
          and receipt.get('workflow_run_id') == str(review.id), 'review_receipt_missing_or_scope')
    proof_session = _FreshProofSession(session)
    components = await validate_volume_foundation_components(proof_session, settings,
        project=project, revision=revision, volume=volume)
    from bestseller.services.foundation_catalog_transition import verified_component_identity
    component_identity = await verified_component_identity(session,
        project=project, review=review, components=components)
    _need(meta.get('source_component_receipt_hash') == component_identity, 'source_components_changed')
    for kind in KINDS[:3]:
        record = components['sources'][kind]
        _need(record['artifact_id'] == str(candidates[kind].id)
              and record['content_hash'] == snapshot_hash(candidates[kind].content), 'source_components_changed')
    candidate = {'volumes': [planner._without_planning_meta(item) for item in volume.content]}
    await verify_volume_source_admission(proof_session, settings=settings, project=project,
        workflow_run_id=review.id, candidate_payload=candidate,
        **{kind: candidates[kind].content for kind in KINDS[:3]}, receipt=receipt)
    binding = {'schema': CONSUMER_KIND, 'project_id': str(project.id),
        'source_dependency_revision_id': str(revision.id), 'admission_workflow_id': str(review.id),
        'admission_receipt_sha256': content_hash(receipt),
        'candidate_body_sha256': content_hash(candidate),
        'source_candidates': {kind: _source_record(row) for kind, row in candidates.items()},
        'source_component_receipt_sha256': component_identity,
        'canonical_source': {'id': project.metadata_json.get('creation_canonical_snapshot_id'),
                             'sha256': project.metadata_json.get('creation_canonical_snapshot_hash')},
        'book_design_sha256': project.metadata_json.get('book_design_snapshot_hash')}
    return binding, candidates


async def validate_reviewed_volume_foundation(session, settings, *, project, workflow, artifacts):
    """Read-only consumption fence, including the original paid admission proof."""
    from bestseller.services import planner
    workflow = await _fresh_row(session,WorkflowRunModel,workflow.id)
    # Read current DB authority without discarding caller-owned runtime edits.
    current_project = (await session.execute(select(ProjectModel.__table__).where(ProjectModel.id == project.id))).mappings().one()
    project = ProjectModel(**{prop.key:current_project[prop.columns[0].name]
                              for prop in inspect(ProjectModel).column_attrs})
    _need(settings is not None and workflow.project_id == project.id
          and workflow.scope_type == 'project' and workflow.scope_id == project.id
          and workflow.workflow_type == 'complete_foundation_revision'
          and workflow.status == 'completed'
          and workflow.metadata_json.get('foundation_consumer_kind') == CONSUMER_KIND, 'consumer_kind')
    meta = workflow.metadata_json
    _need(meta.get('artifact_ids') == {kind:str(row.id) for kind,row in artifacts.items()},
          'consumer_artifact_ids_changed')
    binding, candidates = await _binding(session, settings, project=project,
        revision_id=meta['source_dependency_revision_id'], admission_workflow_id=meta['admission_workflow_id'])
    _need(meta.get('verified_edit_binding') == binding, 'consumption_binding_changed')
    for kind in KINDS:
        source = candidates[kind]
        row = await _fresh_row(session,PlanningArtifactVersionModel,artifacts[kind].id)
        _need(row.id != source.id and row.project_id == project.id and row.artifact_type == kind
              and row.scope_ref_id is None and row.status == 'approved'
              and row.source_run_id == workflow.id, 'derived_asset_owner')
        derived_body = [planner._without_planning_meta(item) for item in row.content] if kind == 'volume_plan' else planner._without_planning_meta(row.content)
        source_body = [planner._without_planning_meta(item) for item in source.content] if kind == 'volume_plan' else planner._without_planning_meta(source.content)
        _need(derived_body == source_body,
              'derived_asset_body_changed')
        metas = [item['_meta'] for item in row.content] if kind == 'volume_plan' else [row.content['_meta']]
        _need(all(item.get('foundation_consumer_kind') == CONSUMER_KIND
                  and item.get('derived_from_candidate') == binding['source_candidates'][kind]
                  and item.get('original_paid_generation_aliased') is False
                  and not any(key in item for key in ('llm_run_id','input_hash','source_projection_sha256'))
                  for item in metas), 'derived_asset_provenance')
    _need(meta.get('derived_content_hashes') == {kind: content_hash(row.content) for kind,row in artifacts.items()},
          'derived_asset_content_changed')
    return {'schema': CONSUMER_KIND, 'binding': binding,
            'original_paid_generation_aliased': False, 'new_grade_or_credit_granted': False}


def _derived_content(source, project, binding):
    from bestseller.services import planner
    detached = SimpleNamespace(**{key:deepcopy(getattr(project,key,None)) for key in (
        'id','slug','title','genre','sub_genre','language','target_chapters','target_word_count','audience','metadata_json')})
    lineage = planner.planning_artifact_meta(detached)
    _need(detached.metadata_json.get('book_design_snapshot') == project.metadata_json.get('book_design_snapshot'),
          'book_design_changed')
    meta = {**lineage, 'foundation_consumer_kind': CONSUMER_KIND,
            'derived_from_candidate': binding['source_candidates'][source.artifact_type],
            'original_paid_generation_aliased': False,
            'reviewed_admission_workflow_id': binding['admission_workflow_id']}
    content = ([deepcopy(planner._without_planning_meta(item)) for item in source.content]
               if source.artifact_type == 'volume_plan' else deepcopy(planner._without_planning_meta(source.content)))
    if source.artifact_type == 'volume_plan':
        for item in content:
            item['_meta'] = deepcopy(meta)
    else:
        content['_meta'] = meta
    return content


async def complete_reviewed_volume_foundation(session, settings, *, project_id,
        revision_id, admission_workflow_id, expected_candidate_sha256, actor):
    """Atomically derive and pin a reviewed cohort; never dispatch or commit.

    expected_candidate_sha256 hashes the original Volume content INCLUDING
    root _meta, distinct from the admission's metadata-free candidate hash.
    Reject pending caller mutations: begin_nested would otherwise flush them
    outside the savepoint. Existing source candidates are never updated.
    """
    from bestseller.services import foundation_selection as selection
    from bestseller.services.truth_version import maybe_bump_project_truth_version

    _need(not session.new and not session.dirty and not session.deleted, 'clean_session_required')
    _need(isinstance(actor, str) and bool(actor.strip()), 'actor_required')
    project_id,revision_id,admission_workflow_id = (UUID(str(value)) for value in
        (project_id,revision_id,admission_workflow_id))
    identity = uuid5(NAMESPACE_URL, 'bestseller:reviewed-volume-foundation:' + str(project_id)
                     + ':' + str(revision_id) + ':' + str(admission_workflow_id))
    async with session.begin_nested():
        project = await session.get(ProjectModel, UUID(str(project_id)), populate_existing=True, with_for_update=True)
        _need(project is not None, 'project_missing')
        binding, candidates = await _binding(session, settings, project=project,
            revision_id=revision_id, admission_workflow_id=admission_workflow_id, lock=True)
        _need(binding['source_candidates']['volume_plan']['content_sha256'] == expected_candidate_sha256,
              'expected_candidate_changed')
        existing = await session.get(WorkflowRunModel, identity, populate_existing=True)
        if existing is not None:
            _need(existing.project_id == project.id and existing.metadata_json.get('verified_edit_binding') == binding,
                  'existing_consumption_changed')
            selected = await selection.select_completed_foundation(session, project=project,workflow_id=identity,settings=settings)
            _need(project.metadata_json.get(selection.POINTER) == selected, 'already_consumed_selection_changed')
            return selected
        # New adoption is an initial-foundation operation, never a mid-prose
        # rewrite or a way to reactivate a superseded candidate.
        latest = await session.scalar(select(PlanningArtifactVersionModel.id).where(
            PlanningArtifactVersionModel.project_id == project.id,
            PlanningArtifactVersionModel.artifact_type == 'volume_plan',
            PlanningArtifactVersionModel.scope_ref_id.is_(None),
            PlanningArtifactVersionModel.status == 'candidate').order_by(
                PlanningArtifactVersionModel.version_no.desc(),PlanningArtifactVersionModel.created_at.desc()).limit(1))
        _need(latest == candidates['volume_plan'].id, 'superseded_candidate')
        for model in (ChapterModel,ChapterDraftVersionModel,CanonFactModel,TimelineEventModel):
            _need(await session.scalar(select(model.id).where(model.project_id == project.id).limit(1)) is None,
                  'prose_or_knowledge_exists')
        live = await session.scalar(select(WorkflowRunModel.id).where(WorkflowRunModel.project_id == project.id,
            WorkflowRunModel.status.in_(['running','pending','queued'])).limit(1))
        _need(live is None, 'active_workflow')
        workflow = WorkflowRunModel(id=identity,project_id=project.id,workflow_type='complete_foundation_revision',
            scope_type='project',scope_id=project.id,status='running',current_step='derive_reviewed_volume_foundation',
            requested_by=actor.strip()[:64], idempotency_key='reviewed-volume-foundation:'+str(identity),
            metadata_json={'foundation_consumer_kind':CONSUMER_KIND,
                'source_dependency_revision_id':str(revision_id),'admission_workflow_id':str(admission_workflow_id),
                'verified_edit_binding':binding,'actor':actor.strip(),
                'original_paid_generation_aliased':False,'new_grade_or_credit_granted':False})
        session.add(workflow);await session.flush()
        derived = {}
        for kind, source in candidates.items():
            version = int((await session.scalar(select(func.max(PlanningArtifactVersionModel.version_no)).where(
                PlanningArtifactVersionModel.project_id==project.id,
                PlanningArtifactVersionModel.artifact_type==kind,PlanningArtifactVersionModel.scope_ref_id.is_(None)))) or 0)+1
            content = _derived_content(source,project,binding)
            row = PlanningArtifactVersionModel(project_id=project.id,artifact_type=kind,scope_ref_id=None,
                version_no=version,status='approved',schema_version=source.schema_version,content=content,
                source_run_id=identity,notes='Derived from exact candidate and physically verified source-hard admission; not generated.')
            session.add(row);derived[kind]=row
            maybe_bump_project_truth_version(project,artifact_type=ArtifactType(kind),content=content)
        await session.flush()
        workflow.metadata_json={**workflow.metadata_json,
            'artifact_ids':{kind:str(row.id) for kind,row in derived.items()},
            'derived_content_hashes':{kind:content_hash(row.content) for kind,row in derived.items()}}
        workflow.status='completed';workflow.current_step='reviewed_volume_edit_derived_cohort_complete'
        await session.flush()
        selected=await selection.pin_completed_foundation(session,project=project,workflow_id=identity,settings=settings)
        await session.flush()
        return selected
