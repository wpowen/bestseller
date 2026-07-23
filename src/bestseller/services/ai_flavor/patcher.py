"""Span-level patcher that applies localized fixes only at marked positions.

This is the *fix* half of the gate-and-fix loop. The detector hands over
a list of ``AiFlavorSpan`` records; the patcher produces a new string
plus an audit trail of every edit, without rewriting any text outside
the marked spans.

Strategy ladder per span (cheapest first):

1. **Static suggestion** — first non-empty entry in ``span.suggestions``
   replaces the matched text. Used for cluster excesses ("缓缓" → ""
   for de-duplication) and for explicit synonym swaps ("delve into" →
   "explore"). Zero LLM cost.
2. **LLM micro-rewrite** *(optional)* — when a ``MicroRewriter`` is
   passed in, it receives only the sentence-sized window plus the span
   highlight and returns a one-sentence rewrite. Capped via ``llm_budget``
   to bound spend per chapter.
3. **Preserve and block** — if an attempted optional rewrite fails, the
   original sentence is retained and returned as skipped.
   The residual detector span remains visible, so the quality gate blocks
   or routes the chapter for repair instead of deleting story text.

When no rewriter is configured, blocking spans remain untouched and are
reported for the terminal gate; story text is never silently deleted.

Edits are applied in **reverse** span order so character offsets stay
valid for the entire pass — a classic mistake-avoider.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Protocol

from bestseller.services.ai_flavor.types import AiFlavorSpan


logger = logging.getLogger(__name__)


class MicroRewriter(Protocol):
    """Pluggable single-sentence rewriter contract.

    Implementations must return a string that *replaces the entire
    sentence* — not just the span — so the patcher can drop in the
    rewrite as a single splice. Raise to signal "give up, preserve and
    block"; never return ``None`` or empty string.
    """

    def rewrite_sentence(
        self,
        *,
        sentence: str,
        flagged_text: str,
        why: str,
        language: str,
    ) -> str: ...


@dataclass(frozen=True)
class PatchEdit:
    """One concrete edit applied to the chapter text."""

    span: AiFlavorSpan
    strategy: str  # "static" | "sentence_drop" | "llm_rewrite"
    before: str
    after: str


@dataclass(frozen=True)
class PatchResult:
    """Outcome of applying a patch pass."""

    patched_text: str
    edits: tuple[PatchEdit, ...]
    skipped: tuple[AiFlavorSpan, ...]

    @property
    def edits_count(self) -> int:
        return len(self.edits)


def _first_static_suggestion(span: AiFlavorSpan) -> str | None:
    """Return the first non-``None`` suggestion. Empty string counts as
    a valid "delete the phrase" suggestion, so we only reject ``None``."""

    for s in span.suggestions:
        return s
    return None


def _shrink_excess_whitespace(text: str) -> str:
    """Collapse runs of blank lines / double spaces left over by deletes.

    Conservative — only normalises sequences a copyeditor would always
    fix, so we don't drift away from author voice. Run *once* at the end
    of the patch pass rather than per-edit, to keep edits inspectable.
    """

    import re

    text = re.sub(r" {2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip("\n").rstrip()


def apply_patches(
    content_md: str,
    spans: Iterable[AiFlavorSpan],
    *,
    language: str,
    llm_rewriter: MicroRewriter | None = None,
    llm_budget: int = 8,
    audit_hook: Callable[[PatchEdit], None] | None = None,
) -> PatchResult:
    """Apply localized fixes only at the marked spans.

    Parameters
    ----------
    content_md
        Original chapter markdown. Unmodified; the patched copy is
        returned in ``PatchResult.patched_text``.
    spans
        Iterable of detector-produced spans. The patcher sorts them
        internally — caller does not need to.
    language
        ``"zh"`` or ``"en"``; passed through to the optional rewriter
        so it can pick the right prompt.
    llm_rewriter
        Optional. When supplied, used at most ``llm_budget`` times per
        chapter to handle severity=block spans that have no static
        suggestion. Without a rewriter (or once the budget is spent)
        those spans remain in the draft and are reported as skipped so the
        terminal gate can route them to repair or human review.
    llm_budget
        Hard ceiling on micro-rewrite calls per chapter. Once exhausted,
        every remaining "needs LLM" span is preserved and reported as skipped.
    audit_hook
        Optional callback invoked once per edit. Used by
        ``ai_flavor_gate`` to write a per-chapter audit markdown file
        without coupling the pure patcher to filesystem I/O.
    """

    span_list = sorted(spans, key=lambda s: (s.start, s.end))
    if not span_list:
        return PatchResult(patched_text=content_md, edits=(), skipped=())

    # Reverse-order application keeps offsets valid.
    span_list_rev = list(reversed(span_list))

    edits: list[PatchEdit] = []
    skipped: list[AiFlavorSpan] = []
    llm_calls_used = 0
    working = content_md

    # Track sentence ranges already rewritten so sibling spans do not trigger
    # repeated rewrites against stale offsets.
    rewritten_sentences: set[tuple[int, int]] = set()

    for span in span_list_rev:
        if span.sentence_span in rewritten_sentences:
            # Sentence already rewritten by a sibling span; nothing to do.
            continue

        static = _first_static_suggestion(span)
        if static is not None:
            before = working[span.start : span.end]
            working = working[: span.start] + static + working[span.end :]
            edit = PatchEdit(span=span, strategy="static", before=before, after=static)
            edits.append(edit)
            if audit_hook is not None:
                audit_hook(edit)
            continue

        if span.severity == "block" and span.remove_sentence_on_block:
            # LLM rewrite first. If it fails or the budget is exhausted, keep
            # the original sentence so a quality block cannot silently delete
            # story content; the residual span routes to repair/human review.
            rewritten: str | None = None
            if llm_rewriter is not None and llm_calls_used < llm_budget:
                sent_start, sent_end = span.sentence_span
                sentence = working[sent_start:sent_end]
                try:
                    rewritten = llm_rewriter.rewrite_sentence(
                        sentence=sentence,
                        flagged_text=span.matched_text,
                        why=span.why,
                        language=language,
                    )
                    llm_calls_used += 1
                except Exception:
                    logger.warning(
                        (
                            "ai_flavor_gate: micro-rewrite failed for %s, "
                            "preserving sentence for gate block"
                        ),
                        span.rule_id,
                        exc_info=True,
                    )
                    rewritten = None

            if rewritten and rewritten.strip():
                sent_start, sent_end = span.sentence_span
                before = working[sent_start:sent_end]
                working = working[:sent_start] + rewritten + working[sent_end:]
                edit = PatchEdit(
                    span=span, strategy="llm_rewrite", before=before, after=rewritten
                )
            else:
                skipped.append(span)
                continue
            rewritten_sentences.add(span.sentence_span)
            edits.append(edit)
            if audit_hook is not None:
                audit_hook(edit)
            continue

        # No suggestion, not a block→drop: leave untouched (warn/info
        # span with no fix available — gate logs but doesn't enforce).
        skipped.append(span)

    return PatchResult(
        patched_text=_shrink_excess_whitespace(working),
        edits=tuple(edits),
        skipped=tuple(skipped),
    )
