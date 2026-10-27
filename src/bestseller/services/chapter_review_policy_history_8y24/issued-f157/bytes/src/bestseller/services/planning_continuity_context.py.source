"""Read-only, story-position-bound adopted context for continuation planning."""
from __future__ import annotations

import copy
from sqlalchemy import select
from bestseller.infra.db.models import CanonFactModel, ChapterModel, ChapterDraftVersionModel, TimelineEventModel
from bestseller.services.feedback import validate_adopted_knowledge_source
from bestseller.services.prompt_source_policy import content_hash


async def build_planning_continuity_context(session, *, project_id, before_chapter: int, allow_draft_references: bool = False) -> dict:
    if type(before_chapter) is not int or before_chapter <= 1:
        raise ValueError('continuation_requires_later_chapter')
    chapters = list(await session.scalars(select(ChapterModel).where(
        ChapterModel.project_id == project_id, ChapterModel.chapter_number < before_chapter,
    ).order_by(ChapterModel.chapter_number)))
    if [c.chapter_number for c in chapters] != list(range(1, before_chapter)):
        raise ValueError('continuation_prior_chapter_gap')
    adopted, bodies, by_chapter = [], [], {}
    qualified_pending, pending_bodies = [], []
    for chapter in chapters:
        drafts = list(await session.scalars(select(ChapterDraftVersionModel).where(
            ChapterDraftVersionModel.project_id == project_id,
            ChapterDraftVersionModel.chapter_id == chapter.id,
        ).order_by(ChapterDraftVersionModel.is_current.desc(), ChapterDraftVersionModel.version_no.desc())))
        accepted = None
        for draft in drafts:
            lineage, rejection = await validate_adopted_knowledge_source(session,
                project_id=project_id, chapter_id=chapter.id,
                source_chapter_draft_version_id=draft.id, chapter_md=draft.content_md or '', lock=False)
            if rejection is None:
                accepted = (draft, lineage)
                break
        if accepted is None:
            if not allow_draft_references:
                raise ValueError('continuation_prior_chapter_not_adopted')
            from bestseller.services.draft_continuity import select_reference,draft_mode_enabled
            from bestseller.infra.db.models import ProjectModel
            from bestseller.settings import apply_runtime_llm_profile,load_settings
            if not await draft_mode_enabled(session,project_id=project_id):
                raise ValueError('continuation_pending_mode_not_authorized')
            project=await session.get(ProjectModel,project_id)
            selected=await select_reference(session,apply_runtime_llm_profile(load_settings()),project=project,chapter=chapter)
            if selected is None:raise ValueError('continuation_prior_pending_not_qualified')
            draft,reference=selected
            proof={'chapter_id':str(chapter.id),'chapter_number':chapter.chapter_number,
                'draft_id':str(draft.id),'body_sha256':reference['body_sha256'],
                'reference_state':'pending_literary_review','qualification_receipt_id':reference['receipt_id'],
                'formal_adoption':False,'canon_authority':False}
            qualified_pending.append(proof)
            if chapter.chapter_number>=before_chapter-2:pending_bodies.append({**proof,'body':draft.content_md})
            continue
        draft, lineage = accepted
        proof = {'chapter_id': str(chapter.id), 'chapter_number': chapter.chapter_number,
            'draft_id': str(draft.id), 'body_sha256': lineage['source_body_sha256'],
            'adoption_state': lineage['adoption_state']}
        adopted.append(proof)
        by_chapter[str(chapter.id)] = proof
        if chapter.chapter_number >= before_chapter - 2:
            bodies.append({**proof, 'body': draft.content_md})

    def source_matches(data, chapter_id):
        proof = by_chapter.get(str(chapter_id))
        source = data.get('source_lineage') or {}
        return (proof is not None and source.get('project_id') == str(project_id)
            and source.get('chapter_id') == str(chapter_id)
            and source.get('source_chapter_draft_version_id') == proof['draft_id']
            and source.get('source_body_sha256') == proof['body_sha256']
            and data.get('knowledge_lineage_status') not in {'superseded', 'stale', 'quarantined'})

    facts = []
    rows = await session.scalars(select(CanonFactModel).where(
        CanonFactModel.project_id == project_id, CanonFactModel.valid_from_chapter_no < before_chapter,
    ).order_by(CanonFactModel.id))
    for fact in rows:
        if not fact.is_current and fact.valid_to_chapter_no is None:
            continue  # Retired invalid projections are not historical truth.
        if fact.valid_to_chapter_no is not None and fact.valid_to_chapter_no < before_chapter:
            continue
        if source_matches(fact.value_json, fact.source_chapter_id):
            facts.append({'id': str(fact.id), 'subject': fact.subject_label, 'predicate': fact.predicate,
                'value': copy.deepcopy(fact.value_json.get('value')),
                'evidence': copy.deepcopy(fact.value_json.get('evidence')),
                'source_chapter_id': str(fact.source_chapter_id),
                'valid_from_chapter': fact.valid_from_chapter_no})
    events = []
    rows = await session.scalars(select(TimelineEventModel).where(
        TimelineEventModel.project_id == project_id,
        TimelineEventModel.chapter_id.in_([c.id for c in chapters]),
    ).order_by(TimelineEventModel.story_order, TimelineEventModel.id))
    for event in rows:
        if source_matches(event.metadata_json, event.chapter_id):
            events.append({'id': str(event.id), 'name': event.event_name,
                'evidence': copy.deepcopy(event.metadata_json.get('evidence')),
                'source_chapter_id': str(event.chapter_id), 'story_order': str(event.story_order)})
    context = {'schema': 'planning-continuity.v1', 'project_id': str(project_id),
        'before_chapter': before_chapter, 'adopted_sources': adopted,
        'recent_adopted_prose': bodies, 'source_bound_canon': facts, 'source_bound_timeline': events,
        'authority': 'Only adopted prose establishes occurrences. Extracted observations retain their source; plans and character claims are not objective events.'}
    if qualified_pending:
        context.update(qualified_pending_sources=qualified_pending,recent_qualified_pending_prose=pending_bodies,
            authority='Adopted entries alone establish formal occurrences. Qualified pending manuscripts are explicitly provisional draft references; their events never enter formal Canon from this context.')
    return {**context, 'context_hash': content_hash(context)}


async def build_authorized_planning_context(session, *, project_id, before_chapter: int, project=None) -> dict:
    """Draft producers opt in explicitly; formal adopted-only readers stay strict."""
    from bestseller.services.draft_continuity import draft_mode_enabled
    if not await draft_mode_enabled(session,project_id=project_id,project=project):
        return await build_planning_continuity_context(session,project_id=project_id,before_chapter=before_chapter)
    return await build_planning_continuity_context(session,project_id=project_id,before_chapter=before_chapter,allow_draft_references=True)
