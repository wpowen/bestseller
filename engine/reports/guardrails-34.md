# 第 34 条：工程护栏交付

状态：开发完成，待监工验收。工作树 `scratch/g34-worktree`，分支 `feat/guardrails`。

## Evidence

| 项目 | 已交付与证据 |
|---|---|
| 真实书库回归 | `tests/fixtures/real_library/`：真实四本书，97 个文件、665,904 bytes（<3MB），保留首三章、末章及代表性去 AI 状态章；无 responses/http。字段白名单及 `.env` 过滤测试见 `test_webui_real_snapshot.py`。 |
| 默认回归 | `tools/real_library_smoke.py` 已接入默认 pytest，真实 HTTP 随机端口、临时副本：4 本、107 请求、20 章读取、20 次五格式导出，全部成功；最近独立运行 `/api/books` 最慢 **62.11ms <500ms**，模型子进程 0，原快照未变。 |
| 写保护 | `tools/books_guard.py` 识别当前工作树及 Git common-dir 所属主仓库 books；webui 测试启动拒绝这些路径及子目录/别名。conftest 审计写入、删除、移动、链接等，并封住 `os.open(..., dir_fd=...)`。隔离进程回归见 `test_webui_books_guard.py`。 |
| 真实书库保全 | 开始清单 `/tmp/g34-books-before.json`；完成时 `verify` 返回 **added=[] / deleted=[] / changed=[]**，退出码 0。清单和书库未进 Git。 |
| 删除与追溯 | 移至 `.trash/<UTC时间>--<原id>`；7 天内可恢复，冲突不覆盖；下次服务启动清理超期目录。保留费用、跳过回收站扫描；ops 每行记录 source/user_agent/client，记录写盘前脱敏。归档、恢复、费用、超期、符号链接及 watcher 原有回归均通过。 |
| 脱敏 | `tools/redact.py` 支持任意文本过滤和报告写盘。网页写手/去 AI/选题输出经 PIPE 过滤再写日志；pytest 失败、捕获输出及 `--log-file` 均过滤。假 `.env` 覆盖拆分输出、stderr、报告、pytest 文件日志。 |
| 外部评审核对 | `tools/verify_review.py` 按章核对终稿/正文/目录，支持中文章数、章号列表、前置/后置引用及章名；输出核实/找不到/无引文。实际核对使用《一掌镇乾坤》权限只读的完整书稿副本，结果见 [核对表](guardrails-34-review.md)。 |
| 体检展示 | 详情只读 `book_checkup.json`，显示阶段/地点、对手提及跨度、章名重复、衔接/格式/登场/称呼。标明参考、证据/推断/未知；缺失文件显示未知。未修改或导入第 33 条的生产代码。 |

真实快照捕获时四本均已完稿，均有流水线信息，无重命名。无终稿、旧版独立去 AI、重命名等状态明确只在临时副本构造。
精简快照保留原计划章数，未保留章节在网页中仍是缺章，不把精简后的进度冒充真实书库进度。

### 验证

按指定解释器执行：

```sh
cd engine
PYTHONPATH=.:tools /Volumes/MACSSD/owen-home/Documents/workspace/bestseller/.venv/bin/python -m pytest -q --no-cov tests/
```

最终结果：**773 passed, 1 skipped, 761 subtests passed in 35.88s**。
唯一跳过项为现有 Linux `/proc` watcher 身份测试（本机 macOS）。`ruff format --check .`：147 files already formatted。
`check_scope.py`：PASS；Python 最大单文件 491 行；前端最大单文件 491 行；无跨包导入和禁用词。`git diff --check` 通过。

Playwright 在系统临时副本、随机端口 50275 上逐一打开 20 章，逐章比较真实渲染正文，无“尚未生成/正在去 AI”占位；
实际点击删除确认 → 回收站 → 恢复，书库恢复四本、回收站归零，控制台 0 errors / 0 warnings。
已查看书库、体检、回收站渲染截图，未见裁切或布局缺陷。体检页使用明确标为测试用的 schema v1 数据验证展示，非真实质量结论。
截图保留在 `/tmp/g34-browser/output/playwright/`，浏览器及临时 HTTP 实例均已关闭。

### 分项提交

- 写保护：`7e3126537`；相对描述符写入补测/修复：`a22bda59b`。
- 脱敏：`bdc4914d2`；pytest 文件日志补测/修复：`5e548076f`。
- 回收站及追溯：`d88c9fd30`。
- 评审核对：`cec99ab0e`。
- 真实快照回归：`2485e4b45`。
- 体检消费端：`92ee82845`。

各项先跑缺失行为回归再实现；补充发现的两个绕过出口也分别确认失败后修复并提交。
没有改 `engine/bookengine/`、`engine/deai/`、第 33 条内容、AGENTS 或 docker-compose；没有模型调用、部署、push 或触碰 8787。

## Inference

现有证据支持本机离线工程护栏和浏览器消费端行为；HTTP 延迟仅代表宿主机临时副本，不能外推为 Docker 绑定挂载性能。
对手“提及”不等于实际出场，体检推断不等于读者验收。pytest 文件保护覆盖本进程的 Python 文件 API，并有 webui 子进程启动检查，不能替代任意外部程序的系统级只读挂载。

## Unknown / 待监工核对

- 第 33 条真实模型新书尚未在本分支合并验收；体检生成端与消费端最终联调、生产部署和读者验收未执行。
- 原要求“复现两条找不到”按**问题类别**已复现：目录多出 `1`（评审同时引用第 7、50 章，两处均无），第 9 章《拿稳你的锤》（无）。
- 严格逐字核对另发现第 4 章引文不精确：评审是“背着母亲退入北废矿道”，终稿和正文实际是“背着母亲退入北侧的废矿道”。工具如实标为找不到，未做模糊匹配凑数。
  因此实际输出为 **4 处引文 / 3 条评审** 找不到，而不是把额外转述认作已核实。是否把这处归类为“事件属实但引文不精确”，由监工确认；未改评审原文或原稿。
