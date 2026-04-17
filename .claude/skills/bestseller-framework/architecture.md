# Architecture — Mode A 系统地图

> Mode A 协助开发时的速查。完整版见 [docs/ai-context.md](../../../docs/ai-context.md) § 2–12。

## 1. 分层总览

```
HTTP Client / Web UI (port 8787)
    │
FastAPI REST API (port 8000)
    │
ARQ Task Queue ←── Redis ──→ APScheduler (publishing cron)
    │
ARQ Worker processes (×N, max_jobs=4, timeout=86400s)
    │
Pipeline Orchestration Layer  (services/pipelines.py)
    │
Service Layer: planner · drafts · reviews · knowledge ·
               context · retrieval · continuity · narrative · publishing
    │
PostgreSQL 16 + pgvector   +   Redis (queue / cache / pubsub)
```

## 2. 关键服务

| 服务 | 文件 | 职责 |
|------|------|------|
| Pipeline orchestrator | `services/pipelines.py` | scene/chapter/project 端到端 |
| Draft generator | `services/drafts.py` | 构建 writer context → 调 LLM → 校验 |
| Review & scoring | `services/reviews.py` | 多维打分、rewrite task 创建 |
| Planner | `services/planner.py` | foundation/novel/volume plan |
| Knowledge extraction | `services/knowledge.py` | canon facts / timeline / snapshot |
| Context assembly | `services/context.py` | `SceneWriterContextPacket`（RAG）|
| Retrieval engine | `services/retrieval.py` | 60% 向量 + 20% 词法 + 20% 结构 |
| Continuity | `services/continuity.py` | fact 单调性、chapter snapshot |
| LLM gateway | `services/llm.py` | 角色分派、断路器、审计日志 |
| Prompt packs | `services/prompt_packs.py` | genre 感知的片段加载 |
| Publishing | `services/publishing/` | 多平台适配（KDP / 起点 / 番茄…）|

## 3. LLM Role System

**所有 LLM 调用经 `complete_text(req: LLMCompletionRequest)`。切勿直调 LiteLLM。**

| 角色 | 模型 | 温度 | max_tokens | 用途 |
|------|------|------|-----------|------|
| `planner` | claude-opus-4-5 | 0.82 | 16000 | 规划、多候选推理 |
| `writer` | claude-sonnet-4-5 | 0.85 | 8000 | 正文，流式 |
| `critic` | claude-haiku-4-5 | 0.25 | 2000 | 确定性评分 |
| `summarizer` | claude-haiku-4-5 | 0.20 | 1500 | 知识压缩 |
| `editor` | claude-sonnet-4-5 | 0.40 | 8000 | 定点重写 |

### 弹性机制

- 断路器：连续 5 次失败 → 60s 冷却，自动探活
- 重试：最多 3 次，指数退避（`RateLimitError` / `APITimeoutError` / `ServiceUnavailableError`）
- Per-loop HTTP pooling：每 event loop 共享一个 `httpx.AsyncClient`
- 全链路审计：每次调用落 `llm_runs` 表（model / role / tokens / latency / cost / prompt_hash）

## 4. Pipeline Flow

### Scene

```
build_scene_writer_context_from_models()   ← 组装 RAG 上下文包
    ↓
generate_scene_draft()  [writer, 0.85, streaming]
    ↓
propagate_scene_discoveries()              ← 抽 canon / timeline / snapshot
    ↓
review_scene_draft()    [critic, 0.25]     ← 5 维打分
    ↓ 任一维 < 0.70
rewrite_scene_from_task()   [editor, 0.40] ← 最多 2 次，3% 最小改善阈值
```

### Chapter

```
for each scene → run_scene_pipeline()
assemble_chapter_draft()        ← 合并场景
review_chapter_draft()          ← 4 章级维度
extract_chapter_state_snapshot()
checkpoint_commit()             ← 原子事务边界
```

### Project

```
generate_foundation_plan()
  └─ generate_novel_plan()          ← 章节契约 + scene card
       └─ for each chapter → run_chapter_pipeline()
            └─ every 20 ch → review_project_consistency()
                 └─ run_project_repair() if 失败
                      └─ export (MD / DOCX / EPUB)
                           └─ publishing schedule
```

## 5. 技术栈

| 组件 | 技术 |
|------|------|
| REST API | FastAPI + Uvicorn |
| Task queue | ARQ |
| Publishing scheduler | APScheduler + SQLAlchemy job store |
| DB | PostgreSQL 16 + pgvector（HNSW 索引）|
| Cache / PubSub | Redis |
| LLM gateway | LiteLLM（**只**经 `services/llm.py`）|
| Migrations | Alembic |
| Config | Pydantic Settings + YAML 分层 |

## 6. 代码入口速查

| 你想改 | 文件 |
|-------|------|
| pipeline 主干 | [src/bestseller/services/pipelines.py](../../../src/bestseller/services/pipelines.py) |
| 场景生成 | [src/bestseller/services/drafts.py](../../../src/bestseller/services/drafts.py) |
| 打分规则 | [src/bestseller/services/reviews.py](../../../src/bestseller/services/reviews.py) |
| 规划算法 | [src/bestseller/services/planner.py](../../../src/bestseller/services/planner.py) |
| 检索 | [src/bestseller/services/retrieval.py](../../../src/bestseller/services/retrieval.py) |
| 所有表 | [src/bestseller/infra/db/models.py](../../../src/bestseller/infra/db/models.py) |
| 配置 | [config/default.yaml](../../../config/default.yaml) + [src/bestseller/settings.py](../../../src/bestseller/settings.py) |
| Prompt packs | [config/prompt_packs/](../../../config/prompt_packs/) |
| Alembic | [migrations/](../../../migrations/) |
| 编排入口 | [docker-compose.yml](../../../docker-compose.yml) |

## 7. 开发约定（最关键 5 条）

1. 不可变数据：不在原对象上 mutate，返回新实例
2. LLM 调用走 `complete_text()`，必传 `project_id` / `workflow_run_id` 以便审计
3. 每 scene `checkpoint_commit()`，避免长事务
4. 面向客户端的进度用 `RedisProgressReporter.report()`（SSE 经 pubsub `bestseller:workflow:{id}`）
5. 新表配 Alembic，新配置走 `BESTSELLER__<SECTION>__<KEY>` env 覆盖
