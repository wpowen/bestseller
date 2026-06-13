# 修仙长篇正文前准备包基准——评估报告（2026-06-12）

> 规格与原创 premise：docs/xianxia-benchmark-spec-20260612.md
> 试跑项目：`shilouyan-bench-v1`《蚀漏砚》仙侠 / 500 章 / 110 万字 / xianxia-upgrade-core pack
> 模型：planner=MiniMax-M2.7-highspeed（生产同款）；纯规划，未生成任何正文
> 工具：scripts/benchmark_structural_check.py（S1-S6 程序化核对，可复用）

## 一、框架能力审计结论（试跑前）

**管线**：premise → book_spec → world_spec → cast_spec →（kernels）→ volume_plan → chapter_outline_batch（每批10章，卷内多批+跨批台账）→ materialize（story-bible/章/场景卡/narrative graph）→ 写手上下文包。纯规划入口 `bestseller planning generate`，CLI 直连 PG，无需 web/worker。

**schema 支持度速评**（对 10 卷/500 章修仙史诗）：
- 章纲 ChapterOutlineInput：极强（情绪/钩子/因果契约/信息控制/场景 entry/exit 链）。
- 卷纲 VolumePlanEntryInput：强（卷目标/障碍/高潮/伏笔 planted+paid_off/下卷钩子）。
- 伏笔：narrative 层有 ClueRead/PayoffRead（plant 章位→预期回收章位→实际回收章位），但卷纲层 planted/paid_off 是**互不关联的自由文本列表，无 ID 闭环**。
- 修炼体系：PowerSystemInput 有结构化 tiers/protagonist_starting_tier，但见 P-1。
- 势力演化：FactionInput 全静态字段，**无跨卷状态时间轴**（登记册 R7 同族）。

## 二、试跑前已坐实的根因（代码级证据）

| # | 问题 | 证据 | 影响指标 |
|---|---|---|---|
| P-1 | world_spec prompt 只列出 `power_system` 字段名，从不说明其子结构（tiers/acquisition_method/hard_limits/protagonist_starting_tier）→ 模型恒输出自由文本，校验器把整段塞进 `name`，`tiers` 恒空。**级联**：plan_judge `_check_power_tier_escalation`（plan_judge.py:263）假定 dict，遇 str 直接 AttributeError 被捕获跳过——仙侠境界递进校验判官整体失效（试跑实证 traceback） | planner.py:12872-12893 prompt 全文；v4 与本基准 world_spec 实物均为 str；tmp/shilouyan-planning-run.log:83 | S1 升级体系一致性（数据缺口+校验判官双失效） |
| P-2 | cast prompt 对 supporting_cast 的结构契约只要求 {name,role,active_volumes,relationship_to_protagonist,evolution_arc}，**不含动机字段** → goal/flaw 恒 null（v4 实测 0/18，prompt 要求的字段 18/18 全填） | planner.py:13217-13224 | S4 角色动机 |
| P-3 | 确定性兜底卷计划把指令文本当数据填入：`foreshadowing_planted=["埋下一条必须在第N+1卷继续发酵的未解变量。"]`、`reader_hook_to_next="眼前压力虽然变形或后撤，但故事还不能停下来。"`。**漏入机制**：`_generate_structured_artifact(merge_fallback=True)` 经 `_merge_planning_payload`（planner.py:768-784）把兜底值按 key **静默回填进 LLM 产物的缺失字段**——LLM 成功也会漏模板（v4 卷1实物含该模板句即此路径） | planner.py:9331-9373（脚手架）+ 768-784（merge） | S3 伏笔 / S5 卷级节奏（R9 死字段根源之一） |
| P-4 | R4 premise 名册直通的覆盖缺口：标记词表无「关键配角」等自然表述；频次兜底要求出现≥3次+首字在常见姓表 → premise 中点名一次的配角（宋拾/关铎/裴萤/白杪）全部丢失，模型自造新配角顶替；仅出现4次的主角谢迟被锁 | planner.py:4162-4304；本基准 cast_spec 实物 | S4 / 设定保真 |
| P-6 | 卷纲验收不校验章数覆盖率：500 章目标、hierarchy 应切 10 卷，LLM 输出 5 卷×50 章=250 章即通过入库——**半本书无规划**。同时全部 5 卷 reader_hook_to_next=None（list 型 fallback 不做 per-item merge，模板没漏入但空洞也没补） | 本基准 volume_plan 实物 | 卷级节奏/长篇连贯性（最大规模缺口） |
| P-7 | 境界词汇污染无校验：卷纲中「元婴」出现 25 次（题材通用词库惯性），与 world_spec 原创七境（引气~问衡）矛盾共存；本应拦截的 power_tier_escalation 判官恰因 P-1 崩溃跳过 | 本基准 volume_plan vs world_spec 实物比对 | S1 升级体系一致性 |
| P-5 | world_spec 体量与 planner max_tokens 冲突：500 章触发 world-richness 规模化约束（要求大世界观），首次输出 14479 字符即 finish_reason=length 被拒，重试 2 次后才入库 | artifacts/planner_failures/20260612T1457*_world_spec_*；planner.py:12916 注入规模化块 | 规划吞吐/稳定性 |

（注：P-1/P-2/P-3 均为「模型只填 prompt 契约里有的字段」同一规律的三个切面——schema 很富，prompt 输出契约没跟上。）

## 三、端到端试跑记录

- 项目创建 + planning generate 启动：2026-06-12 ~14:55，日志 tmp/shilouyan-planning-run.log
- hook_strength_gate 自动降级放行（R1 修复生效）；book_spec 主角名漂移自修复 2 处（R2 族修复生效）
- world_spec：3 次尝试入库（截断→SCHEMA_LIST_INVALID→成功）；势力10/规则30/地点15，丰富度达标；power_system 自由文本（P-1 复现）
- cast_spec：v1 后 personhood 闸门查出 15 项缺陷自修复出 v2；relationship-scaling 修复未收敛（critical 1→1 保留原稿）；配角仅 3 人且 premise 名册丢失（P-4 复现）
- kernels：compliance_boundary / public_emotion 入库；story_design_kernel schema 校验多轮重试
- （volume_plan / chapter_outline_batch 结果待填）

## 三.5、试跑最终记录（2026-06-13 02:30 完成采集）

- 总耗时约 3.5h（foundation+kernels ≈2h；卷一/卷二章纲 ≈1.5h）；37 个 planning artifact；章纲批次 17 次尝试（首次通过率≈0%，全部进 1-3 轮修复指令重生成）；卷三至卷五为控制成本在采集足够证据后主动中止（卷纲全 5 卷已存，章纲样例 100 章已存）。
- 卷一章纲商业判官 0.340 不及格（codes：CONTINUITY_DUPLICATE_CHAPTERS / LOGIC_DUPLICATE_INCOMPATIBLE_STATES / SCENE_DELTA_NO_PROGRESS / METHODOLOGY_FIRST_PARAGRAPH_FAIL / CONSTRAINT_SCENE_CARD_ABSTRACT / KNOWLEDGE_BOUNDARY_VIOLATION / REVERSAL_NO_FORESHADOWING），卷二 0.580 不及格——两卷均**放行入库，findings 无消费者**（R12 规划层只拦不修的实证）。
- 物化：100 章 / 209 场景卡 / narrative graph（线索 18、回收 3、情绪轨 12、契约 100+209）。卷纲登记 37 植/17 收 → 物化后仅 18 线索/3 回收（**约半数伏笔在 volume_plan→narrative graph 物化时丢失**）。
- 标题撞名确定性去重生效 ×2（R16）；跨批次台账生效（无 v3 式同弧线×6）；hook 闸门自动降级放行（R1）；批次必填字段 0 缺失（R6 修复跨题材复现有效）。
- 产物落盘：output/shilouyan-bench-v1/（obsidian-vault 58 文件 + context-pack/ 场景上下文 JSON×2 + 写手 prompt 全量 trace×2 + 合并章纲）。

## 四、八维度评估（S1-S8，程序化核对 + 人工复核）

| 维度 | 判定 | 证据 |
|---|---|---|
| 升级体系一致性 (S1) | **不达标** | power_system 自由文本、tiers=0；境界校验判官因 P-1 崩溃跳过；卷纲「元婴」×25 与原创七境矛盾共存（P-7）无任何拦截 |
| 势力演化 (S2) | **达标（带缺口）** | 10 势力入册、5 卷主导势力 5 种轮换；但 schema 无跨卷势力状态时间轴（R7），演化只存在于自由文本 |
| 伏笔回收 (S3) | **部分达标** | 37 条具体伏笔登记（内容质量好，人工抽样卷1植→卷2/3收语义闭环成立）；但无 ID 闭环、机器不可校验、跨度≥3卷的大伏笔受 5 卷计划压缩限制、物化丢失约半数 |
| 角色动机 (S4) | **不达标** | 主角动机链强（卷主题/章 goal 100% 填充且具体）；配角仅 3 人（500 章规模）、有动机无弧线；premise 点名的 4 配角全部丢失（P-4） |
| 卷级节奏 (S5) | **部分达标** | conflict_phase 5 卷轮换、卷主题脊柱强、无连续同构（R8 风险未现）；但 reader_hook_to_next 5/5 空缺、卷计划只覆盖 250/500 章（P-6） |
| 章级可写性 (S6) | **基本达标** | 100 章必填字段（opening_situation/main_conflict/target_emotion/hook_type/causal_contract/goal）缺失 0；hook_type 12 式分布健康；场景 participants≥2 仅 82%（38 独角戏）低于 90% 线 |
| 正文前置条件完整性 (S7) | **达标** | 写手 prompt 实测含 progression/rule_system/faction_ecology/entry_system 上下文块；ch55 注入 6 条未回收线索；世界观专名（引气/问衡/蚀漏砚/衡山院）在 prompt 在场；user prompt ~15-16k token（重但完整） |
| 长篇连贯性 (S8) | **部分达标** | 跨批次台账有效（无 v3 同弧线×6 灾难）；残余近重复节拍簇 ~10 处（涉及 ~15% 章节），商业判官能抓到但 findings 无修复通道（R12）；target_emotion 分布失衡：紧张38/悬疑26/震撼21 vs 爽4/燃4——仙侠爽文的情绪工程缺题材配比约束 |

**总体判定：当前框架不能稳定产出达标的修仙长篇正文前准备包。** 八维度 2 达标 / 3 部分达标 / 3 不达标。内容语义质量普遍好于结构化质量——瓶颈集中在「输出契约不完整（P-1/P-2/P-6）、脚手架污染（P-3）、验收不闭环（R12/P-6）」三类工程问题，而非模型能力。

## 五、对照组：zhaoshen-hr-v4（人工重度修补的生产书）同口径核对

S1 FAIL（power_system 自由文本）/ S2 PASS（26 势力，卷际主导力量 7 种）/ S3 FAIL（51 条伏笔登记但 plant→payoff 无一可匹配闭环）/ S4 FAIL（18 配角 goal 0/18）/ S5 FAIL（1 条模板钩子句）/ S6 PASS（50 章必填字段 0 缺失，participants≥2 比率 91%）——说明 S1/S3/S4 是**框架系统性缺口**而非单书事故。

## 六、修复轮记录（最多3轮）

### 轮 1：P-1 升级体系输出契约（S1 主瓶颈）✅
- 修改：① `planner.py::_world_spec_prompts` 中英双语补 power_system 结构化输出契约（tiers 有序列表/acquisition_method/hard_limits/protagonist_starting_tier，注明下游消费方）② `plan_judge.py::_check_power_tier_escalation` 对 str 形态 power_system 做与 WorldSpecInput 同语义的 coercion——自由文本不再炸判官，而是命中"层级不足"真实 finding。
- TDD：tests/unit/test_power_system_contract.py 3 测（RED 复现 AttributeError → GREEN）；test_plan_judge.py 10 测回归绿。
- 真模型最小验证（1 次 planner 调用，同 premise）：power_system 返回 dict，tiers=['引气','通脉','凝府','丹枢','洗象','照神','问衡'] 7 境有序，protagonist_starting_tier/acquisition_method 充实，WorldSpecInput 解析 tiers=7。**S1 由不达标转为可达标**（存量书需重生成 world_spec 才受益）。

### 轮 2：P-6 卷纲覆盖契约 + P-5 输出预算使能（卷级规模主瓶颈）✅
- 根因补全：①卷纲 prompt 从未告知模型"要几卷、覆盖多少章"②`_planner_stage_max_tokens` 把 volume_plan 硬编码 8192 token——10 卷计划（生产实测 ~18k 字符）物理放不下，截断重试后收缩成 5 卷是必然产物。
- 修改：① prompt 注入【硬性契约】行（恰好 N 卷/章数总和=全书/非末卷钩子必填）② `story_bible.py::enforce_volume_plan_contract`（VOLUME_COUNT_MISMATCH/VOLUME_CHAPTER_COVERAGE_SHORT/VOLUME_HOOK_MISSING 三码，接入 `_volume_plan_validator_for` 只挂两个全量生成位点，repair 位点不挂防修复死锁）③ `_planner_stage_max_tokens` 对 volume_plan 族按卷数伸缩 `min(32768, max(8192, 卷数×2560))`（带 project 上下文才生效，遗留调用行为不变）。
- TDD：tests/unit/test_volume_plan_contract.py 7 测；对真实失败样本（基准书 5 卷计划）确定性验证三码全中。
- 真模型验证（1 次调用，同书 foundation）：cap=25600，finish_reason=stop（无截断），**一次产出 10 卷/500 章全覆盖/钩子 10/10 非空/契约一次通过**——对照试跑时多轮截断重试后的 5 卷半书。

### 轮 3：P-3 兜底脚手架去污（数据完整性瓶颈）✅
- 修改：`_fallback_volume_plan` 的 foreshadowing_planted/paid_off 不再填指令文本（改空列表）、reader_hook_to_next 去模板句（无里程碑时为空串）——配合 merge 回填机制，"假内容"被"诚实的空"取代，空值会被轮 2 契约/伏笔规模化闸门抓住，而套话能穿透所有检测。
- TDD：tests/unit/test_fallback_volume_plan_no_scaffold_leak.py 2 测（RED→GREEN）。
- 全量回归：**6599 passed, 3 skipped**（含 planner/volume/judge/story_bible 全家族）。

## 六.5、缺口修复记录 G1-G4（2026-06-13，第二轮目标）

> 三轮修复（P-1/P-3/P-5/P-6）已由用户经 Cursor commit（A1-A5=cb4cf45，三轮+文档+工具=ed842a3）。本节为 G1→G4 顺序落地，每项 RED→GREEN+真实样本验证。

### G1：cast 配角动机契约 + 名册下限/输出预算随卷数伸缩 ✅
- 根因（P-2）：supporting_cast 结构契约只列 5 字段（无 goal/flaw）；「紧凑输出合同」硬编码"3-5 名"与 relationship-scaling 块（10 卷 floor=15）互斥，模型服从小数，500 章史诗只产 3 配角且 scaling 修复无法收敛。
- 修改：① `_cast_spec_prompts` supporting_cast 结构加 goal（独立动机）/flaw（双语）；②「紧凑输出合同」3-5 改为 `compute_supporting_bounds(卷数)` 伸缩值（10 卷→15-30）；③ `_planner_stage_max_tokens` 对 cast_spec 加 cap 伸缩 `ceiling*600+2000`（短书回落 8192，10 卷→20k）——否则 23 配角带完整字段（实测 12615 tok）会被 8192 截断，G1 契约白做。
- TDD：tests/unit/test_cast_motivation_contract.py 6 测。
- 真实样本（v1 foundation + 新 prompt 单次）：**旧 3 配角缺 arc → 23 配角，goal/flaw/arc 全 23/23 填充**，finish_reason=stop。
- 端到端（v4 全集重跑，完整管线含 identity-lock+repair）：supporting_cast **25 个**，goal/flaw/evolution_arc/relationship 全 **25/25**。

### G2：premise 自然语态名册抓取 ✅
- 根因（P-4）：标记词表无「关键配角」等自然表述；嵌入式命名（「孤女宋拾」人名在描述短语尾部）被只认 2-3 字独立 run 的旧逻辑漏掉，cast planner 自造配角顶替。
- 修改：① `_PREMISE_ROSTER_MARKERS` 加「关键配角/主要配角/核心配角/重要配角/主要人物/核心人物」族（中英）；② 新增 Pass 1.5 `_name_from_phrase_tail`——marker 段内按「、，；」分割短语，去括号，取最后 CJK run 的姓锚定 2-3 字尾名；只在 marker 段内运行，普通叙述不挖。
- TDD：tests/unit/test_premise_roster_extraction.py 4 测。
- 真实样本（蚀漏砚完整 premise）：**宋拾/关铎/裴萤/白杪 4/4 全锁 + 主角谢迟锁定，零 role/aside noise**（试跑时这四个全丢失）。
- 端到端（v4 全集重跑）：四配角全部进入 cast_spec 且排 supporting_cast 前四位（premise 名册直通生效）。

### G3：卷纲伏笔 seed 标签闭环 + 物化按 seed 建链 ✅
- 根因（实证修正认知）：物化 18 clue ≠「37→18 丢一半」（v1 只物化卷 1-2，18=两卷 planted 数，正常）；**真缺陷是 `_match_clue_code` 文本子串匹配关联 payoff→clue**——LLM plant/payoff 措辞不同则匹配失败。DB 实证：v1 **payoff source_clue 0/3 linked、clue 0/18 resolved**，伏笔从不闭环。
- 修改：① 卷纲 foreshadowing 约束块（EN+ZH）要求每条 planted 以全书唯一 `[S<n>]` 开头、paid_off 以同一 `[S<n>]` 引用；② `_extract_seed_tag` 纯函数 + `_build_clues_and_payoffs` 用 `seed_to_clue_code`（跨卷累积）精确关联，无标签回退文本匹配（向后兼容）；③ payoff 关联后回写 clue status=resolved+actual_paid_off（闭环）；标签在 label 中 strip。
- TDD：tests/unit/test_foreshadowing_seed_linkage.py 4 测。
- 真实样本（v1 foundation + 新 prompt 单次）：**31 planted 全带唯一 seed 标签、26 paid_off 26/26 链接到已 planted 的 seed**（对比 v1 基线 0/3）。
- 边界：只对新书生效（新 prompt 产 seed），不回填迁移老数据（合目标边界）。

### G4：章纲商业判官 findings 同一次运行内回灌重生成 ✅
- 根因（R12 规划层重演）：判官 fail 后仅 `_attach_prewrite_quality_report` 记录元数据即存 artifact；`_commercial_repair_directives` 只从 `metadata` 读（靠外层 heal 的**下一次** generate 填充），单次 planning generate 内从不重生成——卷一 0.340 直接入库。
- 修改：① `_run_planner_outline_commercial_judge` 返回 payload 加 `repair_directives`（fail 时 `build_outline_repair_directives(result)`）；② 新增纯函数 `_outline_judge_repair_directives`（pass/轮次耗尽/无 directives 时停，防死循环）；③ 章纲生成调用点重构为 generate→judge→bounded-repair 循环，fail 时把 directives 追加 base_constraints **在同一次运行内**重生成（默认 1 轮，`outline_commercial_judge_repair_rounds` 可配）。
- TDD：tests/unit/test_outline_judge_inloop_repair.py 5 测。
- 端到端：待 v4 重跑到卷一章纲验证（判官 fail→in-run repair round 日志 + 分数提升）。

## 七、达标判定与剩余缺口

### 达标判定
**修复前：不达标**（8 维度 2 达标/3 部分/3 不达标）。**修复后（组件级验证）**：S1（结构化七境+判官复活）、S5/卷覆盖（10卷500章一次过契约）已转为可达标；S6/S7 本就基本达标。**端到端整书重跑未执行**（成本考量，三轮均以"真实失败样本拒收+单次真模型调用通过"做最小可证验证）——结论：框架经此三轮修复**具备产出达标修仙长篇准备包的能力**，但需一次完整重跑（约 3-4h/卷一+卷二）做最终确认。

### 剩余缺口（按优先级，含应改模块/推荐改法/验证方式）

**G1-G4 均已落地**（详见 §六.5：代码+RED→GREEN 单测+真实样本验证）。下表为剩余缺口 G5-G9。

| # | 缺口 | 证据 | 应改模块 | 推荐改法 | 验证方式 |
|---|---|---|---|---|---|
| ~~G1~~ | ✅ 配角动机契约+名册/预算伸缩 | 见 §六.5 | — | — | 23 配角 goal/flaw/arc 23/23 |
| ~~G2~~ | ✅ premise 自然语态名册抓取 | 见 §六.5 | — | — | 宋拾/关铎/裴萤/白杪 4/4 |
| ~~G3~~ | ✅ 伏笔 seed 标签闭环+物化建链 | 见 §六.5 | — | — | 26/26 payoff 链接（基线 0/3） |
| ~~G4~~ | ✅ 章纲判官 in-run repair 通道 | 见 §六.5 | — | — | 5 单测；端到端待 v4 |
| G5 | 势力演化无结构化时间轴（R7 族） | FactionInput 全静态字段 | domain/story_bible.py FactionInput / VolumePlanEntryInput | 卷纲加 faction_state_deltas:[{faction,change}]（轻量），物化进 world_state | 结构核对 S2 升级为逐卷状态变化判定 |
| G6 | 境界词汇污染检测缺失（P-7）：「元婴」混入原创七境无拦截 | 基准书卷纲实物（25 处） | plan_judge 或新 soft 闸门 | 用 world_spec.tiers 白名单扫卷纲/章纲中境界类词（题材通用境界词表做黑名单源），词表外境界词报 warning | 对基准书卷纲跑出 25 处命中 |
| G7 | target_emotion 题材配比无约束：仙侠爽文 100 章中爽4/燃4 vs 紧张38 | 基准书章纲分布统计 | webnovel_method_cards.yaml + planner A2 注入 | 方法卡加题材→情绪配比建议表，批次验收做分布偏离 warning | 重跑卷一统计分布 |
| G8 | 独角戏场景 18%（82%<90% 线） | 209 场景卡统计 | outline_field_enrichment（R6 同通道） | participants<2 时从章纲 participants/势力名册确定性补全 | 结构核对 S6 |
| G9 | M2.7 思考 token 与产出共享 max_tokens 的预算学（P-5 残余）：outline 批次仍会截断（批次从 10 收缩到 5/3） | 试跑 17 次 outline 尝试、截断修复日志 | llm.py / 各 stage cap | 为 reasoning 模型在 stage cap 上加思考预留系数，或 MiniMax 侧 reasoning 单独计费参数 | 重跑卷一统计批次截断率 |

### 下一步优先级建议（更新于 2026-06-13，G1-G4 落地后）
1. **G6 境界词汇污染检测**（半天）：用 world_spec.tiers 白名单扫卷纲/章纲，「元婴」等题材通用词混入原创七境时报 warning——升级体系一致性的直接守卫，与 G1-G4 同属"原创设定保真"主线。
2. **G5 势力演化时间轴**（1 天）：FactionInput 加逐卷 state_deltas，把 S2 从"静态入册"升级到"演化可核对"。
3. **G7 情绪配比约束**（半天）：webnovel_method_cards 加题材→情绪配比建议表，批次验收做分布偏离 warning（仙侠爽文不应紧张 38/爽 4）。
4. **G8 独角戏补全 + G9 reasoning 预算系数**（各半天）：R6 同通道确定性补全 participants；为 M2.7 思考 token 在 stage cap 加预留系数，降 outline 批次截断率。

### 运行记录（可复现）
- premise/指标：docs/xianxia-benchmark-spec-20260612.md；tmp/shilouyan-premise.txt
- 试跑日志：tmp/shilouyan-planning-run.log；失败样本 artifacts/planner_failures/20260612T14*-15*_shilouyan-*
- 产物：DB 项目 shilouyan-bench-v1（37 planning artifacts）+ output/shilouyan-bench-v1/（obsidian-vault 58 文件、context-pack/ 上下文+写手 prompt trace、outline-merged-vol1-2.json）
- 工具：scripts/benchmark_structural_check.py（S1-S6 程序化核对，--outline-file 支持）
- 测试：tests/unit/test_power_system_contract.py / test_volume_plan_contract.py / test_fallback_volume_plan_no_scaffold_leak.py（12 新测）；全量 6599 passed
