# BestSeller — PostgreSQL 数据库架构设计

**数据库**: PostgreSQL 16+  
**核心扩展**: `pgcrypto`, `pgvector`, `pg_trgm`  
**ORM**: SQLAlchemy 2.x  
**状态**: Unified Draft

## 1. 设计原则

### 1.1 PostgreSQL 是主数据库

BestSeller 从第一天开始就以 PostgreSQL 作为唯一主数据库，不再以 SQLite 作为默认落地方案。

原因很直接：

1. 长篇小说生产不是单机记事本场景，而是版本化、可回溯、可并发、可恢复的工作流场景。
2. `CanonFact`、`TimelineEvent`、`RewriteTask`、`ReviewReport`、`retrieval chunk` 这些对象天然需要事务、一致性和复杂查询。
3. 后续接 Web UI、后台 worker、批量任务时，不应该再做一次“SQLite -> PostgreSQL”的系统迁移。

### 1.2 数据真值原则

PostgreSQL 存储以下权威数据：

- 结构化实体
- 规划中间产物
- 正文版本
- 检索 chunk
- 事实系统与时间线
- 质量报告
- 工作流状态
- LLM 调用审计

Markdown / DOCX / EPUB / PDF 都是导出物，不再是主存储。

### 1.3 主键策略

统一使用 `UUID` 主键：

```sql
id UUID PRIMARY KEY DEFAULT gen_random_uuid()
```

原因：

1. 统一应用层和数据库层的 ID 语义。
2. 便于异步任务、导入导出、未来多进程和 Web API。
3. 当前数据规模下，UUID 成本远低于跨层 ID 不一致带来的维护成本。

## 2. 必需扩展与基础函数

```sql
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
```

## 3. 完整逻辑分层

### 3.1 项目与规划层

- `projects`
- `style_guides`
- `planning_artifact_versions`

### 3.2 世界观与角色层

- `world_rules`
- `locations`
- `factions`
- `artifacts`
- `characters`
- `relationships`
- `character_state_snapshots`
- `character_knowledge`

### 3.3 大纲与正文层

- `volumes`
- `chapters`
- `scene_cards`
- `scene_draft_versions`
- `chapter_draft_versions`

### 3.4 一致性层

- `canon_facts`
- `timeline_events`
- `foreshadowing_seeds`
- `rewrite_tasks`
- `rewrite_impacts`

### 3.5 质量与检索层

- `review_reports`
- `quality_scores`
- `summaries`
- `retrieval_chunks`
- `workflow_runs`
- `workflow_step_runs`
- `llm_runs`
- `export_artifacts`

## 4. 完整 DDL

```sql
-- ============================================================
-- 1. 项目层
-- ============================================================

CREATE TABLE projects (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    slug                    TEXT NOT NULL UNIQUE,
    title                   TEXT NOT NULL,
    language                TEXT NOT NULL DEFAULT 'zh-CN',
    genre                   TEXT NOT NULL,
    sub_genre               TEXT,
    target_word_count       INTEGER NOT NULL CHECK (target_word_count > 0),
    target_chapters         INTEGER NOT NULL CHECK (target_chapters > 0),
    current_volume_number   INTEGER NOT NULL DEFAULT 1,
    current_chapter_number  INTEGER NOT NULL DEFAULT 0,
    audience                TEXT,
    status                  TEXT NOT NULL DEFAULT 'planning'
                                CHECK (status IN ('planning','writing','revising','paused','completed','archived')),
    metadata                JSONB NOT NULL DEFAULT '{}'::jsonb,
    lock_version            INTEGER NOT NULL DEFAULT 0,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TRIGGER trg_projects_updated_at
BEFORE UPDATE ON projects
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE style_guides (
    project_id              UUID PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
    pov_type                TEXT NOT NULL CHECK (pov_type IN (
                                'first_person','third_limited','third_omniscient','second_person'
                            )),
    tense                   TEXT NOT NULL CHECK (tense IN ('past','present','future')),
    tone_keywords           JSONB NOT NULL DEFAULT '[]'::jsonb,
    prose_style             TEXT,
    sentence_style          TEXT NOT NULL DEFAULT 'mixed'
                                CHECK (sentence_style IN ('short_punchy','long_flowing','mixed','minimalist')),
    info_density            TEXT NOT NULL DEFAULT 'medium'
                                CHECK (info_density IN ('light','medium','dense')),
    dialogue_ratio          NUMERIC(5,4) NOT NULL DEFAULT 0.35 CHECK (dialogue_ratio BETWEEN 0 AND 1),
    taboo_words             JSONB NOT NULL DEFAULT '[]'::jsonb,
    taboo_topics            JSONB NOT NULL DEFAULT '[]'::jsonb,
    reference_works         JSONB NOT NULL DEFAULT '[]'::jsonb,
    custom_rules            JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TRIGGER trg_style_guides_updated_at
BEFORE UPDATE ON style_guides
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE planning_artifact_versions (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id              UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    artifact_type           TEXT NOT NULL CHECK (artifact_type IN (
                                'premise','book_spec','world_spec','cast_spec',
                                'volume_plan','chapter_outline_batch'
                            )),
    scope_ref_id            UUID,
    version_no              INTEGER NOT NULL CHECK (version_no > 0),
    status                  TEXT NOT NULL DEFAULT 'approved'
                                CHECK (status IN ('draft','approved','superseded','rejected')),
    schema_version          TEXT NOT NULL,
    content                 JSONB NOT NULL,
    source_run_id           UUID,
    notes                   TEXT,
    created_by              TEXT NOT NULL DEFAULT 'system',
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (project_id, artifact_type, scope_ref_id, version_no)
);

CREATE INDEX idx_planning_artifacts_project_type
    ON planning_artifact_versions(project_id, artifact_type, created_at DESC);

-- ============================================================
-- 2. 世界观与角色层
-- ============================================================

CREATE TABLE world_rules (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id              UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    rule_code               TEXT NOT NULL,
    category                TEXT NOT NULL CHECK (category IN (
                                'physics','magic','social','economic',
                                'political','religious','technological','other'
                            )),
    name                    TEXT NOT NULL,
    description             TEXT NOT NULL,
    story_consequence       TEXT NOT NULL,
    exploitation_potential  TEXT,
    hard_constraint         BOOLEAN NOT NULL DEFAULT TRUE,
    is_active               BOOLEAN NOT NULL DEFAULT TRUE,
    introduced_chapter_no   INTEGER,
    metadata                JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (project_id, rule_code)
);

CREATE TRIGGER trg_world_rules_updated_at
BEFORE UPDATE ON world_rules
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE locations (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id              UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    parent_location_id      UUID REFERENCES locations(id) ON DELETE SET NULL,
    name                    TEXT NOT NULL,
    location_type           TEXT NOT NULL,
    description             TEXT NOT NULL,
    atmosphere              TEXT,
    sensory_details         JSONB NOT NULL DEFAULT '{}'::jsonb,
    introduced_chapter_no   INTEGER,
    metadata                JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (project_id, name)
);

CREATE INDEX idx_locations_parent ON locations(parent_location_id);
CREATE TRIGGER trg_locations_updated_at
BEFORE UPDATE ON locations
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE factions (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id              UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    headquarters_id         UUID REFERENCES locations(id) ON DELETE SET NULL,
    name                    TEXT NOT NULL,
    faction_type            TEXT NOT NULL,
    description             TEXT NOT NULL,
    goal                    TEXT,
    method                  TEXT,
    internal_conflict       TEXT,
    public_reputation       TEXT,
    secret_agenda           TEXT,
    metadata                JSONB NOT NULL DEFAULT '{}'::jsonb,
    is_active               BOOLEAN NOT NULL DEFAULT TRUE,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (project_id, name)
);

CREATE TRIGGER trg_factions_updated_at
BEFORE UPDATE ON factions
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE artifacts (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id              UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    current_holder_id       UUID,
    current_location_id     UUID REFERENCES locations(id) ON DELETE SET NULL,
    name                    TEXT NOT NULL,
    artifact_type           TEXT NOT NULL,
    description             TEXT NOT NULL,
    properties              JSONB NOT NULL DEFAULT '{}'::jsonb,
    introduced_chapter_no   INTEGER,
    is_significant          BOOLEAN NOT NULL DEFAULT FALSE,
    metadata                JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (project_id, name)
);

CREATE TABLE characters (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id              UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    primary_faction_id      UUID REFERENCES factions(id) ON DELETE SET NULL,
    current_location_id     UUID REFERENCES locations(id) ON DELETE SET NULL,
    name                    TEXT NOT NULL,
    aliases                 JSONB NOT NULL DEFAULT '[]'::jsonb,
    role                    TEXT NOT NULL CHECK (role IN (
                                'protagonist','antagonist','deuteragonist','supporting','minor','narrator'
                            )),
    background              TEXT,
    goal                    TEXT NOT NULL,
    motivation              TEXT,
    fear                    TEXT NOT NULL,
    flaw                    TEXT,
    secret                  TEXT,
    speech_pattern          TEXT,
    age                     INTEGER,
    gender                  TEXT,
    power_tier              TEXT,
    is_pov_character        BOOLEAN NOT NULL DEFAULT FALSE,
    metadata                JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (project_id, name)
);

CREATE INDEX idx_characters_project_role ON characters(project_id, role);
CREATE INDEX idx_characters_name_trgm ON characters USING gin (name gin_trgm_ops);
CREATE TRIGGER trg_characters_updated_at
BEFORE UPDATE ON characters
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

ALTER TABLE artifacts
    ADD CONSTRAINT fk_artifacts_holder
    FOREIGN KEY (current_holder_id) REFERENCES characters(id) ON DELETE SET NULL;

CREATE TABLE relationships (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id              UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    character_a_id          UUID NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
    character_b_id          UUID NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
    relationship_type       TEXT NOT NULL,
    strength                NUMERIC(5,4) NOT NULL DEFAULT 0 CHECK (strength BETWEEN -1 AND 1),
    public_face             TEXT,
    private_reality         TEXT,
    tension_summary         TEXT,
    established_chapter_no  INTEGER,
    last_changed_chapter_no INTEGER,
    metadata                JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CHECK (character_a_id <> character_b_id),
    UNIQUE (project_id, character_a_id, character_b_id)
);

CREATE TRIGGER trg_relationships_updated_at
BEFORE UPDATE ON relationships
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE character_state_snapshots (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id              UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    character_id            UUID NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
    chapter_id              UUID,
    scene_card_id           UUID,
    chapter_number          INTEGER NOT NULL,
    scene_number            INTEGER,
    arc_state               TEXT,
    emotional_state         TEXT,
    physical_state          TEXT,
    power_tier              TEXT,
    trust_map               JSONB NOT NULL DEFAULT '{}'::jsonb,
    beliefs                 JSONB NOT NULL DEFAULT '[]'::jsonb,
    notes                   TEXT,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_character_state_snapshots_lookup
    ON character_state_snapshots(project_id, character_id, chapter_number DESC, scene_number DESC);

CREATE TABLE character_knowledge (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id              UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    character_id            UUID NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
    canon_fact_id           UUID NOT NULL,
    awareness_level         TEXT NOT NULL CHECK (awareness_level IN (
                                'unaware','rumored','partial','full','mistaken'
                            )),
    mistaken_belief         TEXT,
    learned_chapter_no      INTEGER,
    learned_scene_id        UUID,
    learned_via             TEXT,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (character_id, canon_fact_id)
);

CREATE INDEX idx_character_knowledge_character ON character_knowledge(character_id, awareness_level);
CREATE TRIGGER trg_character_knowledge_updated_at
BEFORE UPDATE ON character_knowledge
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ============================================================
-- 3. 大纲与正文层
-- ============================================================

CREATE TABLE volumes (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id              UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    volume_number           INTEGER NOT NULL,
    title                   TEXT NOT NULL,
    theme                   TEXT,
    goal                    TEXT,
    obstacle                TEXT,
    climax_summary          TEXT,
    resolution_summary      TEXT,
    target_word_count       INTEGER,
    target_chapter_count    INTEGER,
    status                  TEXT NOT NULL DEFAULT 'planned'
                                CHECK (status IN ('planned','writing','review','complete')),
    metadata                JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (project_id, volume_number)
);

CREATE TRIGGER trg_volumes_updated_at
BEFORE UPDATE ON volumes
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE chapters (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id              UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    volume_id               UUID REFERENCES volumes(id) ON DELETE SET NULL,
    pov_character_id        UUID REFERENCES characters(id) ON DELETE SET NULL,
    primary_location_id     UUID REFERENCES locations(id) ON DELETE SET NULL,
    chapter_number          INTEGER NOT NULL,
    title                   TEXT,
    chapter_goal            TEXT NOT NULL,
    opening_situation       TEXT,
    main_conflict           TEXT,
    hook_type               TEXT,
    hook_description        TEXT,
    information_revealed    JSONB NOT NULL DEFAULT '[]'::jsonb,
    information_withheld    JSONB NOT NULL DEFAULT '[]'::jsonb,
    foreshadowing_actions   JSONB NOT NULL DEFAULT '{}'::jsonb,
    chapter_emotion_arc     TEXT,
    target_word_count       INTEGER NOT NULL DEFAULT 3000,
    current_word_count      INTEGER NOT NULL DEFAULT 0,
    revision_count          INTEGER NOT NULL DEFAULT 0,
    status                  TEXT NOT NULL DEFAULT 'planned'
                                CHECK (status IN (
                                    'planned','outlining','drafting','review','revision','complete'
                                )),
    metadata                JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (project_id, chapter_number)
);

CREATE INDEX idx_chapters_project_status ON chapters(project_id, status);
CREATE TRIGGER trg_chapters_updated_at
BEFORE UPDATE ON chapters
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE scene_cards (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id              UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    chapter_id              UUID NOT NULL REFERENCES chapters(id) ON DELETE CASCADE,
    pov_character_id        UUID REFERENCES characters(id) ON DELETE SET NULL,
    location_id             UUID REFERENCES locations(id) ON DELETE SET NULL,
    scene_number            INTEGER NOT NULL,
    title                   TEXT,
    scene_type              TEXT NOT NULL,
    time_label              TEXT,
    participants            JSONB NOT NULL DEFAULT '[]'::jsonb,
    purpose                 JSONB NOT NULL DEFAULT '{}'::jsonb,
    entry_state             JSONB NOT NULL DEFAULT '{}'::jsonb,
    exit_state              JSONB NOT NULL DEFAULT '{}'::jsonb,
    key_dialogue_beats      JSONB NOT NULL DEFAULT '[]'::jsonb,
    sensory_anchors         JSONB NOT NULL DEFAULT '{}'::jsonb,
    forbidden_actions       JSONB NOT NULL DEFAULT '[]'::jsonb,
    hook_requirement        TEXT,
    target_word_count       INTEGER NOT NULL DEFAULT 1000,
    status                  TEXT NOT NULL DEFAULT 'planned'
                                CHECK (status IN ('planned','drafted','reviewed','approved','needs_rewrite')),
    metadata                JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (chapter_id, scene_number)
);

CREATE INDEX idx_scene_cards_chapter_status ON scene_cards(chapter_id, status);
CREATE TRIGGER trg_scene_cards_updated_at
BEFORE UPDATE ON scene_cards
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE scene_draft_versions (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id              UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    scene_card_id           UUID NOT NULL REFERENCES scene_cards(id) ON DELETE CASCADE,
    version_no              INTEGER NOT NULL CHECK (version_no > 0),
    content_md              TEXT NOT NULL,
    word_count              INTEGER NOT NULL DEFAULT 0,
    is_current              BOOLEAN NOT NULL DEFAULT TRUE,
    model_name              TEXT,
    prompt_template         TEXT,
    prompt_version          TEXT,
    prompt_hash             TEXT,
    generation_params       JSONB NOT NULL DEFAULT '{}'::jsonb,
    llm_run_id              UUID,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (scene_card_id, version_no)
);

CREATE UNIQUE INDEX uq_scene_draft_current
    ON scene_draft_versions(scene_card_id)
    WHERE is_current;

CREATE TABLE chapter_draft_versions (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id              UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    chapter_id              UUID NOT NULL REFERENCES chapters(id) ON DELETE CASCADE,
    version_no              INTEGER NOT NULL CHECK (version_no > 0),
    content_md              TEXT NOT NULL,
    word_count              INTEGER NOT NULL DEFAULT 0,
    assembled_from_scene_draft_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    is_current              BOOLEAN NOT NULL DEFAULT TRUE,
    llm_run_id              UUID,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (chapter_id, version_no)
);

CREATE UNIQUE INDEX uq_chapter_draft_current
    ON chapter_draft_versions(chapter_id)
    WHERE is_current;

-- ============================================================
-- 4. Canon 与时间线层
-- ============================================================

CREATE TABLE canon_facts (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id              UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    subject_type            TEXT NOT NULL CHECK (subject_type IN (
                                'project','character','location','faction','artifact',
                                'world_rule','chapter','scene','event','relationship','concept'
                            )),
    subject_id              UUID,
    subject_label           TEXT NOT NULL,
    predicate               TEXT NOT NULL,
    fact_type               TEXT NOT NULL CHECK (fact_type IN (
                                'attribute','relationship','event','state','rule'
                            )),
    value_json              JSONB NOT NULL,
    confidence              NUMERIC(5,4) NOT NULL DEFAULT 1 CHECK (confidence BETWEEN 0 AND 1),
    source_type             TEXT NOT NULL DEFAULT 'extracted'
                                CHECK (source_type IN ('manual','extracted','inferred','imported')),
    source_scene_id         UUID REFERENCES scene_cards(id) ON DELETE SET NULL,
    source_chapter_id       UUID REFERENCES chapters(id) ON DELETE SET NULL,
    valid_from_chapter_no   INTEGER NOT NULL DEFAULT 1,
    valid_to_chapter_no     INTEGER,
    supersedes_fact_id      UUID REFERENCES canon_facts(id) ON DELETE SET NULL,
    is_current              BOOLEAN NOT NULL DEFAULT TRUE,
    tags                    JSONB NOT NULL DEFAULT '[]'::jsonb,
    notes                   TEXT,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_canon_facts_subject_predicate
    ON canon_facts(project_id, subject_type, subject_id, predicate);

CREATE UNIQUE INDEX uq_canon_current_fact
    ON canon_facts(project_id, subject_type, subject_id, predicate)
    WHERE is_current;

CREATE TRIGGER trg_canon_facts_updated_at
BEFORE UPDATE ON canon_facts
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

ALTER TABLE character_knowledge
    ADD CONSTRAINT fk_character_knowledge_fact
    FOREIGN KEY (canon_fact_id) REFERENCES canon_facts(id) ON DELETE CASCADE;

CREATE TABLE timeline_events (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id              UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    chapter_id              UUID REFERENCES chapters(id) ON DELETE SET NULL,
    scene_card_id           UUID REFERENCES scene_cards(id) ON DELETE SET NULL,
    location_id             UUID REFERENCES locations(id) ON DELETE SET NULL,
    caused_by_event_id      UUID REFERENCES timeline_events(id) ON DELETE SET NULL,
    event_name              TEXT NOT NULL,
    event_type              TEXT NOT NULL CHECK (event_type IN (
                                'background','offscreen','onscreen','flashback','future'
                            )),
    story_time_label        TEXT NOT NULL,
    story_order             NUMERIC(14,4) NOT NULL,
    participant_ids         JSONB NOT NULL DEFAULT '[]'::jsonb,
    consequences            JSONB NOT NULL DEFAULT '[]'::jsonb,
    duration_hint           TEXT,
    is_revealed             BOOLEAN NOT NULL DEFAULT TRUE,
    metadata                JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_timeline_project_story_order
    ON timeline_events(project_id, story_order);

CREATE TRIGGER trg_timeline_events_updated_at
BEFORE UPDATE ON timeline_events
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE foreshadowing_seeds (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id              UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    thread_key              TEXT,
    plant_scene_id          UUID NOT NULL REFERENCES scene_cards(id) ON DELETE CASCADE,
    plant_chapter_no        INTEGER NOT NULL,
    plant_text_hint         TEXT NOT NULL,
    plant_subtlety          INTEGER NOT NULL DEFAULT 5 CHECK (plant_subtlety BETWEEN 1 AND 10),
    intended_payoff_chapter_no INTEGER,
    payoff_scene_id         UUID REFERENCES scene_cards(id) ON DELETE SET NULL,
    payoff_chapter_no       INTEGER,
    payoff_description      TEXT,
    status                  TEXT NOT NULL DEFAULT 'planted'
                                CHECK (status IN ('planned','planted','developing','paid_off','abandoned')),
    metadata                JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_foreshadowing_active
    ON foreshadowing_seeds(project_id, plant_chapter_no)
    WHERE status IN ('planned','planted','developing');

CREATE TRIGGER trg_foreshadowing_updated_at
BEFORE UPDATE ON foreshadowing_seeds
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ============================================================
-- 5. 质量、重写、摘要层
-- ============================================================

CREATE TABLE review_reports (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id              UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    target_type             TEXT NOT NULL CHECK (target_type IN ('scene','chapter','volume','project')),
    target_id               UUID NOT NULL,
    reviewer_type           TEXT NOT NULL CHECK (reviewer_type IN (
                                'continuity','quality','style','foreshadowing',
                                'character_consistency','timeline','human'
                            )),
    verdict                 TEXT NOT NULL CHECK (verdict IN ('pass','warn','fail')),
    severity_max            TEXT CHECK (severity_max IN ('low','medium','high','critical')),
    structured_output       JSONB NOT NULL DEFAULT '{}'::jsonb,
    llm_run_id              UUID,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_review_reports_target
    ON review_reports(target_type, target_id, created_at DESC);

CREATE TABLE quality_scores (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id              UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    target_type             TEXT NOT NULL CHECK (target_type IN ('scene','chapter')),
    target_id               UUID NOT NULL,
    review_report_id        UUID REFERENCES review_reports(id) ON DELETE SET NULL,
    is_current              BOOLEAN NOT NULL DEFAULT TRUE,
    score_overall           NUMERIC(4,2) NOT NULL CHECK (score_overall BETWEEN 0 AND 10),
    score_goal              NUMERIC(4,2),
    score_conflict          NUMERIC(4,2),
    score_emotion           NUMERIC(4,2),
    score_dialogue          NUMERIC(4,2),
    score_style             NUMERIC(4,2),
    score_hook              NUMERIC(4,2),
    evidence_summary        JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX uq_quality_scores_current
    ON quality_scores(target_type, target_id)
    WHERE is_current;

CREATE TABLE rewrite_tasks (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id              UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    parent_task_id          UUID REFERENCES rewrite_tasks(id) ON DELETE SET NULL,
    trigger_type            TEXT NOT NULL CHECK (trigger_type IN (
                                'canon_change','quality_fail','continuity_error',
                                'style_drift','manual','cascade'
                            )),
    trigger_source_id       UUID,
    rewrite_strategy        TEXT NOT NULL CHECK (rewrite_strategy IN (
                                'full_rewrite','targeted_edit','continuity_patch',
                                'style_polish','fact_correction'
                            )),
    priority                INTEGER NOT NULL DEFAULT 5 CHECK (priority BETWEEN 1 AND 10),
    status                  TEXT NOT NULL DEFAULT 'pending'
                                CHECK (status IN ('pending','queued','in_progress','done','skipped','failed')),
    instructions            TEXT NOT NULL,
    context_required        JSONB NOT NULL DEFAULT '[]'::jsonb,
    started_at              TIMESTAMPTZ,
    completed_at            TIMESTAMPTZ,
    attempts                INTEGER NOT NULL DEFAULT 0,
    error_log               TEXT,
    metadata                JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_rewrite_tasks_pending
    ON rewrite_tasks(project_id, priority DESC, created_at)
    WHERE status IN ('pending','queued');

CREATE TRIGGER trg_rewrite_tasks_updated_at
BEFORE UPDATE ON rewrite_tasks
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE rewrite_impacts (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    rewrite_task_id         UUID NOT NULL REFERENCES rewrite_tasks(id) ON DELETE CASCADE,
    impacted_type           TEXT NOT NULL CHECK (impacted_type IN ('fact','scene','chapter','summary')),
    impacted_id             UUID NOT NULL,
    impact_level            TEXT NOT NULL CHECK (impact_level IN ('must','should','may')),
    impact_score            NUMERIC(5,4) NOT NULL CHECK (impact_score BETWEEN 0 AND 1),
    reason                  TEXT NOT NULL
);

CREATE INDEX idx_rewrite_impacts_task
    ON rewrite_impacts(rewrite_task_id, impact_level, impact_score DESC);

CREATE TABLE summaries (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id              UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    summary_type            TEXT NOT NULL CHECK (summary_type IN (
                                'scene','chapter','volume','project','character_arc','mainline'
                            )),
    target_id               UUID NOT NULL,
    is_current              BOOLEAN NOT NULL DEFAULT TRUE,
    brief                   TEXT NOT NULL,
    standard                TEXT NOT NULL,
    detailed                TEXT,
    key_events              JSONB NOT NULL DEFAULT '[]'::jsonb,
    state_changes           JSONB NOT NULL DEFAULT '{}'::jsonb,
    llm_run_id              UUID,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX uq_summaries_current
    ON summaries(summary_type, target_id)
    WHERE is_current;

-- ============================================================
-- 6. 检索、工作流、审计层
-- ============================================================

CREATE TABLE retrieval_chunks (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id              UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    source_type             TEXT NOT NULL CHECK (source_type IN (
                                'scene_draft','chapter_draft','summary','canon_fact','world_rule'
                            )),
    source_id               UUID NOT NULL,
    chunk_index             INTEGER NOT NULL DEFAULT 0,
    chunk_text              TEXT NOT NULL,
    embedding_model         TEXT NOT NULL,
    embedding_dim           INTEGER NOT NULL CHECK (embedding_dim > 0),
    embedding               VECTOR(1024) NOT NULL,
    lexical_document        TEXT,
    metadata                JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (source_type, source_id, chunk_index)
);

CREATE INDEX idx_retrieval_chunks_project_source
    ON retrieval_chunks(project_id, source_type, source_id);

CREATE INDEX idx_retrieval_chunks_embedding
    ON retrieval_chunks USING hnsw (embedding vector_cosine_ops);

CREATE INDEX idx_retrieval_chunks_text_trgm
    ON retrieval_chunks USING gin (chunk_text gin_trgm_ops);

CREATE TABLE workflow_runs (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id              UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    workflow_type           TEXT NOT NULL CHECK (workflow_type IN (
                                'novel_pipeline','chapter_pipeline','rewrite_pipeline','export_pipeline'
                            )),
    status                  TEXT NOT NULL CHECK (status IN (
                                'pending','queued','running','waiting_human','failed','completed','cancelled'
                            )),
    scope_type              TEXT CHECK (scope_type IN ('project','volume','chapter','scene','rewrite_task')),
    scope_id                UUID,
    requested_by            TEXT NOT NULL DEFAULT 'system',
    current_step            TEXT,
    error_message           TEXT,
    metadata                JSONB NOT NULL DEFAULT '{}'::jsonb,
    started_at              TIMESTAMPTZ,
    finished_at             TIMESTAMPTZ,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_workflow_runs_pending
    ON workflow_runs(status, created_at)
    WHERE status IN ('pending','queued');

CREATE TRIGGER trg_workflow_runs_updated_at
BEFORE UPDATE ON workflow_runs
FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE TABLE workflow_step_runs (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workflow_run_id         UUID NOT NULL REFERENCES workflow_runs(id) ON DELETE CASCADE,
    step_name               TEXT NOT NULL,
    step_order              INTEGER NOT NULL,
    status                  TEXT NOT NULL CHECK (status IN (
                                'pending','running','failed','completed','skipped','waiting_human'
                            )),
    input_ref               JSONB NOT NULL DEFAULT '{}'::jsonb,
    output_ref              JSONB NOT NULL DEFAULT '{}'::jsonb,
    error_message           TEXT,
    started_at              TIMESTAMPTZ,
    finished_at             TIMESTAMPTZ,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (workflow_run_id, step_order)
);

CREATE TABLE llm_runs (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id              UUID REFERENCES projects(id) ON DELETE SET NULL,
    workflow_run_id         UUID REFERENCES workflow_runs(id) ON DELETE SET NULL,
    step_run_id             UUID REFERENCES workflow_step_runs(id) ON DELETE SET NULL,
    logical_role            TEXT NOT NULL CHECK (logical_role IN (
                                'planner','writer','critic','editor','summarizer','canon_keeper'
                            )),
    provider                TEXT NOT NULL,
    model_name              TEXT NOT NULL,
    prompt_template         TEXT,
    prompt_version          TEXT,
    prompt_hash             TEXT,
    input_tokens            INTEGER,
    output_tokens           INTEGER,
    latency_ms              INTEGER,
    finish_reason           TEXT,
    request_payload_ref     TEXT,
    response_payload_ref    TEXT,
    metadata                JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_llm_runs_project_created
    ON llm_runs(project_id, created_at DESC);

CREATE TABLE export_artifacts (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id              UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    export_type             TEXT NOT NULL CHECK (export_type IN ('markdown','docx','epub','pdf','json_report')),
    source_scope            TEXT NOT NULL CHECK (source_scope IN ('project','volume','chapter')),
    source_id               UUID NOT NULL,
    storage_uri             TEXT NOT NULL,
    checksum                TEXT,
    version_label           TEXT,
    created_by_run_id       UUID REFERENCES workflow_runs(id) ON DELETE SET NULL,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

## 5. 关键查询设计

### 5.1 场景上下文装配

```sql
WITH scene_ctx AS (
    SELECT sc.id,
           sc.project_id,
           sc.chapter_id,
           c.chapter_number,
           sc.scene_number,
           sc.pov_character_id,
           sc.location_id,
           sc.participants
    FROM scene_cards sc
    JOIN chapters c ON c.id = sc.chapter_id
    WHERE sc.id = :scene_card_id
)
SELECT
    sc.project_id,
    c.chapter_goal,
    c.main_conflict,
    c.hook_type,
    c.hook_description,
    sg.pov_type,
    sg.tone_keywords,
    sg.custom_rules
FROM scene_ctx sc
JOIN chapters c ON c.id = sc.chapter_id
JOIN style_guides sg ON sg.project_id = sc.project_id;
```

### 5.2 当前有效 Canon 事实

```sql
SELECT cf.*
FROM canon_facts cf
JOIN chapters c ON c.project_id = cf.project_id
WHERE c.id = :chapter_id
  AND cf.is_current = TRUE
  AND cf.valid_from_chapter_no <= c.chapter_number
  AND (cf.valid_to_chapter_no IS NULL OR cf.valid_to_chapter_no >= c.chapter_number)
ORDER BY cf.subject_label, cf.predicate;
```

### 5.3 变更影响面分析

```sql
WITH changed_fact AS (
    SELECT *
    FROM canon_facts
    WHERE id = :canon_fact_id
)
SELECT
    sc.id AS impacted_scene_id,
    c.chapter_number,
    sc.scene_number,
    CASE
        WHEN sc.id = cf.source_scene_id THEN 1.0
        WHEN sc.participants ? (SELECT subject_id::text FROM changed_fact) THEN 0.7
        ELSE 0.4
    END AS impact_score
FROM changed_fact cf
JOIN chapters c
  ON c.project_id = cf.project_id
 AND c.chapter_number >= cf.valid_from_chapter_no
JOIN scene_cards sc
  ON sc.chapter_id = c.id;
```

### 5.4 混合检索

```sql
SELECT
    rc.source_type,
    rc.source_id,
    rc.chunk_text,
    0.70 * (1 - (rc.embedding <=> :query_embedding)) +
    0.30 * similarity(rc.chunk_text, :query_text) AS hybrid_score
FROM retrieval_chunks rc
WHERE rc.project_id = :project_id
ORDER BY hybrid_score DESC
LIMIT 20;
```

### 5.5 Worker 抢占任务

```sql
WITH next_job AS (
    SELECT id
    FROM workflow_runs
    WHERE status = 'queued'
    ORDER BY created_at
    FOR UPDATE SKIP LOCKED
    LIMIT 1
)
UPDATE workflow_runs wr
SET status = 'running',
    started_at = NOW(),
    updated_at = NOW()
FROM next_job
WHERE wr.id = next_job.id
RETURNING wr.*;
```

## 6. 关键约束

### 6.1 只有一个 current draft

- `scene_draft_versions`
- `chapter_draft_versions`
- `quality_scores`
- `summaries`

都必须通过部分唯一索引保证同一对象只有一个 current 版本。

### 6.2 Canon 当前值唯一

同一个 `(project_id, subject_type, subject_id, predicate)` 在 `is_current = true` 条件下只能有一条当前有效事实。

### 6.3 顺序不靠主键

顺序必须由业务字段和唯一约束保证：

- `UNIQUE (project_id, chapter_number)`
- `UNIQUE (chapter_id, scene_number)`
- `UNIQUE (scene_card_id, version_no)`

## 7. 对旧方案冲突的处理结果

| 冲突点 | 原状态 | 统一后 |
| --- | --- | --- |
| 主数据库 | SQLite / PostgreSQL 摇摆 | PostgreSQL 16+ |
| 向量库 | ChromaDB | pgvector |
| 正文真值 | 文件与数据库冲突 | PostgreSQL 真值，文件为导出物 |
| 主键 | UUID vs 自增整数 | UUID |
| 规划产物 | 只在文档里 | `planning_artifact_versions` 表 |
| 中断恢复 | 文档里说支持 | `workflow_runs` / `workflow_step_runs` 明确落表 |

## 8. 迁移策略

### 8.1 当前项目阶段

项目当前没有正式业务表和历史数据，因此不需要做 SQLite -> PostgreSQL 数据迁移。

### 8.2 第一版迁移建议

1. 初始化 PostgreSQL schema。
2. 初始化扩展：`pgcrypto`, `pgvector`, `pg_trgm`。
3. 先落核心表：
   - `projects`
   - `style_guides`
   - `planning_artifact_versions`
   - `volumes`
   - `chapters`
   - `scene_cards`
   - `scene_draft_versions`
   - `canon_facts`
   - `workflow_runs`
4. 第二批再补：
   - `timeline_events`
   - `character_state_snapshots`
   - `review_reports`
   - `rewrite_tasks`
   - `retrieval_chunks`

## 9. 性能建议

### 9.1 先做对，再做大

当前最重要的是：

- 正确的事务边界
- 合理索引
- 版本表设计
- 检索过滤正确

而不是过早分库分表。

### 9.2 需要优先观察的指标

- `scene_draft_versions` 写入吞吐
- `canon_facts` 冲突查询延迟
- `retrieval_chunks` HNSW 索引大小
- `workflow_runs` 排队时延
- 单章生成全过程平均耗时

---

这份 schema 的重点不是“数据库表很多”，而是**让长篇小说生产中的规划、正文、事实、一致性、重写和工作流第一次进入同一套可事务化的数据模型**。这才是后续真正能写出长篇、改得动长篇、审得住长篇的前提。

