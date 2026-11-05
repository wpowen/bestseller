"""Filesystem exports, independent of the writing engine and model clients."""
import json
import os
from pathlib import Path
import re
import tempfile
from tools.chapter_outputs import has_final

FORMATS = ('txt', 'md', 'docx', 'epub', 'fanqie')


def read(path):
    return path.read_bytes().decode('utf-8')


def load(path):
    return json.loads(read(path)) if path.is_file() else {}


def heading(number, title):
    title = str(title).strip()
    if not title or '\n' in title or '\r' in title:
        raise ValueError('Chapter title must be one nonempty line')
    return title if re.match(r'^第[0-9一二三四五六七八九十百千万零〇两]+章(?:\s|$)', title) else f'第{number}章 {title}'


def chapter_file(path):
    text = read(path)
    line, sep, rest = text.partition('\n\n')
    if not sep or not line.startswith('## ') or not rest.endswith('\n\n'):
        raise ValueError(f'Invalid chapter wrapper: {path.name}')
    return line[3:], rest[:-2]


def read_book(folder, version='final'):
    folder = Path(folder).resolve()
    meta = load(folder / 'run.json')
    count = meta.get('book', {}).get('chapters')
    if count is None:
        numbers = {int(m[1]) for p in folder.glob('chapter-*.md')
                   if (m := re.fullmatch(r'chapter-(\d+)(?:\.body|\.draft|\.r2\.draft|\.final)?\.md', p.name))}
        count = max(numbers, default=0)
    if type(count) is not int or count < 1:
        raise ValueError('No chapter count or chapters found')
    title = ''
    if (folder / 'book.md').is_file():
        first = read(folder / 'book.md').split('\n', 1)[0]
        title = first[2:] if first.startswith('# ') else ''
    if not title:
        for name in ('plan.json', 'bible.json', 'story_bible.json', 'steps/plan-overall.json'):
            title = load(folder / name).get('title', '')
            if title:
                break
    title = title or load(folder / 'web.json').get('display_title') or load(folder / 'web.json').get('title')
    if not title or '\n' in title or '\r' in title:
        raise ValueError('Missing or invalid book title')
    rows = []
    for n in range(1, count + 1):
        if version in ('final', 'polished') and not has_final(folder, n):
            continue
        base = folder / f'chapter-{n:03d}'
        wrapped = base.with_suffix('.md')
        label = load(folder / f'chapters/{n:03d}.json').get('title')
        if wrapped.is_file() and not label:
            wrapped_label, wrapped_body = chapter_file(wrapped)
            label = label or wrapped_label
        if not label:
            outline = load(folder / 'steps/04-outline.json').get('chapters', [])
            label = next((r.get('title') for r in outline if r.get('number') == n), None)
        if not label:
            raise ValueError(f'Missing chapter title: {n}')
        if version in ('final', 'polished'):
            source = folder / f'final/chapter-{n:03d}.md'
            rows.append((n, heading(n, label), read(source)))
            if not rows[-1][2].strip(): raise ValueError(f'Empty polished chapter: {n}')
            continue
        suffixes = ('.r2.draft.md', '.draft.md') if version == 'draft' else ('.body.md', '.final.md')
        source = next((Path(str(base) + s) for s in suffixes if Path(str(base) + s).is_file()), None)
        if source:
            body = read(source)
        elif version == 'final' and wrapped.is_file():
            body = wrapped_body
        else:
            raise ValueError(f'Missing {version} chapter: {n}')
        if not body.strip():
            raise ValueError(f'Empty chapter: {n}')
        rows.append((n, heading(n, label), body))
    if not rows:
        raise ValueError('No completed final chapters')
    return title, rows


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(value.encode('utf-8') if isinstance(value, str) else value)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def plain(text):
    """Remove prose Markdown while keeping literal punctuation and wording."""
    text = re.sub(r'^\s*(```|~~~).*$', '', text, flags=re.M)
    text = re.sub(r'^ {0,3}(?:[-*_] *){3,}$', '', text, flags=re.M)
    text = re.sub(r'^ {0,3}(?:#{1,6} +|> ?|[-+*] +|\d+[.)] +)', '', text, flags=re.M)
    text = re.sub(r'!?\[([^\]]*)\]\([^\n)]*\)', r'\1', text)
    for marker in (r'\*\*', '__', '~~', '`', r'\*', '_'):
        text = re.sub(marker + r'(?=\S)(.+?)(?<=\S)' + marker, r'\1', text)
    text = re.sub(r'\\([\\`*_{}\[\]()#+.!>~-])', r'\1', text)
    return text.strip('\n')


def render(out, title, rows, formats, toc=True, prune=False):
    from .documents import docx_bytes, epub_bytes
    out = Path(out)
    if 'md' in formats:
        expected = {f'chapter-{n:03d}.md' for n, _, _ in rows}
        for old in out.glob('chapter-*.md') if prune else ():
            if re.fullmatch(r'chapter-\d{3,}\.md', old.name) and old.name not in expected:
                old.unlink()
        sections = [f'# {title}\n\n']
        if toc:
            sections.append('目录\n' + '\n'.join(f'- [{label}](#chapter-{n:03d})' for n, label, _ in rows) + '\n\n')
        for n, label, body in rows:
            chapter = f'## {label}\n\n{body}\n\n'
            write(out / f'chapter-{n:03d}.md', chapter)
            sections.append((f'<a id="chapter-{n:03d}"></a>\n\n' if toc else '') + chapter)
        write(out / 'book.md', ''.join(sections))
    if 'txt' in formats:
        text = title + '\n\n目录\n' + '\n'.join(label for _, label, _ in rows) + '\n\n'
        write(out / 'book.txt', text + ''.join(f'{label}\n\n{body}\n\n' for _, label, body in rows))
    if 'docx' in formats:
        write(out / 'book.docx', docx_bytes(title, rows))
    if 'epub' in formats:
        write(out / 'book.epub', epub_bytes(title, rows))
    if 'fanqie' in formats:
        expected = set()
        for n, label, body in rows:
            name = re.sub(r'[\x00-\x1f<>:"/\\|?*]', '_', plain(label))[:100].rstrip('. ')
            filename = f'{n:03d}-{name}.txt'
            expected.add(filename)
            write(out / 'fanqie' / filename, plain(label) + '\n\n' + plain(body))
        for old in (out / 'fanqie').glob('*.txt') if prune else ():
            if re.fullmatch(r'\d{3,}-.+\.txt', old.name) and old.name not in expected:
                old.unlink()


def export_book(bookdir, formats=FORMATS, version='final', out=None):
    if version not in ('draft', 'final', 'polished') or not formats or set(formats) - set(FORMATS):
        raise ValueError('Invalid version or formats')
    bookdir = Path(bookdir).resolve()
    target = Path(out).absolute() if out else bookdir / 'exports' / version
    resolved = target.resolve()
    if resolved == bookdir or (bookdir in resolved.parents and bookdir / 'exports' not in resolved.parents):
        raise ValueError('Output inside a book must be under exports/')
    parents = [p for p in target.parents if bookdir in p.parents]
    if any(p.is_symlink() for p in (target, *parents)) or (target.exists() and any(p.is_symlink() for p in target.rglob('*'))):
        raise ValueError('Output must not contain symlinks')
    title, rows = read_book(bookdir, version)
    total = load(bookdir / 'run.json').get('book', {}).get('chapters', max(r[0] for r in rows))
    missing = sorted(set(range(1, total + 1)) - {r[0] for r in rows})
    if missing:
        rows.append((total + 1, '导出说明', '仅含终稿；缺少第 ' + '、'.join(map(str, missing)) + ' 章'))
    render(resolved, title, rows, formats, prune=True)
    return {'out': str(resolved), 'title': title, 'version': version, 'chapters': len(rows) - bool(missing), 'missing_chapters': missing, 'formats': list(formats)}


def legacy_export(out, title, outline, chapters):
    # Preserve positional/keyword arguments, return None and old Markdown bytes.
    rows = [(i, f"第{i}章 {outline[i - 1]['title']}", body) for i, body in enumerate(chapters, 1)]
    render(out, title, rows, ('md', 'docx'), toc=False)
