"""Supersede stale planning projections for one fully verified run window."""
from copy import deepcopy
from types import SimpleNamespace
from uuid import UUID
from sqlalchemy import select
from bestseller.infra.db.models import ChapterModel, WorkflowRunModel
from bestseller.services.prompt_source_policy import content_hash

KEY = 'verified_prefix_window_activation'
SCHEMA = 'verified-prefix-window-activation.v1'


def need(ok, reason):
    if not ok:
        raise ValueError('prefix_window_activation_' + reason)


async def _proof(session, settings, project):
    from bestseller.services.creation_configuration import read_window
    from bestseller.services.chapter_outline_readiness import verify_outline_prefix_readiness
    from bestseller.services.prefix_source_proposal import SCHEMA as SOURCE_SCHEMA
    window = read_window(project)
    need(window is not None, 'configured_window_required')
    rows = list(await session.scalars(select(ChapterModel).where(ChapterModel.project_id == project.id,
        ChapterModel.chapter_number.between(window.start, window.end))))
    need({r.chapter_number for r in rows} == window.numbers(), 'window_not_materialized')
    proofs = []
    for chapter in sorted(rows, key=lambda row: row.chapter_number):
        marker = (chapter.metadata_json or {}).get('outline_prefix_readiness') or {}
        need(bool(marker.get('artifact_id')), 'marker_missing')
        from bestseller.infra.db.models import SceneCardModel
        scenes = list(await session.scalars(select(SceneCardModel).where(SceneCardModel.chapter_id == chapter.id)
            .order_by(SceneCardModel.scene_number)))
        receipt = await verify_outline_prefix_readiness(session, project=project,
            artifact_id=UUID(marker['artifact_id']), chapter=chapter, scenes=scenes, settings=settings)
        workflow = await session.get(WorkflowRunModel, UUID(receipt['workflow_run_id']))
        source = workflow.metadata_json['source_record']
        need(source.get('schema') == SOURCE_SCHEMA, 'explicit_source_proposal_required')
        parent = await session.get(WorkflowRunModel, UUID(source['parent_workflow_id']))
        resolved_parent = None
        if (parent is not None and parent.status == 'machine_blocked'
                and parent.metadata_json.get('phase') == 'admission_protocol_blocked'):
            from bestseller.services.planning_source_resolution import SCHEMA as RESOLUTION_SCHEMA, verify_resolution
            admission = receipt.get('source_admission') or {}
            if admission.get('schema') == RESOLUTION_SCHEMA:
                state = workflow.metadata_json
                plan = await verify_resolution(session, project=project,
                    resolution_id=admission['resolution_id'], candidate=state['candidates'][-1]['candidate'],
                    source_payload=state['source_payload'])
                need(plan['parent_workflow_id'] == str(parent.id)
                    and chapter.chapter_number in plan['chapter_numbers']
                    and admission['resolution_plan_sha256'] == plan['plan_sha256'], 'resolved_parent_binding')
                resolved_parent = {'resolution_id': admission['resolution_id'], 'plan_sha256': plan['plan_sha256']}
        need(parent is not None and (
            parent.status == 'machine_blocked' and parent.metadata_json.get('phase') == 'rejected'
            or parent.status == 'completed' and parent.metadata_json.get('phase') == 'completed'
            or resolved_parent is not None
        ), 'prior_terminal_prefix_required')
        if parent.status == 'completed':
            parent_receipt = await verify_outline_prefix_readiness(session, project=project,
                artifact_id=UUID(parent.metadata_json['artifact_id']), settings=settings)
            need(parent_receipt['workflow_run_id'] == str(parent.id), 'prior_receipt_required')
        proofs.append({'chapter_number': chapter.chapter_number, 'artifact_id': marker['artifact_id'],
            'receipt_hash': content_hash(receipt), 'source_record_hash': content_hash(source),
            'prior_workflow_id': str(parent.id), 'prior_metadata_hash': content_hash(parent.metadata_json),
            **({'independent_source_resolution': resolved_parent} if resolved_parent is not None else {})})
    return {'window_hash': (project.metadata_json or {})['execution_window_hash'],
        'window': window.model_dump(mode='json'), 'proofs': proofs}


async def verify_activated_prefix_window(session, settings, project):
    receipt = (project.metadata_json or {}).get(KEY)
    if receipt is None:
        return
    need(isinstance(receipt, dict) and receipt.get('schema') == SCHEMA, 'receipt_schema')
    from bestseller.services.pipelines import _project_blocked_for_structural_repair
    need(not _project_blocked_for_structural_repair(project), 'independent_structural_block')
    current = await _proof(session, settings, project)
    need(current == receipt.get('binding'), 'source_or_window_changed')
    need(receipt.get('whole_book_approved') is False and receipt.get('old_rejection_overwritten') is False,
        'authority_escalation')


async def activate_verified_prefix_window(session, settings, *, project, actor, reason):
    from bestseller.services.gate_registry import project_blocks_all_prose_generation, project_resume_is_terminally_blocked
    from bestseller.services.production_control import load_control_state
    need(bool(actor) and bool(reason), 'actor_reason')
    metadata = deepcopy(project.metadata_json or {})
    control = await load_control_state(session, project.id)
    need(not control.halted and str(project.status) not in {'paused', 'cancelled', 'archived'}, 'halted')
    # Remove exactly the two stale projections in a comparison copy. All
    # independent structural flags/reasons retain their normal force.
    comparison = {**metadata, 'planning_status': 'writing'}
    comparison.pop(KEY, None)
    probe = SimpleNamespace(status='writing', metadata_json=comparison)
    from bestseller.services.pipelines import _project_blocked_for_structural_repair
    need(not _project_blocked_for_structural_repair(probe)
         and not project_blocks_all_prose_generation(probe)
         and not project_resume_is_terminally_blocked(comparison), 'independent_structural_block')
    binding = await _proof(session, settings, project)
    prior = metadata.get(KEY)
    if prior is not None and prior.get('binding') == binding:
        await verify_activated_prefix_window(session, settings, project)
        return deepcopy(prior)
    need(str(project.status) == 'needs_replan' or metadata.get('planning_status') == 'needs_replan'
         or prior is not None, 'no_stale_planning_projection')
    receipt = {'schema': SCHEMA, 'binding': binding, 'actor': actor, 'reason': reason,
        'previous_status': str(project.status), 'previous_planning_status': metadata.get('planning_status'),
        'whole_book_approved': False, 'unreviewed_tail_approved': False, 'old_rejection_overwritten': False}
    history = deepcopy(metadata.get('prefix_window_activation_history') or [])
    if prior is not None:
        history.append(prior)
    project.metadata_json = {**metadata, KEY: receipt, 'prefix_window_activation_history': history,
        'planning_status': 'writing'}
    project.status = 'writing'
    await session.flush()
    return deepcopy(receipt)
