"""Exact preservation contracts, not claims of model semantic accuracy."""

import copy
from uuid import uuid4

import pytest

from bestseller.services import world_source_partition as partition
from bestseller.services.ideology_kernel import cost_style_directive
from bestseller.services.world_authority import world_authority_source_hash
from bestseller.services.world_cost_claims import cost_clause_index, reconcile_cost_claims

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "pieces",
    [
        [
            ("越阶符耗尽则符废", "keep"),
            ("人软", "remove_self_weakening"),
            ("但不会退阶", "keep"),
            ("且两日手抖", "remove_self_weakening"),
            ("。", "keep"),
        ],
        [
            ("The beacon lasts two days and failure consumes the crystal", "keep"),
            (" and weakens the pilot for two days", "remove_self_weakening"),
            (", without rank loss.", "keep"),
        ],
    ],
)
def test_confirmed_partition_preserves_independent_failure_duration_and_guarantee(pieces):
    quote = "".join(text for text, _ in pieces)
    sources = {
        "creation": {"cost_style": "minimal", "cost_rule": cost_style_directive("minimal")},
        "world": {"power_system": {"hard_limits": quote}},
    }
    clauses = cost_clause_index(sources)
    # Use one complete target to exercise mixed semantics within a single clause.
    assert len(clauses) == 1
    clause_id = clauses[0]["clause_id"]
    source_hash = world_authority_source_hash(sources)
    cost_review = {
        "complete": True,
        "source_hash": source_hash,
        "clauses": [
            {
                "clause_id": clause_id,
                "facts": [
                    {
                        "effect": "self_weakening",
                        "actor": "protagonist",
                        "usage_scope": "overreach",
                        "polarity": "incurs",
                    }
                ],
            }
        ],
    }
    diagnostic = reconcile_cost_claims([cost_review, copy.deepcopy(cost_review)], sources=sources)
    review = {
        "complete": True,
        "source_hash": source_hash,
        "clauses": [
            {
                "clause_id": clause_id,
                "segments": [{"quote": text, "kind": kind} for text, kind in pieces],
            }
        ],
    }
    proof = partition.reconcile_source_partition(
        [review, copy.deepcopy(review)], sources=sources, diagnostic=diagnostic
    )
    proof["llm_run_ids"] = [str(uuid4()), str(uuid4())]
    candidate = partition.build_partition_candidate(
        sources=sources, diagnostic=diagnostic, partition=proof
    )
    assert candidate["edits"][0]["replacement"] == "".join(
        text for text, kind in pieces if kind == "keep"
    )
    assert candidate["promotion_allowed"] is False
