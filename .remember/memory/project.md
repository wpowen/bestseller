# Project Memory

## User Preferences
- README 应能单独读懂：架构分层思路、端到端数据流、核心不变量（LLM 网关、事务边界、门控）；与 `docs/architecture.md` 互补而非重复全文。
- Reply in Chinese.
- Prioritize complete, executable outputs over partial snippets.
- Keep code modular, cohesive, and maintainable.
- Include clear comments only where logic is non-obvious.
- Consider robustness, error handling, and security in implementations.
- Avoid creating extra Markdown documentation unless explicitly requested.

## Working Conventions
- For this repository, prefer adding runnable planning artifacts under `examples/planning/` when building story content through the framework.
- When user requests novel writing via specific skill, deliver both planning artifacts and full chapter prose in project files (not only outlines).
- For full-length novel generation requests, place final readable deliverables under `output/ai-generated/<novel-slug>/` with volume/chapter structure.
- Keep `.audit-reports/backups/` out of version control; do not commit backup chapter files to GitHub.
