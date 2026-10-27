"""Creation workflow attribution is persisted, including the final checkpoint."""
from uuid import uuid4

import pytest
from sqlalchemy import delete, select

from bestseller.infra.db.models import LlmRunModel, WorkflowRunModel
from bestseller.infra.db.session import session_scope
from bestseller.services.llm import LLMCompletionRequest, bind_conception_model, complete_text
from bestseller.settings import load_settings

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_creation_workflow_receipt_survives_failure():
    settings = load_settings(env={
        "BESTSELLER__DATABASE__URL":
            "postgresql+asyncpg://bestseller:bestseller@localhost:5432/bestseller_test",
        "BESTSELLER__LLM__MOCK": "true",
    })
    workflow_id = uuid4()
    run_id = None
    try:
        async with session_scope(settings) as session:
            session.add(WorkflowRunModel(
                id=workflow_id, workflow_type="conception_initial", status="running",
                requested_by="test", metadata_json={},
            ))
        async with session_scope(settings) as session:
            with bind_conception_model(None, workflow_run_id=workflow_id):
                result = await complete_text(session, settings, LLMCompletionRequest(
                    logical_role="planner", system_prompt="system", user_prompt="story",
                    fallback_response="{}",
                ))
            run_id = result.llm_run_id
            assert run_id is not None
            await session.rollback()
        async with session_scope(settings) as session:
            row = await session.scalar(select(LlmRunModel).where(LlmRunModel.id == run_id))
            assert row is not None
            assert row.workflow_run_id == workflow_id
            assert row.project_id is None
    finally:
        async with session_scope(settings) as session:
            await session.execute(
                delete(LlmRunModel).where(LlmRunModel.workflow_run_id == workflow_id)
            )
            await session.execute(
                delete(WorkflowRunModel).where(WorkflowRunModel.id == workflow_id)
            )
