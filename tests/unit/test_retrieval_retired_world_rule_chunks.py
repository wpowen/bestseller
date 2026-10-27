"""Real SQLite refresh removes only retired own WorldRule source chunks."""

from uuid import uuid4

import pytest
from sqlalchemy import delete, select
from test_foundation_materialization_rebuild import all_rows
from test_foundation_materialization_rebuild import case as case

from bestseller.infra.db.models import RetrievalChunkModel, WorldRuleModel
from bestseller.services.retrieval import refresh_story_bible_retrieval_index
from bestseller.settings import load_settings

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.mark.parametrize("replace", [True, False])
async def test_actual_refresh_cleans_all_retired_chunks_including_empty_current_world(
    case, replace
):
    async with case.factory() as session:
        before = await all_rows(session)
        old_chunk_ids = {
            r["id"]
            for r in before["retrieval_chunks"]
            if r["project_id"] == str(case.project_id) and r["source_type"] == "world_rule"
        }
        assert len(old_chunk_ids) == 3  # Two chunks for one old rule plus a prior orphan.
        await session.execute(
            delete(WorldRuleModel).where(WorldRuleModel.project_id == case.project_id)
        )
        new_id = uuid4()
        if replace:
            session.add(
                WorldRuleModel(
                    id=new_id,
                    project_id=case.project_id,
                    rule_code="NEW",
                    name="Current rule",
                    description="Only the current seal is authoritative.",
                    metadata_json={},
                )
            )
        await session.flush()
        await refresh_story_bible_retrieval_index(session, load_settings(env={}), case.project_id)
        after = await all_rows(session)

        def protected(rows):
            return [
                r
                for r in rows
                if r["project_id"] != str(case.project_id)
                or r["source_type"] not in {"world_rule", "character", "relationship", "volume"}
            ]

        assert protected(after["retrieval_chunks"]) == protected(before["retrieval_chunks"])
        assert not old_chunk_ids & {r["id"] for r in after["retrieval_chunks"]}
        actual = set(
            await session.scalars(
                select(RetrievalChunkModel.source_id).where(
                    RetrievalChunkModel.project_id == case.project_id,
                    RetrievalChunkModel.source_type == "world_rule",
                )
            )
        )
        assert actual == ({new_id} if replace else set())
        for table in (
            "canon_facts",
            "timeline_events",
            "planning_artifact_versions",
            "workflow_runs",
        ):
            assert after[table] == before[table]
        await session.rollback()
        assert await all_rows(session) == before
