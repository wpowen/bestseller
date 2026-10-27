from __future__ import annotations

import copy

import pytest

from bestseller.services import conception
from bestseller.services.budget import ExecutionBudgetError

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_packaging_quota_persisted_before_paid_work_and_not_reset():
    state, saved = {"conception_log": []}, []

    async def save():
        saved.append(copy.deepcopy(state))

    first = await conception._reserve_conception_repair_quota(state, "final_packaging", 1, save)
    assert first == 1 and saved[-1]["repair_quotas"]["final_packaging"] == 1
    restored = copy.deepcopy(saved[-1])
    with pytest.raises(ExecutionBudgetError, match="execution_repair_quota_exhausted"):
        await conception._reserve_conception_repair_quota(restored, "final_packaging", 1, save)


@pytest.mark.asyncio
async def test_old_packaging_receipts_seed_quota_without_reset():
    state = {"conception_log": [{"agent": "final_packaging_seed_repair", "attempt": 2}]}
    with pytest.raises(ExecutionBudgetError):
        await conception._reserve_conception_repair_quota(state, "final_packaging", 2, None)
    assert state["repair_quotas"]["final_packaging"] == 2


def test_resolved_receipt_does_not_certify_fact_or_modified_body():
    report = conception._review_adjudication_audit(
        {"contradictions": ["alleged contradiction"]},
        {
            "premise": "unchanged",
            "review_adjudications": [
                {
                    "issue_id": "contradictions:0",
                    "decision": "resolved",
                    "reason": "because I say so",
                }
            ],
        },
    )
    assert report["dispositions_complete"] is True
    assert report["facts_verified"] is False
    assert report["claim_scope"] == "disposition_completeness_only"
    assert len(report["unverified_resolution_claims"]) == 1
