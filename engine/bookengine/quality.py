"""Bounded S3 chapter writing, one length correction, and paragraph-local repair."""
from copy import deepcopy
from pathlib import Path
import yaml
import re

from .checks import advance_state, check_events, event_structure_valid, length_band, text_issues
from .client import SYSTEM
from .config import style_rules
from .context import MAX_CONTEXT_CHARS, pack_context
from .longmemory import place_only
from .storage import load, save
from .text import (_paragraph_spans, apply_paragraph_edits, chinese_count,
                   normalize_review, number_paragraphs, pack, prose)

HARD = {'absent_character', 'character_location', 'object_locations', 'time_reversal'}
EVENTS = yaml.safe_load((Path(__file__).resolve().parents[1] / 'data/quality_fields.yaml').read_text())['events_prompt']
EVENT_SCHEMA = ('{"kind":"character","name":"","location":"","action":"stay","paragraph":1,"quote":""},'
                '{"kind":"object","name":"","location":"","owner":"","action":"move","paragraph":1,"quote":""},'
                '{"kind":"time","day":0,"minute":540,"flashback":false,"paragraph":1,"quote":""},'
                '{"kind":"foreshadow","name":"","status":"open","paragraph":1,"quote":""}')
EXTRACT_SCHEMA = '{"summary":"","events":[' + EVENT_SCHEMA + '],"relationships":[]}'
REVIEW_SCHEMA = '{"issues":[{"paragraph":1,"suggestion":""}],"events":[' + EVENT_SCHEMA + ']}'
PATCH_SCHEMA = ('{"findings":[],"patches":[{"chapter":1,"paragraph":1,"replacement":"",'
                '"reason":"","events":[]}]}')


def require(condition, reason='Invalid S3 output structure'):
    if not condition:
        raise ValueError(reason)


def validate_events(value):
    require(isinstance(value.get('events'), list))
    require(all(isinstance(e, dict) and type(e.get('paragraph')) is int
                and isinstance(e.get('quote'), str) for e in value['events']))


def validate_extract(value):
    validate_events(value)
    require(isinstance(value.get('summary'), str) and bool(value['summary'].strip()))
    require(isinstance(value.get('relationships'), list))


def _inserted_quote(needle, paragraph, original):
    matches = []
    for start, char in enumerate(paragraph):
        if char != needle[0]:
            continue
        end = start
        for char in needle[1:]:
            end = paragraph.find(char, end+1, min(len(paragraph), end+10))
            if end < 0:
                break
        if end >= 0 and end-start+1 <= len(needle)+16:
            context = paragraph[max(0,start-2):end+1]
            if not any(c in context and c not in original for c in '不没未无否'):
                matches.append(paragraph[start:end+1])
    return matches[0] if len(matches)==1 else None


def anchor_events(value, text):
    paragraphs = [text[a:b] for a, b in _paragraph_spans(text)]
    events = []
    relationship_events = list(value.get('relationship_events', []))
    for event in value.get('events', []):
        item = dict(event) if isinstance(event, dict) else event
        if isinstance(item, dict):
            if 'kind' not in item and item.get('type') in ('character', 'object', 'time', 'foreshadow'):
                item['kind'] = item['type']
            if item.get('kind', item.get('type')) == 'relationship' and isinstance(item.get('name'),str) and item['name'] in str(value.get('state',{}).get('relationships', [])):
                relationship_events.append(item)
                continue
            if item.get('kind') in ('character', 'object') and isinstance(item.get('location'), str):
                item['location'] = place_only(item['location'], item.get('owner', ''))
            if item.get('kind') == 'object':
                item.setdefault('owner', '')
                if item.get('action') in ('enter', 'appear', 'reveal'):
                    item['action'] = 'move'
            if item.get('kind') == 'foreshadow' and 'status' not in item:
                if item.get('action') in ('open', 'closed'):
                    item['status'] = item['action']
            if item.get('kind') == 'foreshadow' and isinstance(item.get('name'),str):
                named = re.fullmatch(r'(F\d{3,})\s*[:：]\s*(.+)', item['name'])
                if named:
                    item['name'], item['description'] = named.groups()
            for key in ('day', 'minute'):
                if isinstance(item.get(key), str) and item[key].isdigit():
                    item[key] = int(item[key])
        if isinstance(item, dict) and isinstance(item.get('quote'), str) and item['quote']:
            name = item.get('name')
            if item.get('kind') == 'character' and isinstance(name, str) and name and (
                    name + '的电话' in item['quote'] or '电话那头的' + name in item['quote']):
                item['remote'] = True
            matches = [i for i, p in enumerate(paragraphs, 1) if item['quote'] in p]
            if len(matches) == 1:
                item['paragraph'] = matches[0]
            elif not matches:
                needle = ''.join(c for c in item['quote'] if c.isalnum())
                found = []
                if len(needle) >= 6:
                    for n, p in enumerate(paragraphs, 1):
                        positions = [i for i, c in enumerate(p) if c.isalnum()]
                        plain = ''.join(p[i] for i in positions)
                        start = plain.find(needle)
                        if start >= 0 and plain.find(needle, start + 1) < 0:
                            found.append((n, p[positions[start]:positions[start + len(needle) - 1] + 1]))
                if len(found) == 1:
                    item['paragraph'], item['quote'] = found[0]
                elif len(needle) >= 8 and type(item.get('paragraph')) is int and 1 <= item['paragraph'] <= len(paragraphs):
                    paragraph = paragraphs[item['paragraph']-1]
                    literal = _inserted_quote(needle, paragraph, item['quote'])
                    if literal:
                        item['quote'] = literal
        events.append(item)
    return dict(value, events=events, **({'relationship_events':relationship_events} if relationship_events else {}))


def pointed_edits(value, allowed):
    edits = value.get('edits')
    if not isinstance(edits, list):
        return value
    return dict(value, edits=[e for e in edits if not (isinstance(e, dict)
                and type(e.get('paragraph')) is int and e['paragraph'] not in allowed)])


def prepare_state_retry(client, name, text):
    path = client.out / 'steps' / (name + '.json')
    if not path.exists():
        return
    cached = anchor_events(load(path), text)
    if not event_structure_valid(cached['events'], text):
        target = client.out / 'invalid-states' / f'{name}-{len(client.calls)}.json'
        target.parent.mkdir(parents=True, exist_ok=True)
        path.replace(target)
        for call in reversed(client.calls):
            if call['stage'] == name:
                call.setdefault('previous_status', call['status'])
                call['status'] = 'invalid_stage_output'
                break
        save(client.history_path, client.calls)


def ask(client, name, instruction, required, optional=None, schema=None, **kwargs):
    overhead = len(client.prefix) + len(SYSTEM) + len(instruction) + len(schema or '') + 400
    context = pack_context(required, optional or {}, MAX_CONTEXT_CHARS - overhead)
    return client.stage(name, instruction + '\n' + context, schema, **kwargs)


def context_parts(bible, outline, chapter, state):
    return {'故事圣经': pack(bible), '全部细纲': pack(outline),
            '当前已发生状态': pack(state), '本章细纲': pack(chapter)}


def _paragraph_issues(review, program, count):
    merged = {}
    for issue in review + [x for x in program if x['kind'] != 'ungrounded_event']:
        number = issue.get('paragraph')
        if type(number) is int and 1 <= number <= count:
            merged.setdefault(number, []).append(issue['suggestion'])
    return [dict(paragraph=n, suggestion='；'.join(dict.fromkeys(items)))
            for n, items in sorted(merged.items())]


def write_chapters(client, book, bible, outline, out, assets):
    banned = style_rules(assets)
    chapters, records, summaries, state = [], [], [], {}
    low, high = length_band(book.words_per_chapter)
    for idx, chapter in enumerate(outline['chapters'], 1):
        saved_text = out / f'chapter-{idx:03d}.draft.md'
        saved_state = out / f'steps/ch{idx}-state.json'
        if saved_text.exists() and saved_state.exists():
            final = saved_text.read_text()
            extraction = anchor_events(load(saved_state), final)
            if event_structure_valid(extraction.get('events'), final):
                validate_extract(extraction)
                chapters.append(final)
                records.append(extraction)
                state = advance_state(state, extraction['events'], final)
                state['relationships'] = extraction['relationships']
                summaries.append(dict(chapter=idx, summary=extraction['summary']))
                continue
        required = context_parts(bible, outline, chapter, state)
        optional = {'前文摘要': pack(summaries[-8:]), '上一章正文': chapters[-1] if chapters else '无'}
        budget = (f'正文汉字必须{low}—{high}，目标{book.words_per_chapter}；标点空白不计。'
                  '严格2—3场戏：三场各约650—750汉字，两场各约950—1100汉字。'
                  '不写场景标题、书名、章名和字数说明，不重复前章；完成本章细纲全部情节点。')
        raw = ask(client, f'ch{idx}-draft', '写本章完整正文。' + budget
                  + '\n避免这些固定套话：' + pack(banned), required, optional,
                  cap=12000, effort='medium',
                  validate=lambda v: require(bool(v.strip())))
        draft = prose(raw)

        def validate_review(value):
            validate_events(value)
            require(isinstance(value.get('issues'), list))
            for issue in value['issues']:
                require(isinstance(issue, dict) and type(issue.get('paragraph')) is int
                        and 1 <= issue['paragraph'] <= len(_paragraph_spans(draft))
                        and isinstance(issue.get('suggestion'), str))

        review = ask(client, f'ch{idx}-review', '审校连续性、声口、情绪与节奏。只列真实问题，'
                     '用段落号定位，每条意见仅改该段即可解决，无问题则issues=[]。'
                     '同时提取本章当前状态事件，供程序核对。' + EVENTS,
                     dict(required, 本章编号正文=number_paragraphs(draft)), optional,
                     REVIEW_SCHEMA, cap=12000, validate=validate_review,
                     normalize=lambda v: anchor_events(normalize_review(v), draft))
        program = text_issues(draft, banned, chapters) + check_events(state, review['events'], draft)
        issues = _paragraph_issues(review['issues'], program, len(_paragraph_spans(draft)))
        final = draft
        if issues:
            allowed = {x['paragraph'] for x in issues}

            def validate_edits(value):
                require(isinstance(value.get('edits'), list)
                        and all(isinstance(x, dict) for x in value['edits']))
                require({x.get('paragraph') for x in value['edits']} == allowed)
                apply_paragraph_edits(draft, value['edits'], allowed)

            edits = ask(client, f'ch{idx}-rewrite', '只改点名段落，每段一次，保留其它段落逐字不动。'
                        '不加情节，明确修好人物到离场、物品移动和时间衔接。' + budget,
                        {'本章编号正文': number_paragraphs(draft), '修改意见': pack(issues),
                         '已发生状态': pack(state)}, schema='{"edits":[{"paragraph":1,"text":""}]}',
                        cap=12000, validate=validate_edits, normalize=lambda v: pointed_edits(v, allowed))
            final = apply_paragraph_edits(draft, edits['edits'], allowed)
        count = chinese_count(final)
        if not low <= count <= high:
            action = '压缩' if count > high else '扩写'
            change = '保留全部情节点、人物选择、状态转移与结局，只删赘述重复。' if count > high else '只加动作、对话、环境细节，不增情节、不增人物和物品、不改变时间。'
            final = prose(ask(client, f'ch{idx}-length',
                          f'当前正文实测{count}汉字。{action}到{book.words_per_chapter}汉字，'
                          f'需{action}{abs(count-book.words_per_chapter)}汉字，约调整{abs(count-book.words_per_chapter)*100/max(count,1):.0f}%篇幅。'
                          f'必须落在{low}—{high}汉字。' + change
                          + '直接给出全部正文，无标题。此次为唯一字数修正。',
                          {'本章正文': final, '必须保留的本章情节点': pack(chapter)}, cap=12000,
                          validate=lambda v: require(bool(v.strip()))))
        save(out / f'chapter-{idx:03d}.draft.md', final)
        prepare_state_retry(client, f'ch{idx}-state', final)

        def validate_final_state(value):
            validate_extract(value)
            require(event_structure_valid(value['events'], final))

        extraction = ask(client, f'ch{idx}-state', '提取最终正文的简短摘要（180字内）、状态事件及当前关系。'
                         '列出主要人物的到离场/地点、重要物品的移动、明确时间和伏笔开收，不编造。'
                         '每章通常8—18条事件，所有引文必须来自最终正文。' + EVENTS,
                         {'旧状态': pack(state), '最终编号正文': number_paragraphs(final)},
                         schema=EXTRACT_SCHEMA, cap=12000, validate=validate_final_state,
                         normalize=lambda v: anchor_events(v, final))
        chapters.append(final)
        records.append(extraction)
        state = advance_state(state, extraction['events'], final)
        state['relationships'] = extraction['relationships']
        save(out / f'chapter-{idx:03d}.state.json', state)
        summaries.append(dict(chapter=idx, summary=extraction['summary']))
    return chapters, records, banned


def validate_patches(value, chapters):
    from .text import apply_patches
    require(isinstance(value.get('patches'), list))
    apply_patches(chapters, value['patches'])
    for patch in value['patches']:
        require(type(patch.get('paragraph')) is int)
        require(not re.search(r'\n\s*\n', patch['replacement']))
        validate_events(patch)


def normalize_patches(value):
    patches = []
    for patch in value.get('patches', []):
        if not isinstance(patch, dict):
            patches.append(patch)
            continue
        item = dict(patch, events=[dict(e, paragraph=e.get('paragraph', 1))
                    if isinstance(e, dict) else e for e in patch.get('events', [])])
        patches.append(anchor_events(item, item.get('replacement', '')))
    return dict(value, patches=patches)


def patched_records(records, patches):
    updated = []
    for idx, record in enumerate(records, 1):
        chapter_patches = {p['paragraph']: p for p in patches if p['chapter'] == idx}
        deleted = sorted(n for n, p in chapter_patches.items() if not p['replacement'])
        events = []
        for event in record['events']:
            original_number = event['paragraph']
            if original_number in chapter_patches:
                patch = chapter_patches[original_number]
                key = lambda e: (e.get('kind'), e.get('name') if e.get('kind') != 'time' else None)
                if not patch['replacement'] or key(event) in {key(e) for e in patch['events']}:
                    continue
            item = dict(event, paragraph=original_number - sum(n < original_number for n in deleted))
            events.append(item)
        for number, patch in chapter_patches.items():
            for event in patch['events']:
                events.append(dict(event, paragraph=number - sum(n < number for n in deleted)))
        updated.append(dict(record, events=events))
    return updated


def check_book(chapters, records, patches, banned, target, out):
    """Replay final prose with all surviving observations and explicit moves."""
    state, previous, reports = {}, [], []
    low, high = length_band(target)
    for idx, (text, record) in enumerate(zip(chapters, patched_records(records, patches)), 1):
        events = record['events']
        issues = text_issues(text, banned, previous) + check_events(state, events, text)
        state = advance_state(state, events, text)
        state['relationships'] = record['relationships']
        chars = chinese_count(text)
        reports.append(dict(chapter=idx, chars=chars, within_band=low <= chars <= high,
                            issues=issues, state=deepcopy(state)))
        save(out / f'chapter-{idx:03d}.state.json', state)
        previous.append(text)
    save(out / 'state.json', state)
    return dict(chapters=reports, word_band=[low, high], context_limit_chars=MAX_CONTEXT_CHARS,
                hard_conflicts=sum(i['kind'] in HARD for r in reports for i in r['issues']),
                repeated_sentences=sum(i['kind'] == 'repeated_sentence' for r in reports for i in r['issues']),
                banned_phrases=sum(i['kind'] == 'banned_phrase' for r in reports for i in r['issues']),
                ungrounded_events=sum(i['kind'] == 'ungrounded_event' for r in reports for i in r['issues']),
                length_outliers=[r['chapter'] for r in reports if not r['within_band']])
