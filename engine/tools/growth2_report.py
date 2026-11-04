"""Read-only INBOX11 metrics; stdout is JSON, --write writes a one-page snapshot.

python3 -B engine/tools/growth2_report.py [--root ../engine-books/growth-payoff-2]
--legacy-root defaults to <root sibling>/growth-payoff; --phase defaults to
<root>/phase.json. No timestamps are inferred from mtime. Preferred phase schema:
{"started": "ISO8601", "books": {"xuanhuan-50":
 {"base_calls": 227, "base_seconds": 7121.989, "base_chapters": 43}}}.
books may be a list with id/name/path; baselines/call_baselines mappings and
start_calls/calls/baseline_calls, execution_seconds/seconds aliases are accepted.
Phase baselines win over this revision's metadata. Legacy books without a usable
baseline remain Unknown; their historical calls are never charged as new calls.
Only --write mutates a file (engine/reports/growth-payoff-2.md); books are read only.
Numeric diagnostics describe saved model extractions, not human acceptance.
"""
import argparse
from collections import Counter
import datetime as dt
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from growth_report import (ENGINE, canonical, english_keys, mapping, numeric,
                           quoted, rank, read, timestamp)
from bookengine.batch import PRICE_SOURCE, usage_report
from bookengine.text import chinese_count, prose
from bookengine.q1_plan import GROWTH

ROOT = ENGINE.parent.parent / 'engine-books/growth-payoff-2'
REPORT = ENGINE / 'reports/growth-payoff-2.md'
SPECS = {'xuanhuan-50': (44, 50, 'continuation'),
         'xuanhuan-20': (1, 20, 'new'), 'suspense-20': (1, 20, 'new'),
         'suspense-50': (50, 50, 'regression')}
TYPES = {'对抗', '谋划', '情感', '日常', '揭秘', '收获'}
GROUP = {'群体表态', '集体认同', '齐声', '众人作证', '群体表态/齐声/众人作证'}


def first(obj, keys):
    return next((obj[k] for k in keys if k in obj), None)


def phase_entry(phase, name, folder):
    for key in ('books', 'baselines', 'call_baselines'):
        entries = phase.get(key)
        if isinstance(entries, list):
            entries = {str(first(mapping(x), ('id', 'name', 'path'))): x for x in entries}
        for label, entry in mapping(entries).items():
            if label in (name, str(folder)) or Path(label).name == name:
                return entry if isinstance(entry, dict) else {'base_calls': entry}
    entry = mapping(phase.get(name))
    for key in ('base_calls', 'start_calls', 'baseline_calls', 'base_seconds', 'baseline_seconds'):
        values = mapping(phase.get(key))
        if name in values: entry = {**entry, key: values[name]}
    return entry


def baseline(meta, phase, name, folder, calls, fresh):
    entry = phase_entry(phase, name, folder)
    revision = mapping(meta.get('revision'))
    current = revision if revision.get('name') in ('growth-payoff-2', 'r2') else {}
    call_keys = ('base_calls', 'start_calls', 'baseline_calls', 'calls')
    second_keys = ('base_seconds', 'baseline_seconds', 'execution_seconds', 'seconds')
    source = 'phase.json' if entry else 'revision' if current else 'new_book' if fresh else 'Unknown'
    count = first(entry, call_keys) if any(k in entry for k in call_keys) else first(current, call_keys)
    seconds = first(entry, second_keys) if any(k in entry for k in second_keys) else current.get('base_seconds')
    started = first(entry, ('started', 'started_at', 'start_time')) or first(phase, ('started', 'started_at', 'start_time')) or current.get('started')
    errors = []
    if count is None and fresh:
        count = 0
    if count is None and timestamp(started) and calls is not None:
        dates = [timestamp(c.get('started')) for c in calls]
        if all(dates) and dates == sorted(dates):
            count = sum(x < timestamp(started) for x in dates)
            source += ':call_started'
    if type(count) is not int or count < 0 or calls is not None and count > len(calls):
        count = None
        errors.append('call baseline missing/invalid; round cost Unknown')
    if seconds is None and fresh:
        seconds = 0
    if numeric(seconds) is None:
        seconds = None
    return dict(source=source, base_calls=count, base_seconds=seconds, started=started,
                base_chapters=entry.get('base_chapters', current.get('base_chapters')), errors=errors)


def call_metrics(calls, numbers):
    chapters = {}
    for n in numbers:
        items = [c for c in calls if re.search(rf'(?:^|-)ch0*{n}(?:-|$)', str(c.get('stage', '')))]
        drafts = [c for c in items if re.fullmatch(r'(?:r2|g)-ch0*\d+-draft(?:-retry)?', str(c.get('stage', '')))]
        retry = lambda c: type(c.get('attempt')) is int and c['attempt'] > 1
        chapters[str(n)] = dict(total_calls=len(items), body_calls=len(drafts),
            outside_body_calls=len(items)-len(drafts), network_retries=sum(map(retry, items)),
            outside_body_retries=sum(retry(c) for c in items if c not in drafts),
            logical_retry_calls=sum(bool(re.search(r'retry|recover|schema2', str(c.get('stage', '')))) for c in items),
            stages=dict(Counter(str(c.get('stage', 'Unknown')) for c in items)))
    unassigned = [c for c in calls if not re.search(r'(?:^|-)ch\d+(?:-|$)', str(c.get('stage', '')))]
    outside = [c for c in calls if re.search(r'(?:^|-)ch\d+(?:-|$)', str(c.get('stage', '')))
               and not re.fullmatch(r'(?:r2|g)-ch\d+-draft(?:-retry)?', str(c.get('stage', '')))]
    return dict(chapters=chapters, outside_body_calls=len(outside),
                violation_chapters=[int(n) for n, c in chapters.items() if c['outside_body_calls'] > 2],
                planning_and_other_calls=len(unassigned),
                planning_and_other_stages=dict(Counter(str(c.get('stage', 'Unknown')) for c in unassigned)),
                all_chapter_stages=dict(Counter(str(c.get('stage', 'Unknown')) for c in calls if c not in unassigned)))


def hook_metrics(hooks, total):
    windows = [list(range(n-29, n+1)) for n in range(30, max(hooks, default=0)+1)]
    complete = [w for w in windows if all(hooks.get(n) is not None for n in w)]
    peaks = [max(Counter(hooks[n] for n in w).values()) for w in complete]
    lower = [max(Counter(hooks[j] for j in w if hooks.get(j) is not None).values(), default=0) for w in windows]
    counts = Counter(x for x in hooks.values() if x is not None)
    peak, size = max(counts.values(), default=0), len(hooks)
    return dict(complete_windows=len(complete), unknown_windows=len(windows)-len(complete),
                partial=total < 30 or len(hooks) < total, window_size=30, window_limit_ratio=.25,
                window_peak=max(peaks, default=None), window_peak_ratio=max(peaks)/30 if peaks else None,
                violation_windows=sum(p/30 > .25 for p in peaks),
                window_lower_bound_peak=max(lower, default=None),
                violation_windows_lower_bound=sum(p/30 > .25 for p in lower),
                wholebook_peak=peak, wholebook_denominator=size,
                wholebook_peak_ratio=peak/size if size else None,
                wholebook_within_limit=(False if size and peak/size > .25 else
                                       True if size == total and None not in hooks.values() else None),
                unknown_chapters=[n for n, v in hooks.items() if v is None], counts=dict(counts))


def solution_metrics(rows, numbers, canon, total):
    aliases, menu = mapping(canon.get('solution_aliases')), canon.get('solution_menu', [])
    history, unknown, multiple, empty, ambiguous = {}, [], [], [], []
    # Runtime migrates historical action lists to one primary per chapter. Use
    # that saved ledger, never invent a primary by picking a convenient type.
    latest = mapping(rows[max(rows)].get('state')) if rows else {}
    ledger = latest.get('solutions')
    primary = {}
    if isinstance(ledger, list):
        for item in ledger:
            if type(mapping(item).get('chapter')) is int:
                primary.setdefault(item['chapter'], []).append(item)
    for n, row in sorted(rows.items()):
        items = primary.get(n, []) if isinstance(ledger, list) else row.get('solutions')
        if not isinstance(items, list):
            unknown.append(n)
            continue
        kinds = []
        for item in items:
            kind = canonical(mapping(item).get('type'), aliases)
            if kind not in menu or mapping(item).get('chapter', n) != n:
                unknown.append(n)
            if kind:
                kinds.append('群体表态' if kind in GROUP else kind)
            if mapping(item).get('primary_basis') == 'first_record_requires_body_review':
                ambiguous.append(n)
        history[n] = kinds
        if len(kinds) > 1:
            multiple.append(n)
        if not kinds:
            empty.append(n)
    repeat5, over30, group_whole, group_final = [], [], [], []
    last_volume = canon.get('final_volume_start')
    if type(last_volume) is not int or not 1 <= last_volume <= total:
        last_volume = (total-1)//25*25+1
    for n, kinds in history.items():
        if any(k in history.get(j, []) for k in kinds for j in range(max(1, n-4), n)):
            repeat5.append(n)
        if any(sum(k in history.get(j, []) for j in range(max(1, n-29), n+1)) > 2 for k in kinds):
            over30.append(n)
        if '群体表态' in kinds:
            if sum('群体表态' in history.get(j, []) for j in range(1, n+1)) > 2:
                group_whole.append(n)
            if n >= last_volume and sum('群体表态' in history.get(j, []) for j in range(last_volume, n+1)) > 1:
                group_final.append(n)
    scoped = lambda values: sorted(set(values) & set(numbers))
    return dict(denominator=len(numbers), multiple_main_chapters=scoped(multiple), empty_chapters=scoped(empty),
                repeat_5_chapters=scoped(repeat5), over_30_chapters=scoped(over30),
                group_wholebook_count=sum('群体表态' in x for x in history.values()),
                group_final_volume_count=sum('群体表态' in x for n, x in history.items() if n >= last_volume),
                group_final_volume_start=last_volume, group_wholebook_violation_chapters=scoped(group_whole),
                group_final_volume_violation_chapters=scoped(group_final),
                violation_chapters=scoped(multiple+repeat5+over30+group_whole+group_final),
                unknown_chapters=scoped(unknown), primary_ambiguous_chapters=scoped(ambiguous),
                source='state.solutions' if isinstance(ledger, list) else 'row.solutions',
                history_missing=sorted(set(range(1, max(rows, default=0)+1))-set(history)))


def raw_extract(folder, n, row, calls, body):
    # Never silently use an old extraction after a failed final extraction.
    if row.get('state_failed') is True and row.get('state_recovered') is not True:
        return None
    stages = [str(c.get('stage', '')) for c in calls
              if re.fullmatch(rf'(?:r2|g)-ch0*{n}-(?:finish|final-state|state|recover)(?:-retry)?(?:-schema2)?', str(c.get('stage', '')))]
    stages += [p.stem for p in sorted((folder/'steps').glob(f'r2-ch{n}-*.json'))
               if p.stem.endswith(('finish', 'final-state')) and p.stem not in stages]
    if not stages:
        return None
    raw = read(folder/'steps'/f'{stages[-1]}.json')
    raw = mapping(raw)
    raw = mapping(raw.get('extract', raw.get('extraction', raw)))
    if not isinstance(raw.get('state'), dict) or any(raw.get(k) != row.get(k) for k in ('type', 'hook_type', 'payoff')):
        return None
    payoff = mapping(raw.get('payoff'))
    if payoff.get('fulfilled') is True and not quoted(payoff.get('quote'), body):
        return None
    return raw


def chapter_metrics(folder, rows, bodies, numbers, canon, bible, calls):
    stats, due, state_stats = Counter(), Counter(), Counter()
    payoffs, hooks, strengths, displays, injuries = {}, {}, [], [], {}
    raw_by_n = {n: raw_extract(folder, n, row, calls, bodies.get(n)) for n, row in rows.items()}
    previous = mapping(bible.get('initial_dashboard'))
    for n, row in sorted(rows.items()):
        if n > 1 and n-1 not in rows:
            previous = {}
        body, raw = bodies.get(n), raw_by_n[n]
        payoff, state = mapping(row.get('payoff')), mapping(row.get('state'))
        dashboard = mapping(state.get('dashboard'))
        a, b = rank(previous.get('strength'), 'strength', canon), rank(dashboard.get('strength'), 'strength', canon)
        actual = canonical(payoff.get('type'), {**mapping(canon.get('payoff_aliases')), **mapping(canon.get('payoff_subtype_to_type'))})
        paid = payoff.get('fulfilled') is True and actual in canon.get('payoff_menu', []) and quoted(payoff.get('quote'), body)
        payoffs[n] = actual if paid else None
        hooks[n] = row.get('hook_type') if row.get('hook_type') in canon.get('hook_types', []) else None
        if a is not None and b is not None and b > a:
            strengths.append(dict(chapter=n, before=previous.get('strength'), after=dashboard.get('strength'), levels=b-a))
        if n in numbers:
            for key, value in (('type', row.get('type')), ('hook', row.get('hook_type')), ('payoff', payoff.get('type'))):
                stats[key+'_empty'] += not isinstance(value, str) or not value.strip()
            stats['type_invalid'] += bool(row.get('type')) and row.get('type') not in TYPES
            stats['hook_invalid'] += bool(row.get('hook_type')) and hooks[n] is None
            stats['payoff_invalid'] += not (type(payoff.get('fulfilled')) is bool and (actual in canon.get('payoff_menu', []) if payoff.get('fulfilled') else actual == '无兑现'))
            stats['payoff_quote_invalid'] += payoff.get('fulfilled') is True and not quoted(payoff.get('quote'), body)
            stats['extraction_unknown'] += raw is None
            due['due_unknown'] += type(row.get('payoff_due')) is not bool
            due['due'] += row.get('payoff_due') is True
            due['paid'] += row.get('payoff_due') is True and paid and actual == row.get('payoff_type')
            due['every_chapter_due_violations'] += row.get('payoff_due') is not True or not row.get('payoff_type') or row.get('payoff_type') == '无兑现'
            due['flag_mismatch'] += type(row.get('payoff_fulfilled')) is not bool or row['payoff_fulfilled'] != bool(paid and actual == row.get('payoff_type'))
            state_stats['actual_illegal_strength'] += dashboard.get('strength') is not None and b is None
            state_stats['actual_strength_unknown'] += b is None
            state_stats['actual_jump'] += a is not None and b is not None and b-a > 1
            state_stats['transition_unknown'] += a is None or b is None
            if a is not None and b is not None and b < a:
                state_stats['demotion_unknown' if raw is None else 'unsupported_demotion'] += raw is None or not quoted(mapping(raw.get('demotion_event')).get('quote'), body)
            for issue in row.get('issues', []) if isinstance(row.get('issues'), list) else []:
                for key, pattern in (('rejected_illegal', r'strength_outside_canon'), ('rejected_jump', r'unsupported_upgrade|jump|skip|step_exceed'), ('rejected_demotion', r'unsupported_demotion')):
                    state_stats[key] += bool(re.search(pattern, str(issue)))
            power = state.get('power_status')
            state_stats['power_unknown'] += not isinstance(power, dict)
            state_stats['power_english_keys'] += english_keys(power)
            realm = mapping(mapping(power).get('境界')).get('值')
            state_stats['realm_dashboard_mismatch'] += realm is not None and realm != dashboard.get('strength')
            failed, recovered = row.get('state_failed_original', row.get('state_failed')), row.get('state_recovered') is True
            state_stats['original_failures'] += failed is True or recovered
            state_stats['recovered'] += recovered
            state_stats['unresolved'] += row.get('state_failed') is True and not recovered
            state_stats['failure_history_unknown'] += type(failed) is not bool and not recovered
            state_stats['extraction_retries'] += row.get('state_retries', 0) if type(row.get('state_retries')) is int else 0
            count = first(row, ('injuries_count', 'new_body_costs', 'new_disabilities', 'new_physical_costs'))
            if count is None and row.get('state_failed') is False:
                count = state.get('injuries_count')
            items = first(mapping(raw), ('body_costs', 'physical_costs', 'injuries'))
            if isinstance(count, list): count = len(count)
            if count is None and isinstance(items, list):
                physical = [x for x in mapping(raw).get('costs', []) or [] if isinstance(x, dict)
                            and (x.get('physical') is True or x.get('type') in ('伤残', '身体伤残', '身体代价'))]
                count = len({mapping(x).get('content') for x in items+physical
                             if isinstance(mapping(x).get('content'), str) and quoted(mapping(x).get('quote'), body)})
            injuries[str(n)] = count if type(count) is int and count >= 0 else None
        previous = dashboard if n+1 in rows else {}
    last = max(rows, default=0)
    for advance in strengths:
        n, deadline = advance['chapter'], advance['chapter']+3
        if n not in numbers and not any(n < x <= deadline for x in numbers):
            continue
        found, observed = [], []
        for j in range(n, min(deadline, last)+1):
            raw = mapping(raw_by_n.get(j))
            if type(raw.get('realm_displayed')) is bool:
                observed.append(j)
                if raw['realm_displayed'] and quoted(raw.get('realm_display_quote'), bodies.get(j)):
                    found.append(j)
            item = first(raw, ('realm_display', 'power_display', 'growth_display', 'ability_display'))
            items = item if isinstance(item, list) else [item] if isinstance(item, dict) else []
            if item is not None: observed.append(j)
            for event in items:
                if quoted(event.get('quote'), bodies.get(j)):
                    found.append(j)
        displays.append(dict(**advance, deadline=deadline, shown_chapters=sorted(set(found)),
                             status='shown' if found else 'pending' if last < deadline else
                             'missed' if len(set(observed)) == deadline-n+1 else 'Unknown'))
    values = {k: stats[k] for k in ('type_empty', 'hook_empty', 'payoff_empty', 'type_invalid', 'hook_invalid', 'payoff_invalid', 'payoff_quote_invalid', 'extraction_unknown')}
    fulfilled = {n: p for n, p in payoffs.items() if p}
    repeated = [n for n in numbers if n >= 3 and payoffs.get(n) and len({payoffs.get(j) for j in range(n-2, n+1)}) == 1]
    ten_windows = [list(range(n-9, n+1)) for n in numbers if n >= 10 and all(j in rows for j in range(n-9, n+1))]
    return dict(classification=values, due=dict(due, denominator=len(numbers), rate=due['paid']/due['due'] if due['due'] else None),
                state=dict(state_stats), hooks=hook_metrics(hooks, max(SPECS[folder.name][1], len(rows))) if folder.name in SPECS else {},
                solutions=solution_metrics(rows, numbers, canon, SPECS.get(folder.name, (1, last, ''))[1]),
                payoff_diversity=dict(consecutive_over_two_chapters=repeated,
                    complete_ten_windows=len(ten_windows),
                    under_three_types_ending_chapters=[w[-1] for w in ten_windows if len({fulfilled[j] for j in w if j in fulfilled}) < 3]),
                upgrade_displays=displays, physical_costs=dict(per_chapter=injuries,
                    violation_chapters=[int(n) for n, count in injuries.items() if count is not None and count > 1],
                    unknown_chapters=[int(n) for n, count in injuries.items() if count is None]))


def volume_metrics(rows, canon, bible, total):
    result = []
    initial = mapping(bible.get('initial_dashboard'))
    latest = mapping(rows[max(rows)].get('state')) if rows else {}
    latest_dash = mapping(latest.get('dashboard'))
    a, b = rank(initial.get('strength'), 'strength', canon), rank(latest_dash.get('strength'), 'strength', canon)
    for start in range(1, total+1, 25):
        end = min(start+24, total)
        before = initial if start == 1 else mapping(mapping(rows.get(start-1, {}).get('state')).get('dashboard'))
        state = mapping(rows.get(end, {}).get('state'))
        after = mapping(state.get('dashboard'))
        complete = all(rows.get(n, {}).get('status') == 'complete' and
                       (rows[n].get('state_failed') is False or rows[n].get('state_recovered') is True)
                       for n in range(start, end+1))
        pairs = [(rank(before.get(k), k, canon), rank(after.get(k), k, canon)) for k in ('strength', 'wealth', 'status')]
        increased = any(x is not None and y is not None and y > x for x, y in pairs)
        baseline_valid = start == 1 or rows.get(start-1, {}).get('state_failed') is False or rows.get(start-1, {}).get('state_recovered') is True
        if not complete or not baseline_valid or not increased and any(x is None or y is None for x, y in pairs): increased = None
        counts = {}
        for key in ('gains', 'costs'):
            ledger = state.get(key)
            valid = isinstance(ledger, list) and all(type(mapping(x).get('chapter')) is int for x in ledger)
            counts[key] = sum(start <= x['chapter'] <= end for x in ledger) if complete and valid else None
        gains, costs = counts['gains'], counts['costs']
        result.append(dict(start=start, end=end, complete=complete, increased=increased, **counts,
                           recorded_gain_ok=gains >= 2 if gains is not None else None,
                           recorded_cost_ok=costs <= 1.5*gains if gains is not None and costs is not None else None))
    return dict(initial_strength=initial.get('strength'), latest_strength=latest_dash.get('strength'),
                strength_delta=b-a if a is not None and b is not None else None,
                volumes=result, basis='saved state ledger; quote coverage reported separately',
                dashboard_samples={str(n):mapping(mapping(rows.get(n, {}).get('state')).get('dashboard'))
                                   for n in sorted({1, 10, 20 if total == 20 else 25, total})})


def book_stats(folder, name, phase):
    start, end, kind = SPECS[name]
    meta, bible = mapping(read(folder/'run.json')), mapping(read(folder/'story_bible.yaml'))
    policy = GROWTH['packs'].get(mapping(meta.get('book')).get('prompt_pack'), {})
    if policy and bible:
        bible = dict(bible, canon=dict(mapping(bible.get('canon')), solution_menu=policy['solution_menu'],
                     solution_aliases=GROWTH['solution_aliases'], final_volume_start=(end-1)//25*25+1))
    ledger = read(folder/'calls.json')
    calls = ledger if isinstance(ledger, list) and all(isinstance(c, dict) for c in ledger) else None
    base = baseline(meta, phase, name, folder, calls, kind == 'new')
    offset = base['base_calls']
    current = calls[offset:] if calls is not None and offset is not None else None
    historical = calls[:offset] if calls is not None and offset is not None else None
    rows, bodies = {}, {}
    for n in range(1, end+1):
        row = read(folder/f'chapters/{n:03d}.json')
        if isinstance(row, dict) and row.get('number') == n:
            rows[n] = row
        try: bodies[n] = (folder/f'chapter-{n:03d}.body.md').read_text(encoding='utf-8')
        except OSError: pass
    numbers = [n for n in rows if start <= n <= end]
    complete = [n for n in numbers if rows[n].get('status') == 'complete' and bodies.get(n)]
    metrics = chapter_metrics(folder, rows, bodies, numbers, mapping(bible.get('canon')), bible, calls or [])
    times = [rows[n]['seconds'] for n in numbers if numeric(rows[n].get('seconds')) is not None]
    active, old = numeric(meta.get('execution_seconds')), base['base_seconds']
    seconds = active-old if active is not None and old is not None and active >= old else None
    drops = mapping(meta.get('dropped_fields'))
    dropped = {n: drops.get(str(n), rows[n].get('dropped_fields')) for n in numbers}
    chars = {str(n): chinese_count(prose(bodies[n])) if n in bodies else None for n in numbers}
    quote_unknown, quote_invalid = [], []
    for n in numbers:
        raw = raw_extract(folder, n, rows[n], calls or [], bodies.get(n))
        if raw is None or any(not isinstance(raw.get(k), list) for k in ('gains', 'costs', 'solutions')):
            quote_unknown.append(n)
        elif any(not quoted(mapping(x).get('quote'), bodies.get(n)) for k in ('gains','costs','solutions') for x in raw[k]):
            quote_invalid.append(n)
    cm = call_metrics(current or [], list(range(start, end+1)))
    elapsed_start = timestamp(base['started'])
    elapsed_end = timestamp(meta.get('finished') or meta.get('updated'))
    cm['known'] = current is not None
    return dict(name=name, folder=str(folder), kind=kind, status=meta.get('status', 'Unknown'), baseline=base,
        progress=dict(generated=len(complete), expected=end-start+1, missing=end-start+1-len(complete),
                      scoped_chapters=numbers, generated_chapters=complete,
                      regression_status=('recorded' if current else 'not_run_or_Unknown') if kind == 'regression' else None),
        usage=dict(round=usage_report(current) if current is not None else None,
                   historical=usage_report(historical) if historical is not None else None,
                   all=usage_report(calls) if calls is not None else None), calls=cm,
        timing=dict(chapter_seconds={str(n):rows[n].get('seconds') for n in numbers},
                    chapter_mean_seconds=sum(times)/len(times) if times else None,
                    chapter_max_seconds=max(times, default=None), chapter_seconds_unknown=len(numbers)-len(times),
                    chapters_at_most_100=sum(x <= 100 for x in times),
                    chapter_mean_within_100=(sum(times)/len(times) <= 100 if times and len(times) == len(numbers) else None),
                    round_execution_seconds=seconds, cumulative_execution_seconds=active,
                    round_including_planning_per_generated=seconds/len(complete) if seconds is not None and complete else None,
                    recorded_wall_seconds=(elapsed_end-elapsed_start).total_seconds() if elapsed_start and elapsed_end and elapsed_end >= elapsed_start else None),
        words=dict(cjk_chars=chars, in_1870_2700=sum(1870 <= c <= 2700 for c in chars.values() if c is not None),
                   unknown=sum(c is None for c in chars.values()), denominator=len(numbers)),
        growth=volume_metrics(rows, mapping(bible.get('canon')), bible, end),
        ledger_quotes=dict(unknown_chapters=quote_unknown, invalid_chapters=quote_invalid),
        dropped=dict(fields=sum(len(x) for x in dropped.values() if isinstance(x, list)),
                     coverage=sum(isinstance(x, list) for x in dropped.values()), denominator=len(numbers)),
        **metrics)


def collect(root=ROOT, legacy_root=None, phase_path=None):
    root = Path(root).resolve()
    legacy_root = Path(legacy_root).resolve() if legacy_root is not None else root.parent/'growth-payoff'
    phase_path = Path(phase_path) if phase_path else root/'phase.json'
    phase = mapping(read(phase_path))
    books = {name:book_stats((root if kind == 'new' else legacy_root)/name, name, phase)
             for name, (_, _, kind) in SPECS.items()}
    # Include unexpected round-root ledgers too: smoke/repair/planning calls cost money.
    known = {(Path(b['folder'])/'calls.json').resolve() for b in books.values()}
    extras = {}
    for path in sorted(root.rglob('calls.json')):
        if path.resolve() in known: continue
        calls = read(path)
        extras[str(path)] = usage_report(calls) if isinstance(calls, list) and all(isinstance(c, dict) for c in calls) else None
    usages = [b['usage']['round'] for b in books.values()]+list(extras.values())
    unknown = [name for name, b in books.items() if b['usage']['round'] is None]
    unknown += [name for name, u in extras.items() if u is None]
    amounts = [u['estimated_usd']['conservative_total'] for u in usages if u is not None]
    total = round(sum(amounts), 9) if not unknown and all(x is not None for x in amounts) else None
    return dict(schema_version=1, observed_at=dt.datetime.now(dt.timezone.utc).isoformat(), root=str(root),
                phase_path=str(phase_path), phase_present=phase_path.exists(), books=books, extra_ledgers=extras,
                round=dict(calls=sum(u['calls'] for u in usages if u is not None),
                    new_generated_chapters=sum(b['progress']['generated'] for b in books.values() if b['kind'] != 'regression'),
                    target_new_chapters=47,
                    known_tokens=sum(u['total_tokens'] for u in usages if u is not None),
                    failures=sum(u['failures'] for u in usages if u is not None),
                    retries=sum(u['retries'] for u in usages if u is not None),
                    unknown_usage_calls=sum(u['unknownusage'] for u in usages if u is not None),
                    conservative_usd=total, budget_usd=.6, within_budget=total <= .6 if total is not None else None,
                    known_no_cache_usd=round(sum(u['estimated_usd']['no_cache_discount'] for u in usages if u is not None),9),
                    unknown_ledgers=unknown, price_source=PRICE_SOURCE),
                evidence='Saved checkpoints/model extractions; incomplete/Unknown is not acceptance; no model calls made.')


def render(data):
    def f(x): return 'U' if x is None else f'{x:.2f}' if isinstance(x, float) else str(x)
    names = list(SPECS)
    lines = ['# growth-payoff 第二轮 · 程序快照',
             '保存的提取/状态与调用账单统计；U=Unknown，不代表人工验收。旧悬疑50为回归，费用计入本轮。', '',
             '| 指标 | 旧玄幻44–50 | 新玄幻20 | 新悬疑20 | 旧悬疑50回归 |', '|---|---|---|---|---|']
    cells = []
    for name in names:
        b = data['books'][name]
        p, d, s, h, sol, t = (b[k] for k in ('progress', 'due', 'state', 'hooks', 'solutions', 'timing'))
        u = b['usage']['round'] or {}
        cells.append([
            f"{p['generated']}/{p['expected']}；{p['regression_status'] or b['status']}",
            f"{d.get('paid',0)}/{d.get('due',0)}="+('U' if d['rate'] is None else f"{d['rate']*100:.1f}%")+f"；逐章due缺{d.get('every_chapter_due_violations',0)}",
            '/'.join(str(b['classification'][k+'_empty']) for k in ('type','hook','payoff')),
            '/'.join(str(s.get(k,0)) for k in ('actual_illegal_strength','actual_jump','unsupported_demotion')),
            '/'.join(str(s.get(k,0)) for k in ('rejected_illegal','rejected_jump','rejected_demotion')),
            (f"{len(b['calls']['violation_chapters'])}/{len(p['scoped_chapters'])}；全计{b['calls']['outside_body_calls']}次" if b['calls']['known'] else 'U（调用基线/账本缺）'),
            f"{f(t['chapter_mean_seconds'])}/{f(t['chapter_max_seconds'])}；U{t['chapter_seconds_unknown']}",
            f"{f(t['round_execution_seconds'])} / {f(t['round_including_planning_per_generated'])}",
            f"{len(sol['violation_chapters'])}/{sol['denominator']}；U{len(sol['unknown_chapters'])}",
            f"{sol['group_wholebook_count']} / {sol['group_final_volume_count']}",
            f"{h['complete_windows']}完整窗；峰{f(h['window_peak_ratio'])}；全书{h['wholebook_peak']}/{h['wholebook_denominator']}"+(' partial' if h['partial'] else ''),
            '; '.join(f"{x['chapter']}→{x['deadline']}:{x['status']}" for x in b['upgrade_displays']) or '无已知升级',
            f"{len(b['physical_costs']['violation_chapters'])}/{len(p['scoped_chapters'])}；U{len(b['physical_costs']['unknown_chapters'])}",
            f"{s.get('power_english_keys',0)} / {b['dropped']['fields']}（覆盖{b['dropped']['coverage']}）",
            f"{s.get('original_failures',0)}/{s.get('recovered',0)}/{s.get('unresolved',0)}；提取U{b['classification']['extraction_unknown']}",
            f"{u.get('calls','U')}/{u.get('total_tokens','U')}/{u.get('failures','U')}/{u.get('retries','U')}",
            f"{b['words']['in_1870_2700']}/{b['words']['denominator']}",
            ('U' if mapping(u.get('estimated_usd')).get('conservative_total') is None else
             f"{u['estimated_usd']['conservative_total']:.4f}"),
            f"{f(b['growth']['initial_strength'])}→{f(b['growth']['latest_strength'])}；{f(b['growth']['strength_delta'])}级",
            '；'.join(f"{v['start']}-{v['end']}:{'升' if v['increased'] else 'U' if v['increased'] is None else '未升'} {f(v['gains'])}/{f(v['costs'])}" for v in b['growth']['volumes']),
        ])
    labels = ['完稿/目标','due兑现率/玄幻逐章due','type/hook/payoff空','实际非法/跳级/无据降级','拒绝非法/跳级/降级',
              '正文外>2违规章/已写章（含word/repair/重试）','章内均耗≤100/最大秒','本轮累计含规划秒/完稿均耗',
              '解法违规章/已写章（1主类、30≤2、5不重）','群体表态全书≤2/末卷≤1',
              '钩子30窗/全书单类≤25%','升级展示章→deadline','单章新增伤残>1违规/U',
              'power英文键/裁字段','原始失败/补回/未回','本轮calls/tokens/失败/网络重试','字数1870–2700','本轮保守费用USD',
              '初始→最新实力','卷级上涨/收获代价（记录值）']
    lines += ['| '+label+' | '+' | '.join(c[i].replace('|','／') for c in cells)+' |' for i, label in enumerate(labels)]
    r = data['round']
    amount = 'U' if r['conservative_usd'] is None else f"{r['conservative_usd']:.4f}"
    lines += ['', f"本轮保守费用 ${amount} / ≤$0.6；新增{r['calls']} calls，{r['known_tokens']}已知tokens，usage未知{r['unknown_usage_calls']}；账本未知{len(r['unknown_ledgers'])}。历史账单在JSON的usage.historical/all中保留，不重计本轮。",
              f"基线优先phase.json，其次本轮revision；章内时间取实row.seconds。完整JSON含逐章调用、违规章号、partial与Unknown。价源：{PRICE_SOURCE}。", '']
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--legacy-root', type=Path)
    parser.add_argument('--phase', type=Path)
    parser.add_argument('--write', action='store_true', help='write engine/reports/growth-payoff-2.md snapshot')
    args = parser.parse_args()
    data = collect(args.root, args.legacy_root, args.phase)
    if args.write:
        REPORT.write_text(render(data), encoding='utf-8')
        print(f'wrote {REPORT}', file=sys.stderr)
    print(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
