# 设计：让正文在门禁范围内真实达标 — 验收契约闭环

日期：2026-06-11
背景：`docs/market-research-500ch-run-20260611.md` 跑书实验，10/10 章 strict acceptance 失败、全部 machine_blocked。
设计目标：**不放宽任何 critical 门禁**，让生成正文真实通过验收；同时把每章的 LLM 成本从"4 轮全场景重写"降到接近"1 轮初写 + 定向微修"。

---

## 1. 深层根因模型

10/10 失败不是写手能力不足，而是**生产（写手 prompt）— 验收（门禁）— 修复（repair）三方契约断裂**。同一份质量要求，三方各拿着不一致的版本：

### 断裂 ①：验收标准存在，但从未传达给生产侧

| 门禁 | 验收标准（代码事实） | 写手拿到了什么 | 证据 |
|---|---|---|---|
| `ENDING_HOOK_MISSING` | 最后 120 字含硬编码词表（？/什么/突然/忽然/裂开/倒计时…） | **什么都没有**。ending hook 是章级概念，场景管线从未把"你是末场景，结尾必须收钩"分解给任何场景 | deterministic_post_write_audit.py:213；scene card 无 ending_hook 字段（infra/db/models.py:639） |
| prewrite 时间预算 | plan 提及旅行词（骑车/路上/赶路…）且不在 `allowed_elapsed_events` 白名单 → 拒绝；**白名单为空时恒拒绝** | prompt 只有一句"时间消耗必须先登记"，白名单内容从未展示；重试反馈也不附白名单 | chapter_constraint_manifest.py:678-690, 910-928, 1258；drafts.py:9241 |
| `NAMING_OUT_OF_POOL` | 正则抓"百家姓+1-2字"全量比对名字池 | prompt 只注入池子**前 10 个名字**（`seed_pool[:10]`）；regen 反馈只列违规名、不给替代 | prompt_constructor.py:265-270；output_validator.py:930-1001 |
| persona 各 channel | weighted ≥0.62，由 hook_density/payoff_density/emotion 等 7 通道确定性关键词计数加权 | 修复剧本只有"同时提升节奏、冲突、情绪"类空泛指令，无通道级数值目标 | reader_persona_simulator.py；quality_repair_playbooks.py:213-230 |

### 断裂 ②：传达了，但生产自由度与验收口径不一致

- 招牌场景指令明确写着台词"**择一改写**出现"（signature_scene_planner.py:234），验收却是 **exact substring**（retention_safety_gate.py:253 `if hint in chapter_text`）。写手照指令改写同义台词 → 必判 `SIGNATURE_SCENE_MISSING` critical。指令亲手教写手怎么挂科。
- mandate 锚词字典是硬编码的 10 原型 × 3 意象（"被揭开的封印/灯下旧账/尘封玉牌"），**探案/仙侠味**。对本次"都市公务员修仙"题材完全错位，写手大概率把这段指令当噪声忽略——题材失配让逐字命中概率进一步归零。
- 语义判官兜底（signature_scene_critic.py:100）只查通用 archetype 词（"真相/原来/答案"），不验 mandate 的具体 stake，松紧两层都失准：literal 层过脆、semantic 层过松且不输出"差在哪"。

### 断裂 ③：修复不定向，毁掉已达标的工作

- auto-repair 无差别重置所有场景为 NEEDS_REWRITE（drafts.py:11289），包括 verdict=pass 的场景。**实证：ch9 初稿 persona weighted=0.80 / abandon=0.06，retention 通过，仅差 `SIGNATURE_IMAGE_MISSING`+`ENDING_HOOK_MISSING` 两个定点问题；3 轮全章重置后修成 splice/hook 失败，终态 blocked。** 一章本可用 ~500 token 定点补结尾，实际烧掉 12 个场景管线仍失败。
- 章级 hint 广播给所有场景（drafts.py:11116）：「结尾要有钩子」被注给中间场景，中间场景永远无法满足。
- repair 目标 = 历史 `auto_repair_last_block_codes` ∪ 最新发现（drafts.py:11370），无"上一轮的码是否已解决"判定 → blocker 漂移、永不收敛。
- rewrite stall（delta≈0.00）同根：review 评分是关键词启发式，反馈无定位无动作，重写改善语义但分数不动，3 review/2 rewrite 全走满。

### 断裂 ④：确定性可修的问题用 20k token 重写

`NAMING_OUT_OF_POOL`（违规名→池内映射/称谓化是纯文本替换）和 `pronoun_mismatch`（检测器已精确定位名字±60 字窗口）都可确定性修复，现状却各自触发全场景 regen（每次 ~20k input token），是整个跑书最大的 token 黑洞，且 repair 轮内反复复发。

### 断裂 ⑤：刻度单点校准，无题材/模型档分布

persona 阈值 0.62 的依据只有两个单点：自产坏章 exorcist_ch003=0.516（读者评"白看一章"）与 commercial_pass=0.72（reader_persona_calibration.py:16）。没有真实榜单语料分布、没有按题材/写手模型档的阈值（judge 体系已有"达标线绑模型档"机制，persona gate 未接）。**注意：ch9 实测 0.80 证明 0.62 对 MiniMax-M3 可达，本设计不动这条线**，但校准程序必须补上，否则换题材/换模型档时无法区分"写得差"与"刻度错"。

### 死锁放大器：软放行通道数学上不可达（独立 bug，仍需修）

retention 软放行（pipelines.py:7453）要求 `retention_retry_count > retention_max_retries(5)`，但外层 repair 循环 `chapter_auto_repair_max_attempts=3` 次就退出并在 pipelines.py:7565 硬路由 machine repair。3 < 6，软通道永不触发。本设计以"真实达标"为主，此 bug 作为**最终兜底**仍要修对（达标失败时按设计意图记 `low_retention_quality` 前进，而不是卡死全书）——兜底存在不等于依赖兜底。

---

## 2. 方案总体：ChapterAcceptanceContract 闭环

核心思想：**一份章级验收契约，编译一次，三方共用。**

```
                ┌─────────────────────────────────┐
                │  ChapterAcceptanceContract       │
                │  （写正文前编译，落库+落盘）       │
                └───────┬───────────┬─────────────┘
        ①分解到场景义务   │           │ ③同一契约驱动验收
                        ▼           ▼
   scene duty: opening_echo /   门禁逐条目验收
   body / ending_hook +         （锚词命中→语义判官→
   锚词/通道目标注入写手prompt     带证据的失败条目）
                        │           │
                        ▼           ▼ ④失败条目→定向patch
                  场景写手生成    span级微修复（不重置pass场景）
                        │           │
                        └─────►重验仅当轮失败条目──►达标
```

契约条目结构（每条目 = 验收点）：

```python
@dataclass(frozen=True)
class AcceptanceClause:
    code: str                 # 对应门禁 block code，如 ENDING_HOOK_MISSING
    target_scene: str         # "first" | "last" | "slot:<n>" | "any"
    target_span: str          # 如 "tail:120chars" / "head:300chars" / "body"
    anchor_tokens: tuple[str, ...]   # 逐字锚词（验收=substring 命中任一）
    semantic_fallback: str | None    # 语义判官的判定问题（锚词0命中时用）
    writer_instruction: str   # 渲染进对应场景 prompt 的指令（与验收同源生成）
    repair_action: str        # "rewrite_span" | "insert_after_evidence" | "text_substitute"
```

**生产承诺 = 验收标准 = 修复目标**，三者由同一条目派生，断裂 ①② 从结构上消除。

---

## 3. 分项设计

### 3.1 契约编译器（新增 `services/acceptance_contract.py`）

在章管线开头（scene contract 编译同阶段）汇集现有零散物料编译契约：

- **ending_hook 条目**：`target_scene=last, target_span=tail:120chars`，anchor_tokens 取自 deterministic_post_write_audit 的同一份词表（单一来源化：词表搬到契约层，audit 引用契约）。writer_instruction：「本场景是本章末场景：最后一段必须以未解问题或突发事件收尾，且显式包含问句或『突然/忽然』类转折词之一」。
- **hook_echo 条目**（ch≥2）：`target_scene=first, target_span=head:300chars`，anchor_tokens = `extract_hook_tokens(prev)` 的前 5 个高信号 token。writer_instruction：「开篇 300 字内逐字使用其中 ≥3 个，正面承接上一章结尾的未解问题」。覆盖率验收 0.5 线不动——5 个给 3 个命中即 0.6，稳过且语义自然。
- **signature 条目**（槽位章）：见 3.2 锚词协议。
- **persona 通道目标条目**：见 3.4。
- **length band / splice 约束**沿用现有 manifest，挂为契约条目以便统一报告。

契约持久化到 chapter metadata + `output/<slug>/design/`（接现有 design dossier 体系），评审可见。

### 3.2 锚词协议：mandate 重构（改 `signature_scene_planner.py`）

1. **锚词生成题材感知**：废弃硬编码 10×3 字典作为唯一来源，改为从该书的 material_library（scene_templates/plot_patterns，玄幻已有，复用 genre scene-bank 接入点）+ book_spec 核心意象中由 LLM 生成 3-5 个**具体名词性锚词**（如本书应产出"灵务局工牌/审批红章/巡查记录仪"级别的词），一次性生成、落盘、终身复用（书内一致性即招牌意象复现）。
2. **指令与验收对齐**：writer_instruction 从"择一改写"改为「以下锚词**至少一个逐字出现**，包装句式自由发挥」。锚词是具体名词时，逐字要求不伤文笔（名词天然该逐字）；台词类 hint 降级为风格参考、不参与验收。
3. **验收两层修正**：literal 层沿用 substring（现在能稳定命中了）；语义判官层（signature_scene_critic）加入 stake_markers 验证 + 失败时输出 `missing: 锚词列表 / 判定理由`，作为 repair 的定位证据。
4. `SIGNATURE_IMAGE_MISSING`（场景级）同协议：scene contract 的 signature_image 字段由契约编译器保证填充（平台路径自举，沿用 ensure_* 模式），写手指令已存在（drafts.py:5814），补的是字段生产与锚词具体化。

### 3.3 场景义务分工（改 scene contract 编译 + `build_scene_draft_prompts`）

- scene contract 增加 `chapter_duty: opening_echo | body | ending_hook` 字段；契约条目按 duty 路由到对应场景的 prompt 渲染（首场景拿 echo 条目、末场景拿 ending hook 条目、槽位场景拿 signature 条目）。
- `build_scene_draft_prompts()`（drafts.py:5468）新增 `acceptance_clauses` 入参，渲染为「本场景验收硬指标」块，置于 user prompt 的场景执行段（与 current_scene_contract_line 相邻）。
- 中间场景不再收到任何章级尾钩/开篇指令（消除指令噪声）。

### 3.4 persona：通道目标进 prompt + 通道反馈进 repair + 校准程序

**不动 0.62 阈值**（ch9 0.80 已证可达），改三件事：

1. **可操作目标前置**：契约编译时从 persona simulator 的通道定义反推写手指令（与爽文方法论同向，不是应试技巧）：
   - payoff：「本章至少 4 处把已立悬念落为可见结果（证据到手/对抗分胜负/关系变化/代价坐实），兑现时刻用明确的确认表述显式落地」——payoff_count sigmoid midpoint=2，≥4 进入高收益区；
   - hook：「章末+2 个转折点各留一个未解问题」（hook_density 权重覆盖 73% persona 人口）；
   - emotion：「冲突高潮处必须有主角的具身情绪反应」。
2. **通道级修复指令**：`PERSONA_*_LOW` 的 repair hint 从空泛剧本改为带数值与定位：「当前 payoff_count=1（目标≥4）；第 N 段立了悬念 X 未兑现，在其后补一段可见结果」。simulator 已输出 per-persona concerns，把 evidence 接进 hint 构造（drafts.py:11116 的 retention_hint_by_code）。
3. **信号与刻度校准**（防 Goodhart 的正路）：
   - payoff/hook marker 词表从 40 个扩展 + 接 hook echo 已有的 semantic group 机制，让不同措辞的真实兑现被计入（先修计量，再谈达标）；
   - 用语料注册表跑 ≥20 真实榜单章 + 20 坏章，建立分题材分布，阈值改为配置化分位数（真书 P25 下沿），并接"达标线绑写手模型档"现成机制；
   - 守卫：A/B 盲评（跨家族判官，按既有规程）验证"通道目标注入后的章"对照组不出现关键词灌水式文笔退化——此前碎句癖教训表明任何写法指令都可能被模型应试化，必须盲评把关。

### 3.5 合规三明治：写前约束 + 写后确定性修复（断裂 ④）

- **naming**：prompt 注入完整名字池（按出场优先级排序，不再 `[:10]`）+ 显式规则「无名路人一律用职务/外貌称谓，不得取名」；写后违规名走确定性替换器——按角色语义就近映射池内名或降级为称谓，替换后只重跑 naming 检查，**不再触发 scene regen**。regen 仅保留给替换器无法处理的结构性混淆。
- **pronoun_mismatch**：检测器已定位名字±60 字窗口，加确定性"她→他"定向替换（处理她的/她们变体），替换后重验；整轮 reset 路径删除。
- **prewrite 时间预算**：白名单注入 prompt + 重试反馈附白名单与违规定位；白名单为空时在**编译期**由 manifest 编译器从章 outline 的时间锚点自动生成 `allowed_elapsed_events`（上游补产出，而非验收期跳过）；plan 输出加 JSON schema 约束消灭 invalid JSON 类失败。

### 3.6 定向 patch repair（改 auto-repair 主路径）

repair 入口改为按失败条目路由，全场景重置降级为最后手段：

| 失败条目 | 动作 | 成本 |
|---|---|---|
| ENDING_HOOK_MISSING | 末场景最后一段 span 重写，prompt 带锚词类别 | ~0.5k token |
| HOOK_ECHO_MISSING | 首场景前 300 字重写，prompt 带 missed tokens | ~0.5k |
| SIGNATURE_*_MISSING | 槽位场景定点改写/插入一段，prompt 带锚词与判官理由 | ~1k |
| PERSONA_PAYOFF_DENSITY_LOW | 在 evidence 定位的悬念点后插入兑现段 | ~1k |
| NAMING/pronoun | 确定性替换，0 LLM | 0 |
| CHAPTER_SPLICE_* | 仅重写接缝两段 | ~1k |
| 场景级结构性失败（POV/逻辑崩坏） | 才允许单场景重置 | 现状成本 |

收敛性规则：

- verdict=pass 且不承载失败条目的场景**锁定不动**（ch9 案例直接对症）；
- 每轮 repair 目标=当轮验收的失败条目，历史码只读不入目标（消 blocker drift，drafts.py:11370 处改造）;
- patch 后只重验当轮条目 + 受影响 span 的 splice 检查；
- review/rewrite 停滞修复：rewrite 指令必须携带 review 的证据段落与具体动作（评分器已能定位证据），delta 改按目标条目相关子分数计算。

### 3.7 兜底（修死锁 bug，但定位为保险丝）

3 轮 repair 耗尽且剩余失败条目全部属于 retention/advisory 类时，走 7453 软放行分支记 `low_retention_quality` 前进（修正 3<6 的不可达条件）。在 3.1-3.6 生效后此路径预期触发率 <5%，监控其触发率作为体系健康度指标——升高即说明契约闭环出现新断裂。

---

## 4. 落地排期

**P0（契约闭环最小可用，预计 2-3 天）**
1. 契约编译器 + ending_hook/hook_echo 两类条目 + 场景义务分工注入（3.1/3.3 主干）
2. prewrite 白名单注入 prompt + 反馈附白名单 + 空白名单编译期自动生成（3.5）
3. naming 完整池注入 + 称谓规则 + 确定性替换器；pronoun 定向替换（3.5）
4. 死锁兜底修复（3.7）

**P1（招牌场景与 persona 达标，2-3 天）**
5. 锚词协议：题材感知 mandate 生成 + 指令/验收对齐 + 判官输出证据（3.2）
6. persona 通道目标进写手 prompt + 通道级 repair hint（3.4.1/3.4.2）
7. 定向 patch repair 路由表 + pass 场景锁定 + 当轮条目重验（3.6）

**P2（校准与计量，并行推进）**
8. payoff/hook 语义组扩展；语料注册表跑分布、阈值配置化分位数 + 绑模型档（3.4.3）
9. blocker 版本化与 repair 收敛指标上报（接 /reviews 判官 API 与 design dossier）

## 5. 验证方案

1. **单元/回归**：每个契约条目"注入→生成→验收"闭环测试；替换器幂等与误替换守卫；既有 86+136 测试不回退。
2. **对照重跑**：用被 block 的 `civil-service-cultivation-market-1781109344` 重跑 ch1-3（同模型 M3）。验收线：strict accepted ≥2/3；单章 LLM 调用次数较基线（初稿 7 + 3 轮 repair × ~14）下降 ≥60%；NAMING regen 次数 = 0；prewrite 兜底率 = 0。
3. **质量守卫 A/B**：契约注入组 vs 基线组各 4 章，跨家族判官盲评（--judge deepseek 规程），确认达标不以文笔退化为代价；重点盯锚词生硬与关键词灌水。
4. **全程指标**：每章上报"首验失败条目数 / patch 轮数 / 软放行触发率"，进 Arena 回归看板。

## 6. 风险与对策

| 风险 | 对策 |
|---|---|
| 锚词逐字要求导致生硬植入 | 锚词限具体名词；包装自由；A/B 盲评守卫；判官层兜底同义表达 |
| persona 通道目标被模型应试化（碎句癖前科） | 指令写"动作/结果"不写"句式"；盲评对照；信号语义化降低对特定词依赖 |
| patch 重写引入新 splice 矛盾 | patch 后强制重验受影响 span 的 splice/presence 检查 |
| 契约编译失败成为新单点 | 编译失败降级为"无契约旧行为"+ 告警，不阻断（沿用 soft 闸门审计原则） |
| 确定性替换误伤（同姓不同人） | 替换仅限检测器高置信窗口；低置信回退 regen；替换记录入 trace 可审计 |
