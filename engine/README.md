# bookengine v1

生产路径为选题 → 故事圣经 → 排期 → 正文 → 检查 → 可选去 AI → 终稿与导出。只有 Q1 / B 写法；旧 runner、long、quality 管线和 legacy 文风入口已删除。启动 8787 和网页开书流程见仓库 [README.md](../README.md)。

## 命令行

Python 3.10+，依赖 PyYAML、python-docx。以下从仓库根目录执行，密钥只从 `.env` 读取：

```sh
# 新书；重复同一命令从检查点恢复
PYTHONPATH=engine python -m bookengine engine/examples/realistic-life.yaml --q1 --style b --env .env --out books/my-book
# 显式增加原书目标章数，不重写已完成章节；仍用原设定文件
PYTHONPATH=engine python -m bookengine engine/examples/realistic-life.yaml --q1 --style b --extend-to 50 --env .env --out books/my-book
# 批量：manifest 中 books 是 id/path 列表，path 相对 manifest 解析
PYTHONPATH=engine python -m bookengine --q1 --style b --batch engine/examples/q1/batch.yaml --jobs 2 --env .env --out books/my-batch
# 独立去 AI；也支持单个 Markdown、--chapters 1-10、--model MiniMax-M3
PYTHONPATH=engine python -m deai run books/my-book --env .env
# 使用书库自动去 AI 同款段落审改
PYTHONPATH=engine python -m deai run books/my-book --env .env --editor story-deslop
# 与写手同时运行，只读已完成检查点，最多并发三章
PYTHONPATH=engine python -m deai run books/my-book --watch --pipeline --concurrency 3 --env .env
# CLI 原文写作与独立去 AI 分别运行；关闭去 AI 时网页会直接发布原稿终稿
# 默认只导出 final/ 的终稿；缺章列入说明，不从草稿补齐
PYTHONPATH=engine python -m exporter books/my-book
```

`--batch` 支持 1–6 个并发子进程，书内顺序、书间并发，一本失败不影响其他本；续跑保留旧账、旧正文和检查点。`--q1` 必填，`--style` 仅接受 `b`。

选题工坊独立运行：

```sh
PYTHONPATH=engine python -m ideation --env .env --books books --out books/topics/my-batch --genre suspense-mystery --chapters 100 --channel 男频
```

同一输出目录恢复已完成步骤，换批使用新目录；最近五批参与去重。榜单获取失败时使用缓存或明确标无数据，判官仅在当日同题材锚点校准通过后参与排序。盲选工具 `engine/tools/ideation_acceptance.py` 须显式指定 `--env`、`--out`、`--blind`；原始调用、响应与答案保存在忽略的书目录，不进 Git。

## 文件与恢复

- `story_bible.yaml`、`threads.yaml`：故事圣经与多线排期；已有设计只读，显式续写另存 continuation。
- `story_design.yaml`、`story_ledger.json`、`design-gate.json`：新开的书在第 1 章前生成一次故事设计（伏笔埋设/提醒/兑现章、真相揭开章、人物往事与习惯、关系台阶、首次出场章）；账本记录每条义务是否写进正文并附原句；此前已准备或已开写的书没有这一层，照旧运行。
- `steps/`、`responses/`、`calls.json`：阶段检查点、原始响应、写作用量，已完成步骤复用，未知用量保持未知。
- `chapter-NNN.body.md`、`chapters/NNN.json`、`state.json`、`run.json`：正文、完章记录、派生状态和整书进度；正文与完成检查点落盘后，watcher 才处理。
- `final/chapter-NNN.md` 和 `.deai.json`：有效终稿及去 AI 检查记录；`final/book.md`、`SUMMARY.md` 提供整书结果。已有终稿不重复处理或覆盖。
- `runtime.json` 与 `.pipeline/polish/runtime.json`：写作、去 AI 各自的等待/错误状态；`web.json` 保存网页队列及流水线状态。

Q1/B 起草允许人物说出读者还不知道的顾虑，动作或对白已表明的意思可省去旁白复述。人物、地点、前情和章纲仍随写作请求传入。

书库勾选自动去 AI 时使用段落编辑器 `story-deslop`。每章先扫描原文。对重复解释，扫描须指出目标段和另一处已经表达同一意思的原句。局部表达问题可定点改写一次，再核对人物、事实、时间和伏笔。结构问题和上下文不足的段落保留原文，建议记入报告。扫描无可改项、回执无效或复检失败时保留原文。关闭自动去 AI 可直接生成原稿终稿。独立 CLI 保留原有句级编辑入口。

每章 `final/chapter-NNN.deai.json` 保存引文、去向、改动及复检。全书 `final/SUMMARY.md` 分别统计提案、实质改写、仅标点或空白的段数。已经完成的流水线终稿在续跑时复用。读者盲评可在书库外离线进行：

```sh
PYTHONPATH=engine python -m tools.deai_reader_trial prepare samples.json trial --repeat scene1 raw
PYTHONPATH=engine python -m tools.deai_reader_trial score trial/组织者/盲号.json trial/反馈.txt --pair raw edit
```

`samples.json` 提供 `samples` 数组，每项有 `group`、`arm` 和 `text`。将读本和反馈表交给读者，填写后再对照盲号。完全相同的重复文本可测同一读者两次评分的波动。

去 AI 保护数字、实体、事实和章末钩子，字数维持在原稿 ±10% 内。单章复检或模型坏返回耗尽重试时保留原稿继续下一章，额度、接口和代码错误按对应状态处理。程序检测结果仅供读者验收时参考。

选题、写作、去 AI 均无金额预算，费用照常记账和显示。书一旦开写便持续写完；接口等待持续退避（最长 10 分钟、无总时限），额度用尽充值后继续，项目错误修复后继续。用户暂停与并发排队保留。服务重启分别恢复两条进程，旧书缺 watcher 时补齐；千章级任务开跑前仍须告知用户。

## 模块与检查

| 路径 | 职责 |
|---|---|
| `bookengine/q1.py`、`q1_chapter.py`、`q1_extract.py` | 整书调度、单章生成与修补、上下文及事实提取 |
| `bookengine/q1_plan.py`、`q1_prepare.py`、`q1_schema.py` | 按需卷章规划、不可覆盖的故事设计、结构归一化 |
| `bookengine/q1_quality.py`、`contracts.py` 等 | 人物/时间/状态一致性、兑现与成长的程序检查 |
| `bookengine/design_gate.py`、`story_design.py`、`story_duties.py`、`story_report.py` | 开写前的设计完整性检查；伏笔、真相、人物内里、关系台阶的带章号排期；每章义务与写后账本；体检里的故事层统计 |
| `deai/` | 独立检测、局部修复、复检与回滚，不导入 bookengine |
| `ideation/` | 市场快照、抽轴发散、去重筛选、选题卡与盲选 |
| `exporter/` | MD、DOCX、TXT、EPUB、番茄分章 ZIP，仅导出终稿 |
| `tools/webui/` | Python 后端与独立 JS/HTML/CSS，管理队列、阅读与导出 |
| `tools/runtime_support.py`、`tools/chapter_outputs.py` | 共享重试/运行状态及完章/终稿协议，不反向导入写手或去 AI |
| `tools/reconcile_q1_state.py` | 从已保存提取重放派生状态，默认 dry-run |

在 `engine/` 执行：

```sh
python tools/check_scope.py
PYTHONPATH=.:tools python -m pytest -q --no-cov tests/
python -m ruff format --check .
```

各包总量不限；单个 Python 文件 ≤500 行，前端单文件 ≤800 行、每行 ≤160 字符。Python 格式化行宽 120；历史 reports 原样保留，不参与格式化。scope 检查依赖隔离和禁用词，禁止 import `bestseller`，bookengine/deai 互不导入，ideation 不导入 bookengine。

待办与人审状态见 [INBOX.md](INBOX.md)，已结束指令原文见 [指令归档](reports/inbox-archive-2026-11.md)，历史阶段设计见 [PLAN.md](PLAN.md)；PLAN 中已被后续指令取代的约束仅作历史记录。每轮报告区分 Evidence / Inference / Unknown。
