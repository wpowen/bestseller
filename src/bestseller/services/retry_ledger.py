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
* **Why a retry happens decides whether it costs budget.** A human clicking
  "retry" is new information and re-opens credit; a crash-recovery reissue is
  the platform's own fault and is free; only unattended automatic retries burn
  the budget down.

This mirrors DeterminFlow's failure-chain model (``failure_policy.py``), which
solved the identical class of runaway loop.

The module is intentionally pure: it takes mappings and returns new mappings, so
it can be unit-tested without a database and reused by chapter-scoped,
project-scoped and gate-scoped callers alike.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, replace
from enum import Enum
from typing import Any, Final

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
]


class RetryTrigger(str, Enum):
    """Why this attempt is happening. Determines the budget semantics."""

    INITIAL = "initial"
    """First attempt of a unit of work. Never consumes budget."""

    AUTO = "auto"
    """Unattended automatic retry. The only trigger that consumes budget."""

    MANUAL = "manual"
    """A human asked for this. Opens a fresh chain — human input is new credit."""

    VALIDATOR = "validator"
    """A downstream judge/gate rejected upstream work and asked for rework.

    Also opens a fresh chain: a *specific* rejection with feedback is new
    information, unlike blindly re-running the same failing step.
    """

    RECOVERY = "recovery"
    """Re-issued after a crash/restart interrupted an in-flight attempt.

    Free: a process dying is the platform's fault, not the book's. Charging it
    would let an unstable worker exhaust a healthy book's budget.
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
        return max(0, budget.effective_limit - self.auto_attempts_used)

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
        return cls(
            auto_attempts_used=_non_negative_int(raw.get("auto_attempts_used")),
            chain_serial=_non_negative_int(raw.get("chain_serial")),
            lifetime_attempts=_non_negative_int(raw.get("lifetime_attempts")),
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
    """Budget scope for a gate failure: one chain per failure *mode*.

    Spinning on a single unsatisfiable gate must stop, but reaching a different
    gate is progress and earns a fresh chain. Reasons carry a ``:detail``
    suffix, so the base reason is the identity.
    """

    base = str(reason or "unknown").split(":", 1)[0].strip() or "unknown"
    return f"generation_gate:{base}"


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
    if not isinstance(block, Mapping):
        return RetryChain()
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
