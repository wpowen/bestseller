"""S4 chapter drafting, combined review/edit, compact state and bounded failures."""
from tools.runtime_support import QuotaExhausted, ProjectError
from .checks import check_events, high_confidence, length_band, text_issues
from .longmemory import compact_state, extract_summary
from .length import correct_length
from .longutil import ask, memory_sections, paragraph_edits
from .quality import EVENTS, EVENT_SCHEMA, anchor_events, patched_records, require
from .storage import load, save
from .text import apply_paragraph_edits, chinese_count, number_paragraphs, pack, prose

REVIEW_SCHEMA = ('{"issues":[{"paragraph":1,"kind":"","reason":""}],'
                 '"edits":[{"paragraph":1,"text":""}],"events":[' + EVENT_SCHEMA + ']}')
STATE_SCHEMA = ('{"summary":"","characters":{"固定姓名":{"location":"","present":true}},'
                '"objects":{"关键物品":{"location":"","owner":""}},"clock":{"day":0,"minute":540},'
                '"foreshadows":{"固定伏笔名":"open"},"relationships":["甲与乙：一句当前关系"]}')
HARD = {'simultaneous_locations', 'dead_character', 'time_reversal'}


def state_result(value, previous):
    source = value.get('state', value)
    require(isinstance(source, dict), '状态须为对象')
    summary = extract_summary(value) or extract_summary(source)
    require(bool(summary), '缺少summary')
    keys = ('characters', 'who_is_where', 'people', 'objects', 'items', 'clock', 'time',
            'foreshadows', 'unresolved', 'relationships', 'relations')
    require(any(k in source for k in keys), '缺少状态字段，可用空对象表示无变化')
    for k in ('characters', 'people', 'who_is_where', 'objects', 'items'):
        if k in source:
            require(isinstance(source[k], (dict, list)), k + '须为对象或列表')
    return compact_state(source, previous), summary[:240]


def review_value(value, text, allowed=None):
    value = paragraph_edits(value, text)
    if allowed is not None:
        value['edits'] = [e for e in value['edits'] if e['paragraph'] in allowed]
    edited = apply_paragraph_edits(text, value['edits'], {e['paragraph'] for e in value['edits']})
    events = value.get('events', [])
    value['events'] = [e for e in events if isinstance(e, dict)] if isinstance(events, list) else []
    return anchor_events(value, edited)


def observations(state, events, text, banned, previous):
    view = dict(state, objects={n: dict(v, owner=v.get('owner', '')) for n, v in state.get('objects', {}).items()})
    return high_confidence(view, events, text) + [dict(i, hard=False) for i in text_issues(text, banned, previous)]


def apply_fix(text, events, fix):
    allowed = {e['paragraph'] for e in fix['edits']}
    final = apply_paragraph_edits(text, fix['edits'], allowed)
    patches = [dict(chapter=1, paragraph=e['paragraph'], replacement=e['text'],
                    events=[x for x in fix['events'] if x.get('paragraph') == e['paragraph']])
               for e in fix['edits']]
    merged = patched_records([dict(events=events)], patches)[0]['events']
    moves = [e for e in fix['events'] if e.get('paragraph') not in allowed
             and (e.get('action') in ('move', 'enter', 'leave') or e.get('kind') == 'time')
             and not any(i['kind'] == 'ungrounded_event' for i in check_events({}, [e], final))]
    key = lambda e: (e.get('paragraph'), e.get('kind'), e.get('name') if e.get('kind') != 'time' else None)
    replaced = {key(e) for e in moves}
    merged = [e for e in merged if key(e) not in replaced] + moves
    return final, anchor_events(dict(events=merged), final)['events']


def write_chapter(client, book, chapter, volume, canon, previous_end, state, summaries,
                  rolling, banned, previous_texts, out):
    idx = chapter['number']
    path = out / f'chapters/{idx:03d}.json'
    if path.exists():
        return load(path)
    name = f'ch{idx}'
    sections = memory_sections(chapter, previous_end, state, summaries, volume, canon, rolling)
    low, high = length_band(book.words_per_chapter)
    budget = (f'正文目标{book.words_per_chapter}汉字，必须{low}—{high}，理想2100—2300；标点不算。'
              '严格两场戏，每场约950—1050汉字；不要把场景规划中的背景再展开。'
              '完成选择与代价后立即收尾，不加第三场。'
              '短段落、具体动作、鲜明对话，禁止总结人生道理、复述前文、用大段背景凑字。')
    warnings = []
    reference = next((c['input_chars'] for c in client.calls if c['stage'] == 'ch10-draft'), None)
    input_limit = min(30000, int(reference * 1.25)) if reference and idx > 10 else 30000
    draft = prose(ask(client, name + '-draft', '写本章正文，不带书名章名、编号和说明。' + budget
                      + '场景切换明确谁到了哪里、带走什么。固定人物名、能力与代价不可改变。'
                      + '避免这些套话：' + pack(banned), sections, cap=8000,
                      validate=lambda v: require(chinese_count(v) >= book.words_per_chapter // 2),
                      max_attempts=2, input_limit=input_limit))
    count = chinese_count(draft)
    length_attempted = not low <= count <= high
    if not low <= count <= high:
        try:
            draft = correct_length(client, name + '-length', draft, chapter, book.words_per_chapter)
        except (QuotaExhausted, ProjectError):
            raise
        except (RuntimeError, ValueError) as exc:
            warnings.append(dict(stage='length', reason=str(exc)))
    save(out / f'chapter-{idx:03d}.draft.md', draft)
    program_text = text_issues(draft, banned, previous_texts)
    try:
        review = ask(client, name + '-review',
                     '合并审查与修改：检查声口、设定、人物进出场、物品流转、时间与字数。'
                     '只修确有问题的段落，edits中每段完整替换，其它正文不动，不扩剧情。'
                     '目标2200汉字，保持1870—2530，不增加不必要描写。'
                     'events只提取修改完成后正文的明确状态变化，通常4—8条，最多12条；'
                     '优先主要人物转场/关键物品/明确时钟，无变化不复制旧状态，伏笔由末尾状态表记。'
                     'events.quote只抄修后段落连续4—12字，不拼接、不补主语、不省略中间文字；'
                     '段落编号沿原文，地点沿用旧状态固定称呼。'
                     '不要把到达后写成stay，人物到新场景必须move；物品换位置也记move。' + EVENTS,
                     [('本章编号正文', number_paragraphs(draft)), ('程序点名问题', pack(program_text))]
                     + sections, REVIEW_SCHEMA, cap=8000, max_attempts=2,
                     normalize=lambda v: review_value(v, draft),
                     validate=lambda v: require(isinstance(v.get('edits'), list)))
    except (QuotaExhausted, ProjectError):
        raise
    except (RuntimeError, ValueError) as exc:
        warnings.append(dict(stage='review', reason=str(exc)))
        review = dict(edits=[], events=[])
    final = apply_paragraph_edits(draft, review['edits'], {e['paragraph'] for e in review['edits']})
    events = review['events']
    found = observations(state, events, final, banned, previous_texts)
    points = [i for i in found if i.get('hard')]
    detected = [dict(kind=i['kind'], paragraph=i['paragraph'], reason=i['suggestion']) for i in points]
    if points:
        allowed = {i['paragraph'] for i in points}

        def validate_fix(value):
            require({e['paragraph'] for e in value['edits']} == allowed,
                    '每个点名段落都须给出修后全文；无正文错误可保持原文并纠正events')
            fixed, claims = apply_fix(final, events, value)
            hard = [i for i in observations(state, claims, fixed, banned, previous_texts) if i['kind'] in HARD]
            require(not hard, '修后仍有硬冲突：' + pack([
                dict(paragraph=i['paragraph'], reason=i['suggestion']) for i in hard])[:400])

        try:
            fix = ask(client, name + '-fix',
                      '只改点名段落，其余逐字保留。若是状态抽取错误，正文不变，仅修events。'
                      'events给点名段落修后状态，以及解决问题必需的其它原段已有移动/到离场/跨日事件。'
                      '不重复无关的stay。若原文别段已有到场，补其事件即可，不得再次补写同一人物入场。'
                      'quote只抄修后原段连续4—12字，不补主语、不拼接两处文字。'
                      '跨午夜day加一，明确回忆加flashback=true；电话另一端人物remote=true。'
                      '已离场人物真正回场须补入场，人物换位置与物品转移须记move而非stay。' + EVENTS,
                      [('点名问题', pack(points)), ('本章编号正文', number_paragraphs(final))] + sections,
                      REVIEW_SCHEMA, cap=8000, max_attempts=2, validate=validate_fix,
                      normalize=lambda v: review_value(v, final, allowed))
            final, events = apply_fix(final, events, fix)
        except (QuotaExhausted, ProjectError):
            raise
        except (RuntimeError, ValueError) as exc:
            warnings.append(dict(stage='fix', reason=str(exc)))
    if not length_attempted and not low <= chinese_count(final) <= high:
        try:
            final = correct_length(client, name + '-length', final, chapter, book.words_per_chapter,
                                   {e['paragraph'] for e in events if type(e.get('paragraph')) is int})
            events = anchor_events(dict(events=events), final)['events']
        except (QuotaExhausted, ProjectError):
            raise
        except (RuntimeError, ValueError) as exc:
            warnings.append(dict(stage='length', reason=str(exc)))
    remaining = observations(state, events, final, banned, previous_texts)
    save(out / f'chapter-{idx:03d}.body.md', final)

    def valid_state(value):
        state_result(value, state)

    try:
        value = ask(client, name + '-state',
                    '读取最终正文，提取100—150汉字摘要及精简当前状态；不是审校，不要引文、段号或events。'
                    '人物只存所在地和是否在场，关键物品只存位置/持有人，时间为day/minute，'
                    'location只写地名或容器名，沿用已有短称；不要拼长路径，不附逗号后的伤势、外貌、动作。'
                    'owner仅指当前携带者，不是所有权；物留在固定地点时owner空串。'
                    '伤势、能力进展与代价写入summary，不塞进location。物品随载具移动，存移动后地点。'
                    '伏笔只存固定名称:open/closed；每段关系一句当前状态。只写发生变化的字段，旧事实自动合并。'
                    '人物与关键物品采用固定名称，不复制别称，不存普通杂物。只把确实回收的伏笔标closed。'
                    '跨午夜日数递增，回忆不改当前时间，电话另一端不能当回到现场。'
                    '严格按紧凑JSON结构输出，最多12个人、10件关键物、12条本章伏笔变化，关系最多8条。',
                    [('最终正文', final), ('旧状态', pack(state)[:6000]),
                     ('本章大纲', pack(chapter)), ('固定人物与物品', pack(canon)[:3200])],
                    STATE_SCHEMA, cap=4500, max_attempts=2, validate=valid_state,
                    cumulative_output_failures=True)
        state, summary = state_result(value, state)
    except (QuotaExhausted, ProjectError):
        raise
    except (RuntimeError, ValueError) as exc:
        warnings.append(dict(stage='state', reason=str(exc), fallback='previous'))
        summary = f'第{idx}章摘要提取失败，参照本章大纲：' + str(chapter.get('ending_hook', ''))
        state = compact_state(state)
    row = dict(number=idx, title=chapter['title'], status='complete', chars=chinese_count(final),
               within_band=low <= chinese_count(final) <= high, summary=summary, state=state,
               detected=detected, remaining=[dict(kind=i['kind'], paragraph=i['paragraph'],
               reason=i['suggestion']) for i in remaining], warnings=warnings,
               hard_conflicts=sum(i['kind'] in HARD for i in remaining))
    save(path, row)
    save(out / f'chapter-{idx:03d}.state.json', state)
    return row
