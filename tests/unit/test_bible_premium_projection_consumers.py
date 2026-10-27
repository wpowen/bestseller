"""Current source selection must survive real gate and hydration consumers."""
from copy import deepcopy
import socket

import pytest

from bestseller.services.category_hard_engines import build_category_engine_fixture, evaluate_category_hard_engine
from bestseller.services.material_density import hydrate_premium_capability_metadata
from bestseller.services.premium_book_gate import evaluate_premium_project_readiness
from bestseller.services.story_bible_projection import validated_premium_projection
from test_adopted_bible_lineage import db as db, knowledge_db as knowledge_db, stub_llm, update
from test_knowledge_downstream_source_dependencies import _replace_source

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv('BESTSELLER_LINEAGE_PG', '0')

    def forbidden(*_args, **_kwargs):
        raise AssertionError('Projection consumer tests forbid network/provider calls')

    monkeypatch.setattr(socket.socket, 'connect', forbidden)


@pytest.fixture
async def retired(db, monkeypatch):
    session, project, *_ = db
    baseline = build_category_engine_fixture('realistic-life', good=True)
    project.genre = '现实'
    project.metadata_json = {**project.metadata_json, **baseline,
        'genre_intent_contract': {'genre_key': 'realistic', 'prompt_pack_key': 'realistic-life', 'source': 'user'}}
    await session.flush()
    update_payload = {'premium_engine_updates': {'relationship_events': [{
        'character_a': '甲', 'character_b': '乙', 'axis': 'trust',
        'active_choice': '甲主动把保管记录交给乙。', 'after': '乙开始委托甲保管记录。',
    }]}}
    stub_llm(monkeypatch, update_payload)
    assert (await update(db))['extraction_status'] == 'ok'
    raw = deepcopy(project.metadata_json['premium_state_ledger'])
    previous_snapshot = deepcopy(project.metadata_json['premium_state_snapshot'])
    replacement = await _replace_source(db, '观测员没有再与同伴交换记录。')
    stub_llm(monkeypatch, {})
    assert (await update(replacement))['extraction_status'] == 'empty'
    await session.commit()
    assert project.metadata_json['premium_state_ledger'] == raw
    assert project.metadata_json['premium_state_snapshot'] != previous_snapshot
    history = project.metadata_json['premium_state_source_projection']['projection_history']
    assert any(item['previous_values'].get('premium_state_snapshot') == previous_snapshot for item in history)
    return replacement


async def test_gate_uses_paired_current_view_without_erasing_original_ledger(retired):
    project = retired[1]
    before = deepcopy(project.metadata_json)
    view = validated_premium_projection(before)
    assert len(before['premium_state_ledger']['relationship_events']) == 2
    assert len(view['active_ledger']['relationship_events']) == 1
    assert evaluate_category_hard_engine(before, category_key='realistic-life').passed
    assert evaluate_premium_project_readiness(before, genre='现实', sub_genre='现实百态').passed
    mixed = deepcopy(before)
    mixed.pop('premium_state_source_projection')
    assert not evaluate_category_hard_engine(mixed, category_key='realistic-life').passed
    assert project.metadata_json == before


@pytest.mark.parametrize('bad', ['raw', 'selected', 'snapshot', 'owner', 'none'])
async def test_tampered_current_view_blocks_both_gates_without_writes(retired, bad):
    original = deepcopy(retired[1].metadata_json)
    metadata = deepcopy(original)
    view = metadata['premium_state_source_projection']
    if bad == 'raw':
        metadata['premium_state_ledger']['relationship_events'].pop()
    elif bad == 'selected':
        view['active_ledger']['relationship_events'].clear()
    elif bad == 'snapshot':
        metadata['premium_state_snapshot'] = {'passed': True}
    elif bad == 'owner':
        view['receipt']['project_id'] = '11111111-1111-1111-1111-111111111111'
    else:
        metadata['premium_state_source_projection'] = None
    assert not evaluate_category_hard_engine(metadata, category_key='realistic-life').passed
    assert not evaluate_premium_project_readiness(metadata, genre='现实').passed
    assert retired[1].metadata_json == original


async def test_hydration_does_not_reintroduce_retired_events(retired):
    session, project, *_ = retired
    before = deepcopy(project.metadata_json)
    result = await hydrate_premium_capability_metadata(session, project_id=str(project.id),
        pack_id='category_realistic_life_zh')
    assert result['premium_state_passed']
    assert project.metadata_json['premium_state_ledger'] == before['premium_state_ledger']
    assert project.metadata_json['premium_state_snapshot'] == before['premium_state_snapshot']
    assert project.metadata_json['premium_state_source_projection'] == before['premium_state_source_projection']
    assert evaluate_category_hard_engine(project.metadata_json, category_key='realistic-life').passed
