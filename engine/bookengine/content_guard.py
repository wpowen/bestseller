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
