# BestSeller · bookengine

从一句话（或空白）到一整本可读、可复制、可导出的中文网文。

当前主线是 `engine/` 里的 **bookengine**：标准库为主的 Python 包，没有数据库，用文件检查点断点续写，模型用 MiniMax M3.1 Flash。
老框架（PostgreSQL + API + worker + Web Studio，约 123 万行 Python）已于 2026-11-04 归档：

```bash
git checkout legacy-final-20261104   # 或 git checkout legacy
```

## 快速开始（网页，端口 8787）

```bash
test -f .env || cp .env.example .env  # 仅首次创建；在 .env 填 MINIMAX_API_KEY
docker compose up -d --build
open http://127.0.0.1:8787
```

在页面上依次操作：

1. **开书**：选题材、填章数（5–2000），一句话想法可以空着；点“生成选题”，从 12 个设定里点选一个。
2. 设置并发书数，“自动去 AI”默认开启，然后点“开始写作”。选题、写作和去 AI 都不设金额预算；费用照常记账和显示。
3. **书库**：查看每本书的进度、花费、状态；可以暂停、继续、从检查点重试。
4. **阅读**：按终稿目录阅读；阅读设置里的“查看修改对照”可查看草稿与终稿；可以复制本章或全书，下载 MD / DOCX / TXT / EPUB / 番茄分章 ZIP；也能查看故事圣经、章纲和检测报告。五种格式只导出已有终稿；缺章会在页面和导出说明中列出。

写作与去 AI 同时启动；每章完成检查点落盘后立即处理，写手继续下一章。去 AI 合格用修改稿，否则保留原文作为终稿，检查记录照常保留。关闭自动去 AI 时，已完成原文直接成为终稿。两条进程各自恢复，已有终稿不重复处理或覆盖。

书一旦开写就持续写完，费用不阻断运行。只有项目出错、账户额度或 token 用尽、接口等待三类异常状态；
接口等待不设总时长上限，退避后继续重试。已保存正文和检查点保留，修复或充值后从断点继续；
千章级任务开跑前仍须告知用户。

书稿都存在 `./books/`，每本书一个目录，删除容器不影响书稿。
`scratch/engine-books` 保留为指向 `../books` 的兼容链接；网页服务始终使用真实的 `books/` 目录。
不开服务时，可以直接双击 `books/webui/LIBRARY.html` 离线阅读。

## 命令行

在 `engine/` 目录执行，以下 `python` 指 Python 3.10+ 且已安装 `PyYAML` 和 `python-docx` 的解释器（已有虚拟环境时先激活；本机系统 Python 3.9 不适用，Docker 自带 3.12）。首次写书使用示例设定；续跑时把两个变量改为原设定文件和原书目录：

```bash
# 写一本书：书的设定写在一个 YAML 里，字段为 prompt_pack / chapters / words_per_chapter / user_input
BOOK_CONFIG=examples/realistic-life.yaml
BOOK_DIR=../books/my-book
PYTHONPATH=. python -m bookengine "$BOOK_CONFIG" --q1 --style b --env ../.env --out "$BOOK_DIR"
# 去 AI 味：只改命中的句子，保真检查不过就回滚；结果写到 final/
PYTHONPATH=. python -m deai run "$BOOK_DIR" --env ../.env
# 限定章节可加 --chapters 1-2；同一输入与配置的已完成章节会跳过，不重复付费
# 写作和去 AI 的调用记录分别保留，续跑不清零；未知用量仍标为未知，不作为金额停机条件。
# 导出：txt / md / docx / epub，以及番茄上传用的分章 txt
PYTHONPATH=. python -m exporter "$BOOK_DIR"
# final/polished 均只导出终稿；回滚章使用原文，缺章列入导出说明，不以草稿补齐
PYTHONPATH=. python -m exporter "$BOOK_DIR" --version polished
# 不用 Docker 起网页
PYTHONPATH=. python -m tools.webui --env ../.env --books ../books --port 8787
```

同一条命令重跑会复用检查点，不会重写已经完成的章节。Docker 已运行时，先停服务再选择宿主网页命令，避免两个服务操作同一写作队列；查看命令参数可用 `--help`。CLI 导出的默认 `final` 与 `polished` 均读取 `final/` 终稿；显式 `--version draft` 仅用于开发时查看初稿。

## 目录

| 路径 | 内容 |
|---|---|
| `engine/bookengine/` | 写书主流程：选题 → 圣经 → 排期 → 正文 → 检查 |
| `engine/ideation/` | 选题工坊：市场快照、抽轴发散、筛选与卡片呈现 |
| `engine/tools/runtime_support.py` | 共享的运行状态与接口错误处理，通过 `tools.runtime_support` 导入，scope 单列 `shared` 统计 |
| `engine/deai/` | 独立的去 AI 味流程：检测、局部修复、复检、回滚 |
| `engine/exporter/` | 导出 |
| `engine/tools/webui*` | 本地网页，Python 后端与 JS / HTML / CSS 前端分开维护 |
| `config/` | 题材包 `prompt_packs/`、场景落地、文风锚点、番茄市场画像，引擎只读 |
| `engine/INBOX.md` / `engine/PLAN.md` | 开发指令收件箱与阶段计划 |
| `engine/reports/` | 各轮实测报告 |
| `books/` | 书稿与检查点（不进 git） |

## 检查

所有包和前端总行数均不设上限，代码按职责拆分。单个 Python 文件 ≤500 行；
前端单文件 ≤800 行，每行 ≤160 字符，不为凑行数压缩代码。
禁止 import `bestseller`；bookengine 与 deai 互不导入，ideation 不导入 bookengine；禁用词检查保留。
`check_scope.py` 继续输出各包和前端总行数、最大单文件行数及依赖、禁用词检查结果。
共享文件 `engine/tools/runtime_support.py` 与 `engine/tools/chapter_outputs.py`（完成检查点和终稿协议）也纳入 Python 单文件、依赖和禁用词检查，不设总量上限；不得反向导入 bookengine 或 deai。

```bash
cd engine
python tools/check_scope.py                                 # 行数统计、单文件/前端行宽限制与独立性
PYTHONPATH=.:tools python -m pytest -q --no-cov tests/        # 全部测试
```
