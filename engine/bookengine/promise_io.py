"""Exact audit input budgets: preserve complete sources and schema or return Unknown."""

import warnings

from .client import SYSTEM
from .longmemory import long_context
from .longutil import normalize_sections


def precise_ask(ask, client, key, instruction, sections, schema, cap=2500, *, required_sources=None, parser=None):
    limit = min(10000, getattr(client, "context_limit", None) or 10000)
    supplied_sections = list(sections.items()) if isinstance(sections, dict) else list(sections)
    serialized_sections = normalize_sections(supplied_sections)
    required = len(SYSTEM) + len(instruction) + len(schema) + 700
    required += sum(len(name) + len(value) + 24 for name, value in serialized_sections)
    if required > limit:
        return dict(context_unknown=dict(required=required, limit=limit, reason="complete_sources_exceed_context"))
    if "repair" in key:
        sources = [value for value in (required_sources or []) if isinstance(value, str) and value]
        budget = max(0, limit - len(SYSTEM) - len(instruction) - len(schema) - 350)
        packed = long_context(serialized_sections, budget)
        if not sources or not all(value in packed for value in sources):
            warnings.warn(f"{key}: empty_sources; repair request skipped", RuntimeWarning, stacklevel=2)
            return {"status": "skipped", "reason": "empty_sources"}
    saved = getattr(client, "prefix", "")
    clipped = getattr(client, "clip_context", None)
    if client is not None:
        client.prefix = ""
        if clipped is not None:
            client.clip_context = False
    try:
        options = {"parser": parser} if parser is not None else {}
        return ask(client, key, instruction, supplied_sections, schema, cap=cap, input_limit=limit, **options)
    finally:
        if client is not None:
            client.prefix = saved
            if clipped is not None:
                client.clip_context = clipped
