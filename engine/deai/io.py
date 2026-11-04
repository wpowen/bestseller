"""Independent disk boundary; no engine state is consumed."""
import dataclasses
import json
import math
import os
from pathlib import Path
import re
import tempfile
import yaml

def safe(root, name):
    root = Path(root).resolve()
    target = (root / name).resolve()
    if target == root or root not in target.parents:
        raise ValueError('Path escapes output directory')
    return target

def clean(value): return value

def read_text(path): return Path(path).read_bytes().decode('utf-8')

def parse_json(text):
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        for match in re.finditer(r'[\[{]', text):
            try:
                return json.JSONDecoder().raw_decode(text[match.start():])[0]
            except json.JSONDecodeError:
                pass
        raise ValueError('Invalid JSON response') from None

def read_json(path, default=None):
    return parse_json(read_text(path)) if Path(path).exists() else default

def save(root, name, value):
    path = safe(root, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    content = value if isinstance(value, str) else json.dumps(clean(value), ensure_ascii=False, indent=2)
    fd, tmp = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='') as stream:
            stream.write(content); stream.flush(); os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)

def chapters(book):
    book = Path(book).resolve(); found = {}
    for path in sorted(book.iterdir()):
        match = re.fullmatch(r'chapter-(\d{3,})(\.body)?\.md', path.name)
        if match and path.is_file() and path.resolve().parent == book:
            if match[1] not in found or match[2]: found[match[1]] = path
    return sorted(found.items())

def process(book, number, source, env, mode, polish, client):
    import fcntl
    root = Path(book).resolve(); source = Path(source)
    if not re.fullmatch(r'\d{3,}', number) or source.resolve().parent != root: raise ValueError('Invalid chapter path')
    work = safe(root, f'final/.deai/{number}'); work.mkdir(parents=True, exist_ok=True)
    with safe(work, '.chapter.lock').open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            text = read_text(source)
            if not text.strip():return 'waiting'
            final = safe(root, f'final/chapter-{number}.md')
            previous = read_json(safe(root, f'final/chapter-{number}.deai.json'), {})
            if previous.get('status') in ('polished', 'unchanged') and previous.get('checks',{}).get('dialogue_mode','scene')==mode and safe(work, 'input.md').exists() and safe(work, 'output.md').exists() and final.exists() and read_text(safe(work, 'input.md')) == text and read_text(safe(work, 'output.md')) == read_text(final): return 'cached'
            ctx = read_json(safe(root, 'deai-context.json'), {}); size = len(re.findall(r'[\u3400-\u4dbf\u4e00-\u9fff]', text))
            bible = safe(root, 'story_bible.yaml')
            if bible.exists():
                data=yaml.safe_load(read_text(bible)) or {};cards=data.get('characters',data.get('character_cards',[]))
                if isinstance(cards,dict):cards=[dict(v,name=k) if isinstance(v,dict) else dict(name=k,summary=str(v)) for k,v in cards.items()]
                ctx.setdefault('characters',cards);ctx.setdefault('setting',data.get('world',data.get('setting',data.get('premise'))))
            ctx.update(dialogue_mode=mode, word_range=[math.ceil(size * .9), math.floor(size * 1.1)]); ctx['length_range'] = ctx['word_range']
            result = polish(text, ctx, client(work, env)); report = dataclasses.asdict(result) if dataclasses.is_dataclass(result) else vars(result).copy()
            if report.get('status') in ('error', 'failed', 'invalid'): save(root, f'final/chapter-{number}.deai.json', report); return 'error'
            if report.get('status') == 'retained': result.text = text; report.update(text=text, complete=False)
            else: report['complete'] = report.get('status') in ('polished', 'unchanged')
            if not result.text.strip(): raise ValueError('Polish did not produce a valid result')
            if not ctx['length_range'][0] <= len(re.findall(r'[\u3400-\u4dbf\u4e00-\u9fff]', result.text)) <= ctx['length_range'][1]: raise ValueError('Output outside original length +/-10%')
            if read_text(source) != text:
                report['status'] = 'source_changed'; save(root, f'final/chapter-{number}.deai.json', report); return 'source_changed'
            save(work, 'output.md', result.text); save(root, f'final/chapter-{number}.deai.json', report)
            save(root, f'final/chapter-{number}.md', result.text); save(work, 'input.md', text)
            return report.get('status', 'ok')
        except Exception as exc:
            save(root, f'final/chapter-{number}.deai.json', {'status': 'error', 'error': clean(str(exc))}); return 'error'
