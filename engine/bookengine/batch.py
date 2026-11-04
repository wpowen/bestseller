"""Bounded single-book subprocesses and read-only checkpoint statistics."""
import datetime as dt
import fcntl
import json
from math import isfinite
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import time

import yaml

from .config import Book
from .storage import now, save
from .text import chinese_count, prose

PRICE = dict(input=0.15, output=0.60, cached_input=0.015)
PRICE_SOURCE = 'https://platform.minimax.io/docs/pricing/overview'
TOKEN_KEYS = ('prompt_tokens', 'completion_tokens', 'total_tokens')

def read_manifest(manifest, long=False):
    """Resolve book paths relative to the manifest, validating before launch."""
    manifest = Path(manifest).resolve()
    value = yaml.safe_load(manifest.read_text(encoding='utf-8'))
    if not isinstance(value, dict) or not isinstance(value.get('books'), list) or not value['books']:
        raise ValueError('manifest needs a nonempty books list')
    books, seen = [], set()
    for item in value['books']:
        if not isinstance(item, dict) or set(item) != {'id', 'path'}:
            raise ValueError('each book needs only id and path')
        key, path = item['id'], item['path']
        if not isinstance(key, str) or not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,63}', key) or key in seen:
            raise ValueError('book ids must be safe, lowercase and unique')
        if not isinstance(path, str) or not path.strip():
            raise ValueError('book path must be a nonempty string')
        path = (manifest.parent / path).resolve(strict=True)
        if not path.is_file():
            raise ValueError('book path must be a file')
        book = Book.read(path)
        if book.chapters > 10 and not long:
            raise ValueError('Batch currently supports up to 10 chapters; use --long for a serial')
        books.append(dict(id=key, path=path, book=book.record()))
        seen.add(key)
    return books

def usage_report(calls, totals=None):
    """Estimate only recorded usage; missing fields remain explicitly listed."""
    totals = totals or {}
    result = dict(calls=len(calls), **dict.fromkeys(TOKEN_KEYS, 0), cached_tokens=0,
                  failures=0, retries=0, base_calls=0, length_calls=0, repair_calls=0, retry_calls=0,
                  length_attempts=0, missing_usage=[])
    prices=[0.,0.]
    for call in calls:
        rates=(.30,1.20,.06) if call.get('model')=='MiniMax-M3' else (PRICE['input'],PRICE['output'],PRICE['cached_input'])
        length = call.get('stage', '').endswith('-length')
        repair = call.get('stage') in ('06-check-fix', '07-check-fix', '08-check-fix')
        attempt = call.get('attempt', 1)
        result['base_calls'] += attempt == 1 and not length and not repair
        result['length_calls'] += attempt == 1 and length
        result['repair_calls'] += attempt == 1 and repair
        result['retry_calls'] += attempt > 1
        result['length_attempts'] += length
        usage = call.get('usage') or {}
        usage = usage if isinstance(usage, dict) else {}
        valid = {k: usage[k] for k in TOKEN_KEYS if type(usage.get(k)) is int and usage[k] >= 0}
        missing = [k for k in TOKEN_KEYS if k not in valid]
        if missing:
            caps = [call.get(k) for k in ('input_bytes', 'output_token_cap')]
            upper = (caps[0] * rates[0] + caps[1] * rates[1]) / 1_000_000 if all(
                type(x) is int and x >= 0 for x in caps) else None
            result['missing_usage'].append(dict(seq=call.get('seq'), stage=call.get('stage'), fields=missing,
                                               input_bytes=caps[0], output_token_cap=caps[1], unknown_upper_bound=upper))
        for key in TOKEN_KEYS:
            result[key] += valid.get(key, sum(valid.get(k, 0) for k in TOKEN_KEYS[:2])
                                     if key == 'total_tokens' else 0)
        details = usage.get('prompt_tokens_details') or {}
        details = details if isinstance(details, dict) else {}
        cached = details.get('cached_tokens', usage.get('cached_tokens', usage.get('cache_read_input_tokens', 0)))
        if type(cached) is int:
            result['cached_tokens'] += min(valid.get('prompt_tokens', 0), max(0, cached))
        cached=min(valid.get('prompt_tokens',0),max(0,cached)) if type(cached) is int else 0
        prices[0]+=((valid.get('prompt_tokens',0)-cached)*rates[0]+cached*rates[2]+valid.get('completion_tokens',0)*rates[1])/1_000_000
        prices[1]+=(valid.get('prompt_tokens',0)*rates[0]+valid.get('completion_tokens',0)*rates[1])/1_000_000
        result['failures'] += call.get('status') not in ('ok', 'pending')
        result['retries'] += call.get('attempt', 1) > 1
    if not calls and totals:
        for key in ('calls', *TOKEN_KEYS, 'cached_tokens', 'failures', 'retries'):
            result[key] = totals.get(key, 0)
        if totals.get('missing_usage_calls'):
            result['missing_usage'].append(dict(count=totals['missing_usage_calls'], fields=list(TOKEN_KEYS)))
        if totals.get('missing_usage'):
            result['missing_usage'] = totals['missing_usage']
        for key in ('base_calls', 'length_calls', 'length_attempts', 'repair_calls'):
            result[key] = totals.get(key, None if result['calls'] else 0)
        result['retry_calls'] = totals.get('retry_calls', result['retries'])
    result['unknownusage'] = sum(x.get('count', 1) for x in result['missing_usage'])
    prompt, completion, cached = (result[k] for k in ('prompt_tokens', 'completion_tokens', 'cached_tokens'))
    cached = min(prompt, max(0, cached))
    result['estimated_usd'] = dict(
        cache_adjusted=round(((prompt - cached) * PRICE['input'] + cached * PRICE['cached_input']
                              + completion * PRICE['output']) / 1_000_000, 9),
        no_cache_discount=round((prompt * PRICE['input'] + completion * PRICE['output']) / 1_000_000, 9))
    if calls:result['estimated_usd'].update(cache_adjusted=round(prices[0],9),no_cache_discount=round(prices[1],9))
    bounds = [x.get('unknown_upper_bound') for x in result['missing_usage']]
    upper = None if any(x is None for x in bounds) else round(sum(bounds), 9)
    result['estimated_usd'].update(unknown_upper_bound=upper, conservative_total=None if upper is None
                                  else round(result['estimated_usd']['no_cache_discount'] + upper, 9))
    return result

def sum_estimates(rows):
    return {k: None if any(r['estimated_usd'][k] is None for r in rows)
            else round(sum(r['estimated_usd'][k] for r in rows), 9)
            for k in ('cache_adjusted', 'no_cache_discount', 'unknown_upper_bound', 'conservative_total')}

def read_report(entry, out, state=None, at=None):
    """Read checkpoints only; never edit chapters or retry runtime files."""
    state, at, errors = state or {}, at or now(), []
    folder = Path(out) / entry['id']

    def read(name, default):
        try:
            return json.loads((folder / name).read_text(encoding='utf-8'))
        except FileNotFoundError:
            return default
        except (OSError, ValueError):
            errors.append(name)
            return default

    meta, calls = read('run.json', {}), read('calls.json', [])
    status = state.get('status', meta.get('status', 'queued'))
    if is_complete(folder, meta):
        status = 'complete'
    elif status == 'complete':
        status = 'incomplete'
    started = meta.get('started') or state.get('started')
    finished = meta.get('finished') if status == 'complete' else state.get('finished')
    elapsed = 0
    if started:
        try:
            elapsed = max(0, (dt.datetime.fromisoformat(finished or at)
                              - dt.datetime.fromisoformat(started)).total_seconds())
        except (ValueError, TypeError):
            errors.append('timestamps')
    if meta.get('execution_seconds') is not None:
        elapsed=meta['execution_seconds']
        if status=='running' and meta.get('revision',{}).get('started'):elapsed=meta['revision']['base_seconds']+max(0,(dt.datetime.fromisoformat(at)-dt.datetime.fromisoformat(meta['revision']['started'])).total_seconds())
    counts = []
    saved = meta.get('chapter_chinese_chars', [])
    long_mode = bool(meta.get('mode'))
    for number in range(1, entry['book']['chapters'] + 1):
        count = saved[number - 1] if number <= len(saved) else None
        suffixes = ('.md', '.draft.md')
        if long_mode:
            name = f'chapters/{number:03d}.json'
            chapter = read(name, {})
            if not isinstance(chapter, dict):
                errors.append(name)
                chapter = {}
            if chapter.get('status') != 'complete':
                counts.append(None)
                continue
            count, suffixes = None, ('.body.md',)
        if count is None:
            for suffix in suffixes:
                path = folder / f'chapter-{number:03d}{suffix}'
                try:
                    count = chinese_count(prose(path.read_text(encoding='utf-8')))
                    break
                except FileNotFoundError:
                    continue
                except OSError:
                    errors.append(path.name)
        counts.append(count)
    steps = [p for p in (folder / 'steps').glob('*')
             if p.suffix in ('.json', '.md') and not p.name.endswith('.retry.json')]
    stage = calls[-1].get('stage', 'waiting') if calls else 'waiting'
    if not calls and steps:
        stage = max(steps, key=lambda p: p.stat().st_mtime).stem
    totals = meta.get('totals') or meta.get('usage')
    usage = usage_report(calls, totals)
    max_input_chars = max((c.get('input_chars', 0) for c in calls
                           if type(c.get('input_chars', 0)) is int), default=0)
    if 'calls.json' in errors and not totals:
        for key in ('calls', *TOKEN_KEYS, 'cached_tokens', 'failures', 'retries', 'unknownusage',
                    'base_calls', 'length_calls', 'repair_calls', 'retry_calls', 'length_attempts'):
            usage[key] = None
        usage['estimated_usd'] = dict.fromkeys(usage['estimated_usd'])
    if 'calls.json' in errors:
        max_input_chars = None
    return dict(id=entry['id'], path=str(entry['path']), status=status,
                started=started, finished=finished, wall_seconds=round(elapsed, 3),
                stage='complete' if status == 'complete' else stage, completed_stages=len(steps),
                chapter_progress=dict(complete=sum(x is not None for x in counts), total=len(counts)),
                chapter_chinese_chars=counts, read_errors=errors, exit_code=state.get('exit_code'),
                error=state.get('error'), max_input_chars=max_input_chars, quality=meta.get('quality', {}),
                **usage)

def format_report(row):
    progress = row['chapter_progress']
    return (f"{row['id']} status={row['status']} stage={row['stage']} "
            f"chapters={progress['complete']}/{progress['total']} calls={row['calls']} "
            f"base={row['base_calls']} length={row['length_calls']} fix={row['repair_calls']} retries={row['retry_calls']} "
            f"tokens={row['prompt_tokens']}/{row['completion_tokens']}/{row['total_tokens']} "
            f"cached={row['cached_tokens']} unknownusage={row['unknownusage']} "
            f"elapsed={row['wall_seconds']:.1f}s failures={row['failures']} "
            f"word_counts={row['chapter_chinese_chars']}")

def validate_options(timeout, jobs, interval):
    if type(jobs) is not int or not 1 <= jobs <= 6:
        raise ValueError('jobs must be a positive integer <= 6')
    for value in (timeout, interval):
        if type(value) not in (int, float) or not isfinite(value) or value <= 0:
            raise ValueError('timeout and interval must be finite positive numbers')

def is_complete(folder, meta):
    return meta.get('status') == 'complete' and all(
        (Path(folder) / name).is_file() for name in ('book.md', 'book.docx'))

def write_reading_sample(entries, out):
    """Collect only exported titles and selected chapters, in manifest order."""
    out, sections = Path(out), []
    for entry in entries:
        folder = out / entry['id']
        title = next(line for line in (folder / 'book.md').read_text(encoding='utf-8').splitlines()
                     if re.match(r'^#{1,6}\s+\S', line))
        sections.append(title)
        total = entry['book']['chapters']
        q1 = (folder/'run.json').exists() and json.loads((folder/'run.json').read_text()).get('mode') == 'Q1'
        numbers = (1,50,51,75,100) if q1 else (1, 100, 200, 299, 300) if total > 10 else (1, total)
        for number in dict.fromkeys(n for n in numbers if n <= total):
            sections.append((folder / f'chapter-{number:03d}.md').read_text(encoding='utf-8').strip())
    path = out / 'READ-ME-FIRST.md'
    save(path, '\n\n'.join(sections) + '\n')
    return path

def run_batch(manifest, out, env, assets, timeout=180, jobs=6, interval=5, quality=False, long=False, q1=False, style='b'):
    """Run from the caller's cwd and inherited environment; return batch.json data."""
    validate_options(timeout, jobs, interval)
    entries, out = read_manifest(manifest, long=long), Path(out).resolve()
    for entry in entries:
        folder = out / entry['id']
        if folder.is_symlink():
            raise ValueError('book output must not be a symlink')
    out.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (out / 'batch.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return _run_batch(entries, out, env, assets, timeout, jobs, interval, quality, long, q1, style)

def _run_batch(entries, out, env, assets, timeout, jobs, interval, quality=False, long=False, q1=False, style='b'):
    states, pending, active = {}, [], {}
    child_env = os.environ.copy()
    rate_file = child_env.get('BOOKENGINE_RATE_FILE') or str(
        Path(tempfile.gettempdir()) / f'bookengine-rate-{os.getuid()}.json')
    child_env['BOOKENGINE_RATE_FILE'] = str(Path(rate_file).expanduser().resolve())
    previous = json.loads((out / 'batch.json').read_text()) if (out / 'batch.json').exists() else {}
    created_at = previous.get('created_at') or now()
    old_rows = {r['id']: r for r in previous.get('books', [])}
    for entry in entries:
        folder = out / entry['id']
        try:
            meta = json.loads((folder / 'run.json').read_text())
        except (OSError, ValueError):
            meta = {}
        if meta and meta.get('book') != entry['book']:
            raise ValueError('book YAML changed; use a new output folder: ' + entry['id'])
        if meta and (bool(meta.get('mode')) != long):
            raise ValueError('long mode differs; use a matching output folder: ' + entry['id'])
        if not long and folder.exists() and any(folder.iterdir()) and (
                (quality and meta.get('quality_mode') != 3) or (not quality and meta.get('quality_mode') == 3)):
            raise ValueError('quality mode differs; use a matching output folder: ' + entry['id'])
        done = is_complete(folder, meta)
        states[entry['id']] = dict(status='complete' if done else 'queued',
                                  started=old_rows.get(entry['id'], {}).get('started'))
        if not done:
            pending.append(entry)
    save(out / 'batch.json', dict(previous, created_at=created_at, started=created_at, status='running'))
    stopped, handlers = 0, {}

    def stop(signum, frame):
        nonlocal stopped
        stopped = signum

    def snapshot():
        rows = [read_report(e, out, states[e['id']]) for e in entries]
        for row in rows:
            print(format_report(row), flush=True)
        return rows

    try:
        for sig in (signal.SIGINT, signal.SIGTERM):
            handlers[sig] = signal.signal(sig, stop)
        next_report = 0
        while (pending or active) and not stopped:
            for key, process in list(active.items()):
                code = process.poll()
                if code is not None:
                    row = read_report(next(e for e in entries if e['id'] == key), out)
                    states[key].update(status='stopped' if row['status']=='stopped' else 'complete' if code == 0 and row['status'] == 'complete' else 'degraded',
                                       finished=now(), exit_code=code)
                    del active[key]
            while pending and len(active) < jobs and not stopped:
                entry = pending.pop(0)
                folder = out / entry['id']
                states[entry['id']].update(status='running', started=states[entry['id']].get('started') or now())
                try:
                    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
                    with (folder / 'book.log').open('ab') as log:
                        active[entry['id']] = subprocess.Popen(
                            [sys.executable, '-u', '-m', 'bookengine', str(entry['path']), '--env', str(env),
                             '--assets', str(assets), '--out', str(folder), '--timeout', str(timeout)]
                            + (['--q1','--style',style] if q1 else ['--long'] if long else ['--quality'] if quality else []),
                            stdout=log, stderr=subprocess.STDOUT, start_new_session=True, env=child_env)
                except OSError as exc:
                    states[entry['id']].update(status='degraded', finished=now(), error=type(exc).__name__)
            if time.monotonic() >= next_report:
                if any(r['status']=='stopped' for r in snapshot()):
                    stopped = signal.SIGTERM
                next_report = time.monotonic() + interval
            if pending or active:
                time.sleep(min(0.2, max(0, next_report - time.monotonic())))
    finally:
        try:
            for process in active.values():
                if process.poll() is None:
                    process.terminate()
            deadline = time.monotonic() + 5
            for key, process in active.items():
                try:
                    process.wait(timeout=max(0, deadline - time.monotonic()))
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                row = read_report(next(e for e in entries if e['id'] == key), out)
                states[key].update(status='complete' if process.returncode == 0 and row['status'] == 'complete'
                                   else 'interrupted', finished=now(), exit_code=process.returncode)
            for entry in pending:
                states[entry['id']]['status'] = 'interrupted'
            rows = snapshot()
            result = dict(status='interrupted' if stopped else
                          ('complete' if all(r['status'] == 'complete' for r in rows) else 'degraded'),
                          created_at=created_at, started=created_at, finished=now(),
                          wall_seconds=round((dt.datetime.fromisoformat(now()) - dt.datetime.fromisoformat(created_at)).total_seconds(), 3),
                          completed_books=sum(r['status'] == 'complete' for r in rows),
                          books=rows, estimated_usd=sum_estimates(rows),
                          pricing=dict(usd_per_million_tokens=PRICE, source=PRICE_SOURCE,
                                       note='Subscription key: public pay-as-you-go equivalent for recorded usage only; '
                                            'not an actual bill. Unknown bounds use input_bytes and output_token_cap; '
                                            'missing caps mean null. Conservative totals may double count partial usage; '
                                            'cache detail absent means zero discount.'))
            save(out / 'batch.json', result)
        finally:
            for sig, handler in handlers.items():
                signal.signal(sig, handler)
    if stopped == signal.SIGINT:
        raise KeyboardInterrupt
    if stopped:
        raise SystemExit(128 + stopped)
    completed = [e for e, row in zip(entries, result['books']) if row['status'] == 'complete']
    if result['status'] == 'complete' and completed and (long or (quality and len(entries) == 6)):
        write_reading_sample(completed, out)
    return result
