"""Resumable serial writing with observable rolling progress warnings."""
from tools.runtime_support import QuotaExhausted, ProjectError
from collections import Counter
import datetime as dt
import fcntl
from pathlib import Path
import re
import signal
import time

import yaml

from .client import Client
from .config import Book, knowledge, style_rules
from .export import export
from .longchapter import write_chapter
from .longmemory import compact_state
from .serialstate import manage_state, serialize_state
from .longplan import chapter_window, overall
from .longutil import ask, costs
from .storage import load, now, save
from .text import pack, chinese_count
REVISION = 's5a-r3'


def usage(client):
    row = costs(client)
    row['normal_calls'] = sum(c.get('attempt', 1) == 1 and re.fullmatch(r'ch\d+-(draft|review|fix|state)', c['stage']) is not None
                              for c in client.calls)
    row['planning_calls'] = sum(c.get('attempt', 1) == 1 and c['stage'].startswith('plan-') for c in client.calls)
    row['memory_calls'] = sum(c.get('attempt', 1) == 1 and c['stage'].startswith('memory-') for c in client.calls)
    row['closing_calls'] = sum(c.get('attempt', 1) == 1 and c['stage'] == 'final-consistency' for c in client.calls)
    return row


def elapsed(meta):
    return (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(meta['started'])).total_seconds()


def resume_stop(chapters, seconds, failures, calls, total):
    return chapters >= 50 and ((total > 10 and seconds/50 > 90)
                                  or failures/max(1,calls) > .1)


def checkpoint(meta, client, rows, out):
    meta.update(completed_chapters=sum(r['status'] == 'complete' for r in rows),
                failed_chapters=[r['number'] for r in rows if r['status'] != 'complete'],
                wall_seconds=round(elapsed(meta), 3), usage=usage(client), updated=now())
    save(out / 'run.json', meta)


def emit_progress(meta, client, row, out):
    cost = usage(client)['estimated_usd']['conservative_total']
    inputs = [c.get('input_chars', 0) for c in client.calls if c['stage'] == f"ch{row['number']}-draft"]
    line = (f"chapter={row['number']} elapsed_s={elapsed(meta):.3f} resume_elapsed_s={meta.get('revision',{}).get('seconds',0):.3f} cost_usd={cost} "
            f"input_chars={max(inputs, default=0)} active_threads={row.get('active_threads',0)} "
            f"output_failure_rate={meta.get('revision',{}).get('output_failures',0)/max(1,meta.get('revision',{}).get('calls',0)):.6f}\n")
    # Preserve the original checkpoint time and cost when replaying cached chapters.
    log = out / 'progress.log'
    lines = log.read_text().splitlines(True) if log.exists() else []
    if any(x.startswith(f"chapter={row['number']} ") for x in lines):
        return
    save(log, ''.join(lines) + line)
    print(line.strip(), flush=True)


def finish(meta, client, rows, plan, out, banned):
    rows = [load(out / f"chapters/{r['number']:03d}.json")
            if (out / f"chapters/{r['number']:03d}.json").exists() else r for r in rows]
    if meta['mode'] == 'S4':
        from .longrepair import final_quality
        try:
            rows = final_quality(client, Book(**meta['book']), rows, plan, out, banned)
        except (QuotaExhausted, ProjectError):
            raise
        except (RuntimeError, ValueError) as exc:
            meta['closing_error'] = str(exc)
    complete = [r for r in rows if r['status'] == 'complete']
    bodies = [(out / f"chapter-{r['number']:03d}.body.md").read_text() for r in complete]
    if len(complete) == meta['book']['chapters']:
        export(out, plan['title'], complete, bodies)
        samples = ['# ' + plan['title']]
        for idx in ((1, 100, 200, 299, 300) if len(rows) >= 300 else (1, len(rows)) if meta['mode'] == 'S5a' else (1, 2, 50, 99, 100)):
            if idx <= len(rows):
                samples.append((out / f'chapter-{idx:03d}.md').read_text().strip())
        save(out / 'READ-ME-FIRST.md', '\n\n'.join(samples) + '\n')
    inputs = {}
    for c in client.calls:
        if re.fullmatch(r'ch\d+-draft', c['stage']):
            inputs.setdefault(c['stage'].split('-')[0][2:], c['input_chars'])
    end_state = rows[-1].get('state', {}) if rows else {}
    save(out / 'state.json', serialize_state(end_state) if meta['mode'] == 'S5a' else end_state)
    save(out / 'foreshadows.json', end_state.get('foreshadows', {}))
    chars = [r.get('chars', 0) for r in complete]
    threads = end_state.get('foreshadows', {})
    opened = {n for r in rows for n, v in r.get('state', {}).get('foreshadows', {}).items() if v == 'open'}
    closed = {n for r in rows for n, v in r.get('state', {}).get('foreshadows', {}).items() if v == 'closed'}
    detected = [dict(chapter=r['number'], **i) for r in rows for i in r.get('detected', [])]
    remaining = [dict(chapter=r['number'], **i) for r in rows for i in r.get('remaining', [])]
    snapshots = {str(i): rows[i-1].get('state', {}).get('characters', {})
                 for i in (1, 50, 100) if i <= len(rows)}
    meta.update(status='complete' if len(complete) == meta['book']['chapters'] else 'incomplete',
                finished=now(), acceptance=dict(
                    word_band=[1870, 2530], within_band=sum(r.get('within_band', False) for r in complete),
                    generated=len(complete), chapter_chars=chars,
                    outliers=[dict(chapter=r['number'], chars=r['chars']) for r in complete if not r['within_band']],
                    hard_conflicts=sum(r.get('hard_conflicts', 0) for r in rows),
                    detected=detected, remaining=remaining,
                    foreshadows_recorded=len(threads), foreshadows_ever_opened=len(opened),
                    foreshadows_closed=sum(v == 'closed' for v in threads.values()),
                    foreshadows_ever_closed=len(opened & closed),
                    closed_without_prior_open=sorted(closed - opened),
                    foreshadows_open=sum(v == 'open' for v in threads.values()),
                    draft_input_chars=inputs, input_ratio_100_10=(inputs.get('100', 0) / inputs['10']) if inputs.get('10') and inputs.get('100') else None,
                    character_states=snapshots, fixed_canon=plan['canon']))
    if meta['mode'] == 'S5a':
        serial = load(out / 'serial-meta.json')
        meta['acceptance'].update(foreshadows_recorded=serial['counts']['opened_total'],
            foreshadows_closed=serial['counts']['closed_total'], foreshadows_open=serial['counts']['active'],
            max_active_threads=max(r.get('active_threads', 0) for r in rows),
            max_state_chars=max(r.get('state_chars', 0) for r in rows),
            input_ratio_300_20=inputs.get('300', 0)/inputs['20'] if inputs.get('20') and inputs.get('300') else None,
            state_issues=[dict(chapter=r['number'], **i) for r in rows for i in r.get('state_issues', [])])
    checkpoint(meta, client, rows, out)
    print(pack({k: meta[k] for k in ('status', 'completed_chapters', 'wall_seconds', 'usage')}), flush=True)


def run_long(book_path, out, env, assets, timeout=180):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (out / 'runner.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        stop = [False]
        def request_stop(signum, frame):
            stop[0] = True
        old = {sig: signal.signal(sig, request_stop) for sig in (signal.SIGINT, signal.SIGTERM)}
        try:
            return _run(Book.read(book_path), out, env, Path(assets), timeout, lambda: stop[0])
        finally:
            for sig, handler in old.items():
                signal.signal(sig, handler)


def _run(book, out, env, assets, timeout, stop_requested=lambda: False):
    manifest = out / 'run.json'
    if manifest.exists():
        meta = load(manifest)
        if Book(**meta['book']).record() != book.record() or meta.get('mode') not in ('S4', 'S5a'):
            raise ValueError('Use the original S4 YAML and output directory')
        if meta['status'] == 'complete':
            return meta
    else:
        meta = dict(mode='S5a', started=now(), book=book.record(), status='running',
                    preflight={})
        reviewed = [i for p in (out.parent / 's3').glob('*/run.json')
                    for i in load(p).get('quality', {}).get('ungrounded_event_review', [])]
        meta['preflight']['s3_event_review'] = dict(Counter(i['status'] for i in reviewed))
        save(manifest, meta)
    if meta.get('revision',{}).get('name') != REVISION:
        if meta.get('revision'):
            meta.setdefault('revisions',[]).append(dict(meta['revision'],stop_reason=meta.get('stop_reason')))
        meta['revision'] = dict(name=REVISION, started=now(), start_calls=len(load(out/'calls.json')) if (out/'calls.json').exists() else 0,
            base_chapters=[int(p.stem) for p in (out/'chapters').glob('[0-9][0-9][0-9].json')])
    meta['status'] = 'running'
    meta.pop('stop_reason',None)
    save(manifest,meta)
    serial_mode = meta['mode'] == 'S5a'
    prefix = knowledge(book, assets)
    if not serial_mode:
        platform = yaml.safe_load((assets / 'market_profiles/fanqie/xuanhuan-brain.yaml').read_text())
        prefix += '\n番茄玄幻只读口径：\n' + pack(platform)
    client = Client(out, env, prefix[:5000], timeout)
    client.context_limit, client.clip_context = 30000, True
    client.relaxed, client.stop_requested = serial_mode, stop_requested
    banned = style_rules(assets) + ['arrives']
    plan = overall(client, book)
    rows, state, summaries, previous, rolling = [], compact_state({}), [], [], plan['premise'][:600]
    serial, archive = {}, load(out / 'archive.json') if (out / 'archive.json').exists() else {}
    def thread_view():
        return {k:r['description'] for k,r in serial.get('foreshadows', {}).items() if r['status'] == 'open'}
    outlines, volumes = [], []
    for idx in range(1, book.chapters + 1):
        started = time.monotonic()
        cached = (out / f'chapters/{idx:03d}.json').exists()
        if (idx - 1) % 10 == 0:
            try:
                outlines, volumes = chapter_window(client, plan, idx, book.chapters, state, rolling, thread_view())
            except (QuotaExhausted, ProjectError):
                raise
            except (RuntimeError, ValueError, KeyError, TypeError, AttributeError) as exc:
                # A failed planning window is explicitly marked; later windows still run.
                outlines = []
                meta.setdefault('planning_errors', []).append(dict(chapter=idx, reason=str(exc)))
        chapter = next((x for x in outlines if x['number'] == idx), None)
        volume = next((x for x in volumes if x['start'] <= idx <= x['end']), {})
        try:
            if not chapter:
                chapter = dict(number=idx, title=f'第{idx}章', scenes=[dict(action=rolling[-400:])],
                    ending_hook=plan.get('ending','推进本卷目标'))
            if serial_mode:
                from .serialchapter import write_serial
                row = write_serial(client, book, chapter, volume, plan['canon'],
                    previous[-1][-1400:] if previous else '', state, summaries, rolling,
                    banned, previous, out, thread_view())
            else:
                row = write_chapter(client, book, chapter, volume, plan['canon'],
                                previous[-1][-1400:] if previous else '', state, summaries, rolling,
                                banned, previous, out)
        except (QuotaExhausted, ProjectError):
            raise
        except (RuntimeError, ValueError, KeyError, TypeError) as exc:
            body = (out/f'chapter-{idx:03d}.draft.md').read_text() if (out/f'chapter-{idx:03d}.draft.md').exists() else ''
            save(out/f'chapter-{idx:03d}.body.md', body)
            row = dict(number=idx, title=chapter.get('title','') if chapter else '', status='complete',
                       state=compact_state(state), summary='', chars=chinese_count(body),
                       within_band=False, warnings=[dict(stage='chapter',fallback='saved_draft_or_empty',error=str(exc))])
        row.setdefault('seconds', round(time.monotonic() - started, 3))
        if row['status'] == 'complete':
            if serial_mode:
                if 'serial_meta' in row:
                    serial = row['serial_meta']
                else:
                    try:
                        row['state'], serial, removed, issues = manage_state(row['state'], state, serial,
                            idx, book.chapters, row.get('seen', []), item_retired=row.get('retired_objects', []))
                    except (ValueError, KeyError, TypeError):
                        row['state'], removed, issues = compact_state(state), [], [dict(code='state_fallback',key=str(idx))]
                        serial.setdefault('counts',dict(active=len(state.get('foreshadows',{})),opened_total=0,closed_total=0))
                    row.update(serial_meta=serial, state_issues=issues, active_threads=serial['counts']['active'],
                               state_chars=len(serialize_state(row['state'])))
                    for item in removed:
                        archive.setdefault(item['kind'], {})[item['key']] = item
                    if removed:
                        save(out / f'archive/{idx:03d}.json', removed)
                    save(out / 'archive.json', archive)
                serial['dead_characters'] = sorted(set(serial.get('dead_characters',[])) | set(row.get('deaths',[]) if isinstance(row.get('deaths'),list) else []))
                save(out / 'serial-meta.json', serial)
            state = compact_state(row['state'])
            row['state'] = state
            summaries.append(dict(chapter=idx, summary=row['summary']))
            previous.append((out / f'chapter-{idx:03d}.body.md').read_text())
            save(out / f'chapters/{idx:03d}.json', row)
        rows.append(row)
        save(out / 'state.json', serialize_state(state) if serial_mode else state)
        save(out / f'chapter-{idx:03d}.state.json', state)
        save(out / 'foreshadows.json', state['foreshadows'])
        checkpoint(meta, client, rows, out)
        if stop_requested():
            meta['status'] = 'interrupted'
            save(manifest, meta)
            return meta
        print(f"chapter={idx} status={row['status']} chars={row.get('chars')} hard={row.get('hard_conflicts')} "
              f"seconds={row['seconds']}", flush=True)
        if idx % 10 == 0:
            try:
                if serial_mode:
                    memory = ask(client, f'memory-{idx}',
                        '压缩滚动记忆与十章摘要为600汉字内，保留不可逆选择、代价、关系。'
                        '同时逐条比对活跃伏笔，把同一疑问的不同描述合并；thread_aliases以重复ID为键、保留ID为值。'
                        '不可把不同谜团强合，不把合并算收束；无重复返回空对象。',
                        [('最近十章', pack(summaries[-10:])), ('旧记忆', rolling),
                         ('活跃伏笔', pack(thread_view()))], '{"summary":"","thread_aliases":{}}',
                        cap=2200, max_attempts=2, validate=lambda v: bool(v['summary']))
                    rolling = str(memory.get('summary') or '\n'.join(x['summary'] for x in summaries[-3:]))[:900]
                    state, serial, merged, issues = manage_state({}, state, serial, idx, book.chapters,
                        aliases=memory.get('thread_aliases', {}))
                    for item in merged:
                        archive.setdefault(item['kind'], {})[item['key']] = item
                    row.update(state=state, serial_meta=serial, state_issues=row.get('state_issues', [])+issues)
                    row.update(active_threads=serial['counts']['active'], state_chars=len(serialize_state(state)))
                    save(out / f'chapters/{idx:03d}.json', row)
                    save(out / f'chapter-{idx:03d}.state.json', state)
                    save(out / 'serial-meta.json', serial)
                    save(out / 'archive.json', archive)
                    save(out / 'state.json', serialize_state(state))
                else:
                    rolling = ask(client, f'memory-{idx}',
                              '将滚动记忆与这十章摘要压缩成600—800汉字。只留不可逆选择、能力代价、'
                              '重要关系变化、未解伏笔及已兑现结果；姓名固定，不复述已闭合的小动作。直接给记忆正文。',
                              [('这十章摘要', pack(summaries[-10:])), ('旧滚动记忆', rolling),
                               ('当前状态', pack(state)[:4500])], cap=2200, max_attempts=2)
                    rolling = rolling[:900]
            except (QuotaExhausted, ProjectError):
                raise
            except (RuntimeError, ValueError, KeyError, TypeError, AttributeError) as exc:
                meta.setdefault('memory_errors', []).append(dict(chapter=idx, reason=str(exc)))
                rolling = ('\n'.join(x['summary'] for x in summaries[-5:]))[:900]
            save(out / 'rolling-summary.md', rolling)
        revision = meta['revision']
        new_rows = [r for r in rows if r['number'] not in revision['base_chapters']]
        current = client.calls[revision['start_calls']:]
        seconds = (dt.datetime.now(dt.timezone.utc)-dt.datetime.fromisoformat(revision['started'])).total_seconds()
        failures = sum(c['status'] not in ('ok','pending') for c in current)
        revision.update(chapters=len(new_rows),seconds=round(seconds,3),calls=len(current),failures=failures,
            output_failures=sum(c['status'] in ('degraded','invalid_stage_output','invalid_output') for c in current))
        if not cached:
            row.update(resume_seconds=seconds,resume_calls=len(client.calls))
            save(out/f'chapters/{idx:03d}.json',row)
        first = new_rows[-51] if len(new_rows)>50 else {}
        window = client.calls[first.get('resume_calls',revision['start_calls']):]
        window_failures = sum(c['status'] not in ('ok','pending') for c in window)
        if serial_mode and resume_stop(len(new_rows), seconds-first.get('resume_seconds',0), window_failures, len(window), book.chapters):
            meta.setdefault('warnings', []).append(dict(chapter=idx,
                window50_average_s=(seconds-first.get('resume_seconds',0))/50,
                failure_rate=window_failures/max(1,len(window))))
            save(manifest, meta)
        if idx % 25 == 0 and not cached:
            emit_progress(meta, client, row, out)
    finish(meta, client, rows, plan, out, banned)
    return meta
