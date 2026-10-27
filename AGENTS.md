# BestSeller agent entry

先按任务读取最相关的 [README.md](README.md)、`docs/`、`.agents/skills/` 或 `src/` 文档；不要把单一 Skill 当作整个项目流程。

生产链是 `Premise → Plan → Draft → Review → Rewrite → Knowledge → Export`。PostgreSQL 是事实源，`output/` 是可重建导出物；所有 LLM 调用须经 `src/bestseller/services/llm.py::complete_text` 并带 `project_id`、`workflow_run_id`。

保留硬约束、软约束 Override/追债、Canon/Timeline 单调性、来源归因、checkpoint 和审校回执。静态或 fixture 通过不等于真实模型、读者或榜单证据；报告明确 Evidence / Inference / Unknown。

测试时先读取现有隔离测试库配置并核验库名确为测试库，再按需使用 `.venv/bin/pytest --no-cov`；不要在入口中写连接串或密码。生产操作须在明确授权后遵守项目既有流程，并保留生产回读证据；不要覆盖用户已有改动。

快速入口：`README.md`、`docs/architecture.md`、`src/bestseller/services/pipelines.py`、`src/bestseller/services/project_health.py`、`.agents/skills/bestseller-framework/SKILL.md`。
