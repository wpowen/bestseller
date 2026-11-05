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
2. 在书库“⚙ 设置”里设置同时写作的书数（默认 3，范围 1–4）。选题后开始写作；“高级”中的“自动去 AI”默认开启，一本书占一个名额。
3. 每章正文和完成检查点落盘后，去 AI 立即接手，写手继续下一章；每本书最多同时处理 3 个已完成章节。关闭自动去 AI 时，原稿成为终稿。
4. 书库显示“终稿 N / 计划 M 章”和正文进度，详情页提供目录、规划和检测结果。阅读设置里的“查看修改对照”可以查看草稿。
5. 下载 MD、DOCX、TXT、EPUB 或番茄分章 ZIP，均只导出已有终稿；缺章会写入导出说明。也可以复制本章或全书。

去 AI 合格采用修改稿；没有修改、保真复检失败或模型坏返回重试耗尽时，原稿成为终稿，并保留失败阶段和原因，继续处理下一章。程序指标和判官分数仅供参考，最终仍需读者验收。

## 停止与断点恢复

- **等待接口**：限流、排队、HTTP 408/429/5xx、超时和连接错误持续退避重试，最长每 10 分钟一次，不设总等待时长上限。网页显示次数和下次重试时间。
- **额度用尽**：保留检查点；充值后点“继续”从断点接着跑。
- **项目出错**：显示阶段和错误摘要，修复后从检查点重试。坏返回每阶段至少重试 6 次；章节质量提示记录后继续。

用户可主动暂停和继续。服务重启时检查旧进程和锁，从检查点恢复写手与去 AI watcher；已有正文、终稿和调用记录不覆盖，不重复处理已完成章节。写手与去 AI 分别保存状态，某一阶段失败不停止另一阶段。旧书开启自动去 AI 但缺流水线信息时，会补齐元数据和缺失 watcher。

网页“永久删除”只需一次确认；书目录先移入回收站，7 天内可在书库归档区的“回收站”入口恢复，超过 7 天由服务下次启动时清理；已完成或已暂停的书会先结束本书的去 AI watcher，有写作进程或无法确认归属的进程时拒绝删除。终稿齐全的书不会重复启动 watcher。只允许管理 `books/webui/` 下的直属书和归档，费用和操作日志保留，操作记录含来源（网页 / API / CLI）、User-Agent 和客户端地址。弹窗与菜单可点外部或按 Esc 关闭。

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

### 验收必跑

网页或运行时改动必须通过真实书库形态回归（默认已接入 pytest）：

```sh
cd engine
PYTHONPATH=.:tools python tools/real_library_smoke.py
PYTHONPATH=.:tools python -m pytest -q --no-cov tests/
```

四本真实书的脱敏精简快照在 `engine/tests/fixtures/real_library/`，保留首三章、末章及代表状态章，
总计不超过 3MB。脚本只在系统临时目录的副本上启动随机端口 HTTP 服务，检查书库、详情、逐章阅读数据、
五种导出和归档 / 恢复 / 回收站流程；书库接口每次须小于 500ms。脚本禁止启动模型子进程。
快照捕获时四本均已完稿；无终稿、重命名、旧版独立去 AI 等补充状态仅在临时副本中构造。
HTTP 检查不替代浏览器显示验收或读者验收。

测试启动和 pytest 文件操作会拒绝仓库 `books/`（包括主仓库、子目录和符号链接别名）；
临时服务也须设置 `BOOKWEB_TEST=1` 并使用系统临时目录。生产启动按上面的普通命令执行。
监工可在运行前后生成和比对真实书库文件清单（清单放在书库外）：

```sh
python engine/tools/books_guard.py snapshot /tmp/books-before.json
python engine/tools/books_guard.py verify /tmp/books-before.json
```

`verify` 列出新增、删除和改动文件，有差异时退出码为 1；运行中的合法写作也可能产生差异，须按来源核对。
快照刷新工具 `python engine/tools/real_library_snapshot.py /临时目录/real_library` 只读取书库，
输出采用字段白名单并脱敏，不包含 `responses/` 或 `http/` 原始调用。

测试失败输出及网页子进程日志写盘前会过滤 `.env` 中长度至少 12 的值。报告也应在写盘前过滤：

```sh
python engine/tools/redact.py --env .env --output /tmp/report.md < /临时目录/report-source.md
python engine/tools/verify_review.py /只读书稿副本 /评审.md --env .env --output /tmp/review-check.md
```

评审核对输出“核实 / 找不到 / 无引文”，只对照引用章的终稿、正文和目录；忽略空白，但不会把转述认作逐字引文。
目录与章名会单独核对，未带引文的事件判断仍需人工阅读。详情页“检测报告（参考）”只读取已有的
`book_checkup.json`，展示阶段、地点、对手提及跨度、章名重复及格式 / 衔接 / 登场 / 称呼提示；
文件缺失时显示未知，统计不代表读者验收。

各包总行数不限；单 Python 文件不超过 500 行，前端单文件不超过 800 行、每行不超过 160 字符。Python 按 120 列格式化，超限按职责拆文件。禁止 import `bestseller`；bookengine 与 deai 互不导入。`check_scope.py` 保留独立性和禁用词检查。

用户可见更新见 [CHANGELOG.md](CHANGELOG.md)。当前待办见 [engine/INBOX.md](engine/INBOX.md)，历史指令保存在 [指令归档](engine/reports/inbox-archive-2026-11.md)，实测证据见 [engine/reports/](engine/reports/)。旧框架只保存在 `legacy-final-20261104` 标签和 `legacy` 分支；当前代码不依赖它。千章级任务开始前须告知用户。

## 交付体验与独立试跑

按图操作见 [体验指南](docs/体验指南.md)。首次建议 5 章，自动去 AI 保持开启。选题先展示卡片，参考分随后补上；点选后，卡片的书名、人物、身份、场景、规则和开场成为固定事实。详情页“你选的选题”可随时对照。1000/2000 章会显示按历史记账推算的时长和费用，尚未完整验证。

需要在已有 8787 服务旁独立试跑时，先把本仓库克隆到一个新目录并进入该目录，再复制你已配置好的 `.env`：

```sh
# 在新克隆目录内执行；把路径替换成你自己的已配置文件
cp /你的配置目录/.env .env
chmod 400 .env
mkdir trial-books
export BOOKWEB_PORT=8788
export BOOKS_HOST_DIR="$PWD/trial-books"
export BOOKWEB_IMAGE=bestseller-bookweb:deliver-trial
docker compose -p bestseller-deliver up -d --build
open http://127.0.0.1:8788
```

镜像标签、项目名、端口和书目录均与常用实例分开；不要在旧书仍在写作时重建原实例。网络正常且镜像基础层已有缓存时，首次构建一般需 1–3 分钟。只需 Docker Compose；无需在宿主机安装 Python 依赖。

试跑结束，先下载需要的终稿，然后在同一终端、同一克隆目录执行：

```sh
docker compose -p bestseller-deliver down --rmi all
```

确认不再需要试跑书后，可删除整个新克隆目录（含 `.env` 和 `trial-books/`）。此操作不涉及原仓库、原书目录和 8787 服务。普通书房仍按上面的默认启动命令使用。
