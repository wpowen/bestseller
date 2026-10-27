"""Restore only independently proved runtime length edits to a pinned plan."""
from copy import deepcopy
from types import SimpleNamespace
from uuid import UUID
from sqlalchemy import inspect
from bestseller.infra.db.models import PlanningArtifactVersionModel,SceneCardModel

async def restore_prefix_runtime_scene_targets(session,settings,*,project,chapter,scenes):
    from bestseller.services import chapter_outline_readiness as ready
    marker=(chapter.metadata_json or {}).get('outline_prefix_readiness')
    if not marker:
        return {'status':'not_source_bound'}
    artifact=await session.get(PlanningArtifactVersionModel,UUID(marker['artifact_id']))
    await ready.verify_outline_prefix_readiness(session,project=project,artifact_id=artifact.id,settings=settings)
    from bestseller.domain.workflow import ChapterOutlineBatchInput
    approved=next(c for c in artifact.content['chapters'] if c['chapter_number']==chapter.chapter_number)
    typed=ChapterOutlineBatchInput.model_validate({'chapters':[approved]}).chapters[0]
    targets={row.scene_number:row.target_word_count for row in typed.scenes}
    staged=[];changes=[]
    for scene in scenes:
        if scene in session.dirty:
            raise ValueError('prefix_runtime_target_unflushed_source_change')
        await session.refresh(scene, with_for_update=True)
        original=targets.get(scene.scene_number)
        values={prop.key:deepcopy(getattr(scene,prop.key)) for prop in inspect(SceneCardModel).column_attrs}
        if scene.target_word_count!=original:
            meta=scene.metadata_json or {}
            if (meta.get('auto_repair_original_target_word_count')!=original
                or meta.get('auto_repair_adjusted_target_word_count')!=scene.target_word_count
                or not meta.get('auto_repair_length_scale')):
                raise ValueError('prefix_runtime_target_origin_unproved')
            changes.append({'scene_id':str(scene.id),'number':scene.scene_number,'before':scene.target_word_count,
                'approved':original,'runtime_receipt':deepcopy(meta)})
            values['target_word_count']=original
        staged.append(SimpleNamespace(**values))
    ready._verify_materialized_content(chapter,staged,approved)
    after_hash=ready.outline_prefix_projection_hash(chapter,staged)
    if after_hash!=marker['projection_hash']:
        raise ValueError('prefix_runtime_target_other_plan_difference')
    if not changes:
        return {'status':'already_exact','projection_hash':after_hash}
    receipt={'schema':'prefix-runtime-target-restoration.v1','artifact_id':str(artifact.id),
        'marker_unchanged':deepcopy(marker),'before_projection_hash':ready.outline_prefix_projection_hash(chapter,scenes),
        'restored_projection_hash':after_hash,'changes':changes,'grade_overwritten':False,'new_model_calls':0}
    for scene in scenes:
        scene.target_word_count=targets[scene.scene_number]
    history=deepcopy((chapter.metadata_json or {}).get('deterministic_audit_runtime_target_restoration') or [])
    if receipt not in history:history.append(receipt)
    chapter.metadata_json={**chapter.metadata_json,'deterministic_audit_runtime_target_restoration':history}
    await session.flush()
    return receipt
