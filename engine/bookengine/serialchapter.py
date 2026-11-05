"""Two-call serial chapters: prose, then combined review and final state."""
from tools.runtime_support import QuotaExhausted, ProjectError
from .checks import high_confidence, length_band, text_issues, roll_day
from .length import correct_length
from .longchapter import apply_fix, review_value
from .longutil import ask, memory_sections
from .quality import anchor_events
from .storage import load, save
from .serialstate import restore_entities
from .longmemory import compact_state
from .longmemory import extract_summary
from .text import apply_paragraph_edits, chinese_count, number_paragraphs, pack, prose
import re


def paragraph_flow(text):
    """Keep every word and line; group short lines before numbering/review."""
    groups, pending = [], []
    for block in re.split(r'\n\s*\n', text.strip()):
        if block.strip() in ('---', '***'):
            if pending:
                groups.append('\n'.join(pending)); pending = []
            groups.append(block)
            continue
        pending.append(block)
        if chinese_count(''.join(pending)) >= 65:
            groups.append('\n'.join(pending)); pending = []
    if pending:
        groups.append('\n'.join(pending))
    return '\n\n'.join(groups)


def serial_observations(state, events, text, banned, previous, archive, retired=(), before=None):
    names = {e[k] for e in events if isinstance(e,dict) for k in ('name','owner') if isinstance(e.get(k),str)}
    view = restore_entities(state, archive, names, before=before)
    view['_dead'] = list(retired)
    return high_confidence(view, events, text) + [dict(i,hard=False) for i in text_issues(text,banned,previous)]


def write_serial(client, book, chapter, volume, canon, previous_end, state,
                 summaries, rolling, banned, previous, out, threads):
    idx = chapter['number']
    path = out / f'chapters/{idx:03d}.json'
    if path.exists():
        return load(path)
    name, warnings = f'ch{idx}', []
    archive = load(out / 'archive.json') if (out / 'archive.json').exists() else {}
    meta = load(out / 'serial-meta.json') if (out / 'serial-meta.json').exists() else {}
    inspect = lambda claims, body: serial_observations(state, claims, body, banned, previous,
                          archive, meta.get('dead_characters', ()), before=idx)
    sections = memory_sections(chapter, previous_end, state, summaries, volume, canon, rolling)
    sections.insert(3, ('伏笔固定编号与原义', pack(threads)))
    closed = sorted(((k,v) for k,v in meta.get('foreshadows', {}).items() if v['status']=='closed'),
                    key=lambda x:x[1]['updated_at'], reverse=True)[:6]
    sections.insert(4, ('最近已收伏笔，不能换名重开', pack({k:v['description'] for k,v in closed})[:600]))
    closing = idx * 10 > book.chapters * 9
    thread_rule = ('最后10%只收不开，结尾最多留2条续作钩子。' if closing else
                   '活跃伏笔目标≤6、上限12；已满先回收，不得新增谜团或换名重复开。')
    low, high = length_band(book.words_per_chapter)
    reference = next((c['input_chars'] for c in client.calls if c['stage'] == 'ch20-draft'), None)
    limit = min(30000, int(reference * 1.25)) if reference and idx > 20 else 30000
    text = prose(ask(client, name + '-draft',
        f'只写本章正文，目标{book.words_per_chapter}汉字，合格区间{low}—{high}，标点不算。'
        '控制在2000—2200汉字；严格两场戏，每场950—1100汉字，不加第三场。'
        '全文约45—55段，每段35—50汉字，对白可以并入动作段，不堆百余个单句碎段。'
        '完成本章选择、行动与后果即收尾，删重复解释，不用总结凑字。'
        '每次转场写清谁到了哪里、关键物品由谁带走；旧地点名称固定，不把地名拼成长路径。'
        '人物姓名、能力边界、代价固定；不加入章名、场景规划、编号或统计。'
        '冲突靠行动、人物选择和代价推进，常规流程一句带过，别把核单签字当高潮。'
        + thread_rule + '避免套话：' + pack(banned), sections, cap=6200, effort='low',
        max_attempts=2, input_limit=limit,
        normalize=None if (out / f'steps/{name}-draft.md').exists() else paragraph_flow))
    length_used = not low <= chinese_count(text) <= high
    if length_used:
        try:
            text = correct_length(client, name + '-length', text, chapter, book.words_per_chapter)
        except (QuotaExhausted, ProjectError):
            raise
        except (RuntimeError, ValueError) as exc:
            warnings.append(dict(stage='length', reason=str(exc)))
    save(out / f'chapter-{idx:03d}.draft.md', text)
    schema = ('{"edits":[],"summary":"","state":{"characters":{},"objects":{},'
              '"clock":null,"foreshadows":{},"relationships":[]},"events":[],"deaths":[]}')
    sampled = idx == 1 or idx % 10 == 0 or idx == book.chapters
    try:
        review = ask(client, name + '-review',
            ('简短审查情节和人物，确有问题才给段落替换。' if sampled else '直接摘录状态，不审文风，edits=[]。')
            + '冲突靠行动、人物选择和代价推进。摘要100字；状态只写变化，缺项可省略。'
            '人物所在地和在场、关键物品位置、当前时间、关系一句话。'
            '已有伏笔用原F编号，新开用中文短名；只把已回答的疑问标closed。'
            'events只写明确的时钟变化、同一时间异地出现和死亡/再出场；回忆flashback=true，预计projected=true。'
            'deaths只列已明确死亡的人物名字；不要把离场、失踪、受伤当死亡。'
            + thread_rule,
            [('本章正文', number_paragraphs(text))] + sections,
            schema, cap=6500, effort='low', max_attempts=1)
    except (QuotaExhausted, ProjectError):
        raise
    except (RuntimeError, ValueError, KeyError, TypeError) as exc:
        warnings.append(dict(stage='review_state', reason=str(exc), fallback='previous'))
        review = {}
    review = review if isinstance(review, dict) else {}
    try:
        review = review_value(review, text)
    except (ValueError, KeyError, TypeError, AttributeError):
        review = dict(edits=[], events=[])
    final = apply_paragraph_edits(text, review['edits'], {e['paragraph'] for e in review['edits']})
    events = review['events']
    found = inspect(events, final)
    if any(i.get('hard') for i in found):
        try:
            fix = ask(client, name + '-fix',
                '仅修改点名段落解决同时异地、死人再出场或时间倒流，其他正文不动。'
                '冲突靠行动、人物选择和代价推进，不增加文书流程。无需证明或引文。',
                [('点名问题', pack([i for i in found if i.get('hard')])),
                 ('本章正文', number_paragraphs(final))],
                '{"edits":[],"events":[],"state":{}}', cap=5000, max_attempts=1)
            fix = review_value(fix if isinstance(fix,dict) else {}, final,
                               {i['paragraph'] for i in found if i.get('hard')})
            final, events = apply_fix(final, events, fix)
        except (QuotaExhausted, ProjectError):
            raise
        except (RuntimeError, ValueError, KeyError, TypeError):
            warnings.append(dict(stage='fix', fallback='keep_text'))
    if not length_used and not low <= chinese_count(final) <= high:
        try:
            final = correct_length(client, name + '-length', final, chapter, book.words_per_chapter,
                                   {e['paragraph'] for e in events if type(e.get('paragraph')) is int})
            events = anchor_events(dict(events=events), final)['events']
        except (QuotaExhausted, ProjectError):
            raise
        except (RuntimeError, ValueError) as exc:
            warnings.append(dict(stage='length', reason=str(exc)))
    raw_state = review.get('state', review)
    raw_state = raw_state if isinstance(raw_state,dict) else {}
    current = compact_state(raw_state, state)
    current['clock'] = roll_day(state.get('clock'),current.get('clock'),final[-1200:],final)
    current['foreshadows'] = raw_state.get('foreshadows', {})
    summary = extract_summary(review) or str(chapter.get('ending_hook', ''))[:200]
    remaining = inspect(events, final)
    save(out / f'chapter-{idx:03d}.body.md', final)
    row = dict(number=idx, title=chapter['title'], status='complete', chars=chinese_count(final),
        within_band=low <= chinese_count(final) <= high, summary=summary, state=current,
        detected=[dict(kind=i['kind'], paragraph=i['paragraph'], reason=i['suggestion']) for i in found],
        remaining=[dict(kind=i['kind'], paragraph=i['paragraph'], reason=i['suggestion']) for i in remaining],
        warnings=warnings, hard_conflicts=sum(i.get('hard', False) for i in remaining),
        retired_objects=[n for n in review.get('retired_objects',[]) if isinstance(n,str)] if isinstance(review.get('retired_objects'),list) else [],
        deaths=[n for n in review.get('deaths',[]) if isinstance(n,str)] if isinstance(review.get('deaths'),list) else [],
        review_sampled=sampled,
        seen=[n for n,v in compact_state(raw_state)['characters'].items() if v.get('present') and n in final])
    save(path, row)
    return row
