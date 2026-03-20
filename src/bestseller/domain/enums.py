from __future__ import annotations

from enum import StrEnum


class ArtifactType(StrEnum):
    PREMISE = "premise"
    BOOK_SPEC = "book_spec"
    WORLD_SPEC = "world_spec"
    CAST_SPEC = "cast_spec"
    VOLUME_PLAN = "volume_plan"
    CHAPTER_OUTLINE_BATCH = "chapter_outline_batch"


class ProjectStatus(StrEnum):
    PLANNING = "planning"
    WRITING = "writing"
    REVISING = "revising"
    PAUSED = "paused"
    COMPLETED = "completed"
    ARCHIVED = "archived"


class VolumeStatus(StrEnum):
    PLANNED = "planned"
    WRITING = "writing"
    REVIEW = "review"
    COMPLETE = "complete"


class ChapterStatus(StrEnum):
    PLANNED = "planned"
    OUTLINING = "outlining"
    DRAFTING = "drafting"
    REVIEW = "review"
    REVISION = "revision"
    COMPLETE = "complete"


class SceneStatus(StrEnum):
    PLANNED = "planned"
    DRAFTED = "drafted"
    REVIEWED = "reviewed"
    APPROVED = "approved"
    NEEDS_REWRITE = "needs_rewrite"


class WorkflowStatus(StrEnum):
    PENDING = "pending"
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_HUMAN = "waiting_human"
    FAILED = "failed"
    COMPLETED = "completed"
    CANCELLED = "cancelled"

