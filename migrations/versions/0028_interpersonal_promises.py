"""interpersonal_promises — track promises / oaths / debts between
characters so the system can surface them in chapter prompts and
auto-mark them inherited / lapsed when the promisor or promisee dies.

Distinct from ``setup_payoff`` (author→reader narrative debts) and
``chase_debt_ledger`` (override-contract narrative debts) — those are
about the narrative contract between book and reader. This table is
about the contract between people inside the story: a master's
deathbed wish, a vow to avenge a lover, an oath to protect a sister.
Without it, long novels lose track of these emotional anchors and the
writer LLM lets vendettas / promises evaporate silently.

Lifecycle:
    active     — both parties alive, promise still binding
    fulfilled  — promise has been kept (writer-driven)
    broken     — promise was breached (writer-driven, milestone event)
    inherited  — promisor died; another character has been designated
                 to carry the obligation (death_ripple service)
    lapsed     — promisor and/or promisee died with no inheritor —
                 the obligation joins the long tail of unfinished
                 emotional business
    cancelled  — explicitly retired by planner
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID


revision = "0028_interpersonal_promises"
down_revision = "0027_book_generation_schedules"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "interpersonal_promises",
        sa.Column(
            "id",
            PGUUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "project_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # Promisor / promisee are the two characters bound by the
        # promise. ``promisor`` makes the commitment; ``promisee`` is
        # the recipient. Both are stored as FK + label so the row stays
        # readable even after a hard delete or a legacy rename.
        sa.Column(
            "promisor_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("characters.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "promisee_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("characters.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("promisor_label", sa.Text(), nullable=False),
        sa.Column("promisee_label", sa.Text(), nullable=False),
        # Free-form one-line description of the commitment, plus an
        # optional structured kind (revenge / protection / message /
        # fealty / debt) so prompt rendering can differentiate.
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("kind", sa.String(32), nullable=True),
        # Anchor chapters — when the promise was made and when it is
        # due (if any). ``due_chapter`` is the latest chapter by which
        # the promise should be resolved; passed without resolution
        # turns the promise into emotional debt.
        sa.Column("made_chapter_number", sa.Integer(), nullable=True),
        sa.Column("due_chapter_number", sa.Integer(), nullable=True),
        # Status lifecycle (see module docstring for semantics).
        sa.Column(
            "status",
            sa.String(16),
            nullable=False,
            server_default=sa.text("'active'"),
        ),
        sa.Column("resolved_chapter_number", sa.Integer(), nullable=True),
        sa.Column("resolution_summary", sa.Text(), nullable=True),
        # When the promise transitions to ``inherited``, this points at
        # the new bearer of the obligation.
        sa.Column(
            "inherited_by_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("characters.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("inherited_by_label", sa.Text(), nullable=True),
        sa.Column(
            "metadata",
            JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.CheckConstraint(
            "status IN ('active','fulfilled','broken','inherited','lapsed','cancelled')",
            name="ck_interpersonal_promises_status",
        ),
        sa.CheckConstraint(
            "promisor_id IS NULL OR promisee_id IS NULL OR promisor_id <> promisee_id",
            name="ck_interpersonal_promises_distinct",
        ),
    )
    op.create_index(
        "idx_interpersonal_promises_project_status",
        "interpersonal_promises",
        ["project_id", "status"],
    )
    op.create_index(
        "idx_interpersonal_promises_promisor",
        "interpersonal_promises",
        ["project_id", "promisor_id"],
    )
    op.create_index(
        "idx_interpersonal_promises_promisee",
        "interpersonal_promises",
        ["project_id", "promisee_id"],
    )
    op.create_index(
        "idx_interpersonal_promises_due_chapter",
        "interpersonal_promises",
        ["project_id", "due_chapter_number"],
    )


def downgrade() -> None:
    op.drop_index(
        "idx_interpersonal_promises_due_chapter",
        table_name="interpersonal_promises",
    )
    op.drop_index(
        "idx_interpersonal_promises_promisee",
        table_name="interpersonal_promises",
    )
    op.drop_index(
        "idx_interpersonal_promises_promisor",
        table_name="interpersonal_promises",
    )
    op.drop_index(
        "idx_interpersonal_promises_project_status",
        table_name="interpersonal_promises",
    )
    op.drop_table("interpersonal_promises")
