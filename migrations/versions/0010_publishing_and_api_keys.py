from __future__ import annotations

from alembic import op


revision = "0010_publishing_and_api_keys"
down_revision = "0010_narr_depth_char_voice"
branch_labels = None
depends_on = None


UPGRADE_SQL = (
    # API keys for service-to-service authentication
    """
    CREATE TABLE IF NOT EXISTS api_keys (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        name VARCHAR(100) NOT NULL,
        key_hash VARCHAR(128) NOT NULL UNIQUE,
        is_active BOOLEAN NOT NULL DEFAULT true,
        last_used_at TIMESTAMPTZ,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_api_keys_active
    ON api_keys(is_active)
    WHERE is_active = true
    """,

    # Publishing platforms (per-project configuration)
    """
    CREATE TABLE IF NOT EXISTS publishing_platforms (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        name VARCHAR(100) NOT NULL,
        platform_type VARCHAR(32) NOT NULL,
        api_base_url VARCHAR(500),
        credentials_encrypted TEXT,
        rate_limit_rpm INTEGER NOT NULL DEFAULT 10,
        metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        CONSTRAINT uq_publishing_platforms_project_type UNIQUE (project_id, platform_type)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_publishing_platforms_project
    ON publishing_platforms(project_id)
    """,

    # Publishing schedules
    """
    CREATE TABLE IF NOT EXISTS publishing_schedules (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        platform_id UUID NOT NULL REFERENCES publishing_platforms(id) ON DELETE CASCADE,
        cron_expression VARCHAR(100) NOT NULL,
        timezone VARCHAR(64) NOT NULL DEFAULT 'Asia/Shanghai',
        start_chapter INTEGER NOT NULL DEFAULT 1,
        current_chapter INTEGER NOT NULL DEFAULT 0,
        chapters_per_release INTEGER NOT NULL DEFAULT 1,
        status VARCHAR(16) NOT NULL DEFAULT 'active',
        metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        CONSTRAINT ck_publishing_schedules_status CHECK (status IN ('active','paused','completed'))
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_publishing_schedules_project
    ON publishing_schedules(project_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_publishing_schedules_status
    ON publishing_schedules(status)
    """,

    # Publishing history
    """
    CREATE TABLE IF NOT EXISTS publishing_history (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        schedule_id UUID NOT NULL REFERENCES publishing_schedules(id) ON DELETE CASCADE,
        project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
        platform_id UUID NOT NULL REFERENCES publishing_platforms(id) ON DELETE CASCADE,
        chapter_number INTEGER NOT NULL,
        published_at TIMESTAMPTZ,
        status VARCHAR(16) NOT NULL DEFAULT 'pending',
        platform_chapter_id VARCHAR(200),
        platform_response JSONB NOT NULL DEFAULT '{}'::jsonb,
        error_message TEXT,
        retry_count INTEGER NOT NULL DEFAULT 0,
        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
        CONSTRAINT ck_publishing_history_status CHECK (status IN ('pending','success','failed','retrying'))
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_publishing_history_schedule
    ON publishing_history(schedule_id, chapter_number)
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_publishing_history_project
    ON publishing_history(project_id, published_at)
    """,
)

DOWNGRADE_SQL = (
    "DROP INDEX IF EXISTS idx_publishing_history_project",
    "DROP INDEX IF EXISTS idx_publishing_history_schedule",
    "DROP TABLE IF EXISTS publishing_history",
    "DROP INDEX IF EXISTS idx_publishing_schedules_status",
    "DROP INDEX IF EXISTS idx_publishing_schedules_project",
    "DROP TABLE IF EXISTS publishing_schedules",
    "DROP INDEX IF EXISTS idx_publishing_platforms_project",
    "DROP TABLE IF EXISTS publishing_platforms",
    "DROP INDEX IF EXISTS idx_api_keys_active",
    "DROP TABLE IF EXISTS api_keys",
)


def upgrade() -> None:
    for statement in UPGRADE_SQL:
        op.execute(statement.strip())


def downgrade() -> None:
    for statement in DOWNGRADE_SQL:
        op.execute(statement.strip())
