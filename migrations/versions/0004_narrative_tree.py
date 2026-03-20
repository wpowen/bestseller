from __future__ import annotations

from alembic import op


revision = "0004_narrative_tree"
down_revision = "0003_narrative_graph"
branch_labels = None
depends_on = None


NARRATIVE_TREE_SQL = (
    """
    CREATE TABLE IF NOT EXISTS narrative_tree_nodes (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        node_path VARCHAR(512) NOT NULL,
        parent_path VARCHAR(512),
        depth INTEGER NOT NULL,
        node_type VARCHAR(64) NOT NULL,
        title VARCHAR(255) NOT NULL,
        summary TEXT,
        body_md TEXT NOT NULL,
        source_type VARCHAR(64) NOT NULL,
        source_ref_id UUID,
        scope_level VARCHAR(32) NOT NULL DEFAULT 'project',
        scope_volume_number INTEGER,
        scope_chapter_number INTEGER,
        scope_scene_number INTEGER,
        lexical_document TEXT NOT NULL DEFAULT '',
        metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        CONSTRAINT uq_narrative_tree_node_path UNIQUE (project_id, node_path)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_narrative_tree_project_parent
    ON narrative_tree_nodes(project_id, parent_path)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_narrative_tree_project_type_depth
    ON narrative_tree_nodes(project_id, node_type, depth)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_narrative_tree_project_scope
    ON narrative_tree_nodes(project_id, scope_level, scope_volume_number, scope_chapter_number, scope_scene_number)
    """,
)


def upgrade() -> None:
    for statement in NARRATIVE_TREE_SQL:
        op.execute(statement.strip())


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS narrative_tree_nodes CASCADE")
