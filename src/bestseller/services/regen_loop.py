"""L4.5 Regeneration Loop.

When L4 (output_validator) + L5 (chapter_validator) flag a blocking violation
on a draft, the responsible generator is given a *bounded* number of chances
to fix the problem — with the violation's ``prompt_feedback`` stitched into
the next regeneration prompt.

This module is the *orchestrator* for that retry loop. It is deliberately
pure-logic and generator-agnostic:

* The caller provides an async ``regenerator`` callable that takes the
  feedback string and returns a new candidate text.
* The caller provides an async ``validator`` callable that returns a
  ``QualityReport`` for a candidate.
* The loop runs up to ``budget`` attempts (default 3). When the budget is
  exhausted while violations remain, it raises ``RegenerationExhausted``
  with the full attempt trail so the caller can persist forensic data.

Design notes:

* **Bounded cost** — Each call honours a per-chapter budget (3) AND an
  optional ``global_budget_remaining`` count so the pipeline can cap the
  total regen spend across the project (plan §9 decision 1, 12-per-book).
* **Attempt trail** — ``RegenAttempt`` records every (feedback → output →
  report) triple. Failure telemetry feeds into L8 scorecard later.
* **No side effects** — the loop does not persist anything or mutate the
  DB. Callers own persistence; the loop is reentrant-safe.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from bestseller.services.output_validator import QualityReport, Violation


logger = logging.getLogger(__name__)


DEFAULT_BUDGET_PER_CHAPTER = 3
DEFAULT_GLOBAL_BUDGET = 12


# ---------------------------------------------------------------------------
# Callable protocols.
# ---------------------------------------------------------------------------


# ``Regenerator`` takes the assembled remediation feedback and returns the
# next candidate text. Async because LLM calls are async everywhere in the
# rest of the codebase.
Regenerator = Callable[[str], Awaitable[str]]

# ``Validator`` takes the candidate text and returns a QualityReport. Async
# because L5 checks may need to load data from the DB (roster, prior-chapter
# tail, etc.) even though L4 checks are CPU-only.
Validator = Callable[[str], Awaitable[QualityReport]]


# ---------------------------------------------------------------------------
# Data classes.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RegenAttempt:
    """Trail entry — what was fed back, what came out, how it validated.

    ``feedback`` is empty for the first entry (that's the *initial* output
    before any regeneration). Subsequent entries carry the remediation
    prompt that was injected.
    """

    attempt_no: int
    feedback: str
    output: str
    report: QualityReport


@dataclass(frozen=True)
class RegenerationResult:
    """Happy-path return value.

    ``attempts`` always includes at least one entry (the initial candidate).
    When ``attempts[-1].report.blocks_write`` is False, the loop succeeded.
    """

    final_output: str
    attempts: tuple[RegenAttempt, ...]

    @property
    def regen_count(self) -> int:
        """Number of regenerations performed (initial attempt excluded)."""

        return max(0, len(self.attempts) - 1)

    @property
    def final_report(self) -> QualityReport:
        return self.attempts[-1].report

    def violation_codes_history(self) -> tuple[tuple[str, ...], ...]:
        """Per-attempt violation code lists — useful for regression diagnosis."""

        return tuple(
            tuple(v.code for v in att.report.violations)
            for att in self.attempts
        )


class RegenerationExhausted(RuntimeError):
    """Raised when the regen budget is exhausted and the last candidate
    still blocks write.

    Carries the full attempt trail so callers can persist the failed draft
    + all intermediate attempts for human inspection (plan §L6 "dump to
    /rejected_drafts/").
    """

    def __init__(
        self,
        attempts: tuple[RegenAttempt, ...],
        *,
        budget: int,
        reason: str = "regeneration_budget_exhausted",
    ) -> None:
        self.attempts = attempts
        self.budget = budget
        self.reason = reason
        last_codes = (
            [v.code for v in attempts[-1].report.violations]
            if attempts
            else []
        )
        super().__init__(
            f"{reason}: {len(attempts)} attempts (budget={budget}), "
            f"final violations={last_codes}"
        )


@dataclass
class GlobalBudget:
    """Mutable counter shared across chapters for project-level spend caps.

    Tracked at the caller layer so the pipeline can stop the regen loop
    from consuming more than ``global_budget`` attempts across all chapters
    of a single project run. Plan §9 decision 1: 12 per book.
    """

    total: int = DEFAULT_GLOBAL_BUDGET
    used: int = 0

    @property
    def remaining(self) -> int:
        return max(0, self.total - self.used)

    def consume(self, n: int = 1) -> None:
        self.used += n

    def would_exhaust(self, n: int = 1) -> bool:
        return self.used + n > self.total


# ---------------------------------------------------------------------------
# Loop driver.
# ---------------------------------------------------------------------------


async def regenerate_until_valid(
    *,
    initial_output: str,
    initial_report: QualityReport,
    regenerator: Regenerator,
    validator: Validator,
    budget: int = DEFAULT_BUDGET_PER_CHAPTER,
    global_budget: GlobalBudget | None = None,
    context_label: str = "chapter",
) -> RegenerationResult:
    """Drive the regen-with-feedback loop.

    Args:
        initial_output: The LLM's first draft (already generated).
        initial_report: The L4+L5 validation result for ``initial_output``.
            If it doesn't block writes, the loop is a no-op.
        regenerator: Async callable that accepts remediation feedback and
            produces the next candidate text.
        validator: Async callable that returns a ``QualityReport`` for the
            candidate.
        budget: Max regenerations allowed per call (does not count the
            initial attempt). Default 3 — plan §9 decision 1.
        global_budget: Optional project-level spend cap. Consumed per
            regeneration attempt; if exhausted mid-loop, the loop exits
            early without raising so Phase 1 degrades gracefully.
        context_label: Short identifier used in logs (``chapter-42``,
            ``scene-42-3``). Empty string allowed.

    Returns:
        RegenerationResult with the final output + full attempt trail.

    Raises:
        RegenerationExhausted when the per-call budget is spent AND the
        final candidate still blocks write. Global-budget exhaustion does
        NOT raise (plan §9 cost-governor policy); it returns the best
        candidate so far and lets the caller decide whether to ship.
    """

    attempts: list[RegenAttempt] = [
        RegenAttempt(
            attempt_no=1,
            feedback="",
            output=initial_output,
            report=initial_report,
        )
    ]

    if not initial_report.blocks_write:
        logger.debug(
            "%s: initial output passed quality gate; no regen needed", context_label
        )
        return RegenerationResult(
            final_output=initial_output,
            attempts=tuple(attempts),
        )

    for retry_idx in range(budget):
        # Honour global budget so a chain of bad chapters can't drain the
        # LLM spend cap. Exit gracefully rather than raising.
        if global_budget is not None and global_budget.would_exhaust(1):
            logger.warning(
                "%s: global regen budget exhausted (used %d/%d); "
                "stopping before attempt %d",
                context_label,
                global_budget.used,
                global_budget.total,
                retry_idx + 2,
            )
            break

        feedback = attempts[-1].report.feedback_for_regen()
        logger.info(
            "%s: regen attempt %d/%d — feedback codes=%s",
            context_label,
            retry_idx + 1,
            budget,
            [v.code for v in attempts[-1].report.violations],
        )

        try:
            new_output = await regenerator(feedback)
        except Exception as exc:
            logger.warning(
                "%s: regenerator raised on attempt %d: %s",
                context_label,
                retry_idx + 1,
                exc,
            )
            raise RegenerationExhausted(
                attempts=tuple(attempts),
                budget=budget,
                reason=f"regenerator_exception: {exc}",
            ) from exc

        try:
            new_report = await validator(new_output)
        except Exception as exc:
            logger.warning(
                "%s: validator raised on attempt %d: %s",
                context_label,
                retry_idx + 1,
                exc,
            )
            raise RegenerationExhausted(
                attempts=tuple(attempts),
                budget=budget,
                reason=f"validator_exception: {exc}",
            ) from exc

        attempts.append(
            RegenAttempt(
                attempt_no=retry_idx + 2,
                feedback=feedback,
                output=new_output,
                report=new_report,
            )
        )
        if global_budget is not None:
            global_budget.consume(1)

        if not new_report.blocks_write:
            logger.info(
                "%s: regen succeeded after %d attempt(s)",
                context_label,
                retry_idx + 1,
            )
            return RegenerationResult(
                final_output=new_output,
                attempts=tuple(attempts),
            )

    # If we fall through, the loop either hit the per-call budget with
    # failures still present, OR the global budget ran out. In the former
    # case we raise; in the latter we return the best-effort output so the
    # caller can decide what to do (dump to rejected, open an audit task).
    # would_exhaust(1) is True when the global budget can't support even
    # one more attempt — that's the signal that the loop broke early due
    # to the global governor rather than consuming all its per-call retries.
    if (
        global_budget is not None
        and global_budget.would_exhaust(1)  # global cannot fund another attempt
        and attempts[-1].report.blocks_write
    ):
        logger.warning(
            "%s: returning best-effort output because global budget exhausted; "
            "final report still blocks write",
            context_label,
        )
        return RegenerationResult(
            final_output=attempts[-1].output,
            attempts=tuple(attempts),
        )

    raise RegenerationExhausted(
        attempts=tuple(attempts),
        budget=budget,
    )


# ---------------------------------------------------------------------------
# Helpers for building feedback from heterogeneous sources.
# ---------------------------------------------------------------------------


def compose_feedback_from_violations(
    violations: tuple[Violation, ...],
    *,
    header: str | None = None,
) -> str:
    """Build a standalone feedback string from a raw violation list.

    Useful when the caller has multiple ``QualityReport``s it wants to
    merge (e.g., a scene-level report + chapter-level report) before
    feeding a single prompt back to the regenerator.
    """

    if not violations:
        return ""
    default_header = (
        "上次生成的内容未通过质量校验。请按以下整改要求重写，"
        "保持剧情、人物、场景不变：\n"
    )
    lines = [
        f"{idx}) [{v.code}] {v.detail}\n   整改：{v.prompt_feedback}"
        for idx, v in enumerate(violations, 1)
    ]
    return (header or default_header) + "\n".join(lines)


__all__ = [
    "DEFAULT_BUDGET_PER_CHAPTER",
    "DEFAULT_GLOBAL_BUDGET",
    "GlobalBudget",
    "RegenAttempt",
    "RegenerationExhausted",
    "RegenerationResult",
    "Regenerator",
    "Validator",
    "compose_feedback_from_violations",
    "regenerate_until_valid",
]
