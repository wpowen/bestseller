"""Pure serial memory reducer. Persist all four returned values in the caller.

State has compact_state's five fields; only open stable IDs enter foreshadows.
meta.foreshadows[ID] holds description, aliases, status, opened_at, updated_at
(and target for merged IDs). Closed/merged tombstones must survive restarts.
meta.counts exposes active, closed_total, opened_total; meta.next_id never resets.
Entity sidecars hold last_seen/updated_at. Only seen refreshes existing characters'
attendance: replaying a full state snapshot must not keep absent people alive.
meta.retired_objects preserves retirement tombstones outside context.
Archive is a delta of {kind, key, value, meta, reason, chapter}; issues are dicts
with code/key. Neither is context. Explicit alias maps are model assertions,
not semantic similarity inferred here. Input is parsed data, never model text.
"""

from copy import deepcopy
import json
import re

from .longmemory import _records, compact_state

MAX_STATE_CHARS = 12_000
MAX_ACTIVE = 12
_ID = re.compile(r'F[0-9]{3,9}\Z')


def serialize_state(state):
    """The size contract counts Unicode characters, including JSON formatting."""
    return json.dumps(state, ensure_ascii=False, separators=(',', ':'))


def restore_entities(state, archive, names, *, before=None):
    """Return a historical check view for explicit names; active facts win.

    Accept archive.json's kind/name index or a list of archive deltas. before
    excludes entries from that chapter onward during replay. Most recent valid
    snapshot wins. Never restores threads, relationships or clocks. Do not save
    this view as active state or send it wholesale to the model. A retired item
    is historical evidence, not revived: also check meta.retired_objects against
    current grounded non-flashback events in the caller.
    """
    result, wanted = compact_state(state), set(names)
    if isinstance(archive, dict):
        entries = [r for kind in ('characters', 'objects')
                   for r in archive.get(kind, {}).values()]
    else:
        entries = archive or []
    eligible = [r for r in entries if isinstance(r, dict)
                and r.get('kind') in ('characters', 'objects')
                and type(r.get('chapter')) is int
                and (before is None or r['chapter'] < before)]
    for entry in sorted(eligible, key=lambda r: r['chapter'], reverse=True):
        kind, key = entry['kind'], entry.get('key')
        if isinstance(key, str) and key in wanted and key not in result[kind]:
            normalized = compact_state({kind: {key: entry.get('value')}})[kind]
            if key in normalized:
                result[kind][key] = normalized[key]
                if kind == 'objects':
                    result[kind][key].setdefault('owner', '')
    return result


def _events(value):
    for part in value if isinstance(value, (list, tuple)) else [value]:
        if not isinstance(part, dict):
            continue
        for field in ('unresolved', 'foreshadowing', 'foreshadows'):
            source = part.get(field, {})
            if field == 'unresolved' and isinstance(source, (list, tuple)):
                source = [{x: 'open'} if isinstance(x, str) else x for x in source]
            for name, record in _records(source):
                if isinstance(name, str) and name.strip():
                    status = record.get('status', record.get('state')) if isinstance(record, dict) else record
                    desc = record.get('description', '') if isinstance(record, dict) else ''
                    yield name.strip(), status, desc.strip() if isinstance(desc, str) else ''


def manage_state(value, previous, meta, chapter, total, seen=(), aliases=None, *, item_retired=(), protected=()):
    """Return (state, meta, archive_delta, issues), without I/O or input mutation.

    value is a compact_state-compatible delta (or ordered deltas). New threads
    accept {name: 'open'} or {F001: {status: 'open', description: name}}. Closing
    requires an existing open ID/name. aliases maps names or existing open IDs
    to existing open IDs; merging never counts as closing. Rejected proposals
    are archived. Last ceil(total/10) chapters prohibit new threads; at the final
    chapter >3 open threads is an issue, never an automatic closure.
    Explicit retired objects and characters absent >20 chapters leave context.
    Whole low-relevance/old entries are archived until compact JSON fits 12k;
    active threads and protected characters survive trimming; objects go first.
    Invalid chapter order/baselines raise ValueError.
    """
    if type(chapter) is not int or type(total) is not int or not 1 <= chapter <= total:
        raise ValueError('Require 1 <= chapter <= total')
    meta = deepcopy(meta or {})
    if chapter < meta.get('chapter', 0):
        raise ValueError('Chapter cannot move backwards')
    old, delta = compact_state(previous), compact_state(value)
    state, archive, issues = compact_state(value, previous), [], []
    state['foreshadows'] = {}
    threads = meta.setdefault('foreshadows', {})
    seen, retired = set(seen), set(meta.get('retired_objects', ())) | set(item_retired)
    protected = set(protected)
    meta['retired_objects'] = sorted(retired)

    def issue(code, key):
        issues.append(dict(code=code, key=key))

    def archive_entry(kind, key, data, reason, sidecar=None):
        archive.append(dict(kind=kind, key=key, value=deepcopy(data),
                            meta=deepcopy(sidecar or {}), reason=reason, chapter=chapter))

    def resolve(name):
        key = name if name in threads else next(
            (k for k, r in threads.items() if name == r['description'] or name in r['aliases']), None)
        visited = set()
        while key in threads and threads[key]['status'] == 'merged':
            if key in visited:
                raise ValueError('Cyclic thread metadata')
            visited.add(key)
            key = threads[key]['target']
        return key

    def add(name, description, status, at):
        number = max(meta.get('next_id', 1), 1)
        if _ID.fullmatch(name):
            key = name
        else:
            while f'F{number:03d}' in threads:
                number += 1
            key = f'F{number:03d}'
        meta['next_id'] = max(number, int(key[1:]) + 1)
        threads[key] = dict(description=description, aliases=[], status=status,
                            opened_at=at, updated_at=at)
        return key

    # Import an older five-field snapshot once, without losing closed history.
    for name, status in old['foreshadows'].items():
        key = resolve(name)
        if key is None:
            key = add(name, name, status, max(0, chapter - 1))
            if status == 'closed':
                archive_entry('foreshadows', key, status, 'closed', threads[key])
        if threads[key]['status'] == 'open':
            state['foreshadows'][key] = 'open'
    # Sidecar is also sufficient to restore protected open IDs after projection.
    state['foreshadows'].update({k: 'open' for k, r in threads.items() if r['status'] == 'open'})
    if aliases is not None and not isinstance(aliases, dict):
        issue('invalid_alias_map', '')
        aliases = {}
    for source, target in (aliases or {}).items():
        if not isinstance(source, str) or not source.strip():
            issue('invalid_alias', str(source))
            continue
        source = source.strip()
        origin = resolve(source)
        if (not isinstance(target, str) or target not in state['foreshadows'] or
                (origin and threads[origin]['status'] != 'open')):
            issue('invalid_alias', source)
            continue
        if _ID.fullmatch(source) and origin is None:
            issue('unknown_alias_id', source)
            continue
        record = threads[target]
        names = [source]
        if origin and origin != target:
            merged = threads[origin]
            names += [merged['description'], *merged['aliases']]
            merged.update(status='merged', target=target, updated_at=chapter)
            state['foreshadows'].pop(origin, None)
            archive_entry('foreshadows', origin, 'open', 'alias_merged', merged)
        record['aliases'] = sorted(set(record['aliases'] + names) - {target, record['description']})
    if len(state['foreshadows']) > MAX_ACTIVE:
        raise ValueError('Previous active threads exceed cap; supply explicit aliases')

    # Closures run first so a full snapshot can close one and open another at cap.
    events = list(_events(value))
    events.sort(key=lambda e: e[1] != 'closed')
    for name, status, description in events:
        key = resolve(name)
        reason = None
        if status not in ('open', 'closed'):
            reason = 'invalid_thread_status'
        elif key and threads[key]['status'] == 'closed':
            if status == 'closed':
                continue
            reason = 'reopen_closed'
        elif status == 'closed':
            if key not in state['foreshadows']:
                reason = 'close_without_open'
            else:
                threads[key].update(status='closed', updated_at=chapter)
                state['foreshadows'].pop(key)
                archive_entry('foreshadows', key, 'closed', 'closed', threads[key])
        elif key:
            threads[key]['updated_at'] = chapter
        else:
            description = description or ('' if _ID.fullmatch(name) else name)
            collision = resolve(description) if description else None
            if not description:
                reason = 'missing_description'
            elif collision:
                reason = 'reopen_closed' if threads[collision]['status'] == 'closed' else 'alias_required'
            elif chapter * 10 > total * 9:
                reason = 'ending_new_thread'
            elif len(state['foreshadows']) >= MAX_ACTIVE:
                reason = 'active_cap'
            else:
                key = add(name, description, 'open', chapter)
                state['foreshadows'][key] = 'open'
        if reason:
            issue(reason, name)
            archive_entry('foreshadows', name, dict(status=status, description=description), 'rejected_' + reason)

    def remove(kind, key, reason):
        data = state[kind].pop(key) if kind != 'relationships' else key
        if kind == 'relationships':
            state[kind].remove(key)
        sidecar = meta.setdefault(kind, {}).pop(key, {})
        archive_entry(kind, key, data, reason, sidecar)

    for kind in ('characters', 'objects', 'relationships'):
        tracking = meta.setdefault(kind, {})
        for key in state[kind]:
            fresh = key not in old[kind]
            record = tracking.setdefault(key, dict(last_seen=chapter if fresh else max(0, chapter - 1),
                                                   updated_at=chapter if fresh else max(0, chapter - 1)))
            if key in delta[kind]:
                record['updated_at'] = chapter
            if kind == 'characters' and key in seen:
                record['last_seen'] = chapter
        if kind == 'relationships':
            for key in old[kind]:
                if key not in state[kind]:
                    archive_entry(kind, key, key, 'superseded', tracking.pop(key, {}))
        for key in list(state[kind]):
            if kind == 'characters' and key not in protected and chapter - tracking[key]['last_seen'] > 20:
                remove(kind, key, 'absent_20')
            elif kind == 'objects' and key in retired:
                remove(kind, key, 'retired')

    def priority(entry):
        kind, key = entry
        data = state[kind][key] if kind != 'relationships' else key
        relevant = (key in seen or (isinstance(data, dict) and
                    (data.get('present') is True or data.get('owner') in seen)) or
                    (kind == 'relationships' and any(n in key for n in seen)))
        return kind != 'objects', bool(relevant), meta[kind][key]['updated_at'], kind, key

    candidates = sorted(((kind, key) for kind in ('characters', 'objects', 'relationships')
                         for key in state[kind] if kind != 'characters' or key not in protected), key=priority)
    for kind, key in candidates:
        if len(serialize_state(state)) <= MAX_STATE_CHARS:
            break
        remove(kind, key, 'context_budget')
    if len(serialize_state(state)) > MAX_STATE_CHARS:
        raise ValueError('Protected state exceeds 12000 characters')
    meta['chapter'] = chapter
    meta['counts'] = dict(active=len(state['foreshadows']),
                          closed_total=sum(r['status'] == 'closed' for r in threads.values()),
                          opened_total=len(threads))
    if chapter == total and meta['counts']['active'] > 3:
        issue('ending_open_target', str(meta['counts']['active']))
    return state, meta, archive, issues
