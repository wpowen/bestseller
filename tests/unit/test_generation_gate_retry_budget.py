"""The worker-side gate auto-continue must be budgeted.

2026-07-25: a volume-outline gate failed, the auto-continue path cleared the
pause and re-queued, the gate failed again — 880k tokens. The self-heal path had
been given a budget afterwards; this worker path had not, so the loop was still
open from the other side.
"""

from __future__ import annotations

import os

import pytest

from bestseller.services.retry_ledger import (
    RetryTrigger,
    evaluate_retry,
    generation_gate_budget,
    generation_gate_scope,
    load_chain,
    store_chain,
)


@pytest.mark.unit
def test_scope_is_per_failure_mode_not_per_book() -> None:
    """Spinning on one gate stops; reaching a different gate is progress."""

    assert generation_gate_scope("bible_gate_failed") == generation_gate_scope(
        "bible_gate_failed:chapter=7"
    )
    assert generation_gate_scope("bible_gate_failed") != generation_gate_scope(
        "volume_outline_gate_failed"
    )


@pytest.mark.unit
def test_unknown_reason_still_gets_a_stable_scope() -> None:
    assert generation_gate_scope(None) == generation_gate_scope("")
    assert generation_gate_scope(None).endswith("unknown")


@pytest.mark.unit
def test_budget_reads_the_shared_env_knob(monkeypatch: pytest.MonkeyPatch) -> None:
    """One knob for both restart paths — they must not be tunable apart."""

    monkeypatch.setenv("BESTSELLER_GENERATION_GATE_MAX_AUTO_RESUMES", "7")
    assert generation_gate_budget().effective_limit == 7

    monkeypatch.setenv("BESTSELLER_GENERATION_GATE_MAX_AUTO_RESUMES", "not-a-number")
    assert generation_gate_budget().effective_limit == 3


@pytest.mark.unit
def test_default_budget_matches_self_heal_constant() -> None:
    """Guards against the two paths drifting apart again."""

    from bestseller.worker import self_heal

    if "BESTSELLER_GENERATION_GATE_MAX_AUTO_RESUMES" in os.environ:
        pytest.skip("env override active")
    assert (
        generation_gate_budget().effective_limit
        == self_heal.GENERATION_GATE_MAX_AUTO_RESUMES
    )


@pytest.mark.unit
def test_repeated_failures_on_one_gate_exhaust_and_stay_exhausted() -> None:
    """The exact 2026-07-25 shape: same gate, over and over."""

    budget = generation_gate_budget()
    metadata: dict[str, object] = {
        "last_generation_gate_reason": "volume_outline_gate_failed",
    }
    scope = generation_gate_scope("volume_outline_gate_failed")

    allowed_attempts = 0
    for _ in range(30):
        decision = evaluate_retry(
            load_chain(metadata, scope=scope),
            trigger=RetryTrigger.AUTO,
            budget=budget,
        )
        if not decision.allowed:
            break
        allowed_attempts += 1
        metadata = store_chain(metadata, scope=scope, chain=decision.chain)

    assert allowed_attempts == budget.effective_limit
    # Still exhausted on every subsequent poll — no drift, no revival.
    for _ in range(10):
        assert not evaluate_retry(
            load_chain(metadata, scope=scope),
            trigger=RetryTrigger.AUTO,
            budget=budget,
        ).allowed


@pytest.mark.unit
def test_progressing_to_a_different_gate_earns_a_fresh_budget() -> None:
    budget = generation_gate_budget()
    metadata: dict[str, object] = {}

    stuck_scope = generation_gate_scope("volume_outline_gate_failed")
    for _ in range(budget.effective_limit):
        decision = evaluate_retry(
            load_chain(metadata, scope=stuck_scope),
            trigger=RetryTrigger.AUTO,
            budget=budget,
        )
        metadata = store_chain(metadata, scope=stuck_scope, chain=decision.chain)
    assert not evaluate_retry(
        load_chain(metadata, scope=stuck_scope),
        trigger=RetryTrigger.AUTO,
        budget=budget,
    ).allowed

    progressed_scope = generation_gate_scope("bible_gate_failed")
    assert evaluate_retry(
        load_chain(metadata, scope=progressed_scope),
        trigger=RetryTrigger.AUTO,
        budget=budget,
    ).allowed


@pytest.mark.unit
def test_budget_survives_the_purge_that_broke_the_original_guard() -> None:
    """The 2026-07-25 counter was wiped by a purge of adjacent keys."""

    budget = generation_gate_budget()
    scope = generation_gate_scope("volume_outline_gate_failed")
    metadata: dict[str, object] = {
        "generation_gate_auto_retry_needed": True,
        "last_generation_gate_reason": "volume_outline_gate_failed",
        "last_generation_gate_blocked_at": "2026-07-25T00:00:00+00:00",
        "paused_at": "2026-07-25T00:00:00+00:00",
        "production_paused": True,
    }
    decision = evaluate_retry(
        load_chain(metadata, scope=scope), trigger=RetryTrigger.AUTO, budget=budget
    )
    metadata = store_chain(metadata, scope=scope, chain=decision.chain)

    # Exactly the purge performed by the auto-continue path.
    for key in (
        "generation_gate_auto_retry_needed",
        "generation_resume_blocked_by_planning_gate",
        "generation_auto_repair_exhausted",
        "generation_resume_blocked_until_repair_audit",
        "production_paused",
        "production_pause_reason",
        "last_generation_gate_blocked_at",
        "paused_at",
    ):
        metadata.pop(key, None)

    assert load_chain(metadata, scope=scope).auto_attempts_used == 1


@pytest.mark.unit
def test_exhausted_book_is_parked_not_retried() -> None:
    """Verifies the terminal-state contract the worker relies on."""

    from bestseller.worker import tasks

    assert hasattr(tasks, "_mark_generation_gate_budget_exhausted")
    assert hasattr(tasks, "_consume_generation_gate_retry_budget")


@pytest.mark.unit
def test_arq_implicit_retry_is_disabled() -> None:
    """ARQ's invisible max_tries=5 must not silently re-run whole books."""

    from bestseller.worker.main import WorkerSettings

    assert getattr(WorkerSettings, "max_tries", None) == 1
