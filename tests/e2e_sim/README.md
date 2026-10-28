# 整书仿真（P3 / refs/tags/e2e17）

2026-10-28 19:50决定已执行：精准排除测试包workflow历史 `authorization_ref`，包内清单保留排除前原始行SHA并另记导出行SHA，不改现有来源或payload hash。捕获成功，但唯一PG运行在导入前被旧测试驱动守卫拒绝，原生reader及十类零表均未到达；**前缀尚未验收，不能供正文流程使用。** 3次实际额度已耗尽，不再实际运行。测试基建修复只有离线证据，详见 `reports/parallel-2026-10-28/P3/HANDOFF.md`、`r5-prefix-receipt.json`。主线在int/full-flow从正常建书入口使用全程剧本；P3不改业务代码。此前只读全包扫描保留在GUARD-SCAN.md。

整书完整配置仍缺29个显式绑定回复、建书入口未覆盖；53个协议登记及七模板示例不是完整剧本。整书剧本的编译器和模板覆盖见 `whole_book_tapes.py` 与交接目录的 `WHOLE-BOOK-SCRIPT.md`。普通P3 runner仍只支持独立前缀检查及单章场景，不等于主线整书运行器。

## 一条命令

前缀及独立可信 SHA、脱敏的原运行角色配置准备好后，在 P3 worktree 根目录执行：

```sh
make e2e-sim ARGS='--prefix-only'  # 先独立验证PG导入、原生reader和十类空表
make e2e-sim                        # 前置条件满足后才执行章节场景
# 单独运行截断场景：每个场景都使用新数据库/新资源，绝不克隆项目身份。
make e2e-sim ARGS='--scenario review_length'
```

默认从仓库外 `/Volumes/MACSSD/owen-home/Documents/workspace/bestseller-par-private/P3/LATEST.json` 读取前缀位置和可信 SHA。也可显式指定：

```sh
make e2e-sim ARGS='--prefix-bundle /absolute/private/P3/prefix.json --prefix-sha256 TRUSTED_CANONICAL_SHA --source-roles /absolute/private/P3/runtime-roles.json'
```

捕获输出须为全新的绝对路径：拒绝 `..`、任意层软链接、已存在目录；解析真实路径后仍须位于上述 P3 私有根内。目录权限 0700，文件 0600；示例绝对路径仅表示参数形状。前缀正文、原始 prompt、保留响应及运行中完整原生日志均留在仓库之外。reports 只保存安全栈、统计、文件清单、SHA 和 JUnit。不得将包复制进仓库、reports 或 Git。没有前缀时拒绝启动，无手工或合成审批 fallback。

命令仅使用本地缓存镜像，绝不拉取或构建。第三轮已只读核验缓存，未见 PG16/vector，未联网拉取；候选仍为 `ankane/pgvector:latest`（既有PG15.4镜像）、`redis:7-alpine`、`bestseller-api:latest`。本轮没有启动Docker资源。每次新建 `par_P3_<nonce>_` 容器、PG 数据库、内部网络，PG/Redis 临时内存盘，不发布宿主端口，不复用其他资源。源代码/跟踪配置只读挂载，隐藏镜像内 `/app`，排除 dotenv、local.yaml 和原运行时 profile。

## 准备和验证前缀

P2 原提交 `128afbdd535d5b18e9987be1467a3181d6830b9b` 已 cherry-pick 到 P3；原始 P2 不变。`tests/support/planning_prefix_capture.py` 的 P3 扩展保持原始身份/来源 hash，增加明确 PG 测试库路由、实际 READ ONLY 身份校验、严格空库检查、必要 Volume/Character 父行闭合和私有 payload 暂存。导入不复制活预算：历史 FK 哨兵始终 stopped、零额度、过期；不重开源书额度。

生产捕获只获准 fa14e681-2bd9-44f5-93d1-7f2ef9900d69。`capture_source.py` 在单个 READ ONLY 事务内执行，服务端 statement_timeout=30s，凭证仅在内存，网络只允许既定本机 PG 端点。禁止另一项目 d407ca85…、主线 S01/私有目录/隔离容器。每次失败保存安全栈，达到重试上限后不得自行再跑。包必须先通过原生 complete-plan reader，不能制造来源、审批、审校或采用证明。capture 失败不会生成可用包。

获授权捕获时，在单个进程只读读取生产 `.env`、Compose 数据库配置、`config/default.yaml` 与明确指定的生产 artifacts 根。凭据只在内存；先原生应用 artifacts 内启用的运行配置，再固定显式角色和 artifacts 根供嵌套 reader 使用，不复制生产配置文件。CLI 形状（R5实际额度已耗尽；以下是获准捕获的接口说明，不代表再次运行许可）：

```sh
PYTHONPATH=src:tests python -B -m e2e_sim.capture_source \
  --source-root /absolute/authorized/production-checkout \
  --source-artifacts /absolute/authorized/production-artifacts \
  --exclude-authorization-ref \
  --output /Volumes/MACSSD/owen-home/Documents/workspace/bestseller-par-private/P3/NEW_CAPTURE \
  --report reports/parallel-2026-10-28/P3/NEW_CAPTURE.json
```

`--exclude-authorization-ref` 只在既定fa14测试捕获的workflow元数据精准路径生效，默认不启用。`export_manifest` 声明路径、原因、用户决定、排除前原始行SHA政策与导出投影SHA；导入会验证清单。此例外不作用于原始保留payload，不放行其他位置的同名字段。不剔除或重写原始保留 payload 中的字段来迎合凭据守卫；这会改变来源 hash。若守卫拒绝，保存安全栈及哈希诊断后停止，先明确字段语义和导出规则。

Runner 严格先执行独立 prefix 进程：

1. 核验包独立 SHA，导入全新测试 PG。
2. 调原生 `read_existing_complete_fanqie_plan`，对比完整结果 hash。
3. 逐表确认草稿、审校、评分、采用决策、重写、Canon、Timeline、章节快照、导出十类下游表全零。
4. 只有新 `prefix-output/prefix-check.json` 绑定当前 run_id、随机 invocation_id、可信包 SHA，并明确十类计数全为整数0，才启动正常 API、ARQ worker 和章节 HTTP 驱动。`--prefix-only` 到此返回，不启动上述章节进程。

首次运行还会经原生 `alembic upgrade head` 初始化 PG；全新库使用仓库既有 greenfield baseline/stamp 路径。报告身份、版本和扩展回读，不能把 stamp 说成历史迁移链逐条重放。

## 场景与证据

`chapter_adoption` 通过正常 `POST /api/v1/projects/{slug}/chapters/1/pipeline`、GET task 轮询，期望原生采用和精确正文的知识闭合；`read_adopted_knowledge(..., require_bible=True)` 强制 Bible 完整性。`review_length` 使用同一入口，剧本把 `chapter_review` 的第一次物理返回设为 length，期望 UNKNOWN，无采用/重写/知识副作用。每次只选一个场景，保留原项目 UUID，不在同一库内复用已变动的场景状态。

每个进程阶段必须原子创建全新 output 目录，存在即拒绝。prefix/scenario 使用不同 invocation UUID；前置回执只能消费一次。POST 前记录日志 inode/偏移，只采纳本次追加且绑定当前 task/workflow 的调用；任务未 completed 不可通过采用场景。截断等 LlmRun 只取证据 helper 返回的确切 ID，不按时间、模板或 workflow 范围猜测。

`evidence.py` 逐个 synthetic send 关联实际 project、workflow、operation key、物理 input hash、原生 attempt ID、LlmRun 及其 execution_receipt/paid_call_origin。任何歧义、重复、错 workflow/hash/operation 都不能增加成功计数。未知发送/超时缺少完整原生回执时保持未匹配，不制造证明。

reports 中每次运行的 nonce 目录包含：

| 文件 | 内容 |
|---|---|
| `prefix-input-manifest.json` | 私有包路径、可信包/文件 SHA，不含正文 |
| `prefix-output/prefix-check.json`、`invocation.json` | 独立导入、reader、下游空表检查结果 |
| `failures-*.jsonl` | 异常类型、消息 SHA/字符和字节长度、逐帧文件/函数/行号、固定错误码/SQLSTATE；无消息原文、源码行、局部变量 |
| `wire-*.jsonl` | synthetic 标识、实际模板/UUID/operation/hash/attempt 计数，无 prompt/正文/密钥 |
| `network-*.jsonl` | 被拒绝的网络动作及目标 SHA；非空强制整次失败 |
| `scenario-output/results.json`、`scenario-output/junit.xml` | 所选场景断言及安全停点 |
| `run.log`、角色 `.log`、`runner-junit.xml` | 安全编排元数据、完整原日志的私有位置/SHA、进程退出码、精确资源清理结果 |
| `round4-run.json` | 本轮状态，缺安全栈的失败禁止后续运行 |

退出 0=全部所选断言通过；1=保留业务失败；2=设施、映射、来源前置、网络或清理错误。带安全栈和具体码的业务停点可以交付诊断，但不能当成采用/知识闭合通过。原生日志不复制到 reports；需要本机审查时按私有路径读取。SIGKILL/daemon 故障/低磁盘可阻止清理，须按 nonce+标签精确核验，禁止全局 prune。

## 剧本与不可 mock 的边界

`tapes.py` 显式条目按实际项目 UUID、模板和从1开始的物理调用序号匹配。`NativeTemplateObserver` 只读原生父任务的 request/call 身份，读取已有发送计数和真实 attempt ID；游标只用于仿真路由，不授予付款/重试权限。请求无法唯一关联、条目耗尽、schema 或 endpoint 不符即失败。实际运行角色参数保持原声明，凭证值使用明确无效的离线占位值；这不构成真实付费或批准证明。endpoint 从这些声明生成精确 allowlist，不能通配、带用户信息或 access query。

支持 JSON/SSE、function 工具调用、正文、token 计数、stop/tool_calls/length/content_filter；`timeout` 抛 ReadTimeout，`disconnect` 在指定 SSE 分片后抛 ReadError。原生账本决定 Unknown，绝不把 length/filter 转成 stop。只替换 HTTPX 最低 transport 返回；SDK、complete_text、请求钩子、发送门禁、来源/审校证明、采用、Override/追债、Canon/Timeline、知识闭合、完成判定都不 mock。源码适配只能在测试目录；不得修业务代码来消除基线 RED。

外层 Docker internal 网络和内层 Python socket 围栏同时存在；普通 HTTP 驱动只额外允许测试 API 的精确 IP:8000。非 Python 原生扩展/任意子进程不属于 Python 围栏的证明范围，套件不启动下载器或外部模型。

离线契约测试（无真实 DB/Docker）：

```sh
PYTHONPATH=src:tests python -B -m pytest --noconftest --confcutdir=tests/e2e_sim --no-cov tests/e2e_sim
```

P2 原回归需显式 SQLite 测试 URL 并加载仓库测试安全守卫。隔离 PG 的真实三步检查必须单独运行；不得把 fake-session/SQLite 通过冒充 PG 通过。
