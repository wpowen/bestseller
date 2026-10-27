from __future__ import annotations

import os
from uuid import uuid4

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from bestseller.infra.db.models import WorkflowRunModel
from bestseller.services.conception_checkpoints import (
    build_conception_checkpoint,
    load_conception_checkpoint,
    save_conception_checkpoint,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _test_database_url() -> str:
    value = os.environ.get("BESTSELLER__DATABASE__URL", "")
    if not value or "test" not in value.lower() or not value.startswith("postgresql"):
        pytest.skip("requires an explicit PostgreSQL test database")
    return value


def _checkpoint() -> dict:
    return build_conception_checkpoint(
        {
            "premise": "巡检员发现仙城账目被改写。",
            "title": "仙城坏账",
            "synopsis": "巡检员沿着被改写的公务账目追查责任链。",
            "writing_profile": {"market": {"logline": "巡检员追查仙城坏账。"}},
            "story_spine": {"who": "巡检员", "wants": "找出改账者"},
            "concept_contract": {"schema_version": "concept-contract.v2"},
            "ctx": {"creation_intent": {"allowed_styles": ["修仙职场喜剧"]}},
            "tags": ["仙侠", "职场"],
            "commercial_brief": {"reader_promise": "查清责任链"},
            "world_model_payload": {"setting": "仙城公务体系"},
            "conception_log": [{"stage": "candidate_selected"}],
            "llm_run_ids": [str(uuid4())],
            "chapter_count": 12,
            "genre_key": "xianxia",
            "unlisted_resume_field": {"must_survive": True},
        },
        input_hash="integration-input-v1",
    )


async def test_checkpoint_flush_respects_caller_transaction_and_round_trips() -> None:
    engine = create_async_engine(_test_database_url())
    factory = async_sessionmaker(engine, expire_on_commit=False)
    workflow_run_id = uuid4()
    try:
        async with factory() as session:
            session.add(
                WorkflowRunModel(
                    id=workflow_run_id,
                    workflow_type="conception_initial",
                    status="running",
                    requested_by="test",
                    metadata_json={"existing_receipt": {"keep": True}},
                )
            )
            await session.commit()

        checkpoint = _checkpoint()
        async with factory() as session:
            await save_conception_checkpoint(
                session,
                workflow_run_id=workflow_run_id,
                checkpoint=checkpoint,
            )
            await session.rollback()

        async with factory() as session:
            row = await session.scalar(
                select(WorkflowRunModel).where(WorkflowRunModel.id == workflow_run_id)
            )
            assert row is not None
            assert "conception_checkpoint" not in row.metadata_json

            await save_conception_checkpoint(
                session,
                workflow_run_id=workflow_run_id,
                checkpoint=checkpoint,
            )
            await session.commit()

        async with factory() as session:
            loaded = await load_conception_checkpoint(
                session,
                workflow_run_id=workflow_run_id,
                input_hash="integration-input-v1",
            )
            assert loaded == checkpoint
            assert loaded["state"]["unlisted_resume_field"] == {"must_survive": True}
            row = await session.get(WorkflowRunModel, workflow_run_id)
            assert row.metadata_json["existing_receipt"] == {"keep": True}
    finally:
        async with factory() as session:
            await session.execute(
                delete(WorkflowRunModel).where(WorkflowRunModel.id == workflow_run_id)
            )
            await session.commit()
        await engine.dispose()
