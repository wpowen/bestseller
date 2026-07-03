# 去AI味：anti-vibe-writing 融合（翻译腔层）——记录与验证（2026-07-03）

## 背景与调研结论

调研 [weijt606/anti-vibe-writing](https://github.com/weijt606/anti-vibe-writing)（v1.6.0，MIT，
非虚构文档去AI味 skill），目标是取其精华融入本框架，让生成正文没有 AI 味。

**取（对小说正文适用、且是我们检测器的真实盲区）**：
- 翻译腔/欧化句式层（源自 yage.ai 拆解）：对…进行 / 使…得到 名词化空转、
  评价式被字句（被…很好地 / 被X所Y）、"作为一个X，…"开头
- 回避系动词"是"抬调子（堪称/可谓/称得上/不啻为）密度
- 破折号段密度（AI 最扎眼的标点痕迹）
- "以前…现在…"时间对比骨架密度
- "形容词+冒号"替读者下判断密度

**弃（糟粕/不适用/已证伪）**：
- 互联网黑话词表（赋能/打通）——小说正文不会出现；且 [[opening-jargon-lever-falsified-ab]]
  已证伪"词表大小预测可读性"
- markdown/结构层规则（首先其次/标题/列点）——小说无此形态
- 人味儿质感往叙述注残句/语气词——与 [[syntactic-rhythm-ai-flavor]] 证伪结论相反
  （anti_ai_voice 曾教出碎句癖）
- 随手打错别字子档——红线
- 复数"们"/主语代词密度——小说里"弟子们/他/她"合法高频，误伤大
- 学习模式 host profile——本框架已有 voice_dna 等价物

## 改动清单

| 文件 | 改动 |
|---|---|
| `data/ai_flavor/patterns_zh.json` | +8 条 discourse 规则（5 条 category=translationese，threshold 1-2；dash_density/then_now_contrast/adjective_colon_verdict 各 1 条）+1 条 cluster 规则（lifted_copula，family_flag，threshold 2） |
| `src/bestseller/services/ai_flavor/detector.py` | 4 个密度型新类别进 `_ADVISORY_STRUCTURAL` 封顶族（不能单独推过 50 修复线）；translationese 独特句式不封顶 |
| `src/bestseller/services/ai_flavor_gate.py` | `translationese` 进 `DESLOP_DISCOURSE_CATEGORIES` 触发集（patcher 改不了句法，只有整段重写能清）；密度型故意不进（防成本/误伤） |
| `src/bestseller/services/deslop_revise.py` | `_EXTRA_SELF_CHECK` 增第 10 条翻译腔自查（含"念出来"自检法），收尾计数 9→10 |
| `tests/unit/test_ai_flavor_translationese.py` | 新增 22 测（每规则正例 + 防误伤负例 + 对白豁免 + 封顶 + 路由正反例 + deslop 同步） |

**防误伤设计**（本仓历史头号坑）：
- "被一掌拍飞"动作被字句 = 地道中文，评价式副词/所字式才命中
- "即使…得到"负向后顾排除，不误配"使…得到"
- "声音很冷：「滚。」"对白引入用负向前瞻豁免，只抓"答案很简单：他早就知道"
- 对白域全部豁免（角色拿腔拿调是合法刻画）；全部 advisory（warn，无自动删改）
- 不动写手 prompt（瘦身纪律 [[prose-prompt-diet]]：prompt 减、检测器量、deslop 清）

## 三层验证

- **L1 单测**：22/22 绿，含 9 个防误伤负例（干净武侠正文全类别零命中即 no-op 证明——
  旧规则数据下新代码行为逐字节等价，因新类别永不触发）。
- **L2 回归**：flavor/deslop/slop 相关 154 测全绿（唯一失败为既有测试断言"上面 9 条"
  字面量，系本次有意扩为 10 条，已同步更新）；cross_book_leakage_guard 5 测绿。
- **L3 真机 A/B**：live DB 拉 40 章真实生成正文（诡异客栈/福星甩不掉/青囊/蚀漏砚等，
  126k 字），旧规则目录 vs 新规则目录零 token 对比：
  - 新规则命中 11/40 章，**全部为 dash_density**（Δ+4 分，advisory 封顶）；
  - **0 章因新规则跨过 50 修复线**（无 churn 风险）；
  - translationese/lifted_copula/then_now/adj_colon 在近期管线产物中 0 命中——
    独特翻译腔已被现有 prompt+deslop 压干净，新规则做的是**残留标点债的测量**
    和**回归防线**（弱模型/新题材再犯时 deslop 循环自动接住）。
  - 脚本：scratchpad `l3/ab_compare.py`（会话级，未入库）。

## 后续可选（未做，按需排期）

- 同义词循环的小说变体（同一角色"少年/青年/男子"轮换指称）需实体感知，规则层做不了；
- anti-vibe 学习模式的量化提取维度（句长分布/句号逗号比/禁忌词+样本证据）可反哺
  voice_dna 提取口径；
- human-texture 语气词情绪指向表（吧=试探/嘛=说服/呗=随意）可做成**对白域**
  advisory craft 素材——须 A/B 后再上（防"装真人"新AI味）。
