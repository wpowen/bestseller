"""Shared helpers for market validation adapters."""

from __future__ import annotations

import re

# Public-page fetches present a regular browser UA; all adapters keep request
# volume minimal (single-digit requests per validation run).
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

_NUMBER_RE = re.compile(r"(\d+(?:\.\d+)?)")


def parse_chinese_number(value: object) -> int:
    """Parse ``44.7万`` / ``1.2亿`` / ``888.84万字`` style labels into an int."""

    if isinstance(value, (int, float)):
        return int(value)
    text = str(value or "").replace(",", "").strip()
    if not text:
        return 0
    match = _NUMBER_RE.search(text)
    if not match:
        return 0
    number = float(match.group(1))
    if "亿" in text:
        number *= 100_000_000
    elif "万" in text:
        number *= 10_000
    return int(number)


def first_value(mapping: dict, *keys: str) -> object | None:
    for key in keys:
        value = mapping.get(key)
        if value not in (None, ""):
            return value
    return None
