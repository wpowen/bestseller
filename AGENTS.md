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
