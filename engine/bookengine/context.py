"""Pure context packing; budgets count Unicode characters, including formatting."""

import re


MAX_CONTEXT_CHARS = 48_000
_SEPARATOR = "\n\n"
_MARKER = "\n\n[truncated]"
_PARAGRAPH_BREAK = re.compile(r"\r?\n[ \t]*\r?\n")


def _budget(limit: int) -> int:
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
        raise ValueError("limit must be a nonnegative integer")
    return min(limit, MAX_CONTEXT_CHARS)


def limit_info(text: str, limit: int = MAX_CONTEXT_CHARS) -> dict:
    """Report chars, effective limit, signed remaining characters, and fits."""
    budget = _budget(limit)
    used = len(text)
    return dict(chars=used, limit=budget, remaining=budget - used, fits=used <= budget)


def pack_context(required: dict[str, str], optional: dict[str, str], limit: int) -> str:
    """Pack ``## heading\nvalue`` sections separated by two newlines.

    Required sections are preserved exactly or raise ValueError. Optional sections
    are tried in insertion order, taking the longest complete paragraph prefix
    that fits with a [truncated] marker when the full section cannot fit. Sections
    with no fitting paragraph are skipped; later smaller sections may still fit.
    Values are not stripped or normalized. The effective limit is capped at 48,000;
    callers must subtract all other request content before passing their limit.
    """
    budget = _budget(limit)
    packed = _SEPARATOR.join(f"## {heading}\n{value}" for heading, value in required.items())
    if len(packed) > budget:
        raise ValueError(f"required context uses {len(packed)} characters; limit is {budget}")
    for heading, value in optional.items():
        prefix = (_SEPARATOR if packed else "") + f"## {heading}\n"
        available = budget - len(packed) - len(prefix)
        if len(value) <= available:
            packed += prefix + value
            continue
        end = None
        for boundary in _PARAGRAPH_BREAK.finditer(value):
            if boundary.start() + len(_MARKER) > available:
                break
            if value[:boundary.start()].strip():
                end = boundary.start()
        if end is not None:
            packed += prefix + value[:end] + _MARKER
    return packed
