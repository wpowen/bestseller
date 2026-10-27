"""Real session checkpoints must not close an executor's enclosing context."""

from uuid import UUID

import pytest
from sqlalchemy import select

from bestseller.infra.db.models import (
    ChapterDraftVersionModel, ExecutionBudgetModel, RewriteTaskModel, WorkflowRunModel,
)
from bestseller.services import rewrite_outline_dispatch as dispatch
from bestseller.services.budget import bind_execution_scope
from bestseller.services.llm import _release_session_before_external_llm_call
from bestseller.settings import load_settings
from test_outline_alignment_dispatch import (
    dispatch_db, guard_dispatch_boundaries, prepare, setup, successful_rewrite,  # noqa: F401 -- Real SQLite and offline fixtures.
)

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.mark.parametrize("checkpoints", [1, 2, 3])
async def test_llm_checkpoint_then_post_return_sql_and_commit(dispatch_db, checkpoints):  # noqa: F811 -- Imported pytest fixture.
    factory = dispatch_db
    case = await setup(factory)
    calls = []
    rewrite = successful_rewrite(factory, calls)

    async def checkpointed(session, *args, **kwargs):
        for _ in range(checkpoints):
            await _release_session_before_external_llm_call(session)
            # The real LLM boundary commits; normal post-return SQL starts a
            # fresh transaction rather than using a closed begin() context.
            assert await session.scalar(select(RewriteTaskModel.id).where(
                RewriteTaskModel.id == kwargs["rewrite_task_id"])) == case.alignment_id
        return await rewrite(session, *args, **kwargs)

    with bind_execution_scope(case.scope):
        prepared = await prepare(dispatch, factory, case)
        result = await dispatch.execute_outline_alignment_dispatch(factory,
            load_settings(env={}), workflow_run_id=prepared.workflow_run_id,
            rewrite_executor=checkpointed)
        assert result.state == "review_ready"
        again = await dispatch.execute_outline_alignment_dispatch(factory,
            load_settings(env={}), workflow_run_id=prepared.workflow_run_id,
            rewrite_executor=checkpointed)
        assert again == result and len(calls) == 1
    async with factory() as session:
        task = await session.get(RewriteTaskModel, case.alignment_id)
        draft = await session.get(ChapterDraftVersionModel,
            UUID(result.review_source_binding["draft_id"]))
        workflow = await session.get(WorkflowRunModel, prepared.workflow_run_id)
        budget = await session.get(ExecutionBudgetModel, case.scope)
        assert task.status == "completed" and task.attempts == 1
        assert draft.is_current and draft.promotion_state == "under_review"
        assert workflow.status == "completed"
        assert budget.calls_used == 7 and budget.tokens_charged == 12000


@pytest.mark.parametrize("after_output", [False, True])
async def test_failure_after_checkpoint_keeps_claim_and_rolls_back_output(dispatch_db, after_output):  # noqa: F811 -- Imported pytest fixture.
    factory = dispatch_db
    case = await setup(factory)
    calls = []
    rewrite = successful_rewrite(factory, calls)

    async def interrupted(session, *args, **kwargs):
        await _release_session_before_external_llm_call(session)
        if after_output:
            await rewrite(session, *args, **kwargs)
        else:
            assert await session.get(RewriteTaskModel, case.alignment_id) is not None
        raise RuntimeError("post-return technical failure")

    with bind_execution_scope(case.scope):
        prepared = await prepare(dispatch, factory, case)
        with pytest.raises(RuntimeError, match="post-return technical failure"):
            await dispatch.execute_outline_alignment_dispatch(factory,
                load_settings(env={}), workflow_run_id=prepared.workflow_run_id,
                rewrite_executor=interrupted)
        assert (await prepare(dispatch, factory, case)).state == "blocked"
    async with factory() as session:
        old = await session.get(ChapterDraftVersionModel, case.draft.id)
        task = await session.get(RewriteTaskModel, case.alignment_id)
        workflow = await session.get(WorkflowRunModel, prepared.workflow_run_id)
        assert old.is_current and old.content_md == case.draft.content_md
        assert task.status == "pending" and task.attempts == 0
        assert workflow.status == "machine_blocked"
        assert workflow.metadata_json[dispatch._KEY]["claim_id"]
        assert workflow.metadata_json[dispatch._KEY]["output"] is None
