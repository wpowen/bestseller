from __future__ import annotations

from uuid import UUID
from datetime import datetime

from pydantic import BaseModel, Field


class PlatformCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    platform_type: str = Field(pattern=r"^(fanqie|qidian|qimao|custom)$")
    api_base_url: str | None = None
    credentials: dict[str, str] | None = None  # encrypted before storage
    rate_limit_rpm: int = Field(default=10, ge=1)


class PlatformResponse(BaseModel):
    id: UUID
    project_id: UUID
    name: str
    platform_type: str
    api_base_url: str | None
    rate_limit_rpm: int
    created_at: datetime

    model_config = {"from_attributes": True}


class ScheduleCreateRequest(BaseModel):
    platform_id: UUID
    cron_expression: str = Field(
        min_length=9,
        description="Cron expression, e.g. '0 8 * * *' for 08:00 daily",
    )
    timezone: str = "Asia/Shanghai"
    start_chapter: int = Field(default=1, ge=1)
    chapters_per_release: int = Field(default=1, ge=1)


class ScheduleResponse(BaseModel):
    id: UUID
    project_id: UUID
    platform_id: UUID
    cron_expression: str
    timezone: str
    start_chapter: int
    current_chapter: int
    chapters_per_release: int
    status: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class PublishHistoryItem(BaseModel):
    id: UUID
    chapter_number: int
    status: str
    published_at: datetime | None
    platform_chapter_id: str | None
    error_message: str | None
    retry_count: int
    created_at: datetime

    model_config = {"from_attributes": True}


class PublishHistoryResponse(BaseModel):
    items: list[PublishHistoryItem]
    total: int
