"""A partial historical checkpoint must not replenish retry lifetime credit."""

import copy

import pytest

from bestseller.services.retry_ledger import (
    RetryBudget,
    RetryTrigger,
    evaluate_retry,
    load_chain,
    store_chain,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("scope", ["generation_gate", "independent_repair"])
@pytest.mark.parametrize("lifetime", [None, 0, 2])
@pytest.mark.parametrize("trigger", list(RetryTrigger))
def test_checkpoint_counters_cannot_discard_already_spent_attempts(scope, lifetime, trigger):
    raw = {
        "auto_attempts_used": 20,
        "chain_serial": 4,
        "last_trigger": "auto",
        "last_reason": "original_failure",
    }
    if lifetime is not None:
        raw["lifetime_attempts"] = lifetime
    metadata = {"retry_chain": {scope: raw}, "prior_receipts": [{"failure": "retain"}]}
    before = copy.deepcopy(metadata)
    restored = load_chain(metadata, scope=scope)
    decision = evaluate_retry(restored, trigger=trigger, reason="renamed_failure")
    assert decision.exhausted
    assert decision.chain.lifetime_attempts == 20
    assert decision.chain.last_reason == "original_failure"
    saved = store_chain(metadata, scope=scope, chain=decision.chain)
    assert load_chain(saved, scope=scope) == decision.chain
    assert saved["prior_receipts"] == before["prior_receipts"]
    assert metadata == before


def test_well_formed_checkpoint_retains_larger_lifetime_and_attribution():
    metadata = {
        "retry_chain": {
            "generation_gate": {
                "auto_attempts_used": 1,
                "lifetime_attempts": 8,
                "chain_serial": 3,
                "last_trigger": "validator",
                "last_reason": "specific_feedback",
            }
        }
    }
    chain = load_chain(metadata, scope="generation_gate")
    assert chain.lifetime_attempts == 8 and chain.last_reason == "specific_feedback"
    decision = evaluate_retry(
        chain, trigger=RetryTrigger.RECOVERY, budget=RetryBudget(lifetime_attempt_limit=9)
    )
    assert decision.allowed and decision.chain.lifetime_attempts == 9
    assert decision.chain.auto_attempts_used == 1 and decision.chain.chain_serial == 3
