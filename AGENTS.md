# BestSeller agent entry

先读 [README.md](README.md)、[engine/README.md](engine/README.md)，再看 `engine/INBOX.md` 和 `engine/PLAN.md` 里当前有效的条目。老框架已归档到标签 `legacy-final-20261104` 和分支 `legacy`，主分支不要再引用或恢复其中的代码。

生产链是：选题 → 故事圣经 → 排期 → 正文 → 检查 → 去 AI 味（可选）→ 导出。
- 书目录里的正文、规划、状态、步骤检查点和调用记录（`calls.json`、`responses/`、`steps/`）是唯一的恢复依据。没有数据库。
- 模型调用只走 engine 自带的客户端，用量写进书目录。密钥只从 `.env` 读取，不打印，不写进书稿和日志。

保留以下能力：人物、时间、状态的一致性检查；已保存正文和检查点不被覆盖；局部修改后复检，不合格就回滚。
程序检测和判官分数只是参考，不能代替读者验收。报告里要明确区分 Evidence（证据）、Inference（推断）、Unknown（未知）。

开发约束：
- `engine/tools/check_scope.py` 必须 PASS：bookengine ≤5000 行，deai ≤800，webui ≤1200，exporter ≤400，单文件 ≤500 行。
- 不允许 import `bestseller`；bookengine 与 deai 互不导入。
- 测试命令：`cd engine && PYTHONPATH=.:tools python -m pytest -q --no-cov tests/`。
- 用真实模型写书前先确认预算。千章级任务要另外取得授权。
- 不要覆盖用户已有的改动，也不要删除 `books/` 里的书稿。没有用户要求不 push。
