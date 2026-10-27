"""Opt-in, exact source clauses selected by a trusted creator revision.

No genre vocabulary or defaults. The canonical writer verifies origin first;
these pure checks bind transport and candidate to that canonical selection.
"""
from collections.abc import Mapping
from copy import deepcopy
from typing import Any
import re


def _chapter_number(value: str) -> int:
    if value.isascii() and value.isdigit():
        return int(value)
    digits = {'零': 0, '〇': 0, '一': 1, '二': 2, '两': 2, '三': 3, '四': 4,
        '五': 5, '六': 6, '七': 7, '八': 8, '九': 9}
    units = {'十': 10, '百': 100, '千': 1000}
    total = number = 0
    for char in value:
        if char in digits:
            number = digits[char]
        elif char in units:
            total += (number or 1) * units[char]
            number = 0
        else:
            raise ValueError('planning_chapter_reference_invalid')
    return total + number


def validate_planning_chapter_references(payload: Any, *, target_chapters: int) -> None:
    """Check explicit chapter references, never quantities, years or raw archives."""
    numerals = r'[0-9零〇一二两三四五六七八九十百千]+'
    pattern = re.compile(r'第?(' + numerals + r')(?:\s*[-–—~～至到]\s*第?(' + numerals + r'))?\s*章(之后|以后|后(?!记))?')
    def visit(value: Any, path: str) -> None:
        if isinstance(value, str):
            for match in pattern.finditer(value):
                numbers = [_chapter_number(x) for x in match.groups()[:2] if x is not None]
                if (any(n < 1 or n > target_chapters for n in numbers)
                        or numbers != sorted(numbers)
                        or (match.group(3) and max(numbers) >= target_chapters)):
                    raise ValueError('planning_chapter_reference_outside_extent:' + path)
        elif isinstance(value, Mapping):
            for key, child in value.items():
                # Metadata retains historical source/response evidence verbatim.
                if key != '_meta':
                    visit(child, path + '/' + str(key))
        elif isinstance(value, list):
            for index, child in enumerate(value):
                visit(child, path + '/' + str(index))
    visit(payload, '')


def resolve_pointer(value: Any, pointer: str) -> Any:
    if not isinstance(pointer, str) or not pointer.startswith('/'):
        raise ValueError('retained_clause_pointer_invalid')
    for part in pointer[1:].split('/'):
        part = part.replace('~1', '/').replace('~0', '~')
        try:
            if isinstance(value, list):
                if not part.isdigit() or str(int(part)) != part:
                    raise ValueError('retained_clause_pointer_invalid')
                value = value[int(part)]
            else:
                value = value[part]
        except (KeyError, IndexError, TypeError) as exc:
            raise ValueError('retained_clause_pointer_missing') from exc
    return deepcopy(value)


def selected_clauses(project: Any, kind: str) -> list[dict[str, Any]]:
    binding = getattr(project, '_source_bound_planning_canonical', {})
    content = binding.get('content', {}) if isinstance(binding, Mapping) else {}
    rows = content.get('retained_planning_clauses', [])
    if not isinstance(rows, list):
        raise ValueError('retained_planning_clauses_invalid')
    selected = []
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or not all(row.get(k) for k in (
            'kind', 'target_pointer', 'source_artifact_id', 'source_content_sha256', 'source_pointer',
        )) or 'value' not in row:
            raise ValueError('retained_planning_clause_invalid')
        key = (row['kind'], row['target_pointer'])
        if key in seen:
            raise ValueError('retained_planning_clause_duplicate')
        seen.add(key)
        if row['kind'] == kind:
            selected.append(deepcopy(row))
    return selected


def validate_retained_planning_clauses(payload: Any, *, project: Any, kind: str) -> None:
    for row in selected_clauses(project, kind):
        actual = resolve_pointer(payload, row['target_pointer'])
        if type(actual) is not type(row['value']) or actual != row['value']:
            raise ValueError('retained_planning_clause_changed:' + row['target_pointer'])
