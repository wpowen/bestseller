# BestSeller agent entry

先读 [README.md](README.md)、[engine/README.md](engine/README.md)，再看 `engine/INBOX.md` 和 `engine/PLAN.md` 里当前有效的条目。老框架已归档到标签 `legacy-final-20261104` 和分支 `legacy`，主分支不要再引用或恢复其中的代码。

生产链是：选题 → 故事圣经 → 排期 → 正文 → 检查 → 去 AI 味（可选）→ 导出。
- 书目录里的正文、规划、状态、步骤检查点和调用记录（`calls.json`、`responses/`、`steps/`）是唯一的恢复依据。没有数据库。
- 模型调用只走 engine 自带的客户端，用量写进书目录。密钥只从 `.env` 读取，不打印，不写进书稿和日志。

保留以下能力：人物、时间、状态的一致性检查；已保存正文和检查点不被覆盖；局部修改后复检，不合格就回滚。
程序检测和判官分数只是参考，不能代替读者验收。报告里要明确区分 Evidence（证据）、Inference（推断）、Unknown（未知）。

开发约束：
- `engine/tools/check_scope.py` 必须 PASS。包的总行数不设上限（用户 2026-11-05 定）；代码多了就按职责拆成多个文件，单个 Python 文件 ≤500 行，前端单文件 ≤800 行、每行 ≤160 字符，不许把代码压成长行。
- 不允许 import `bestseller`；bookengine 与 deai 互不导入。
- 测试命令：`cd engine && PYTHONPATH=.:tools python -m pytest -q --no-cov tests/`。
- 写书不设金额预算（用户 2026-11-05 定）：书一旦开写就要写完。只有三种情况可以停：项目本身出错；模型账户额度或 token 用尽；接口限流或排队（这时等待重试，不算停）。花费照常记账、照常显示。千章级任务开跑前仍先告诉用户。
- 不要覆盖用户已有的改动，也不要删除 `books/` 里的书稿。没有用户要求不 push。
- 不要往真实书库 `books/` 写测试书。测试和临时实例的书目录放在系统临时目录里。
- 不要运行会打印进程参数或环境变量的命令（`ps -ef`/`ps aux`/`pgrep -f`/`pgrep -fl`/`env`/`printenv`）。本机别的进程会把密钥写在命令行上。查进程用 `pgrep -x 名称`，或者按 PID 读 `comm`。
- 网页和运行时相关的改动，验收时除了自造的夹具，还必须在真实书库的只读副本上跑一遍（见 `engine/reports/prevention-plan-20261105.md`）。
- 外部模型的评审意见，先逐条核对书稿里的引文和章号，再当成需求。

当前模块边界：
- `bookengine/q1.py` 只负责整书调度；单章逻辑在 `q1_chapter.py`，上下文与提取在 `q1_extract.py`，设计/规划/schema 在 `q1_prepare.py`、`q1_plan.py`、`q1_schema.py`。
- CLI 只接受显式 `--q1 --style b`，保留 `--extend-to` 和 Q1 `--batch`；不得恢复已删除的非 Q1 管线或 legacy 文风入口。
- `tools/runtime_support.py`、`tools/chapter_outputs.py` 提供共享协议，不反向依赖 bookengine/deai；`tools/webui/` 后端与 JS/HTML/CSS 分开维护，书库缓存只是派生视图，文件仍为恢复依据。
- 当前待办只在 `engine/INBOX.md`；已结束或被取代的指令原文见 `engine/reports/inbox-archive-2026-11.md`，历史 PLAN 和报告不能覆盖当前约定。
- Python 用 Ruff、行宽 120：`cd engine && python -m ruff format --check .`；历史 reports 保持原样，不参与格式化。
- 应用发布版本唯一来源是 `engine/tools/version.py`，包元数据、bookengine 与网页共用；去 AI 的规则/缓存版本独立保留，不作发布版本使用。
