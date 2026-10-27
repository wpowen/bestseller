"""Actual feedback entry uses the adopted prefix, never a mutable cache."""
from copy import deepcopy
import socket

import pytest
from sqlalchemy import select
from test_character_snapshot_sources import configured as configured, db as db, extract
from test_adopted_knowledge_lineage import stub_llm
from test_knowledge_downstream_source_dependencies import _next_chapter

from bestseller.infra.db.models import CharacterStateSnapshotModel
from bestseller.services import feedback, llm, story_bible
from bestseller.services.character_snapshot_sources import body_hash
from bestseller.settings import load_settings

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv('BESTSELLER_LINEAGE_PG', '0')
    def forbidden(*args, **kwargs):
        raise AssertionError('feedback prompt tests forbid network/providers')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(llm, '_call_litellm', forbidden)


@pytest.mark.parametrize('blocked', [False, True])
async def test_actual_feedback_roster_cannot_reintroduce_withdrawn_or_current_cache(
    configured, monkeypatch, blocked,
):
    session, project, chapter, draft, character = configured
    prefix_fact = draft.content_md
    await extract(configured, monkeypatch, {'character_states': [{
        'character_name': character.name,
        'arc_state': 'SOURCE_QUALIFIED_PREFIX_ARC',
        'knowledge_gained': [prefix_fact],
    }]})
    snapshot = await session.scalar(select(CharacterStateSnapshotModel))
    retained = deepcopy(snapshot.source_binding)
    character.arc_state = 'STALE_MUTABLE_ROW_ARC'
    character.knowledge_state_json = {'knows': ['STALE_MUTABLE_ROW_FACT', 'SECOND_STALE_FACT']}
    if blocked:
        key = f'{draft.id}:{body_hash(draft.content_md)}'
        chapter.metadata_json = {**chapter.metadata_json, 'continuity_admission_by_source': {
            key: {'draft_id': str(draft.id), 'body_sha256': body_hash(draft.content_md),
                  'passed': False},
        }}
    await session.commit()
    later = await _next_chapter(configured[:4], 'The witness describes a separate present action.')
    await session.commit()
    calls = stub_llm(monkeypatch, feedback, {})
    await feedback.extract_chapter_feedback(
        session, load_settings(), project_id=project.id, chapter=later[2],
        chapter_md=later[3].content_md, source_chapter_draft_version_id=later[3].id,
    )
    assert len(calls) == 1
    request = calls[0]
    assert request.prompt_version == '2.1-bound-character-roster'
    assert 'STALE_MUTABLE_ROW_' not in request.user_prompt
    assert ('SOURCE_QUALIFIED_PREFIX_ARC' in request.user_prompt) is not blocked
    # The existing compact roster reports counts, not the fact text. Two
    # stale mutable facts must not replace the one source-qualified fact.
    assert ('knowledge=knows(1)' in request.user_prompt) is not blocked
    assert 'knowledge=knows(2)' not in request.user_prompt
    await session.refresh(snapshot)
    assert snapshot.source_binding == retained


async def test_character_source_read_failure_blocks_actual_feedback_before_gateway(
    configured, monkeypatch,
):
    session, project, chapter, draft, _ = configured
    calls = stub_llm(monkeypatch, feedback, {})
    async def unavailable(*args, **kwargs):
        raise RuntimeError('character_source_unavailable')
    monkeypatch.setattr(story_bible, 'get_effective_character_state', unavailable)
    with pytest.raises(RuntimeError, match='character_source_unavailable'):
        await feedback.extract_chapter_feedback(
            session, load_settings(), project_id=project.id, chapter=chapter,
            chapter_md=draft.content_md, source_chapter_draft_version_id=draft.id,
        )
    assert calls == []
