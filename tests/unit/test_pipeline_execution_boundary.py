import pytest

from bestseller.services.budget import bind_execution_scope,record_execution_stop,ExecutionBudgetError
from bestseller.services.execution_stage import pipeline_operation_stage
from bestseller.services.pipelines import _checkpoint_commit

pytestmark=pytest.mark.unit


@pytest.mark.asyncio
async def test_swallowed_terminal_cannot_complete_stage_or_commit():
    commits=[]
    class Session:
        async def commit(self):
            commits.append("committed")
    @pipeline_operation_stage
    async def run(session,project_slug,chapter_number):
        try:
            error=ExecutionBudgetError("execution_budget_exhausted","test:scope")
            record_execution_stop(error)
            raise error
        except ExecutionBudgetError:
            pass
        await _checkpoint_commit(session)
        return "incorrect success"
    with bind_execution_scope("test:scope"):
        with pytest.raises(ExecutionBudgetError):
            await run(Session(),"unrelated-fixture",1)
    assert commits==[]


@pytest.mark.asyncio
async def test_swallowed_terminal_cannot_return_success_without_checkpoint():
    @pipeline_operation_stage
    async def run(project_slug):
        record_execution_stop(ExecutionBudgetError("execution_cancelled","test:scope"))
        return "incorrect success"
    with bind_execution_scope("test:scope"):
        with pytest.raises(ExecutionBudgetError):
            await run("different-fixture")
