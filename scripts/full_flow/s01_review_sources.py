"""Read real retained S01 inputs under two PG plans; no dispatch or DB writes."""
import asyncio
from copy import deepcopy
import json
import os
from pathlib import Path
from uuid import UUID

from sqlalchemy import event, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bestseller.services import recorded_review_sources as recorded
from bestseller.services import review_source_ordering as ordering
from bestseller.settings import load_settings
from support.cp1_rehearsal import RunState, all_snapshots, comparison_session, require

OUT = Path(os.environ['FULL_FLOW_S01_EVIDENCE'])
STATE = RunState(OUT / 'RESULT.json')
IDS = dict(project_id=UUID('d407ca85-9a9b-4564-a033-6bc529e0dd76'),
           chapter_id=UUID('0bf75fb2-20c8-4239-95d3-a94ed7ab9678'),
           draft_id=UUID('da40bde2-9f60-47d7-bf12-22a59efc0666'))
CYCLE = '7d84c773c45db019a6f3e3232842a8c4eb489922d46136f93ed909464e0d0887'
REPORT_ID = '6eb05f3f-bb8b-4d5c-b068-ea3459e391a6'


def nodes(value):
    if isinstance(value, dict):
        return ([value['Node Type']] if 'Node Type' in value else []) + [
            n for v in value.values() for n in nodes(v)]
    if isinstance(value, list):
        return [n for v in value for n in nodes(v)]
    return []


async def main():
    engine = None
    STATE.data.update(schema='full-flow-s01-recorded-source-plans.v1',
                      failed_workflow_type='S01 archived review input proof', model_calls=0,
                      production_access=False, scope='archived input authenticity and nonreuse only; not full HTTP S01/E21')
    try:
        settings = load_settings()
        url = make_url(settings.database.url).set(database='full_flow_s01_20261028_test_db')
        require(url.host.startswith('int-full-flow-') and url.database.endswith('_test_db'),
                'S01_OWNED_DATABASE', 'own isolated S01 database', str(url))
        settings.database.url = url.render_as_string(hide_password=False)
        settings.artifact_store.local_dir = Path('/s01-input-artifacts')
        engine = create_async_engine(url, connect_args={'server_settings': {
            'default_transaction_read_only': 'on', 'timezone': 'UTC'}})
        counts = {'select': 0, 'set': 0, 'write': 0, 'flush': 0, 'commit': 0, 'model': 0}
        def query_guard(conn, cursor, statement, parameters, context, many):
            kind = statement.lstrip().split(None, 1)[0].upper()
            require(kind in {'SELECT', 'SET', 'EXPLAIN', 'SHOW'},
                    'S01_SELECT_ONLY', 'SELECT/SET/EXPLAIN/SHOW', kind)
            counts['select' if kind in {'SELECT', 'EXPLAIN', 'SHOW'} else 'set'] += 1
        event.listen(engine.sync_engine, 'before_cursor_execute', query_guard)
        factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
        async with engine.connect() as baseline:
            await comparison_session(baseline)
            before = await all_snapshots(baseline)
            async with factory() as session:
                observations = []
                for mode in ('index', 'sequential'):
                    STATE.checkpoint('classify_' + mode)
                    for feature, enabled in [('seqscan', mode == 'sequential'),
                                             ('indexscan', mode == 'index'),
                                             ('indexonlyscan', mode == 'index'), ('bitmapscan', False)]:
                        await session.execute(text('SET LOCAL enable_' + feature + '=' + ('on' if enabled else 'off')))
                    plan = await session.scalar(text('EXPLAIN (FORMAT JSON) SELECT id FROM review_reports WHERE id=:id'), {'id': REPORT_ID})
                    (OUT / (mode + '-explain.json')).write_text(json.dumps(plan, ensure_ascii=False, indent=2))
                    actual_nodes = nodes(plan)
                    require(any('Index' in n for n in actual_nodes) if mode == 'index' else 'Seq Scan' in actual_nodes,
                            'S01_ACTUAL_QUERY_PLAN.' + mode, mode, actual_nodes)
                    view = await recorded.read_archived_review_sources(session, settings, **IDS, cycle_id=CYCLE)
                    classification = ordering._source_epoch(view)
                    require(classification['code'] == 'pre_f0_source_unprovable',
                            'S01_NONREUSE.' + mode, 'pre_f0_source_unprovable', classification)
                    observations.append({'mode': mode, 'classification': deepcopy(classification),
                                         'paid_calls': ordering._call_receipts(view), 'plan_nodes': actual_nodes})
                require(observations[0]['classification'] == observations[1]['classification']
                        and observations[0]['paid_calls'] == observations[1]['paid_calls'],
                        'S01_TWO_PLANS_EXACT_EQUAL', observations[0], observations[1])
                await session.rollback()
            after = await all_snapshots(baseline)
            require(before == after, 'S01_ALL_NATIVE_ROWS_UNCHANGED', before, after)
            STATE.checkpoint('same_nonreuse_full_archived_input_verified', candidate_success=True,
                             classifications=observations, counters=counts,
                             row_counts={k: len(v['rows']) for k, v in before.items()},
                             unchanged_full_tables=True)
    except BaseException as error:
        STATE.failure(error, step=STATE.data.get('current_stage', 'source_setup'))
    finally:
        if engine is not None:
            await engine.dispose()
        STATE.finish(final_checks_complete=not STATE.failed)
    return 1 if STATE.failed else 0


if __name__ == '__main__':
    raise SystemExit(asyncio.run(main()))
