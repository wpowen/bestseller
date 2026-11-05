"""Independent disk boundary; no engine state is consumed."""

import dataclasses
import datetime as dt
import json
import math
import os
from pathlib import Path
import re
import tempfile
import yaml
from tools.runtime_support import ProjectError, QuotaExhausted, write_runtime
from .version import VERSION


def safe(root, name):
    root = Path(root).resolve()
    target = (root / name).resolve()
    if target == root or root not in target.parents:
        raise ValueError('Path escapes output directory')
    return target


def clean(value):
    return value


def read_text(path):
    return Path(path).read_bytes().decode('utf-8')


def parse_json(text):
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        for match in re.finditer(r'[\[{]', text):
            try:
                return json.JSONDecoder().raw_decode(text[match.start() :])[0]
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
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def chapters(book):
    book = Path(book).resolve()
    found = {}
    for path in sorted(book.iterdir()):
        match = re.fullmatch(r'chapter-(\d{3,})(\.body)?\.md', path.name)
        if match and path.is_file() and path.resolve().parent == book:
            if match[1] not in found or match[2]:
                found[match[1]] = path
    return sorted(found.items(), key=lambda item: int(item[0]))


def policy(book, mode, model):
    data = Path(__file__).parent / 'data'
    return dict(
        version=VERSION,
        mode=mode,
        model=model,
        data={p.name: read_text(p) for p in [*sorted(data.glob('*.yaml')), data.parents[1] / 'data/deai_recall.yaml']},
        context={
            n: read_text(Path(book) / n) for n in ('story_bible.yaml', 'deai-context.json') if (Path(book) / n).exists()
        },
    )


def reusable(root, number, text, configuration):
    work = safe(root, f'final/.deai/{number}')
    final = safe(root, f'final/chapter-{number}.md')
    report = read_json(safe(root, f'final/chapter-{number}.deai.json'), {})
    finished = report.get('complete') is True and report.get('status') in ('polished', 'unchanged')
    rolled_back = report.get('processing_complete') is True and report.get('status') in ('retained', 'partial')
    return (
        (finished or rolled_back)
        and read_json(safe(work, 'policy.json')) == configuration
        and final.exists()
        and all(safe(work, n).exists() for n in ('input.md', 'output.md'))
        and read_text(safe(work, 'input.md')) == text
        and read_text(safe(work, 'output.md')) == read_text(final)
    )


def keep_previous(report, previous, old_source, source):
    return (
        report.get('complete') is not True
        and previous.get('complete') is True
        and previous.get('status') in ('polished', 'unchanged')
        and old_source == source
    )


def summarize(book, statuses):
    rows = [
        '# 全书去 AI 味处理',
        '',
        '| 章 | 状态 | 字数 原→终 | 命中句 原→终 | 检测分 原→终 | 采纳句 | 耗时秒 |',
        '|---|---|---:|---:|---:|---:|---:|',
    ]
    bodies = []
    notes = []
    bible = Path(book) / 'story_bible.yaml'
    metadata = yaml.safe_load(read_text(bible)) if bible.exists() else {}
    bodies.append('# ' + str((metadata or {}).get('title', Path(book).name)))
    for n, source in chapters(book):
        r = read_json(safe(book, f'final/chapter-{n}.deai.json'), {})
        c = r.get('checks', {})
        final = safe(book, f'final/chapter-{n}.md')
        before = len({h['sid'] for h in r.get('hits', [])})
        after = len({h['sid'] for h in c.get('remaining_hits', [])})
        rows.append(
            f'| [{n}](chapter-{n}.md) | {statuses.get(n, r.get("status", "等待"))} | {c.get("original_chars", "?")}→{c.get("final_chars", "?")} | {before}→{after} | {r.get("before_score", "?")}→{r.get("after_score", "?")} | {len(r.get("edits", []))} | {r.get("seconds", "?")} |'
        )
        if final.exists():
            bodies.append(f'## 第{int(n)}章\n\n' + read_text(final))
        d = c.get('diagnostics', {})
        risks = d.get('chapter', {}).get('risks', [])
        if d.get('status') != 'checked' or d.get('facts') or risks:
            notes.append(
                f'- 第{int(n)}章：'
                + json.dumps(
                    dict(diagnostic_status=d.get('status', '未完成'), facts=d.get('facts', []), structure=risks),
                    ensure_ascii=False,
                )
            )
    save(
        book,
        'final/SUMMARY.md',
        '\n'.join(rows + ['', '风格分仅为检测器指标，人工可读性待抽读。事实与结构提示不计入风格分。', ''] + notes)
        + '\n',
    )
    save(book, 'final/book.md', '\n\n'.join(bodies) + '\n')


def process(book, number, source, env, mode, polish, client, model='MiniMax-M3.1-Flash'):
    import fcntl

    root = Path(book).resolve()
    source = Path(source)
    if not re.fullmatch(r'\d{3,}', number) or source.resolve().parent != root:
        raise ValueError('Invalid chapter path')
    work = safe(root, f'final/.deai/{number}')
    work.mkdir(parents=True, exist_ok=True)
    with safe(work, '.chapter.lock').open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            text = read_text(source)
            if not text.strip():
                return 'waiting'
            final = safe(root, f'final/chapter-{number}.md')
            configuration = policy(root, mode, model)
            if reusable(root, number, text, configuration):
                return 'cached'
            ctx = read_json(safe(root, 'deai-context.json'), {})
            size = len(re.findall(r'[\u3400-\u4dbf\u4e00-\u9fff]', text))
            bible = safe(root, 'story_bible.yaml')
            if bible.exists():
                data = yaml.safe_load(read_text(bible)) or {}
                cards = data.get('characters', data.get('character_cards', []))
                if isinstance(cards, dict):
                    cards = [
                        dict(v, name=k) if isinstance(v, dict) else dict(name=k, summary=str(v))
                        for k, v in cards.items()
                    ]
                ctx.setdefault('characters', cards)
                ctx.setdefault('setting', data.get('world', data.get('setting', data.get('premise'))))
            ctx.update(dialogue_mode=mode, word_range=[math.ceil(size * 0.9), math.floor(size * 1.1)])
            ctx['length_range'] = ctx['word_range']
            llm = client(work, env, model=model)
            result = polish(text, ctx, llm)
            report = dataclasses.asdict(result) if dataclasses.is_dataclass(result) else vars(result).copy()
            report['deai_version'] = configuration['version']
            calls = getattr(llm, 'calls', [])
            usages = [c.get('usage') or {} for c in calls]
            times = [dt.datetime.fromisoformat(c[k]) for c in calls for k in ('started', 'finished') if c.get(k)]
            report['cumulative'] = dict(
                requests=len(calls),
                prompt_tokens=sum(u.get('prompt_tokens') or 0 for u in usages),
                completion_tokens=sum(u.get('completion_tokens') or 0 for u in usages),
                unknown_usage=sum(u.get('prompt_tokens') is None or u.get('completion_tokens') is None for u in usages),
                estimated_usd=llm.estimate(),
                wall_seconds=round((max(times) - min(times)).total_seconds(), 3) if times else 0,
            )
            if report.get('status') in ('error', 'failed', 'invalid'):
                raise ProjectError(f'deai-{number}: polish failed: {report.get("warnings", [])}')
            if report.get('status') == 'retained':
                result.text = text
                report.update(text=text, complete=False)
            else:
                report['complete'] = report.get('status') in ('polished', 'unchanged')
            report['processing_complete'] = True
            if (
                final.exists()
                and safe(work, 'input.md').exists()
                and keep_previous(
                    report,
                    read_json(safe(root, f'final/chapter-{number}.deai.json'), {}),
                    read_text(safe(work, 'input.md')),
                    text,
                )
            ):
                save(work, 'attempt-last.json', report)
                return report.get('status', 'retained')
            if not result.text.strip():
                raise ProjectError('Polish did not produce a valid result')
            if (
                not ctx['length_range'][0]
                <= len(re.findall(r'[\u3400-\u4dbf\u4e00-\u9fff]', result.text))
                <= ctx['length_range'][1]
            ):
                report.setdefault('warnings', []).append(dict(rollback='word_band'))
                result.text = text
                report.update(text=text, status='retained', complete=False, edits=[])
            if read_text(source) != text:
                report['status'] = 'source_changed'
                save(root, f'final/chapter-{number}.deai.json', report)
                return 'source_changed'
            save(work, 'output.md', result.text)
            save(root, f'final/chapter-{number}.deai.json', report)
            save(root, f'final/chapter-{number}.md', result.text)
            save(work, 'input.md', text)
            save(work, 'policy.json', configuration)
            return report.get('status', 'ok')
        except InterruptedError:
            raise
        except Exception as exc:
            kind = 'quota_exhausted' if isinstance(exc, QuotaExhausted) else 'error'
            error = exc if isinstance(exc, (QuotaExhausted, ProjectError)) else ProjectError(f'deai-{number}: {exc}')
            redact = getattr(locals().get('llm'), 'redact', str)
            message = redact(str(error))
            report = dict(status=kind, error=message, stage=f'deai-{number}')
            previous = read_json(safe(root, f'final/chapter-{number}.deai.json'), {})
            if previous.get('complete') is True:
                save(work, 'attempt-last.json', report)
            else:
                save(root, f'final/chapter-{number}.deai.json', report)
            from .client import runtime_root

            runtime = runtime_root(root)
            existing = read_json(runtime / 'runtime.json', {})
            stage = existing.get('stage') if existing.get('kind') == kind else f'deai-{number}'
            write_runtime(runtime, kind, stage, error=message)
            raise error from exc
