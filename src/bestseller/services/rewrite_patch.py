"""Deterministic, non-promoting local rewrite patch contract."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ProtectedSpan:
    start: int
    end: int
    content_hash: str


@dataclass(frozen=True)
class RewriteEdit:
    start: int
    end: int
    target_hash: str
    anchor_before: str
    anchor_after: str
    replacement: str


@dataclass(frozen=True)
class RewritePatch:
    parent_hash: str
    edits: tuple[RewriteEdit, ...]
    protected_spans: tuple[ProtectedSpan, ...] = ()
    max_changed_ratio: float = 0.25


@dataclass(frozen=True)
class RewritePatchResult:
    accepted: bool
    parent_text: str
    candidate_text: str
    parent_hash: str
    candidate_hash: str
    changed_ratio: float
    failure_codes: tuple[str, ...] = ()
    automatic_promotion_allowed: bool = field(default=False, init=False)


def apply_rewrite_patch(parent_text: str, patch: RewritePatch) -> RewritePatchResult:
    ordered_edits = sorted(patch.edits, key=lambda item: (item.start, item.end))
    if not ordered_edits:
        return _rejected(parent_text, "empty_patch")
    if any(
        edit.start < 0 or edit.end > len(parent_text) or edit.start >= edit.end
        for edit in ordered_edits
    ):
        return _rejected(parent_text, "invalid_edit_bounds")
    if any(not edit.anchor_before and not edit.anchor_after for edit in ordered_edits):
        return _rejected(parent_text, "missing_edit_anchors")
    if any(
        current.start < previous.end
        for previous, current in zip(ordered_edits, ordered_edits[1:], strict=False)
    ):
        return _rejected(parent_text, "overlapping_edits")
    if any(
        edit.start < protected.end and protected.start < edit.end
        for edit in patch.edits
        for protected in patch.protected_spans
    ):
        return _rejected(parent_text, "protected_span_touched")
    if any(
        text_hash(parent_text[protected.start : protected.end])
        != protected.content_hash
        for protected in patch.protected_spans
    ):
        return _rejected(parent_text, "protected_span_mismatch")
    if text_hash(parent_text) != patch.parent_hash:
        return _rejected(parent_text, "parent_hash_mismatch")
    if any(
        text_hash(parent_text[edit.start : edit.end]) != edit.target_hash
        for edit in patch.edits
    ):
        return _rejected(parent_text, "target_hash_mismatch")
    if any(not _anchors_match(parent_text, edit) for edit in patch.edits):
        return _rejected(parent_text, "anchor_mismatch")
    changed_ratio = _changed_ratio(parent_text, patch.edits)
    effective_limit = min(max(float(patch.max_changed_ratio), 0.0), 0.25)
    if changed_ratio > effective_limit:
        return _rejected(
            parent_text,
            "changed_ratio_exceeded",
            changed_ratio=changed_ratio,
        )
    candidate = parent_text
    for edit in sorted(patch.edits, key=lambda item: item.start, reverse=True):
        candidate = f"{candidate[: edit.start]}{edit.replacement}{candidate[edit.end :]}"
    return RewritePatchResult(
        accepted=True,
        parent_text=parent_text,
        candidate_text=candidate,
        parent_hash=text_hash(parent_text),
        candidate_hash=text_hash(candidate),
        changed_ratio=changed_ratio,
        failure_codes=(),
    )


def _rejected(
    parent_text: str,
    code: str,
    *,
    changed_ratio: float = 0.0,
) -> RewritePatchResult:
    parent_hash = text_hash(parent_text)
    return RewritePatchResult(
        accepted=False,
        parent_text=parent_text,
        candidate_text=parent_text,
        parent_hash=parent_hash,
        candidate_hash=parent_hash,
        changed_ratio=changed_ratio,
        failure_codes=(code,),
    )


def _changed_ratio(parent_text: str, edits: tuple[RewriteEdit, ...]) -> float:
    parent_size = len(parent_text.encode("utf-8"))
    if parent_size == 0:
        return 0.0
    changed_size = sum(
        max(
            len(parent_text[edit.start : edit.end].encode("utf-8")),
            len(edit.replacement.encode("utf-8")),
        )
        for edit in edits
    )
    return changed_size / parent_size


def _anchors_match(parent_text: str, edit: RewriteEdit) -> bool:
    before = parent_text[edit.start - len(edit.anchor_before) : edit.start]
    after = parent_text[edit.end : edit.end + len(edit.anchor_after)]
    return before == edit.anchor_before and after == edit.anchor_after


__all__ = [
    "ProtectedSpan",
    "RewriteEdit",
    "RewritePatch",
    "RewritePatchResult",
    "apply_rewrite_patch",
    "text_hash",
]
