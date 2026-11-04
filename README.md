# BestSeller · bookengine

从一句话（或空白）到一整本可读、可复制、可导出的中文网文。

当前主线是 `engine/` 里的 **bookengine**：标准库为主的 Python 包，没有数据库，用文件检查点断点续写，模型用 MiniMax M3.1 Flash。
老框架（PostgreSQL + API + worker + Web Studio，约 123 万行 Python）已于 2026-11-04 归档：

```bash
git checkout legacy-final-20261104   # 或 git checkout legacy
```

## 快速开始（网页，端口 8787）

```bash
cp .env.example .env        # 填 MINIMAX_API_KEY
docker compose up -d --build
open http://127.0.0.1:8787
```

在页面上依次操作：

1. **开书**：选题材、填章数（5–2000），一句话想法可以空着；点“生成选题”，从 12 个设定里点选一个。
2. 设置单书预算和并发数，选择是否开启“去 AI 润色”，然后点“开始写作”。
3. **书库**：查看每本书的进度、花费、状态；可以暂停、继续、从检查点重试。
4. **阅读**：按章节目录阅读，能对比草稿和终稿；可以复制本章或全书，下载 MD / DOCX / TXT；也能查看故事圣经、章纲和检测报告。

书稿都存在 `./books/`，每本书一个目录，删除容器不影响书稿。
`scratch/engine-books` 保留为指向 `../books` 的兼容链接；网页服务始终使用真实的 `books/` 目录。
不开服务时，可以直接双击 `books/webui/LIBRARY.html` 离线阅读。

## 命令行

在 `engine/` 目录执行，Python 环境需装好 `PyYAML` 和 `python-docx`：

```bash
# 写一本书：书的设定写在一个 YAML 里，字段为 prompt_pack / chapters / words_per_chapter / user_input
PYTHONPATH=. python -m bookengine examples/realistic-life.yaml --q1 --style b --env ../.env --out ../books/my-book
# 去 AI 味：只改命中的句子，保真检查不过就回滚；结果写到 final/
PYTHONPATH=. python -m deai run ../books/my-book --env ../.env
# 导出：txt / md / docx / epub，以及番茄上传用的分章 txt
PYTHONPATH=. python -m exporter ../books/my-book
# 不用 Docker 起网页
PYTHONPATH=. python -m tools.webui --env ../.env --books ../books --port 8787
```

同一条命令重跑会复用检查点，不会重写已经完成的章节。

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
