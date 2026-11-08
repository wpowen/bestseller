"""Keep optional post-draft repairs from stopping a book on provider content moderation.

Every wrapped repair has the shape ``fn(client, prefix, text, raw, failed, ...)`` and
returns ``(text, raw, failed, note)``. When the provider refuses the request after the
client's own retries, the chapter keeps the text it had before the repair (the same as
a rejected repair being rolled back) and records why.
"""

from functools import wraps

from .client import ContentRejected


def skipped(exc):
    return dict(status="skipped_content_rejected", error=str(exc)[:200])


def guarded(repair):
    @wraps(repair)
    def run(client, prefix, text, raw, failed, *rest, **options):
        try:
            return repair(client, prefix, text, raw, failed, *rest, **options)
        except ContentRejected as exc:
            return text, raw, failed, skipped(exc)

    return run


DRAFT_VARIANTS = ("", "-restrained1", "-restrained2")
RESTRAINED_DRAFT = "\n上一稿未通过平台内容审核。情节、人物和事实不变；暴力只写动作与后果，不写血腥、残肢、酷刑细节。"


def draft_until_readable(write_draft, finish):
    """Redraft with restraint when the provider refuses to read the draft itself.

    Each variant uses its own stage names, so the refused draft and its call
    records stay on disk untouched. Returns ``(text, finish_result, variant)``.
    """
    for attempt, suffix in enumerate(DRAFT_VARIANTS):
        text = write_draft(suffix)
        try:
            return text, finish(suffix, text), suffix or "base"
        except ContentRejected:
            if attempt == len(DRAFT_VARIANTS) - 1:
                raise
    raise AssertionError("unreachable")
