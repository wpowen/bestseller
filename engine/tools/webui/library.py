"""Read-only views of public book output; web-owned data stays separate."""
import datetime as dt
import html
import json
import math
import os
from pathlib import Path
import re
import tempfile
import yaml

GENRES = {'suspense-mystery': '悬疑推理', 'xuanhuan-power-fantasy': '玄幻爽文',
          'xianxia-upgrade-core': '仙侠升级', 'urban-power-reversal': '都市逆袭',
          'cozy-mystery': '轻推理', 'history-strategy': '历史谋略'}
# Read only public prompt-pack labels; unsupported CLI identifiers stay unavailable.
LABELS = {'cozy-fantasy':'治愈系奇幻', 'cozy-litrpg':'轻松游戏升级', 'dark-romance':'暗黑爱情',
          'enemies-to-lovers':'宿敌相恋', 'epic-fantasy':'史诗奇幻', 'litrpg-progression':'游戏进阶',
          'mafia-romance':'黑帮爱情', 'paranormal-romance':'超自然爱情', 'psychological-thriller':'心理惊悚',
          'reverse-harem':'多男主恋爱', 'romantasy':'奇幻言情', 'space-opera':'太空歌剧',
          'system-apocalypse-healer':'末日系统治愈者', 'villainess-reincarnation':'反派女配重生'}
for pack in sorted((Path(__file__).resolve().parents[3] / 'config/prompt_packs').glob('*.yaml')):
    if not re.fullmatch(r'[a-z0-9-]+', pack.stem): continue
    label = re.search(r'^name:\s*(.+)', pack.read_text('utf-8'), re.M)
    if label: GENRES.setdefault(pack.stem, LABELS.get(pack.stem, label[1].strip('\"\'')))

BLOCKED = re.compile(r'private|key|d407', re.I)


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def read(path, default=None):
    try:
        return json.loads(Path(path).read_text('utf-8'))
    except (OSError, ValueError):
        return default


def text(path):
    try:
        return Path(path).read_text('utf-8')
    except OSError:
        return ''


def bible_data(path):
    try:
        data = yaml.safe_load(text(path))
        return data if isinstance(data, dict) else {}
    except yaml.YAMLError:
        return {}


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2))
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)


def costs(folder, include_final=True):
    """No cache discount; missing usage retains request upper bounds."""
    known = bound = 0.; unknown = calls = tokens = 0
    paths = [folder / 'calls.json']
    if include_final: paths.extend(folder.glob('final/**/http/ledger.json'))
    if (folder / 'http/ledger.json').exists(): paths.append(folder / 'http/ledger.json')
    for path in paths:
        ledger = read(path, None)
        if path.exists() and not isinstance(ledger, list):
            bound += 1e6; unknown += 1
            continue
        for row in ledger or []:
            if not isinstance(row, dict):
                bound += 1e6; unknown += 1
                continue
            calls += 1
            usage = row.get('usage') or {}
            rates = (.30, 1.20) if row.get('model') == 'MiniMax-M3' else (.15, .60)
            ins, outs = usage.get('prompt_tokens'), usage.get('completion_tokens')
            valid = lambda x: type(x) is int and x >= 0
            tokens += sum(v for v in (ins, outs) if valid(v))
            if valid(ins) and valid(outs):
                known += (ins * rates[0] + outs * rates[1]) / 1e6
            else:
                unknown += 1
                capin = row.get('input_bytes', max(row.get('input_chars', 0) * 4, 40000))
                capout = row.get('output_token_cap', row.get('max_tokens', 16000))
                if not valid(capin): capin = 40000
                if not valid(capout): capout = 16000
                bound += (max(ins if valid(ins) else 0, capin) * rates[0] + max(outs if valid(outs) else 0, capout) * rates[1]) / 1e6
    return dict(cost=round(known + bound, 9), known=round(known, 9),
                unknown_bound=round(bound, 9), unknown=unknown, calls=calls, tokens=tokens)


def stage_costs(folder, web):
    writer = costs(folder, include_final=False)['cost']
    deai = max(0, costs(folder)['cost'] - writer - web.get('external_polish_cost', 0)) if web.get('polish') else 0
    return writer, round(deai, 9)


class Library:
    def __init__(self, root, read_only=False):
        self.read_only = read_only
        self.root = Path(root).resolve()
        self.own = self.root / 'webui'
        if not read_only:
            self.safe(self.own).mkdir(parents=True, exist_ok=True)

    def safe(self, path):
        path = Path(path)
        relative = path.relative_to(self.root)
        if BLOCKED.search(str(relative)) or any(p.is_symlink() for p in [path, *path.parents] if p != self.root):
            raise ValueError('不可读取此路径')
        resolved = path.resolve()
        if resolved == self.root or self.root not in resolved.parents:
            raise ValueError('路径越界')
        return resolved

    def folder(self, key):
        if not isinstance(key, str) or '..' in key or key.startswith('/'):
            raise ValueError('无效书籍编号')
        return self.safe(self.root / key)

    def folders(self):
        found = []
        for current, dirs, files in os.walk(self.root, followlinks=False):
            dirs[:] = [d for d in dirs if not BLOCKED.search(d) and not d.startswith('.')
                       and not (Path(current) / d).is_symlink() and d not in ('responses', 'steps', 'final', 'http')]
            if 'run.json' in files or 'web.json' in files:
                try: found.append(self.safe(Path(current)))
                except ValueError: pass
                dirs[:] = []
        return found

    def chapters(self, folder):
        result = []
        for source in sorted(folder.glob('chapter-*.body.md')):
            m = re.fullmatch(r'chapter-(\d{3,})\.body\.md', source.name)
            if not m: continue
            number = int(m[1]); n = m[1]
            row = read(self.safe(folder / f'chapters/{n}.json'), {})
            report = read(self.safe(folder / f'final/chapter-{n}.deai.json'), row)
            final = text(self.safe(folder / f'final/chapter-{n}.md'))
            valid_final = report.get('status') in ('polished', 'unchanged') and report.get('complete', True) is True
            result.append(dict(number=number, title=row.get('title', f'第{number}章'),
                draft=text(self.safe(source)), final=final if valid_final else '', report=report,
                checks={k: row[k] for k in ('within_band', 'hard_conflicts', 'remaining', 'warnings',
                                          'state_issues', 'quality_issues') if k in row}))
        if not result:
            for source in sorted(folder.glob('chapter-*.md')):
                m = re.fullmatch(r'chapter-(\d{3,})\.md', source.name)
                if m: result.append(dict(number=int(m[1]), title=f'第{int(m[1])}章', draft=text(self.safe(source)), final='', report={}))
        for row in result:
            row.update(status='final' if row['final'] else 'draft', words=len(re.findall(r'[\u3400-\u9fff]', row['final'] or row['draft'])))
        return result

    def meta(self, folder):
        from run_status import auto_deai, runtime_view
        run = read(self.safe(folder / 'run.json'), {}); web = read(self.safe(folder / 'web.json'), {})
        rows = self.chapters(folder); book = run.get('book', {})
        bible = bible_data(self.safe(folder / 'story_bible.yaml'))
        title = web.get('display_title') or bible.get('title') or web.get('title') or folder.name
        total = book.get('chapters', web.get('chapters', len(rows)))
        owned = folder.parent in (self.own, self.own / '.archive') and bool(web)
        state = web.get('status', run.get('status', 'unknown')) if owned else run.get('status', 'unknown')
        if state == 'budget_paused':
            state = 'paused'
        runtime = runtime_view(self.safe(folder), state)
        if runtime.get('kind') in ('waiting', 'quota_exhausted', 'error'):
            state = runtime['kind']
        elapsed = run.get('execution_seconds', run.get('wall_seconds', 0)) or 0
        stat = costs(folder)
        writer, deai = stage_costs(folder, web)
        scores = [r['report'].get('after_score') for r in rows if isinstance(r.get('report'), dict)]
        scores = [x for x in scores if isinstance(x, (int, float)) and math.isfinite(x)]
        info = {r['number']: {k: r[k] for k in ('number', 'title', 'status', 'words')} for r in rows}
        findings = [dict(number=r['number'], **r['checks']) for r in rows if r.get('checks') and (
            r['checks'].get('within_band') is False or r['checks'].get('hard_conflicts') or
            any(r['checks'].get(k) for k in ('remaining', 'warnings', 'state_issues', 'quality_issues')))]
        return dict(id=str(folder.relative_to(self.root)), title=title, synopsis=bible.get('premise', ''),
                    quality_issues=findings,
                    chapter_info=[info.get(n, dict(number=n, title=f'第{n}章', status='pending', words=0)) for n in range(1, total+1)],
                    genre=GENRES.get(book.get('prompt_pack', web.get('genre')), book.get('prompt_pack', '未知')),
                    chapters=len(rows), total=total, words=sum(len(re.findall(r'[\u3400-\u9fff]', r['draft'])) for r in rows),
                    status=state, created=web.get('created', run.get('started', '')), owned=owned,
                    polish=web.get('polish', False), auto_deai=auto_deai(web), deai_cost=deai,
                    stage=runtime.get('stage', ''), retry=runtime.get('retry', 0),
                    next_retry_at=runtime.get('next_retry_at'), error=runtime.get('error', ''),
                    step=runtime.get('error') or web.get('reason') or run.get('stop_reason') or (
                        ('正文' if rows else '圣经 / 排期') if state == 'running' else state),
                    score=round(sum(scores)/len(scores), 3) if scores else None,
                    writer_cost=writer, postprocess_cost=round(stat['cost']-writer,9),
                    eta=round(elapsed / len(rows) * max(0, total-len(rows))) if rows else None, **stat)

    def listing(self):
        return sorted([self.meta(p) for p in self.folders()], key=lambda x: x['created'], reverse=True)

    def archive(self):
        return [self.meta(self.safe(p)) for p in self.safe(self.own / '.archive').glob('*') if not p.name.startswith('.') and p.is_dir()]

    def spending(self):
        paid = read(self.safe(self.own / '.spent.json'), {})
        if not isinstance(paid, dict):
            raise ValueError('费用汇总损坏')
        for base in (self.own, self.safe(self.own / '.archive')):
            for folder in base.glob('*'):
                if folder.name.startswith('.') or not folder.is_dir() or folder.is_symlink():
                    continue
                charge = sum(stage_costs(folder, read(self.safe(folder / 'web.json'), {})))
                key = 'webui/' + folder.name
                paid[key] = dict(charged=max(charge, paid.get(key, {}).get('charged', 0)))
        return sum(r['charged'] for r in paid.values()) + sum(costs(self.safe(p))['cost'] for p in self.safe(self.own / '.topics').glob('*') if p.is_dir())

    def detail(self, key):
        folder = self.folder(key)
        characters = bible_data(self.safe(folder / 'story_bible.yaml')).get('characters', [])
        if isinstance(characters, dict):
            characters = [dict(value, name=value.get('name', name)) for name, value in characters.items() if isinstance(value, dict)]
        if not isinstance(characters, list):
            characters = []
        return dict(meta=self.meta(folder), chapters=self.chapters(folder),
                    characters=[c for c in characters if isinstance(c, dict)],
                    bible=text(self.safe(folder / 'story_bible.yaml')),
                    outline='\n\n'.join(text(self.safe(p)) for p in sorted(folder.glob('writing_outlines/*.json'))))

    def static(self):
        """Self contained, escaped chapter text, readable from file://."""
        books = []
        for folder in self.folders():
            meta = self.meta(folder)
            parts = ['<h2>' + html.escape(meta['title']) + '</h2>']
            for row in self.chapters(folder):
                parts.append('<details><summary>' + html.escape(f"第{row['number']}章 {row['title']}") + '</summary><pre>' + html.escape(row['final'] or row['draft']) + '</pre></details>')
            books.append(''.join(parts))
        page = '<!doctype html><meta charset="utf-8"><title>我的书库</title><style>body{max-width:850px;margin:50px auto;background:#faf7f0;color:#27271f;font:18px serif}pre{white-space:pre-wrap;line-height:2;font:inherit}summary{cursor:pointer;padding:15px}</style><h1>我的书库 · 离线阅读</h1>' + ''.join(books)
        if not self.read_only:
            save(self.safe(self.own / 'LIBRARY.html'), page)
        return page
