"""Persist the append-only ranking evidence authority ledger."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0042_ranking_evidence_authority"
down_revision = "0041_story_engine_reader_study"
branch_labels = None
depends_on = None

JSON = sa.JSON().with_variant(JSONB(), "postgresql")


def upgrade() -> None:
    uuid = sa.Uuid()
    now = sa.func.now()
    op.create_table(
        "ranking_evaluation_runs",
        sa.Column("id", uuid, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "project_id", uuid, sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("candidate_revision", sa.String(128), nullable=False),
        sa.Column("candidate_hash", sa.String(64), nullable=False),
        sa.Column("target_platform", sa.String(64)),
        sa.Column("target_list", sa.String(128)),
        sa.Column("target_genre", sa.String(128)),
        sa.Column("preregistration_json", JSON, nullable=False),
        sa.Column("preregistration_hash", sa.String(64), nullable=False),
        sa.Column("analysis_plan_json", JSON, nullable=False),
        sa.Column("analysis_plan_hash", sa.String(64), nullable=False),
        sa.Column("required_cell_manifest_json", JSON, nullable=False),
        sa.Column("required_cell_manifest_hash", sa.String(64), nullable=False),
        sa.Column("roster_json", JSON, nullable=False),
        sa.Column("roster_hash", sa.String(64), nullable=False),
        sa.Column("sealed_candidate_manifest_hash", sa.String(64), nullable=False),
        sa.Column("packaging_contract_hash", sa.String(64)),
        sa.Column("status", sa.String(16), nullable=False, server_default=sa.text("'planned'")),
        sa.Column(
            "next_event_sequence", sa.BigInteger, nullable=False, server_default=sa.text("1")
        ),
        sa.Column("last_event_hash", sa.String(64), nullable=False, server_default=sa.text("''")),
        sa.Column("frozen_at", sa.DateTime(timezone=True)),
        sa.Column("finalized_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=now),
        sa.CheckConstraint(
            "status IN ('planned','running','finalized','blocked')",
            name="ck_ranking_evaluation_runs_ranking_run_status",
        ),
    )
    op.create_index(
        "idx_ranking_run_project_status", "ranking_evaluation_runs", ["project_id", "status"]
    )

    op.create_table(
        "ranking_evidence_artifacts",
        sa.Column("id", uuid, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "run_id",
            uuid,
            sa.ForeignKey("ranking_evaluation_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("evidence_layer", sa.String(32), nullable=False),
        sa.Column("source_kind", sa.String(32), nullable=False),
        sa.Column("artifact_uri", sa.Text, nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("raw_event_ledger_hash", sa.String(64)),
        sa.Column("sample_count", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("cluster_count", sa.Integer, nullable=False, server_default=sa.text("0")),
        sa.Column("verdict", sa.String(32)),
        sa.Column("blocking_codes", JSON, nullable=False, server_default=sa.text("'[]'")),
        sa.Column("unknowns", JSON, nullable=False, server_default=sa.text("'[]'")),
        sa.Column("producer_identity", sa.String(128), nullable=False),
        sa.Column("producer_version", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=now),
    )
    op.create_index(
        "idx_ranking_artifact_run_layer", "ranking_evidence_artifacts", ["run_id", "evidence_layer"]
    )

    op.create_table(
        "ranking_raw_evidence_events",
        sa.Column("id", uuid, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("event_uuid", uuid, nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "run_id",
            uuid,
            sa.ForeignKey("ranking_evaluation_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sequence", sa.BigInteger, nullable=False),
        sa.Column("evidence_layer", sa.String(32), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False, server_default=now),
        sa.Column("source", sa.String(128), nullable=False),
        sa.Column("session_hash", sa.String(128)),
        sa.Column("assignment_hash", sa.String(128)),
        sa.Column("cluster_hash", sa.String(128)),
        sa.Column("subject_hash", sa.String(128)),
        sa.Column("candidate_revision", sa.String(128), nullable=False),
        sa.Column("candidate_hash", sa.String(64), nullable=False),
        sa.Column("payload_json", JSON, nullable=False),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("previous_event_hash", sa.String(64), nullable=False),
        sa.Column("event_hash", sa.String(64), nullable=False),
        sa.Column("collector_identity", sa.String(128), nullable=False),
        sa.Column("collector_version", sa.String(64), nullable=False),
        sa.Column("key_id", sa.String(128)),
        sa.Column("auth_receipt", sa.Text),
        sa.Column("ingest_idempotency_key", sa.String(300), nullable=False),
        sa.Column(
            "validation_status", sa.String(32), nullable=False, server_default=sa.text("'valid'")
        ),
        sa.Column("invalid_code", sa.String(128)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=now),
        sa.UniqueConstraint("run_id", "sequence", name="uq_ranking_event_run_sequence"),
        sa.UniqueConstraint("event_uuid", name="uq_ranking_event_event_uuid"),
        sa.UniqueConstraint("ingest_idempotency_key", name="uq_ranking_event_ingest_key"),
    )
    op.create_index(
        "idx_ranking_event_run_sequence", "ranking_raw_evidence_events", ["run_id", "sequence"]
    )

    op.create_table(
        "book_packaging_contracts",
        sa.Column("id", uuid, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "project_id", uuid, sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("contract_revision", sa.String(128), nullable=False),
        sa.Column("target_platform", sa.String(64), nullable=False),
        sa.Column("source_design_snapshot_hash", sa.String(64), nullable=False),
        sa.Column("primary_title", sa.Text, nullable=False),
        sa.Column("intro_variants_json", JSON, nullable=False),
        sa.Column("title_candidate_decision_ids", JSON, nullable=False),
        sa.Column("active_intro_variant_map", JSON, nullable=False),
        sa.Column("tag_effect_architecture_refs", JSON, nullable=False),
        sa.Column("realization_refs", JSON, nullable=False),
        sa.Column("canonical_payload_json", JSON, nullable=False),
        sa.Column("canonical_payload_hash", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default=sa.text("'draft'")),
        sa.Column(
            "supersedes_id", uuid, sa.ForeignKey("book_packaging_contracts.id", ondelete="RESTRICT")
        ),
        sa.Column("frozen_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=now),
    )
    op.create_index(
        "idx_packaging_contract_project_status",
        "book_packaging_contracts",
        ["project_id", "status"],
    )

    op.create_table(
        "story_rebuild_workspaces",
        sa.Column("id", uuid, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "project_id", uuid, sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("source_canonical_revision", sa.String(128), nullable=False),
        sa.Column("candidate_revision", sa.String(128), nullable=False),
        sa.Column("artifact_lineage_json", JSON, nullable=False),
        sa.Column(
            "validation_state", sa.String(32), nullable=False, server_default=sa.text("'draft'")
        ),
        sa.Column("cutover_receipt_json", JSON),
        sa.Column("rollback_receipt_json", JSON),
        sa.Column("cutover_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=now),
    )
    op.create_index(
        "idx_rebuild_workspace_project_state",
        "story_rebuild_workspaces",
        ["project_id", "validation_state"],
    )

    if op.get_bind().dialect.name == "postgresql":
        op.execute("""
        CREATE OR REPLACE FUNCTION ranking_evidence_guard() RETURNS trigger AS $$
        BEGIN
          -- A project delete may cascade through the foreign key.  The
          -- application still cannot delete a ledger row directly; allowing
          -- nested FK cleanup keeps test and project lifecycle cleanup safe.
          IF TG_OP = 'DELETE' AND NOT EXISTS (
            SELECT 1 FROM projects p
            JOIN ranking_evaluation_runs r ON r.project_id = p.id
            WHERE r.id = OLD.run_id
          ) THEN RETURN OLD; END IF;
          IF TG_OP IN ('UPDATE','DELETE','TRUNCATE') THEN
            RAISE EXCEPTION 'ranking evidence ledger is append-only';
          END IF;
          IF TG_TABLE_NAME = 'ranking_raw_evidence_events' AND EXISTS (
            SELECT 1 FROM ranking_evaluation_runs r
            WHERE r.id = NEW.run_id AND r.status = 'finalized'
          ) THEN RAISE EXCEPTION 'finalized ranking run cannot receive events'; END IF;
          RETURN NEW;
        END; $$ LANGUAGE plpgsql;
        """)
        op.execute(
            "CREATE TRIGGER ranking_raw_evidence_events_immutable "
            "BEFORE UPDATE OR DELETE ON ranking_raw_evidence_events "
            "FOR EACH ROW EXECUTE FUNCTION ranking_evidence_guard()"
        )
        op.execute(
            "CREATE TRIGGER ranking_raw_evidence_events_no_truncate "
            "BEFORE TRUNCATE ON ranking_raw_evidence_events "
            "FOR EACH STATEMENT EXECUTE FUNCTION ranking_evidence_guard()"
        )
        op.execute(
            "CREATE TRIGGER ranking_raw_evidence_events_no_finalized_insert "
            "BEFORE INSERT ON ranking_raw_evidence_events "
            "FOR EACH ROW EXECUTE FUNCTION ranking_evidence_guard()"
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            "DROP TRIGGER IF EXISTS ranking_raw_evidence_events_immutable "
            "ON ranking_raw_evidence_events"
        )
        op.execute(
            "DROP TRIGGER IF EXISTS ranking_raw_evidence_events_no_truncate "
            "ON ranking_raw_evidence_events"
        )
        op.execute(
            "DROP TRIGGER IF EXISTS ranking_raw_evidence_events_no_finalized_insert "
            "ON ranking_raw_evidence_events"
        )
        op.execute("DROP FUNCTION IF EXISTS ranking_evidence_guard()")
    for index, table in (
        ("idx_rebuild_workspace_project_state", "story_rebuild_workspaces"),
        ("idx_packaging_contract_project_status", "book_packaging_contracts"),
        ("idx_ranking_event_run_sequence", "ranking_raw_evidence_events"),
        ("idx_ranking_artifact_run_layer", "ranking_evidence_artifacts"),
        ("idx_ranking_run_project_status", "ranking_evaluation_runs"),
    ):
        op.drop_index(index, table_name=table)
    for table in (
        "story_rebuild_workspaces",
        "book_packaging_contracts",
        "ranking_raw_evidence_events",
        "ranking_evidence_artifacts",
        "ranking_evaluation_runs",
    ):
        op.drop_table(table)
