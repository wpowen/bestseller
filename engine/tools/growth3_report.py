"""Read-only INBOX13 statistics. Prints JSON; never writes books or reports.

python3 -B engine/tools/growth3_report.py [--root ../engine-books/growth-payoff-3]
Costs cover every unique phase.base_calls ledger after its recorded offset,
including legacy ending regressions. None means Unknown, never an assumed zero.
Content diagnostics describe saved state and program candidates, not human QA.
"""
import argparse
from collections import Counter
import datetime as dt
import json
from pathlib import Path
import re
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
from growth2_report import (ENGINE, PRICE_SOURCE, TYPES, book_stats, hook_metrics,
                            mapping, numeric, rank, read, usage_report)
from bookengine.identity import name_hits, validate_bible
from bookengine.endings import english_hits
from bookengine.text import chinese_count

ROOT = ENGINE.parent.parent / 'engine-books/growth-payoff-3'
BOOKS = ('xuanhuan-20', 'suspense-20')


def folder_of(value, root):
    path = Path(str(value))
    return (path if path.is_absolute() else root / path).resolve()


def entries(value):
    if isinstance(value, list):
        return {str(mapping(v).get('label', mapping(v).get('name', i))): mapping(v)
                for i, v in enumerate(value)}
    return mapping(value)


def ledger_stats(root, phase):
    grouped, diagnostics = {}, []
    for label, offset in mapping(phase.get('base_calls')).items():
        path = folder_of(label, root)
        path = path if path.name == 'calls.json' else path / 'calls.json'
        entry = grouped.setdefault(str(path.resolve()), dict(labels=[], offsets=[]))
        entry['labels'].append(label)
        entry['offsets'].append(offset)
    for label, spec in {**entries(phase.get('books')), **entries(phase.get('regressions'))}.items():
        if not isinstance(spec, dict):
            continue
        path = folder_of(spec.get('path', label), root) / 'calls.json'
        if str(path.resolve()) not in grouped:
            diagnostics.append('uncovered ledger: ' + str(path))
    for path in root.rglob('calls.json'):
        if str(path.resolve()) not in grouped:
            diagnostics.append('uncovered ledger: ' + str(path))
    ledgers = {}
    for path, info in grouped.items():
        offsets, calls = info['offsets'], read(Path(path))
        valid = (all(type(n) is int and n >= 0 for n in offsets)
                 and len(set(offsets)) == 1 and isinstance(calls, list)
                 and all(isinstance(c, dict) for c in calls) and offsets[0] <= len(calls))
        ledgers[path] = dict(**info, known=valid, usage=usage_report(calls[offsets[0]:]) if valid else None,
                             error=None if valid else 'Missing/invalid ledger, offset, or conflicting aliases')
    usages = [x['usage'] for x in ledgers.values() if x['known']]
    unknown = [p for p, x in ledgers.items() if not x['known']]
    estimates = {}
    for key in ('cache_adjusted', 'no_cache_discount', 'unknown_upper_bound', 'conservative_total'):
        amounts = [u['estimated_usd'][key] for u in usages]
        estimates[key] = (round(sum(amounts), 9) if grouped and not unknown and not diagnostics
                          and all(v is not None for v in amounts) else None)
    total = estimates['conservative_total']
    return dict(ledgers=ledgers, errors=diagnostics, unknown_ledgers=unknown,
                unique_ledgers=len(ledgers), estimated_usd=estimates, conservative_usd=total,
                known_no_cache_usd=round(sum(u['estimated_usd']['no_cache_discount'] for u in usages), 9),
                calls=sum(u['calls'] for u in usages), known_tokens=sum(u['total_tokens'] for u in usages),
                failures=sum(u['failures'] for u in usages), retries=sum(u['retries'] for u in usages),
                unknown_usage_calls=sum(u['unknownusage'] for u in usages), budget_usd=.4,
                within_budget=total <= .4 if total is not None else None, price_source=PRICE_SOURCE)


def final_body(folder, n):
    try:
        return (folder / f'chapter-{n:03d}.body.md').read_text(encoding='utf-8')
    except OSError:
        return None


def extra_metrics(folder, stats):
    bible = mapping(read(folder / 'story_bible.yaml'))
    cards = bible.get('characters')
    cards_count = len(cards) if isinstance(cards, list) else 0
    try:
        validate_bible(bible)
        valid, card_error = True, None
    except (ValueError, TypeError) as exc:
        valid, card_error = False, str(exc)
    rows = {n: mapping(read(folder / f'chapters/{n:03d}.json')) for n in range(1, 21)}
    outlines = {n: mapping(read(folder / f'writing_outlines/{n:03d}.json')) for n in range(1, 21)}
    bodies = {n: final_body(folder, n) for n in rows}
    observed = [n for n, row in rows.items() if row.get('number') == n]
    missing = [n for n in rows if n not in observed or bodies[n] is None]
    drift = {str(n): name_hits(bodies[n], bible) for n in observed if bodies[n] is not None and valid}
    recorded_drift = [n for n in observed if rows[n].get('name_drift')]
    english = {str(n): english_hits(bodies[n]) for n in observed if bodies[n] is not None}
    canon = mapping(bible.get('canon'))
    hooks, empty, invalid = {}, {}, {}
    menus = dict(type=TYPES, hook=set(canon.get('hook_types', [])), payoff=set(canon.get('payoff_menu', [])) | {'无兑现'})
    for key in menus:
        values = {n: mapping(row.get('payoff')).get('type') if key == 'payoff' else row.get(key if key == 'type' else 'hook_type')
                  for n, row in rows.items() if n in observed}
        empty[key] = [n for n, value in values.items() if not isinstance(value, str) or not value.strip()]
        invalid[key] = [n for n, value in values.items() if n not in empty[key]
                        and value not in menus[key] and not (key == 'hook' and n == 20 and value == '完结收束')]
        if key == 'hook':
            hooks = {n: value if n not in empty[key] + invalid[key] else None for n, value in values.items()}
    stats['classification'].update(hook_empty=len(empty['hook']), hook_invalid=len(invalid['hook']))
    stats['hooks'] = hook_metrics(hooks, 20)
    stats['hooks']['terminal_hook'] = rows[20].get('hook_type')
    stats['hooks']['terminal_hook_legal'] = hooks.get(20) is not None if 20 in observed else None
    before, previous_known, upgrades = mapping(bible.get('initial_dashboard')), True, []
    last_upgrade = 0
    for n in observed:
        after = mapping(mapping(rows[n].get('state')).get('dashboard'))
        a, b = rank(before.get('strength'), 'strength', canon), rank(after.get('strength'), 'strength', canon)
        if a is not None and b is not None and b > a:
            setup = outlines.get(n-1, {})
            upgrades.append(dict(chapter=n, before=before.get('strength'), after=after.get('strength'), levels=b-a,
                                 gap=n-last_upgrade if previous_known else None, previous_growth_setup=setup.get('growth_setup'),
                                 previous_next_upgrade=setup.get('next_upgrade'), upgrade_due=outlines[n].get('upgrade_due')))
            last_upgrade, previous_known = n, True
        elif a is None or b is None or (n > 1 and n-1 not in observed):
            previous_known = False
        before = after
    word_counts = stats['words']['cjk_chars']
    good = sum(v is not None and 1870 <= v <= 2700 for v in word_counts.values())
    stats['words'].update(target_denominator=20, target_rate=good/20,
                          observed_rate=good/len(observed) if observed else None,
                          at_least_85_percent=good/20 >= .85 if len(observed) == 20 and not missing else None,
                          out_of_range_chapters=[int(n) for n, v in word_counts.items() if v is not None and not 1870 <= v <= 2700])
    all_calls = read(folder / 'calls.json')
    offset = stats['baseline']['base_calls']
    calls = all_calls[offset:] if isinstance(all_calls, list) and type(offset) is int else None
    outside = {}
    for n in observed:
        items = [c for c in calls or [] if re.search(rf'(?:^|-)ch0*{n}(?:-|$)', str(c.get('stage', '')))]
        other = [c for c in items if not re.search(r'(?:^|-)ch\d+-draft(?:-retry)?$', str(c.get('stage', '')))]
        outside[str(n)] = dict(physical_calls=len(other), logical_calls=len({str(c.get('stage')) for c in other}),
                              network_retries=sum(type(c.get('attempt')) is int and c['attempt'] > 1 for c in other))
    solutions = stats['solutions']
    group_bad = solutions['group_wholebook_violation_chapters'] + solutions['group_final_volume_violation_chapters']
    group_unknown = sorted(set(solutions['unknown_chapters'] + [n for n in observed
                           if rows[n].get('state_failed') is True and rows[n].get('state_recovered') is not True]))
    outside_bad = [int(n) for n, value in outside.items() if value['logical_calls'] > 2]
    empty_drafts = [n for n in observed if (draft := folder / f'chapter-{n:03d}.r2.draft.md').exists()
                    and chinese_count(draft.read_text(encoding='utf-8')) == 0]
    return dict(cards_valid=valid if bible else None, card_count=cards_count, cards_error=card_error,
                empty_draft_chapters=empty_drafts,
                card_names=[c.get('name') for c in cards if isinstance(c, dict)] if isinstance(cards, list) else [],
                name_drift_chapters=sorted(set(recorded_drift + [int(n) for n, hits in drift.items() if hits])),
                name_drift_hits=drift, name_drift_recorded_chapters=recorded_drift,
                name_drift_unknown_chapters=[n for n in range(1,21) if str(n) not in drift],
                english_chapters=[int(n) for n, hits in english.items() if hits], english_hits=english,
                english_unknown_chapters=[n for n in range(1,21) if str(n) not in english],
                injury_over_one_chapters=stats['physical_costs']['violation_chapters'],
                injury_unknown_chapters=stats['physical_costs']['unknown_chapters'],
                ending=dict(opens_new_mainline=rows[20].get('opens_new_mainline'),
                            closed=rows[20].get('opens_new_mainline') is False if 20 in observed else None),
                classification=dict(empty_chapters=empty, invalid_chapters=invalid, missing_chapters=missing),
                upgrades=dict(actual=upgrades, gap_outside_4_8=[x['chapter'] for x in upgrades if x['gap'] is not None and not 4 <= x['gap'] <= 8],
                              previous_setup_missing=[x['chapter'] for x in upgrades if x['previous_growth_setup'] is not True],
                              planned={str(n): {k: o.get(k) for k in ('growth_setup','upgrade_due','next_upgrade')}
                                       for n, o in outlines.items() if o}, basis='saved dashboard transitions and writing_outlines'),
                outside_body=dict(per_chapter=outside, known=calls is not None, violation_chapters=outside_bad,
                                  within_two=False if outside_bad else True if calls is not None and not missing else None),
                group_quota=dict(wholebook_count=solutions['group_wholebook_count'], wholebook_max=2,
                                 final_volume_count=solutions['group_final_volume_count'], final_volume_max=1,
                                 violation_chapters=sorted(set(group_bad)),
                                 unknown_chapters=group_unknown,
                                 within_limit=False if group_bad else True if not missing and not group_unknown else None),
                chapter_mean_within_100=stats['timing']['chapter_mean_within_100'] if not missing else None)


def collect(root=ROOT, phase_path=None):
    root = Path(root).resolve()
    phase_path = Path(phase_path) if phase_path else root / 'phase.json'
    phase = mapping(read(phase_path))
    books = {}
    for name in BOOKS:
        spec = mapping(entries(phase.get('books')).get(name))
        folder = folder_of(spec.get('path', name), root)
        stats = book_stats(folder, name, phase)
        stats['inbox13'] = extra_metrics(folder, stats)
        books[name] = stats
    regressions = {}
    results = entries(phase.get('regression_results'))
    for label, spec in entries(phase.get('regressions')).items():
        spec = mapping(spec)
        label = str(spec.get('label', label))
        folder, n = folder_of(spec.get('path', label), root), spec.get('chapter')
        row = mapping(read(folder / f'chapters/{n:03d}.json')) if type(n) is int else {}
        body = final_body(folder, n) if type(n) is int else None
        regressions[label] = dict(path=str(folder), chapter=n, baseline=spec.get('baseline'),
                                  result=results.get(label), opens_new_mainline=row.get('opens_new_mainline'),
                                  english=english_hits(body) if body is not None else None)
    round_stats = ledger_stats(root, phase)
    round_stats.update(new_generated_chapters=sum(b['progress']['generated'] for b in books.values()), target_new_chapters=40)
    return dict(schema_version=1, observed_at=dt.datetime.now(dt.timezone.utc).isoformat(), root=str(root),
                phase_path=str(phase_path), phase_present=phase_path.exists(), books=books, regressions=regressions,
                regression_results=phase.get('regression_results'), round=round_stats,
                basis='final saved bodies; checkpoint extraction assertions are not human content acceptance')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--phase', type=Path)
    args = parser.parse_args()
    print(json.dumps(collect(args.root, args.phase), ensure_ascii=False, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
