"""Shared placement guard for model-suggested single-paragraph insertions."""

from .text import _paragraph_spans


def place_tail_bounded_patch(text, value, *, max_chars=500):
    """Insert no earlier than the final three paragraphs and report any relocation."""
    spans = _paragraph_spans(text)
    requested = value.get("after") if isinstance(value, dict) else None
    patch = value.get("text") if isinstance(value, dict) else None
    if type(requested) is not int or not 1 <= requested <= len(spans):
        return text, dict(status="invalid", reason="invalid_after", requested_after=requested)
    if not isinstance(patch, str) or not patch.strip() or "\n" in patch or len(patch) > max_chars:
        return text, dict(status="invalid", reason="invalid_patch_text", requested_after=requested)
    effective = requested if value.get("replace") is True else max(requested, max(1, len(spans) - 2))
    start, end = spans[effective - 1]
    if value.get("replace") is True:
        changed = text[:start] + patch.strip() + text[end:]
    else:
        changed = text[:end] + "\n\n" + patch.strip() + text[end:]
    return changed, dict(
        status="placed",
        placement="requested" if effective == requested else "relocated_to_tail",
        requested_after=requested,
        effective_after=effective,
    )
