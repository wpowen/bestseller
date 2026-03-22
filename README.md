# BestSeller

BestSeller 是一个面向长篇小说生产的人机共创框架，目标是把长篇创作拆成可持续迭代的生产流水线，而不是一次性“大 prompt 写书”。

当前仓库已经落到第一版可运行基线：

- `PostgreSQL 16+` 作为唯一主数据库
- `pgvector` 作为内建语义检索层
- `Project / Volume / Chapter / SceneCard / PlanningArtifact / Canon / Workflow` 首批模型已经入代码
- 已补齐故事圣经执行层：
  - `BookSpec / WorldSpec / CastSpec / VolumePlan` 可物化为项目、世界规则、地点、阵营、角色、关系和角色状态快照
- 提供 Alembic 初始迁移基线
- 提供基础 CLI：
  - `bestseller status`
  - `bestseller db render-sql`
  - `bestseller db init`
  - `bestseller workflow materialize-story-bible|materialize-outline|materialize-narrative-graph|materialize-narrative-tree|list|show`
  - `bestseller project create|list|show|structure|pipeline|review|repair|autowrite`
  - `bestseller planning import|generate|list|show`
  - `bestseller chapter add|assemble|context|review|rewrite|pipeline`
  - `bestseller scene add`
  - `bestseller scene context|draft|review|rewrite|pipeline`
  - `bestseller rewrite impacts|cascade`
  - `bestseller retrieval refresh|search`
  - `bestseller story-bible show`
  - `bestseller narrative show|tree-show|path-show|search`
  - `bestseller export markdown|docx|epub|pdf`
  - `bestseller canon list`
  - `bestseller timeline list`
  - `bestseller writing-preset list|show|hot`
- 已接入统一 `LLM gateway`，支持 `writer / critic / editor` 三类角色
- 已接入 `summarizer` 角色，为知识层生成场景摘要
- 默认可在无外部模型依赖时退回 `mock/fallback`，同时把 `llm_runs` 审计记录写入数据库
- 已具备最小自动流水线：
  - `planning generate`: 从 premise 自动生成 `premise -> book/world/cast/volume/chapter-outline`
  - `scene pipeline`: `draft -> review -> rewrite -> re-review`
  - `chapter pipeline`: 串行跑完本章 scenes，再 `assemble -> export`
  - `project pipeline`: 从项目级串行跑完整个已规划章节，导出整书，并落库项目级一致性评审
  - `project autowrite`: 从 premise 直接生成整书规划并跑完整个写作流水线
- 已具备最小知识层：
  - `CanonFact` 自动沉淀角色最近状态、出场记录、场景摘要和最新剧情推进
  - `TimelineEvent` 自动沉淀场景级事件时间线
  - `CharacterStateSnapshot` 自动沉淀角色的阶段状态，供后续场景写作读取
  - `RetrievalChunk` 自动索引故事圣经与当前正文，可用 `retrieval search` 查询
  - `scene context` 会把故事圣经、近期剧情、时间线、角色可见事实和检索命中组装成写作上下文包
- 已具备第一阶段显式叙事图谱：
  - `PlotArc / ArcBeat / Clue / Payoff / ChapterContract / SceneContract / EmotionTrack / AntagonistPlan` 已入库
  - `workflow materialize-narrative-graph` 可从现有规划和故事圣经重建叙事图谱
  - `scene/chapter context` 已显式吃到 arcs / beats / clues / emotion tracks / antagonist plans / contracts
  - `narrative show` 可直接查看当前项目的叙事图谱
- 已具备 Narrative Tree 与路径式上下文检索：
  - `workflow materialize-narrative-tree` 会把 PostgreSQL 真值导出成 `/book /world /characters /arcs /emotion-tracks /antagonists /volumes /chapters /scenes` 叙事树
  - `narrative tree-show|path-show|search` 可直接查看树节点、按路径精确读取、按路径偏好做树搜索
  - `scene/chapter context` 现在按 `path retrieval -> tree search -> hybrid retrieval` 三层顺序装配上下文
- 已具备“分阶段扩世界”控制层：
  - `WorldBackbone` 固定全书主旋律、主线驱动力和不可轻易漂移的世界主干
  - `VolumeFrontier` 限定当前卷允许展开的世界边界
  - `DeferredReveal` 管理未来才允许揭开的真相和暗线
  - `ExpansionGate` 管理世界扩张闸门，并随章节推进同步状态
  - scene/chapter context 只注入当前 frontier 允许可见的世界规则、地点、势力与未来揭示边界
- 已具备项目级一致性评审：
  - 汇总章节覆盖率、知识层覆盖率、Canon 完整度、Timeline 完整度、待重写压力和导出就绪度
  - `project review v2` 已纳入主线推进、暗线埋设/兑现、情绪线连续性、角色弧光台阶、世界规则落地、反派推进压力
  - 结果落到 `review_reports` 和 `quality_scores`
- 已具备 `Scene / Chapter Quality Rubric v2`：
  - scene review 现在显式评分 `hook_strength / conflict_clarity / emotional_movement / payoff_density / voice_consistency`
  - chapter review 现在显式评分 `main_plot_progression / subplot_progression / ending_hook_effectiveness / volume_mission_alignment`
  - 审校报告的 `structured_output` 已带 `scores`，方便后续工作台做 findings dashboard
- 已具备重写影响分析：
  - 场景审校生成 `RewriteTask` 时，会同步推导被波及的 `CanonFact / Scene / Chapter`
  - 可用 `bestseller rewrite impacts` 查询或刷新影响面
  - 可用 `bestseller rewrite cascade` 自动重跑受影响章节
- 已具备多格式导出：
  - `Markdown`、`DOCX`、`EPUB` 已可直接导出
  - `PDF` 需要安装可选导出依赖：`pip install -e .[export]`
- 已补单元测试，覆盖配置加载、领域模型、CLI、schema SQL、session、workflow、draft 和 export 渲染

当前仍未完成的部分：

- 更强的正文生成策略，真实模型已可接入，但 fallback 仍然偏结构化稳态写法
- 更精细的多轮 planner 迭代和人工确认点
- 更强的 retrieval embedding 与更复杂的跨章节自动级联策略

## Web Studio

当前已经提供本地 Web Studio，用于发起写作、跟踪执行和阅读成品。

启动：

```bash
./start.sh
./studio.sh
```

页面当前支持：

- 发起 `project autowrite`
- 选择平台 / 题材 / 篇幅 / prompt pack / 写作画像
- 查看执行看板和阶段日志
- 查看项目结构、故事圣经、叙事图谱、流程跟踪
- 查看章节 / 场景状态板，而不只是结构 JSON
- 查看故事圣经中的：
  - 全书主干
  - 当前卷世界边界
  - 延后揭示
  - 世界扩张闸门
- 查看叙事图谱中的：
  - 主线与节拍
  - 伏笔 / 兑现账本
  - 关系线 / 情绪线 / 反派推进
  - chapter / scene contract
- 阅读 Markdown 成品，显示：
  - 正文总字数
  - 排除空白后的字符数
  - 段落数
  - 预计阅读时长
  - 文件大小和更新时间

这层的目标不是“一次性跑完千万字”，而是把长篇按阶段推进，并把当前阶段真正需要看到的信息稳定展示出来。

## Prompt Pack

当前系统已经支持“题材专用 Prompt Pack”。这层不是零散的 if/else，而是可版本化的题材包，直接接入：

- `planning generate`
- `project autowrite`
- scene writer
- scene/chapter review
- scene/chapter rewrite

当前内置 4 个 pack：

- `apocalypse-supply-chain`
  - 末日囤货升级流
- `xianxia-upgrade-core`
  - 仙侠升级夺机缘
- `urban-power-reversal`
  - 都市异能反转流
- `romance-tension-growth`
  - 感情拉扯成长流

列出和查看：

```bash
./scripts/run.sh prompt-pack list
./scripts/run.sh prompt-pack show apocalypse-supply-chain
```

创建项目或整书自动写作时，可以直接指定：

```bash
./scripts/run.sh project create my-story "我的长篇" 末日科幻 220000 40 \
  --prompt-pack apocalypse-supply-chain

./scripts/run.sh project autowrite my-story "我的长篇" 末日科幻 220000 40 \
  --premise "末日零点我能提前购买未来物资，主角重生在末日爆发前三天，拥有未来商城与资源差优势。" \
  --prompt-pack apocalypse-supply-chain
```

Prompt Pack 文件目录：

- `config/prompt_packs/`

每个 pack 文件包含：

- 题材说明
- 反例 / anti-patterns
- `writing_profile_overrides`
- planner / writer / review / rewrite 专用片段

多数情况下，后续新增一个题材，不需要改 Python 逻辑，只需要新增一个 pack 文件。

## 写作预设目录

除了 Prompt Pack，系统还内置了一套更上层的“写作预设目录”，用于一次性确定：

- 平台目标
- 主类型
- 细分卖点
- 目标总字数
- 目标章节数
- 人物原型
- 爽点 / 套路标签
- 章节节奏和读者承诺

当前已经内置：

- 平台预设：`番茄小说 / 起点中文网 / 七猫小说 / 晋江文学城 / 纵横中文网 / 17K 小说网 / 中文网文平台（通用）`
- 题材预设：`末日囤货 / 末日规则 / 末日基建 / 仙侠升级 / 都市异能反转 / 都市黑科技 / 历史争霸 / 悬疑追凶 / 规则怪谈 / 无限流 / 星际舰队 / 女性成长拉扯 / 古言宫斗 / 御兽养成 / 电竞直播 / 民俗悬疑 / 重生创业`
- 篇幅预设：从 `4 章样书试写` 一直到 `超长连载阶段单元`
- 热点题材推荐：支持按最近市场热度排序查看

硬约束：

- 每章最低 `5000` 字
- 推荐每章 `5500` 字左右
- 推荐范围 `5000-6000` 字

查看预设目录：

```bash
./scripts/run.sh writing-preset list
./scripts/run.sh writing-preset show apocalypse-supply --kind genre
./scripts/run.sh writing-preset show qidian --kind platform
./scripts/run.sh writing-preset show trial-4 --kind length
./scripts/run.sh writing-preset hot --limit 8
```

## 写作画像配置

这轮已经把“写作配置”从几个基础字段扩成了一套可执行的 `writing_profile`。它不是只展示在页面里，而是会同时被：

- `project create`
- `project autowrite`
- `planning generate`
- scene/chapter draft prompt
- scene/chapter review & rewrite prompt

共同消费。

当前支持的配置维度包括：

- 平台与内容定位
  - `platform_target`
  - `content_mode`
  - `reader_promise`
  - `selling_points`
  - `trope_keywords`
  - `hook_keywords`
  - `opening_strategy`
  - `chapter_hook_strategy`
  - `hook_deadline_words`
  - `pacing_profile`
  - `payoff_rhythm`
  - `update_strategy`
- 主角与人物引擎
  - `protagonist_archetype`
  - `protagonist_core_drive`
  - `golden_finger`
  - `growth_curve`
  - `romance_mode`
  - `relationship_tension`
  - `antagonist_mode`
  - `ensemble_mode`
- 世界与信息释放方式
  - `worldbuilding_density`
  - `info_reveal_strategy`
  - `rule_hardness`
  - `power_system_style`
  - `mystery_density`
  - `setting_tags`
- 文风与表达约束
  - `pov_type`
  - `tense`
  - `tone_keywords`
  - `prose_style`
  - `sentence_style`
  - `info_density`
  - `dialogue_ratio`
  - `taboo_topics`
  - `taboo_words`
  - `reference_works`
  - `custom_rules`
- 连载节奏硬约束
  - `opening_mandate`
  - `first_three_chapter_goal`
  - `scene_drive_rule`
  - `exposition_rule`
  - `chapter_ending_rule`
  - `free_chapter_strategy`

系统内置了按题材分流的默认预设：

- `末日 / 科幻 / 星际 / 生存`
- `仙侠 / 玄幻 / 奇幻 / 升级`
- `都市 / 异能 / 悬疑 / 现实`
- `女频 / 成长 / 言情 / 宫斗`

示例 profile 文件在：

- `examples/configs/web_serial_profile.yaml`

CLI 可以直接吃文件：

```bash
./scripts/run.sh project create my-story "我的长篇" sci-fi 220000 40 \
  --profile-file examples/configs/web_serial_profile.yaml

./scripts/run.sh project autowrite my-story "我的长篇" sci-fi 220000 40 \
  --premise "末日零点我能提前购买未来物资，主角重生在末日爆发前三天，拥有未来商城与资源差优势。" \
  --profile-file examples/configs/web_serial_profile.yaml
```

## 一键启动

项目现在提供本地开发脚本：

- `./start.sh`
  - 根目录启动入口
  - 内部转发到 `./scripts/start.sh`
- `./stop.sh`
  - 根目录停止入口
  - 内部转发到 `./scripts/stop.sh`
- `./scripts/start.sh`
  - 创建或更新 `.venv`
  - 默认安装 `.[dev,export]`
  - 检测到真实 LLM key 时会额外安装 `.[llm,cloud]`
  - 拉起本地 `PostgreSQL + pgvector` 容器
  - 若检测到 `.env` / `.env.local` 或当前环境中存在 LLM API key，则默认关闭 mock 模式
  - 写入 `.runtime/dev.env`
  - 执行 `alembic upgrade head`
- `./scripts/run.sh ...`
  - 自动注入 `.runtime/dev.env`
  - 直接运行 CLI
- `./scripts/verify.sh`
  - 跑单测
  - 跑一条完整功能验证链路
  - 验证 `planning list/show`、`project structure`、`story-bible show`
  - 验证 `scene context`、`chapter context`
  - 验证 `chapter review`、`chapter rewrite`
  - 验证 `project repair`
  - 验证 `rewrite impacts`、`retrieval search`、`project pipeline`、`project review`
  - 验证 `markdown/docx/epub/pdf` 导出
- `./scripts/stop.sh`
  - 停止并移除本地 PostgreSQL 容器
  - `./scripts/stop.sh --purge` 还会删除 Docker volume

也可以直接用 Makefile 包装：

```bash
./start.sh
./stop.sh --purge

make dev-start
make ui
make verify
make dev-stop
```

## Web Studio

现在已经有本地 HTML 页面，可直接用浏览器交互完成：

- 创建项目
- 输入 premise
- 直接触发 `project autowrite`
- 配置平台定位、读者承诺、卖点、套路关键词、主角 archetype、金手指、节奏和文风
- 查看任务阶段进度
- 查看项目级 workflow 跟踪，直接看到每条 workflow、每个 step 是否完成
- 查看项目结构、故事圣经、叙事图谱
- 直接预览 `project.md` 和各章节产物
- 对待修订项目触发 `project repair`

启动方式：

```bash
./start.sh
./studio.sh
```

或：

```bash
make ui
```

默认会打开浏览器到本地页面。CLI 入口等价于：

```bash
./scripts/run.sh ui serve --open-browser
```

如果你需要指定端口：

```bash
./scripts/run.sh ui serve --host 127.0.0.1 --port 8895 --open-browser
```

页面背后走的是真实系统流水线，不是单独做了一套“演示页”逻辑。它调用的是当前项目里的：

- `run_autowrite_pipeline`
- `run_project_repair`
- `build_project_structure`
- `build_story_bible_overview`
- `build_narrative_overview`
- `build_project_workflow_overview`

页面里生成整书后，真实产物仍然落在：

- `output/<project-slug>/project.md`
- `output/<project-slug>/chapter-001.md`
- `output/<project-slug>/project.docx`
- `output/<project-slug>/project.epub`
- `output/<project-slug>/project.pdf`

说明：

- 页面可以直接配置目标总字数和目标章节数。
- 页面左侧“本轮写作配置”就是当前项目实际使用的 `writing_profile` 中文输入面板。
- 页面中的“写作配置”标签会展示当前项目最终落库的 `writing_profile`。
- 页面中的“流程跟踪”标签会展示项目的 workflow 总览、最近状态以及每条 workflow 的 step 列表。
- 从系统能力上说，可以把目标字数设置到 `1000000` 甚至更高。
- 但真实模型成本、运行时间、长篇稳定性会随体量线性上升，所以当前更推荐按“卷/阶段”推进，而不是第一次就直接跑千万字整书。
- 更稳的做法是先用页面生成 `3k/12k/50k` 级样书验证风格，再逐步放大。

如果你要从系统外部直接看流程数据，也可以调：

```bash
curl http://127.0.0.1:8895/api/projects/<project-slug>/workflow
```

返回里会包含：

- `run_count`
- `completed_run_count`
- `failed_run_count`
- `latest_run_status`
- 每条 workflow 的 step 详情

## Gemini 试用

先在项目根目录准备 `.env`：

```bash
cp .env.example .env
```

把下面两项填进去：

```bash
BESTSELLER_LLM_PROVIDER=gemini
GEMINI_API_KEY=你的-gemini-api-key
```

也可以不用 `GEMINI_API_KEY`，直接填 `GOOGLE_API_KEY`。如果两个都存在，启动脚本会优先使用 `GOOGLE_API_KEY`。

然后启动环境：

```bash
./start.sh
./scripts/run.sh status
```

如果切换成功，`status` 里应至少看到：

- `llm_mock: false`
- `llm_writer_model: openai/gemini-2.5-flash`
- `llm_writer_api_base: https://generativelanguage.googleapis.com/v1beta/openai/`

当前 Gemini 预设走 Google 官方 OpenAI-compatible endpoint，先统一用 `gemini-2.5-flash` 跑通整条链路，后续再按质量和成本拆分 planner/writer/critic 模型。

第一次试跑一整本，建议先用小体量：

```bash
./scripts/run.sh project autowrite demo-story "Demo Story" sci-fi 22000 4 --premise "一名被放逐的导航员发现帝国正在篡改边境航线记录，并被迫在追杀中揭穿真相。"
./scripts/run.sh project structure demo-story
./scripts/run.sh story-bible show demo-story
./scripts/run.sh project review demo-story
ls -la output/demo-story
```

重点看这些产物：

- `output/demo-story/project.md`
- `output/demo-story/chapter-001.md`
- `output/demo-story/project.docx`
- `output/demo-story/project.epub`
- `output/demo-story/project.pdf`

`project autowrite` 现在默认会：

- 在终端 `stderr` 实时打印阶段、章节、repair 和产物路径
- 在第一次项目级一致性审校未通过时，自动执行一轮 `project repair`
- 在最终 JSON 中返回 `repair_attempted`、`repair_workflow_run_id`、`output_dir`、`output_files`、`export_status`

如果你只想要纯 JSON 而不看阶段日志，可以显式关闭：

```bash
./scripts/run.sh project autowrite demo-story "Demo Story" sci-fi 22000 4 --premise "一名被放逐的导航员发现帝国正在篡改边境航线记录，并被迫在追杀中揭穿真相。" --no-progress
```

如果你不想自动执行 repair，可以显式关闭：

```bash
./scripts/run.sh project autowrite demo-story "Demo Story" sci-fi 22000 4 --premise "一名被放逐的导航员发现帝国正在篡改边境航线记录，并被迫在追杀中揭穿真相。" --no-auto-repair
```

## 小说质量与提示词策略

这轮还把规划和写作提示词补成了更偏“商业连载小说”的方向，重点不再是泛化创作，而是更接近平台可读性：

- 先交代 `reader_promise`，再展开世界观
- 前三章必须建立主角差异化、短期目标和追读钩子
- 背景信息必须嵌进行动、交易、对抗和结果里释放
- 每章末尾都要制造继续阅读的压力
- 审校时会显式检查平台适配、卖点兑现、hook 强度和“成品网文感”

更完整的研究说明见：

- `docs/novel-framework-research-and-proposal.md`
- `docs/prompt-engineering-strategy.md`
- `docs/novel-writing-configuration-research.md`
- `docs/prompt-pack-design.md`

## Quick Start

```bash
make install
make test
make run ARGS="status"
```

初始化数据库：

```bash
make run ARGS="db init"
make db-upgrade
```

创建项目：

```bash
make run ARGS="project create my-story '我的故事' fantasy 330000 60"
```

导入规划产物：

```bash
make run ARGS="planning import my-story book_spec --file examples/planning/book_spec.json"
make run ARGS="planning import my-story world_spec --file examples/planning/world_spec.json"
make run ARGS="planning import my-story cast_spec --file examples/planning/cast_spec.json"
make run ARGS="planning import my-story volume_plan --file examples/planning/volume_plan.json"
make run ARGS="planning import my-story chapter_outline_batch --file examples/planning/chapter_outline_batch.json"
```

先把故事圣经物化成可执行对象：

```bash
make run ARGS="workflow materialize-story-bible my-story"
```

也可以让系统直接自动生成全套规划：

```bash
make run ARGS="planning generate my-story --premise '一名被放逐的导航员发现帝国正在篡改边境航线记录。'"
make run ARGS="planning list my-story"
make run ARGS="planning show my-story book_spec"
```

把大纲物化为章节和场景：

```bash
make run ARGS="workflow materialize-outline my-story --file examples/planning/chapter_outline_batch.json"
```

运行完整项目流水线：

```bash
make run ARGS="project pipeline my-story --materialize-story-bible --materialize-outline"
make run ARGS="project review my-story"
make run ARGS="project repair my-story"
make run ARGS="project structure my-story"
make run ARGS="story-bible show my-story"
make run ARGS="workflow materialize-narrative-graph my-story"
make run ARGS="workflow materialize-narrative-tree my-story"
make run ARGS="narrative show my-story"
make run ARGS="narrative tree-show my-story"
make run ARGS="narrative path-show my-story --path /chapters/001/contract"
make run ARGS="narrative search my-story --query '主线 真相 调查' --path /chapters/001 --path /arcs/main-plot"
make run ARGS="scene context my-story 2 1"
make run ARGS="chapter context my-story 1"
make run ARGS="canon list my-story"
make run ARGS="timeline list my-story"
make run ARGS="retrieval search my-story --query '主角 当前目标 真相'"
```

也可以按章节或场景运行：

```bash
make run ARGS="scene pipeline my-story 1 1"
make run ARGS="chapter pipeline my-story 1 --export-markdown"
```

也可以拆开执行单步命令：

```bash
make run ARGS="scene draft my-story 1 1"
make run ARGS="scene draft my-story 1 2"
make run ARGS="chapter assemble my-story 1"
make run ARGS="chapter review my-story 1"
make run ARGS="chapter rewrite my-story 1"
make run ARGS="chapter context my-story 1"
make run ARGS="scene review my-story 1 1"
make run ARGS="scene rewrite my-story 1 1"
make run ARGS="rewrite cascade my-story --chapter-number 1 --scene-number 1"
make run ARGS="export markdown my-story --chapter-number 1"
make run ARGS="export markdown my-story"
make run ARGS="export docx my-story"
make run ARGS="export epub my-story"
make run ARGS="export pdf my-story"
```

如果你使用一键脚本，建议改用：

```bash
./scripts/start.sh
./scripts/run.sh status
./scripts/run.sh project autowrite demo-story "Demo Story" sci-fi 22000 4 --premise "一名被放逐的导航员发现帝国正在篡改边境航线记录。"
./scripts/verify.sh
./scripts/stop.sh
```

## Benchmark & Evaluation

当前内置了一套样书评测套件，覆盖三类基线：

- 末日囤货
- 玄幻升级
- 都市悬疑

先列出当前可用 suite：

```bash
./scripts/run.sh benchmark list
```

运行整套 benchmark：

```bash
./scripts/run.sh benchmark run sample-books --slug-prefix bench
```

只跑一个 case：

```bash
./scripts/run.sh benchmark run sample-books --case doomsday-hoarding --slug-prefix bench
```

运行结果会输出结构化 JSON，并把报告写到：

```bash
output/benchmarks/
```

每个 case 会检查这些最小基线：

- 是否完成整书 autowrite
- 是否生成 `project.md`
- 项目级评分是否达到阈值
- 是否包含要求的叙事线类型
- 是否生成 emotion tracks / antagonist plans

另外，scene/chapter review 现在已经会显式对照 `chapter contract / scene contract` 做偏差审校；即使最终仍需人工复核，也会先导出当前草稿，便于人工判断问题落点。

## Core Docs

- [当前状态与后续路线](docs/current-status-and-roadmap.md)
- [PageIndex 集成评估与叙事架构路线](docs/pageindex-integration-and-narrative-roadmap.md)
- [走向更完整长篇系统的 TODO](docs/perfect-novel-todo.md)
- [架构设计](docs/architecture.md)
- [数据库方案](docs/database-schema.md)
- [Prompt 设计](docs/prompt-engineering-strategy.md)
- [开源框架调研与总体方案](docs/novel-framework-research-and-proposal.md)
