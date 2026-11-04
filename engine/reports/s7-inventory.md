# 第 21 条 D 轨：S7 只读归档预演

2026-11-04；独立 worktree `scratch/engine-d-worktree`，分支 `feat/book-engine-d-inventory`，基点 `9b3f009f2`。验收依据为 `engine/INBOX.md:462–529`，特别是 D/E/S7 的边界；第 21 条覆盖旧 PLAN。**结论：新引擎可按下表划出独立保留集，但文档与 Compose 尚有旧框架引用，B/C/E 尚待合流验收；本页不是归档或上线完成证明。** 唯一写入为本报告；未移动、删除或修改源码/配置，未读取 `.env`，未调用 Client/LLM、访问生产 DB/d407、运行部署、push 或合并 main。INBOX 所述“生产库没有书”“删前备份存在”是背景，本轨均未验证，不作为已证事实。

| 处置建议 | 精确范围 / 真实边界 | 核对结果与待办 |
| --- | --- | --- |
| 主分支保留 | `engine/` 全部，含 `bookengine/`、`deai/`、`data/`、`examples/`、`tests/`、`tools/`、包配置与文档 | 基点 178 个文本文件、21,701 行；**根目录 `tests/` 与 `engine/tests/` 区分处理**。`storage.py`、runner、q1 的书目录/检查点是恢复依据，不依赖 PG。 |
| 主分支保留：配置 | `config/prompt_packs/*.yaml`（基点全部 32 份）、`config/scene_grounding.yaml`、`config/prose_style_anchors.yaml`、`config/market_profiles/fanqie/xuanhuan-brain.yaml` | 共 35 份、4,697 行；下文记录真实 `Path.read_text` 路径。仅保留这些外部 config；`engine/data/` 与 `engine/deai/data/` 本就在 engine 保留集内。 |
| 主分支保留：新网页，待 B 合流 | `engine/tools/webui.py`、`engine/tools/webui/`，及 B 在 `engine/tools/webui*` 边界内交付的必要文件 | **规划边界**：公开 bookengine 命令 + 输出目录；B 阶段 8765、E 阶段 8787；下载接 C。D 基点未含新网页，不能据旧 `src/bestseller/web` 推断实现或声称运行通过。 |
| 主分支保留：导出，待 C 合流 | 已有 `engine/bookengine/export.py`；C 必要时新增 `engine/exporter/` | 基点 export 实际仅 md/docx，调用本包 `storage.save` 和 python-docx，不 import legacy。txt/epub/番茄分章 txt 是 C 的**待合流边界**；不接 `src/bestseller/services/exports.py`。 |
| 主分支保留：入口与支撑 | `README.md`、`AGENTS.md`、`.gitignore`、`.dockerignore`、`docker-compose.yml`、`docker-compose.ssd.yml` | 当前字节共 1,793 行；文档改写、旧 Compose 的隔离见下文，不能照现状默认启动。`.env.example` 按验收应保留，但本 worktree **不存在且基点未跟踪**，需后续补安全模板，D 不创建。E 的 `engine/Dockerfile`/bookweb 也尚未纳入基点。 |
| 移入 legacy：旧运行与工具 | `src/bestseller/`、根 `tests/`、`scripts/`、`migrations/` | 4,930 个文本文件；旧导出随旧代码归档。旧根 `Dockerfile`、`pyproject.toml`、`uv.lock`、`alembic.ini`、`Makefile`、`start.sh`、`stop.sh`、`studio.sh` 不作新 engine 的安装/运行入口。 |
| 移入 legacy：旧专用配置 | 基点 `config/` 中除上述 35 份外的全部文件 | 156 份、30,776 行；包括 `default.yaml`、quality/gate/genre 阈值、`novel_categories/`、`genre_taxonomy.yaml`、`platform_profiles.yaml`、其他市场配置等。旧 PLAN 的宽泛素材清单不等于运行依赖，不能照单保留。 |
| 旧 Docker 服务归 legacy 使用，E 先隔离 | `web`、`api`、`worker`、`scheduler`、`mcp`；`migrate` 仍为旧框架专用 | 按第 21 条，前五个改 `profiles: [legacy]`、默认不启动，**不直接删除定义**；migrate 已有 `[migrate]` profile。旧构建与执行代码归档，不能承诺归档后还能在新主分支直接构建 legacy profile；需 legacy checkout。`db`/`redis`/`backup` 按 E 暂留，D 未核验运行状态或决定其去留。 |
| 其余基点文件：精简方案归历史快照，待确认 | 上述保留集与旧四目录/旧 config 之外的补集：702 个文本文件、904,299 行 | 包括旧 `docs/`、`data/`、`examples/`、`reports/`、`.agents/`、`.claude/`、`.cursor/`、`.audit-reports/`、`.playwright-cli/`、`.remember/`、其余根文件，以及独立的 `story-factory/`、`douyin_plova/`、`.codex-pet/`、`tmp/`。这几项独立项目不是 bookengine 依赖，也不据此认定都是旧框架；表内“精简主分支”数值以它们不留主分支为条件，用户若另留须重算。非跟踪的本机资料不在本清单中，不能据本页删除。 |

**真实读取核对（Evidence，无模型）：** CLI `engine/bookengine/__main__.py:11–17` 的默认 assets 为本 worktree 根下 `config/`；显式 `--assets` 可替换根目录。`config.py:40–61` 的 `knowledge` 直接读题材 YAML 与 scene_grounding，`style_rules` 直接读 prose_style_anchors；YAML 的说明/字段不会自动触发别的文件读取。`q1_plan.py:130–131` 也直接读同一题材路径；`longform.py:169–171` 在非 serial 分支另读市场 YAML。没有把 PLAN 列出的其他配置当作依赖。

以 `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=engine python3 -` 执行纯读取，临时包装 `Path.read_text` 记录绝对路径，只 import `bookengine.config`，从现有 `../engine-books/growth-payoff-3/{suspense-20,xuanhuan-20}/run.json` 的 `book` 构造 Book，再调用 `knowledge(book, assets)`、`style_rules(assets)`。实际记录（以下均相对于 D worktree，前缀为 `/Volumes/MACSSD/owen-home/Documents/workspace/bestseller/scratch/engine-d-worktree/`）：

| 输入 / 分支 | `Path.read_text` 实际记录 | 只读结果 |
| --- | --- | --- |
| `growth-payoff-3/suspense-20/run.json` 的真实书参数 | `config/prompt_packs/suspense-mystery.yaml` → `config/scene_grounding.yaml` → `config/prose_style_anchors.yaml` | knowledge 2,551 字符，style 9 条；现有目录 20 个 body、bible/state 均存在，首章读取 2,579 字符。 |
| `growth-payoff-3/xuanhuan-20/run.json` 的真实书参数 | `config/prompt_packs/xuanhuan-power-fantasy.yaml` → `config/scene_grounding.yaml` → `config/prose_style_anchors.yaml` | knowledge 1,613 字符，style 9 条；现有目录 20 个 body、bible/state 均存在，首章读取 3,111 字符。两书均无 style_reference；未续跑或导出。 |
| 所有现存题材文件的素材读取覆盖 | 对 `sorted((assets/'prompt_packs').glob('*.yaml'))` 的 32 个 stem 逐个调用 `knowledge(Book(stem,5,2200,'只读素材预演'), assets)`，再调用 style_rules | 32/32 素材读取成功；这不等于全部题材通过 Book.read 的参数校验、Q1 写作能力或读者验收。 |
| 非 serial 长篇市场读取 | `config/market_profiles/fanqie/xuanhuan-brain.yaml` | 解析 longform.py 的 AST，仅求值其真实 `read_text()` 表达式，读出 482 字符；没有调用 `_run` 或创建 Client。 |

去重后 config 路径 **35 个**；进程检查 `bookengine.client` 未导入、`bestseller` 未导入。现有书的 `run.json` 标记 complete 仅为文件回读，非 D 新生成/人工质量验收。纯读取覆盖不到未来 B/C/E 新依赖，合流时应复核。

**保留物中的遗留引用与处理建议（未修改）：**

| 位置 | 残留与影响 | 归档前建议 |
| --- | --- | --- |
| `AGENTS.md:3–11`、根 `README.md` | PG/complete_text、旧服务/数据库 schema/导出命令、旧路径与技能入口大量残留；归档后会误导新任务或成为断链 | 按下表改原则，旧完整说明留 legacy；README 的架构图、安装/启动/测试/导出章节一并重写。 |
| `engine/PLAN.md:11–14` | 仍列宽泛 config，且写“导出在 S6 才接 src/bestseller/services/exports.py” | 第 21 条已废止该方案；标明历史被覆盖，新的导出入口写本包/C。 |
| `engine/data/NOTICE.md:40–60` | 仍有旧 `src/bestseller/services/novel_expression_guidance.py` 和即将归档 config 的历史路径 | 属历史说明，不是运行 import；保留说明及许可，归档时把历史路径标为 legacy 可查，不能当当前依赖。 |
| `engine/INBOX.md:119,473–519` | 旧 UI 参考、旧导出否决与归档步骤的历史引用 | 保留历史语境，不把“参考旧样式”升级为导入旧运行代码。 |
| 两份根 Compose | 主 Compose 仍有旧命令、共享旧应用构建/环境/挂载；ssd 文件注释仍指旧 src 活挂载和 scripts 验证脚本 | E 隔离旧服务并新增独立 bookweb 后重查；本表保留整个当前文件计数，不假装旧定义已删或 profile 已生效。 |
| `.dockerignore:52–62`、`.gitignore` | 前者还写旧 Dockerfile COPY data 的说明，后者保留旧工具/语料路径忽略项 | 前者按 E 新构建边界校准；忽略项本身不执行旧框架，可保留保护本机资料。 |
| engine Python 与保留的 35 份 config | 对全部 117 个 engine Python 文件做 AST import 检查，未发现 bestseller 导入（含常量动态导入）；保留 config 未发现旧代码路径/PG/complete_text 引用 | 本地静态独立性证据；不声称已经验证未来新网页、exporter 的运行。`--style legacy`、旧检查点兼容变量等指 engine 自身历史格式，不是旧 bestseller 依赖。 |

**归档前后行数实测口径：** 固定样本为基点 `git ls-tree -r --name-only -z 9b3f009f2` 中的路径；逐个读 D worktree 当前文件字节（基点文件无改动）。只计无 NUL 且严格 UTF-8 可解码的普通文本，行数为 `len(text.splitlines())`，含空行、注释、Markdown/YAML/JSON/HTML 等；另列 `.py` 同口径行数，**文本总行数不冒充代码行数**。不跟随符号链接；任意路径段属于 `.git/scratch/output/node_modules/.venv/venv/__pycache__/deps/vendor/site-packages/dist/build/.pytest_cache/.mypy_cache/.ruff_cache` 即排除；`.env` 与 `.env.*` 不读取（`.env.example` 本次缺失）。实际排除 4 个 build 内文件、2 个二进制/非 UTF-8 文件；其余 6,007 个文本文件。仅计基点跟踪文件，故不计本报告新增内容、非跟踪本机文件及同级 worktree/书稿；B/C/A/E 后续差量未知。

“归档后”是**对本页拟保留集现存字节逐一实测的只读预演**：路径以 `engine/` 开头，或属于上述 35 个 config，或是 `AGENTS.md/README.md/.gitignore/.dockerignore/docker-compose.yml/docker-compose.ssd.yml` 六个根文件。并未真的归档；实际归档、文档改写和合流后的最终行数仍待重测，不能把预演数写成生产结果。

| 固定基点样本 | 文本文件数 | 全部文本行 | 其中 Python 行 |
| --- | ---: | ---: | ---: |
| 归档前：全仓纳入口径 | 6,007 | 3,280,965 | 1,248,593 |
| 旧 `src/bestseller/` | 2,424 | 1,639,365 | 586,645 |
| 旧根 `tests/` | 2,043 | 525,383 | 515,492 |
| 旧 `scripts/` | 415 | 148,017 | 120,621 |
| 旧 `migrations/` | 48 | 4,934 | 4,917 |
| 旧四目录合计 | 4,930 | 2,317,699 | **1,227,675** |
| 归档后预演：保留 `engine/` | 178 | 21,701 | 16,351 |
| 归档后预演：保留 config | 35 | 4,697 | 0 |
| 归档后预演：保留六个根文件 | 6 | 1,793 | 0 |
| **归档后预演合计** | **219** | **28,191** | **16,351** |
| 移出拟精简主分支的差量 | 5,788 | 3,252,774 | 1,232,242 |

INBOX 的“约 128 万旧框架”只作背景；本次独立实测旧四目录 Python 为 **1,227,675** 行。core/deai 行数与包含工具、测试、数据的 engine 全目录行数不是同一口径。只运行只读 `PYTHONDONTWRITEBYTECODE=1 python3 engine/tools/check_scope.py`，实际输出：

```text
bookengine_lines=4984 (limit=5000)
deai_lines=674 (limit=800)
webui_lines=0 (limit=1200)
max_file_lines=365 (bookengine/batch.py)
imports_bestseller=no
cross_imports=none
forbidden_keywords=none
PASS: package limits and independence
```

此基点检查只扫 `engine/{bookengine,deai,webui}`，**未覆盖 B 的 tools/webui 边界，也未检查 exporter**；0 不代表新网页验收。新 limits/路径由 A 合流时统一修改；C 需满足 export 核心净增 ≤150 / exporter ≤400，B ≤1,200，单文件 ≤500。D 未跑付费书、部署或依赖数据库的测试。

**原则原文与建议替换（仅建议，AGENTS.md/README.md 原文件均未改）：**

| 文件与原文 | 建议替换 |
| --- | --- |
| `AGENTS.md:5`：“PostgreSQL 是事实源，`output/` 是可重建导出物” | 当前 bookengine 以指定书目录里的正文、规划、状态、步骤检查点及调用记录为恢复依据；md/docx 等由当前书稿独立导出。PG 是 legacy 的存储约定，不是新引擎事实源。 |
| `AGENTS.md:5`：“所有 LLM 调用须经 `src/bestseller/services/llm.py::complete_text` 并带 `project_id`、`workflow_run_id`。” | 模型调用使用 engine 自有客户端和书目录记录，不导入 legacy complete_text，不强加旧 PG 标识合同；deai 接入边界以 A 合流实现为准。 |
| `AGENTS.md:7`：“保留硬约束、软约束 Override/追债、Canon/Timeline 单调性、来源归因、checkpoint 和审校回执。” | 保留当前 engine 的人物/时间/状态一致性、已保存正文与检查点、局部修改及复检回滚规则；不宣称实现了旧 Override/追债/数据库回执机制。继续明确 Evidence / Inference / Unknown，程序检测不替代用户阅读验收。 |
| `AGENTS.md:9`：“测试时先读取现有隔离测试库配置并核验库名确为测试库，再按需使用 `.venv/bin/pytest --no-cov`” | 新引擎本地检查用 bookengine 自有测试与 check_scope，不为文件型引擎测试读取 DB 配置或连接生产库；legacy 测试规范留 legacy。 |
| `AGENTS.md:3,11`：“先按任务读取最相关的 [README.md](README.md)、`docs/`、`.agents/skills/` 或 `src/` 文档”；“快速入口：`README.md`、`docs/architecture.md`、`src/bestseller/services/pipelines.py`、`src/bestseller/services/project_health.py`、`.agents/skills/bestseller-framework/SKILL.md`。” | 入口改为根 README、engine/README、INBOX/PLAN 的当前有效条目、engine/bookengine 与对应模块文档；归档旧入口注明 legacy。 |
| `README.md:104`：“正文、规划版本、Canon、时间线、审校与 `LlmRun` 以 PostgreSQL 为权威；`output/` 下的 Markdown 等是**导出衍生物**，可随时从库中重建。” | 改写为文件型书目录、检查点恢复和书稿导出关系；同时替换第 18、711 行的 PG 唯一事实源表述，不能承诺从 PG 重建新书。 |
| `README.md:143`：“**所有 LLM 调用**经 `src/bestseller/services/llm.py::complete_text`，并传入 `project_id` 与 `workflow_run_id`，自动落 `llm_runs`。” | 改为 engine 自有 Client、书目录 `calls.json`/`responses/`/`steps/` 及已知/未知用量记录；不再写自动落旧表。同步重写旧 LiteLLM 角色配置章节。 |
| `README.md:188`：“**导出** \| Markdown · DOCX · EPUB · PDF \| `exports.py`”；第 1005 行起：“bestseller export markdown my-story” 等旧命令 | 写 bookengine 独立 export；基点只声明 md/docx，C 合流验收后再写 txt/md/docx/epub 与分章 txt 的真实入口，不承诺 PDF/Obsidian。 |
| `README.md:857`：“./start.sh                  # 创建 venv、拉起 PostgreSQL、执行迁移” | 用 engine 自有安装/CLI 与 B 的真实使用方式替换；E 通过后再写 bookweb/8787 的实际启动与挂载。旧 api/worker/scheduler/mcp/migrate 说明归 legacy，不假装 E 已切换。 |

**后续边界：** 用户确认 D 清单及原则改写，B/C 与 E 分别通过后，S7 才能打 `legacy-final-YYYYMMDD` 标签、建 legacy 分支并实际归档；回滚方法为 `git checkout legacy-final-…`（占位名称，D 未创建标签）。保留 `.env.example` 缺项、上述文档/Compose 引用和合流后重计行数均需处理；生产库状态、备份、8787 页面及真实开书冒烟在本轨为 **Unknown/未验证**。本轨已完成的是只读依赖预演、当前字节计数和单报告交付。
