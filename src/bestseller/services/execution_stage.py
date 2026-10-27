"""Stable semantic phase identity for pipeline-owned model operations."""
from functools import wraps
import inspect
from contextlib import nullcontext

from bestseller.services.llm import bind_execution_operation
from bestseller.services.prompt_source_policy import content_hash
from bestseller.services.budget import bind_execution_scope, current_execution_scope, raise_if_execution_stopped


def attach_workflow_failure(error, workflow) -> None:
    """Detach an exact workflow identity before its failure transaction rolls back."""
    import asyncio
    from bestseller.services.progress_context import TaskCancelledError
    from bestseller.services.retry_ledger import execution_terminal_code
    recoverable = isinstance(error, asyncio.CancelledError)
    if isinstance(error, TaskCancelledError):
        code = "operator_halted"
    elif recoverable:
        code = "workflow_interrupted"
    else:
        code=execution_terminal_code(error)
    if not code and not hasattr(error,"source_identity"):
        # A normal exception is still a failed attempt. Its FAILED writes
        # live in the transaction about to roll back, so detach them too.
        # It does not authorize a permanent project-wide execution latch.
        code = "workflow_failed"
        recoverable = True
    values=workflow.__dict__
    identity_keys = ("id", "project_id", "workflow_type")
    identity = values
    if not all(key in values for key in identity_keys):
        snapshot = values.get("_failure_identity")
        if not isinstance(snapshot, dict) or not all(key in snapshot for key in identity_keys):
            return
        if any(key in values and values[key] != snapshot[key] for key in identity_keys):
            return
        from sqlalchemy import inspect as sqlalchemy_inspect

        state = sqlalchemy_inspect(workflow, raiseerr=False)
        if state is not None and state.identity is not None and state.identity != (snapshot["id"],):
            return
        identity = snapshot
    if (not identity.get("id") or not isinstance(identity.get("workflow_type"), str)
            or not identity["workflow_type"]):
        return
    receipt={"workflow_id":str(identity["id"]),"project_id":str(identity["project_id"] or ""),
             "workflow_type":identity["workflow_type"],"step":values.get("current_step"),
             "code":code or "rewrite_source_rejected"}
    if code == "workflow_failed":
        receipt["error_type"] = type(error).__name__
    if recoverable:
        receipt["recoverable"] = True
    prior=list(getattr(error,"workflow_failures",()))
    if receipt not in prior:
        prior.append(receipt)
    error.workflow_failures=tuple(prior)


def pipeline_operation_stage(function):
    signature = inspect.signature(function)

    @wraps(function)
    async def wrapped(*args, **kwargs):
        values = signature.bind_partial(*args, **kwargs).arguments
        subject = {key: values.get(key) for key in (
            "project_slug", "slug", "project_id", "chapter_number", "scene_number",
        ) if values.get(key) is not None}
        namespace = f"pipeline:{function.__name__}:{content_hash(subject)}"
        scope = current_execution_scope()
        session = values.get("session")
        # Web/worker already own a scope. Direct service/CLI callers must get
        # the same persistent project scope and terminal latch, not just a hash.
        if scope is None and session is not None:
            from sqlalchemy.ext.asyncio import AsyncSession
            if isinstance(session, AsyncSession):
                from sqlalchemy import select
                from bestseller.infra.db.models import ProjectModel
                from bestseller.services.retry_ledger import project_execution_scope
                slug = values.get("project_slug") or values.get("slug")
                if slug:
                    project = await session.scalar(select(ProjectModel).where(ProjectModel.slug == slug))
                    if project is not None:
                        scope = project_execution_scope(project)
        with (bind_execution_scope(scope) if scope is not None else nullcontext()), bind_execution_operation(namespace):
            result = await function(*args, **kwargs)
            raise_if_execution_stopped()
            return result
    return wrapped
