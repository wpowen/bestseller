from __future__ import annotations

from enum import StrEnum


class ArtifactType(StrEnum):
    PREMISE = "premise"
    BOOK_SPEC = "book_spec"
    WORLD_SPEC = "world_spec"
    CAST_SPEC = "cast_spec"
    VOLUME_PLAN = "volume_plan"
    CHAPTER_OUTLINE_BATCH = "chapter_outline_batch"
    IF_STORY_BIBLE = "if_story_bible"
    IF_ARC_PLAN = "if_arc_plan"
    IF_WALKTHROUGH = "if_walkthrough"
    IF_ACT_PLAN = "if_act_plan"
    IF_ARC_SUMMARY = "if_arc_summary"
    IF_WORLD_SNAPSHOT = "if_world_snapshot"
    IF_BRANCH_DEFINITION = "if_branch_definition"
    ACT_PLAN = "act_plan"
    PLAN_VALIDATION = "plan_validation"
    VOLUME_CHAPTER_OUTLINE = "volume_chapter_outline"
    VOLUME_CAST_EXPANSION = "volume_cast_expansion"
    VOLUME_WORLD_DISCLOSURE = "volume_world_disclosure"
    VOLUME_WRITING_FEEDBACK = "volume_writing_feedback"
    CREATIVE_EXPLORATION = "creative_exploration"
    PROMOTIONAL_BRIEF = "promotional_brief"


class ProjectType(StrEnum):
    LINEAR = "linear"
    INTERACTIVE = "interactive"


class IFGenerationPhase(StrEnum):
    STORY_BIBLE = "story_bible"
    ACT_PLAN = "act_plan"
    ARC_PLAN = "arc_plan"
    CHAPTER_GEN = "chapter_gen"
    ARC_SUMMARY = "arc_summary"
    WORLD_SNAPSHOT = "world_snapshot"
    BRANCH_PLAN = "branch_plan"
    BRANCH_CHAPTER_GEN = "branch_chapter_gen"
    WALKTHROUGH = "walkthrough"
    ASSEMBLY = "assembly"
    COMPILE = "compile"
    COMPLETED = "completed"
    FAILED = "failed"


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

