# 第 59 条：eval-57 故事层遗留

状态：开发完成，待监工验收

日期：2026-11-11

## 结论

第 59 条的五项框架能力均已实现并分别提交：解法四步结构去重、术语和人物首提铺垫、解释密度、关系章正文落实与显式连续对抗豁免、每阶段双兑现点与严格逐字证据。源书 `books/eval-57`、第 57/58 条对照目录均未写入；离线验证和真实试写全部在 `bestseller-tmp` 下完成。

真实 20 章试写自然结束且 `run.json.status=complete`，但没有达到所有故事层目标：第二阶段没有正文关系章，第 17–20 章仍是未获豁免的连续对抗；首提和解释密度的若干真实修复被回滚；两个“小兑现”位置偏晚；第 10 章虽写出击倒对手和后果，却没有逐字确认死亡，因此严格审读保持 `Unknown`。这些结果不能当作人工读者验收通过。

## 提交

| 项目 | 提交 | 内容 |
| --- | --- | --- |
| B1 | `3688210b3` | 四步解法结构、规划历史、正文定向修复、体检重复组 |
| B2 | `38b7a5c11` | 世界术语首用、未解析实体、写前提示与正文修复 |
| B2 补漏 | `3d5ebcbdc` | 人物首次被点名也进入有界修复；已有 finish 人物修复尝试过时不重复付费 |
| B3 | `f6f36744c` | 解释密度分档、定向改写、复检回滚、体检注册 |
| B4 | `1bb53fcf4` | 关系变化合同、正文章型复检、末阶段显式豁免 |
| B4 真机修正 | `079efa5a7` | 可选 `combat_run_exemption: null` 按未申请豁免处理 |
| B5 | `cd3a804c1` | 中段小兑现/阶段末大兑现、含“杀”结局约束、动态章号证据 schema |
| B5 真机修正 | `a8e40382e` | `P01/P1` 归一到主承诺，体检区分缺点、错权重和错位置 |
| 恢复契约 | `6c1493291` | 四步解法历史加入缓存指纹后仍复用同版本耗尽结果 |

## Evidence：离线复现

只读源书复制到：

`/Volumes/MACSSD/owen-home/Documents/workspace/bestseller-tmp/f59-eval57-offline-20261111`

### B1 解法结构

- 保存的逐字观察把第 2–8 章归为同一组：`借用感官 → 辨认线索 → 选择路径 → 识破陷阱`。
- 观察覆盖 7 章；其余 43 章没有可信四步观察，保持 `Unknown`，未据此改稿。
- 已观察集合中没有额外重复组；这不等于其余 43 章“零误报”或“无重复”。
- 单元假客户端用 eval-57 第 2/3 章式原文完成一次定向改写，四步签名改变且最终状态复检通过。

### B2 首次出现

- 世界术语缺交代：`(1, 驭兽宗)`、`(7, 醒脉)`、`(7, 凝纹)`、`(12, 蕴魂)`、`(23, 边境猎场)`、`(40, 铸域)`、`(41, 雪原祖窟)`。
- 未解析实体：`(40, 另一头契兽)`。
- 旧人物体检仍检出 7 人：白苓、霜团、陶七、沈照霜、薛骨、呼延烈、祁玄澜。
- 单元假客户端分别证明“醒脉”定义和对白里首次点名人物可在首现段补入并归零；补入后仍走最终状态复检。

### B3 解释密度

判定基准是非对白解释字符占比和最小解释字符数：主阈值 `ratio >= 0.24` 且解释字符 `>= 220`；较长章节另用 `ratio >= 0.15`、解释字符 `>= 330`、至少 5 个解释段候选的保守入口。段落类别仅限设定说明、因果解释、规则复述，属于 `agent_editorial_not_human` 启发式。

| 章 | ratio | 解释字符 | 结果 |
| --- | ---: | ---: | --- |
| 4 | 0.2910 | 513 | dense |
| 11 | 0.1530 | 331 | dense |
| 35 | 0.2131 | 461 | dense |
| 46 | 0.3270 | 639 | dense |

- eval-57 另有 7 个候选：5、6、7、8、10、31、40。未做逐章人工编辑验收，状态为 `Unknown`，不能称为 7 个已确认误报。
- eval-52 校准候选为 13、21、38；同样只是启发式候选。
- 单元假客户端将第 35 章式解释段改为动作与对白，保留人物和术语，复检降到 normal。

### B4 关系章与连续对抗

- eval-57 五阶段体检：阶段 1/3/4/5 `missing_relationship`，阶段 2 在第 15 章 `met`。
- 第 47–50 章是未获豁免的连续 4 章对抗。
- 单元假客户端把“计划为关系、正文为对抗”的主场景改成两人关系变化，最终章型重提取为情感；无效或复检不通过会回滚。
- 只有末阶段、连续到末章且理由不少于 6 字的显式豁免才生效；`null` 视为未申请，不再制造结构错误。

### B5 兑现密度与第 58 条根因

- eval-57 每阶段原本都只有一个兑现点：8、16、25、35、48，五阶段均为 `missing_two_points`。
- 第 58 条第 10 章根因是复审响应把真实引文的 `chapter` 全写成 1；严格逐字校验拒绝了错章证据并回滚，不是“没有写事件”。schema 现按当前章动态给章号，错章仍保持 Unknown，没有放宽判定。
- 新合同要求每阶段两个不同章号，分别为中段小兑现和末两章大兑现；标题或卖点字面含“杀”时至少一个阶段对手结局为“被杀”。

### 假客户端边界

五项均有独立的假客户端成功路径及回滚/Unknown 测试，使用了 eval-57 原文或同型片段。没有对 50 章副本执行一次“让假模型批量重写整本并全部归零”的伪验收；B1 未观察的 43 章和 B3 的 7 个额外候选仍是 Unknown。因此“整本假修复后归零”未被证明，不能把代表性单元修复冒充整书修复。

## Evidence：真实模型 20 章试写

目录：

`/Volumes/MACSSD/owen-home/Documents/workspace/bestseller-tmp/f59-real20-20261111`

种子从 `books/eval-57/seed.yaml` 单独复制，只在临时目录把顶层 `chapters` 改为 20；启动前由 `Book.read` 读回 `chapters=20`。命令自然退出，没有中断或续写到第 21 章。

### 运行账本

| 指标 | 结果 |
| --- | ---: |
| `run.status` | complete |
| 完成章数 | 20 |
| 正文/章元数据 | 20 / 20 |
| 调用 | 205（197 ok，8 `invalid_stage_output` 后重试） |
| wall/execution | 4927.16 秒（约 82 分 7 秒） |
| token | 1,239,716（cached 68,932） |
| 估算费用 | cache-adjusted $0.41803698；保守 $0.42734280 |

8 次无效返回发生于：两次第一阶段章纲重排、第 3 章承诺审读、第 6/16 章跨章开头、第 7 章边界提取、第 12 章章末修复、第 19 章核心证据审读。全部有调用账本记录并成功重试。

章节字符数：

`[2274, 2309, 2193, 2335, 1919, 2104, 2161, 1952, 2233, 2358, 2585, 1872, 2532, 2168, 1883, 2341, 2266, 2615, 2523, 2148]`

第 5、8、12、15 章低于约 2200 字目标，但仍在运行时接受带内；报告不把“20/20 word_band_pass”解释成人工篇幅满意。

### 真实体检结果

| 项目 | 结果 | 判定 |
| --- | --- | --- |
| B1 四步解法 | 10 章有逐字观察，重复组 0；另 10 章 Unknown | 已观察范围未重复；全 20 章零重复未证明 |
| B2 世界术语 | 缺：第 1 章醒脉、驭兽宗、暴雪矿镇 | 未归零 |
| B2 未解析实体 | 第 8 章“另一头雪兽”3 处 | 未归零 |
| B2 人物 | 顾砚、苏晚棠、霜团、陆小满、石头、白苓、岑朔仍为 Inference | 未归零；框架已在真机后补人物定向修复 |
| B3 解释密度 | 9、11、19、20 为 dense | 未归零 |
| B4 关系章 | 阶段 1：4、8 met；阶段 2：missing | 未达标 |
| B4 对抗上限 | 17–20 连续 4 章，未获豁免 | 未达标 |
| B5 兑现点 | 阶段 1：7/10；阶段 2：16/19，各 2 个 | 数量达标，但两个小兑现都晚于各阶段中点，`invalid_positions` |
| B5 正文证据 | 7、16、19 fulfilled/Evidence；10 Unknown | 第 10 章未逐字确认邢烈死亡，严格判定正确保持 Unknown |
| 重复 | 句子/叙述/对白重复均 0 | Evidence |
| 章纲对齐 | 13/20；5、10、12、14、15、16、20 不一致 | 低于全对齐 |

### 没生效和回滚

- 第 1、8 章首次出现修复：`rolled_back / invalid_edits`。
- 第 9、11、19、20 章解释密度修复：均 `rolled_back / invalid_edits`；所以体检仍列为 dense。
- 第 14 章计划关系变化为顾砚/苏晚棠，正文实际为谋划；关系主场景修复返回不匹配原段，`rolled_back / invalid_edit_target`。
- 第 18 章虽携带顾砚/霜团关系合同，保存正文仍判为对抗；没有把标签当成关系章。
- 第 10 章承诺复审拿到了同章逐字引文，但只证明“倒在巢口、再不可能搜捕”，没有明确“死亡”；修复链不对 Unknown 强行补写，最终仍 Unknown。
- 解法结构修复在真实 20 章未触发，因为已观察签名没有同阶段重复；不能据此证明未观察 10 章也不同构。
- 运行时 `structure-planning.json` 为 `failed_continued`：当时 `P01` 未归一、可选空豁免被误判。真机后两项已用失败测试修复；对保存圣经作内存归一后仍有“小兑现位置偏晚”两项真实结构问题。

### 正文片段

关系章（第 4 章，顾砚给霜团保留退路）：

> “不是让你过来。”顾砚扯过自己的破外袍盖住草坑，“只是这里暖。你愿意待哪就待哪。”

> 顾砚僵了一瞬，伸出的手停在半空，最终只落在木栏上，没有碰它。

小兑现（第 7 章）：

> “借你的眼睛。”他说，“只借一次。”

> 第一车兽材倒进炉膛，铜管中立刻传出重新隆起的轰鸣……炉心由暗红转成明亮赤色，矿镇总算守住了主炉。

第二阶段兑现（第 16/19 章）：

> 顾砚闭上眼，只借了一次感知。

> 顾砚扯住链环，将他拽回祖窟入口，当着所有人的面将他按跪在碎裂石枢旁。

首次术语交代成功例（第 11 章）：

> 凝纹之后便是第三阶蕴魂，能将自身力量更稳地凝进兽纹。

### 与第 57/58 条对照

对照目录保持只读：

- `/Volumes/MACSSD/owen-home/Documents/workspace/bestseller-tmp/f57-real10d-20261110`
- `/Volumes/MACSSD/owen-home/Documents/workspace/bestseller-tmp/f58-real10-20261111`

前三者只比较共同的前 10 章时：

| 指标 | f57 | f58 | f59 前 10 章 |
| --- | ---: | ---: | ---: |
| 最长连续对抗 | 5 | 3 | 3 |
| 阶段 1 关系章 | 缺 | 4 | 4、8 |
| 句子重复 | 1 | 0 | 0 |
| 对话重复 | 1 | 1 | 0 |
| 世界术语缺交代 | 4 | 4 | 3 |
| 章纲对齐 | 8/10 | 10/10 | 8/10 |

所以重复、阶段 1 关系节奏和最长连续对抗没有比 f57 差；相对 f58，章纲对齐从 10/10 退到 8/10。扩到完整 20 章后，f59 又出现阶段 2 关系章缺失和 17–20 连续 4 章对抗，不能宣称“第 57/58 条已有指标全部不退化”。

## Inference

- 解释密度、章型和人物首现体检是有限规则或模型审读结果，不等于真人阅读质量。
- 第 10 章上下文强烈暗示邢烈死亡，但没有逐字死亡陈述；按合同仍应 Unknown，不能由上下文推断替代证据。
- 第 14 章已有顾砚/苏晚棠互动，但保存章型为谋划且修复回滚，因此不能把“有关系戏”当作“关系主章”。

## Unknown / 待监工

- 未观察的 10 个真实试写章节是否存在语义同构解法。
- eval-57 B3 的 7 个额外候选究竟有多少是人工确认误报。
- 真实试写的文学质量、关系变化是否自然、兑现是否有读者冲击力，尚无人类验收。
- 没有证明 50 章副本经假客户端批量修复后全部归零；验收后仍需按 INBOX 要求，用固定书名的新种子写完整 50 章并做全书评审。

## 验证

- 全量：`1719 passed, 2 skipped, 756 subtests passed`。
- `engine/tools/check_scope.py`：PASS；最大 Python 文件 499 行；无 `bestseller` import、无 bookengine/deai 交叉导入。
- `ruff format --check .`：最终提交前重新执行。
- 工作树未 push、未合并 main。

## 返工二

状态：返工二完成，待监工网页真机验收。

### 结论与验收边界

返工二的 P0–P2 已有代码和离线契约测试。真书试写的执行分工在 2026-11-11 调整为：监工只通过框架网页的建书、写书流程驱动真机，Codex 不再启动或续写真书。因此本节只报告代码证据、既有目录的只读离线重算和未完成试跑数据；速度目标、故事层指标和读者质量均保留为 **Unknown，等待监工网页真机验收**。

`f59-real20c-20261111` 在调整分工时已中止并原样保留：14 个调用记录为 12 `ok`、1 `invalid_stage_output`、1 `pending`；没有落盘章节元数据或正文，`runner.lock` 保留。最后一项是 `r2-ch1-finish` 的未完成请求。该目录不续写、不清锁，也不把未完成数据计作 20c 验收。中止前章纲请求已是 `chapters-1-5`，且只有 1 次 11.73 秒的定向重排；这只能证明入口使用了五章窗口，不能证明整书速度或质量。

返工二相关提交：

| 提交 | 内容 |
| --- | --- |
| `2a641251a` | 关系主场景目标扩至最多 16 段、长章至少 8 段 |
| `c66858aa1` | 五章窗口、硬约束付费门、一次/两目标、截断分类、写手主场景合同 |
| `e63b02926` | 相邻开头地点/修改范围与跨章全命中修复 |
| `a95aaa769` | 关系修复回滚原因细分 |
| `4d3bedca4` | 原始速度诊断归档 |
| `ab5f6bf53` | 单一标准章型词表、体检统一、确定性章名与重排决定账本补齐 |

### P0–P2 代码与测试核对

| 项 | 实现 | 契约证据 | 状态 |
| --- | --- | --- | --- |
| P0-1 统一章型词表 | `chapter_rhythm.normalize_chapter_type` 是唯一入口；规划排期、章纲、正文抽取、语义复核和体检均在比较前调用。标准值只有对抗/谋划/情感/日常/揭秘/收获；`打/救/动作/爽点/能力兑现/关系推进` 等归一，私有词变 Unknown。原 `growth_extract.yaml` 的第二套别名表已删除。 | `test_speed_rework59.py` 覆盖旧词、返工样本词、抽取、排期与体检同一结果；`test_minimal_b_part2.py` 覆盖未知私有词。 | 完成 |
| P0-2 章名重复不重排 | `rename_repeated_titles` 在付费判定前本地处理；为避免删一个字生成“断取痕”一类残题，命中章确定性改为中性 `第N章`，并记录 `title_renames/action=deterministic_title_rename`。 | `test_speed_rework59.py` 和 `test_outline_diversity.py` 断言重写客户端不会被调用。 | 完成；中性标题文学性待网页真机评审 |
| P0-3 五章窗口与截断 | `planning_window_end` 同时约束章节主循环和 `q1_plan.outlines`，窗口最多 5 章。provider `finish_reason=length` 记 `status=truncated`，抛 `TruncatedOutput`，不再覆盖成 `invalid_stage_output`，也不以同一整批提示重试。 | `test_speed_rework59.py` 断言 1–20 只取 1–5，截断只请求 1 次且账本为 `truncated`。 | 完成 |
| P1-4 仅硬约束触发 | `hard_replan_issues` 只接收阶段缺关系主章、连续对抗超 3、兑现位置丢失、对手结局丢失。章型/钩子/结构对齐仍记录，但不使缓存失效；语义章型模型复核从生产规划链移除。 | 测试把 `title_keyword`、`rhythm_content_mismatch`、`rhythm_model_mismatch` 与四类硬问题同批输入，只返回四类硬问题。 | 完成 |
| P1-5 只改 1–2 章 | 同一 hard kind 聚合为一个阶段级问题，`replan_targets` 总数最多 2；上下文只带目标及前后章。 | `test_speed_rework59.py` 断言实际传给 rewrite 的目标数为 1–2。 | 完成 |
| P1-6 最多一次 | `MAX_REWRITE_CALLS=1`；一次不合格即保留原稿。报告新增 `replan_decisions`，逐目标记录 `accepted_model_replan` 或 `kept_original`、次数和原因。 | 失败客户端只被调用 1 次，原章纲不变，决定为 `kept_original/attempts=1`。 | 完成 |
| P1-7 主场景合同前置 | 写手 instruction 的第一段是“不截断的本章主场景硬合同”，逐字包含标准章型、`rhythm_focus`、两场 goal/action/choice/result、`hook_type` 和 hook；同一合同也是第一个 section，优先于文风。 | `test_minimal_b.py` 走真实 `chapter → ask → Client.stage` 拼装路径，断言具体钩子、动作、结果在 prompt 中且合同位于文风前。 | 代码完成；真实 `plan_alignment >=15/20` 待网页真机 |
| P2 关系修复 | 完整正文和最多 16 个分散段送模，长章至少 8 个有效编辑；禁止把定规则、排路线、分工作为主结果。回滚拆分为 `after_type_mismatch/state_recheck_failed/lost_grounded_payoff/hard_fact_conflict`。 | `test_relationship_rhythm59.py` 覆盖全章材料、分散段、采用与回滚。 | 代码完成；真机成功率待网页验收 |
| P2 相邻开头 | 补齐“深处/入口/站台”和“钻入/退到”等真实样本；框架固定只改第一段，不再让模型自报 `replace_count` 后合并两段、遗留旧第二段。 | `test_opening_recheck39.py` 覆盖四组真实父子地点及框架拥有修改范围。 | 完成 |
| P2 跨章衔接 | `adjacent_opening_hits` 不再首命中即 `break`，一次返回前两段全部重复句；替换段数必须精确覆盖全部命中段。 | `test_final_hygiene_58_openings.py` 覆盖 A/B 两句同时重放、一次修复和少改一段时回滚。 | 完成 |

### P2 原始回滚账与已有提交验证

按 `calls.json` 和 `chapters/*.json` 逐章回读，`speed-plan-20261111.md` 中“相邻开头 6/10、跨章衔接 5/10”的旧摘要与目录原始记录不一致。真实口径如下：

| 目录 / 修复 | 逻辑触发 | API 请求 | 采用 | 回滚 | Unknown |
| --- | ---: | ---: | ---: | ---: | ---: |
| attempt1 关系主场景 | 8 | 8 | 0 | 8 | 0 |
| attempt1 相邻开头 | 9 | 9 | 1 | 8 | 0 |
| attempt1 跨章衔接 | 10 | 11 | 4 | 6 | 0 |
| 中止 20b 关系主场景（已落盘 1–11 章） | 1 | 1 | 0 | 1 | 0 |
| 中止 20b 相邻开头（已落盘 1–11 章） | 5 | 5 | 2 | 3 | 0 |
| 中止 20b 跨章衔接（已落盘 1–11 章） | 2 | 3 | 0 | 2 | 0 |
| 中止 20b 第 12 章相邻开头 | 1 | 1 | 0 | 0 | 1 |

attempt1 关系回滚为 6 次 `relationship_recheck_failed`、2 次 `invalid_edit_target`；相邻开头为 5 次 `opening_still_disconnected`、3 次 `introduced_repetition`；跨章 6 次均为 `opening_recheck_failed`，其中 5 次是修掉首个重复后才暴露第二个。中止 20b 第 12 章只有请求，没有章节落盘，严格保持 Unknown。

- `67d9cbe48` 的材料覆盖已在中止 20b 第 4 章生效：关系修复 `input_chars=3490`，实际返回 7 个编辑；但整章仍抽成“谋划”，所以结果是“材料覆盖生效、关系验收未生效”，不能报成功。
- `2a641251a` 在上述证据后把上限扩为 16 段、长章最低 8 段并排除“规则/路线/分工”主结果；尚无网页真机成功证据。
- `f78b361e2` 在中止 20b 第 4 章生效：章纲不再同时携带战斗兑现/对手结局与关系合同；正文仍没写成情感章，所以只证明合同分离，不证明关系章成功。
- 三项修复均保留。若监工网页真机中关系修复仍超过 50% 回滚，建议继续仅在“规划明确为关系主章且正文非情感”的硬触发下运行；相邻开头和跨章衔接已有确定性根因修复，不建议停跑。

### attempt1 / 中止 20b 离线重算

方法：只读加载两个目录的 `story_bible.yaml`、`outline-diversity/*.json`、`writing_outlines/*.json` 和 `chapters/*.json` 最终正文观察；在内存中先执行当前的确定性标题改名、能力合同和书名承诺投影，再运行当前全部规划检查，最后只把 `hard_replan_issues` 送入最多两目标/一次调用策略。没有模型调用，没有改写源目录。旧目录由旧算法产生大量相互重叠的后缀窗口，因此“新付费调用”是对这些旧报告逐个重算的保守上限，不等价于一次全新五章窗口运行。

| 指标 | attempt1 | 中止 20b |
| --- | ---: | ---: |
| 旧 `outline-diversity` 报告 | 14 | 2 |
| 旧 attempt 记录 / 有目标 | 51 / 43 | 8 / 8 |
| 旧 diversity API 请求 | 47（37 ok） | 12（6 ok） |
| 旧目标槽位（终止轮前） | 206 | 48 |
| 旧语义章型复核请求 | 14 | 2 |
| 新规则仍会付费的报告上限 | 6 | 2 |
| 新目标槽位上限 | 6 | 2 |
| API 请求降幅 | 87.2% | 83.3% |
| 目标槽位降幅 | 97.1% | 95.8% |
| 本地确定性章名改名 | 11 | 8 |

attempt1 新规则仍触发的只有关系主章缺失：旧重叠报告 `001-010`、`002-010-observed2`、`003-010-observed3`、`004-010-observed4` 各只指向第 5 章，`011-020`、`012-020-observed7` 各只指向第 15 章。兑现位置和“被杀”元数据在当前确定性合同投影后不再需要模型重排。中止 20b 只剩 `001-010` 的第 10 章连续对抗硬问题，以及 `011-020` 的第 15 章关系主章硬问题。

attempt1 的 93 条旧 `rhythm_replans` 决定中，否决原因包含章名重复 30 条、`rhythm_content_mismatch` 22 条。22 条里 13 条在统一词表后直接消失：

| 旧 allowed → observed | 条数 | 新判定 |
| --- | ---: | --- |
| 救 → 对抗 | 2 | 对抗 = 对抗 |
| 能力 → 对抗 | 2 | 对抗 = 对抗 |
| 爽点 → 对抗 | 2 | 对抗 = 对抗 |
| 动作 / 打 / 收束 / 能力兑现 / 对抗兑现 / 阶段兑现 → 对抗 | 各 1，共 6 | 对抗 = 对抗 |
| 关系推进 → 情感 | 1 | 情感 = 情感 |

其余 9 条是真实内容与标签不同，仍保留为诊断，但不再触发重排。中止 20b 的 `rhythm_replans` 决定中没有这类同义词否决；attempt issue 池里有 1 条“能力兑现 → 对抗”，在新入口同样会于判断前消失。章名重复旧决定在 attempt1 / 中止 20b 分别为 30 / 12 条；新规则只产生 11 / 8 次内存改名，付费请求均为 0。

### `c66858aa1` 对 `q1.py` / `client.py` 的必要性与边界

`q1.py` 是唯一逐章调度循环，仅有以下调度改动：

1. 导入 `planning_window_end`，让生产循环和可单测的 `q1_plan.outlines` 共用五章上限。
2. 删除 `solution_issues` import；它原先令任一剩余解法诊断在每写完一章后重开保存窗口，正是逐章重排的入口。诊断本身仍在规划报告中，没有删除。
3. 把三分支重开条件改为“当前 batch 为空或不含本章”后才规划；不再因软诊断每章重开。
4. 把 `idx + 9` 改为 `planning_window_end(...)`，实际生产请求最多 5 章。没有改正文写手、修复器、状态合并、导出或书稿保存。

这几行必须位于 `q1.py`：只在 `q1_plan` 内截断 end，不能阻止逐章循环先因 `solution_issues` 反复进入规划，也不能保证所有生产调用者采用同一窗口。

`client.py` 是 provider 响应和 `calls.json` 状态落账的唯一边界，仅有以下记账改动：

1. 新增 `TruncatedOutput(ProjectError)`，把输出上限与模型内容不合格分开。
2. `finish_reason=length` 时先写 `status=truncated`，再抛该异常；不再走 `Retryable("truncated_return")`。
3. 异常分派让 `TruncatedOutput` 原样上抛，避免后续宽泛 `ValueError` 分支覆盖成 `invalid_output/invalid_stage_output` 并重发同一整批。

没有改密钥读取、请求 payload、网络 408/429/5xx 退避、额度判断或正常坏 JSON 重试。因此 `q1.py` 的变更只限调度，`client.py` 的变更只限截断分类和调用账本。

### 验证与 Unknown

- 全量测试：`1766 passed, 2 skipped, 751 subtests passed`。
- `check_scope.py`：PASS；最大 Python 文件 499 行，无 `bestseller` import、无 bookengine/deai 交叉 import。
- `ruff format --check .`：PASS，298 个文件均已格式化。
- 未 push，未合并 main；未改 `deai/`、`tools/webui/` 或真实书库。
- **Unknown**：监工网页真机的平均每章耗时、规划占比、作废重排率、截断数、`plan_alignment`、关系章、连续对抗、兑现位置、被杀、术语铺垫、解释密度，以及三类修复的新回滚率。
- **Unknown**：真人对中性 `第N章` 确定性改名的接受度；它满足“不为章名重复付费重排”的框架合同，不等于文学标题验收。

## 返工三

状态：返工三完成，待监工网页真机验收。

本轮遵从分工调整，只改框架代码、跑假客户端和只读离线回算；没有启动、续写或新建真书。证据目录仅为：

- `/Volumes/MACSSD/owen-home/Documents/workspace/bestseller-tmp/web59-ch10-copy`
- `/Volumes/MACSSD/owen-home/Documents/workspace/bestseller-tmp/web59-ch19-copy`

原网页书目录未改。以下“旧结果”均指保存副本的历史输出；“新规则”指当前代码的离线判定或假客户端契约，不冒充网页真机结果。

### P0：关系章章纲、写手与修复

根因已查实：

1. `q1_schemas.yaml` 的阶段骨架 scene 原本没有 `choice` 字段，模型只能返回 `action`。
2. `rhythm_skeleton.bind_stage_content()` 随后明确执行 `choice=action`，所以第 4、14 章的两场 scene 都出现逐字相同，不是模型原始输出自行复制。
3. 第 4 章虽被标为情感，但旧硬重排策略不接收 `rhythm_scene_focus/content_mismatch`，战斗场景可带着关系标签进入写手。
4. 第 14 章关系修复的 16 个目标中，模型给出 15 个逐字匹配的编辑；仅第 17 段把原文“目光冷下来”误写成“目光冷起来”。旧代码因一个 `original_mismatch` 把其余 15 个有效编辑全部回滚为 `invalid_edit_target`。

代码变更：

- 阶段骨架 schema、窗口和 fallback 都新增独立 `choice`；绑定阶段内容时分别保留 `action/choice`，不再互相复制。
- 新增 `relationship_outline_issues()`：两场必须各有不同的 action/choice，且都落实坦白、试探、拒绝、让步、接受、边界或和解；握刀、撞门、追杀、闯关、受控搏斗为主动作时直接拦截。
- `rhythm_relationship_scene` 成为硬问题，只本地重写目标章一次；返回章纲仍不合格就保留旧稿并记录，不接受只换标签。
- 写手首段合同现在明确要求关系从 before 变到 after；追杀、机关和受控搏斗最多一笔带过。
- 关系正文修复改为逐条采用有效编辑；单个错 `original` 记录到 `rejected_edits`，只要仍有至少 8 个有效主场景编辑就进入章型/状态/兑现复检。
- 节奏合同升到 v11。旧缓存只有在新关系硬合同或其他硬规划合同确实失败时才失效；旧软诊断不会触发付费迁移。

对 `web59-ch19-copy` 的只读回算：

| 章 | 新章纲校验结果 | 主要缺陷 |
| --- | --- | --- |
| 4 | 拦截 | scene 1 `combat_main_action`；两场均 `choice_duplicates_action`；action/choice 非人际主动作 |
| 14 | 拦截 | 两场均 `choice_duplicates_action`；scene 2 以探阵分工为主，非关系变化 |
| 允许例 | 通过 | 第 14 章式合同改为“坦白失控冲动→对方交出阵路但保留收回权→接受叫停边界”，两场 action/choice 分开，issues 为 0 |

假客户端走真实 `_ask → Client.stage` 拼装路径，stage 为 `plan-q1-chapters-4-4-relationship-offline`；发出的 prompt 逐字包含第 4 章战斗 action 和 relationship_change.before，schema 含 `choice`，返回的合格章纲被采用。契约测试保留在 `test_relationship_rhythm59.py`。

旧网页副本没有被改写，故体检仍是 19 章中对抗 13 / 谋划 4 / 揭秘 2 / 情感 0，阶段 1 `missing_relationship`，第 4–7 章仍是未豁免连续 4 章对抗。这是历史结果，不是新代码真机结论。

旧关系修复调用账：第 4 章 seq 66，`input_chars=4382`，返回编辑后 `after_type_mismatch`；第 14 章 seq 162，`input_chars=4818`，因上述单个错原文而 `invalid_edit_target`。新代码能让第 14 章候选进入后续复检，但没有付费模型重跑，最终能否变成情感章仍为 **Unknown**。

### P1：相邻章开头重复

7 对全部不是“没触发”。它们在初稿已重复，修复也全部请求，但 7/7 因返回段数不符合框架的隐藏要求而回滚，且没有一对进入 `cross-chapter-recheck`：

| 对章 | 命中句数 | 修复调用 | 历史返回 | 历史结果 |
| --- | ---: | --- | --- | --- |
| 1/2 | 3 | seq 34 / 6609 chars / ok | 要 2 段，回 4 段 | `invalid_opening_patch` |
| 5/6 | 1 | seq 83 / 6668 / truncated；seq 84 / 6668 / ok | 要 2 段，回 3 段 | `invalid_opening_patch` |
| 6/7 | 2 | seq 92 / 6656 / truncated；seq 93 / 6681 / ok | 要 2 段，回 1 段 | `invalid_opening_patch` |
| 10/11 | 2 | seq 135 / 6649 / ok | 要 2 段，回 4 段 | `invalid_opening_patch` |
| 12/13 | 3 | seq 148 / 6665 / ok | 要 2 段，回 3 段 | `invalid_opening_patch` |
| 13/14 | 1 | seq 158 / 6651 / ok | 要 1 段，回 3 段 | `invalid_opening_patch` |
| 17/18 | 3 | seq 192 / 6657 / ok | 要 2 段，回 3 段 | `invalid_opening_patch` |

写手真实路径原先先由 `q1_extract.context()` 发送 `previous[-900:]`，又由 `with_completed_action_context()` 追加末三段；本次所有命中原句都落在两段范围内，相当于逐字发送两遍。现在写手只收到保存的 summary/events/payoff/gains/costs 和结尾地点、在场人物、视角等结构化事实，不再收到上一章原句。修复 prompt 明写“必须恰好 N 个自然段”，只发送当前待改开头、命中诊断和当前状态；段数不符时记录 expected/returned。

离线回算仍能在旧正文中检出这 7 对、共 15 个命中句；旧正文只读不变。假客户端按明确 N 段返回时能进入事实复检。网页模型在新 prompt 下的采用率仍为 **Unknown**。

### P2-5：对手字段与卖点截断

`opponent_resolutions.opponent` 不是体检展示错。`plan-q1-structure-redo` 原始结构里五阶段的 `antagonist` 与 `opponent_resolution.name` 都是具名对手；随后 `selected_card.enforce()` 只因 identity/setting 漂移，就把 volume 的全部字符串字段替换成整段 premise。`title_promises` 只是如实展示被污染的 `stage.antagonist`。

现在选题卡兜底仍可清掉冲突世界/能力，但不再改写已验证的 stage location/antagonist/goal/climax/opponent_resolution。跨书测试断言阶段对手仍与 resolution.name 一致。

旧副本保存的五个 opponent 仍是 82 字 premise，未迁移、未改文件。新书生成路径不再污染。`extract_reader_promises()` 也取消 40 字语义截断：把旧副本字段重新抽取（不读已保存的截断 rows）后，三条长度为 8 / 82 / 88；第 40 字后的暴力词可参与 primary 判断，前 40 字相同的两条也不会误去重。旧副本已保存 rows 仍是 8 / 40 / 40，保持历史证据。

### P2-6：第 14 章未闭合引号

原因确认，未伪报修复：`chapter-014.r2.draft.md` 和 `chapter-014.body.md` 均平衡，书稿层引号修复正确记为 `checked/before=0`。之后 `final/chapter-014.deai.json` 把：

> 门内阵槽九处，重复点应该有三处——不，可能四处，第四处在折返之后。”

替换为缺闭引号的：

> 门内阵槽九处，重复点应该有三处；也可能四处，第四处在折返之后。

因此最终第 14 章第 42 段仍有 1 个缺失 `”`。根因是去 AI 后处理发生在 bookengine 引号门禁之后。本条文件归属禁止改 `deai/`，共享 final 采用协议也不在第 59 条可改清单；本轮只保留体检告警，没有越权改后处理。要彻底闭环需监工另授权 deai/final adoption 的发布后卫生门禁。

### P2-7：术语首提说明书化

第 1 章“醒脉是第一阶；凝纹是第二阶”从 `chapter-001.r2.draft.md` 起就存在，不是 first-appearance repair 插入。根因是框架为裸 `world.levels` 自动造“第 N 阶/承接上一阶”定义，再允许写手用一句叙述交代；旧检测只要看到“境界/第 N 阶”就判 Evidence。

现在裸层级只提供结构提示，不再制造可照抄的序号定义；写手与修复 prompt 明确要求用修炼动作、受限结果、突破结果或人物对白体现，禁止“某词是第几阶/承接上一阶”的说明书句。确定性复检也把原句判为 `term_first_use_missing`，而“你才醒脉，催不动这道阵；等凝纹后再来”可通过。首提必须有交代的严格要求未放宽。

### P2-8：选题卡金手指照搬

原句从两路进入写手：完整 `selected_card.golden_finger_rule`，以及主角档案里的完整 `bible.power`；旧选题卡 drift 兜底还把同一句复制到 rule/ability/cost/limit 四字段。现改为：

- 新书只保存模型从卡片抽出的短规则要点；不再把整句复制到 ability/cost/limit。
- 写手只收到 title/premise/opening/hook、短规则要点和当前 power status，不收到规则原句，并有“禁止照搬或把要点拼回说明句”的硬提示。
- `prose_similarity` 新增 `card_rule_copies`：对选题卡与 power.rule 做归一近似重合检查；单次命中也进入定向修复候选。

对第 1 章旧正文离线重算，原来 `card_explanations=0` 的假阴性现在为 `card_rule_copies=1`，相似度 0.8200；命中原文正是“每斩断一条控制线……继承对方一部分地位与权限”。旧正文未改。

### P2-9：每书独立境界阶梯

`growth_payoff.yaml` 的玄幻 pack 已删除“醒脉/凝纹/蕴魂/融血/铸域/王境/皇境/圣境”字面列表，改为 shape-only 规格：8 层、每名 2–4 字、同书统一意象和构词、禁止数字序号与占位名。新书 prepare 必须由模型在 `world.levels` 生成 8 个唯一专名，验证后一次性绑定到 `bible.canon.ladder.strength`；不合格会走原 prepare 重试，不用写死名兜底。

向后兼容：已有 `story_bible.yaml` 带 canon 时，prepare 仍直接采用保存 canon，不走生成绑定、不改旧境界。单测以新书生成的 8 个专名验证绑定，并以旧 canon 验证字节语义不变。eval-57 和 web59 旧副本继续保留原阶梯，符合“已有书圣经不受影响”。

### 提交与验证

| 提交 | 内容 |
| --- | --- |
| `ee32864f2` | 关系章纲硬校验、独立 action/choice、写手主线合同、有效编辑部分采用 |
| `d4d90ee23` | 写手不再接收上一章原句；开头修复公开精确段数合同 |
| `a68b0270d` | 选题卡兜底保留阶段对手；promise 取消 40 字语义截断 |
| `ce2dd5eb9` | 境界首提必须融入动作/限制/对白 |
| `51ef5fbca` | 规则原句移出写手 prompt；新增近似照搬检测 |
| `955fbcd72` | 新书模型生成独立境界阶梯，旧书 canon 原样恢复 |
| `65ce09d1f` | 增大的主场景合同不再挤掉末章 callback 事实 |
| `3987a5481` | v11 旧关系章纲只在硬失败时失效重建 |
| `f32ca5b2c` | 旧缓存仅硬约束迁移，软诊断不付费；旧书/测试兼容 |
| `0a7cb5e39` | 写手上下文与缓存判断拆模块，恢复单文件 ≤500 行 |

验证结果：

- 全量测试：`1782 passed, 2 skipped, 751 subtests passed`。
- `engine/tools/check_scope.py`：PASS；最大 Python 文件 494 行；无 `bestseller` import、无 bookengine/deai 交叉 import、无禁词。
- `ruff format --check .`：PASS，299 个文件已格式化。
- 未 push、未合并；未改 `deai/`、`tools/webui/`、真实书库或两个只读副本。

### 仍待监工网页真机验收

- 关系章是否实际写成情感主章、关系正文修复是否通过 after_type/state/payoff 复检。
- 新 prompt 下 7 类相邻开头的真实采用率。
- 新书 opponent 显示、完整 promise、独立境界阶梯和首提写法。
- 已达标的速度 4.7 分钟/章、规划 22%，兑现位置/证据、解释密度、术语缺失、重复和连续性不得退化。
- 第 14 章最终文本引号问题仍未闭环，原因和权限边界见上；需要监工决定是否扩大 deai/final adoption 文件归属。

## 返工四

状态：返工四完成，待监工网页真机验收。

本轮只改框架代码，并只读检查以下副本；没有启动、续写或新建真书，也没有改原网页书目录：

- `/Volumes/MACSSD/owen-home/Documents/workspace/bestseller-tmp/web59b-ch10-copy`
- `/Volumes/MACSSD/owen-home/Documents/workspace/bestseller-tmp/web59-ch19-copy`

### P0：关系合同接线与正文章型

根因不是写手阶段再次丢字段，而是保存合同的 schema 与清理器唯一接受的 schema 不一致。`relationship_rhythm` 调用的是 `relationship_contract(outline)`；旧解析器只接受 `people + cause + before + after`：

- 第二本第 4 章保存 `before/after/turning_point/cost`，人物只在两场 `scenes[].characters` 中；
- 第 6 章保存 `characters + cause + before + after`；
- 历史数据还存在 `pair` 别名。第 8 章虽有不完整 `pair/before/after`，但章型是“揭秘”，属于陈旧附带字段，不能反向把揭秘章改成关系章。

现在关系合同在比较和修复前统一归一：明确的关系章可从 `characters/pair/scenes` 取得两人，从 `turning_point/opening_conflict/conflict` 取得缺失起因；非关系章的不完整陈旧字段不升级为合同。节奏合同版本升至 v12。离线走真实 `repair_relationship_rhythm → precise_ask` 路径的结果：

| 章 | `planned` | 新正文判定 | 是否调用修复 | 假客户端结果 |
| --- | --- | --- | --- | --- |
| 4 | 顾长风/木岳霖、隐瞒→坦白并获得制止权 | 情感 | 0；正文已满足，不应重写 | `checked` |
| 6 | 顾长风/木手、工具关系→共同决定次序 | 对抗 | 1；完整正文逐字进入 `完整正文只读` | 假客户端故意返回空 edits，故如实 `rolled_back/invalid_edits` |

第 4 章的正文判定旧误差来自抽取提示只给合法标签、没有说明标签边界。现在抽取提示明确：两人围绕隐瞒、选择、信任和边界的言语冲突属于情感；对抗只指与敌对势力的正面冲突、追杀、搏斗或突围；若后半章由敌人到场并展开大段战斗，仍判对抗。体检另用保守落地规则纠正旧保存结果：必须有有效关系合同、合同两人均在正文出现、至少 4 段关系变化证据，且关系证据段严格多于正面战斗段。该规则只纠正计划关系章的假阴性，不把普通打斗章改成情感。

第二本第 1–10 章逐章离线重算：

| 章 | 章纲 | 旧保存判定 | 新规则判定 | 结论 |
| ---: | --- | --- | --- | --- |
| 1 | 对抗 | 对抗 | 对抗 | 一致 |
| 2 | 对抗 | 对抗 | 对抗 | 一致 |
| 3 | 对抗 | 对抗 | 对抗 | 一致 |
| 4 | 情感 | 对抗 | 情感 | 抽取假阴性已纠正 |
| 5 | 对抗 | 对抗 | 对抗 | 一致 |
| 6 | 情感 | 对抗 | 对抗 | 后半章为韩厉、铜镜和被控弟子的长段救援战，属写手偏离，不能误判情感 |
| 7 | 对抗 | 对抗 | 对抗 | 一致 |
| 8 | 揭秘 | 对抗 | 对抗 | 正文没有以证据核对和推翻旧判断为主，属写手偏离 |
| 9 | 对抗 | 对抗 | 对抗 | 一致 |
| 10 | 对抗 | 对抗 | 对抗 | 一致 |

体检新值为对抗 9 / 关系 1；阶段 1 由 `missing_relationship` 变为 `met`，关系章为第 4 章；最长连续对抗由历史保存类型的 10 章变为第 5–10 章连续 6 章，仍未达标。章纲对齐由 7/10 变为 8/10，剩余第 6、8 章均是写手主场景偏离，不是抽取器遗漏。新接线会让第 6 章真实进入关系主场景修复；是否能在网页真机通过章型、状态和兑现复检仍为 **Unknown**。

第一本副本在同一严格规则下仍为对抗 13 / 谋划 4 / 揭秘 2 / 关系 0，最长连续对抗仍是第 4–7 章共 4 章；第 4 章正文确有正面战斗，未被错误改判关系。对齐为 12/19，错章为 3、4、6、8、12、14、16。这个对照证明新规则不会仅因章纲带关系标签就把战斗正文洗成情感。

相关提交：

- `7cd255e29`：关系合同归一、正文保守判型、抽取边界和体检统一。

### P0：兑现补段的时序位置

生产代码中会把模型 `after` 真正应用到正文的入口只有两个：`hook_momentum` 的兑现补段，以及 `q1_chapter.supplement` 的 finish 抽取补段；二者现共用 `repair_placement.place_tail_bounded_patch()`。为同时满足“章末三段以内”和“不能引用尚未发生的状态”，插入补段一律放在现有正文最后一段之后；随后仍执行状态、兑现和章末钩子复检。框架记录 `requested_after/effective_after/placement=relocated_to_tail`。无效段号或文本分别记录 `invalid_after/invalid_patch_text` 并回滚。`replace=true` 是定点替换而非插入，仍保留原目标段。

两本副本逐章只读审计：共有 5 个兑现修复 API 请求（含第 4、8 章各一次截断/无效后的重试），4 个成功返回 `after` 的 payload，只有 1 个实际落盘。

| 副本 / 章 | 调用 | 返回位置 | 历史结果 | 时序结论 |
| --- | --- | ---: | --- | --- |
| 第二本 ch2 | seq 40，9168 chars，ok | 35；原正文 27 段 | `rolled_back/payoff_recheck_unknown`，补段未落盘 | 未破坏顺序；旧代码未单列非法位置 |
| 第二本 ch4 | seq 64 truncated；seq 65，9664 chars，ok | 1；正文 78 段 | `repaired`，唯一落盘 | **确认插乱**：第 2 段先出现“我选告诉你”，第 3 段后才开始“保护还是隐瞒”的争执 |
| 第二本 ch7 | seq 96，9522 chars，ok | 1；正文 34 段 | `rolled_back/payoff_recheck_unknown`，补段未落盘 | 未破坏顺序 |
| 第一本 ch8 | seq 106 invalid/truncated；seq 107，9687 chars，ok | 41；正文 66 段 | `rolled_back/payoff_recheck_unknown`，补段未落盘 | 未破坏顺序 |

相邻章开头修复使用替换而非正文中段插入：第一本 ch3/ch8/ch17 共 3 次采用，另 7 次 `invalid_opening_patch` 回滚，没有发现新的“结果先于场景”事故。tail 修复只重写最后两段，也不属于任意位置插入。

相关提交：

- `85ed8228d`：两个生产插入入口共用章末三段防线，并记录搬移/回滚原因。
- `d5af17ba8`：补齐 finish 抽取路径的同一防线契约测试。
- `03820c3cd`：进一步收紧为现有正文全部事件之后再插入，避免末三段内部仍发生结果倒置。

### P1：第 3 章“牵丝境”首提

这次不是写手没执行，也不是修复没触发。第 3 章 seq 49 `r2-ch3-first-appearance-repair` 已正常请求，`input_chars=639`、status=ok；最终首提已变为：

> “牵丝境能顺着丝势借物移力，牵着哪一头，线后的东西便会被引向另一头。”他低声说完，掌心贴紧断桩。

误报来自确定性体检 `_term_is_bound()`：层级首提只接受“催动/运转/突破/只能”等动作或限制词，却没有接受任务合同明确允许的直接对白能力说明。现在仅当首提句确为直接引语，且术语后在 24 字内出现“能/可/只能/不能/借/使/让”等能力或限制关系时才算 Evidence；普通裸术语和“第几阶/承接上一阶”的说明书句仍不通过。第二本离线重算的 `world_introductions.missing_terms` 从 `[ch3 牵丝境]` 变为 `[]`。

相关提交：

- `e19ed48ac`：接受有实际能力含义的直接对白首提，不放宽裸用和说明书句。

### 已生效项的离线回读

- 相邻章开头重复仍为 0，对应 `adjacent_opening_repetitions.count=0`。
- 第 6、9、10 章兑现仍为逐字 Evidence/fulfilled；本轮没有改兑现判据。
- 对手字段、每书境界阶梯、截断放大重试、解释密度和重复检测代码均未改。
- 第二本副本原始第 4 章错位补段仍保留为历史证据；离线检查没有改书稿，因此不能把框架防线误报成该旧正文已修复。

### 预算测试偶发失败

按完整 pytest 节点 `tests/test_webui_deai_budget.py::DeaiBudgetTests::test_create_discards_default_and_custom_deai_budget` 连续运行 20 次，20/20 通过；整文件和全量测试中也通过。代码回读确认 `Queue.create()` 不写 `budget/deai_budget`，fixture 每次使用独立临时目录并屏蔽调度线程/子进程。当前分支无法复现监工的 2/5，且没有失败断言或日志可指向生产代码；因此保持 **Unknown**，没有盲改 `tools/webui/`。监工给出的省略 class 的节点名在当前 pytest 中匹配不到测试，报告采用上述完整节点名。

### 验证与边界

- 全量测试按要求独立跑 3 遍：
  - 第 1 遍：`1789 passed, 2 skipped, 751 subtests passed in 34.01s`；
  - 第 2 遍：`1789 passed, 2 skipped, 751 subtests passed in 35.96s`；
  - 第 3 遍：`1789 passed, 2 skipped, 751 subtests passed in 37.22s`。
- `engine/tools/check_scope.py`：PASS；最大 Python 文件 494 行，无 `bestseller` import、无 bookengine/deai 交叉 import、无禁词。
- `ruff format --check .`：PASS，300 个文件已格式化。
- 未改 `deai/`、`tools/webui/`、真实书库或两个只读副本；未 push、未合并。
- **Unknown / 待网页真机**：第 6 章式关系正文重写能否采用、网页新书关系章数量与连续对抗是否最终达标、兑现补段搬移后模型复检成功率，以及预算测试在监工运行方式下的偶发失败能否复现。
