# INBOX 26：写到完本（2026-11-05，开发第 1–4 步）

结论：开发完成，待监工验收。只在 `scratch/run-worktree` / `feat/run-to-finish` 开发并本地提交；第 5 步真实跑书未执行。

## Evidence

- 起始工作树干净，基线提交 `76af867ed0c9`；开发前基线为 580 tests、844 subtests 通过。按当前 AGENTS 和 INBOX 26 覆盖旧金额/包总行数约定，没有恢复 legacy 代码。
- 写作、去 AI、选题、网页均移除金额停止逻辑和金额参数；保留调用、tokens、费用估算及 Unknown 用量。网页新书和 API 不再提供 `budget` / `deai_budget` / 总金额上限等字段；旧 `budget_paused` 只按可继续的暂停显示。旧金额环境变量不再阻断调用。
- 各包和前端总行数不限；Python 单文件 500、前端单文件 800、前端每行 160、禁词和依赖隔离仍由 scope 检查。当前 README 已同步；历史 PLAN / 报告保持原始记录。本仓库当前无独立有效 DESIGN.md，历史设计报告未回改。
- 共享错误和状态模块为 `tools/runtime_support.py`，不反向导入 bookengine/deai；现有 `COPY engine/tools/` 能包含它，未改 Dockerfile。`.env.example` 和 compose 只移除指定的网页金额变量，没有执行任何 Docker 操作。
- 429/408/5xx、超时、连接错误和明确限流业务码一直退避；每次最多 600 秒，没有累计等待截止时间。等待状态有阶段、次数和下次重试时间。账户额度单独报告，充值后继续原检查点；六次坏返回仍失败报告项目错误。
- 完成的步骤直接复用；旧坏步骤保留原字节、另存修复步骤；Q1 前章补提写入 `chapters/NNN.recovery.json`，不会回写原章节检查点或正文。恢复前检查 PID 和真实 flock，活进程/仍持有的锁不删除、不重复启动。
- 服务重建扫描运行/排队/润色任务，死 PID 清旧锁并排队继续；保留自动去 AI 选择，正文完成后移交去 AI。账户/项目错误不因重启而自动重试。
- 不合格章节保留检查记录继续；去 AI 保真/字数/风格回滚保留原文及报告，标为 retained/partial，处理可完成但不伪称通过润色验收，终稿导出仍检查所有章节。
- 开发测试均为假客户端/假 HTTP；真实本地子进程测试只执行临时假 CLI。未调用真实模型，未读生产密钥，未向主仓库 books 写入、删除或复制书稿；`gemini.md` 测试通过现有只读 git common-dir 回退引用主仓库原文件。未启动、重建或操作 8787。

### 接口错误码依据

只读普查主仓库共 482 个 `calls.json`：62 次 HTTP 429、74 次 HTTP 422、16 次 gaierror、3 次 invalid_stage_output、1 条 interrupted、1 条 pending；现存 calls/responses 中没有检出可确认的账户业务码。仅汇总状态，不输出正文、请求、密钥，也不修改账目。

以下业务码分类依据是当次读取的 [MiniMax 官方错误码](https://platform.minimax.cn/docs/api-reference/errorcode)，不是声称本地发生过这些账户错误：

| 业务码 | 分类和处理 |
|---|---|
| 1008、2053、2056、2067 | 余额/积分/套餐资源不足，`quota_exhausted`；保留记录，等待充值或资源恢复后点继续 |
| 1000、1001、1002、1024、1033、1041、1045、2045、2046、2047、2062、2064、2066、2160 | 服务、超时、频率、连接、并发等等待，持续退避 |
| 1039 | 请求 token 参数限制；官方要求调整 max_tokens，属于项目参数错误，不能误报账户额度用尽 |
| 1004、2013、2049 等其他非暂时错误 | 配置、参数、鉴权等项目错误；不无限重发 |

2056 是可随时间恢复的套餐资源限制；本轮按“额度用尽”展示，用户资源恢复后继续。HTTP 408/429/5xx 优先作为接口等待，明确账户不足的响应除外；HTTP 200 的业务错误同样检查。

### 停止原因普查表

覆盖生产入口 `bookengine` CLI（Q1、long、短篇、batch）、`deai run`、`ideation`、web 选题 worker/写作队列、导出；未把历史实验脚本当作生产入口。

| 路径/原门禁或轮次 | 归类 | 当前处理 |
|---|---|---|
| check_scope 各包/前端总行数上限 | 不属于运行停止 | 删除总量上限；按职责拆分，保留单文件/依赖/禁词检查 |
| q1.QClient 金额上限 | 不属于三类 | 删除；调用和费用继续累计 |
| deai MiniMax/CLI 的金额预留、预算根目录 | 不属于三类 | 删除金额限制和 CLI 参数；保留 ledger 和未知用量 |
| ideation 每批 $0.05 | 不属于三类 | 删除参数；保留市场缓存、历史去重和费用 |
| web 预约金额、全局额度条、SIGSTOP 预算冻结 | 不属于三类 | 删除自动冻结；高成本和 Unknown 不阻断写作 |
| 原 budget_paused / run.status=stopped | 旧物化停止状态 | 网页可继续；写作不再因旧止损状态提前返回 |
| bookengine/deai 网络累计 900 秒截止 | 接口等待 | 删除总截止；每次退避 10/60/300/600 秒，最高 600 秒 |
| HTTP/业务限流、超时、断连 | 接口等待 | 不消耗格式失败次数，持续等待，状态可见 |
| 余额/积分/套餐额度耗尽 | 额度用尽 | 原检查点和原账目保留，不自动重发；继续原任务 |
| 空返回、截断、JSON/关键结构失败，旧 1–3 次 | 项目出错 | 生产阶段至少六次，每次附前次原因；耗尽后含阶段和摘要 |
| Q1 人物卡/关键圣经结构无效 | 项目出错 | 在保存完成步骤前验证；六次失败不创建假圣经 |
| benchmark 12 设定关键结构缺失 | 项目出错 | 在 stage 验证，至少六次；不保存不完整完成步骤 |
| 长篇滚动 50 章均耗/失败率、Q1 同类止损 | 不属于三类 | 改为 warnings 后继续，耗时/失败率不归零 |
| 章节字数、对白、伏笔、成长、硬矛盾/质量指标未过 | 不属于整书停止 | 保留有限局部修订和检测记录，继续下一章；网页逐章可见 |
| deai 局部修复次数、复检、保真/字数/新风格回滚 | 不属于整书停止 | 有限修复后回滚/保留，继续下一章；不把回滚标为通过 |
| ideation 判官六次格式耗尽 | 不属于整书停止 | 参考评分 Unknown 后继续；普通项目/额度错误不吞掉 |
| ideation 补充一次仍不足 12、榜单不可用 | 不属于三类 | 记录 shortage/无市场数据并继续展示；不以分数代替选择 |
| ideation Unknown 请求恢复 | 可恢复过程 | 原账目保留，持久 attempts 区分后续请求，附原因，不清账、不假装已计费用量 |
| YAML/模型/模式变化、路径越界、无密钥、鉴权/参数、文件损坏或程序异常 | 项目出错 | 保留保护检查和阶段摘要；不自动变成空正文或成功 |
| 故事状态时间倒退、无法压缩的保护状态等 | 项目错误或可降级质量提示 | 可降级状态保旧并记录；不可恢复结构/配置错误报告项目错误 |
| 活 PID、真实持锁、共享书只读/重复任务 | 项目所有权冲突 | 不删活锁、不重复写；保留 orphaned 诊断供原服务处理 |
| 队列并发满、限速器排队 | 调度等待 | 自动等空位；降低并发后的原进程自动继续，不按费用冻结 |
| 用户手动暂停、SIGINT/服务关闭 | 明确用户/生命周期控制 | 保留控制能力，不当质量/预算失败；服务恢复续接活跃检查点 |
| 完成正文后自动去 AI | 阶段转换 | 持久 auto_deai；重启保留手动去 AI 所在阶段 |
| 未完成/未通过终稿的整书导出 | 导出项目条件 | 拒绝伪装最终稿，保留所有草稿和检查记录，不阻止正文继续 |

### 测试清单

| 项目 | 假客户端/本地证据 |
|---|---|
| 429 连续 20 次后成功 | `test_run_to_finish.py`：真实 Client + Q1 两章流程写完，生成 MD/DOCX；另有 deai 同类测试 |
| HTTP 200 连续业务限流、HTTP 错误体分类 | `test_run_to_finish.py`、`test_run_finish_deai.py`：限流按等待、账户错误独立、1039 不误报额度 |
| 额度不足 → 继续 → 写完 | 实际 Q1 临时目录测试第一章写完、第二章额度错误、续跑完本；第一章正文及章节检查点原字节不变；web action/tick 状态闭环 |
| 坏返回 5 次后成功 | writing/deai/ideation；覆盖空/截断/无法解析和关键结构，携前次原因；六次仍失败为项目错误 |
| kill 真实进程、重建服务、自动续写 | `test_run_finish_web.py` 真本地假 CLI 持 flock、保存第一章、SIGKILL；新 Queue 清旧锁续第二章，原正文与状态字节不变 |
| 恢复 queued/running/polishing | 死 PID 恢复、活 PID/持锁保护、旧 runtime 清理、额度不自动重试、spawn 错误不无限重启 |
| 正文完成后自动去 AI | 开书保存设置、重启保留、队列阶段移交；手动后处理也保留阶段 |
| 金额字段和阻断消失 | config/API/create/CLI/env/前端契约；旧预算字段忽略、旧暂停兼容，Unknown 账目不丢且继续运行 |
| 旧完成步骤不覆盖 | 写作/deai/ideation 原步骤与章节比字节；旧坏步骤另建修复步骤；成功 HTTP 断点复用零新请求 |
| 质量未过继续 | Q1 字数不合格仍完本；deai 回滚续处理、记录 retained/partial，检测报告可见，保留导出真实性检查 |
| scope 边界 | 超旧包上限仍 PASS；单文件/行宽/跨包/动态 import/禁词和 shared 反向依赖仍 FAIL |
| 界面读回 | 隔离 `127.0.0.1:18791`，临时合成书和选题、禁用 worker/模型；真实浏览器已检查费用/三类状态和无预算写作设置，控制台 0 错误/0 警告 |

浏览器截图：[书库状态与费用](run-to-finish-20261105/library.png)、[无预算写作设置](run-to-finish-20261105/settings.png)。截图为离线 fixture，不是生产书或真实成书证明。

最终检查结果见本报告末尾验证读回。

## Inference

这些回归共同支持开发契约：费用和质量参考值不会自动卡住整书；接口暂时失败可以一直等，进程终止后可从持久检查点恢复。假模型、临时文件和假 CLI 的成功不等于真实 MiniMax、生产部署、真实书籍质量或读者验收通过。

## Unknown

第 5 步的三本真实任务、真实账号额度错误、真实长时间等待、生产容器恢复以及 50 章全流程仍未验证，必须等监工验收、合并和重新部署后执行。未运行千章，未合并，未 push。没有为本次开发更改主仓库 books 或重置任何生产账目。

## 最终验证读回

- 用户指定的全套测试命令（在工作树根目录）：

  ```sh
  cd engine && PYTHONPATH=.:tools /Volumes/MACSSD/owen-home/Documents/workspace/bestseller/.venv/bin/python -m pytest -q --no-cov tests/
  ```

  结果：`754 passed, 850 subtests passed in 19.18s`，没有失败或跳过。
- 改实现前写作客户端的 13 项新增行为测试失败，缓存保护新增用例也先失败；实现后定向检查和最终全套均通过。旧的预算/速度止损测试已迁移到新契约，并保留账目和章节不变断言。
- `git diff --check`：PASS。仅有任务范围内的源代码、测试、文档和离线截图；未改 AGENTS、PLAN、历史报告、Dockerfile 或任何主仓库书稿。
- `tools/check_scope.py`：

```text
bookengine_lines=5032
deai_lines=1553
webui_lines=817
exporter_lines=253
ideation_lines=510
shared_lines=79
webui_frontend_lines=1492 (max_file=456 wizard.js)
max_file_lines=466 (deai/flow.py)
imports_bestseller=no
cross_imports=none
forbidden_keywords=none
PASS: file limits and independence
```

- Playwright 桌面实际渲染检查完成；页面控制台 `0 Errors / 0 Warnings`。浏览器会话与隔离 fixture 服务均已关闭。
- 原有 gemini 测试沿用只读主仓库引用；没有拷贝书稿到工作树。第 5 步保留给验收、合并、重新部署之后。

## 第 27 条

结论：开发完成，待监工验收。接在第 26 条提交 `32a61a48f` 之后，同一工作树和分支开发；本轮起点为已合并 main 的 `6ff1550d5`。未执行第 26 条第 5 步真实跑书。

### Evidence（证据）

- 写作与去 AI 通过公开 CLI 同时启动，调度逻辑分到 `tools/webui/pipeline.py`。一个并发名额对应一本书；降低并发、手动暂停与继续会控制同书两个进程，保留原 PID，不重复启动。
- 去 AI 使用 `--watch --pipeline`，约每 2 秒扫描一次。只在 `chapters/NNN.json` 的 `status=complete` 后读取正文，不按文件出现或时间猜测。完成检查点在 Q1 正文原子落盘之后写出。共享文件协议 `tools/chapter_outputs.py` 不导入 bookengine 或 deai，已加入 scope 的 shared 检查。
- 写作仍使用书根 `runtime.json`；去 AI 使用 `.pipeline/polish/runtime.json`，PID 和阶段结果分别保存在网页元数据中。等待、额度用尽和项目错误沿用第 26 条处理；一阶段失败不停止另一阶段。网页展示实际错误阶段，写手继续运行时仍显示正文进度。重启时分别恢复健康阶段，已确认的额度/项目错误不会自行重发；活 PID / 活锁仍拒绝重复启动或接管。
- 已有合格终稿在扫描和章锁内都检查并跳过，不因规则或配置变化重新处理、覆盖。新章去 AI 合格采用修改稿；unchanged 保留原文；retained/partial 回滚整章原文作为终稿。`processing_complete` 与润色验收 `complete` 分开，回滚仍保留失败/检查记录，不伪称润色通过。
- 自动去 AI 在表单和新书 API 默认打开，显式关闭仍保留选择，并以 `--original` 将已完成原文发布成终稿，模型客户端不构造、不调用。旧正文已完成书点“继续”，只启动补终稿阶段；已存在终稿不再生成。
- 书库和详情的章数、字数以终稿统计，另列正文完成章数。默认阅读器不回退草稿；未出终稿显示“本章正在去 AI，完成后显示”。草稿仍在磁盘和 API 的次要对照数据中，顶栏版本按钮已移到阅读设置的“查看修改对照”。离线书库也不回退草稿。
- 网页五种下载均读取终稿；旧 `version=draft` 下载参数也只返回终稿。CLI 默认 `final` 和 `polished` 均读取 `final/`，显式 draft 仅保留作开发诊断。缺章不混入草稿：页面、下载响应 `X-Missing-Chapters`、导出文件的“导出说明”均列出缺章；零终稿拒绝导出。部分导出补齐后，派生导出目录的旧章节/缺章说明会清理；该清理只在 export_book 的导出目录启用，不用于写作原稿导出适配器。
- 删除采用**拒绝运行中删除**方案：书卡和详情均有直接入口，一次确认，不要求 title。拒绝仍有任一写作/去 AI 进程的书（包括暂停或失联进程）；仅限网页直属书与其归档，路径和符号链接保护、操作日志与费用累计保留。
- 弹窗、菜单、阅读设置和手机目录统一处理外部点击、Esc 和互斥；手机遮罩点击不会因再次 toggle 而重开目录。

### 假模型与进程验证

开发前全套基线：`754 passed, 850 subtests passed in 14.33s`。新增首批 7 项行为回归先得到 **7 failed**，失败原因分别对应默认开关、双进程、完成章过滤、终稿统计、回滚终稿、删除和部分导出；实现后转绿。旧草稿导出/全章齐全才允许下载/输入书名删除契约已迁移到本条新契约，路径、日志、费用与运行进程保护仍保留。

| 场景 | 当前证据 |
|---|---|
| 真 Q1 写手进入第 3 章时，前两章已有终稿 | `test_real_q1_writer_reaches_third_chapter_with_first_two_finals`：真实 Q1 检查点与 watcher/IO，写作 HTTP 和去 AI 客户端均为假；第三章正文请求前断言 1、2 终稿已在、3 尚未在；全书 3 终稿齐全 |
| 未完成章不读 | `test_pipeline_watcher_never_reads_uncommitted_body_and_finishes`：第三章 body 已存在但检查点未完成，前两章处理后断言第三章无终稿；补完成标记后才处理 |
| 双进程杀掉、重建队列恢复 | `test_real_two_process_kill_restart_reuses_finals_without_new_calls`：真实本地两个子进程，去 AI 走真实 CLI/IO 和假客户端；两个 PID 均 SIGKILL，重建 Queue 后两阶段续跑；假客户端构造记录恰为 001、002、003 各一次，旧终稿和第一章检查点逐字节不变 |
| 去 AI 额度错误不拖停写手 | 假进程回归：去 AI 退出，写手原 PID 继续；继续只重启失败阶段；重建服务只恢复健康写手，额度失败阶段不自动重发 |
| 并发与手动暂停 | 两书四进程降为一书时暂停同书两个 PID，空位后恢复原两个 PID；手动暂停/继续同样控制两个阶段 |
| 每章都有终稿与关闭自动开关 | watcher 完成本书后自行退出，齐章才标 complete；original 模式精确保留 CRLF 原文且不构造模型客户端 |
| 删除 | 无 title 的 HTTP 删除成功、日志保留；运行书和仍存活的去 AI 进程拒绝；共享、嵌套、越界、符号链接与费用保护继续通过 |
| 五种导出 | MD/TXT/DOCX/EPUB/番茄 ZIP 只含终稿；缺章列表和说明；部分→完整导出清掉旧说明；显式初稿诊断仍独立 |

最终用户指定命令：

```sh
cd engine && PYTHONPATH=.:tools /Volumes/MACSSD/owen-home/Documents/workspace/bestseller/.venv/bin/python -m pytest -q --no-cov tests/
```

最终结果：`771 passed, 846 subtests passed in 40.05s`（无失败或跳过）。

`git diff --check`：PASS。`tools/check_scope.py`：

```text
bookengine_lines=5032
deai_lines=1569
webui_lines=1036
exporter_lines=270
ideation_lines=510
shared_lines=111
webui_frontend_lines=1533 (max_file=456 wizard.js)
max_file_lines=466 (deai/flow.py)
imports_bestseller=no
cross_imports=none
forbidden_keywords=none
PASS: file limits and independence
```

### 浏览器实测

临时合成书目录 `/var/folders/21/t2f_q3md2ln9l36r_s8j86mw0000gn/T/inbox27-web-cz5ma53e/books`，独立 WebUI `127.0.0.1:18793`，空凭据文件；没有启动写作、选题或模型请求。使用 Playwright 实测，桌面与 390×844 手机布局均已渲染并检查截图：

- 菜单：外部点击、Esc、切换另一菜单只保留一个；[菜单](run-to-finish-20261105/shots/menu-open.png)。
- 删除：卡片直接入口、无输入框、外部点击/Esc/取消；一次确认后临时测试书从书库消失；详情删除入口同样可用。[确认框](run-to-finish-20261105/shots/delete-confirm.png)、[删除后书库](run-to-finish-20261105/shots/library-final-progress.png)。
- 重命名：外部点击和 Esc 均关闭，没有误提交。
- 阅读：默认终稿、待终稿不显示草稿；对照仅在阅读设置内显式开启。[终稿阅读](run-to-finish-20261105/shots/final-reader.png)、[等待终稿](run-to-finish-20261105/shots/reader-pending-final.png)、[阅读设置](run-to-finish-20261105/shots/reader-settings.png)。
- 手机目录：遮罩和 Esc 关闭；目录与阅读设置相互排斥。[目录抽屉](run-to-finish-20261105/shots/mobile-directory.png)、[关闭后的阅读页](run-to-finish-20261105/shots/mobile-final-reader.png)。
- 五种实际 HTTP 下载均 200，缺章头为 `2,3`；导出页没有草稿选择器。[终稿导出](run-to-finish-20261105/shots/final-only-export.png)。
- 确认自动去 AI 复选框默认勾选；控制台 `0 Errors / 0 Warnings`。浏览器会话和临时服务已关闭，18793 无监听。

### Inference（推断）与 Unknown（未知）

这些离线模型、真实本地进程和浏览器证据支持本条开发契约。尚未调用真实模型验证生产速度、实际去 AI 质量或长时间并行行为，也未部署、运行生产书、执行第 26 条第 5 步或获得监工/读者验收。仍存活的旧子进程保持所有权保护，不会由新服务强行接管。

本轮没有修改或删除主仓库 `books/` 书稿，没有操作 Docker 或 8787，没有 push；只做本地提交。第 27 条已标为“开发完成，待监工验收”，按指令在此停止。
