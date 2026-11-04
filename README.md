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
2. 分别设置写作预算、去 AI 预算（默认 $0.50）和并发数，选择是否开启“去 AI 润色”，然后点“开始写作”。两份预算独立，均受全局总额限制。
3. **书库**：查看每本书的进度、花费、状态；可以暂停、继续、从检查点重试。
4. **阅读**：按章节目录阅读，能对比草稿和终稿；可以复制本章或全书，下载 MD / DOCX / TXT / EPUB / 番茄分章 ZIP；也能查看故事圣经、章纲和检测报告。最终稿全章完成后才能整书导出；预算暂停会保留原检查点。

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
PYTHONPATH=. python -m deai run "$BOOK_DIR" --env ../.env --budget 0.50
# 限定章节可加 --chapters 1-2；同一输入与配置的已完成章节会跳过，不重复付费
# --budget 默认 $0.50，累计 final/ 下已知及未知用量上界；与写作费用分开，续跑不清零。
# 多目录共享限额可用 --budget-root 指定包含所有后处理账目的目录。
# 导出：txt / md / docx / epub，以及番茄上传用的分章 txt
PYTHONPATH=. python -m exporter "$BOOK_DIR"
# 导出全部通过去 AI 检查的终稿；任何一章缺失即拒绝，不以草稿补齐
PYTHONPATH=. python -m exporter "$BOOK_DIR" --version polished
# 不用 Docker 起网页
PYTHONPATH=. python -m tools.webui --env ../.env --books ../books --port 8787
```

同一条命令重跑会复用检查点，不会重写已经完成的章节。Docker 已运行时，先停服务再选择宿主网页命令，避免两个服务操作同一写作队列；查看命令参数可用 `--help`。CLI 导出的默认 `final` 指写作定稿，`polished` 才是独立去 AI 的 `final/` 版本。

## 目录

| 路径 | 内容 |
|---|---|
| `engine/bookengine/` | 写书主流程：选题 → 圣经 → 排期 → 正文 → 检查（核心代码 ≤5000 行） |
| `engine/deai/` | 独立的去 AI 味流程：检测、局部修复、复检、回滚（≤800 行） |
| `engine/exporter/` | 导出（≤400 行） |
| `engine/tools/webui*` | 本地网页（≤1200 行） |
| `config/` | 题材包 `prompt_packs/`、场景落地、文风锚点、番茄市场画像，引擎只读 |
| `engine/INBOX.md` / `engine/PLAN.md` | 开发指令收件箱与阶段计划 |
| `engine/reports/` | 各轮实测报告 |
| `books/` | 书稿与检查点（不进 git） |

## 检查

```bash
cd engine
python tools/check_scope.py                                   # 行数上限与独立性
PYTHONPATH=.:tools python -m pytest -q --no-cov tests/        # 全部测试
```
