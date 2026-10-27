"""Historical audit counters retain the actual SQLite paid/physical proof chain."""
from copy import deepcopy
import json
from pathlib import Path
from uuid import UUID

import pytest
from test_planning_admission_literal_runtime import assess, literal as literal  # noqa: F401
from test_planning_draft_admission import harness as harness  # noqa: F401

from bestseller.infra.db.models import ExecutionBudgetModel, LlmRunModel
from bestseller.services import planning_review_literal_transport as transport
from bestseller.services.prompt_compiler import estimate_legacy_prompt_tokens

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


async def historical(h, monkeypatch):
    with monkeypatch.context() as old:
        old.setattr(transport, 'estimate_prompt_tokens', estimate_legacy_prompt_tokens)
        state = {}
        receipt = await assess(h, state)
    return state, receipt


async def test_actual_paid_historical_counter_reuses_original_receipt_and_ledger(literal, monkeypatch):
    h = literal
    state, receipt = await historical(h, monkeypatch)
    original_state, original_receipt = deepcopy(state), deepcopy(receipt)
    async with h.session_factory() as session:
        run = await session.get(LlmRunModel, UUID(receipt['llm_run_id']))
        before = deepcopy(run.metadata_json)
        files = {ref: (Path(h.settings.artifact_store.local_dir) / ref).read_bytes()
                 for ref in (run.request_payload_ref, run.response_payload_ref)}
    assert await assess(h, state) == original_receipt
    assert await assess(h, deepcopy(state)) == original_receipt
    assert state == original_state and len(h.sent) == 1
    async with h.session_factory() as session:
        run = await session.get(LlmRunModel, UUID(receipt['llm_run_id']))
        assert run.metadata_json == before
        budget = await session.get(ExecutionBudgetModel, 'audit:admission-fixture')
        assert (budget.calls_used, budget.tokens_charged) == (1, 15)
    assert all((Path(h.settings.artifact_store.local_dir) / ref).read_bytes() == raw
               for ref, raw in files.items())


@pytest.mark.parametrize('target', ['request', 'response', 'metadata', 'settlement'])
async def test_historical_counter_still_requires_physical_metadata_and_paid_proof(literal, monkeypatch, target):
    h = literal
    state, receipt = await historical(h, monkeypatch)
    async with h.session_factory() as session:
        run = await session.get(LlmRunModel, UUID(receipt['llm_run_id']))
        if target in {'request', 'response'}:
            ref = run.request_payload_ref if target == 'request' else run.response_payload_ref
            path = Path(h.settings.artifact_store.local_dir) / ref
            value = json.loads(path.read_text())
            if target == 'request':
                value['messages']['user']['text'] += ' changed source'
            else:
                value['raw_message']['reasoning_content'] += ' changed physical return'
            path.write_text(json.dumps(value))
        elif target == 'metadata':
            run.metadata_json = {**run.metadata_json, 'source_complete_retention': {}}
            await session.commit()
        else:
            run.input_tokens += 1
            await session.commit()
    with pytest.raises((ValueError, AssertionError)):
        await assess(h, deepcopy(state))
    assert len(h.sent) == 1
