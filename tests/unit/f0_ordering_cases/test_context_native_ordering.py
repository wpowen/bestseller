"""Real SQLite context readers, without grades, adoption or approval fixtures.

This is source-composition evidence, not a legal book-production prefix or E21
PostgreSQL proof. All source rows have fixed persistent identities. The engine
reverses unordered SELECT results; SQL/Python readers must preserve source bytes.
"""
import json
import zlib
from types import SimpleNamespace

from pgvector.sqlalchemy import Vector
import pytest
from sqlalchemy import JSON, MetaData, Uuid, event, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.schema import CreateTable

from bestseller.infra.db import models as m
from bestseller.services import context, feedback
from bestseller.settings import load_settings

from .f0_ordering_support import source_rows, uid

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.fixture
async def source_db(tmp_path):
    path = tmp_path / 'f0_source_context_test.sqlite3'
    assert path.name.endswith('_test.sqlite3')
    engine = create_async_engine(f'sqlite+aiosqlite:///{path}')
    @event.listens_for(engine.sync_engine, 'connect')
    def sqlite_function_io(connection, record):
        # Only dialect IO adaptation in this single-connection component test.
        # This neither exercises nor claims PostgreSQL advisory-lock safety.
        connection.create_function('hashtextextended', 2, lambda value, seed: zlib.crc32(value.encode(), seed))
        connection.create_function('pg_advisory_xact_lock', 1, lambda key: None)
    def create_tables(connection):
        # Dialect adaptation only. No grade/current/promoted source is seeded.
        metadata = MetaData()
        for original in m.Base.metadata.tables.values():
            table = original.to_metadata(metadata)
            for column in table.c:
                if isinstance(column.type, JSONB):
                    column.type = JSON()
                elif isinstance(column.type, PGUUID):
                    column.type = Uuid(as_uuid=True)
                elif isinstance(column.type, Vector):
                    column.type = JSON()
                if column.server_default and '::jsonb' in str(column.server_default.arg):
                    column.server_default = None
        for table in metadata.sorted_tables:
            connection.execute(CreateTable(table))
    async with engine.begin() as connection:
        await connection.run_sync(create_tables)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        project = m.ProjectModel(id=uid(1000), slug='f0-native-source-context', title='Ordinary source rows',
                                 genre='test', language='en', target_word_count=8000, target_chapters=4,
                                 metadata_json={})
        previous = m.ChapterModel(id=uid(1001), project_id=project.id, chapter_number=1,
                                 title='Earlier', chapter_goal='Introduce source rows', metadata_json={})
        chapter = m.ChapterModel(id=uid(1002), project_id=project.id, chapter_number=2,
                                title='Current', chapter_goal='Compare input order', metadata_json={})
        scene = m.SceneCardModel(id=uid(1003), project_id=project.id, chapter_id=chapter.id,
                                scene_number=1, scene_type='setup', title='Current scene', participants=[],
                                purpose={'story':'Compare input order'}, entry_state={}, exit_state={},
                                target_word_count=1500, metadata_json={})
        session.add(project); await session.flush()
        session.add_all([previous, chapter]); await session.flush()
        session.add(scene); await session.commit()
        yield SimpleNamespace(session=session, project=project, chapter=chapter,
                              previous=previous, scene=scene, engine=engine)
    await engine.dispose()




async def seed_context(case):
    arcs, *groups = source_rows(case)
    case.session.add_all(arcs); await case.session.flush()
    case.session.add_all([row for group in groups for row in group]); await case.session.commit()


@pytest.mark.parametrize('kind', ['scene', 'chapter'])
async def test_f0_08_to_11_native_context_bytes_and_cap_membership(source_db, kind):
    case = source_db
    await seed_context(case)
    settings = load_settings(env={})
    settings.generation.prompt_context_scenes = 2
    results=[]
    for reversed_selects in ('OFF','ON'):
        await case.session.execute(text(f'PRAGMA reverse_unordered_selects = {reversed_selects}'))
        if kind == 'scene':
            packet = await context.build_scene_writer_context_from_models(
                case.session,settings,case.project,case.chapter,case.scene)
        else:
            packet = await context.build_chapter_writer_context(
                case.session,settings,case.project.slug,2)
        results.append(packet.model_dump(mode='json'))
        await case.session.commit()
    assert json.dumps(results[0],sort_keys=True) == json.dumps(results[1],sort_keys=True)
    first=results[0]
    event_cap = 2 if kind=='scene' else 4
    assert [e['event_name'] for e in first['recent_timeline_events']] == [f'event-{i}' for i in range(5,5-event_cap,-1)]
    assert first['recent_timeline_events'][0]['consequences'] == ['second','first']
    assert [c['clue_code'] for c in first['unresolved_clues']] == [f'clue-{i}' for i in range(1,7 if kind=='scene' else 9)]
    assert [p['payoff_code'] for p in first['planned_payoffs']] == [f'payoff-{i}' for i in range(1,5 if kind=='scene' else 7)]
    assert first['structure_beat_name'] == 'structure-1'


async def test_f0_13_and_14_actual_feedback_source_reader_and_full_prompt(source_db):
    case=source_db
    await seed_context(case)
    characters=[m.CharacterModel(id=uid(1600+i),project_id=case.project.id,name=f'person-{i}',role='support',metadata_json={}) for i in range(1,4)]
    plans=[m.AntagonistPlanModel(id=uid(1700+i),project_id=case.project.id,plan_code=f'plan-{i}',title=f'plan-{i}',
                               antagonist_label='Opponent',threat_type='test',goal='A distinct goal',
                               current_move='A current move',next_countermove='A later reaction',status='active',metadata_json={}) for i in range(1,4)]
    case.session.add_all([*characters,*plans]);await case.session.commit()
    prompts=[]
    for reverse in ('OFF','ON'):
        await case.session.execute(text(f'PRAGMA reverse_unordered_selects = {reverse}'))
        inputs=await feedback.load_feedback_prompt_context(case.session,project_id=case.project.id,chapter=case.chapter)
        prompts.append(feedback._build_feedback_extraction_prompts(chapter=case.chapter,chapter_md='The present source.',**inputs))
        assert [c.name for c in inputs['characters']] == ['person-1','person-2','person-3']
        assert [p.plan_code for p in inputs['active_antagonist_plans']] == ['plan-1','plan-2','plan-3']
        assert [a.arc_code for a in inputs['active_arcs']] == ['arc-1','arc-2','arc-3']
        assert [c.clue_code for c in inputs['unresolved_clues']] == [f'clue-{i}' for i in range(1,10)]
    assert prompts[0] == prompts[1]
    # No source fixture may silently grant downstream production authority.
    assert not list(await case.session.scalars(text('SELECT id FROM quality_scores')))
    assert not list(await case.session.scalars(text('SELECT id FROM chapter_draft_versions')))
