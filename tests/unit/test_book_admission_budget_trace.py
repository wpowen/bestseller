"""Real prefix repair request construction -> real isolated financial reserve.

Admission DB and provider response are fixture boundaries; no paid execution.
"""

from copy import deepcopy

import pytest
from sqlalchemy import select, func
from test_chapter_outline_readiness import rig as rig, _approve, service
from test_finite_book_budget_sqlite import case as case
from bestseller.infra.db import models as m
from bestseller.services import finite_book_budget as finite, llm
from bestseller.services.budget import ExecutionBudgetError
from bestseller.settings import LLMRoleSettings

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


async def test_real_admission_repair_requests_use_repair_budget_and_stop_without_second_send(
    rig, case, monkeypatch
):
    ledger, factory, rid, q, wid = case
    async with factory() as s:
        row = await s.get(m.ExecutionBudgetModel, ledger.scope_id)
        meta = deepcopy(row.metadata_json)
        binding = meta["finite_book_runs"][rid]["binding"]
        binding["project_id"] = str(rig.project.id)
        binding["caps"] = dict(
            calls=3, primary=1, hard_repair=1, transport_retry=1, money_micro_cny=4000000
        )
        row.metadata_json = meta
        await s.commit()
    original = service.complete_text
    requests, completed = [], []
    role = LLMRoleSettings(
        model=q["model"],
        api_base=q["endpoint"],
        temperature=0.2,
        max_tokens=6500,
        timeout_seconds=10,
    )

    async def boundary(session, settings, request):
        # Request was constructed by the normal prefix admission implementation.
        payload = llm._effective_input_payload(request, role)
        digest = llm._effective_input_hash(request, role)
        quote = await finite.quote(request, role, payload, digest)
        requests.append((request.prompt_template, quote["category"], digest))
        claim = await ledger.claim("normal-admission:" + digest, digest)
        attempt = await ledger.reserve(
            claim,
            role.model,
            digest,
            1000,
            endpoint=role.api_base,
            workflow_id=str(request.workflow_run_id),
            template=request.prompt_template,
            version=request.prompt_version,
            finite_book_binding=quote,
        )
        completed.append(request.prompt_template)
        response = await original(session, settings, request)
        # Synthetic usage, not a paid result. Demonstrates financial release only.
        await ledger.settle(
            attempt,
            "completed",
            actual_tokens=30,
            claim=claim,
            result=dict(model_name=role.model, input_tokens=10, output_tokens=20),
        )
        return response

    monkeypatch.setattr(service, "complete_text", boundary)
    rig.judges[:] = [False]
    rig.patches[:] = [{"patches": []}, {"patches": []}]
    with (
        llm.bind_execution_scope(ledger.scope_id),
        finite.bind_run(rid, project_id=rig.project.id, stage="5:admit"),
    ):
        with pytest.raises(ExecutionBudgetError, match="finite_calls_exhausted"):
            await _approve(rig)
    assert len(requests) == 2 and requests[0][2] != requests[1][2]
    assert all(
        t == "chapter_outline_prefix_surgical_repair" and cat == "hard_repair"
        for t, cat, _ in requests
    )
    assert len(completed) == len(rig.patch_inputs) == 1
    assert not rig.imports
    async with factory() as s:
        budget = await s.get(m.ExecutionBudgetModel, ledger.scope_id)
        run = budget.metadata_json["finite_book_runs"][rid]
        assert run["calls"] == 1
        assert run["counts"] == dict(primary=0, hard_repair=1, transport_retry=0)
        assert budget.calls_used == 317
        assert await s.scalar(select(func.count()).select_from(m.ExecutionAttemptModel)) == 1
