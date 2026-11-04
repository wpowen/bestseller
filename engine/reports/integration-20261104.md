# 整合收尾与 8787（2026-11-04）

阶段记录：第 1、2、4 步完成；第 3 步服务已上线，5 章草稿写完，去 AI 完成 2/5 后预算暂停；第 5–7 步核对中。没有执行第 22 条。

| 步骤 | 当前实测 |
|---|---|
| 1 收尾 | 忽略 legacy-workspace/scratch；旧 scripts 和 .claude/evals 已搬入归档；原有 .remember 两处修改保留。 |
| 2 书稿 | 迁移至真实 books/，scratch/engine-books → ../books；原有 1376 个正文文件保留。 |
| 3 上线 | 唯一运行的 bestseller 容器 bookweb healthy；两个主机名均可阅读复制；页面下载 MD/TXT/DOCX/EPUB/番茄 ZIP 并检查文件成功。新书 5/5，终稿 2/5；费用含 Unknown 上界约 $0.263，原检查点冻结。 |
| 4 清理 | 下列 17 个干净 worktree 已移除，全部分支保留；3 个非 worktree 目录只移入归档。 |

移除（均在 scratch/）：int-worktree、engine-{b,c,d}-worktree、par-worktrees/{P1,P2,P3,P4,P6,P8,P9,P11,P12,Q1,Q2,Q3,Q4}。
保留（未提交改动）：.book1-fixed-runtime（1 项）、engine-worktree（INBOX）、isolated-book1（72 项）、isolated-book1-execution（58 项）、legacy-book2（36 项）、v5-worktrees/g2b（2 项）。非 worktree 的 mini-baseline/v5-test-runs/v5-b1 已移到 legacy-workspace/2026-11-04/scratch/。
其他 Claude 会话 eager-khayyam-d7a981、gracious-hodgkin-2c9a97、sad-noyce-eef051、sweet-bohr-880be5 均未操作。

尚待确认：Docker 旧镜像/停止容器与 47G 私有语料的去留；数据卷一律保留。
截图：[新书阅读](bookweb-8787/smoke-reader.jpg)、[去 AI 终稿](bookweb-8787/smoke-final.jpg)、[localhost 旧书](bookweb-8787/localhost-existing-10.jpg)、[书库](bookweb-8787/library.jpg)。浏览器 file:// 被策略阻止，HTTP 书库打开正常。

回滚（未执行）：先保存工作区修改，`git checkout legacy-final-20261104`，再 `docker compose up -d`。备份和旧数据卷均保留。
