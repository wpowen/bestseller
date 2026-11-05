"""Validate stage shapes before a malformed response can become a clean review."""


def require(condition, message):
    if not condition:
        raise ValueError(message)


def records(value, name):
    rows = value.get(name)
    require(isinstance(rows, list), f'JSON field {name} must be a list')
    require(all(isinstance(row, dict) for row in rows), f'JSON field {name} must contain objects')
    return rows


def coverage(value, parts):
    checked = value.get('checked_pids')
    require(isinstance(checked, list), 'JSON checked_pids must cover every paragraph')
    require(set(checked) == {part['pid'] for part in parts}, 'JSON incomplete paragraph coverage')


def validate_response(stage, value, data):
    if 'paragraphs' in data and 'examples' in data:
        coverage(value, data['paragraphs'])
        ids = {row['sid'] for part in data['paragraphs'] for row in part['sentences']}
        for issue in records(value, 'issues'):
            require(issue.get('sid') in ids, 'JSON issue sid is outside the requested paragraphs')
            require(
                type(issue.get('severity')) is int and 1 <= issue['severity'] <= 3,
                'JSON severity must be integer 1/2/3',
            )
            require(issue.get('type') == data.get('requested_type'), 'JSON issue type differs from the requested type')
    elif stage == 'diagnostics':
        coverage(value, data['paragraphs'])
        records(value, 'facts')
        require(isinstance(value.get('chapter'), dict), 'JSON chapter must be an object')
    elif 'items' in data:
        rows = records(value, 'checks')
        require(
            {row.get('id') for row in rows} == {item['id'] for item in data['items']},
            'JSON fact checks must cover every item',
        )
        require(all(type(row.get('ok')) is bool for row in rows), 'JSON fact ok must be boolean')
    elif 'targets' in data:
        rows = records(value, 'candidates')
        require(
            {row.get('sid') for row in rows} == {item['sid'] for item in data['targets']},
            'JSON candidates must cover every target',
        )
        require(
            all(
                isinstance(row.get('choices'), list) and all(isinstance(choice, str) for choice in row['choices'])
                for row in rows
            ),
            'JSON candidate choices must be strings',
        )
    elif stage == 'inventory':
        entities = value.get('entities')
        require(isinstance(entities, list) and all(isinstance(e, str) for e in entities),
                'JSON inventory entities must be strings')
        records(value, 'paragraphs')
        ids = {p['pid'] for p in data['paragraphs']}
        for scene in records(value, 'scenes'):
            pids = scene.get('pids')
            require(isinstance(pids, list) and all(isinstance(p, str) and p in ids for p in pids),
                    'JSON scene pids must be paragraph identifiers')
    elif stage == 'dialogue-cast':
        rows = records(value, 'slots')
        require(
            {row.get('id') for row in rows} == {slot['id'] for slot in data['slots']},
            'JSON cast must cover every dialogue slot',
        )
    elif stage == 'dialogue-scene':
        for variant in records(value, 'variants'):
            rows = records(variant, 'lines')
            require(
                {row.get('id') for row in rows} == {slot['id'] for slot in data['slots']},
                'JSON variant must cover every dialogue slot',
            )
            require(all(isinstance(row.get('text'), str) for row in rows), 'JSON dialogue text must be a string')
    elif stage.startswith('actor-'):
        require(isinstance(value.get('text'), str), 'JSON actor text must be a string')
