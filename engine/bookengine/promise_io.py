"""Exact audit input budgets: preserve complete sources and schema or return Unknown."""

from .client import SYSTEM


def precise_ask(ask, client, key, instruction, sections, schema, cap=2500):
    limit = min(10000, getattr(client, "context_limit", None) or 10000)
    required = len(SYSTEM) + len(instruction) + len(schema) + 700
    required += sum(len(name) + len(value) + 24 for name, value in sections)
    if required > limit:
        return dict(context_unknown=dict(required=required, limit=limit, reason="complete_sources_exceed_context"))
    saved = getattr(client, "prefix", "")
    clipped = getattr(client, "clip_context", None)
    if client is not None:
        client.prefix = ""
        if clipped is not None:
            client.clip_context = False
    try:
        return ask(client, key, instruction, sections, schema, cap=cap, input_limit=limit)
    finally:
        if client is not None:
            client.prefix = saved
            if clipped is not None:
                client.clip_context = clipped
