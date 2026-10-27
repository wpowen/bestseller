from __future__ import annotations

from uuid import UUID

import pytest
from sqlalchemy import delete, select

from bestseller.infra.db.models import LlmRunModel
from bestseller.infra.db.session import session_scope
from bestseller.services.llm import (
    LLMCompletionRequest,
    bind_conception_model,
    complete_text,
)
from bestseller.settings import load_settings

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_final_conception_receipt_survives_outer_rollback() -> None:
    settings = load_settings(
        env={
            "BESTSELLER__DATABASE__URL": (
                "postgresql+asyncpg://bestseller:bestseller@localhost:5432/bestseller_test"
            ),
            "BESTSELLER__LLM__MOCK": "true",
        }
    )

    run_id: UUID | None = None
    async with session_scope(settings) as session:
        with bind_conception_model(None):
            result = await complete_text(
                session,
                settings,
                LLMCompletionRequest(
                    logical_role="planner",
                    system_prompt="system",
                    user_prompt="integration final receipt",
                    fallback_response="{}",
                ),
            )
        run_id = result.llm_run_id
        assert run_id is not None
        # Simulate the outer conception failure after the final LLM call.
        await session.rollback()

    async with session_scope(settings) as session:
        row = await session.scalar(select(LlmRunModel).where(LlmRunModel.id == run_id))
        assert row is not None
        assert row.metadata_json["llm_run_flushed"] is True
        assert row.metadata_json["runtime_provenance"]["process_fingerprint"]
        await session.execute(delete(LlmRunModel).where(LlmRunModel.id == run_id))
