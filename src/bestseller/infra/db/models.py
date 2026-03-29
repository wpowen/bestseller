from __future__ import annotations

from typing import Any
from uuid import UUID

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from bestseller.infra.db.base import Base, CreatedAtMixin, TimestampMixin, UUIDPrimaryKeyMixin


JSON_DICT = dict[str, Any]
JSON_LIST = list[Any]


class ProjectModel(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "projects"

    slug: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    language: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'zh-CN'"))
    genre: Mapped[str] = mapped_column(String(100), nullable=False)
    sub_genre: Mapped[str | None] = mapped_column(String(100))
    target_word_count: Mapped[int] = mapped_column(Integer, nullable=False)
    target_chapters: Mapped[int] = mapped_column(Integer, nullable=False)
    current_volume_number: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    current_chapter_number: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    audience: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'planning'"))
    project_type: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'linear'"))
    metadata_json: Mapped[JSON_DICT] = mapped_column("metadata", JSONB, nullable=False, default=dict)
    lock_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))

    style_guide: Mapped["StyleGuideModel | None"] = relationship(back_populates="project")


class StyleGuideModel(TimestampMixin, Base):
    __tablename__ = "style_guides"

    project_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        primary_key=True,
    )
    pov_type: Mapped[str] = mapped_column(String(32), nullable=False)
    tense: Mapped[str] = mapped_column(String(32), nullable=False)
    tone_keywords: Mapped[JSON_LIST] = mapped_column(JSONB, nullable=False, default=list)
    prose_style: Mapped[str | None] = mapped_column(Text)
    sentence_style: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'mixed'"))
    info_density: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'medium'"))
    dialogue_ratio: Mapped[float] = mapped_column(Numeric(5, 4), nullable=False, server_default=text("0.35"))
    taboo_words: Mapped[JSON_LIST] = mapped_column(JSONB, nullable=False, default=list)
    taboo_topics: Mapped[JSON_LIST] = mapped_column(JSONB, nullable=False, default=list)
    reference_works: Mapped[JSON_LIST] = mapped_column(JSONB, nullable=False, default=list)
    custom_rules: Mapped[JSON_LIST] = mapped_column(JSONB, nullable=False, default=list)

    project: Mapped[ProjectModel] = relationship(back_populates="style_guide")


class PlanningArtifactVersionModel(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "planning_artifact_versions"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "artifact_type",
            "scope_ref_id",
            "version_no",
            name="uq_planning_artifact_version",
        ),
    )

    project_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    artifact_type: Mapped[str] = mapped_column(String(64), nullable=False)
    scope_ref_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'approved'"))
    schema_version: Mapped[str] = mapped_column(String(32), nullable=False)
    content: Mapped[JSON_DICT] = mapped_column(JSONB, nullable=False)
    source_run_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    notes: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(String(64), nullable=False, server_default=text("'system'"))


class WorldRuleModel(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "world_rules"
    __table_args__ = (
        UniqueConstraint("project_id", "rule_code", name="uq_world_rule_code"),
        UniqueConstraint("project_id", "name", name="uq_world_rule_name"),
    )

    project_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    rule_code: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    story_consequence: Mapped[str | None] = mapped_column(Text)
    exploitation_potential: Mapped[str | None] = mapped_column(Text)
    metadata_json: Mapped[JSON_DICT] = mapped_column("metadata", JSONB, nullable=False, default=dict)


class LocationModel(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "locations"
    __table_args__ = (UniqueConstraint("project_id", "name", name="uq_location_name"),)

    project_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    location_type: Mapped[str] = mapped_column(String(100), nullable=False, server_default=text("'location'"))
    atmosphere: Mapped[str | None] = mapped_column(Text)
    key_rule_codes: Mapped[JSON_LIST] = mapped_column(JSONB, nullable=False, default=list)
    story_role: Mapped[str | None] = mapped_column(Text)
    metadata_json: Mapped[JSON_DICT] = mapped_column("metadata", JSONB, nullable=False, default=dict)


class FactionModel(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "factions"
    __table_args__ = (UniqueConstraint("project_id", "name", name="uq_faction_name"),)

    project_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    goal: Mapped[str | None] = mapped_column(Text)
    method: Mapped[str | None] = mapped_column(Text)
    relationship_to_protagonist: Mapped[str | None] = mapped_column(Text)
    internal_conflict: Mapped[str | None] = mapped_column(Text)
    metadata_json: Mapped[JSON_DICT] = mapped_column("metadata", JSONB, nullable=False, default=dict)


class CharacterModel(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "characters"
    __table_args__ = (
        UniqueConstraint("project_id", "name", name="uq_character_name"),
        Index("idx_characters_project_role", "project_id", "role"),
    )

    project_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    role: Mapped[str] = mapped_column(String(64), nullable=False, server_default=text("'supporting'"))
    age: Mapped[int | None] = mapped_column(Integer)
    background: Mapped[str | None] = mapped_column(Text)
    goal: Mapped[str | None] = mapped_column(Text)
    fear: Mapped[str | None] = mapped_column(Text)
    flaw: Mapped[str | None] = mapped_column(Text)
    strength: Mapped[str | None] = mapped_column(Text)
    secret: Mapped[str | None] = mapped_column(Text)
    arc_trajectory: Mapped[str | None] = mapped_column(Text)
    arc_state: Mapped[str | None] = mapped_column(Text)
    power_tier: Mapped[str | None] = mapped_column(String(100))
    knowledge_state_json: Mapped[JSON_DICT] = mapped_column("knowledge_state", JSONB, nullable=False, default=dict)
    is_pov_character: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("FALSE"))
    metadata_json: Mapped[JSON_DICT] = mapped_column("metadata", JSONB, nullable=False, default=dict)


class RelationshipModel(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "relationships"
    __table_args__ = (
        UniqueConstraint("project_id", "character_a_id", "character_b_id", name="uq_relationship_pair"),
        CheckConstraint("character_a_id <> character_b_id", name="relationship_self_reference"),
        CheckConstraint("strength >= -1 AND strength <= 1", name="relationship_strength_range"),
    )

    project_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    character_a_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("characters.id", ondelete="CASCADE"),
        nullable=False,
    )
    character_b_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("characters.id", ondelete="CASCADE"),
        nullable=False,
    )
    relationship_type: Mapped[str] = mapped_column(String(100), nullable=False)
    strength: Mapped[float] = mapped_column(Numeric(5, 4), nullable=False, server_default=text("0"))
    public_face: Mapped[str | None] = mapped_column(Text)
    private_reality: Mapped[str | None] = mapped_column(Text)
    tension_summary: Mapped[str | None] = mapped_column(Text)
    established_chapter_no: Mapped[int | None] = mapped_column(Integer)
    last_changed_chapter_no: Mapped[int | None] = mapped_column(Integer)
    metadata_json: Mapped[JSON_DICT] = mapped_column("metadata", JSONB, nullable=False, default=dict)


class CharacterStateSnapshotModel(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "character_state_snapshots"
    __table_args__ = (
        Index(
            "idx_character_state_snapshots_lookup",
            "project_id",
            "character_id",
            "chapter_number",
            "scene_number",
        ),
    )

    project_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    character_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("characters.id", ondelete="CASCADE"),
        nullable=False,
    )
    chapter_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("chapters.id"))
    scene_card_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("scene_cards.id"))
    chapter_number: Mapped[int] = mapped_column(Integer, nullable=False)
    scene_number: Mapped[int | None] = mapped_column(Integer)
    arc_state: Mapped[str | None] = mapped_column(Text)
    emotional_state: Mapped[str | None] = mapped_column(Text)
    physical_state: Mapped[str | None] = mapped_column(Text)
    power_tier: Mapped[str | None] = mapped_column(String(100))
    trust_map: Mapped[JSON_DICT] = mapped_column(JSONB, nullable=False, default=dict)
    beliefs: Mapped[JSON_LIST] = mapped_column(JSONB, nullable=False, default=list)
    notes: Mapped[str | None] = mapped_column(Text)


class VolumeModel(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "volumes"
    __table_args__ = (UniqueConstraint("project_id", "volume_number", name="uq_volume_number"),)

    project_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    volume_number: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    theme: Mapped[str | None] = mapped_column(Text)
    goal: Mapped[str | None] = mapped_column(Text)
    obstacle: Mapped[str | None] = mapped_column(Text)
    target_word_count: Mapped[int | None] = mapped_column(Integer)
    target_chapter_count: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'planned'"))
    metadata_json: Mapped[JSON_DICT] = mapped_column("metadata", JSONB, nullable=False, default=dict)


class WorldBackboneModel(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "world_backbones"
    __table_args__ = (UniqueConstraint("project_id", name="uq_world_backbone_project"),)

    project_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False, server_default=text("'全书世界主干'"))
    core_promise: Mapped[str] = mapped_column(Text, nullable=False)
    mainline_drive: Mapped[str] = mapped_column(Text, nullable=False)
    protagonist_destiny: Mapped[str | None] = mapped_column(Text)
    antagonist_axis: Mapped[str | None] = mapped_column(Text)
    thematic_melody: Mapped[str | None] = mapped_column(Text)
    world_frame: Mapped[str | None] = mapped_column(Text)
    invariant_elements: Mapped[JSON_LIST] = mapped_column(JSONB, nullable=False, default=list)
    stable_unknowns: Mapped[JSON_LIST] = mapped_column(JSONB, nullable=False, default=list)
    metadata_json: Mapped[JSON_DICT] = mapped_column("metadata", JSONB, nullable=False, default=dict)


class VolumeFrontierModel(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "volume_frontiers"
    __table_args__ = (
        UniqueConstraint("project_id", "volume_number", name="uq_volume_frontier_number"),
        Index(
            "idx_volume_frontiers_project_chapter_range",
            "project_id",
            "start_chapter_number",
            "end_chapter_number",
        ),
    )

    project_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    volume_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("volumes.id", ondelete="SET NULL"))
    volume_number: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    frontier_summary: Mapped[str] = mapped_column(Text, nullable=False)
    expansion_focus: Mapped[str | None] = mapped_column(Text)
    start_chapter_number: Mapped[int] = mapped_column(Integer, nullable=False)
    end_chapter_number: Mapped[int | None] = mapped_column(Integer)
    visible_rule_codes: Mapped[JSON_LIST] = mapped_column(JSONB, nullable=False, default=list)
    active_locations: Mapped[JSON_LIST] = mapped_column(JSONB, nullable=False, default=list)
    active_factions: Mapped[JSON_LIST] = mapped_column(JSONB, nullable=False, default=list)
    active_arc_codes: Mapped[JSON_LIST] = mapped_column(JSONB, nullable=False, default=list)
    future_reveal_codes: Mapped[JSON_LIST] = mapped_column(JSONB, nullable=False, default=list)
    metadata_json: Mapped[JSON_DICT] = mapped_column("metadata", JSONB, nullable=False, default=dict)


class DeferredRevealModel(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "deferred_reveals"
    __table_args__ = (
        UniqueConstraint("project_id", "reveal_code", name="uq_deferred_reveal_code"),
        Index(
            "idx_deferred_reveals_project_visibility",
            "project_id",
            "reveal_volume_number",
            "reveal_chapter_number",
            "status",
        ),
    )

    project_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    volume_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("volumes.id", ondelete="SET NULL"))
    reveal_code: Mapped[str] = mapped_column(String(64), nullable=False)
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[str] = mapped_column(String(64), nullable=False, server_default=text("'key_reveal'"))
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    source_volume_number: Mapped[int | None] = mapped_column(Integer)
    reveal_volume_number: Mapped[int] = mapped_column(Integer, nullable=False)
    reveal_chapter_number: Mapped[int] = mapped_column(Integer, nullable=False)
    guard_condition: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'scheduled'"))
    metadata_json: Mapped[JSON_DICT] = mapped_column("metadata", JSONB, nullable=False, default=dict)


class ExpansionGateModel(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "expansion_gates"
    __table_args__ = (
        UniqueConstraint("project_id", "gate_code", name="uq_expansion_gate_code"),
        Index(
            "idx_expansion_gates_project_unlock",
            "project_id",
            "unlock_volume_number",
            "unlock_chapter_number",
            "status",
        ),
    )

    project_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    volume_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("volumes.id", ondelete="SET NULL"))
    gate_code: Mapped[str] = mapped_column(String(64), nullable=False)
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    gate_type: Mapped[str] = mapped_column(String(64), nullable=False, server_default=text("'world_expansion'"))
    condition_summary: Mapped[str] = mapped_column(Text, nullable=False)
    unlocks_summary: Mapped[str] = mapped_column(Text, nullable=False)
    source_volume_number: Mapped[int | None] = mapped_column(Integer)
    unlock_volume_number: Mapped[int] = mapped_column(Integer, nullable=False)
    unlock_chapter_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'planned'"))
    metadata_json: Mapped[JSON_DICT] = mapped_column("metadata", JSONB, nullable=False, default=dict)


class ChapterModel(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "chapters"
    __table_args__ = (UniqueConstraint("project_id", "chapter_number", name="uq_chapter_number"),)

    project_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    volume_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("volumes.id"))
    chapter_number: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str | None] = mapped_column(String(200))
    chapter_goal: Mapped[str] = mapped_column(Text, nullable=False)
    opening_situation: Mapped[str | None] = mapped_column(Text)
    main_conflict: Mapped[str | None] = mapped_column(Text)
    hook_type: Mapped[str | None] = mapped_column(String(64))
    hook_description: Mapped[str | None] = mapped_column(Text)
    information_revealed: Mapped[JSON_LIST] = mapped_column(JSONB, nullable=False, default=list)
    information_withheld: Mapped[JSON_LIST] = mapped_column(JSONB, nullable=False, default=list)
    foreshadowing_actions: Mapped[JSON_DICT] = mapped_column(JSONB, nullable=False, default=dict)
    chapter_emotion_arc: Mapped[str | None] = mapped_column(Text)
    target_word_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("5500"))
    current_word_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    revision_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'planned'"))
    metadata_json: Mapped[JSON_DICT] = mapped_column("metadata", JSONB, nullable=False, default=dict)

    scenes: Mapped[list["SceneCardModel"]] = relationship(back_populates="chapter")


class SceneCardModel(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "scene_cards"
    __table_args__ = (
        UniqueConstraint("chapter_id", "scene_number", name="uq_scene_number"),
        Index("idx_scene_cards_project_status", "project_id", "status"),
    )

    project_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    chapter_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("chapters.id", ondelete="CASCADE"),
        nullable=False,
    )
    scene_number: Mapped[int] = mapped_column(Integer, nullable=False)
    scene_type: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str | None] = mapped_column(String(200))
    time_label: Mapped[str | None] = mapped_column(String(200))
    participants: Mapped[JSON_LIST] = mapped_column(JSONB, nullable=False, default=list)
    purpose: Mapped[JSON_DICT] = mapped_column(JSONB, nullable=False, default=dict)
    entry_state: Mapped[JSON_DICT] = mapped_column(JSONB, nullable=False, default=dict)
    exit_state: Mapped[JSON_DICT] = mapped_column(JSONB, nullable=False, default=dict)
    key_dialogue_beats: Mapped[JSON_LIST] = mapped_column(JSONB, nullable=False, default=list)
    sensory_anchors: Mapped[JSON_DICT] = mapped_column(JSONB, nullable=False, default=dict)
    forbidden_actions: Mapped[JSON_LIST] = mapped_column(JSONB, nullable=False, default=list)
    hook_requirement: Mapped[str | None] = mapped_column(Text)
    target_word_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1000"))
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'planned'"))
    metadata_json: Mapped[JSON_DICT] = mapped_column("metadata", JSONB, nullable=False, default=dict)

    chapter: Mapped[ChapterModel] = relationship(back_populates="scenes")


class PlotArcModel(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "plot_arcs"
    __table_args__ = (
        UniqueConstraint("project_id", "arc_code", name="uq_plot_arc_code"),
        Index("idx_plot_arcs_project_type_status", "project_id", "arc_type", "status"),
    )

    project_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    arc_code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    arc_type: Mapped[str] = mapped_column(String(64), nullable=False)
    promise: Mapped[str] = mapped_column(Text, nullable=False)
    core_question: Mapped[str] = mapped_column(Text, nullable=False)
    target_payoff: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'planned'"))
    scope_level: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'project'"))
    scope_volume_number: Mapped[int | None] = mapped_column(Integer)
    scope_chapter_number: Mapped[int | None] = mapped_column(Integer)
    description: Mapped[str | None] = mapped_column(Text)
    metadata_json: Mapped[JSON_DICT] = mapped_column("metadata", JSONB, nullable=False, default=dict)


class ArcBeatModel(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "arc_beats"
    __table_args__ = (
        UniqueConstraint(
            "plot_arc_id",
            "beat_order",
            "scope_level",
            "scope_volume_number",
            "scope_chapter_number",
            "scope_scene_number",
            name="uq_arc_beat_scope",
        ),
        Index(
            "idx_arc_beats_project_scope",
            "project_id",
            "scope_level",
            "scope_volume_number",
            "scope_chapter_number",
            "scope_scene_number",
        ),
    )

    project_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    plot_arc_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("plot_arcs.id", ondelete="CASCADE"),
        nullable=False,
    )
    beat_order: Mapped[int] = mapped_column(Integer, nullable=False)
    scope_level: Mapped[str] = mapped_column(String(32), nullable=False)
    scope_volume_number: Mapped[int | None] = mapped_column(Integer)
    scope_chapter_number: Mapped[int | None] = mapped_column(Integer)
    scope_scene_number: Mapped[int | None] = mapped_column(Integer)
    beat_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str | None] = mapped_column(String(200))
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    emotional_shift: Mapped[str | None] = mapped_column(Text)
    information_release: Mapped[str | None] = mapped_column(Text)
    expected_payoff: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'planned'"))
    metadata_json: Mapped[JSON_DICT] = mapped_column("metadata", JSONB, nullable=False, default=dict)


class ClueModel(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "clues"
    __table_args__ = (
        UniqueConstraint("project_id", "clue_code", name="uq_clue_code"),
        Index(
            "idx_clues_project_status",
            "project_id",
            "status",
            "planted_in_chapter_number",
            "expected_payoff_by_chapter_number",
        ),
    )

    project_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    plot_arc_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("plot_arcs.id", ondelete="SET NULL"),
    )
    clue_code: Mapped[str] = mapped_column(String(64), nullable=False)
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    clue_type: Mapped[str] = mapped_column(String(64), nullable=False, server_default=text("'foreshadow'"))
    description: Mapped[str] = mapped_column(Text, nullable=False)
    planted_in_volume_number: Mapped[int | None] = mapped_column(Integer)
    planted_in_chapter_number: Mapped[int | None] = mapped_column(Integer)
    planted_in_scene_number: Mapped[int | None] = mapped_column(Integer)
    expected_payoff_by_volume_number: Mapped[int | None] = mapped_column(Integer)
    expected_payoff_by_chapter_number: Mapped[int | None] = mapped_column(Integer)
    expected_payoff_by_scene_number: Mapped[int | None] = mapped_column(Integer)
    actual_paid_off_chapter_number: Mapped[int | None] = mapped_column(Integer)
    actual_paid_off_scene_number: Mapped[int | None] = mapped_column(Integer)
    reveal_guard: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'planted'"))
    metadata_json: Mapped[JSON_DICT] = mapped_column("metadata", JSONB, nullable=False, default=dict)


class PayoffModel(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "payoffs"
    __table_args__ = (
        UniqueConstraint("project_id", "payoff_code", name="uq_payoff_code"),
        Index(
            "idx_payoffs_project_status",
            "project_id",
            "status",
            "target_chapter_number",
            "actual_chapter_number",
        ),
    )

    project_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    plot_arc_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("plot_arcs.id", ondelete="SET NULL"),
    )
    source_clue_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("clues.id", ondelete="SET NULL"),
    )
    payoff_code: Mapped[str] = mapped_column(String(64), nullable=False)
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    target_volume_number: Mapped[int | None] = mapped_column(Integer)
    target_chapter_number: Mapped[int | None] = mapped_column(Integer)
    target_scene_number: Mapped[int | None] = mapped_column(Integer)
    actual_chapter_number: Mapped[int | None] = mapped_column(Integer)
    actual_scene_number: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'planned'"))
    metadata_json: Mapped[JSON_DICT] = mapped_column("metadata", JSONB, nullable=False, default=dict)


class ChapterContractModel(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "chapter_contracts"
    __table_args__ = (
        UniqueConstraint("project_id", "chapter_id", name="uq_chapter_contract_chapter"),
        UniqueConstraint("project_id", "chapter_number", name="uq_chapter_contract_number"),
    )

    project_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    chapter_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("chapters.id", ondelete="CASCADE"),
        nullable=False,
    )
    chapter_number: Mapped[int] = mapped_column(Integer, nullable=False)
    contract_summary: Mapped[str] = mapped_column(Text, nullable=False)
    opening_state: Mapped[JSON_DICT] = mapped_column(JSONB, nullable=False, default=dict)
    core_conflict: Mapped[str | None] = mapped_column(Text)
    emotional_shift: Mapped[str | None] = mapped_column(Text)
    information_release: Mapped[str | None] = mapped_column(Text)
    closing_hook: Mapped[str | None] = mapped_column(Text)
    primary_arc_codes: Mapped[JSON_LIST] = mapped_column(JSONB, nullable=False, default=list)
    supporting_arc_codes: Mapped[JSON_LIST] = mapped_column(JSONB, nullable=False, default=list)
    active_arc_beat_ids: Mapped[JSON_LIST] = mapped_column(JSONB, nullable=False, default=list)
    planted_clue_codes: Mapped[JSON_LIST] = mapped_column(JSONB, nullable=False, default=list)
    due_payoff_codes: Mapped[JSON_LIST] = mapped_column(JSONB, nullable=False, default=list)
    metadata_json: Mapped[JSON_DICT] = mapped_column("metadata", JSONB, nullable=False, default=dict)


class SceneContractModel(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "scene_contracts"
    __table_args__ = (
        UniqueConstraint("project_id", "scene_card_id", name="uq_scene_contract_scene"),
        Index("idx_scene_contracts_project_position", "project_id", "chapter_number", "scene_number"),
    )

    project_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    chapter_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("chapters.id", ondelete="CASCADE"),
        nullable=False,
    )
    scene_card_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("scene_cards.id", ondelete="CASCADE"),
        nullable=False,
    )
    chapter_number: Mapped[int] = mapped_column(Integer, nullable=False)
    scene_number: Mapped[int] = mapped_column(Integer, nullable=False)
    contract_summary: Mapped[str] = mapped_column(Text, nullable=False)
    entry_state: Mapped[JSON_DICT] = mapped_column(JSONB, nullable=False, default=dict)
    exit_state: Mapped[JSON_DICT] = mapped_column(JSONB, nullable=False, default=dict)
    core_conflict: Mapped[str | None] = mapped_column(Text)
    emotional_shift: Mapped[str | None] = mapped_column(Text)
    information_release: Mapped[str | None] = mapped_column(Text)
    tail_hook: Mapped[str | None] = mapped_column(Text)
    arc_codes: Mapped[JSON_LIST] = mapped_column(JSONB, nullable=False, default=list)
    arc_beat_ids: Mapped[JSON_LIST] = mapped_column(JSONB, nullable=False, default=list)
    planted_clue_codes: Mapped[JSON_LIST] = mapped_column(JSONB, nullable=False, default=list)
    payoff_codes: Mapped[JSON_LIST] = mapped_column(JSONB, nullable=False, default=list)
    metadata_json: Mapped[JSON_DICT] = mapped_column("metadata", JSONB, nullable=False, default=dict)


class EmotionTrackModel(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "emotion_tracks"
    __table_args__ = (
        UniqueConstraint("project_id", "track_code", name="uq_emotion_track_code"),
        Index("idx_emotion_tracks_project_type_status", "project_id", "track_type", "status"),
    )

    project_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    track_code: Mapped[str] = mapped_column(String(64), nullable=False)
    track_type: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    character_a_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("characters.id", ondelete="SET NULL"),
    )
    character_b_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("characters.id", ondelete="SET NULL"),
    )
    character_a_label: Mapped[str] = mapped_column(String(200), nullable=False)
    character_b_label: Mapped[str] = mapped_column(String(200), nullable=False)
    relationship_type: Mapped[str | None] = mapped_column(String(100))
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    desired_payoff: Mapped[str | None] = mapped_column(Text)
    trust_level: Mapped[float] = mapped_column(Numeric(5, 4), nullable=False, server_default=text("0.5"))
    attraction_level: Mapped[float] = mapped_column(Numeric(5, 4), nullable=False, server_default=text("0"))
    distance_level: Mapped[float] = mapped_column(Numeric(5, 4), nullable=False, server_default=text("0.5"))
    conflict_level: Mapped[float] = mapped_column(Numeric(5, 4), nullable=False, server_default=text("0.5"))
    intimacy_stage: Mapped[str] = mapped_column(String(64), nullable=False, server_default=text("'setup'"))
    last_shift_chapter_number: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'active'"))
    metadata_json: Mapped[JSON_DICT] = mapped_column("metadata", JSONB, nullable=False, default=dict)


class AntagonistPlanModel(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "antagonist_plans"
    __table_args__ = (
        UniqueConstraint("project_id", "plan_code", name="uq_antagonist_plan_code"),
        Index(
            "idx_antagonist_plans_project_scope_status",
            "project_id",
            "scope_volume_number",
            "target_chapter_number",
            "status",
        ),
    )

    project_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    antagonist_character_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("characters.id", ondelete="SET NULL"),
    )
    antagonist_label: Mapped[str] = mapped_column(String(200), nullable=False)
    plan_code: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    threat_type: Mapped[str] = mapped_column(String(64), nullable=False, server_default=text("'pressure'"))
    goal: Mapped[str] = mapped_column(Text, nullable=False)
    current_move: Mapped[str] = mapped_column(Text, nullable=False)
    next_countermove: Mapped[str] = mapped_column(Text, nullable=False)
    escalation_condition: Mapped[str | None] = mapped_column(Text)
    reveal_timing: Mapped[str | None] = mapped_column(String(100))
    scope_volume_number: Mapped[int | None] = mapped_column(Integer)
    target_chapter_number: Mapped[int | None] = mapped_column(Integer)
    pressure_level: Mapped[float] = mapped_column(Numeric(5, 4), nullable=False, server_default=text("0.6"))
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'active'"))
    metadata_json: Mapped[JSON_DICT] = mapped_column("metadata", JSONB, nullable=False, default=dict)


class NarrativeTreeNodeModel(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "narrative_tree_nodes"
    __table_args__ = (
        UniqueConstraint("project_id", "node_path", name="uq_narrative_tree_node_path"),
        Index("idx_narrative_tree_project_parent", "project_id", "parent_path"),
        Index("idx_narrative_tree_project_type_depth", "project_id", "node_type", "depth"),
        Index(
            "idx_narrative_tree_project_scope",
            "project_id",
            "scope_level",
            "scope_volume_number",
            "scope_chapter_number",
            "scope_scene_number",
        ),
    )

    project_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    node_path: Mapped[str] = mapped_column(String(512), nullable=False)
    parent_path: Mapped[str | None] = mapped_column(String(512))
    depth: Mapped[int] = mapped_column(Integer, nullable=False)
    node_type: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    summary: Mapped[str | None] = mapped_column(Text)
    body_md: Mapped[str] = mapped_column(Text, nullable=False)
    source_type: Mapped[str] = mapped_column(String(64), nullable=False)
    source_ref_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    scope_level: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'project'"))
    scope_volume_number: Mapped[int | None] = mapped_column(Integer)
    scope_chapter_number: Mapped[int | None] = mapped_column(Integer)
    scope_scene_number: Mapped[int | None] = mapped_column(Integer)
    lexical_document: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    metadata_json: Mapped[JSON_DICT] = mapped_column("metadata", JSONB, nullable=False, default=dict)


class SceneDraftVersionModel(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "scene_draft_versions"
    __table_args__ = (
        UniqueConstraint("scene_card_id", "version_no", name="uq_scene_draft_version"),
        Index("uq_scene_draft_current", "scene_card_id", unique=True, postgresql_where=text("is_current")),
    )

    project_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    scene_card_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("scene_cards.id", ondelete="CASCADE"),
        nullable=False,
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    content_md: Mapped[str] = mapped_column(Text, nullable=False)
    word_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("TRUE"))
    model_name: Mapped[str | None] = mapped_column(String(128))
    prompt_template: Mapped[str | None] = mapped_column(String(128))
    prompt_version: Mapped[str | None] = mapped_column(String(32))
    prompt_hash: Mapped[str | None] = mapped_column(String(128))
    generation_params: Mapped[JSON_DICT] = mapped_column(JSONB, nullable=False, default=dict)
    llm_run_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))


class ChapterDraftVersionModel(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "chapter_draft_versions"
    __table_args__ = (
        UniqueConstraint("chapter_id", "version_no", name="uq_chapter_draft_version"),
        Index("uq_chapter_draft_current", "chapter_id", unique=True, postgresql_where=text("is_current")),
    )

    project_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    chapter_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("chapters.id", ondelete="CASCADE"),
        nullable=False,
    )
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    content_md: Mapped[str] = mapped_column(Text, nullable=False)
    word_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    assembled_from_scene_draft_ids: Mapped[JSON_LIST] = mapped_column(JSONB, nullable=False, default=list)
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("TRUE"))
    llm_run_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))


class CanonFactModel(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "canon_facts"
    __table_args__ = (
        Index(
            "uq_canon_current_fact",
            "project_id",
            "subject_type",
            "subject_id",
            "predicate",
            unique=True,
            postgresql_where=text("is_current"),
        ),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="confidence_range"),
    )

    project_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    subject_type: Mapped[str] = mapped_column(String(64), nullable=False)
    subject_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    subject_label: Mapped[str] = mapped_column(String(255), nullable=False)
    predicate: Mapped[str] = mapped_column(String(128), nullable=False)
    fact_type: Mapped[str] = mapped_column(String(64), nullable=False)
    value_json: Mapped[JSON_DICT] = mapped_column(JSONB, nullable=False)
    confidence: Mapped[float] = mapped_column(Numeric(5, 4), nullable=False, server_default=text("1"))
    source_type: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'extracted'"))
    source_scene_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("scene_cards.id"))
    source_chapter_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("chapters.id"))
    valid_from_chapter_no: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    valid_to_chapter_no: Mapped[int | None] = mapped_column(Integer)
    supersedes_fact_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("canon_facts.id"))
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("TRUE"))
    tags: Mapped[JSON_LIST] = mapped_column(JSONB, nullable=False, default=list)
    notes: Mapped[str | None] = mapped_column(Text)


class TimelineEventModel(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "timeline_events"
    __table_args__ = (Index("idx_timeline_project_story_order", "project_id", "story_order"),)

    project_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    chapter_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("chapters.id"))
    scene_card_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("scene_cards.id"))
    event_name: Mapped[str] = mapped_column(String(255), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    story_time_label: Mapped[str] = mapped_column(String(255), nullable=False)
    story_order: Mapped[float] = mapped_column(Numeric(14, 4), nullable=False)
    participant_ids: Mapped[JSON_LIST] = mapped_column(JSONB, nullable=False, default=list)
    consequences: Mapped[JSON_LIST] = mapped_column(JSONB, nullable=False, default=list)
    duration_hint: Mapped[str | None] = mapped_column(String(255))
    is_revealed: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("TRUE"))
    metadata_json: Mapped[JSON_DICT] = mapped_column("metadata", JSONB, nullable=False, default=dict)


class ReviewReportModel(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "review_reports"
    __table_args__ = (Index("idx_review_reports_target", "target_type", "target_id", "created_at"),)

    project_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    target_type: Mapped[str] = mapped_column(String(32), nullable=False)
    target_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    reviewer_type: Mapped[str] = mapped_column(String(64), nullable=False)
    verdict: Mapped[str] = mapped_column(String(16), nullable=False)
    severity_max: Mapped[str | None] = mapped_column(String(16))
    structured_output: Mapped[JSON_DICT] = mapped_column(JSONB, nullable=False, default=dict)
    llm_run_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))


class QualityScoreModel(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "quality_scores"
    __table_args__ = (
        Index(
            "uq_quality_scores_current",
            "target_type",
            "target_id",
            unique=True,
            postgresql_where=text("is_current"),
        ),
    )

    project_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    target_type: Mapped[str] = mapped_column(String(32), nullable=False)
    target_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    review_report_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("review_reports.id"))
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("TRUE"))
    score_overall: Mapped[float] = mapped_column(Numeric(4, 2), nullable=False)
    score_goal: Mapped[float | None] = mapped_column(Numeric(4, 2))
    score_conflict: Mapped[float | None] = mapped_column(Numeric(4, 2))
    score_emotion: Mapped[float | None] = mapped_column(Numeric(4, 2))
    score_dialogue: Mapped[float | None] = mapped_column(Numeric(4, 2))
    score_style: Mapped[float | None] = mapped_column(Numeric(4, 2))
    score_hook: Mapped[float | None] = mapped_column(Numeric(4, 2))
    evidence_summary: Mapped[JSON_DICT] = mapped_column(JSONB, nullable=False, default=dict)


class RewriteTaskModel(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "rewrite_tasks"
    __table_args__ = (
        Index(
            "idx_rewrite_tasks_pending",
            "project_id",
            "priority",
            "created_at",
            postgresql_where=text("status IN ('pending','queued')"),
        ),
    )

    project_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    parent_task_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("rewrite_tasks.id"))
    trigger_type: Mapped[str] = mapped_column(String(64), nullable=False)
    trigger_source_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    rewrite_strategy: Mapped[str] = mapped_column(String(64), nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("5"))
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'pending'"))
    instructions: Mapped[str] = mapped_column(Text, nullable=False)
    context_required: Mapped[JSON_LIST] = mapped_column(JSONB, nullable=False, default=list)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    error_log: Mapped[str | None] = mapped_column(Text)
    metadata_json: Mapped[JSON_DICT] = mapped_column("metadata", JSONB, nullable=False, default=dict)


class RewriteImpactModel(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "rewrite_impacts"
    __table_args__ = (
        Index("idx_rewrite_impacts_task", "rewrite_task_id", "impact_level", "impact_score"),
        CheckConstraint("impact_score >= 0 AND impact_score <= 1", name="rewrite_impact_score_range"),
    )

    rewrite_task_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("rewrite_tasks.id", ondelete="CASCADE"),
        nullable=False,
    )
    impacted_type: Mapped[str] = mapped_column(String(32), nullable=False)
    impacted_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    impact_level: Mapped[str] = mapped_column(String(16), nullable=False)
    impact_score: Mapped[float] = mapped_column(Numeric(5, 4), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)


class WorkflowRunModel(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "workflow_runs"
    __table_args__ = (
        Index("idx_workflow_runs_pending", "status", "created_at", postgresql_where=text("status IN ('pending','queued')")),
    )

    project_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"))
    workflow_type: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    scope_type: Mapped[str | None] = mapped_column(String(32))
    scope_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    requested_by: Mapped[str] = mapped_column(String(64), nullable=False, server_default=text("'system'"))
    current_step: Mapped[str | None] = mapped_column(String(128))
    error_message: Mapped[str | None] = mapped_column(Text)
    metadata_json: Mapped[JSON_DICT] = mapped_column("metadata", JSONB, nullable=False, default=dict)


class WorkflowStepRunModel(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "workflow_step_runs"
    __table_args__ = (
        UniqueConstraint("workflow_run_id", "step_order", name="uq_workflow_step_order"),
    )

    workflow_run_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("workflow_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    step_name: Mapped[str] = mapped_column(String(128), nullable=False)
    step_order: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    input_ref: Mapped[JSON_DICT] = mapped_column(JSONB, nullable=False, default=dict)
    output_ref: Mapped[JSON_DICT] = mapped_column(JSONB, nullable=False, default=dict)
    error_message: Mapped[str | None] = mapped_column(Text)


class LlmRunModel(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "llm_runs"
    __table_args__ = (Index("idx_llm_runs_project_created", "project_id", "created_at"),)

    project_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("projects.id", ondelete="SET NULL"))
    workflow_run_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("workflow_runs.id", ondelete="SET NULL"))
    step_run_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True), ForeignKey("workflow_step_runs.id", ondelete="SET NULL"))
    logical_role: Mapped[str] = mapped_column(String(64), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model_name: Mapped[str] = mapped_column(String(128), nullable=False)
    prompt_template: Mapped[str | None] = mapped_column(String(128))
    prompt_version: Mapped[str | None] = mapped_column(String(32))
    prompt_hash: Mapped[str | None] = mapped_column(String(128))
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    finish_reason: Mapped[str | None] = mapped_column(String(64))
    request_payload_ref: Mapped[str | None] = mapped_column(Text)
    response_payload_ref: Mapped[str | None] = mapped_column(Text)
    metadata_json: Mapped[JSON_DICT] = mapped_column("metadata", JSONB, nullable=False, default=dict)


class ExportArtifactModel(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "export_artifacts"

    project_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    export_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_scope: Mapped[str] = mapped_column(String(32), nullable=False)
    source_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    storage_uri: Mapped[str] = mapped_column(Text, nullable=False)
    checksum: Mapped[str | None] = mapped_column(String(128))
    version_label: Mapped[str | None] = mapped_column(String(64))
    created_by_run_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("workflow_runs.id", ondelete="SET NULL"),
    )


class RetrievalChunkModel(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "retrieval_chunks"
    __table_args__ = (
        UniqueConstraint("source_type", "source_id", "chunk_index", name="uq_retrieval_chunk"),
        Index("idx_retrieval_chunks_project_source", "project_id", "source_type", "source_id"),
        Index(
            "idx_retrieval_chunks_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    project_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_type: Mapped[str] = mapped_column(String(64), nullable=False)
    source_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    chunk_text: Mapped[str] = mapped_column(Text, nullable=False)
    embedding_model: Mapped[str] = mapped_column(String(128), nullable=False)
    embedding_dim: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1024"))
    embedding: Mapped[Any] = mapped_column(Vector(1024), nullable=False)
    lexical_document: Mapped[str | None] = mapped_column(Text)
    metadata_json: Mapped[JSON_DICT] = mapped_column("metadata", JSONB, nullable=False, default=dict)


class IFGenerationRunModel(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Tracks a single interactive fiction generation pipeline run for a project."""

    __tablename__ = "if_generation_runs"

    project_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    phase: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'story_bible'"))
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'pending'"))
    book_id: Mapped[str | None] = mapped_column(String(128))
    # FK to bible / arc / walkthrough planning artifacts (nullable until each phase completes)
    bible_artifact_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("planning_artifact_versions.id", ondelete="SET NULL"),
    )
    arc_artifact_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("planning_artifact_versions.id", ondelete="SET NULL"),
    )
    walkthrough_artifact_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("planning_artifact_versions.id", ondelete="SET NULL"),
    )
    total_chapters: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    completed_chapters: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    output_dir: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)
    config_snapshot: Mapped[JSON_DICT] = mapped_column(JSONB, nullable=False, default=dict)
    # 多分支支持字段
    total_routes: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    act_plan_json: Mapped[JSON_LIST | None] = mapped_column(JSONB)
    generation_mode: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'simple'")
    )  # "simple" | "branched" | "extended"


class IFActPlanModel(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """Acts-level story structure plan (幕级全书规划)."""

    __tablename__ = "if_act_plans"
    __table_args__ = (
        UniqueConstraint("project_id", "run_id", "act_id", name="uq_if_act_plan"),
        Index("idx_if_act_plans_run", "project_id", "run_id"),
    )

    project_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    run_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("if_generation_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    act_id: Mapped[str] = mapped_column(String(32), nullable=False)  # "act_01"..."act_05"
    act_index: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    chapter_start: Mapped[int] = mapped_column(Integer, nullable=False)
    chapter_end: Mapped[int] = mapped_column(Integer, nullable=False)
    act_goal: Mapped[str] = mapped_column(Text, nullable=False)
    core_theme: Mapped[str | None] = mapped_column(String(100))
    dominant_emotion: Mapped[str | None] = mapped_column(String(64))
    climax_chapter: Mapped[int | None] = mapped_column(Integer)
    entry_state: Mapped[str | None] = mapped_column(Text)
    exit_state: Mapped[str | None] = mapped_column(Text)
    payoff_promises: Mapped[JSON_LIST] = mapped_column(JSONB, nullable=False, default=list)
    branch_opportunities: Mapped[JSON_LIST] = mapped_column(JSONB, nullable=False, default=list)
    arc_breakdown: Mapped[JSON_LIST] = mapped_column(JSONB, nullable=False, default=list)


class IFRouteDefinitionModel(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """Defines a story route/branch (硬分支路线定义)."""

    __tablename__ = "if_route_definitions"
    __table_args__ = (
        UniqueConstraint("project_id", "run_id", "route_id", name="uq_if_route"),
        Index("idx_if_route_definitions_run", "project_id", "run_id"),
    )

    project_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    run_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("if_generation_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    route_id: Mapped[str] = mapped_column(String(64), nullable=False)  # "mainline"|"branch_warrior"
    route_type: Mapped[str] = mapped_column(String(32), nullable=False)  # "mainline"|"branch"|"hidden"
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    branch_start_chapter: Mapped[int | None] = mapped_column(Integer)
    merge_chapter: Mapped[int | None] = mapped_column(Integer)
    entry_condition: Mapped[JSON_DICT] = mapped_column(JSONB, nullable=False, default=dict)
    merge_contract: Mapped[JSON_DICT] = mapped_column(JSONB, nullable=False, default=dict)
    generation_status: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'planned'")
    )  # "planned"|"generating"|"completed"|"failed"
    chapter_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    output_arc_file: Mapped[str | None] = mapped_column(Text)


class IFWorldStateSnapshotModel(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """World state snapshot taken at end of each arc (世界状态快照)."""

    __tablename__ = "if_world_state_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "project_id", "run_id", "route_id", "snapshot_chapter", name="uq_if_world_snapshot"
        ),
        Index(
            "idx_if_world_snapshots_lookup",
            "project_id",
            "run_id",
            "route_id",
            "snapshot_chapter",
        ),
    )

    project_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    run_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("if_generation_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    route_id: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default=text("'mainline'")
    )
    snapshot_chapter: Mapped[int] = mapped_column(Integer, nullable=False)
    arc_index: Mapped[int] = mapped_column(Integer, nullable=False)
    character_states: Mapped[JSON_DICT] = mapped_column(JSONB, nullable=False, default=dict)
    faction_states: Mapped[JSON_DICT] = mapped_column(JSONB, nullable=False, default=dict)
    revealed_truths: Mapped[JSON_LIST] = mapped_column(JSONB, nullable=False, default=list)
    active_threats: Mapped[JSON_LIST] = mapped_column(JSONB, nullable=False, default=list)
    planted_unrevealed: Mapped[JSON_LIST] = mapped_column(JSONB, nullable=False, default=list)
    power_rankings: Mapped[JSON_LIST] = mapped_column(JSONB, nullable=False, default=list)
    world_summary: Mapped[str | None] = mapped_column(Text)  # 200字自然语言，直接注入prompt


class IFArcSummaryModel(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """Arc-level summary generated after each arc completes (Arc级摘要)."""

    __tablename__ = "if_arc_summaries"
    __table_args__ = (
        UniqueConstraint("project_id", "run_id", "route_id", "arc_index", name="uq_if_arc_summary"),
        Index("idx_if_arc_summaries_lookup", "project_id", "run_id", "route_id", "arc_index"),
    )

    project_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    run_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("if_generation_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    route_id: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default=text("'mainline'")
    )
    arc_index: Mapped[int] = mapped_column(Integer, nullable=False)
    chapter_start: Mapped[int] = mapped_column(Integer, nullable=False)
    chapter_end: Mapped[int] = mapped_column(Integer, nullable=False)
    act_id: Mapped[str | None] = mapped_column(String(32))
    protagonist_growth: Mapped[str | None] = mapped_column(Text)
    relationship_changes: Mapped[JSON_LIST] = mapped_column(JSONB, nullable=False, default=list)
    unresolved_threads: Mapped[JSON_LIST] = mapped_column(JSONB, nullable=False, default=list)
    power_level_summary: Mapped[str | None] = mapped_column(Text)
    next_arc_setup: Mapped[str | None] = mapped_column(Text)  # 下一Arc规划的铺垫
    open_clues: Mapped[JSON_LIST] = mapped_column(JSONB, nullable=False, default=list)
    resolved_clues: Mapped[JSON_LIST] = mapped_column(JSONB, nullable=False, default=list)


class IFCanonFactModel(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """IF-specific canon facts with route awareness (IF专用事实库，支持路线感知)."""

    __tablename__ = "if_canon_facts"
    __table_args__ = (
        Index(
            "idx_if_canon_facts_lookup",
            "project_id",
            "run_id",
            "route_id",
            "chapter_number",
            "importance",
        ),
    )

    project_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    run_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("if_generation_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    route_id: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default=text("'all'")
    )  # "all"=全路线适用 | "mainline" | "branch_X"
    chapter_number: Mapped[int] = mapped_column(Integer, nullable=False)
    fact_type: Mapped[str] = mapped_column(
        String(64), nullable=False
    )  # "chapter_summary"|"character_state"|"event"|"world_rule"
    subject_label: Mapped[str] = mapped_column(String(255), nullable=False)
    fact_body: Mapped[str] = mapped_column(Text, nullable=False)  # 自然语言，直接注入prompt
    importance: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'major'")
    )  # "critical"|"major"|"minor"
    is_payoff_of_clue: Mapped[str | None] = mapped_column(String(64))
