# 故事增强 → 正文层接线 · 功能特性记录与验证报告

> 日期：2026-06-18　|　分支：`feat/world-model-derivation-engine`
> 提交：`e9f5c31`（功能）、`052db33`（真机验证脚本）
> 关联能力：[bestseller-story-effect-skills](../.claude/skills/bestseller-framework/SKILL.md)、脑洞引擎、`brainhole_engine`
> 本文档遵循并示范《[开发与验证标准](开发与验证标准-feature-lifecycle-20260618.md)》

---

## 0. TL;DR（一句话结论）

「故事增强」（脑洞 / 喜剧 / 爽点 / 反转…）此前**只接通到大纲层，正文写手对所选增强完全失明**——勾了增强，正文里却看不到核心元素。本次把增强真正端到了写手面前：写手 prompt 现在同时携带「书级基调合同」与「本章已规划的兑现点」。**真机端到端验证 PASS，零副作用。**

---

## 1. 背景与根因

### 1.1 用户症状
选了「故事增强」后，生成的正文里没有这些核心元素（脑洞名场面 / 喜剧落点 / 爽点），怀疑功能没真正生效。

### 1.2 根因：大纲层接通，正文层断连（三处断点，均有代码证据）

| # | 断点 | 证据 |
|:--|:--|:--|
| ① | **写手 prompt 0 引用增强** | `services/drafts.py` 的 `build_scene_draft_prompts` 全文件 grep `brainhole / selected_effect / comedy / hype` → 无命中。写手只靠 `prompt_pack + writing_profile.market.platform_target` 知道「题材 label」，不知道「基调」与「所选增强」 |
| ② | **持久化丢字段** | `services/workflows.py:1851` `_sync_chapter_causality_metadata` 把章纲字段拷进 `chapter.metadata_json`，拷了 `causal_contract / methodology_contract / key_reveals` 等，**唯独不拷** `brainhole_contract` 与 `selected_effect_skills` → 章纲 LLM 按合同兑现的内容落库即丢 |
| ③ | **物化器不搬运** | `services/chapter_scene_contract_materializer.py` 把章纲拆成场景卡时只读 `chapter_goal / opening_situation / main_conflict / chapter_emotion_arc / hook_description`，不搬增强字段 |

补充事实：`ChapterModel`（`infra/db/models.py:531`）没有增强字段的专列，唯一可承载处是 `metadata_json`；大纲层注入是健康的——`planner.py:13200` 的 `_story_enhancer_contract_line` 把书级合同注入了全部 6 个大纲阶段（BookSpec / World / Cast / 卷纲 / 大纲 / 卷章纲）。

**结论：内容生产在大纲层是对的，断点在「大纲 → 持久化 → 物化 → 写手 prompt」这条交接链上。**

---

## 2. 设计与落地

### 2.1 设计原则（遵循「闸门自伤」历史教训）
- **soft / additive**：纯新增 prompt 注入，不新增硬闸门、不阻断成书。
- **未勾选时字节级不变**：无增强选择且无章级合同 → 渲染空串 → prompt 与历史完全一致。
- **单一真源**：书级合同复用既有 `render_story_enhancer_contract_block`，永不与大纲层合同漂移。
- **输入封顶**：每字段截断 240 字，防止冗长合同撑爆 prompt 预算。

### 2.2 改动清单（3 源文件 + 1 测试，纯新增 351 行）

| 文件 | 改动 |
|:--|:--|
| `services/story_enhancers.py`（+124） | 新增 `render_story_enhancer_writer_block(project_meta, chapter_meta)`：① 书级合同（复用 `render_story_enhancer_contract_block`）② `_render_chapter_cashed_effects_block` 渲染本章 `brainhole_contract` / `selected_effect_skills`（字段人类标签化、240 字封顶） |
| `services/workflows.py`（+15） | `_sync_chapter_causality_metadata` 增拷 `brainhole_contract` + `selected_effect_skills` 进 `chapter.metadata_json`（镜像 `causal_contract` 的 truthy→set / else→pop 写法） |
| `services/drafts.py`（+15） | `build_scene_draft_prompts` 渲染上述 block，拼进 `user_prompt`（紧跟 `_concept_lab_contract_line`，zh / en 双分支） |
| `tests/unit/test_story_enhancer_writer_injection.py`（+197） | 10 条单测 |

### 2.3 运行时数据流（修复后）
```
章纲 LLM 兑现 → brainhole_contract / selected_effect_skills
   → _sync_chapter_causality_metadata 持久化进 chapter.metadata_json   ← 修复②
   → build_scene_draft_prompts 读 project.metadata_json + chapter.metadata_json
   → render_story_enhancer_writer_block 渲染「书级合同 + 本章兑现点」     ← 修复①
   → 拼进 user_prompt → 写手 LLM
```

---

## 3. 验证（三层）

### 3.1 L1 单元测试（10 条，全绿）
`tests/unit/test_story_enhancer_writer_injection.py`：
- 渲染空（未勾选 / 无关 metadata）→ `""`
- 书级合同携带（comedy 基调锚点 / 脑洞）
- 本章脑洞兑现点（字段人类标签化）
- selected_effect_skills 的 `expected_contracts`（dict 与 list 两种形态）
- 长文本 240 字封顶
- 持久化增拷 + pop 陈旧
- **端到端 prompt：勾选时含增强块；未勾选时与历史字节级一致（no-op 断言）**

### 3.2 L2 回归（无新增失败）
- enhancer + drafts 内容 **103 passed**（1 条 `test_story_effect_skill_catalog` 失败已用 `git stash` 在干净树 `b31f62b` 复现 → 确认为**既有失败、与本次无关**）
- prompt 构造器（5 文件）**48 passed**
- workflows 集成 **39 passed**

### 3.3 L3 真机端到端（live docker 栈，零 token，零副作用）
脚本：`scripts/verify_story_enhancer_prose_e2e.py`
- **真实运行时路径**：`generate_scene_draft → build_scene_writer_context_from_models → build_scene_draft_prompts`
- **底料**：真实已生成喜剧书 `zhaoshen-hr-v3-1781180702` 第 1 章《哮天犬要辞职》第 1 场景
- **零 token**：`complete_text` 全模块打桩
- **零副作用**：`BaseException` 哨兵在写手调用前截获真实 prompt；`session_scope` 因哨兵非 `Exception` 跳过 commit → 真实书 DB 不被污染
- **对比两变体**（同一真实章节）：

| 正文 prompt 是否携带 | A 基线（未勾增强） | B 勾选增强 + 同步本章合同 |
|:--|:--:|:--:|
| 故事 / 内容（本章在讲什么） | ✅ `哮天犬要辞职` | ✅ |
| 规则（世界观 / 设定 / 圣经） | ✅ `故事圣经` | ✅ |
| 限定条件（字数 / 硬指标 / 守则） | ✅ `字数` | ✅ |
| 风格（题材 / 流派 / 语气） | ✅ `你主攻…流派` | ✅ |
| **故事增强（书级合同）** | **— 缺失（=修复前真实状态）** | **✅ `故事增强`** |
| **本章增强兑现点（脑洞落点）** | **— 缺失** | **✅ `本章已规划的脑洞`** |

- **B 变体实注入片段**（节选）：书级合同头 → `基调锚点·硬底线`（本书是爽文喜剧，每章须有喜剧落点）→ `脑洞引擎` → `comedy_engine` / `hype_satisfaction_engine` 合同；外加本章具体落点 `第301次辞职`、`驱邪符`、`HR系统识别成连续病假条`、`comic_effect_contract`。
- **体量**：user prompt 40802 → 42169 字（+~1.4k，块有意义且受控）。
- **DB 未污染核验**：项目 `has_enhancers=f`、ch1 无 `brainhole_contract` / `selected_effect_skills`（哨兵跳过 commit 生效）。

复跑：`.venv/bin/python scripts/verify_story_enhancer_prose_e2e.py [<slug> <chapter> <scene>]`

---

## 4. 结果与结论

🟢 **端到端验证 PASS。** 正文写手现在被明确告知「在写什么调性的故事 + 这一章要落地哪个增强 beat」，不再只有一个题材标签。基础四维（故事 / 规则 / 限定 / 风格）本就到位；本次补齐了缺失的「故事增强」一维。

## 5. 提交记录
- `e9f5c31` feat: 故事增强接进正文层——书级合同+本章兑现点注入写手prompt
- `052db33` test: 故事增强→正文 真实端到端验证脚本

## 6. 一句话总评
验「功能没生效」要把链路走到底——**大纲达标 ≠ 正文兑现**；大纲 → 持久化 → 物化 → 写手 prompt 任一环不传字段就断。查写手是否真被告知，用 grep 写手 prompt builder 的字段名。
