"""Trigger-classified retry budgets for every automatic recovery path.

Why this module exists
----------------------
2026-07-25: a volume-outline gate failed, the self-heal path cleared the pause
60s later and retried, the same gate failed again, forever — 880k tokens. Two
root causes, and both are shape problems rather than tuning problems:

1. The auto-resume path had **no give-up budget** at all.
2. The one counter that did exist lived in a planning key that a cache purge
   wiped, so the partial guard silently reset itself.

The fix therefore has two rules, and both are enforced here rather than at each
call site:

* **The counter lives in the same persisted mapping as the state it guards.**
  ``load_chain``/``store_chain`` read and write a nested block inside whatever
  metadata dict already carries the pause/block state. Nothing can purge the
  budget without also purging the failure it is guarding, which would end the
  loop anyway.
* Per-chain credit may change with new feedback, but **every dispatch consumes
  the persisted lifetime ceiling**. Recovery, validator and manual triggers
  cannot replenish that ceiling. Paid requests have a separate atomic ledger
  at the LLM boundary; this ledger bounds recovery dispatches only.

This mirrors DeterminFlow's failure-chain model (``failure_policy.py``), which
solved the identical class of runaway loop.

The module is intentionally pure: it takes mappings and returns new mappings, so
it can be unit-tested without a database and reused by chapter-scoped,
project-scoped and gate-scoped callers alike.
"""

from __future__ import annotations

import os
import asyncio
from datetime import datetime, timezone
from collections.abc import Mapping
from dataclasses import dataclass, replace
from enum import Enum
from typing import Any, Final

from bestseller.services.budget import ExecutionBudgetError, ExecutionOperationBusyError, ExecutionProviderNotSentError

__all__ = [
    "ABSOLUTE_MAX_AUTO_ATTEMPTS",
    "DEFAULT_AUTO_ATTEMPT_LIMIT",
    "RetryBudget",
    "RetryChain",
    "RetryDecision",
    "RetryOutcome",
    "RetryTrigger",
    "evaluate_retry",
    "generation_gate_budget",
    "generation_gate_scope",
    "load_chain",
    "store_chain",
    "execution_terminal_code",
    "execution_terminal_metadata",
    "ExecutionRecoveryBlockedError",
    "assert_execution_scope_open",
    "project_execution_scope",
]


class ExecutionRecoveryBlockedError(ExecutionBudgetError):
    """Fail-closed recovery control flow; shares the budget error protocol."""

    terminal = True

    def __init__(self, code: str, message: str) -> None:
        # Keep provider/database details in chained diagnostics, not receipts.
        super().__init__(code)


def project_execution_scope(project: Any) -> str:
    """Preserve the code-stamped creation scope when a book acquires an id."""
    metadata = getattr(project, "metadata_json", None) or {}
    scope = metadata.get("execution_scope_id") if isinstance(metadata, Mapping) else None
    if isinstance(scope, str) and scope.startswith(("project:", "conception:", "conception-continuation:", "workflow:")):
        if 0 < len(scope) <= 256:
            return scope
    return f"project:{project.id}"


def execution_terminal_code(value: Any) -> str | None:
    """Recognise execution stops before ordinary gate/retry classification.

    Structured receipts survive restarts; causal traversal preserves terminal
    budget exceptions wrapped by legacy planners. Do not search arbitrary prose
    for words such as 'cancel' or 'budget'.
    """
    if isinstance(value, BaseException):
        pending = [value]
        seen: set[int] = set()
        while pending:
            error = pending.pop()
            if id(error) in seen:
                continue
            seen.add(id(error))
            if isinstance(error, (ExecutionProviderNotSentError, ExecutionOperationBusyError)):
                # These trusted admission errors already establish no new
                # send. Their internal cause must not reclassify the wrapper.
                continue
            code = getattr(error, "code", None)
            if getattr(error, "terminal", False) is True and code:
                return str(code)
            if isinstance(error, asyncio.CancelledError):
                return "execution_cancelled"
            pending.extend(e for e in (error.__cause__, error.__context__)
                           if isinstance(e, BaseException))
        return None
    if not isinstance(value, Mapping):
        return None
    for key in ("execution_terminal", "execution_budget"):
        receipt = value.get(key)
        if isinstance(receipt, Mapping):
            code = receipt.get("code") or receipt.get("reason")
            status = str(receipt.get("status") or "").lower()
            if receipt.get("terminal") is True or status in {
                "exhausted", "unknown", "cancelled", "ledger_unavailable", "expired",
            }:
                return str(code or f"execution_{status or 'blocked'}")
    if value.get("terminal") is True and value.get("code"):
        return str(value["code"])
    # Historical terminal states predate structured execution receipts.
    for key in ("production_pause_reason", "last_generation_gate_reason",
                "reason", "stop_reason", "current_stage", "status"):
        code = str(value.get(key) or "").split(":", 1)[0].strip().lower()
        if code.startswith("execution_") or code in {
            "execution_cancelled", "generation_gate_retry_budget_exhausted",
            "generation_gate_budget_exhausted", "outline_replan_auto_retry_exhausted",
            "outline_replan_retry_exhausted", "self_heal_no_actionable_progress",
            "chapter_repair_budget_exhausted", "chapter_repair_attempts_exhausted",
            "chapter_first_repair_budget_exhausted",
        }:
            return code
    for key in ("generation_gate_retry_budget_exhausted", "outline_replan_auto_retry_exhausted",
                "outline_replan_retry_exhausted", "chapter_first_repair_budget_exhausted"):
        if value.get(key) is True:
            return key
    return None


async def assert_execution_scope_open(session: Any, scope_id: str) -> None:
    """Read the authoritative ledger, including after a swallowed exception.

    This does not create a budget or open new credit. A missing row means no
    paid work has entered this scope yet; a missing ledger/table fails closed.
    """
    from bestseller.infra.db.models import ExecutionBudgetModel
    try:
        row = await session.get(ExecutionBudgetModel, scope_id)
    except Exception as error:
        raise ExecutionBudgetError("execution_ledger_unavailable", scope_id) from error
    if row is None:
        return
    from bestseller.services.budget import execution_scope_block_code
    code = execution_scope_block_code(row)
    if code is not None:
        raise ExecutionBudgetError(code, scope_id)


def execution_terminal_metadata(metadata: Mapping[str, Any] | None,
                                error: BaseException, *,
                                verified_workflow_id: str | None = None) -> dict[str, Any]:
    """Preserve stop evidence; the SQL caller must authenticate any binding.

    An old workflow_id is not authority. Only a caller-verified identity may
    appear on the current terminal; the original receipt remains in history.
    """
    updated = dict(metadata or {})
    code = execution_terminal_code(error)
    if code is None:
        raise ValueError("An execution terminal error is required")
    old = updated.get("execution_terminal")
    terminal = dict(old) if isinstance(old, Mapping) else {}
    terminal.pop("workflow_id", None)
    terminal.update(terminal=True, code=code)
    if verified_workflow_id is not None:
        terminal["workflow_id"] = verified_workflow_id
    if old is not None and old != terminal:
        history = list(updated.get("execution_terminal_history") or [])
        if old not in history:
            history.append(old)
        updated["execution_terminal_history"] = history
    updated.update({
        "execution_terminal": terminal,
        "production_paused": True,
        "production_pause_reason": code,
        "requires_machine_repair": False,
        "requires_human_review": True,
    })
    return updated


class RetryTrigger(str, Enum):
    """Why this attempt is happening. Determines the budget semantics."""

    INITIAL = "initial"
    """First attempt of a unit of work. Never consumes budget."""

    AUTO = "auto"
    """Unattended retry; consumes chain credit and lifetime dispatch credit."""

    MANUAL = "manual"
    """A human asked for this. Fresh chain credit, within the same lifetime ceiling."""

    VALIDATOR = "validator"
    """A downstream judge/gate rejected upstream work and asked for rework.

    Also opens a fresh chain: a *specific* rejection with feedback is new
    information, unlike blindly re-running the same failing step.
    """

    RECOVERY = "recovery"
    """Re-issued after a crash/restart interrupted an in-flight attempt.

    Does not consume chain credit, but consumes lifetime dispatch credit.
    This permission never authorizes resending an unknown provider result.
    """


class RetryOutcome(str, Enum):
    """What the caller is allowed to do next."""

    PROCEED = "proceed"
    EXHAUSTED = "exhausted"


#: Absolute ceiling. Config may lower the per-scope limit but never raise it
#: past this — a misconfigured YAML must not be able to re-create the 880k-token
#: loop.
ABSOLUTE_MAX_AUTO_ATTEMPTS: Final[int] = 20

#: Default unattended-retry budget per failure chain.
DEFAULT_AUTO_ATTEMPT_LIMIT: Final[int] = 6

#: Key under which a chain is nested inside the caller's metadata mapping.
_CHAIN_BLOCK_KEY: Final[str] = "retry_chain"


@dataclass(frozen=True)
class RetryBudget:
    """How much unattended retrying a scope is allowed."""

    auto_attempt_limit: int = DEFAULT_AUTO_ATTEMPT_LIMIT
    lifetime_attempt_limit: int = ABSOLUTE_MAX_AUTO_ATTEMPTS

    @property
    def effective_lifetime_limit(self) -> int:
        return max(0, min(int(self.lifetime_attempt_limit), ABSOLUTE_MAX_AUTO_ATTEMPTS))

    @property
    def effective_limit(self) -> int:
        """Config value clamped into ``[0, ABSOLUTE_MAX_AUTO_ATTEMPTS]``."""

        return max(0, min(int(self.auto_attempt_limit), ABSOLUTE_MAX_AUTO_ATTEMPTS))


@dataclass(frozen=True)
class RetryChain:
    """One failure chain: a run of attempts not interrupted by new credit.

    ``auto_attempts_used`` resets when a human or a validator re-opens the
    chain; ``chain_serial`` and ``lifetime_attempts`` never reset, so an audit
    can still see the full history of a pathological chapter.
    """

    auto_attempts_used: int = 0
    chain_serial: int = 0
    lifetime_attempts: int = 0
    last_trigger: RetryTrigger | None = None
    last_reason: str | None = None

    def remaining(self, budget: RetryBudget) -> int:
        return max(0, min(budget.effective_limit - self.auto_attempts_used,
                          budget.effective_lifetime_limit - self.lifetime_attempts))

    def to_dict(self) -> dict[str, Any]:
        return {
            "auto_attempts_used": self.auto_attempts_used,
            "chain_serial": self.chain_serial,
            "lifetime_attempts": self.lifetime_attempts,
            "last_trigger": self.last_trigger.value if self.last_trigger else None,
            "last_reason": self.last_reason,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any] | None) -> "RetryChain":
        if not isinstance(raw, Mapping):
            return cls()
        trigger_raw = raw.get("last_trigger")
        try:
            trigger = RetryTrigger(trigger_raw) if trigger_raw else None
        except ValueError:
            trigger = None
        auto_attempts_used = _non_negative_int(raw.get("auto_attempts_used"))
        return cls(
            auto_attempts_used=auto_attempts_used,
            chain_serial=_non_negative_int(raw.get("chain_serial")),
            # Partial/legacy checkpoints cannot erase attempts already proved
            # by the per-chain counter and reopen lifetime credit on recovery.
            lifetime_attempts=max(auto_attempts_used, _non_negative_int(raw.get("lifetime_attempts"))),
            last_trigger=trigger,
            last_reason=_optional_str(raw.get("last_reason")),
        )


@dataclass(frozen=True)
class RetryDecision:
    """Result of asking "may I run this attempt?"."""

    outcome: RetryOutcome
    chain: RetryChain
    budget: RetryBudget
    trigger: RetryTrigger
    detail: str

    @property
    def allowed(self) -> bool:
        return self.outcome is RetryOutcome.PROCEED

    @property
    def exhausted(self) -> bool:
        return self.outcome is RetryOutcome.EXHAUSTED

    def to_event_payload(self) -> dict[str, Any]:
        """Structured payload for progress events and logs."""

        return {
            "outcome": self.outcome.value,
            "trigger": self.trigger.value,
            "auto_attempts_used": self.chain.auto_attempts_used,
            "auto_attempt_limit": self.budget.effective_limit,
            "remaining": self.chain.remaining(self.budget),
            "chain_serial": self.chain.chain_serial,
            "lifetime_attempts": self.chain.lifetime_attempts,
            "lifetime_attempt_limit": self.budget.effective_lifetime_limit,
            "detail": self.detail,
        }


def generation_gate_budget() -> RetryBudget:
    """Budget for automatic retries after a generation gate blocked a book.

    Reads the same environment knob as ``worker.self_heal`` so the two paths
    that can restart a gate-blocked book cannot be tuned apart. Splitting this
    into two constants is how the worker path ended up unbudgeted while the
    self-heal path looked guarded.
    """

    raw = os.getenv("BESTSELLER_GENERATION_GATE_MAX_AUTO_RESUMES", "3")
    try:
        limit = int(raw)
    except (TypeError, ValueError):
        limit = 3
    return RetryBudget(auto_attempt_limit=limit)


def generation_gate_scope(reason: str | None) -> str:
    """One persisted project gate scope, independent of changing validator codes."""
    return "generation_gate"


def _non_negative_int(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def load_chain(container: Mapping[str, Any] | None, *, scope: str) -> RetryChain:
    """Read the chain for ``scope`` out of a metadata mapping.

    ``container`` is the same dict that carries the guarded state (chapter
    metadata, project metadata, gate block payload). Reading a missing or
    malformed block yields a pristine chain rather than raising — a corrupt
    counter must not become a second failure mode on top of the first.
    """

    if not isinstance(container, Mapping):
        return RetryChain()
    block = container.get(_CHAIN_BLOCK_KEY)
    legacy_resume_count = _non_negative_int(container.get("generation_gate_auto_resume_count"))
    if not isinstance(block, Mapping):
        if scope == "generation_gate" and legacy_resume_count:
            return RetryChain(auto_attempts_used=legacy_resume_count,
                              lifetime_attempts=legacy_resume_count)
        return RetryChain()
    if scope == "generation_gate" and scope not in block:
        # Migrate on read, then store the canonical scope before dispatch.
        # Keep legacy entries as evidence and never sum them again once the
        # canonical row is present.
        legacy = [RetryChain.from_dict(raw) for key, raw in block.items()
                  if str(key).startswith("generation_gate:")]
        if legacy or legacy_resume_count:
            return RetryChain(
                auto_attempts_used=max(legacy_resume_count, sum(c.auto_attempts_used for c in legacy)),
                lifetime_attempts=max(legacy_resume_count,
                    sum(max(c.lifetime_attempts, c.auto_attempts_used) for c in legacy)),
                chain_serial=max((c.chain_serial for c in legacy), default=0),
            )
    return RetryChain.from_dict(block.get(scope))


def store_chain(
    container: Mapping[str, Any] | None,
    *,
    scope: str,
    chain: RetryChain,
) -> dict[str, Any]:
    """Return a new mapping with ``chain`` persisted under ``scope``.

    Never mutates ``container`` — callers assign the result back so that the
    counter lands in the same write as the state it guards.
    """

    base: dict[str, Any] = dict(container) if isinstance(container, Mapping) else {}
    existing = base.get(_CHAIN_BLOCK_KEY)
    block: dict[str, Any] = dict(existing) if isinstance(existing, Mapping) else {}
    block[scope] = chain.to_dict()
    base[_CHAIN_BLOCK_KEY] = block
    return base


def evaluate_retry(
    chain: RetryChain,
    *,
    trigger: RetryTrigger,
    budget: RetryBudget | None = None,
    reason: str | None = None,
) -> RetryDecision:
    """Decide whether an attempt may run, and return the advanced chain.

    The returned chain must be persisted by the caller *before* the attempt
    runs. Persisting after would re-create the original bug: a crash mid-attempt
    would lose the increment and the loop would be immortal again.
    """

    resolved_budget = budget or RetryBudget()
    limit = resolved_budget.effective_limit

    if chain.lifetime_attempts >= resolved_budget.effective_lifetime_limit:
        return RetryDecision(
            outcome=RetryOutcome.EXHAUSTED, chain=chain, budget=resolved_budget,
            trigger=trigger,
            detail=f"lifetime retry budget exhausted ({chain.lifetime_attempts}/"
                   f"{resolved_budget.effective_lifetime_limit})",
        )

    if trigger is RetryTrigger.INITIAL:
        advanced = replace(
            chain,
            lifetime_attempts=chain.lifetime_attempts + 1,
            last_trigger=trigger,
            last_reason=_optional_str(reason),
        )
        return RetryDecision(
            outcome=RetryOutcome.PROCEED,
            chain=advanced,
            budget=resolved_budget,
            trigger=trigger,
            detail="initial attempt does not consume budget",
        )

    if trigger is RetryTrigger.RECOVERY:
        advanced = replace(
            chain,
            lifetime_attempts=chain.lifetime_attempts + 1,
            last_trigger=trigger,
            last_reason=_optional_str(reason),
        )
        return RetryDecision(
            outcome=RetryOutcome.PROCEED,
            chain=advanced,
            budget=resolved_budget,
            trigger=trigger,
            detail="crash recovery reissue is free",
        )

    if trigger in (RetryTrigger.MANUAL, RetryTrigger.VALIDATOR):
        advanced = replace(
            chain,
            auto_attempts_used=0,
            chain_serial=chain.chain_serial + 1,
            lifetime_attempts=chain.lifetime_attempts + 1,
            last_trigger=trigger,
            last_reason=_optional_str(reason),
        )
        return RetryDecision(
            outcome=RetryOutcome.PROCEED,
            chain=advanced,
            budget=resolved_budget,
            trigger=trigger,
            detail=f"{trigger.value} retry opens a fresh failure chain",
        )

    # RetryTrigger.AUTO — the only path that spends budget.
    if chain.auto_attempts_used >= limit:
        return RetryDecision(
            outcome=RetryOutcome.EXHAUSTED,
            chain=chain,
            budget=resolved_budget,
            trigger=trigger,
            detail=(
                f"automatic retry budget exhausted "
                f"({chain.auto_attempts_used}/{limit} used in chain "
                f"#{chain.chain_serial})"
            ),
        )

    advanced = replace(
        chain,
        auto_attempts_used=chain.auto_attempts_used + 1,
        lifetime_attempts=chain.lifetime_attempts + 1,
        last_trigger=trigger,
        last_reason=_optional_str(reason),
    )
    return RetryDecision(
        outcome=RetryOutcome.PROCEED,
        chain=advanced,
        budget=resolved_budget,
        trigger=trigger,
        detail=(
            f"automatic retry {advanced.auto_attempts_used}/{limit} "
            f"in chain #{advanced.chain_serial}"
        ),
    )
