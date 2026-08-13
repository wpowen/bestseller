# 市场验证能力（Market Validation）设计与实施计划

> 2026-08-08 设立，**同日落地**（状态见文末）。回答一个此前全靠人工的问题：
> **我们建的这本书，市场愿不愿意读？**
> 覆盖：题材热度、同题材竞品与流量、书名查重/同壳评估、简介与榜单简介对标、爆款可能性评估。
> 自动化 `docs/book-title-market-validation-process.md` 的 6 步人工 SOP。

## 快速使用

```bash
# 题材热度速查（无 DB / 无 LLM）
uv run bestseller market-validation heat --genre 修仙

# 完整验证（建书前：题材 + 概念 + 候选书名 + 简介）
uv run bestseller market-validation validate --genre 修仙 \
  --concept "一句话概念" --title "候选书名A" --title "候选书名B" --blurb "简介文案"

# 对已有项目验证并落库（artifact + metadata 回填）
uv run bestseller market-validation validate --project-slug <slug> --persist

# 读回最近一次报告
uv run bestseller market-validation inspect <slug>
```

管线内自动验证（构思完成后 advisory，不阻断）：`pipeline.enable_market_validation: true`。

## 0. 与既有系统的关系（边界声明）

- **独立目录维护**：`src/bestseller/services/market_validation/` 子包 +
  `src/bestseller/domain/market_validation.py` + `config/market_validation.yaml`。
  不改动 conception/pipelines 的任何现有行为。
- **默认关闭，关掉即字节级不变**：沿用仓库通用契约。管线挂接仅通过
  feature flag（默认 False）+「artifact 存在即消费」两种显式途径。
- **复用不重造**：
  - 评据层复用 `fanqie_market_*` 三张表与 `domain/fanqie_market.py` 的快照契约；
  - Web 检索复用 `services/search_client.py`（Tavily/Serper/Noop，fail-open）；
  - LLM 调用复用 `services/llm.py`（`fallback_response` 必填）；
  - 外部调用骨架照抄 `concept_methodology_agent._gather_market_heat` 的 fail-open 45 行。
- **安全边界**（沿用 `docs/fanqie-market-intelligence.md`）：不摄入付费正文、
  不模仿具名作者、竞品信息仅用于市场判断与匿名工艺约束，禁止复用书名/人名/独家体系。

## 1. 数据源盘点（2026-08-08 实测）

| 数据源 | 状态 | 提供什么 | 工程成本 |
|---|---|---|---|
| FanqieHub `GET /api/data` | ✅ 实测可用，数据日期=当天 | 番茄 37 个(频道×分类)榜单、2200+ 书：在读人数及变化、排名及变化、新入榜、简介、标签、状态 | 低（一次请求全量；参数已从旧版漂移：`category=<精确中文分类名>`、`platform=男频/女频`、`per_page` 可拉满；`rank_type` 已失效，客户端侧过滤 `榜单类型`） |
| 七猫 `qimao.com/paihang/{boy\|girl}/{hot\|new\|over}` | ✅ 实测可用，全明文 SSR | 大热/新书/完结榜：书名/作者/分类/字数/热度/简介；详情页另有评分+累计人气 | 低（httpx+HTML 解析，零反爬） |
| 微信读书 `/web/category/{id}` | ✅ 实测可用，免登录 | 分类书单 JSON：在读人数、评分(0-1000)、评分人数、好中差分布 | 低（`__INITIAL_STATE__` raw_decode；注意口径偏付费精品，与免费大盘互补） |
| 番茄官网 `/page/{bookId}` | ✅ 实测可用 | 书详情明文（readCount/字数/章节数/简介），可做查重复核 | 低；榜单列表页有字体混淆，v1 不直采（FanqieHub 已覆盖） |
| WebSearch（Tavily/Serper） | ✅ 已有抽象层 | 书名查重（`site:fanqienovel.com`）、同壳竞品发现 | 低（复用 search_client，无 key 时 Noop 降级） |
| 起点 | ❌ 瑞数 WAF（202 JS 探针） | — | 高（需无头浏览器）；v1 不做，适配器位留空 |
| 优书网 | ❌ 站点停摆（TLS 挂/裸 404） | — | 放弃；口碑维度由微信读书替代 |

参考轮子（GitHub 调研结论）：oh-story `story-long-scan` 的番茄 cat_id 映射表（男频19/女频18）
与四平台字段规范已取回（scratchpad/ohstory/）；七猫 `api/rank/book-list` JSON 接口
（ElakeApi）与微信读书分类 ID 表可直接抄接口 shape。

## 2. 子系统能力（对外只有一个动词）

`run_market_validation(session, settings, request) -> MarketValidationReport`

输入（三种粒度，字段渐进可选）：
- 题材粒度：`genre_key`/`sub_genre`（canonicalize 后）→ 只出题材热度部分
- 概念粒度：+ `concept_seed`/logline → 加竞品对标与差异化评估
- 成书粒度：+ `title` + `blurb` → 加书名查重/同壳评估、简介对标、综合爆款评估

报告分五个 section，每个 section 独立降级（数据源不可用 → 该节标记 `degraded`，不影响其余）：

1. **genre_heat 题材热度**：映射到平台分类 → 榜单在读人数分布（p10/p50/p90）、
   新书榜占比（品类是否还进得去新人）、在读变化趋势。判据全确定性。
2. **competitor_scan 同题材竞品**：榜单内同题材书 TOP N 的书名/简介/热度/标签，
   与我们 concept_seed 的相似度标记（LLM 判官，找出「已经有人写了」的直接碰撞）。
3. **title_check 书名验证**：自动化 SOP 第 1/2/4 步——
   a) 榜单内精确/近似查重（确定性，编辑距离+核心词命中）；
   b) WebSearch 站内查重（`site:fanqienovel.com "<候选>"`）；
   c) 同壳评估：提取书名格式壳（「我在X当Y」类模式），统计榜单内同壳书数量与热度中位数
      → 壳拥挤且流量差=红牌。壳模式抽取为确定性规则+榜单命名形态统计（长度分布、冒号占比等）。
4. **blurb_benchmark 简介对标**：我们的 blurb vs 该分类榜单 TOP 简介的形态统计
   （长度/句数/首句钩子类型），差异过大给 warning（对齐既有「榜单解剖=90字3句平实开场」结论）。
5. **verdict 综合评估**：LLM 判官汇总以上确定性证据出 0-100 分 + 三档结论
   （GO / REVISE / NO-GO）+ 逐条理由。**判据显式声明不确定性：这是市场风险评估，
   不是爆款预言**；prompt 强制引用 section 1-4 的证据条目，禁止凭空断言。

## 3. 目录与文件

```
src/bestseller/services/market_validation/
    __init__.py        # 门面：run_market_validation, load_market_validation_config
    types.py           # request/report pydantic DTO（domain 层薄别名）
    category_map.py    # 题材 taxonomy → 平台分类映射解析（读 config）
    adapters/
        __init__.py    # registry（仿 publishing/）
        fanqiehub.py   # 修正参数后的番茄榜单适配器（复用 domain/fanqie_market 契约）
        (qidian.py / qimao.py / yousuu.py 视实测结论)
        websearch.py   # search_client 包装：查重/同壳发现
    analyzer.py        # 纯函数：快照 → genre_heat/title_check/blurb_benchmark（无 IO，全可单测）
    judge.py           # LLM 判官：competitor 相似度 + verdict（fallback_response 必填）
    repository.py      # 落库：planning artifact `market_validation_report` + metadata 摘要回填
src/bestseller/domain/market_validation.py   # pydantic 契约（frozen）
config/market_validation.yaml                # enabled、平台分类映射、同壳规则、阈值
tests/unit/test_market_validation_*.py       # 一层一个文件（对标 fanqie_market 六件套）
```

- ArtifactType 新增：`MARKET_VALIDATION_REPORT`（走 `import_planning_artifact`，免迁移）。
- 快照落既有 `fanqie_ranking_snapshots` 表（同源同契约）；非番茄源先不进专用表，
  证据整包存报告 artifact 的 `evidence` 字段，需要跨书查询时再补迁移。
- CLI：`bestseller market-validation` 子 app（typer），命令：
  `validate`（--genre/--title/--blurb/--concept 或 --project-slug）、`snapshot`（抓榜单）、
  `inspect`（读回）。管线之外可独立使用。

## 4. 挂接点（皆为可选消费，默认不挂）

| 挂接 | 方式 | 默认 |
|---|---|---|
| 建书前人工验证 | CLI `validate --genre ... --concept ...` | 手动 |
| 建书后书名/简介验证 | CLI `validate --project-slug`（读 conception snapshot） | 手动 |
| 构思管线自动验证 | `PipelineSettings.enable_market_validation`（False）；开启时在 conception 完成后追加报告 artifact，**不做闸门、不毙书**（advisory only——教训：门禁误杀审计 2026-07-25） | False |
| prompt 层消费 | 报告摘要回填 `project.metadata_json["market_validation_summary"]`，由 drafts 层自愿读取（同 fanqie_craft_profile 模式） | artifact 存在即生效 |

明确不做：不把 verdict 做成硬门（历史上门禁误杀率过高，见 gate-false-positive-audit）；
不自动改书名/简介（报告给人/给上游 agent 决策）。

## 5. 实施顺序（TDD）

1. domain 契约 + config 加载 + 分类映射（纯逻辑，测试先行）
2. fanqiehub 适配器修正（新参数）+ 快照缓存（scratch/data 目录，避免重复请求）
3. analyzer 确定性三件套（genre_heat/title_check/blurb_benchmark）
4. websearch 查重 + judge（LLM，fail-open）
5. repository + CLI + flag 三处同步（settings/default.yaml/test_settings）
6. 真实题材端到端冒烟（真书判据：拿一个已建项目跑全报告）

## 5.0 自测与验证记录（2026-08-08 当日）

**L1 单测**：`tests/unit/test_market_validation_*.py` 八件套（config / category_map /
analyzer / adapters / judge / service / request_builder / pipeline_hook / wiring）。
其中 `test_market_validation_pipeline_hook.py` 不是源码 grep，而是桩掉构思与分流点后
**真跑 `run_autowrite_pipeline` 的构思前置段**，断言挂钩被调用、收到规范题材键、
flag 关闭时零调用、挂钩抛错时建书继续。

**L2 归因**：把管线挂钩改回修复前写法 → `test_hook_resolves_key_from_genre_intent_contract`
当场变红；恢复后转绿。证明断言非空转。

**L3 真栈**：live 栈开 flag 跑真实网页建书（`custom-xianxia-1786179870`），
DB 事实核验通过——`projects.metadata.market_validation_summary` 已写、
`planning_artifact_versions` 有 `market_validation_report` v1、
`llm_runs` 有 2 条 `market_validation_collision_judge`、进度事件已发。

**当日修掉的三个自伤**（离线全绿时都不可见）：
1. flag 判断写在 try 之外且裸取 `settings.pipeline` ⇒ 桩 settings 下 AttributeError
   **把整个建书任务搞崩**（`test_web_server.py` 两红）。已改双层 getattr 防御 + 回归 pin。
2. 挂钩最初只接 CLS 的 `use_conception` 分支，网页建书路径根本走不到（死分支）。
   已补 `web/server.py` 构思完成点。
3. 网页路径把合成预设键 `custom-xianxia` 当规范键传入 ⇒ 热度/竞品整节 SKIPPED，
   而报告照常产出、照常给分（看起来完全正常）。已抽 `request_builder.py` 单一解析器
   三处收口；同一本书修复前后样本数 **0 → 60**。

**题材映射覆盖抽测**：都市脑洞 / 规则怪谈 / 末日求生 / 宫斗宅斗 / 快穿 / 豪门总裁
（男女频各半）全部映射成功并取回当日真实数据。

**已知非本能力的红**：`test_deslop_revise.py::test_self_check_covers_staccato_and_system_ladder`
——commit 21bd191 给自查表加到 13 条但断言仍写 12 条，属画面感车道遗留。

## 5.1 落地状态（2026-08-08）

已实现并真机验证：
- `src/bestseller/services/market_validation/`（config/category_map/analyzer/judge/service/repository + adapters/{fanqiehub,qimao,websearch}）
- `src/bestseller/domain/market_validation.py`、`config/market_validation.yaml`
- CLI `bestseller market-validation {validate,heat,inspect}`（`src/bestseller/cli/market_validation.py`）
- flag `pipeline.enable_market_validation`（默认 False）已接入 `pipelines.py` 构思 pre-pass（advisory、fail-open）
- ArtifactType 新增 `market_validation_report`；metadata 回填键 `market_validation_summary`
- 测试：`tests/unit/test_market_validation_*.py` 六件套（52 例）
- 真机冒烟：修仙题材热度（当日 60 样本）、三候选书名分别命中 fail/pass/caution、
  LLM 撞车判官对 24 本竞品判为无撞车、真实项目 `--persist` 落库 + `inspect` 读回全通。

未做（留待需要时）：微信读书口碑适配器（config 已留位，enabled=false）、起点适配器
（需无头浏览器）、web 查重（需配置 TAVILY/SERPER key，当前 Noop 降级）。

## 5.2 目前它在建书流程里"做什么"与"不做什么"（重要）

**做**：开 flag 后，构思一完成就自动跑一次验证 → 摘要进 `project.metadata`、
全量报告落 artifact、进度事件 `market_validation_completed` 让指挥台看得见。

**不做**：**它不改变任何生成结果**。书名撞车、简介形态偏离、题材热度低——都只是
报告里的结论，不回灌构思、不重起名、不阻断。这是刻意的（门禁误杀史见
`docs/gate-inventory-2026-06.md` 与误杀审计），但也意味着"起作用"目前止于
"给人/给上游 agent 看"。

若要让它真正影响产出，风险最低的一步是**只把书名硬撞车（榜单精确同名，客观可判、
无品味成分）回灌到 `_polish_title` 重起名**，且必须带放弃预算（重试 N 次仍撞车就
放行并记录），避免重蹈自恢复无限循环（见 [[generation-gate-autoresume-token-burn-loop]]）。
其余维度（热度、简介形态、概念撞车）建议保持 advisory。

## 6. 风险与开放问题

- FanqieHub 是第三方聚合站，可能再次漂移/消失 → 适配器隔离参数；快照落库留证据；
  离线种子兜底沿用 `config/market_profiles/fanqie/`。
- 起点/七猫数据源按实测结论决定做/不做（宁缺毋滥，Noop 降级）。
- 「爆款判据」上限：榜单只有幸存者，无失败样本 → verdict 定位为风险排查
  （查重/壳拥挤/热度地板），不承诺预测命中。
