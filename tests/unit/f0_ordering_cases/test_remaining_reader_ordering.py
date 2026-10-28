"""F0-19 ordinary reader rows: actual SQLite context, no grades/adoption."""

import pytest

from bestseller.infra.db import models as m
from bestseller.services import context
from bestseller.settings import load_settings

from .f0_ordering_support import uid
from .test_context_native_ordering import source_db as source_db

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


class ReverseReaderRows:
    """Reverse returned target rows after actual SQL, testing Python final order."""
    def __init__(self, session):
        self.session = session

    def __getattr__(self, name):
        return getattr(self.session, name)

    async def scalars(self, statement, *args, **kwargs):
        values = await self.session.scalars(statement, *args, **kwargs)
        entities = {d.get('entity') for d in statement.column_descriptions}
        if entities & {m.ReaderKnowledgeEntryModel, m.RelationshipEventModel}:
            return list(reversed(list(values)))
        return values


@pytest.mark.parametrize('scope', ['scene', 'chapter'])
@pytest.mark.parametrize('reverse_adapter', [False, True])
async def test_reader_and_milestone_ties_choose_same_cap_members_and_order(source_db, scope, reverse_adapter):
    r = source_db
    r.scene.participants = ['A']
    # Insert reverse physical arrival, plus a lower-priority historical row.
    readers = [m.ReaderKnowledgeEntryModel(id=uid(4100+i), project_id=r.project.id,
        chapter_number=1, knowledge_item=f'reader-{i}', audience='reader_only', metadata_json={})
        for i in range(11, 0, -1)]
    readers.append(m.ReaderKnowledgeEntryModel(id=uid(4001), project_id=r.project.id,
        chapter_number=0, knowledge_item='older', audience='reader_only', metadata_json={}))
    events = [m.RelationshipEventModel(id=uid(4200+i), project_id=r.project.id,
        chapter_number=1, scene_number=1, character_a_label='A', character_b_label='B',
        event_description=f'event-{i}', relationship_change='A visible change',
        is_milestone=True, metadata_json={}) for i in range(11, 0, -1)]
    r.session.add_all([*readers, *events])
    await r.session.commit()
    settings = load_settings(env={})
    settings.generation.prompt_context_scenes = 2
    session = ReverseReaderRows(r.session) if reverse_adapter else r.session
    if scope == 'scene':
        packet = await context.build_scene_writer_context_from_models(session, settings, r.project, r.chapter, r.scene)
    else:
        packet = await context.build_chapter_writer_context(session, settings, r.project.slug, 2)
    assert [v.id for v in packet.reader_knowledge_entries] == [uid(4100+i) for i in range(1, 11)]
    cap = 8 if scope == 'scene' else 10
    assert [v.id for v in packet.relationship_milestones] == [uid(4200+i) for i in range(1, cap+1)]
