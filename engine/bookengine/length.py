"""One proposal: atomic full expansion or independent edits by actual counts."""
from .checks import length_band
from .longutil import ask
from .text import _paragraph_spans, _replace_spans, chinese_count, pack


def normalize_candidates(value, text, protected=()):
    value = value if isinstance(value, dict) else {}
    spans = _paragraph_spans(text)
    source = value.get('edits', value.get('patches', value.get('replacements', value if any(str(k).strip('[]').lstrip('Pp').isdigit() for k in value) else [])))
    if isinstance(source, dict):
        source = [dict(paragraph=n, text=t) for n, t in source.items()]
    result, seen = [], set()
    for item in source if isinstance(source, list) else []:
        if not isinstance(item, dict):
            continue
        number = item.get('paragraph', item.get('paragraph_id',item.get('id')))
        if isinstance(number, str) and number.strip('[]').lstrip('Pp').isdigit():
            number = int(number.strip('[]').lstrip('Pp'))
        replacement = item.get('text', item.get('replacement'))
        if (type(number) is int and 1 <= number <= len(spans) and number not in seen and number not in protected
                and isinstance(replacement, str)):
            result.append(dict(paragraph=number, text=replacement.strip()))
            seen.add(number)
    for number in value.get('optional_deletions', []) if isinstance(value.get('optional_deletions'), list) else []:
        if isinstance(number, str) and number.strip('[]').lstrip('Pp').isdigit():
            number = int(number.strip('[]').lstrip('Pp'))
        if type(number) is int and 1 <= number <= len(spans) and number not in protected:
            result = [e for e in result if e['paragraph'] != number]
            result.append(dict(paragraph=number, text=''))
    return dict(edits=result)


def normalize_expansion(value, text, target):
    """A full expansion is atomic; never salvage only some returned paragraphs."""
    paragraphs = value.get('paragraphs') if isinstance(value, dict) else None
    if not (isinstance(paragraphs, list) and paragraphs
            and all(isinstance(p, str) and p.strip() for p in paragraphs)):
        return dict(paragraphs=[])
    paragraphs = [p.strip() for p in paragraphs]
    count = chinese_count('\n\n'.join(paragraphs))
    improved=count>0 and abs(count-target)<abs(chinese_count(text)-target)
    return dict(paragraphs=paragraphs if improved else [])


def choose_edits(text, edits, target):
    count, spans = chinese_count(text), _paragraph_spans(text)
    low, high = length_band(target)
    if low <= count <= high:
        return text, []
    direction = -1 if count > high else 1
    goal = abs(count - target)
    ceiling = count - low if direction < 0 else high - count
    candidates = []
    for edit in normalize_candidates({'edits': edits}, text)['edits']:
        start, end = spans[edit['paragraph'] - 1]
        delta = direction * (chinese_count(edit['text']) - chinese_count(text[start:end]))
        if delta > 0:
            candidates.append((delta, edit))
    choices = {0: ()}
    for idx, (delta, _) in enumerate(candidates):
        for total, selected in list(choices.items()):
            if total + delta <= ceiling and total + delta not in choices:
                choices[total + delta] = selected + (idx,)
    best = min(choices, key=lambda total: abs(total - goal))
    selected = [candidates[i][1] for i in choices[best]]
    replacements = [(*spans[e['paragraph'] - 1], e['text']) for e in selected]
    return (_replace_spans(text, replacements).strip() if selected else text), selected


def correct_length(client, name, text, chapter, target, protected=()):
    legacy = client.out / 'steps' / (name + '.md')
    if legacy.exists():
        return legacy.read_text().strip()
    count, spans = chinese_count(text), _paragraph_spans(text)
    minimal = getattr(client, 'minimal_prose', False)
    low, high = (1870, 2700) if minimal and target == 2200 else length_band(target)
    compress = count > high
    full = not protected and not compress and (minimal or count < target * .5 or len(spans) < 3)
    action = (f'按原段号给独立压缩候选，候选净删总量至少{count-round(target*.9)}汉字，不能只同义换词。'
              if compress else f'只扩写原场景对白和反应到{target}汉字，不增加剧情。')
    keep = '保留全部情节点、现场对白、口吻、时序与代价，不将对白转述，不强求合段，不删唯一信息。'
    schema = '{"replacements":{"1":"完整替换正文","2":""},"optional_deletions":[3]}'
    selection = '每个候选可独立采用；replacements以源段号为键，删段值为空串。覆盖全部可缩段，不局限开头。'
    numbered = '\n\n'.join(f'[P{n} {chinese_count(text[a:b])}] '+text[a:b] for n,(a,b) in enumerate(spans,1))
    if full:
        schema = '{"paragraphs":["完整全章第一段","完整全章第二段"]}'
        selection = 'paragraphs按顺序包含完整全章正文，程序整章原子采用。'
        numbered = text
    value = ask(client, name, f'本章实测{count}汉字，目标{target}汉字，合格{low}—{high}。只修正一次。'+action+selection+keep,
                [('不修改的段号',pack(sorted(protected))),('编号正文',numbered),
                 ('章节标识',pack({k:chapter[k] for k in ('number','title') if k in chapter}))],
                schema,cap=9000,max_attempts=1,input_limit=10000,
                normalize=lambda v: normalize_expansion(v,text,target) if full else normalize_candidates(v,text,protected))
    if full:
        return '\n\n'.join(value['paragraphs']) or text
    return choose_edits(text,value.get('edits',[]),target)[0]
