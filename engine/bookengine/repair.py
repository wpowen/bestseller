"""One bounded, paragraph-only fix of residual program findings."""
from pathlib import Path

from .checks import advance_state, check_events, text_issues
from .quality import (EVENTS, EVENT_SCHEMA, anchor_events, ask, check_book,
                      normalize_patches, patched_records, require, validate_patches)
from .storage import load, save
from .text import _paragraph_spans, apply_patches, pack

SCHEMA = ('{"patches":[{"chapter":1,"paragraph":1,"replacement":"","reason":"",'
          '"events":[' + EVENT_SCHEMA + ']}]}')


def remaining_points(chapters, records, banned):
    state, points = {}, set()
    for idx, (text, record) in enumerate(zip(chapters, records), 1):
        for issue in text_issues(text, banned, chapters[:idx - 1]) + check_events(state, record['events'], text):
            if issue['kind'] != 'ungrounded_event':
                points.add((idx, issue['paragraph']))
        state = advance_state(state, record['events'], text)
    return points


def fix_program(client, book, chapters, records, banned, out):
    for stage in ('06-check-fix', '07-check-fix', '08-check-fix'):
        path = Path(out) / 'steps' / (stage + '.json')
        if path.exists():
            patches = normalize_patches(load(path))['patches']
            final, updated = apply_patches(chapters, patches), patched_records(records, patches)
            bad_anchors = any(i['kind'] == 'ungrounded_event' for p in patches for i in
                              check_events({}, p['events'], p['replacement']))
            if stage == '08-check-fix' and (bad_anchors or remaining_points(final, updated, banned) & {
                    (p['chapter'], p['paragraph']) for p in patches}):
                target = Path(out) / 'invalid-states' / f'{stage}-{len(client.calls)}.json'
                target.parent.mkdir(parents=True, exist_ok=True)
                path.replace(target)
                for call in reversed(client.calls):
                    if call['stage'] == stage:
                        call.setdefault('previous_status', call['status'])
                        call['status'] = 'invalid_stage_output'
                        break
                save(client.history_path, client.calls)
                chapters, records, report = _fix_program(client, book, chapters, records, banned, out, stage)
                continue
            chapters, records = final, updated
            report = check_book(chapters, records, [], banned, book.words_per_chapter, out)
        else:
            chapters, records, report = _fix_program(client, book, chapters, records, banned, out, stage)
    return chapters, records, report


def _fix_program(client, book, chapters, records, banned, out, stage):
    report = check_book(chapters, records, [], banned, book.words_per_chapter, out)
    points, previous_states = [], {}
    allowed = set()
    for row in report['chapters']:
        idx = row['chapter']
        spans = _paragraph_spans(chapters[idx - 1])
        for number in sorted({i['paragraph'] for i in row['issues'] if i['kind'] != 'ungrounded_event'}):
            a, b = spans[number - 1]
            events = [e for e in records[idx - 1]['events'] if e['paragraph'] == number]
            points.append(dict(chapter=idx, paragraph=number, text=chapters[idx - 1][a:b],
                               events=events,
                               issues=[dict(kind=i['kind'], suggestion=i['suggestion'].partition('」：')[2])
                                       for i in row['issues'] if i['paragraph'] == number]))
            allowed.add((idx, number))
            previous_states[idx] = report['chapters'][idx - 2]['state'] if idx > 1 else {}
    if not points:
        return chapters, records, report

    def normalize(value):
        patches = value.get('patches', [])
        return dict(value, patches=[anchor_events(p, p.get('replacement', '')) for p in patches
                    if isinstance(p, dict) and (p.get('chapter'), p.get('paragraph')) in allowed])

    def validate(value):
        validate_patches(value, chapters)
        for patch in value['patches']:
            point = next(p for p in points if p['chapter'] == patch['chapter'] and p['paragraph'] == patch['paragraph'])
            if patch['replacement'].strip() and point['events']:
                key = lambda e: (e.get('kind'), e.get('name') if e.get('kind') != 'time' else None)
                old = {key(e) for e in point['events'] if e.get('quote') in point['text']}
                new = {key(e) for e in patch['events']}
                require(old <= new, '非空替换段漏记原实体：' + pack(sorted(str(k) for k in old - new)))
            require(not any(i['kind'] == 'ungrounded_event' for i in
                            check_events({}, patch['events'], patch['replacement'])))
        require({(p['chapter'], p['paragraph']) for p in value['patches']} == allowed)
        final = apply_patches(chapters, value['patches'])
        updated = patched_records(records, value['patches'])
        residual = remaining_points(final, updated, banned) & allowed
        require(not residual, '这些章/段修后仍有程序问题：' + pack(sorted(residual)))

    result = ask(client, stage,
                 '只修程序点名段落，一次完成，不改其它段落，不增情节，不做字数重写。'
                 '区分正文矛盾与状态抽取误差：若正文已写明转场/跨夜/回忆，保留原段原文，'
                 '只修该段events；若正文确实矛盾，补清对应段落的入场、移动或时间。'
                 '每个点名段落返回一个patch，replacement仅一段，无空行。'
                 '同一地点的不同描述须统一events.location为旧状态规范名称；'
                 '随身物或数字物载体改变时，依据正文记move。修后点名问题必须消失。'
                 '程序地点问题不能仅用新地点加stay；须补写明确的转场/移物并记move。'
                 '必须完整保留该段仍存在的人物、物品、时钟、伏笔事件，不能靠清空events消除问题；'
                 '非空段必须保留原有类型与实体键；删除整段则replacement与events均空。'
                 'day沿既有时间线递增，跨午夜正确加日，引用旧记录/回忆用flashback=true。' + EVENTS,
                 {'问题段落': pack(points), '各问题章进入时状态': pack(previous_states)}, schema=SCHEMA, cap=16000,
                 validate=validate, normalize=normalize)
    final = apply_patches(chapters, result['patches'])
    updated = patched_records(records, result['patches'])
    return final, updated, check_book(final, updated, [], banned, book.words_per_chapter, out)
