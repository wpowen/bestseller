# BestSeller · bookengine

从选题到整本中文网文：故事圣经 → 排期 → 正文 → 检查 → 可选去 AI → 终稿与导出。没有数据库；每本书的文件和检查点就是恢复依据。模型调用、用量与费用持续记录，选题、写作、去 AI 均不设金额预算。

## 启动书房（8787）

首次创建配置，在 `.env` 填入 `MINIMAX_API_KEY`：

```sh
test -f .env || cp .env.example .env
docker compose up -d --build
open http://127.0.0.1:8787
```

已有服务和书正在运行时，等书完成再重新部署。书稿位于仓库 `books/`，容器重建不会删除书稿。只监听本机，网页允许 `localhost` 和 `127.0.0.1`。

不用 Docker 时，先准备 Python 3.10+、PyYAML 和 python-docx，然后从仓库根目录启动：

```sh
PYTHONPATH=engine python -m tools.webui --env .env --books books --port 8787
```

同一书目录只交给一个网页服务管理；Docker 已占用 8787 时不要再启动宿主服务。

## 开书与阅读

1. 进入“开新书”，选题材、章数和频道；想法可以留空。生成选题、编辑卡片后选定故事；“换一批”创建新批次，历史批次只在主动恢复时读取。
2. 设置并发书数并开始写作。“自动去 AI”默认开启；一本书占一个并发名额。
3. 每章正文和完成检查点落盘后，去 AI 立即接手，写手继续下一章；每本书最多同时处理 3 个已完成章节。关闭自动去 AI 时，原稿成为终稿。
4. 书库显示“终稿 N / 计划 M 章”和正文进度，详情页提供目录、规划和检测结果。阅读设置里的“查看修改对照”可以查看草稿。
5. 下载 MD、DOCX、TXT、EPUB 或番茄分章 ZIP，均只导出已有终稿；缺章会写入导出说明。也可以复制本章或全书。

去 AI 合格采用修改稿；没有修改、保真复检失败或模型坏返回重试耗尽时，原稿成为终稿，并保留失败阶段和原因，继续处理下一章。程序指标和判官分数仅供参考，最终仍需读者验收。

## 停止与断点恢复

- **等待接口**：限流、排队、HTTP 408/429/5xx、超时和连接错误持续退避重试，最长每 10 分钟一次，不设总等待时长上限。网页显示次数和下次重试时间。
- **额度用尽**：保留检查点；充值后点“继续”从断点接着跑。
- **项目出错**：显示阶段和错误摘要，修复后从检查点重试。坏返回每阶段至少重试 6 次；章节质量提示记录后继续。

用户可主动暂停和继续。服务重启时检查旧进程和锁，从检查点恢复写手与去 AI watcher；已有正文、终稿和调用记录不覆盖，不重复处理已完成章节。写手与去 AI 分别保存状态，某一阶段失败不停止另一阶段。旧书开启自动去 AI 但缺流水线信息时，会补齐元数据和缺失 watcher。

删除书只需一次确认；有运行进程的书拒绝删除。只允许管理 `books/webui/` 下的直属书和归档，费用和操作日志保留。弹窗与菜单可点外部或按 Esc 关闭。

## 命令行与开发

当前只支持显式 `--q1 --style b` 的写作管线，另保留 Q1 批量和显式续写。完整命令、模块职责及检查点说明见 [engine/README.md](engine/README.md)。

```sh
cd engine
python tools/check_scope.py
PYTHONPATH=.:tools python -m pytest -q --no-cov tests/
python -m ruff format --check .
```

本机验收用主仓库虚拟环境解释器：

```sh
cd engine
PYTHONPATH=.:tools /Volumes/MACSSD/owen-home/Documents/workspace/bestseller/.venv/bin/python -m pytest -q --no-cov tests/
```

各包总行数不限；单 Python 文件不超过 500 行，前端单文件不超过 800 行、每行不超过 160 字符。Python 按 120 列格式化，超限按职责拆文件。禁止 import `bestseller`；bookengine 与 deai 互不导入。`check_scope.py` 保留独立性和禁用词检查。

用户可见更新见 [CHANGELOG.md](CHANGELOG.md)。当前待办见 [engine/INBOX.md](engine/INBOX.md)，历史指令保存在 [指令归档](engine/reports/inbox-archive-2026-11.md)，实测证据见 [engine/reports/](engine/reports/)。旧框架只保存在 `legacy-final-20261104` 标签和 `legacy` 分支；当前代码不依赖它。千章级任务开始前须告知用户。
