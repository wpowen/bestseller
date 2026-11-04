"""Read growth-payoff checkpoints; --write saves only the final one-page Markdown.

python3 engine/tools/growth_report.py
python3 engine/tools/growth_report.py --write --test-log /tmp/growth-tests.log
"""
import argparse
from collections import Counter
import datetime as dt
from decimal import Decimal, InvalidOperation
import json
import math
from os.path import relpath
from pathlib import Path
import re
import subprocess
import sys

import yaml

ENGINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ENGINE))
from bookengine.batch import PRICE_SOURCE, usage_report
from bookengine.q1 import normalize_extract
from bookengine.contracts import growth_warnings, observations

ROOT = ENGINE.parent.parent / 'engine-books/growth-payoff'
REPORT = ENGINE / 'reports/growth-payoff.md'
BOOKS = ('xuanhuan-50', 'suspense-50')
MAIN_PAYOFFS = {'升级', '打脸', '收获', '身份抬升', '碾压'}
TYPES = {'对抗', '谋划', '情感', '日常', '揭秘', '收获'}
TERMINAL = {'complete', 'incomplete', 'stopped', 'failed', 'interrupted'}


def read(path):
    try:
        text = path.read_text(encoding='utf-8')
        return yaml.safe_load(text) if path.suffix == '.yaml' else json.loads(text)
    except (OSError, ValueError, yaml.YAMLError):
        return None


def mapping(value):
    return value if isinstance(value, dict) else {}


def numeric(value):
    return value if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None


def canonical(value, aliases):
    visited = set()
    while isinstance(value, str) and value in aliases and aliases[value] != value:
        if value in visited:
            return None
        visited.add(value)
        value = aliases[value]
    return value if isinstance(value, str) else None


def rank(value, field, canon):
    ladder = mapping(canon.get('ladder'))
    if field != 'wealth':
        order = ladder.get(field, [])
        return order.index(value) if isinstance(order, list) and value in order else None
    unit = ladder.get('wealth_unit')
    if not isinstance(unit, str) or not unit:
        return None
    match = re.fullmatch(r'\s*(\d+(?:\.\d+)?)\s*' + re.escape(unit), str(value))
    try:
        return Decimal(match[1]) if match else None
    except InvalidOperation:
        return None


def english_keys(value):
    if isinstance(value, dict):
        return sum(bool(re.search('[A-Za-z]', str(k))) + english_keys(v) for k, v in value.items())
    return sum(map(english_keys, value)) if isinstance(value, list) else 0


def quoted(value, body):
    return isinstance(body, str) and isinstance(value, str) and bool(value.strip()) and value in body


def extraction(folder, n, row, calls=(), body=None, canon=None):
    if row.get('state_failed') is True and row.get('state_recovered') is not True:
        return None
    suffix = 'recover' if row.get('state_recovered') is True else 'final-state' if row.get('payoff_patch') is True else 'state'
    pattern = rf'^g-ch0*{n}-{suffix}(?:-retry)?(?:-schema2)?$'
    stages = [c['stage'] for c in reversed(calls) if isinstance(c.get('stage'),str)
              and re.fullmatch(pattern,c['stage'])]
    stages += [f'g-ch{n}-{suffix}{tail}' for tail in ('-retry-schema2','-schema2','-retry','')]
    newer = [s for s in stages if s.endswith('-schema2') and
             (any(c.get('stage') == s for c in calls) or (folder / f'steps/{s}.json').exists())]
    stages = newer + [s for s in stages if not s.endswith('-schema2')]
    for stage in dict.fromkeys(stages):
        raw = normalize_extract(read(folder / f'steps/{stage}.json'))
        payoff = mapping(mapping(raw).get('payoff'))
        valid_quote = (quoted(payoff.get('quote'),body) if payoff.get('fulfilled') is True
                       else payoff.get('quote') in (None,'') or quoted(payoff.get('quote'),body))
        if (isinstance(raw, dict) and isinstance(raw.get('state'), dict)
                and raw.get('type') in TYPES and raw.get('hook_type') in mapping(canon).get('hook_types',[])
                and type(payoff.get('fulfilled')) is bool and valid_quote
                and (payoff.get('type') in mapping(canon).get('payoff_menu',[]) if payoff['fulfilled'] else payoff.get('type') == '无兑现')
                and raw.get('type') == row.get('type') and raw.get('hook_type') == row.get('hook_type')
                and raw.get('payoff') == row.get('payoff')):
            return raw
    return None


def window_stats(values, size, limit):
    peaks, unknown, bad = [], 0, 0
    for start in range(51 - size):
        window = values[start:start + size]
        peak = max(Counter(x for x in window if x).values(), default=0)
        bad += peak > limit
        unknown += None in window
        if None not in window:
            peaks.append(peak)
    return dict(peak=max(peaks) if peaks else None, unknown=unknown, bad=bad, complete=len(peaks))


def timestamp(value):
    try:
        parsed = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
        return parsed.replace(tzinfo=dt.timezone.utc) if parsed.tzinfo is None else parsed
    except (AttributeError, TypeError, ValueError):
        return None


def elapsed(meta, calls, now):
    starts = [timestamp(meta.get('started')), *(timestamp(c.get('started')) for c in calls)]
    starts = [x for x in starts if x is not None]
    end = timestamp(meta.get('finished'))
    if end is None:
        ends = [timestamp(meta.get('updated'))]
        ends += [timestamp(c['started']) + dt.timedelta(seconds=c['seconds']) for c in calls
                 if timestamp(c.get('started')) and numeric(c.get('seconds')) is not None]
        end = max((x for x in ends if x is not None), default=None)
    # execution_seconds is already cumulative across resumes; never sum previous_runs.
    active = numeric(meta.get('execution_seconds'))
    wall = max(0, (end-min(starts)).total_seconds()) if end and starts else None
    return dict(active=active, wall=wall)


def book_stats(folder, now=None):
    now = now or dt.datetime.now(dt.timezone.utc)
    meta, bible = mapping(read(folder / 'run.json')), mapping(read(folder / 'story_bible.yaml'))
    canon, initial = mapping(bible.get('canon')), mapping(bible.get('initial_dashboard'))
    rows, bodies = {}, {}
    for n in range(1, 51):
        row = read(folder / f'chapters/{n:03d}.json')
        if isinstance(row, dict) and type(row.get('number')) is int and row['number'] == n:
            rows[n] = row
        try:
            bodies[n] = (folder / f'chapter-{n:03d}.body.md').read_text(encoding='utf-8')
        except OSError:
            bodies[n] = None
    raw_calls = read(folder / 'calls.json')
    calls = [c for c in raw_calls if isinstance(c, dict)] if isinstance(raw_calls, list) else []
    stats = Counter()
    verified = {key:[] for key in ('gains','costs','solutions')}
    observed_chapters = {key:set() for key in verified}
    hooks, payoffs = [], []
    previous = initial
    hook_menu, payoff_menu = canon.get('hook_types', []), canon.get('payoff_menu', [])
    for n in range(1, 51):
        row, body = rows.get(n, {}), bodies[n]
        payoff = mapping(row.get('payoff'))
        stats['type_empty'] += not isinstance(row.get('type'), str) or not row.get('type', '').strip()
        stats['hook_empty'] += not isinstance(row.get('hook_type'), str) or not row.get('hook_type', '').strip()
        stats['payoff_empty'] += not payoff or not isinstance(payoff.get('type'), str) or not payoff.get('type', '').strip()
        stats['type_invalid'] += bool(row.get('type')) and row.get('type') not in TYPES
        hook = row.get('hook_type')
        hooks.append(hook if hook in hook_menu and isinstance(hook, str) else None)
        aliases = {**mapping(canon.get('payoff_subtype_to_type')), **mapping(canon.get('payoff_aliases'))}
        actual = canonical(payoff.get('type'), aliases)
        valid_payoff = (type(payoff.get('fulfilled')) is bool and
                        (actual in payoff_menu if payoff.get('fulfilled') is True else actual == '无兑现'))
        has_quote = quoted(payoff.get('quote'), body)
        stats['quote_invalid'] += payoff.get('fulfilled') is True and body is not None and not has_quote
        stats['payoff_unknown'] += not valid_payoff or payoff.get('fulfilled') is True and not has_quote
        payoffs.append(actual if valid_payoff and payoff['fulfilled'] and has_quote else '' if valid_payoff and payoff['fulfilled'] is False else None)
        if type(row.get('payoff_due')) is not bool:
            stats['due_unknown'] += 1
        elif row['payoff_due']:
            stats['due'] += 1
            paid = valid_payoff and payoff['fulfilled'] is True and has_quote and actual == row.get('payoff_type')
            stats['paid'] += paid
            stats['due_payoff_unknown'] += not valid_payoff or body is None
            stats['fulfillment_flag_mismatch'] += type(row.get('payoff_fulfilled')) is not bool or row['payoff_fulfilled'] != paid
        recovered = row.get('state_recovered') is True
        failed = row.get('state_failed_original',row.get('state_failed'))
        stats['state_history_unknown'] += type(failed) is not bool and not recovered
        stats['state_failed'] += failed is True or recovered
        stats['state_recovered'] += recovered
        retries = row.get('state_retries')
        stats['state_retries_unknown'] += type(retries) is not int or retries < 0
        stats['state_retries'] += retries if type(retries) is int and retries >= 0 else 0
        stats['payoff_patch'] += row.get('payoff_patch') is True
        state = mapping(row.get('state'))
        power = state.get('power_status')
        stats['power_unknown'] += not isinstance(power, dict)
        stats['power_english'] += english_keys(power)
        dashboard = mapping(state.get('dashboard'))
        value = dashboard.get('strength')
        current_rank, previous_rank = rank(value, 'strength', canon), rank(previous.get('strength'), 'strength', canon)
        if value is None or not canon.get('ladder'):
            stats['strength_unknown'] += 1
        elif current_rank is None:
            stats['strength_invalid'] += 1
        if current_rank is not None and previous_rank is not None and current_rank < previous_rank:
            raw = extraction(folder, n, row, calls, body, canon)
            if raw is None or body is None:
                stats['demotion_unknown'] += 1
            elif not quoted(mapping(raw.get('demotion_event')).get('quote'), body):
                stats['unsupported_demotion'] += 1
        elif current_rank is not None and previous_rank is None and n > 1:
            stats['demotion_unknown'] += 1
        for issue in row.get('issues', []) if isinstance(row.get('issues'), list) else []:
            stats['reject_strength'] += issue == 'strength_outside_canon'
            stats['reject_demotion'] += issue == 'unsupported_demotion'
        previous = dashboard
        stats['solutions_missing_rows'] += not isinstance(row.get('solutions'), list)
        stats['solutions_empty_rows'] += row.get('solutions') == []
        if row:
            raw = extraction(folder,n,row,calls,body,canon)
            stats['metadata_unknown'] += raw is None
            for key in ('gains','costs','solutions'):
                items = mapping(raw).get(key)
                stats['ledger_quote_unknown'] += items is None or not isinstance(items,list)
                if isinstance(items,list):
                    observed_chapters[key].add(n)
                for item in items if isinstance(items,list) else []:
                    stats['ledger_quote_invalid'] += not quoted(mapping(item).get('quote'),body)
            for key,items in observations(raw,body,n).items():
                for item in items:
                    if key == 'solutions':
                        item['type'] = canonical(item['type'],mapping(canon.get('solution_aliases')))
                    verified[key].append(item)
    payoff_window = window_stats(payoffs, 10, 1)
    hook_window = window_stats(hooks, 30, 7.5)
    latest = mapping(rows[max(rows)].get('state')) if rows else {}
    final = mapping(rows.get(50, {}).get('state'))
    end_dashboard = mapping(final.get('dashboard'))
    a, b = rank(initial.get('strength'), 'strength', canon), rank(end_dashboard.get('strength'), 'strength', canon)
    unresolved = stats['state_failed']-stats['state_recovered']
    strength_delta = b-a if a is not None and b is not None and not unresolved else None
    volumes = []
    for vn,(start, end) in enumerate(((1, 25), (26, 50)),1):
        before = initial if start == 1 else mapping(mapping(rows.get(start-1, {}).get('state')).get('dashboard'))
        end_state = mapping(rows.get(end, {}).get('state'))
        after = mapping(end_state.get('dashboard'))
        pairs = [(rank(before.get(k), k, canon), rank(after.get(k), k, canon)) for k in ('strength','status','wealth')]
        increased = True if any(x is not None and y is not None and y > x for x,y in pairs) else None if any(x is None or y is None for x,y in pairs) else False
        counts = []
        complete_state = all(type(rows.get(n,{}).get('state_failed')) is bool and
                             (not rows[n]['state_failed'] or rows[n].get('state_recovered') is True)
                             for n in range(start,end+1))
        for key in ('gains', 'costs'):
            ledger = end_state.get(key)
            valid = isinstance(ledger, list) and all(isinstance(x,dict) and type(x.get('chapter')) is int and isinstance(x.get('type'),str) and x['type'].strip() and isinstance(x.get('content'),str) and x['content'].strip() for x in ledger)
            items = [x for x in ledger if start <= x['chapter'] <= end] if valid else []
            supported = all(n in observed_chapters[key] for n in range(start,end+1)) and all(x in verified[key] for x in items) and all(x in items for x in verified[key] if start<=x['chapter']<=end)
            counts.append(len(items) if valid and complete_state and supported else None)
        if not complete_state:
            increased = None
        baseline_row=rows.get(start-1,{})
        if start>1 and baseline_row.get('state_failed') is True and baseline_row.get('state_recovered') is not True:increased=None
        gains, costs = counts
        memory = mapping(read(folder/f'volumes/{vn:02d}-{end:03d}.json'))
        warning_unknown = not complete_state or gains is None or costs is None or increased is None or not isinstance(memory.get('volume_issues'),list)
        mismatch = None
        if not warning_unknown:
            expected = growth_warnings(end_state,before,after,canon,start,end)
            recorded = [w for w in memory['volume_issues'] if w in ('dashboard_no_growth','gains_below_two','cost_gain_ratio')]
            mismatch = (set(recorded)!=set(expected) or memory.get('before_dashboard')!=before or memory.get('after_dashboard')!=after
                        or canon.get('genre_kind')=='爽文' and ('dashboard_no_growth' in expected)==increased)
        volumes.append(dict(increased=increased, gains=gains, costs=costs,
            warning_mismatch=mismatch,
            gain_ok=None if gains is None else gains>=2,
            cost_ok=None if gains is None or costs is None else costs<=gains*1.5))
    ledger, aliases = latest.get('solutions'), canon.get('solution_aliases')
    solution_unknown = int(not isinstance(ledger, list)) + int(not isinstance(aliases, dict))
    normalized = []
    for item in ledger if isinstance(ledger, list) else []:
        item = mapping(item)
        kind = canonical(item.get('type'), mapping(aliases))
        if kind not in canon.get('solution_menu', []) or type(item.get('chapter')) is not int or not 1 <= item['chapter'] <= 50 or not isinstance(item.get('content'), str) or not item['content'].strip():
            solution_unknown += 1
        elif dict(item,type=kind) not in verified['solutions']:
            solution_unknown += 1
        else:
            normalized.append((item['chapter'], kind))
    last, repeats = {}, []
    for chapter, kind in sorted(normalized):
        if kind in last and chapter-last[kind] < 30:
            repeats.append((kind, last[kind], chapter))
        last[kind] = chapter
    drops = mapping(meta.get('dropped_fields'))
    covered = [n for n in range(1,51) if isinstance(drops.get(str(n)), list) and all(isinstance(x,str) for x in drops[str(n)])]
    times = [row['seconds'] for row in rows.values() if numeric(row.get('seconds')) is not None]
    return dict(name=folder.name, folder=folder, meta=meta, rows=rows, stats=stats, canon=canon,
        generated=sum(r.get('status')=='complete' and bool(bodies[n]) for n,r in rows.items()),
        due_rate=stats['paid']/stats['due'] if stats['due'] else None, payoff_window=payoff_window,
        hook_window=hook_window, hook_peak=max(Counter(x for x in hooks if x is not None).values(),default=0),
        hook_unknown=hooks.count(None), strength_delta=strength_delta, volumes=volumes,
        solution_records=len(normalized), solution_repeats=repeats,
        solution_unknown=solution_unknown+stats['solutions_missing_rows']+int(not normalized)+stats['state_failed']-stats['state_recovered'],
        dropped_coverage=len(covered), dropped_count=sum(len(drops[str(n)]) for n in covered),
        calls=calls, calls_unknown=not isinstance(raw_calls,list),
        api_retries=sum(type(c.get('attempt')) is int and c['attempt']>1 for c in calls),
        recover_retries=len({c['stage'] for c in calls if re.fullmatch(r'g-ch\d+-recover-retry(?:-schema2)?', str(c.get('stage','')))}),
        elapsed=elapsed(meta,calls,now), chapter_fast=sum(x<=110 for x in times),
        chapter_max=max(times) if times else None, chapter_time_unknown=50-len(times))


def costs(root):
    paths = sorted(root.rglob('calls.json'))
    missing = sum(not (root / book / 'calls.json').exists() for book in BOOKS)
    total, count, unknown = 0.0, 0, 0
    for path in paths:
        calls = read(path)
        if not isinstance(calls,list) or any(not isinstance(c,dict) for c in calls) or (not calls and any((path.parent/'chapters').glob('*.json'))):
            missing += 1
            continue
        usage = usage_report(calls)
        count += len(calls)
        unknown += usage['unknownusage']
        value = usage['estimated_usd']['conservative_total']
        if value is None:
            missing += 1
        else:
            total += value
    return dict(total=None if missing else total, calls=count, unknown_usage=unknown, missing=missing)


def test_result(path):
    if path is None:
        return 'Unknown（传--test-log读取最终结果）'
    try:
        text = path.read_text(encoding='utf-8')
    except OSError:
        return 'Unknown（测试日志不可读）'
    matches = list(re.finditer(r'Ran (\d+) tests?\b', text))
    if not matches:
        return 'Unknown（无测试汇总）'
    last = matches[-1]
    return f"{last[1]}项 " + ('PASS' if re.search(r'^OK\s*$',text[last.end():],re.M) else '未通过/Unknown')


def fmt(value, digits=1):
    return 'Unknown' if value is None else f'{value:.{digits}f}'


def render(books, cost, scope, tests):
    lines = ['# 成长爽点闭环 · 两本50章', '程序统计；U=Unknown，缺提取/记录不作通过；不代表人工正文验收。', '',
             '| 指标 | 玄幻 | 悬疑 |', '|---|---|---|']
    cells = []
    for book in books:
        s, h, p = book['stats'], book['hook_window'], book['payoff_window']
        due = f"{s['paid']}/{s['due']}={fmt(None if book['due_rate'] is None else book['due_rate']*100)}%；U{s['due_unknown']+s['due_payoff_unknown']}/标志冲突{s['fulfillment_flag_mismatch']}"
        volumes = book['volumes']
        gain_cost = '；'.join(f"{v['gains'] if v['gains'] is not None else 'U'}/{v['costs'] if v['costs'] is not None else 'U'}" + ('✓' if v['gain_ok'] and v['cost_ok'] else '×' if v['gain_ok'] is False or v['cost_ok'] is False else 'U') for v in volumes)
        rise = '/'.join('升' if v['increased'] else 'U' if v['increased'] is None else '未升' for v in volumes)
        warning_check = '/'.join('U' if v['warning_mismatch'] is None else '冲突' if v['warning_mismatch'] else '一致' for v in volumes)
        count = book['generated']
        denominator = 50 if count == 50 else count
        active, wall = book['elapsed']['active'], book['elapsed']['wall']
        timing = f"{fmt(active)}秒/{denominator}={fmt(active/denominator if active is not None and denominator else None)}秒" + ('（未完成）' if count<50 else '')
        cells.append([
            f"{book['generated']}/50，{book['meta'].get('status','Unknown')}", due,
            '不适用' if book['canon'].get('genre_kind')!='爽文' else f"峰{p['peak'] if p['peak'] is not None else 'U'}，超{p['bad']}窗/U{p['unknown']}；类别U{s['payoff_unknown']}",
            f"{s['type_empty']}/{s['hook_empty']}/{s['payoff_empty']}；非法type{s['type_invalid']}",
            f"{s['strength_invalid']}/{s['unsupported_demotion']}/U{s['strength_unknown']+s['demotion_unknown']}",
            f"{s['reject_strength']}/{s['reject_demotion']}",
            f"{book['strength_delta'] if book['strength_delta'] is not None else 'U'}级" + ('（目标≥2）' if book['name'].startswith('xuanhuan') else ''),
            rise+'；警告对账'+warning_check, gain_cost + ('（目标gain≥2，cost≤1.5gain）' if book['canon'].get('genre_kind')=='爽文' else '（无爽文门槛）'),
            f"{s['power_english']}/U{s['power_unknown']}",
            f"{book['dropped_coverage']}/50；{book['dropped_count']}字段；U{50-book['dropped_coverage']}",
            f"{fmt(None if h['peak'] is None else h['peak']/30*100)}%；{h['complete']}/21窗；超{h['bad']}/U{h['unknown']}",
            f"{book['hook_peak']}/{book['generated']}={fmt(book['hook_peak']/max(1,book['generated'])*100)}%；U{book['hook_unknown']}",
            f"{book['solution_records']}条/重复{len(book['solution_repeats'])}/U{book['solution_unknown']}",
            f"{s['state_failed']}；U{s['state_history_unknown']}",
            f"{s['state_recovered']}/{s['state_failed']-s['state_recovered']}",
            f"{s['quote_invalid']+s['ledger_quote_invalid']}/U{s['metadata_unknown']+s['ledger_quote_unknown']}",
            f"{s['state_retries']}（补提另{book['recover_retries']}）/{book['api_retries']}；U{s['state_retries_unknown']}",
            f"{s['payoff_patch']}/{len(book['calls'])}",
            timing, f"{fmt(wall)}秒（至末落盘，含停机）",
            f"{book['chapter_fast']}/{book['generated']}；最大{fmt(book['chapter_max'])}秒/U{book['chapter_time_unknown']}",
        ])
    labels = ['完成','due兑现≥95%','实兑现10窗峰≤1','type/hook/payoff空','实态非法/无据降级/U','reject非法/降级','相对初始实力','两卷三轴上涨','两卷gain/cost','power英文键/U','run裁字段覆盖/数','hook完整30窗峰≤25%','hook全书峰','解法近30去重','原始state失败数','state补回/未回','定稿quote无效/U','state逻辑重试/API重试','补写/全部调用','active含规划恢复/已写≤110','wall总耗','章内≤110/50；最大/U']
    lines += [f'| {label} | {cells[0][i]} | {cells[1][i]} |' for i,label in enumerate(labels)]
    lines += ['', '玄幻dashboard（实力/财富/地位/势力/舞台）：']
    for n in (1,10,25,50):
        dashboard = mapping(mapping(books[0]['rows'].get(n,{}).get('state')).get('dashboard'))
        lines.append(f"- {n}：" + '/'.join(str(dashboard.get(k,'Unknown')).replace('|','／') for k in ('strength','wealth','status','affiliation','stage')))
    lines.append('')
    for book,title in zip(books,('玄幻','悬疑')):
        links = []
        for n in (1,25,50):
            path = book['folder'] / f'chapter-{n:03d}.md'
            links.append(f'[{n}]({Path(relpath(path, REPORT.parent)).as_posix()})' + ('' if path.exists() else '(未导出)'))
        lines.append(title+'抽读：'+'、'.join(links))
    amount = cost['total']
    lines += ['', f"两书保守费：${fmt(amount,4)} / ≤$1.5；全部{cost['calls']}次，usage缺{cost['unknown_usage']}次，上界缺U{cost['missing']}。含构思、规划、state、补写与重试；仅本轮目录。",
              f"价源：[MiniMax]({PRICE_SOURCE})，输入/输出/缓存$0.15/$0.60/$0.015每百万tokens；保守费不扣缓存并加缺usage上界。",
              'active取累计execution_seconds，不叠加previous_runs；缺累计值为U。补充9：验收通过后再命名本书ladder；未通过则未执行、等待。',
              '测试：'+tests+'。', '```text', scope.strip(), '```']
    return '\n'.join(lines)+'\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=ROOT)
    parser.add_argument('--write',action='store_true')
    parser.add_argument('--test-log',type=Path)
    args = parser.parse_args()
    books = [book_stats(args.root / name) for name in BOOKS]
    cost = costs(args.root)
    for book in books:
        s = book['stats']
        count = book['generated']
        active = book['elapsed']['active']
        average = active/count if active is not None and count else None
        print(f"{book['name']} {book['meta'].get('status','Unknown')} {count}/50{' 未完成' if count<50 else ''} due={s['paid']}/{s['due']} Udue={s['due_unknown']} 原始state失败={s['state_failed']} 补回={s['state_recovered']} active={fmt(active)}s/{count}={fmt(average)}s wall={fmt(book['elapsed']['wall'])}s 章max={fmt(book['chapter_max'])}s")
    print(f"round conservative_usd={fmt(cost['total'],4)} calls={cost['calls']} usage_unknown={cost['unknown_usage']} bound_unknown={cost['missing']}")
    if args.write:
        if any(book['meta'].get('status') not in TERMINAL for book in books):
            parser.error('--write只用于两书结束后的最终报告；实跑仍在进行，未写文件')
        scope = subprocess.run([sys.executable,'-B',str(ENGINE/'tools/check_scope.py')],capture_output=True,text=True)
        text = render(books,cost,scope.stdout or scope.stderr,test_result(args.test_log))
        REPORT.write_text(text,encoding='utf-8')
        han_chars = len(re.findall(r'[\u4e00-\u9fff]',text))
        print(f'wrote {REPORT}；中文字符={han_chars}')


if __name__ == '__main__':
    main()
