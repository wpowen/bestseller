"""Concurrent project-less calls retain their creation workflow provenance."""
import asyncio
from uuid import uuid4

import pytest

from bestseller.infra.db.models import LlmRunModel
from bestseller.services.llm import LLMCompletionRequest, bind_conception_model, complete_text
from bestseller.settings import load_settings


class Session:
    def __init__(self):
        self.rows = []

    def add(self, row):
        self.rows.append(row)

    async def flush(self):
        for row in self.rows:
            if row.id is None:
                row.id = uuid4()

    async def commit(self):
        pass


async def receipt(request=None):
    session = Session()
    await complete_text(
        session, load_settings(env={"BESTSELLER__LLM__MOCK": "true"}),
        request or LLMCompletionRequest(
            logical_role="critic", system_prompt="Evaluate this story.",
            user_prompt="A person must choose a path.", fallback_response="{}",
        ),
    )
    return next(row for row in session.rows if isinstance(row, LlmRunModel))


@pytest.mark.asyncio
async def test_concurrent_creation_receipts_keep_workflows_separate():
    ids = [uuid4(), uuid4()]

    async def generate(workflow_id):
        with bind_conception_model(None, workflow_run_id=workflow_id):
            await asyncio.sleep(0)
            return (await receipt()).workflow_run_id

    assert await asyncio.gather(*(generate(i) for i in ids)) == ids
    assert (await receipt()).workflow_run_id is None


@pytest.mark.asyncio
async def test_nested_workflow_restores_after_failure_and_explicit_request_wins():
    outer, inner, explicit = uuid4(), uuid4(), uuid4()
    request = LLMCompletionRequest(
        logical_role="critic", system_prompt="Review", user_prompt="story",
        fallback_response="{}", workflow_run_id=explicit,
    )
    with bind_conception_model(None, workflow_run_id=outer):
        assert (await receipt(request)).workflow_run_id == explicit
        with pytest.raises(RuntimeError), bind_conception_model(None, workflow_run_id=inner):
            assert (await receipt()).workflow_run_id == inner
            raise RuntimeError("cancel inner attempt")
        assert (await receipt()).workflow_run_id == outer
    assert (await receipt()).workflow_run_id is None
    assert request.workflow_run_id == explicit


@pytest.mark.asyncio
async def test_ambient_creation_does_not_rebind_project_calls_or_mutate_requests():
    request = LLMCompletionRequest(
        logical_role="critic", system_prompt="Review", user_prompt="story",
        fallback_response="{}",
    )
    with bind_conception_model(None, workflow_run_id=uuid4()):
        assert (await receipt(request)).workflow_run_id is not None
    assert request.workflow_run_id is None
    assert (await receipt(request)).workflow_run_id is None


@pytest.mark.asyncio
async def test_project_generation_keeps_its_own_workflow_boundary(monkeypatch):
    from bestseller.services import llm

    async def no_override(*args):
        return None

    monkeypatch.setattr(llm, "_resolve_project_model_override", no_override)
    request = LLMCompletionRequest(
        logical_role="critic", system_prompt="Review", user_prompt="story",
        fallback_response="{}", project_id=uuid4(),
    )
    with bind_conception_model(None, workflow_run_id=uuid4()):
        assert (await receipt(request)).workflow_run_id is None
