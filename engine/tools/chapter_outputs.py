"""Shared filesystem protocol for committed chapters and published final output."""
import json
from pathlib import Path


def load(path):
    try:
        return json.loads(Path(path).read_text('utf-8'))
    except (OSError, ValueError):
        return {}


def committed(book, number):
    return load(Path(book) / f'chapters/{int(number):03d}.json').get('status') == 'complete'


def finalized(report):
    passed = report.get('complete') is True and report.get('status') in ('polished', 'unchanged')
    retained = report.get('processing_complete') is True and report.get('status') in ('retained', 'partial', 'original')
    return passed or retained


def has_final(book, number):
    base = Path(book) / f'final/chapter-{int(number):03d}'
    return (finalized(load(base.with_suffix('.deai.json')))
            and base.with_suffix('.md').is_file() and bool(base.with_suffix('.md').read_text('utf-8').strip()))


def finished(book):
    run = load(Path(book) / 'run.json')
    total = run.get('book', {}).get('chapters', load(Path(book) / 'web.json').get('chapters', 0))
    return run.get('status') == 'complete' and total > 0 and all(has_final(book, n) for n in range(1, total + 1))
