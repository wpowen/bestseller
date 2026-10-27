from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import select

from bestseller.infra.db.models import WorkflowRunModel
from bestseller.infra.db.session import session_scope
from bestseller.settings import load_settings
from bestseller.web.server import _close_creation_workflow_after_task_exit_async

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_task_exit_closes_only_matching_conception_attempt() -> None:
    settings = load_settings(
        env={
            "BESTSELLER__DATABASE__URL": (
                "postgresql+asyncpg://bestseller:bestseller@localhost:5432/bestseller_test"
            )
        }
    )
    attempt_id = f"test-terminal-cleanup:{uuid4()}"
    matching_id = uuid4()
    unrelated_id = uuid4()
    try:
        async with session_scope(settings) as session:
            session.add_all(
                [
                    WorkflowRunModel(
                        id=matching_id,
                        workflow_type="conception_initial",
                        status="running",
                        current_step="conception",
                        metadata_json={"attempt_id": attempt_id},
                    ),
                    WorkflowRunModel(
                        id=unrelated_id,
                        workflow_type="conception_initial",
                        status="running",
                        current_step="conception",
                        metadata_json={"attempt_id": "different"},
                    ),
                ]
            )
        closed = await _close_creation_workflow_after_task_exit_async(
            settings,
            attempt_id=attempt_id,
            status="cancelled",
            error_message="user cancelled",
        )
        assert closed == 1
        async with session_scope(settings) as session:
            matching = await session.scalar(
                select(WorkflowRunModel).where(WorkflowRunModel.id == matching_id)
            )
            unrelated = await session.scalar(
                select(WorkflowRunModel).where(WorkflowRunModel.id == unrelated_id)
            )
            assert matching is not None
            assert matching.status == "cancelled"
            assert matching.current_step == "cancelled"
            assert matching.error_message == "user cancelled"
            assert matching.metadata_json["terminal_status_source"] == "web_task_exit"
            assert unrelated is not None
            assert unrelated.status == "running"
    finally:
        async with session_scope(settings) as session:
            for row_id in (matching_id, unrelated_id):
                row = await session.scalar(
                    select(WorkflowRunModel).where(WorkflowRunModel.id == row_id)
                )
                if row is not None:
                    session.delete(row)
