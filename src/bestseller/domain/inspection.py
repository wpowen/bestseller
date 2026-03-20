from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class WorkflowStepRunRead(BaseModel):
    step_run_id: UUID
    step_name: str = Field(min_length=1)
    step_order: int = Field(ge=0)
    status: str = Field(min_length=1)
    created_at: datetime
    input_ref: dict[str, Any] = Field(default_factory=dict)
    output_ref: dict[str, Any] = Field(default_factory=dict)
    error_message: str | None = None


class WorkflowRunRead(BaseModel):
    workflow_run_id: UUID
    workflow_type: str = Field(min_length=1)
    status: str = Field(min_length=1)
    scope_type: str | None = None
    scope_id: UUID | None = None
    requested_by: str = Field(min_length=1)
    current_step: str | None = None
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)
    step_count: int = Field(ge=0)
    completed_step_count: int = Field(ge=0)
    failed_step_count: int = Field(ge=0)
    steps: list[WorkflowStepRunRead] = Field(default_factory=list)


class ProjectWorkflowOverviewRead(BaseModel):
    project_id: UUID
    project_slug: str = Field(min_length=1)
    project_status: str = Field(min_length=1)
    run_count: int = Field(ge=0)
    completed_run_count: int = Field(ge=0)
    failed_run_count: int = Field(ge=0)
    latest_run_id: UUID | None = None
    latest_run_status: str | None = None
    runs: list[WorkflowRunRead] = Field(default_factory=list)
