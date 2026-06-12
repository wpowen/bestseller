# oh-story 对比诊断与融合方案（2026-06-12）

> 起因：安装 oh-story skill（github.com/worldwonderer/oh-story-claudecode），用它的方法为《神仙都是我招的》手写第1章，与框架自动生成版做 4 判官盲评对比。本文档记录诊断结论与「1+1>2」融合工程方案。

## 一、盲评结果

| 判官视角 | oh-story 手写版 | 框架生成版 |
|---|---|---|
| 番茄男频读者 | 85 | 62 |
| 网文签约编辑 | 86 | 61 |
| 严苛书评人 | 82 | 48 |
| 综合裁判 | 89 | 58 |

4:0 一致判 oh-story 版胜。剔除框架版的缝合 bug 后，框架版潜在分约 72——纯方法/文笔仍输 ~13 分。

## 二、病因（三层，按权重排序）

### 病因1：大纲层情绪选型错误（最大头，方法论缺失）
- 男频爽文的黄金第1章交付了「暖+喜剧」（配角哮天犬的催泪故事），不是「爽」。主角全程是温柔旁观者：无低谷、无打脸、无逆袭。
- 主角自己的反差弧线（被坑失业的 HR→手握给神仙发 offer 的权力）被大纲后置到第5章插叙——最爽的牌被焊死在抽屉里。
- 根因：planner 章纲生成没有「每章先定目标情绪→选剧情模式→选钩子类型」的设计纪律。章纲虽有 hook_type/hype_type 字段，但黄金三章只被默认填充 `悬念冲击/8.0`，无选型过程。

### 病因2：第1章信息过载（方法论缺失）
- 框架版 ch1 砸出十几个专有名词（温故/净尘反应/附则七/代偿预扣/内务司免验/流浪编制…），读者没有任何锚点。
- oh-story 的「信息节流」规则：第1章信息分批释放，优先级 危机感>人设>金手指暗示>世界观；新专有名词须有预算上限。
- 框架章纲已有 `chapter_information_introduced/held_back` 字段（outline-v2），但 planner prompt 未对 ch1-3 施加节流约束。

### 病因3：缝合节拍级重复 bug（工程缺陷）
- ch1 把「签字→金光爪印→交天眼徽章→姜子牙→第七任」整套高潮完整写了两遍，且两遍矛盾（年糕前为猫后为细犬；徽章一次出自油布包一次出自颈圈裂缝）。
- 现有 4 层去重全是字面/短行级，对「同一事件节拍被两个场景各自完整重写」无防线。

### 框架并非全输
世界观底料厚度、钩子埋设密度（前六任在职11天、温故便利贴）、规模化状态追踪/闸门/对标回归体系，均为 oh-story（人工 skill 流）不具备的能力。**oh-story 强在单章设计纪律与文字克制，框架强在长程规模与质量基建——融合方向就是把前者的纪律烘焙进后者的管线。**

## 三、融合架构决策

**注入做在 planner（上游设计），检测做在 gate（下游验收），不碰已过载的写手 prompt。**（写手 prompt 曾中位 16.5k/76 块，刚完成瘦身刀①-⑧；消融已证「方法论该烘焙进 planner 而非写时说教」。）

| # | 工程项 | 位置 | 内容 |
|---|---|---|---|
| A1 | 方法卡配置 | config/webnovel_method_cards.yaml | 章尾钩子13式、章首钩子7式、阶段→钩子强度表、情绪→题材映射、黄金一章必达/禁止清单、信息节流预算（蒸馏自 oh-story，自有表述） |
| A2 | planner 注入 | services/planner.py 章纲 prompts | 每章 target_emotion 必填（受控词表）+ hook_type 从13式选型 + ch1-3 黄金开篇约束（主角300字登场/1000字内期待点/禁序章插叙解说/新专有名词≤预算）；outline_field_enrichment 加确定性兜底 |
| A3 | 开篇验收闸门 | services/opening_golden_chapter_gate.py | advanced tier soft 闸门：对 ch1-3 正文检查主角前置/期待点信号/专有名词密度/章末总结体禁止/章末钩子存在/天气风景开场禁忌 |
| A4 | 节拍级去重 | services/drafts.py 第5层防线 | 跨场景节拍重复检测：近逐字句确定性去重，转述级重演打可恢复标记走 repair（不裸抛、不硬阻断） |

## 三.5、落地状态（2026-06-12 当日完成，未 commit）

| # | 状态 | 落点 | 验证 |
|---|---|---|---|
| A1 | ✅ | config/webnovel_method_cards.yaml + quality_levers/webnovel_method_cards.py（13式/7式/5档强度/9情绪映射/黄金规则，缺失软降级） | 7 单测 |
| A2 | ✅ | planner.py 双 prompt 注入（情绪必填+钩子选型+黄金三章块，中英双语+卷位置感知）；ChapterOutlineInput.target_emotion；enrichment 兜底（ch1-3 缺失恒"爽"）+hook 归一化；场景卡透传 | 相关子集 713 passed |
| A3 | ✅ | opening_golden_chapter_gate.py（六检查项，advanced soft，永不阻断）+ gate_registry + pipelines 接线（ch1-3） | 13 单测；gate 家族 840 passed；真书 ch1 专名计数 44>12 检出 |
| A4 | ✅ | deduplication.py 第5层跨场景节拍重演检测（近逐字删/转述簇进 rewrite_task）+ drafts.py 三路径接入 | 8 单测；真 ch1 检出+ch2-9 零误报；相关 182 passed |
| A5 | ✅ | 根因修复：workflows.py cut_point 仅末场景继承章级兜底+entry_state 链式衔接；narrative_contracts.py 同根因路径一并修 | 6 单测；相关子集 1135 passed |

A4 归因更正病因3：重复非拼接层选版问题，而是**章级 cut_point 被逐字扇出进同章每张场景卡**（s02 按卡重演 s01 已写完的高潮）+ s02 entry_state 不衔接 s01 exit_state。A5 已修上游，A4 防线作为拼接层兜底保留。

## 四、对本书的处置

v3 跑出的 ch1-9 正文受病因1-3 三重影响，建议按 v4 设计（见 book-design-v4-shuangfirst-20260612.md）重排开场后重跑卷一。世界观/人物/十卷弧线资产全部保留。
