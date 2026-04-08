from __future__ import annotations

from typing import Any
from uuid import UUID
from datetime import datetime

from pydantic import BaseModel


class TaskResponse(BaseModel):
    task_id: str
    workflow_run_id: UUID | None = None
    status: str  # queued | running | completed | failed | cancelled
    current_step: str | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    model_config = {"from_attributes": True}


class TaskEnqueuedResponse(BaseModel):
    task_id: str
    status: str = "queued"
    message: str = "Task enqueued successfully"


class AutowriteRequest(BaseModel):
    premise: str | None = None  # overrides project premise if provided


class PipelineRequest(BaseModel):
    force: bool = False  # rerun even if already completed
