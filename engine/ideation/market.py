"""Public rankings only. Real books/anchors belong to filters and judges, never writers."""
from collections import Counter
import datetime as dt
import fcntl
import json
from pathlib import Path
import re
import urllib.request as http
from urllib.parse import urlsplit
import yaml

DATA = Path(__file__).resolve().parents[1] / 'data' / 'ideation'
URL = 'https://www.fanqiehub.com/api/data?board_type=%E6%96%B0%E4%B9%A6%E6%A6%9C&per_page=3000'


def _data(name):
    return yaml.safe_load((DATA / f'{name}.yaml').read_text('utf-8'))


def _read(path):
    try:
        value = json.loads(path.read_text('utf-8'))
        if (isinstance(value, dict) and isinstance(value.get('books'), list)
                and isinstance(value.get('date'), str) and value.get('status') in ('fresh', 'stale', 'missing')
                and isinstance(value.get('source'), str) and isinstance(value.get('features'), dict)):
            dt.date.fromisoformat(value['date'])
            if all(isinstance(b, dict) and all(k in b for k in ('title', 'hook', 'category', 'channel', 'tags'))
                   and all(isinstance(b[k], str) for k in ('title', 'hook', 'category', 'channel'))
                   and isinstance(b['tags'], list) and all(isinstance(t, str) for t in b['tags']) for b in value['books']):
                return value
    except (OSError, ValueError, TypeError):
        pass


class _PublicRedirect(http.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urlsplit(newurl)
        if target.scheme != 'https' or target.netloc not in ('fanqiehub.com', 'www.fanqiehub.com'):
            raise ValueError('Unexpected public API redirect')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _fetch():
    with http.build_opener(_PublicRedirect()).open(URL, timeout=12) as response:
        raw = response.read(12_000_001)
    if len(raw) > 12_000_000:
        raise ValueError('Oversized market response')
    payload = json.loads(raw)
    rows = payload['data']
    if not payload.get('success') or not isinstance(rows, list) or payload.get('total', len(rows)) > len(rows):
        raise ValueError('Incomplete public ranking')
    rows = [b for b in rows if isinstance(b, dict) and b.get('榜单类型') == '新书榜']
    books, dates = [], []
    for row in sorted(rows, key=lambda b: (int(b.get('排名') or 9999), str(b.get('书名', '')))):
        title = row.get('书名')
        if not isinstance(title, str) or not title.strip():
            continue
        tags = row.get('标签') or []
        if isinstance(tags, str):
            tags = re.split(r'[,，、|+]', tags)
        tags = sorted({t.strip() for t in tags if isinstance(t, str) and t.strip()})
        books.append(dict(title=title.strip(), hook=str(row.get('简介') or ''),
                          category=str(row.get('分类') or ''), channel=str(row.get('平台') or ''), tags=tags))
        dates.append(dt.date.fromisoformat(row['数据日期']).isoformat())
    if not books:
        raise ValueError('Empty public ranking')
    return min(dates), books


def features(market, genre, channel=''):
    """Only anchors contain book text; all other text comes from controlled YAML pools."""
    mapping, pools, vocab = _data('genre_map'), _data('pools'), _data('features')
    categories = mapping.get(genre, mapping['default'])
    channel = {'male': '男频', 'female': '女频'}.get(channel, channel)
    books = [b for b in market.get('books', []) if (not genre or b['category'] in categories)
             and (not channel or b['channel'] == channel)]
    shells = pools['shells'].get(genre, pools['shells']['default'])
    if not genre:
        shells = sorted({s for group in pools['shells'].values() for s in group})
    texts = [' '.join([b['title'], b['hook'], *b['tags']]) for b in books]
    counts = {s: sum(s in t for t in texts) for s in shells}
    shapes = Counter('colon' if re.search(r'[:：]', b['title']) else 'comma' if re.search(r'[,，]', b['title'])
                     else 'question' if re.search(r'[?!？！]', b['title']) else 'short' for b in books)
    tags = Counter(t for b in books for t in set(b['tags']) if t in vocab['tags'])
    rules = {name: sum(any(token in t for token in tokens) for t in texts) for name, tokens in vocab['rules'].items()}
    return dict(shells=sorted(counts, key=lambda s: (-counts[s], s)), tags=[t for t, _ in sorted(tags.items(), key=lambda x: (-x[1], x[0]))],
                title_shapes={k: shapes[k] / len(books) if books else 0 for k in ('colon', 'comma', 'question', 'short')},
                rules=[r for r in rules if rules[r]], shell_counts=counts, anchors=books[:6])


def snapshot(root):
    """root is the books directory. One bounded attempt/day, including failed attempts."""
    today = dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).date().isoformat()
    cache = Path(root) / '.market'
    missing = dict(date=today, status='missing', source='FanqieHub', books=[], features={})
    def obtain():
        path = cache / f'{today}.json'
        existing = _read(path)
        if existing is not None:
            return existing
        result = missing
        try:
            date, books = _fetch()
            result = dict(date=date, status='fresh' if date == today else 'stale', source=URL, books=books)
            result['features'] = features(result, '')
        except Exception:  # Market/network/schema failures must never block ideation.
            for old in sorted(cache.glob('????-??-??.json'), reverse=True):
                prior = _read(old) if old.stem <= today else None
                if prior and prior['books'] and prior['date'] <= today:
                    result = dict(prior, status='stale')
                    break
        try:
            temporary = path.with_suffix('.tmp')
            temporary.write_text(json.dumps(result, ensure_ascii=False, sort_keys=True), 'utf-8')
            temporary.replace(path)
        except OSError:
            pass
        return result
    try:
        cache.mkdir(parents=True, exist_ok=True)
        with (cache / '.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            return obtain()
    except OSError:
        current = _read(cache / f'{today}.json')
        if current is not None:
            return current
        for old in sorted(cache.glob('????-??-??.json'), reverse=True):
            prior = _read(old) if old.stem <= today else None
            if prior and prior['books'] and prior['date'] <= today:
                return dict(prior, status='stale')
        return missing
