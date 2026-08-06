"""Retry budget semantics: the guard against the 880k-token runaway loop."""

from __future__ import annotations

import pytest

from bestseller.services.retry_ledger import (
    ABSOLUTE_MAX_AUTO_ATTEMPTS,
    RetryBudget,
    RetryChain,
    RetryOutcome,
    RetryTrigger,
    evaluate_retry,
    load_chain,
    store_chain,
)


@pytest.mark.unit
def test_auto_retries_exhaust_after_the_budget() -> None:
    budget = RetryBudget(auto_attempt_limit=3)
    chain = RetryChain()

    for expected_used in (1, 2, 3):
        decision = evaluate_retry(chain, trigger=RetryTrigger.AUTO, budget=budget)
        assert decision.allowed
        assert decision.chain.auto_attempts_used == expected_used
        chain = decision.chain

    exhausted = evaluate_retry(chain, trigger=RetryTrigger.AUTO, budget=budget)
    assert exhausted.outcome is RetryOutcome.EXHAUSTED
    assert not exhausted.allowed
    # The chain is returned unchanged so an exhausted decision cannot inflate
    # the counter further on repeated polling.
    assert exhausted.chain.auto_attempts_used == 3


@pytest.mark.unit
def test_exhausted_state_is_stable_under_repeated_polling() -> None:
    """A self-heal job polling every 60s must not drift the counter."""

    budget = RetryBudget(auto_attempt_limit=1)
    chain = evaluate_retry(RetryChain(), trigger=RetryTrigger.AUTO, budget=budget).chain

    for _ in range(50):
        decision = evaluate_retry(chain, trigger=RetryTrigger.AUTO, budget=budget)
        assert decision.exhausted
        chain = decision.chain

    assert chain.auto_attempts_used == 1
    assert chain.lifetime_attempts == 1


@pytest.mark.unit
@pytest.mark.parametrize("trigger", [RetryTrigger.MANUAL, RetryTrigger.VALIDATOR])
def test_human_and_validator_retries_reopen_the_chain(trigger: RetryTrigger) -> None:
    budget = RetryBudget(auto_attempt_limit=2)
    chain = RetryChain()
    for _ in range(2):
        chain = evaluate_retry(chain, trigger=RetryTrigger.AUTO, budget=budget).chain
    assert evaluate_retry(chain, trigger=RetryTrigger.AUTO, budget=budget).exhausted

    reopened = evaluate_retry(chain, trigger=trigger, budget=budget)
    assert reopened.allowed
    assert reopened.chain.auto_attempts_used == 0
    assert reopened.chain.chain_serial == chain.chain_serial + 1
    # Fresh credit really is spendable.
    assert evaluate_retry(reopened.chain, trigger=RetryTrigger.AUTO, budget=budget).allowed


@pytest.mark.unit
def test_crash_recovery_reissue_is_free() -> None:
    budget = RetryBudget(auto_attempt_limit=2)
    chain = evaluate_retry(RetryChain(), trigger=RetryTrigger.AUTO, budget=budget).chain

    recovered = evaluate_retry(chain, trigger=RetryTrigger.RECOVERY, budget=budget)

    assert recovered.allowed
    assert recovered.chain.auto_attempts_used == chain.auto_attempts_used
    assert recovered.chain.chain_serial == chain.chain_serial
    assert recovered.chain.lifetime_attempts == chain.lifetime_attempts + 1


@pytest.mark.unit
def test_initial_attempt_does_not_consume_budget() -> None:
    decision = evaluate_retry(RetryChain(), trigger=RetryTrigger.INITIAL)
    assert decision.allowed
    assert decision.chain.auto_attempts_used == 0
    assert decision.chain.lifetime_attempts == 1


@pytest.mark.unit
def test_config_cannot_raise_the_limit_past_the_absolute_ceiling() -> None:
    budget = RetryBudget(auto_attempt_limit=10_000)
    assert budget.effective_limit == ABSOLUTE_MAX_AUTO_ATTEMPTS

    chain = RetryChain(auto_attempts_used=ABSOLUTE_MAX_AUTO_ATTEMPTS)
    assert evaluate_retry(chain, trigger=RetryTrigger.AUTO, budget=budget).exhausted


@pytest.mark.unit
def test_negative_limit_disables_automatic_retry_entirely() -> None:
    budget = RetryBudget(auto_attempt_limit=-5)
    assert budget.effective_limit == 0
    assert evaluate_retry(RetryChain(), trigger=RetryTrigger.AUTO, budget=budget).exhausted


@pytest.mark.unit
def test_chain_round_trips_through_the_guarded_metadata_mapping() -> None:
    """The counter must live in the same dict as the state it guards."""

    metadata = {"production_paused": True, "production_pause_reason": "gate_failed"}
    chain = evaluate_retry(RetryChain(), trigger=RetryTrigger.AUTO).chain

    stored = store_chain(metadata, scope="volume_outline_gate", chain=chain)

    # Original mapping untouched (immutability), guarded state preserved.
    assert "retry_chain" not in metadata
    assert stored["production_paused"] is True
    assert stored["production_pause_reason"] == "gate_failed"

    restored = load_chain(stored, scope="volume_outline_gate")
    assert restored == chain


@pytest.mark.unit
def test_scopes_are_independent() -> None:
    metadata: dict[str, object] = {}
    first = evaluate_retry(RetryChain(), trigger=RetryTrigger.AUTO).chain
    metadata = store_chain(metadata, scope="gate_a", chain=first)

    assert load_chain(metadata, scope="gate_b") == RetryChain()
    assert load_chain(metadata, scope="gate_a").auto_attempts_used == 1


@pytest.mark.unit
@pytest.mark.parametrize(
    "container",
    [None, {}, {"retry_chain": "not-a-mapping"}, {"retry_chain": {"scope": 7}}],
)
def test_malformed_storage_degrades_to_a_pristine_chain(container: object) -> None:
    """A corrupt counter must not become a second failure on top of the first."""

    assert load_chain(container, scope="scope") == RetryChain()  # type: ignore[arg-type]


@pytest.mark.unit
def test_decision_event_payload_is_actionable() -> None:
    budget = RetryBudget(auto_attempt_limit=2)
    decision = evaluate_retry(RetryChain(), trigger=RetryTrigger.AUTO, budget=budget)

    payload = decision.to_event_payload()

    assert payload["outcome"] == "proceed"
    assert payload["trigger"] == "auto"
    assert payload["auto_attempts_used"] == 1
    assert payload["auto_attempt_limit"] == 2
    assert payload["remaining"] == 1
