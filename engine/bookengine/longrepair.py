"""One book-level calibration of remaining findings, with resumable local edits."""
from copy import deepcopy
import json

from .checks import check_events, length_band
from .length import choose_edits
from .longchapter import HARD, apply_fix, observations, review_value
from .longmemory import compact_state
from .longutil import ask, paragraph_edits
from .quality import EVENTS, anchor_events, require
from .storage import load, save
from .text import _paragraph_spans, apply_paragraph_edits, chinese_count, number_paragraphs, pack, prose


def generated(out, number, target):
    """Rebuild this chapter's accepted checkpoints without model requests."""
    text = prose((out / f'steps/ch{number}-draft.md').read_text())
    low, high = length_band(target)
    early = not low <= chinese_count(text) <= high

    def length_fix(body):
        old = out / f'steps/ch{number}-length.md'
        new = out / f'steps/ch{number}-length.json'
        if old.exists():
            return prose(old.read_text())
        return choose_edits(body, load(new)['edits'], target)[0] if new.exists() else body

    if early:
        text = length_fix(text)
    path = out / f'steps/ch{number}-review.json'
    review = review_value(load(path), text) if path.exists() else dict(edits=[], events=[])
    text = apply_paragraph_edits(text, review['edits'], {e['paragraph'] for e in review['edits']})
    events = review['events']
    path = out / f'steps/ch{number}-fix.json'
    if path.exists():
        fix = load(path)
        if 'event_updates' in fix:
            issues = check_events({}, events, text) + [dict(paragraph=e['paragraph']) for e in fix.get('edits', [])]
            text, events, _ = apply_calibration(dict(body=text, events=events, before={}, issues=issues), fix)
        else:
            text, events = apply_fix(text, events, review_value(fix, text))
    if not early:
        text = length_fix(text)
    events = anchor_events(dict(events=events), text)['events']
    return text, events


def apply_calibration(case, value):
    text, events = case['body'], deepcopy(case['events'])
    allowed = {i['paragraph'] for i in case['issues']}
    edits = paragraph_edits(value, text)['edits']
    require(all(e['paragraph'] in allowed and '\n\n' not in e['text'] for e in edits),
            '正文只能替换点名段落，不得新增段落或改其它段')
    text = apply_paragraph_edits(text, edits, allowed)
    repeated = {i['paragraph'] for i in case['issues'] if i.get('kind') == 'repeated_sentence'}
    spans = _paragraph_spans(case['body'])
    changed = {e['paragraph'] for e in edits if e['text'] != case['body'][spans[e['paragraph']-1][0]:spans[e['paragraph']-1][1]]}
    require(repeated <= changed, '点名重复短句必须同义改写，不能作为合法重复忽略')
    for update in value.get('event_updates', []):
        index, fields = update.get('index', update.get('event_index')), update.get('fields')
        require(type(index) is int and 0 <= index < len(events) and isinstance(fields, dict), '事件索引或字段无效')
        events[index].update(fields)
    rejected = []
    can_reject = {i['event_index'] for i in case['issues'] if i.get('kind') == 'ungrounded_event'}
    for item in value.get('rejects', []):
        index = item.get('index', item.get('event_index'))
        require(type(index) is int and 0 <= index < len(events) and bool(item.get('reason')), '撤销观察须有索引和正文理由')
        require(index in can_reject, '禁止撤销已落地事件；issues序号不是events索引，请改字段或正文')
        rejected.append(dict(index=index, event=events[index], reason=item['reason']))
    discarded = {x['index'] for x in rejected}
    events = [e for i, e in enumerate(events) if i not in discarded] + value.get('add_events', [])
    events = anchor_events(dict(events=events), text)['events']
    require(not any(i['kind'] == 'ungrounded_event' for i in check_events({}, events, text)),
            '校准后每条活动事件须准确落到最终正文，勿拼接引文')
    require(not any(i['kind'] in HARD for i in observations(case['before'], events, text, (), [])),
            '点名硬矛盾仍在，须改事件字段或点名段，不能撤销该事件')
    return text, events, rejected


def final_quality(client, book, rows, plan, out, banned, checker=None):
    """Read only flagged chapters; one logical model pass, format retry at most once."""
    cache = out / 'steps/final-context.json'
    check = checker or (lambda state, events, text, banned, previous, idx: observations(state, events, text, banned, previous))
    if cache.exists():
        cases = load(cache)
    else:
        cases, previous = [], []
        for row in rows:
            if row['status'] != 'complete':
                continue
            idx = row['number']
            text, events = generated(out, idx, book.words_per_chapter)
            actual = (out / f'chapter-{idx:03d}.body.md').read_text()
            require(text == actual, f'第{idx}章检查点与正文不一致')
            old = rows[idx-2]['state'] if idx > 1 else {}
            issues = check(old, events, text, banned, previous, idx)
            row['remaining'] = [dict(kind=i['kind'], paragraph=i['paragraph'], reason=i['suggestion']) for i in issues]
            row['hard_conflicts'] = sum(i['kind'] in HARD for i in issues)
            if issues:
                names = {e.get('name') for e in events}
                before = {k: {n:v for n,v in old.get(k, {}).items() if n in names}
                          for k in ('characters', 'objects', 'foreshadows')}
                before['clock'] = old.get('clock')
                cases.append(dict(number=idx, body=text, events=events, before=before,
                                  state=row['state'], summary=row['summary'], issues=issues))
            previous.append(text)
        if not cases:
            return rows
        save(cache, cases)
    compact = lambda v: json.dumps(v, ensure_ascii=False, separators=(',', ':'))
    selected, used = [], len(client.prefix) + len(compact(plan['canon'])) + 2200
    # Hard findings first; soft-only chapters may use local windows if needed.
    for case in sorted(cases, key=lambda c: (-any(i['kind'] in HARD for i in c['issues']), c['number'])):
        soft = all(i['kind'] == 'repeated_sentence' for i in case['issues'])
        body = number_paragraphs(case['body'])
        view = [{**e, 'index': i} for i, e in enumerate(case['events'])]
        if soft:
            spans = _paragraph_spans(case['body'])
            nearby = {n for issue in case['issues'] for n in range(max(1, issue['paragraph']-1),
                       min(len(spans), issue['paragraph']+1)+1)}
            body = '\n\n'.join(f'[P{n}] ' + case['body'][spans[n-1][0]:spans[n-1][1]] for n in sorted(nearby))
            view = [{k:v for k,v in e.items() if k in ('index', 'kind', 'name', 'paragraph', 'quote')}
                    for e in view if e.get('paragraph') in nearby]
        names = {e.get('name') for e in case['events']}
        current = {k: {n:v for n,v in case['state'].get(k, {}).items() if n in names}
                   for k in ('characters', 'objects', 'foreshadows')}
        current['clock'] = case['state'].get('clock')
        item = dict(chapter=case['number'], body=body,
                    events=view, before={} if soft else case['before'], current={} if soft else current,
                    issues=[dict(kind=i['kind'], paragraph=i['paragraph'], event_index=i.get('event_index'),
                                 reason=i['suggestion']) for i in case['issues']])
        size = len(compact(item))
        if used + size <= 29500:
            selected.append(item)
            used += size
    if not selected:
        return rows
    by_id = {c['number']:c for c in cases}
    selected_ids = {c['chapter'] for c in selected}

    def valid(value):
        require(isinstance(value.get('chapters'), list), '缺少chapters数组')
        require({c.get('chapter') for c in value['chapters']} == selected_ids, '须覆盖每个输入章号')
        for item in value['chapters']:
            text, events, _ = apply_calibration(by_id[item['chapter']], item)
            n = item['chapter']
            errors = check(rows[n-2]['state'] if n>1 else {}, events, text, banned, [], n)
            require(not any(i['kind'] in HARD | {'ungrounded_event'} for i in errors), '修后状态仍不一致：'+pack(errors)[:300])

    result = ask(client, 'final-consistency',
                 '最后一次校准已检出的局部问题，不是重写全书。逐条区分抽取误差与正文真矛盾。'
                 '字段误差只给event_updates，fields是应修改的字段，不复制其它字段。'
                 'index为输入events数组从0起的索引；quote只抄最终正文连续4—12字，不拼接。'
                 '同一地点/容器用原before状态规范短称；已有转场补add_events，不重复补写人物入场。'
                 '回忆/远程传音/投影不算人物本体重新入场，分别用flashback/remote标记。'
                 '真正文问题只改issues点名段落，每段完整替换，无空行；不加剧情，不改其它段。'
                 '短句重复可同义语境化，保留姓名、信息、声响次数及固定刻文。'
                 '所有点名重复都必须改写，不能说短句正常而忽略；不同处用不同自然表达。'
                 '不做字数修正，保留全部情节点、不可逆代价，保持原字数区间。'
                 'rejects只允许针对ungrounded_event对应的event_index，且原文完全不存在该事实；'
                 '已落地事件（即便发生硬冲突）不得撤销，重复问题也不能用rejects忽略。'
                 'issues的序号不是events的index；请使用事件表显式index。'
                 '不能靠撤销真实观察消除矛盾，不确定则保留。'
                 'state_delta只修真正错误的当前位置/是否在场/物品位置，不存引文段号。' + EVENTS,
                 [('问题章正文与事件（事件索引从0起）', compact(selected)),
                  ('固定设定', compact(plan['canon']))],
                 '{"chapters":[{"chapter":1,"edits":[{"paragraph":1,"text":""}],'
                 '"event_updates":[{"index":0,"fields":{"quote":"","location":""}}],'
                 '"add_events":[],"rejects":[],"state_delta":{},"reason":""}]}',
                 cap=12000, effort='medium', max_attempts=2, validate=valid)
    changes = {x['chapter']:x for x in result['chapters']}
    previous = []
    for row in rows:
        idx = row['number']
        if row['status'] != 'complete':
            continue
        if idx in changes:
            case, change = by_id[idx], changes[idx]
            text, events, rejected = apply_calibration(case, change)
            row['state'] = compact_state(change.get('state_delta', {}), case['state'])
            row['final_calibration'] = dict(reason=change.get('reason', ''), rejected=rejected)
            save(out / f'chapter-{idx:03d}.body.md', text)
        else:
            text, events = generated(out, idx, book.words_per_chapter)
        old = rows[idx-2]['state'] if idx > 1 else {}
        issues = check(old, events, text, banned, previous, idx)
        row['remaining'] = [dict(kind=i['kind'], paragraph=i['paragraph'], reason=i['suggestion']) for i in issues]
        row['hard_conflicts'] = sum(i['kind'] in HARD for i in issues)
        row['chars'] = chinese_count(text)
        row['within_band'] = length_band(book.words_per_chapter)[0] <= row['chars'] <= length_band(book.words_per_chapter)[1]
        save(out / f'chapters/{idx:03d}.json', row)
        save(out / f'chapter-{idx:03d}.state.json', row['state'])
        previous.append(text)
    return rows
