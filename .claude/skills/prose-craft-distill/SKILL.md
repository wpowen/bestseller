---
name: prose-craft-distill
description: 把一批中文名句/绝句蒸馏成「文采技法骨架」，扩充 config/prose_craft_techniques.yaml（金句构造层）。蒸技法不蒸词句、合成跨题材例、内置辞藻堆砌守卫。当用户提供名句集/美文集并希望提升框架正文文采时使用。
trigger_keywords:
  - 文采
  - 金句
  - 绝句
  - 名句蒸馏
  - prose craft
  - 签名段
  - golden line
  - 文采技法
---

# Prose-Craft Distill · 文采技法蒸馏

把一批「写得好、值得摘抄」的中文句子（名人名句 / 古风绝句 / 现代文艺句 / 丧系句）
蒸馏成**可迁移的修辞技法骨架**，写入 [`config/prose_craft_techniques.yaml`](../../../config/prose_craft_techniques.yaml)，
让写手在落「签名段 / 金句」时有「怎么写」的方法，而不是靠运气。

## 这套能力在框架里的位置（先读懂再改）

- 框架**已有**「神描写」能力：`config/writing_methodology.yaml::visual_writing`（镜头公式 / 动词代形容词 / 五感）
  + `config/prose_style_anchors.yaml`（反 AI 腔 + 作家声口一致性）
  + `config/chapter_signature_audit.yaml`（每章 ≥1 截图段，golden_line 是其中一类）。
- 缺的是「神句子」：让**单句**有记忆点结构、值得截图摘抄。本能力补这一块。
- 注入点：`services/methodology_compiler.py` 的 **PROSE_SCENE** 阶段，渲染
  `quality_levers/prose_craft_techniques.render_prose_craft_block(genre_terms, chapter_number)`，
  紧挨 `prose_style_anchors`。**纯 soft**：是写法建议，不进任何 gate / floor / must_rewrite。

## 三条铁律（违背就会引发历史回归）

1. **蒸技法骨架，不蒸成品词句。** 词句直拼会盖掉模型输出、让全书同质化
   （参见记忆 `title-generation-template-override-regression`）。每条技法记录的是
   *原理 + 结构 + 适用/规避题材 + 堆砌风险*，不是可复制的句子。
2. **micro_examples 必须是合成原创且跨题材。** 用都市/科幻/职场/悬疑的例子证明技法可迁移，
   严禁把源句原样搬进 KB（否则写手会照抄）。同一技法给 ≥2 个不同题材的例子。
3. **永远 soft，绝不硬阻断。** 文采不影响故事性，不得进 must_rewrite / 硬 floor。
   只做写手提示词注入 + 可选的 advisory。

## 蒸馏流程

### 1. 读懂素材，归类技法
通读名句集，按「让这句话可摘抄的**结构性原因**」归类，而不是按题材或情绪。常见技法：
意象并置、对仗·排比、通感、虚实相生、数量词张力、以景结情·留白、反差·顿挫、白描克制、口语锋利。
新素材若出现已有技法覆盖不到的骨架，再新增一条。

### 2. 写 `techniques.<id>` 条目
对每条技法填：`display_name` / `category`(structure|texture|voice) / `principle`(为什么这样写会被记住) /
`structure`(可操作的骨架) / `genre_fit`(good/careful/avoid) / `purple_risk`(它退化成辞藻堆砌的样子) /
`micro_examples`(≥2 个合成、跨题材、带 `tag`)。

### 3. 维护 `genre_emphasis` 路由
为每个题材族列出**强调哪些技法**。关键：现代题材（都市/职场/科幻/现实）**不要**路由到
古风意象类技法，否则会把现代文紫化。未命中题材落 `default`。

### 4. 维护 `purple_prose_guard`
从素材里**最差**的那批长抒情堆砌段反向蒸馏失败模式（堆空词 / ≥3 意象不收口 /
无具体物的抽象感叹 / 形容词叠加），写进 `banned_moves`。它会渲染进写手块尾部当反例。

### 5. 验证（必须，证明真实起效）
- 单测：`.venv/bin/python -m pytest tests/unit/test_prose_craft_techniques.py -q --no-cov`
  （覆盖路由 / 轮换 / 反紫化 / PROSE_SCENE 注入 / soft 保证）。
- 回归：`pytest tests/services/test_methodology_compiler.py tests/unit/test_quality_levers_prose_style_anchors.py tests/unit/test_audit_loop.py -q --no-cov`。
- A/B 实测：`.venv/bin/python scripts/verify_prose_craft_ab.py`
  （同一 scene，baseline vs +craft，比 LLM 文采分 / golden_line 密度 / purple 率；
  确认现代题材不被紫化）。

## 文件清单
- KB：`config/prose_craft_techniques.yaml`
- 加载/渲染：`src/bestseller/services/quality_levers/prose_craft_techniques.py`
- 配线：`src/bestseller/services/methodology_compiler.py`（PROSE_SCENE 阶段）
- 测试：`tests/unit/test_prose_craft_techniques.py`
- A/B：`scripts/verify_prose_craft_ab.py`
