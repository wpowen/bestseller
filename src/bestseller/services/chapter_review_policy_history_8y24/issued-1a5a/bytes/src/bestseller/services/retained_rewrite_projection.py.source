"""Pure, fail-closed projection of an already retained full-body return.

The caller must separately prove physical paid-run/source-body identity, report
actions, current semantic authority, operator permission, lineage and all fees.
Hashes here bind bytes, not that authority. Acceptance produces a candidate for
fresh review, never a grade, promotion, response-reuse permission or cache credit.

API v1 accepts only a decoded JSON array of exact {id,start,end,quote,sha256}
objects. Offsets are Python Unicode code points; hashes are lowercase SHA-256 of
unaltered UTF-8. Quotes must occur exactly once, including overlapping matches.
No JSON repair, coercion, newline conversion or Unicode normalization occurs.

Alignment uses difflib without autojunk and unique, immediately adjacent equal
context on both sides of each scope (document edges act as sentinels). Missing,
repeated, crossing or reordered scope evidence fails closed. Replacements are literal
return slices; all other bytes come from the parent. The existing patch contract
applies entire scopes at its fixed 0.25 byte-ratio limit. Resource limits bound
input size and difflib's matching-pair search, not any story-specific quantity.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from itertools import pairwise

from bestseller.services.rewrite_patch import (
    ProtectedSpan,
    RewriteEdit,
    RewritePatch,
    apply_rewrite_patch,
    text_hash,
)

_MAX_TEXT_CHARS = 100_000
_MAX_ID_CHARS = 256
_MAX_SPANS = 128
_MAX_MATCH_PAIRS = 2_000_000
_MAX_OPCODES = 512
_MAX_REORDER_FRAGMENTS = 10_000
_SPAN_KEYS = {"id", "start", "end", "quote", "sha256"}
_Opcode = tuple[str, int, int, int, int]


@dataclass(frozen=True)
class DiscardedOpcode:
    tag: str
    parent_start: int
    parent_end: int
    returned_start: int
    returned_end: int
    parent_sha256: str
    returned_sha256: str


@dataclass(frozen=True)
class ProjectedSpan:
    id: str
    start: int
    end: int
    parent_sha256: str
    returned_start: int
    returned_end: int
    replacement_sha256: str


@dataclass(frozen=True)
class RetainedRewriteReceipt:
    parent_sha256: str | None
    returned_sha256: str | None
    candidate_sha256: str | None
    applied_spans: tuple[ProjectedSpan, ...] = ()
    discarded_opcodes: tuple[DiscardedOpcode, ...] = ()
    changed_ratio: float = 0.0
    schema_version: str = field(default="retained-rewrite-projection/v1", init=False)
    offset_unit: str = field(default="unicode_code_points", init=False)
    hash_encoding: str = field(default="utf-8", init=False)
    fresh_review_required: bool = field(default=True, init=False)
    automatic_promotion_allowed: bool = field(default=False, init=False)


@dataclass(frozen=True)
class RetainedRewriteProjectionResult:
    accepted: bool
    candidate_text: str | None
    receipt: RetainedRewriteReceipt
    failure_codes: tuple[str, ...] = ()


@dataclass(frozen=True)
class _Span:
    id: str
    start: int
    end: int
    quote: str
    sha256: str


def _valid_hash(value: object) -> bool:
    return (
        type(value) is str
        and len(value) == 64
        and all(char in "0123456789abcdef" for char in value)
    )


def _unique(text: str, quote: str) -> bool:
    first = text.find(quote)
    return first >= 0 and text.find(quote, first + 1) == -1


def _parse_spans(parent: str, records: object) -> tuple[tuple[_Span, ...], str | None]:
    if type(records) is not list or not records:
        return (), "invalid_authorized_spans"
    if len(records) > _MAX_SPANS:
        return (), "resource_limit_exceeded"
    spans: list[_Span] = []
    ids: set[str] = set()
    for record in records:
        if type(record) is not dict or record.keys() != _SPAN_KEYS:
            return (), "invalid_span_record"
        identity, start, end, quote, digest = (
            record[key] for key in ("id", "start", "end", "quote", "sha256")
        )
        if (
            type(identity) is not str
            or len(identity) > _MAX_ID_CHARS
            or not identity.strip()
            or type(quote) is not str
            or len(quote) > len(parent)
            or not quote.strip()
            or type(start) is not int
            or type(end) is not int
            or not _valid_hash(digest)
        ):
            return (), "invalid_span_record"
        try:
            identity.encode("utf-8")
            quote_hash = text_hash(quote)
        except UnicodeEncodeError:
            return (), "invalid_span_record"
        if identity in ids:
            return (), "duplicate_span_id"
        if not 0 <= start < end <= len(parent):
            return (), "invalid_span_bounds"
        if parent[start:end] != quote:
            return (), "span_quote_mismatch"
        if quote_hash != digest:
            return (), "span_hash_mismatch"
        if not _unique(parent, quote):
            return (), "ambiguous_span_quote"
        ids.add(identity)
        spans.append(_Span(identity, start, end, quote, digest))
    spans.sort(key=lambda span: (span.start, span.end))
    if any(right.start < left.end for left, right in pairwise(spans)):
        return (), "overlapping_spans"
    return tuple(spans), None


def _discarded(parent: str, returned: str, ops: list[_Opcode]) -> tuple[DiscardedOpcode, ...]:
    return tuple(
        DiscardedOpcode(tag, i, end, j, stop, text_hash(parent[i:end]), text_hash(returned[j:stop]))
        for tag, i, end, j, stop in ops
        if tag != "equal"
    )


def _boundary(
    parent: str,
    returned: str,
    ops: list[_Opcode],
    position: int,
    limit: int,
    *,
    before: bool,
) -> tuple[int | None, str]:
    if before and position == 0:
        return 0, ""
    if not before and position == len(parent):
        return len(returned), ""
    for tag, i, end, j, _stop in ops:
        if tag != "equal":
            continue
        if before and i < position <= end:
            anchor = parent[max(i, limit) : position]
        elif not before and i <= position < end:
            anchor = parent[position : min(end, limit)]
        else:
            continue
        if anchor and _unique(parent, anchor) and _unique(returned, anchor):
            return j + position - i, anchor
    return None, ""


def _ambiguous_edit_boundary(
    parent: str, returned: str, ops: list[_Opcode], spans: tuple[_Span, ...]
) -> bool:
    """Check all equivalent shifts of an edit through adjacent equal context.

    In repeated text an indel can rotate left/right without changing the reply.
    Unique long anchors do not resolve which side owns that inserted/deleted
    character. The two neighboring equal blocks bound the scan, so each equal
    block is scanned at most twice. Replacements can shift only where both their
    removed and inserted fragments have the same periodic edge characters.
    """
    for index, (tag, start, end, j, stop) in enumerate(ops):
        if tag == "equal":
            continue
        fragments = [text for text in (parent[start:end], returned[j:stop]) if text]
        left_limit = ops[index - 1][1] if index and ops[index - 1][0] == "equal" else start
        right_limit = (
            ops[index + 1][2] if index + 1 < len(ops) and ops[index + 1][0] == "equal" else end
        )
        left, right = start, end
        while left > left_limit and all(
            parent[left - 1] == text[(left - start - 1) % len(text)] for text in fragments
        ):
            left -= 1
        while right < right_limit and all(
            parent[right] == text[(right - end) % len(text)] for text in fragments
        ):
            right += 1
        for span in spans:
            if start == end:
                if left <= span.start <= right or left <= span.end <= right:
                    return True
            elif left < span.end and span.start < right:
                if left < span.start or right > span.end:
                    return True
    return False


def _reorder_failure(
    parent: str, returned: str, ops: list[_Opcode], owners: list[str | None]
) -> str | None:
    """Check maximal literal matches that cross an authorization boundary.

    A single longest match can be repeated and hide a shorter unique move.
    Enumerating starts of matching diagonals includes tied alternatives too.
    Both-outside edits are discarded, not vetoes of an independently anchored
    repair; moves within one authorized scope are also within that permission.
    A unique parent fragment reused across owners is a move/duplication conflict
    even if the return contains multiple copies of that fragment.
    Character-pair work is bounded by the preflight matching-pair budget; a
    separate cap bounds full-body uniqueness searches and the memo set.
    """
    positions: dict[str, list[tuple[int, int, int, int]]] = {}
    for index, (_tag, _start, _end, j, stop) in enumerate(ops):
        for position in range(j, stop):
            positions.setdefault(returned[position], []).append((index, position, j, stop))
    seen: set[str] = set()
    for index, (_tag, start, end, _j, _stop) in enumerate(ops):
        for i in range(start, end):
            for other, j, reply_start, reply_end in positions.get(parent[i], ()):
                if owners[other] == owners[index]:
                    continue
                if i > start and j > reply_start and parent[i - 1] == returned[j - 1]:
                    continue
                size = 1
                while i + size < end and j + size < reply_end:
                    if parent[i + size] != returned[j + size]:
                        break
                    size += 1
                fragment = parent[i : i + size]
                if fragment in seen:
                    continue
                if len(seen) >= _MAX_REORDER_FRAGMENTS:
                    return "resource_limit_exceeded"
                seen.add(fragment)
                if _unique(parent, fragment):
                    return "reorder_conflict"
    return None


def project_retained_rewrite(
    parent_text: str,
    returned_text: str,
    *,
    authorized_spans: list[dict[str, object]],
    expected_parent_sha256: str,
    expected_return_sha256: str,
) -> RetainedRewriteProjectionResult:
    """Return a deterministic candidate/receipt, or the unchanged parent on failure.

    Malformed non-string parents yield candidate_text=None. Validation failures
    before alignment have no opcode inventory; later failures discard every
    computed change. Successful receipts inventory every out-of-scope opcode
    using offsets and hashes only, with no body/quote/replacement text.
    """
    parent_hash = returned_hash = None
    ops: list[_Opcode] = []

    def reject(code: str, *, changed_ratio: float = 0.0) -> RetainedRewriteProjectionResult:
        return RetainedRewriteProjectionResult(
            accepted=False,
            candidate_text=parent_text if type(parent_text) is str else None,
            receipt=RetainedRewriteReceipt(
                parent_hash,
                returned_hash,
                parent_hash,
                discarded_opcodes=_discarded(parent_text, returned_text, ops),
                changed_ratio=changed_ratio,
            ),
            failure_codes=(code,),
        )

    if type(parent_text) is not str or type(returned_text) is not str:
        return reject("invalid_text")
    if max(len(parent_text), len(returned_text)) > _MAX_TEXT_CHARS:
        return reject("resource_limit_exceeded")
    if not parent_text.strip() or not returned_text.strip():
        return reject("empty_text")
    try:
        parent_hash, returned_hash = text_hash(parent_text), text_hash(returned_text)
    except UnicodeEncodeError:
        return reject("invalid_utf8")
    if not _valid_hash(expected_parent_sha256) or not _valid_hash(expected_return_sha256):
        return reject("invalid_expected_hash")
    if parent_hash != expected_parent_sha256:
        return reject("parent_hash_mismatch")
    if returned_hash != expected_return_sha256:
        return reject("return_hash_mismatch")
    spans, failure = _parse_spans(parent_text, authorized_spans)
    if failure:
        return reject(failure)
    parent_counts, returned_counts = Counter(parent_text), Counter(returned_text)
    if (
        sum(count * returned_counts[char] for char, count in parent_counts.items())
        > _MAX_MATCH_PAIRS
    ):
        return reject("resource_limit_exceeded")
    ops = SequenceMatcher(None, parent_text, returned_text, autojunk=False).get_opcodes()
    if len(ops) > _MAX_OPCODES:
        return reject("resource_limit_exceeded")

    changed_scopes: set[str] = set()
    discarded_ops: list[_Opcode] = []
    changed_ops = [op for op in ops if op[0] != "equal"]
    op_owners: list[str | None] = []
    for op in changed_ops:
        _tag, start, end, _j, _stop = op
        owners = []
        for span in spans:
            if start == end:
                if start in (span.start, span.end):
                    return reject("boundary_crossing")
                touches = span.start < start < span.end
            else:
                touches = start < span.end and span.start < end
            if touches:
                if start < span.start or end > span.end:
                    return reject("boundary_crossing")
                owners.append(span.id)
        if owners:
            changed_scopes.update(owners)
        else:
            discarded_ops.append(op)
        op_owners.append(owners[0] if owners else None)
    if _ambiguous_edit_boundary(parent_text, returned_text, ops, spans):
        return reject("ambiguous_alignment")
    if len(changed_scopes) != len(spans):
        return reject("missing_repair")

    failure = _reorder_failure(parent_text, returned_text, changed_ops, op_owners)
    if failure:
        return reject(failure)

    edits: list[RewriteEdit] = []
    applied: list[ProjectedSpan] = []
    protected: list[ProtectedSpan] = []
    previous_end = previous_return_end = 0
    for index, span in enumerate(spans):
        next_start = spans[index + 1].start if index + 1 < len(spans) else len(parent_text)
        start, before = _boundary(
            parent_text, returned_text, ops, span.start, previous_end, before=True
        )
        end, after = _boundary(parent_text, returned_text, ops, span.end, next_start, before=False)
        if start is None or end is None:
            return reject("ambiguous_alignment")
        if start > end or start < previous_return_end:
            return reject("reorder_conflict")
        replacement = returned_text[start:end]
        if not replacement.strip():
            return reject("empty_replacement")
        if replacement == span.quote:
            return reject("missing_repair")
        edits.append(RewriteEdit(span.start, span.end, span.sha256, before, after, replacement))
        applied.append(
            ProjectedSpan(
                span.id, span.start, span.end, span.sha256, start, end, text_hash(replacement)
            )
        )
        if previous_end < span.start:
            protected.append(
                ProtectedSpan(
                    previous_end, span.start, text_hash(parent_text[previous_end : span.start])
                )
            )
        previous_end, previous_return_end = span.end, end
    if previous_end < len(parent_text):
        protected.append(
            ProtectedSpan(previous_end, len(parent_text), text_hash(parent_text[previous_end:]))
        )
    patched = apply_rewrite_patch(
        parent_text,
        RewritePatch(parent_hash, tuple(edits), tuple(protected), max_changed_ratio=0.25),
    )
    if not patched.accepted:
        return reject(patched.failure_codes[0], changed_ratio=patched.changed_ratio)
    return RetainedRewriteProjectionResult(
        accepted=True,
        candidate_text=patched.candidate_text,
        receipt=RetainedRewriteReceipt(
            parent_hash,
            returned_hash,
            patched.candidate_hash,
            tuple(applied),
            _discarded(parent_text, returned_text, discarded_ops),
            patched.changed_ratio,
        ),
    )


__all__ = [
    "DiscardedOpcode",
    "ProjectedSpan",
    "RetainedRewriteProjectionResult",
    "RetainedRewriteReceipt",
    "project_retained_rewrite",
]
