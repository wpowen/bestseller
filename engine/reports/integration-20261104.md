# 整合收尾与 8787 · 2026-11-04

结论：main 上第 1–7 步已执行，8787 可用；冒烟正文 5/5，去 AI 有效终稿 3/5 后预算暂停，**第 3 步全书终稿未达标，待验收**。第 22 条未执行，未 push。

| 步骤 | 结果与证据 |
|---|---|
| 1 收尾 | 忽略 legacy-workspace/scratch；旧 scripts/.claude/evals 仅移入归档；原有 .remember 两处修改保留。 |
| 2 目录 | 真实 books/；旧路径 symlink → ../books；原有 1376 个正文保留，新增 5 章。静态书库 HTTP 可读；浏览器策略禁止 file://，离线打开未实证。 |
| 3 上线 | 唯一运行的 bestseller 容器 bookweb healthy；127.0.0.1/localhost 均可读；旧 5/10 章书可切章复制，MD/TXT/DOCX/EPUB/番茄 ZIP 实际下载并验证文件。 |
| 冒烟 | 《南瓜箱里藏了一座会滚的城》5 章、12,864 汉字；有效终稿 3/5（两章润色、一章不变），第 4、5 章保留原稿并标未完成；179 次调用含选题，958,113 已知 tokens。已知费用估算 $0.211910 + Unknown 上界 $0.045911 = **$0.257821**；未突破 $0.30，检查点保留。 |
| 4 清理 | 逐个复查后移除 17 个干净 worktree，全部分支保留；6 个脏 worktree 保留；3 个非 worktree 目录只搬入归档（见下）。 |
| 5 Docker | 仅盘点，未删旧镜像/停止容器；62 个卷全部保留。待确认清单见下；47G 私有语料保留。 |
| 6 文档 | 快速开始、原书续跑、去 AI 缓存、五格式导出及宿主 8787 已实跑；.env 未覆盖、章节原文/调用记录未因 CLI 验证变化。补充 Python≥3.10、polished 导出版本；旧 S1–S5 说明压成一段。 |
| 7 检查 | 488 tests + 743 subtests 通过；修正迁移后 compare/gemini.md 测试路径。下列 scope PASS；26,478 个书目录文件及容器日志未检出配置密钥明文。 |

磁盘（GiB）：scratch **20.43→0.88**，legacy-workspace **52.51→53.08**；卷可用 **124.19→142.99（+18.80）**。搬书/归档不等于释放空间，Docker 未清理。

移除（scratch/）：int-worktree、engine-{b,c,d}-worktree、par-worktrees/{P1,P2,P3,P4,P6,P8,P9,P11,P12,Q1,Q2,Q3,Q4}。保留未提交改动：.book1-fixed-runtime（1项）、engine-worktree（INBOX）、isolated-book1（72项）、isolated-book1-execution（58项）、legacy-book2（36项）、v5-worktrees/g2b（2项）。mini-baseline/v5-test-runs/v5-b1 已移至 legacy-workspace/2026-11-04/scratch/。其他 Claude worktree eager-khayyam-d7a981、gracious-hodgkin-2c9a97、sad-noyce-eef051、sweet-bohr-880be5 未动。

待用户确认的旧 Docker 资源：bestseller-api/web/worker:latest **各3.07GB**；apiatest **3.09GB**；webatest/workeratest **同一镜像3.05GB**（共享层不可相加当作可回收空间）。停止容器 bestseller-v3-{g2b-redis,g2b-pg,g0-redis,g0-pg} 可写层分别 **4.1/20.5/4.1/16.4kB**，虚拟大小 **43.8/500/43.8/500MB**。数据卷与其他历史资源也未删除；47G 私有语料去留待确认。

截图：[新书阅读](bookweb-8787/smoke-reader.jpg)、[去 AI 终稿](bookweb-8787/smoke-final.jpg)、[localhost 旧书](bookweb-8787/localhost-existing-10.jpg)、[书库](bookweb-8787/library.jpg)。后处理预算不足、离线浏览器验证受限是剩余问题；可读性仍待人工验收。

```text
bookengine_lines=4950 (limit=5000)
deai_lines=789 (limit=800)
webui_lines=573 (limit=1200)
exporter_lines=253 (limit=400)
max_file_lines=365 (bookengine/batch.py)
imports_bestseller=no
cross_imports=none
forbidden_keywords=none
PASS: package limits and independence
```

本地分步提交：8c0c74b45、9c32c2461、0cf2c3685、f1d9bfffd、28a8c57b6，末次测试/报告另见本文件提交记录。回滚（未执行）：先保存工作区修改，`git checkout legacy-final-20261104`，再 `docker compose up -d`；数据库备份、补丁、旧卷均保留。停止，等待监工验收与用户确认。
