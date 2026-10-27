from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import delete, select

from bestseller.infra.db.models import LlmRunModel, WorkflowRunModel
from bestseller.infra.db.session import session_scope
from bestseller.services.llm import LLMCompletionRequest, bind_conception_model, complete_text
from bestseller.settings import load_settings

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_prompt_payload_refs_and_receipt_survive_real_db_readback(tmp_path: Path) -> None:
    settings = load_settings(
        env={
            "BESTSELLER__DATABASE__URL": (
                "postgresql+asyncpg://bestseller:bestseller@localhost:5432/bestseller_test"
            ),
            "BESTSELLER__LLM__MOCK": "true",
        }
    ).model_copy(
        update={
            "artifact_store": load_settings(env={}).artifact_store.model_copy(
                update={"retain_prompt_payloads": True, "local_dir": str(tmp_path)}
            )
        }
    )
    workflow_id = uuid4()
    llm_run_id = None
    try:
        async with session_scope(settings) as session:
            session.add(
                WorkflowRunModel(
                    id=workflow_id,
                    workflow_type="conception_initial",
                    status="running",
                    requested_by="test",
                    metadata_json={},
                )
            )
        async with session_scope(settings) as session:
            with bind_conception_model(None, workflow_run_id=workflow_id):
                result = await complete_text(
                    session,
                    settings,
                    LLMCompletionRequest(
                        logical_role="planner",
                        prompt_template="db_receipt",
                        prompt_version="v1",
                        system_prompt="system",
                        user_prompt="story",
                        fallback_response="fallback",
                    ),
                )
            llm_run_id = result.llm_run_id
            assert llm_run_id is not None
            await session.rollback()
        async with session_scope(settings) as session:
            row = await session.scalar(select(LlmRunModel).where(LlmRunModel.id == llm_run_id))
            assert row is not None
            assert row.workflow_run_id == workflow_id
            assert row.request_payload_ref is not None
            assert row.response_payload_ref is not None
            assert row.metadata_json["prompt_payload_receipt"]["status"] == "stored"
            request_payload = json.loads(
                (tmp_path / row.request_payload_ref).read_text(encoding="utf-8")
            )
            response_payload = json.loads(
                (tmp_path / row.response_payload_ref).read_text(encoding="utf-8")
            )
            assert request_payload["llm_run_id"] == str(llm_run_id)
            assert response_payload["llm_run_id"] == str(llm_run_id)
    finally:
        async with session_scope(settings) as session:
            await session.execute(
                delete(LlmRunModel).where(LlmRunModel.workflow_run_id == workflow_id)
            )
            await session.execute(
                delete(WorkflowRunModel).where(WorkflowRunModel.id == workflow_id)
            )
