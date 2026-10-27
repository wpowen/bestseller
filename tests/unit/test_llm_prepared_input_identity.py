"""Preparation identity must match actual resolved input before budget/send."""
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from bestseller.services import llm
from bestseller.services.budget import ExecutionBudgetError, bind_execution_scope
from bestseller.settings import load_settings


@pytest.mark.asyncio
@pytest.mark.parametrize('proof', [
    {'schema': 'effective-input.v1', 'hash': '0' * 64},
    {'schema': 'prompt-only.v1', 'hash': '0' * 64},
    {'schema': 'effective-input.v1', 'hash': '0' * 64, 'allow': True},
])
async def test_mismatched_prepared_input_blocks_before_claim_or_provider(monkeypatch, proof):
    claim = AsyncMock(side_effect=AssertionError('wrong prepared input claimed a paid slot'))
    send = AsyncMock(side_effect=AssertionError('wrong prepared input reached provider'))
    monkeypatch.setattr(llm.ExecutionLedger, 'claim', claim)
    monkeypatch.setattr('litellm.acompletion', send)
    settings = load_settings(env={})
    settings.llm.mock = True
    request = llm.LLMCompletionRequest(logical_role='critic', system_prompt='Review current sources.',
        user_prompt='An unrelated observatory opens a new instrument.', fallback_response='unused',
        metadata={'prepared_effective_input': proof})
    with bind_execution_scope('test:prepared-input'):
        with pytest.raises(ExecutionBudgetError, match='execution_prepared_input_changed'):
            await llm.complete_text(SimpleNamespace(), settings, request)
    claim.assert_not_awaited()
    send.assert_not_awaited()
