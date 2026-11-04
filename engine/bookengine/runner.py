"""S1 workflow: plan, write, review named paragraphs, summarize, read through."""
import datetime as dt
import fcntl
from pathlib import Path
import re

from .client import Client, MODEL
from .config import Book, knowledge
from .export import export
from .storage import load, now, save
from .text import (apply_paragraph_edits, apply_patches, chinese_count,
                   normalize_review, number_paragraphs, pack, prose)


def require(condition):
    if not condition:
        raise ValueError('Invalid stage structure')


def validate_review(value, text):
    require(isinstance(value.get('issues'), list))
    count = len(re.split(r'\n\s*\n', text.strip()))
    for issue in value['issues']:
        require(isinstance(issue, dict))
        require(type(issue.get('paragraph')) is int and 1 <= issue['paragraph'] <= count)
        require(isinstance(issue.get('suggestion'), str) and bool(issue['suggestion'].strip()))


def validate_state(value):
    require(isinstance(value.get('summary'), str) and bool(value['summary'].strip()))
    require(all(isinstance(value.get(key), list) for key in
                ('who_is_where', 'objects', 'relationships', 'unresolved')))
    require(isinstance(value.get('time'), str) and bool(value['time'].strip()))


def validate_bible(value):
    require(isinstance(value.get('characters'), list) and bool(value['characters']))
    require(bool(value.get('places')))
    for character in value['characters']:
        require(isinstance(character, dict))
        require(all(isinstance(character.get(k), str) and bool(character[k].strip())
                    for k in ('name', 'wants', 'speech')))


def run(book_path, out, env, assets, timeout=180, quality=False):
    if Book.read(book_path).chapters > 10:
        raise ValueError('Use --long for more than 10 chapters')
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (out / 'runner.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return run_locked(Book.read(book_path), out, env, assets, timeout, quality=quality)


def run_locked(book, out, env, assets, timeout, quality=False):
    prefix = knowledge(book, assets)
    manifest = out / 'run.json'
    if not manifest.exists():
        save(manifest, dict(started=now(), status='running', model=MODEL,
                            book=book.record(), knowledge=prefix, quality_mode=3 if quality else 2))
    meta = load(manifest)
    if Book(**meta['book']).record() != book.record() or meta['knowledge'] != prefix:
        raise ValueError('Book YAML or writing knowledge changed; use a new output folder')
    if quality and meta.get('quality_mode') != 3:
        raise ValueError('S3 requires its own output directory')
    if meta.get('status') == 'complete' and all((out / n).exists() for n in ('book.md', 'book.docx')):
        return meta
    client = Client(out, env, prefix, timeout)
    if quality:
        from .quality import write_chapters, ask, check_book, validate_patches, normalize_patches, PATCH_SCHEMA, EVENTS
        from .context import MAX_CONTEXT_CHARS
        client.context_limit = MAX_CONTEXT_CHARS
    stage = client.stage
    ideas = stage('01-ideas', '提出3个不同的短篇方案，写清人物欲望、冲突、关键选择与结局。',
                  '{"ideas":[{"title":"","premise":"","conflict":"","ending":""}]}',
                  validate=lambda v: require(isinstance(v.get('ideas'), list) and len(v['ideas']) == 3
                      and all(isinstance(x, dict) and all(isinstance(x.get(k), str) and x[k].strip()
                              for k in ('title', 'premise', 'conflict', 'ending')) for x in v['ideas'])))
    choice = stage('02-selection', '选择最具体、最好看且符合读者承诺的一案。\n' + pack(ideas),
                   '{"selected_index":0,"reason":""}',
                   validate=lambda v: require(type(v.get('selected_index')) is int
                                               and 0 <= v['selected_index'] < 3))
    selected = ideas['ideas'][choice['selected_index']]
    require(isinstance(selected.get('title'), str) and bool(selected['title'].strip()))
    bible = stage('03-bible', '为选中方案写简明故事圣经，每个人必须有愿望和独特的说话方式。\n' + pack(selected),
                  '{"characters":[{"name":"","wants":"","worry":"","speech":""}],'
                  '"places":[],"relationships":[],"facts":[]}', validate=validate_bible)
    outline = stage('04-outline', f'写完全部{book.chapters}章细纲，每章约{book.words_per_chapter}汉字。'
                    '每章列2—3场戏，每场仅50—80字，交代人物选择、后果与情绪变化；'
                    '全部细纲不超过1800字，不写正文；最后一章完成结局。\n'
                    + pack(selected) + '\n圣经\n' + pack(bible),
                    '{"chapters":[{"number":1,"title":"","reader_feeling":"",'
                    '"scenes":[{"where":"","characters":[],"action":"",'
                    '"choice":"","consequence":""}],"ending_hook":""}]}',
                    validate=lambda v: require(isinstance(v.get('chapters'), list)
                        and len(v['chapters']) == book.chapters
                        and all(isinstance(x, dict) for x in v['chapters'])
                        and [x.get('number') for x in v['chapters']] == list(range(1, book.chapters + 1))
                        and all(x.get('title') and x.get('scenes') for x in v['chapters'])))
    if quality:
        chapters, records, banned = write_chapters(client, book, bible, outline, out, assets)
    else:
        chapters, summaries, state = [], [], {}
        for idx, chapter in enumerate(outline['chapters'], 1):
            context = ('圣经\n' + pack(bible) + '\n全部细纲\n' + pack(outline)
                       + '\n各章摘要\n' + pack(summaries) + '\n当前状态\n' + pack(state)
                       + '\n上一章正文\n' + (chapters[-1] if chapters else '无')
                       + '\n本章细纲\n' + pack(chapter))
            raw = stage(f'ch{idx}-draft', f'写本章完整正文，目标{book.words_per_chapter}汉字。'
                        '以空行分段，不带标题、解释或字数说明。按细纲完成本章的选择与后果。\n' + context,
                        cap=16000, effort='medium',
                        validate=lambda v: require(chinese_count(v) >= book.words_per_chapter * .5))
            draft = prose(raw)
            review = stage(f'ch{idx}-review', '审校连续性、人物声口、情感与节奏。只报确实需要修改的问题，'
                           '不要为了提意见而提意见。用段落编号点名待改段落；每条建议必须能仅修改该段完成。'
                           '不要要求整章重写，无问题则issues为空。\n' + context
                           + '\n本章编号正文\n' + number_paragraphs(draft),
                           '{"issues":[{"paragraph":1,"suggestion":""}],"strengths":[]}',
                           validate=lambda v: validate_review(v, draft), cap=10000, normalize=normalize_review)
            final = draft
            if review['issues']:
                allowed = {x['paragraph'] for x in review['issues']}

                def check_edits(value):
                    require(isinstance(value.get('edits'), list))
                    require(all(isinstance(x, dict) for x in value['edits']))
                    require({x.get('paragraph') for x in value['edits']} == allowed)
                    apply_paragraph_edits(draft, value['edits'], allowed)

                edits = stage(f'ch{idx}-rewrite', '仅改审校点名的段落，每个被点名段落返回一次替换正文。'
                              '每个text只含该段完整正文，不带编号。保留其它段落，不新增情节。\n'
                              + context + '\n正文\n' + number_paragraphs(draft)
                              + '\n意见\n' + pack(review),
                              '{"edits":[{"paragraph":1,"text":""}]}', validate=check_edits, cap=12000)
                final = apply_paragraph_edits(draft, edits['edits'], allowed)
            chapters.append(final)
            save(out / f'chapter-{idx:03d}.draft.md', final)
            state = stage(f'ch{idx}-state', '一次提取本章摘要与最新状态；summary不超过180字。'
                          '只记正文发生的事实，保留有效旧状态；每类表最多10条，只记最新位置与变化。'
                          '不增设字段，不重复记录；检查所有数组与对象正确闭合。'
                          '\n旧状态\n' + pack(state) + '\n本章正文\n' + final,
                          '{"summary":"","who_is_where":[],"objects":[],"time":"",'
                          '"relationships":[],"unresolved":[]}', validate=validate_state, cap=10000)
            summaries.append(dict(chapter=idx, summary=state['summary']))
    def check_continuity(value):
        require(isinstance(value.get('patches'), list))
        apply_patches(chapters, value['patches'])

    if quality:
        from .checks import length_band
        low, high = length_band(book.words_per_chapter)
        initial = check_book(chapters, records, [], banned, book.words_per_chapter, out)
        hints = [{'chapter': r['chapter'], 'issues': [
            dict(paragraph=i['paragraph'], kind=i['kind'],
                 suggestion=i['suggestion'].partition('」：')[2] or i['suggestion'])
            for i in r['issues'] if i['kind'] != 'ungrounded_event']}
            for r in initial['chapters'] if r['issues']]
        continuity = ask(client, '05-continuity', '通读全书，优先修程序指出的硬矛盾、重复与禁用表达。'
                         f'只改有问题的段落，不润色其它段落，不增剧情；保持每章{low}—{high}汉字。'
                         'patch用chapter章号和paragraph编号，replacement只给一段正文（不可含空行）；'
                         '删除填空串。每段最多一个patch。patch.events仅提取替换段落的状态事件，'
                         '没有状态事件或删除段落填[]；引文来自replacement。' + EVENTS,
                         {'圣经': pack(bible), '程序问题': pack(hints),
                          '全书编号正文': '\n\n'.join(f'第{i}章\n{number_paragraphs(t)}' for i, t in enumerate(chapters, 1))},
                         schema=PATCH_SCHEMA, cap=16000,
                         validate=lambda v: validate_patches(v, chapters), normalize=normalize_patches)
    else:
        continuity = stage('05-continuity', '通读全书，只修真正的连续性矛盾，不润色其它段落。'
                           '每个patch用chapter章号和paragraph段落编号定位，replacement为该段修复全文。'
                           '重复段落用编号区分；要删除的段落replacement填空串。同一段只修一次，不整章替换。'
                           '无矛盾则patches为空。\n圣经\n' + pack(bible)
                           + '\n全书\n' + '\n\n'.join(f'第{i}章\n{number_paragraphs(t)}' for i, t in enumerate(chapters, 1)),
                           '{"findings":[],"patches":[{"chapter":1,"paragraph":1,'
                           '"replacement":"","reason":""}]}', validate=check_continuity, cap=14000)
    final_chapters = apply_patches(chapters, continuity['patches'])
    if quality:
        from .quality import patched_records
        from .repair import fix_program
        final_chapters, _, meta['quality'] = fix_program(client, book, final_chapters,
            patched_records(records, continuity['patches']), banned, out)
    export(out, selected['title'], outline['chapters'], final_chapters)
    meta.update(finished=now(), status='complete', totals=client.totals(),
                chapter_chinese_chars=[chinese_count(t) for t in final_chapters],
                continuity_patches=len(continuity['patches']))
    meta['wall_seconds'] = (dt.datetime.fromisoformat(meta['finished'])
                           - dt.datetime.fromisoformat(meta['started'])).total_seconds()
    save(manifest, meta)
    print(pack({k: meta[k] for k in ('status', 'totals', 'wall_seconds', 'chapter_chinese_chars')}), flush=True)
    return meta
