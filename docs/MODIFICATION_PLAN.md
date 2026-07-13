# BestSeller 全链路修改计划

> 基于 `docs/FULL_PIPELINE_ANALYSIS.md` 中的 41 个问题，制定分阶段、可执行、可验证的修改计划。
>
> 原则：小步快跑、每步可独立验证、不破坏现有功能。
>
> 复核修订：2026-07-10——对照代码核验后修正了 Task 0.1（resolve_selection 真实签名）、Task 0.5（用已有 JudgeGenreContext）、Task 1.1（重复仅存在于场景路径）、Task 1.2/1.4（示例代码引号语法错误）、Task 1.5（现无实际重复，改为边界+守卫）、Task 2.1/2.3（对齐 conception 真实调用形态与降级机制）、Task 3.1/3.3（OOXML/reportlab 用法错误）、Task 4.1-4.3（缺 DB 字段需迁移、APScheduler 短任务不宜 sleep 退避）。

---

## 目录

- [修改阶段总览](#修改阶段总览)
- [Phase 0 — 数据完整性修复（1-2天）](#phase-0--数据完整性修复1-2天)
- [Phase 1 — 提示词去重与重构（3-5天）](#phase-1--提示词去重与重构3-5天)
- [Phase 2 — 创建路径收敛（3-5天）](#phase-2--创建路径收敛3-5天)
- [Phase 3 — 导出质量提升（3-4天）](#phase-3--导出质量提升3-4天)
- [Phase 4 — 发布可靠性（2-3天）](#phase-4--发布可靠性2-3天)
- [Phase 5 — Planner 对称性与降级治理（3-5天）](#phase-5--planner-对称性与降级治理3-5天)
- [Phase 6 — 架构治理（长期）](#phase-6--架构治理长期)
- [测试策略](#测试策略)
- [回滚策略](#回滚策略)

---

## 修改阶段总览

```
Phase 0 (数据完整性)    ████░░░░░░  独立，无依赖
Phase 1 (提示词去重)    ░░██████░░  独立，无依赖
Phase 2 (路径收敛)      ░░░░████░░  依赖 Phase 0
Phase 3 (导出质量)      ░░░░████░░  独立，无依赖
Phase 4 (发布可靠性)    ░░░░░░████  独立，无依赖
Phase 5 (Planner治理)   ░░░░░░████  依赖 Phase 1
Phase 6 (架构治理)       ░░░░░░░░██  长期，依赖前5个Phase
```

Phase 0/1/3/4 可并行执行，互不依赖。

---

## Phase 0 — 数据完整性修复（1-2天）

> 目标：修复数据丢失/不一致问题，风险最低、收益最高。

### Task 0.1：CLI 增加 resolve_selection 调用

**对应问题**：P1-2

**文件**：`src/bestseller/cli/main.py`

**当前代码**（行 1657-1668）：
```python
project_payload=ProjectCreate(
    slug=slug,
    title=title,
    genre=genre,           # ← 原始字符串
    sub_genre=sub_genre,
    ...
    metadata={"premise": premise},
    ...
)
```

**修改方案**：

在 `_run()` 函数内、构造 `ProjectCreate` 之前，增加题材解析：

```python
from bestseller.services.genre_taxonomy import resolve_selection

# 真实签名：resolve_selection(channel: str | None, genre: str | None,
#                              sub_genre: str | None = None, tags: ... = None)
# channel 允许 None，由 taxonomy 依题材自行推断（与 API 路径行为一致，
# api/routers/projects.py:48 传的就是 body.channel 可空值）
resolved = resolve_selection(channel, genre, sub_genre)
metadata = {
    "premise": premise,
    "genre_canonical": resolved.genre_key,
    "genre_category": resolved.category,
    "genre_pack": resolved.pack,
}
```

同时给 CLI 命令增加可选 `--channel` 参数（默认 `None`，即交由 taxonomy 推断；可显式传 `male`/`female`）。

**影响范围**：仅 CLI 入口，不影响已有项目。

**验证方式**：
- 用 CLI 创建项目后，检查 `project.metadata` 中是否包含 `genre_canonical`
- 用相同 genre 通过 CLI 和 API 分别创建项目，对比 metadata 一致性

---

### Task 0.2：API genre 字段使用 resolved 结果

**对应问题**：P1-3

**文件**：`src/bestseller/api/routers/projects.py`

**当前代码**（行 59-63）：
```python
project_create = ProjectCreate(
    slug=body.slug,
    title=body.title,
    genre=body.genre,                    # ← 原始字符串
    sub_genre=body.sub_genre or resolved.sub_genre_str,
    ...
)
```

**修改方案**：

行 62 改为：
```python
genre=resolved.genre_str or body.genre,
```

这样 `project.genre` 存储的是 canonical 化后的题材名，与 `metadata.genre_canonical` 保持一致。

**影响范围**：API 创建路径。已存在的项目不受影响（`genre` 字段不回溯修改）。

**验证方式**：
- API 传入 `genre="修仙"`，检查 `project.genre` 是否为 canonical 名（如 `"仙侠"`）
- 下游 `resolve_genre_review_profile(project.genre, ...)` 不再需要 fallback canonicalize

---

### Task 0.3：单章导出增加净化和标题

**对应问题**：P6-2

**文件**：`src/bestseller/services/exports.py`

**当前代码**（行 1455）：
```python
storage_uri, checksum = write_binary_output(
    output_path, build_docx_bytes(title, draft.content_md)
)
```

**修改方案**：

在 `export_chapter_docx` / `export_chapter_epub` / `export_chapter_pdf` 三个函数中，统一增加净化步骤。注意：单章 **Markdown** 导出（exports.py:1380-1382）已经做了这两步，可直接复用其做法——`sanitize_novel_markdown_content`（定义在 drafts.py:3123）+ 已有的 `_ensure_chapter_heading`（exports.py:61，不必手写 startswith 判断）：

```python
from bestseller.services.drafts import sanitize_novel_markdown_content

# 与单章 Markdown 导出（行 1380-1382）保持同一处理
clean_content = sanitize_novel_markdown_content(
    _ensure_chapter_heading(draft.content_md, chapter)
)

storage_uri, checksum = write_binary_output(
    output_path, build_docx_bytes(title, clean_content)
)
```

提取为一个 helper 函数 `_prepare_chapter_content(draft, chapter)` 避免三处重复（参数与 `_ensure_chapter_heading` 的真实签名对齐后再定）。

**影响范围**：单章导出路径。项目级导出不受影响（已有净化）。

**验证方式**：
- 生成一个包含 HTML 注释的草稿，单章导出后检查 DOCX/EPUB/PDF 中不含元数据
- 检查导出文件包含章节标题

---

### Task 0.4：导出跳过章节时返回警告

**对应问题**：P6-1

**文件**：`src/bestseller/services/exports.py`

**当前代码**（行 851-861）：
```python
chapter_payloads: list[tuple[ChapterModel, ChapterDraftVersionModel]] = []
for chapter in chapters:
    draft = await session.scalar(...)
    if draft is None:
        continue                    # ← 静默跳过
    chapter_payloads.append((chapter, draft))
```

**修改方案**：

1. 修改 `_load_project_export_payload` 返回跳过的章节号：

```python
async def _load_project_export_payload(
    session, project_slug
) -> tuple[ProjectModel, list[tuple[ChapterModel, ChapterDraftVersionModel]], list[int]]:
    # ...
    skipped: list[int] = []
    for chapter in chapters:
        draft = await session.scalar(...)
        if draft is None:
            skipped.append(chapter.chapter_number)
            continue
        chapter_payloads.append((chapter, draft))
    return project, chapter_payloads, skipped
```

2. 在 `export_project_markdown` / `export_project_docx` / `export_project_epub` / `export_project_pdf` 中，如果有跳过的章节，在返回结果中增加警告：

```python
project, chapter_payloads, skipped = await _load_project_export_payload(session, project_slug)
if skipped:
    logger.warning("Export skipped chapters without drafts: %s", skipped)
    # 在 ExportResponse 或返回值中增加 skipped_chapters 字段
```

3. 修改调用方解构：所有调用 `_load_project_export_payload` 的地方需要适配新的返回值。`ExportResponse` 定义在 `src/bestseller/api/routers/exports.py:17`（现有字段仅 project_slug/format/file_path/word_count），`skipped_chapters: list[int]`/`warnings: list[str]` 加在这里；服务层函数的返回结构也要同步带出。

**影响范围**：导出服务层 + 所有调用方（4 个 export_project_* 函数）。

**验证方式**：
- 创建一个项目，删除某章的 current draft，导出后检查返回值中是否包含 `skipped_chapters`

---

### Task 0.5：评判 prompt 移除残留题材偏向

**对应问题**：P5-1

**文件**：`src/bestseller/services/chapter_llm_quality_judge.py`

**修改方案**：

行 201-203，将硬编码的"悬疑/驱魔"改为动态生成。该文件已接入 `JudgeGenreContext`（经 `resolve_judge_genre_context`，行 23-26），题材标签字段是 `display_genre`（judge_genre_context.py:166），无需新造字段：

```python
# 旧
"以下是同类型（悬疑/驱魔）榜单级章节的代表性开篇片段"

# 新
f"以下是同类型（{genre_context.display_genre}）榜单级章节的代表性开篇片段"
```

行 242-249，将 `failing_examples` / `passing_examples` 改为从 config 文件按题材加载，或使用更通用的示例。短期可先用通用示例替换。

**影响范围**：仅评判 prompt 文本，不影响评判逻辑。

**验证方式**：
- 对不同题材的项目运行评判，检查 reference block 中的题材标签是否正确

---

## Phase 1 — 提示词去重与重构（3-5天）

> 目标：消除提示词中的重复内容，拆分过载块，统一冲突指令。
> 这是最复杂的 Phase，需要逐项修改并做 A/B 对比。

### Task 1.1：提取统一的 AI 套话黑名单

**对应问题**：P4-1

> 复核注：重复注入只发生在**场景级路径**（`_NOVEL_OUTPUT_PROHIBITION` 内部两处 + EXAMPLES 段，约 3 次）；整章路径黑名单只出现 1 次。`build_anti_slop_footer`（prompt_constructor.py:900-925）只含通用铁律、不含具体套话，**不需要改**。本 Task 的核心收益是单一数据源可维护性 + 场景路径去重。

**文件**：
- 新建：`src/bestseller/services/ai_slop_blacklist.py`
- 修改：`src/bestseller/services/drafts.py`（行 3394-3425 中文 `_NOVEL_OUTPUT_PROHIBITION`（黑名单在 3405、3411-3418 两处）、行 3427-3462 英文版、行 6202-6209、行 8654-8658）

**修改方案**：

1. 新建 `ai_slop_blacklist.py`，定义单一数据源：

```python
"""Single source of truth for AI slop phrases."""

# 中文套话黑名单 — 在 prompt 中只注入一次
ZH_SLOP_PHRASES: tuple[str, ...] = (
    "血液仿佛凝固了", "血液冰封", "浑身的血液都冷了",
    "空气仿佛凝固了", "时间仿佛静止了", "周围的一切仿佛都消失了",
    "心中五味杂陈", "心中百感交集", "眼眶不由得湿润了",
    "一股莫名的情绪", "一种说不清的感觉", "一阵莫名的恐惧",
    "电流般的感觉", "触电般的感觉", "沉甸甸的",
    "仿佛有一只无形的手", "像是被什么东西攫住了",
)

ZH_SLOP_OPENERS: tuple[str, ...] = (
    "显而易见", "毫无疑问", "不言而喻",
)

ZH_SLOP_ENDINGS: tuple[str, ...] = (
    "这一切才刚刚开始", "真正的答案还在等待揭开", "欲知后事如何",
)

# 英文版
EN_SLOP_PHRASES: tuple[str, ...] = (...)
EN_SLOP_OPENERS: tuple[str, ...] = (...)

def render_slop_blacklist_block(language: str) -> str:
    """渲染为 prompt 块，在 prompt 中只调用一次。"""
    if language.lower().startswith("zh"):
        phrases = "\n".join(f"- 「{p}」" for p in ZH_SLOP_PHRASES)
        openers = "、".join(ZH_SLOP_OPENERS)
        endings = "\n".join(f"- 章末「{e}」" for e in ZH_SLOP_ENDINGS)
        return f"【AI套话黑名单——绝对禁止】\n{phrases}\n- 任何以「{openers}」开头的句子\n{endings}"
    # 英文版...
```

2. 在 `drafts.py` 的 `_NOVEL_OUTPUT_PROHIBITION`（中英文版）中，删除内部两处套话列举（行 3405、3411-3418），保留其他禁止项。

3. 在 `drafts.py` 的场景级 system prompt（行 6202-6209）和整章 system prompt（行 8654-8658）中，删除 `# EXAMPLES · AI 套话黑名单` 段。

4. 在两条路径各自 prompt 装配的最后阶段（user_prompt 尾部），统一注入一次 `render_slop_blacklist_block(language)`。

5. `build_anti_slop_footer` 保持不变（其中没有具体套话）；后置检测器（deslop 等）改为 import `AI_SLOP_BLACKLIST` 常量，保证检测词表与 prompt 禁词表同源。

**关键决策**：黑名单放在 user_prompt 的什么位置？

建议放在 output_rules 之后、must_keep_tail_blocks 之前——这个位置是 LLM 注意力最高的尾部区域之一。

**影响范围**：所有章节生成路径的 prompt。需要做 A/B 对比验证。

**验证方式**：
- 对比修改前后 prompt 的 token 数（场景级路径预计减少数百至 ~1000 token；整章路径本无重复，token 基本持平）
- 对同一章节卡生成 3 次，对比 AI 套话出现频率是否上升
- 运行 `tests/` 下的 prompt 相关测试

---

### Task 1.2：拆分 output_rules 块

**对应问题**：P4-2

**文件**：`src/bestseller/services/drafts.py`（行 8849-8891）

**修改方案**：

将 `output_rules`（近 20 类约束）拆分为 6 个独立块：

```python
# 替代原来的单个 output_rules

output_word_count_rules = (
    f"【字数与结构】\n"
    f"正文篇幅硬范围是 {hard_min_words}-{hard_max_words} 个汉字，"
    f"目标约{hard_target_words}字；"
    f"字数是硬交付：少于 {hard_min_words} 字就是失败。"
    f"全文建议22-32段，最多36段；单场通常 {per_scene_min}-{per_scene_max} 字，"
    f"每场5-8段为主，至少4段正在发生的戏；单段通常45-95字。"
)

output_scene_rules = (
    "【场景执行规则】\n"
    "不得把场景卡压缩成一句概述；每个场景必须写出现场空间、角色动作、"
    "可见物证变化、人物反应和至少一轮有辨识度的对话。"
    "任何一场到第8段还没完成离场状态，必须用1段收束并进入下一场。"
    "场景卡的入场状态、离场状态和 forbidden_actions 是硬边界。"
    "正文不得使用 ---、***、空行切场、场景标题或小节分隔符。"
    "每次更换地点或时间，必须先写一句可见转场动作。"
)

output_safety_rules = (
    "【内容安全规则】\n"
    "未写在场景卡、章节契约、角色安全块或故事圣经里的死亡、"
    "关键不可逆事件、额外活人 NPC 等一律禁止。"
    "不得临时发明未在场景卡、角色池、章节契约或故事圣经中出现的人名；"
    "功能性人物只用司机、邻居、保安等身份称谓。"
    "如果角色安全块要求某角色本章不能确认死亡，"
    "连疑问句、传闻句和旁人推测式也不能写。"
)

output_character_rules = (
    "【角色认知规则】\n"
    "非专业角色只能描述自己亲眼看见的异常、听来的警告或身体反应；"
    "除非角色认知状态明确写明，否则不得让普通配角主动说出"
    "或理解本书的核心机制/规则专名。"
    "叙述者也不要替普通角色贴规则标签。"
    "电话/短信只能作为同一视角内的现实沟通工具。"
)

output_chapter_end_rules = (
    "【章末规则】\n"
    "章末最后120字必须满足「钩子+落地帧」："
    "可以抛出新危险或新信息，但最后一句必须是"
    "现场内可看见的完成画面、人物动作、物件变化或选择点。"
    "章末只能保留一个主钩子，不得连续堆叠多个未解悬念。"
    "到最后一个场景的尾钩落成后必须停止。"
)

output_trim_rules = (
    "【删减策略】\n"
    f"如果准备写超过42段或超过{hard_max_words}字，"
    "必须优先删解释、删重复氛围、删二次推理。"
    "如果信息量装不下，优先删解释和术语，"
    "保留动作、冲突、人物选择和章末钩子。"
)
```

在 user_prompt 拼接中，将原来的单个 `"【输出要求】\n" + output_rules` 替换为上述 6 个块按顺序拼接。

**影响范围**：仅 chapter-first 路径的 user_prompt 结构。

**验证方式**：
- 对比修改前后章节质量评分（LLM judge 分数）
- 检查 LLM 是否更好地遵守了各块约束（特别是角色安全、章末规则）

---

### Task 1.3：统一字数控制指令

**对应问题**：P4-3

**文件**：
- `src/bestseller/services/prompt_constructor.py`（行 367-370）
- `src/bestseller/services/drafts.py`（行 8642、行 8959、行 8849-8854）

**修改方案**：

1. 在 `prompt_constructor.py` 的 invariants_section 中，只保留简短的字数声明：
   ```python
   f"【章长度】{min}–{max} 字（目标 {target}）"
   ```

2. 在 `drafts.py` 的整章 system CONSTRAINTS 中，删除字数行（行 8642），因为 user_prompt 的 `【字数与结构】` 块（Task 1.2 产出）已经包含完整字数约束。

3. 在 `drafts.py` 的章节目标段（行 8956-8960）中，删除字数信息（已被 `【字数与结构】` 块覆盖），只保留章节目标：
   ```python
   "【章节目标】\n"
   f"作品：{project.title}\n"
   f"章节：第{chapter.chapter_number}章 {chapter.title or ''}\n"
   f"章节目标：{chapter.chapter_goal or ''}"
   ```

4. 在 `drafts.py` 的场景级 system（行 6177）中，删除 `"CJK 汉字数须在目标的 90%-120% 之间"` 行（已被 invariants_section 覆盖）。

**最终效果**：字数约束只在两个地方出现：
- invariants_section（简短声明）
- `【字数与结构】` 块（详细规则）

**影响范围**：所有章节生成路径。

**验证方式**：
- 检查生成章节的字数是否仍在硬范围内
- 对比修改前后字数分布是否有变化

---

### Task 1.4：统一黄金三章规则

**对应问题**：P4-4

**文件**：
- 新建 helper：`src/bestseller/services/golden_rules.py`
- 修改：`src/bestseller/services/prompt_constructor.py`（行 402-412）
- 修改：`src/bestseller/services/drafts.py`（行 8745-8768、行 6196-6200）
- 修改：`src/bestseller/services/chapter_llm_quality_judge.py`（行 230-250）

**修改方案**：

1. 新建 `golden_rules.py`，定义统一的黄金三章规则：

```python
def render_golden_three_rules(
    chapter_number: int,
    language: str,
    path_mode: str = "chapter_first",  # "chapter_first" | "scene" | "judge"
) -> str:
    """统一的黄金三章规则渲染器。"""
    if chapter_number > 3:
        return render_front_ten_rules(chapter_number, language)
    
    # 第 1-3 章
    if language.lower().startswith("zh"):
        return (
            "【黄金三章·开篇硬契约】\n"
            "1. 前 100 字：必须给读者可感知的压力或异常（视觉/听觉/物件异常）。\n"
            "2. 前 300 字：主角必须表现出一个可代入的人性破绽。\n"
            "3. 前 500 字：主角必须因异常被迫做出决定（不能只是观察/对话/回忆）。\n"
            "4. 前 800 字：读者必须从动作/对白中自然得知——主角身份处境、"
            "地方/局势、主宰本章生死的核心规则（规则第一次生效前必须对读者完整可见）、"
            "金手指首次生效时读者能一句话说出它是什么。\n"
            "5. 章末必须留下一个能让读者立刻点开下一章的具体悬念，"
            "最后一句必须落在完成画面帧、人物动作、物件变化或选择点。\n"
            "6. 句法节奏：禁止「一拍一段」分镜腔——单句独段连续≤2段、全章占叙述段<1/4。\n"
        )
    # 英文版...
```

2. `prompt_constructor.py` 的 `build_opening_hook_directive` 改为调用 `render_golden_three_rules`。

3. `drafts.py` 的 `opening_retention_rules` 改为调用 `render_golden_three_rules`。

4. `drafts.py` 场景级的 `# OUTPUT FORMAT · 开篇硬指标` 改为调用 `render_golden_three_rules(path_mode="scene")`（精简版）。

5. `chapter_llm_quality_judge.py` 的冷读者五锚点检查项从 `render_golden_three_rules` 派生，确保评判标准与生成标准一致。

**影响范围**：所有章节生成和评判路径。

**验证方式**：
- 检查修改后四处规则文本完全一致
- 对比修改前后黄金三章的质量评分

---

### Task 1.5：理顺双轨装配边界 + 唯一性守卫

**对应问题**：P4-5

> 复核注：经核验，l3 块的实际链路是 `build_chapter_l3_blocks`（prompt_constructor.py:1311）→ `ChapterL3Blocks.as_prompt_block()` → `pipelines.py:5060` 存入 `shared_context.l3_prompt_block` → **仅场景级路径**在 drafts.py:6642-6643 注入；整章路径不消费 l3 块，且 drafts.py 未另行调用 `build_anti_slop_footer`——**当前没有实际重复可删**。本 Task 从"去重"改为"定边界 + 防回归守卫"。

**文件**：`src/bestseller/services/prompt_constructor.py`、`src/bestseller/services/drafts.py`、新增测试

**修改方案**：

明确职责分工：
- L3 块（prompt_constructor.py）负责**跨章节稳定段**：invariants、voice_dna、seam_contract、methodology_inject
- `drafts.py` 负责**章节级动态段**：场景卡、契约、上下文、output_rules、章末规则

具体操作：
1. 新增"关键块唯一性"回归测试：对场景级和整章两条路径分别装配一次完整 prompt，断言去 AI 味铁律、字数块、黄金三章块等标记段各只出现一次（用块标题字符串计数）。这是防止未来任一侧改动引入重复的守卫。
2. 作为防御性写法，在装配处保留哨兵检查（供未来整章路径接入 l3 时不引入重复）：

```python
# drafts.py 装配处（防御性哨兵，当前不改变行为）
has_l3_anti_slop = "去AI味写作铁律" in (l3_prompt_block or "")
if not has_l3_anti_slop:
    system_prompt += build_anti_slop_footer(language)
```

3. 评估整章路径（当前主路径）是否接入 l3 稳定段——它现在享受不到 L3 构造器的 invariants/voice_dna/seam_contract 统一渲染。

**影响范围**：章节生成 prompt 装配逻辑 + 测试。

**验证方式**：
- 唯一性测试对两条路径均通过
- 打印完整 prompt 抽查，确认关键块各只出现一次且 token 数无异常增长

---

## Phase 2 — 创建路径收敛（3-5天）

> 目标：让 CLI/API 也能使用 Conception Pipeline。
> 依赖 Phase 0 完成（CLI 已有 resolve_selection）。

### Task 2.1：提取 Conception 为独立服务函数

**对应问题**：P1-1

**文件**：
- 修改：`src/bestseller/services/conception.py`（行 3219）
- 修改：`src/bestseller/services/pipelines.py`（`run_autowrite_pipeline`）
- 修改：`src/bestseller/cli/main.py`（行 1651-1677）

**修改方案**：

1. 确认 `run_conception_pipeline` 已经是独立函数（conception.py:3219），只是 CLI/API 没调用它。真实签名（无 client/project 参数，**不依赖已创建的项目**）：`run_conception_pipeline(session, settings, *, genre_key, chapter_count, user_hints, story_facets, progress, genre, sub_genre)`。Web 路径的顺序是先 conception（server.py:3380）再 autowrite（server.py:3553），conception 结果回填 ProjectCreate。

2. 在 `run_autowrite_pipeline`（pipelines.py:11241，真实签名 `(session, settings, *, project_payload, premise, requested_by, export_markdown, auto_repair_on_attention, progress)`）中增加可选的 conception 步骤，放在 `create_project` **之前**（对齐 Web 路径的先后顺序）：

```python
async def run_autowrite_pipeline(
    session, settings, *,
    project_payload: ProjectCreate,
    premise: str,
    use_conception: bool = False,       # 新增
    conception_tier: str = "standard",  # 新增；run_conception_pipeline 目前无 tier 参数，
                                        # 需按 P2-1 建议 2 一并实现后再透传
    ...,
):
    if use_conception:
        conception = await run_conception_pipeline(
            session, settings,
            genre_key=(project_payload.metadata or {}).get("genre_canonical"),
            chapter_count=project_payload.target_chapters,
            user_hints=premise or None,
            genre=project_payload.genre,
            sub_genre=project_payload.sub_genre,
            progress=progress,
        )
        # 用 conception 结果回填 payload/premise（title/premise/writing_profile 等
        # 字段映射对齐 web/server.py:3380-3553 的现有回填逻辑）
        premise = conception.premise or premise
        project_payload = _merge_conception_into_payload(project_payload, conception)

    project = await create_project(session, settings, project_payload)
    # 继续现有的 planner 流程...
```

3. CLI `project autowrite` 增加 `--conception` flag：

```python
@project_app.command("autowrite")
def project_autowrite(
    ...,
    conception: bool = typer.Option(
        True,  # 默认启用
        "--conception/--no-conception",
        help="Run multi-agent conception pipeline before planning.",
    ),
    conception_tier: str = typer.Option(
        "standard",
        "--conception-tier",
        help="Conception depth: fast, standard, or full.",
    ),
):
```

4. API `POST /projects` 增加异步 conception 支持（返回 202 + job_id）。

**影响范围**：autowrite 流程入口。

**验证方式**：
- CLI `project autowrite --conception` 生成的 book_spec 质量对比 `--no-conception`
- 检查 conception 日志是否完整记录

---

### Task 2.2：Conception Round 1 并行化

**对应问题**：P2-1

**文件**：`src/bestseller/services/conception.py`（行 3421-3495）

**修改方案**：

Round 1 当前是串行 await（`market_proposal`@3429 → `character_proposal`@3450 → cast_reality_audit@3463 → `world_proposal`@3483，全文件无 `asyncio.gather`）。注意 **cast_reality_audit 依赖 character 提案**，必须保留在 character 之后串联；正确的并行结构是三条支线并行、支线内保序：

```python
# 旧（串行）：market → character → cast_audit → world

# 新（并行，支线内保序）
async def _character_lane():
    proposal = await _llm_call_json(...character_architect...)
    audited = await _audit_cast_reality(proposal, ...)  # 依赖 character 提案，保持串联
    return audited

market_proposal, character_proposal, world_proposal = await asyncio.gather(
    _llm_call_json(...market_strategist...),
    _character_lane(),
    _llm_call_json(...world_builder...),
    return_exceptions=True,
)

# 处理异常（与现有 fallback 语义对齐：_llm_call_json 自带 fallback，
# gather 层只需兜住 lane 内未被 fallback 吸收的异常）
for name, result in zip(("market", "character", "world"),
                        (market_proposal, character_proposal, world_proposal)):
    if isinstance(result, Exception):
        logger.warning("Conception Round 1 %s failed: %s", name, result)
        # fail-open：使用对应 fallback 载荷继续
```

**影响范围**：Conception 执行时间预计减少 30-40%（瓶颈从三段串行变为最长单支线 character→audit）。

**验证方式**：
- 对比修改前后 conception 总耗时
- 检查并行执行的结果质量是否与串行一致

---

### Task 2.3：增加 Conception 降级追踪

**对应问题**：P2-1、X-2

**文件**：`src/bestseller/services/conception.py`

**修改方案**：

> 复核注：核心讨论轮（Round 0-3）**没有** try/except——失败走 `_llm_call_json` 的 `fallback=` 兜底载荷（conception.py:554-591）；只有概念淘汰赛/机制回声门/世界模型推导等辅助门用 try/except fail-open。因此"在每轮 try/except 中 append"落不了地，降级采集点要放在 fallback 命中处。

1. 在 `ConceptionResult`（frozen dataclass，conception.py:73-99）中增加字段：

```python
@dataclass(frozen=True)
class ConceptionResult:
    ...
    degraded_rounds: tuple[str, ...] = ()
    # 记录降级的轮次/门，如 ("market_strategist:fallback", "concept_tournament:error")
```

2. 采集点两处：
   - `_llm_call_json` 增加可选的降级上报（如 `on_fallback: Callable[[str], None]` 回调，或返回值带"是否用了 fallback"标记），pipeline 侧在每次调用处传入轮次名，命中 fallback 即记录
   - 各辅助门（tournament / echo gate / world model 等）的 `except` 分支中记录 `f"{gate_name}:error"`

   pipeline 内用局部 `degraded: list[str]` 累积，最后构造 `ConceptionResult(degraded_rounds=tuple(degraded), ...)`（frozen dataclass 只能在构造时传入，不能事后 append）。

3. 在 `run_autowrite_pipeline` 中，检查 `degraded_rounds` 并在 progress 回调中报告。

**影响范围**：Conception 结果结构。

**验证方式**：
- 模拟某轮 LLM 失败，检查 `degraded_rounds` 是否正确记录
- 在 Web Studio 中查看 conception 日志是否显示降级信息

---

## Phase 3 — 导出质量提升（3-4天）

> 目标：修复格式转换的质量问题。
> 独立于其他 Phase，可并行执行。

### Task 3.1：DOCX 增加格式支持

**对应问题**：P6-3

**文件**：`src/bestseller/services/exports.py`（行 516-667）

**修改方案**：

短期（不引入新依赖）：

1. 扩展 `_parse_markdown_line` 支持 `### h3`：
```python
def _parse_markdown_line(line: str) -> tuple[str, str]:
    if line.startswith("### "):
        return "h3", line[4:]
    if line.startswith("## "):
        return "h2", line[3:]
    # ... 现有逻辑
```

2. 在 `build_docx_bytes` 中增加 `**bold**` 和 `*italic*` 的内联格式处理。注意不能只对标记做 `re.sub` 替换——标记**外**的普通文本也必须包进 `<w:r><w:t>`，否则产出非法 OOXML；分段时先按含 `**` 的整段切分再判别，并保留现有 `escape()`（原方案的 re.sub 会把替换出的 XML 再喂给 escape 或裸文本混排，两头都错）：

```python
_INLINE_MARK = re.compile(r'(\*\*.+?\*\*|\*.+?\*)')

def _render_inline_runs(text: str) -> str:
    """把一行文本切成段，逐段包成 OOXML run（普通文本也要包 run）。"""
    runs: list[str] = []
    for seg in _INLINE_MARK.split(text):
        if not seg:
            continue
        if seg.startswith("**") and seg.endswith("**") and len(seg) > 4:
            runs.append(f"<w:r><w:rPr><w:b/></w:rPr><w:t>{escape(seg[2:-2])}</w:t></w:r>")
        elif seg.startswith("*") and seg.endswith("*") and len(seg) > 2:
            runs.append(f"<w:r><w:rPr><w:i/></w:rPr><w:t>{escape(seg[1:-1])}</w:t></w:r>")
        else:
            runs.append(f"<w:r><w:t>{escape(seg)}</w:t></w:r>")
    return "".join(runs)
```

3. 在章节间增加分页符：
```python
# 章节之间插入分页
if i > 0:
    body_parts.append('<w:p><w:r><w:br w:type="page"/></w:r></w:p>')
```

中期：评估迁移到 `python-docx` 库的成本和收益。

**影响范围**：DOCX 导出。

**验证方式**：
- 导出包含粗体/斜体的章节，检查 DOCX 中格式是否正确
- 检查章节间是否有分页

---

### Task 3.2：EPUB 按章节拆分

**对应问题**：P6-4

**文件**：`src/bestseller/services/exports.py`（行 670-725）

**修改方案**：

1. 修改 `build_epub_bytes` 接受章节列表而非单个 content 字符串：

```python
def build_epub_bytes(
    title: str,
    chapters: list[tuple[int, str, str]],  # [(chapter_num, chapter_title, content_md)]
    identifier: str | None = None,
    language: str = "zh-CN",
) -> bytes:
```

2. 每章生成独立的 XHTML 文件：
```python
for ch_num, ch_title, ch_content in chapters:
    html = markdown_to_html(ch_content)
    filename = f"OEBPS/chapter-{ch_num:04d}.xhtml"
    # 写入 zip
    zf.writestr(filename, _wrap_xhtml(ch_title, html))
    # 加入 spine
    spine_items.append(filename)
    # 加入 nav
    nav_entries.append((filename, ch_title))
```

3. 默认 identifier 改为唯一值：
```python
if identifier is None:
    identifier = f"bestseller-{uuid4()}"
```

**影响范围**：EPUB 导出。

**验证方式**：
- 导出的 EPUB 在阅读器中能按章节导航
- 检查 EPUB spine 包含多个 itemref

---

### Task 3.3：PDF 渲染 Markdown

**对应问题**：P6-5

**文件**：`src/bestseller/services/exports.py`（行 728-799）

**修改方案**：

1. 使用 `markdown` 库将 Markdown 转 HTML，再用 reportlab 的 `Paragraph` 解析 HTML：

```python
import markdown as md

def build_pdf_bytes(title: str, content_md: str, language: str = "zh-CN") -> bytes:
    # ...
    html_body = md.markdown(content_md, extensions=['extra', 'sane_lists', 'nl2br'])
    
    for line in html_body.split('\n'):
        if line.startswith('<h1>'):
            story.append(Paragraph(line, h1_style))
        elif line.startswith('<h2>'):
            story.append(Paragraph(line, h2_style))
        elif line.startswith('<p>'):
            # reportlab Paragraph 支持 HTML 标签
            story.append(Paragraph(line, body_style))
        # ...
```

2. 英文项目使用英文字体。注意：Helvetica 是 reportlab 内置 Type1 字体，**无需注册**（`TTFont('Helvetica', 'Helvetica')` 会因找不到 .ttf 文件直接报错）；STSong-Light 是 CID 字体，走 `UnicodeCIDFont` 注册——现有代码 exports.py:741 已经是这么做的，保持不变即可：

```python
if is_en:
    base_font = 'Helvetica'  # 内置字体，直接用
else:
    pdfmetrics.registerFont(UnicodeCIDFont('STSong-Light'))  # 现状（行 741）
    base_font = 'STSong-Light'
```

3. 章节间增加分页：
```python
from reportlab.platypus import PageBreak
# 每章后
story.append(PageBreak())
```

**影响范围**：PDF 导出。

**验证方式**：
- 导出包含粗体的章节，检查 PDF 中格式是否正确
- 检查章节间是否有分页

---

### Task 3.4：质量门禁异常可见化

**对应问题**：P6-6

**文件**：`src/bestseller/services/exports.py`

**修改方案**：

1. 将所有 `except Exception: pass` 改为 `except Exception: logger.warning(...)`：

```python
# 行 1110
try:
    length_ok = _check_length_stability(...)
except Exception as e:
    logger.warning("Length stability check failed (non-fatal): %s", e)
    gate_health["length_stability"] = "error"
```

2. 在 `collect_publication_blockers` 返回值中增加 `gate_health` 字段：

```python
def collect_publication_blockers(...) -> tuple[list[str], dict[str, str]]:
    blockers: list[str] = []
    gate_health: dict[str, str] = {}
    # ...
    return blockers, gate_health
```

3. 调用方可以根据 `gate_health` 决定是否继续导出。

**影响范围**：导出门禁逻辑。

**验证方式**：
- 模拟配置错误，检查 warning 日志是否输出
- 检查 `gate_health` 是否正确记录异常

---

## Phase 4 — 发布可靠性（2-3天）

> 目标：提升发布流程的可靠性和可观测性。
> 独立于其他 Phase。

### Task 4.1：Cookie 有效性预检

**对应问题**：P7-1

**文件**：
- 修改：`src/bestseller/services/publishing/base.py`
- 修改：`src/bestseller/scheduler/jobs.py`

**修改方案**：

1. 在 `PlatformAdapter` 协议中增加 `check_auth` 方法：

```python
class PlatformAdapter(Protocol):
    ...
    async def check_auth(self) -> bool:
        """Check if current credentials are valid."""
        ...
```

2. 在三个适配器中实现 `check_auth`（发送轻量 API 请求验证 Cookie）。

3. 在 `publish_next_chapter`（`jobs.py`）中，发布前先检查：

```python
if not await adapter.check_auth():
    # 标记 schedule 为 paused（status 的 CHECK 约束已含 'paused'，可直接用）
    schedule.status = "paused"
    # 注意：PublishingScheduleModel（infra/db/models.py:1725-1764）没有 last_error 列，
    # 失败原因写入 metadata_json（如 metadata_json["last_error"]），或加 Alembic 迁移新增该列
    logger.error("Publishing paused: cookie expired for platform %s", platform.name)
    break
```

**影响范围**：发布调度逻辑（若选择新增 `last_error` 列则含一次 DB 迁移）。

**验证方式**：
- 使用过期 Cookie 测试，检查是否被预检拦截
- 检查 schedule 状态是否正确更新

---

### Task 4.2：发布失败重试与熔断

**对应问题**：P7-2

**文件**：`src/bestseller/scheduler/jobs.py`

**修改方案**：

> 复核注：发布 job 是 APScheduler 的 cron 短任务（`AsyncIOScheduler`，scheduler/main.py:16、70-78），每次 tick 单发一次，**不是长驻循环**——原方案在 job 内 `asyncio.sleep(3600)` 做小时级退避会把单次短任务挂住一小时，不成立。长间隔重试应交给下一次 cron tick；job 内只做秒级快重试。另外 `PublishingScheduleModel` 没有 `consecutive_failures` 列，需 Alembic 迁移新增（或记入 `metadata_json`）。

1. job 内只做短退避快重试（吸收瞬时网络抖动），跨 tick 熔断靠计数：

```python
MAX_INLINE_RETRIES = 3
INLINE_RETRY_DELAYS = [5, 15, 30]  # 秒级，只兜网络抖动
CIRCUIT_BREAK_AFTER = 5            # 连续失败 tick 数

result = None
for attempt in range(MAX_INLINE_RETRIES):
    try:
        result = await adapter.publish_chapter(content=draft.content_md, meta=meta)
        if result.success:
            break
    except Exception as e:
        logger.warning("Publish attempt %d failed: %s", attempt + 1, e)
        if attempt < MAX_INLINE_RETRIES - 1:
            await asyncio.sleep(INLINE_RETRY_DELAYS[attempt])

if result is None or not result.success:
    # consecutive_failures 需迁移新增列（或存 metadata_json）
    schedule.consecutive_failures = (schedule.consecutive_failures or 0) + 1
    if schedule.consecutive_failures >= CIRCUIT_BREAK_AFTER:
        schedule.status = "paused"   # CHECK 约束已含 'paused'
        logger.error("Publishing circuit breaker triggered for %s", platform.name)
    # 不推进 current_chapter；分钟/小时级重试由下一次 cron tick 天然承担
else:
    schedule.consecutive_failures = 0
```

2. 区分错误类型：
   - 网络超时 → job 内短退避重试 + 跨 tick 重试
   - Cookie 过期 → 不重试，直接暂停 schedule（对接 Task 4.1 的 check_auth）
   - 内容被拒 → 不重试，标记章节需要人工审查

**影响范围**：发布调度逻辑 + 一次 DB 迁移（`consecutive_failures` 列，或复用 `metadata_json`）。

**验证方式**：
- 模拟网络错误，检查是否自动重试
- 模拟连续失败，检查熔断是否触发

---

### Task 4.3：审核状态轮询

**对应问题**：P7-3

**文件**：`src/bestseller/scheduler/jobs.py`

**修改方案**：

> 复核注：`PublishingHistoryModel.status` 有 CHECK 约束，只允许 `'pending','success','failed','retrying'`（infra/db/models.py:1767-1807）——直接赋 `"published"/"approved"/"rejected"` 会违反约束报错；且该表没有 `rejection_reason` 列（有 `error_message`、`platform_response_json`）。需要 Alembic 迁移扩展 status 枚举（如加 `'approved','rejected'`），拒绝原因写 `error_message` 或 `platform_response_json`。

1. 发布成功后（现状写 `status="success"`，保持不变），安排延迟轮询任务（APScheduler `date` trigger 一次性 job，同 book generation 的用法）：

```python
# 发布成功后
history.status = "success"
history.platform_chapter_id = result.platform_chapter_id

# 安排 10 分钟后检查审核状态（date-trigger 一次性 job）
scheduler.add_job(
    check_publish_review_status, trigger="date",
    run_date=now + timedelta(minutes=10), args=[history.id],
)
```

2. 新增调度任务 `check_publish_review_status`：

```python
async def check_publish_review_status(session, history_id):
    history = await session.get(PublishingHistoryModel, history_id)
    adapter = await get_adapter(session, history.platform_id)

    status = await adapter.check_publish_status(history.platform_chapter_id)

    if status == "rejected":
        history.status = "rejected"        # 需先迁移扩展 status CHECK 约束
        history.error_message = ...        # 拒绝原因复用 error_message 列
        logger.warning("Chapter rejected by platform: %s", history.error_message)
        # 通知用户
    elif status == "approved":
        history.status = "approved"        # 同上，需迁移
```

**影响范围**：发布调度逻辑 + 一次 DB 迁移（扩展 `PublishingHistoryModel.status` 枚举），需要新增一个调度任务。

**验证方式**：
- 模拟审核通过/拒绝，检查状态是否正确更新

---

## Phase 5 — Planner 对称性与降级治理（3-5天）

> 目标：修复中英文不对称、提升降级可观测性。
> 依赖 Phase 1 完成（提示词去重）。

### Task 5.1：修复中英文提示词不对称

**对应问题**：P3-2

**文件**：`src/bestseller/services/planner.py`

**修改方案**：

1. book_spec 英文版增加 `unique_hook` 和 `benchmark_works` 输出要求（行 14202-14206）：

```python
# 英文版输出约束增加
"unique_hook": "A one-sentence anti-cliché selling point that differentiates this book from others in the same genre",
"benchmark_works": "3-5 comparable published works with reasons",
```

2. `render_outline_hook_taxonomy_block` 和 `render_golden_opening_rules_block` 增加英文版本（调用点：行 15272/15790 与 15274/15793，均在 `if not is_en:` 内）：

```python
if not is_en:
    _method_block = render_outline_hook_taxonomy_block(_method_stage)
else:
    _method_block = render_outline_hook_taxonomy_block_en(_method_stage)
```

3. volume_outline 英文版补充事实一致性自检指令（行 16030-16034 对应位置）：

```python
"Scene card fact consistency: After generating, self-check each card for "
"internal contradictions (names, locations, object states, timeline). "
"Fix any conflicts before outputting."
```

**影响范围**：英文项目的规划提示词。

**验证方式**：
- 英文项目生成 book_spec，检查是否包含 `unique_hook` 和 `benchmark_works`
- 英文项目生成 outline，检查是否包含 hook taxonomy 和 golden opening rules

---

### Task 5.2：反同质化约束降级可见化

**对应问题**：P3-3、X-2

**文件**：`src/bestseller/services/planner.py`

**修改方案**：

1. 将所有 `try/except Exception: logger.debug(...)` 提升为 `logger.warning(...)`：

```python
# 旧
try:
    narrative_lines_constraints_block = render_narrative_lines_constraints(...)
except Exception:
    logger.debug("narrative_lines injection failed (non-fatal)")
    narrative_lines_constraints_block = ""

# 新
try:
    narrative_lines_constraints_block = render_narrative_lines_constraints(...)
except Exception as e:
    logger.warning("Anti-convergence constraint 'narrative_lines' injection failed: %s", e)
    narrative_lines_constraints_block = "[反同质化约束因异常未注入 — 下游 repair gate 请注意]"
```

2. 在 prompt 中留占位标记，让下游 repair gate 能检测到约束缺失。

**影响范围**：Planner 所有反同质化约束块。

**验证方式**：
- 模拟 YAML 配置缺失，检查 warning 日志是否输出
- 检查 prompt 中是否包含占位标记

---

### Task 5.3：补齐 summarize 函数丢弃的关键字段

**对应问题**：P3-1

**文件**：`src/bestseller/services/planning_context.py`（summarize_* 函数）

**修改方案**：

1. 复核已证实 `summarize_book_spec`（planning_context.py:60-149）**丢弃**了以下字段（函数体完全未引用），直接补齐保留策略，无需再审计：
   - `narrative_lines.core_axis.phrasing_tokens`
   - `protagonist.psych_profile`
   - `power_system.tiers`

   `naming_pool` 与 `summarize_world_spec`/`summarize_cast_spec` 的保留情况仍需按同法核对后处理。

2. 增加字段保留测试：

```python
def test_summarize_book_spec_preserves_narrative_lines():
    book_spec = {
        "narrative_lines": {
            "core_axis": {
                "statement": "...",
                "phrasing_tokens": ["token1", "token2"]
            }
        }
    }
    summary = summarize_book_spec(book_spec)
    assert "phrasing_tokens" in summary or "core_axis" in summary
```

**影响范围**：规划阶段间数据传递。

**验证方式**：
- 运行测试套件，检查所有 summarize 字段保留测试通过

---

## Phase 6 — 架构治理（长期）

> 目标：改善代码可维护性和配置可靠性。
> 无硬依赖，可逐步推进。

### Task 6.1：genre_taxonomy 双轨收敛

**对应问题**：P1-4

**文件**：`src/bestseller/services/writing_presets.py`、`src/bestseller/services/conception.py`

**修改方案**：

将 `_GENRE_PRESETS`（行 952）改为从 `genre_taxonomy.yaml` 动态生成。`_build_genre_context`（conception.py 行 602-624）不再先查 `_GENRE_PRESETS`。

---

### Task 6.2：配置启动校验

**对应问题**：X-3

**修改方案**：

新建 `src/bestseller/services/config_validator.py`，在应用启动时校验：
- genre_taxonomy.yaml 中每个 sub_genre 的 category/pack 都能找到对应文件
- prompt_packs/*.yaml 的结构完整性
- novel_categories/*.yaml 的 challenge_evolution_pathway 完整性
- default.yaml 的字数参数在合理范围内

---

### Task 6.3：核心文件拆分

**对应问题**：X-4

**修改方案**：

长期目标：
- `conception.py`（4400+ 行）→ 拆分为 `conception/pipeline.py`、`conception/prompts.py`、`conception/guardrails.py`
- `planner.py`（23000+ 行）→ 拆分为 `planner/book_spec.py`、`planner/world_spec.py`、`planner/cast_spec.py`、`planner/volume_plan.py`、`planner/outline.py`
- `drafts.py`（12000+ 行）→ 拆分为 `drafts/scene.py`、`drafts/chapter_first.py`、`drafts/assembly.py`、`drafts/sanitize.py`

每次拆分需要：
1. 保持所有 import 路径兼容（通过 `__init__.py` re-export）
2. 运行完整测试套件验证
3. 一次只拆一个文件

---

### Task 6.4：统一降级追踪器

**对应问题**：X-2

**修改方案**：

新建 `src/bestseller/services/degradation_tracker.py`：

```python
class DegradationTracker:
    """统一的降级事件追踪器。"""
    
    def __init__(self):
        self._events: list[dict] = []
    
    def record(self, stage: str, component: str, reason: str, severity: str = "warning"):
        self._events.append({
            "stage": stage,
            "component": component,
            "reason": reason,
            "severity": severity,
        })
    
    def to_dict(self) -> list[dict]:
        return self._events.copy()
```

在 Conception、Planner、章节生成、导出等关键流程中注入 `DegradationTracker`，在静默降级时记录事件，最终附加到产物 metadata 中。

---

## 测试策略

### 每个 Task 的测试要求

| Phase | 测试类型 | 要求 |
|-------|---------|------|
| Phase 0 | 单元测试 | 每个 Task 增加对应单元测试 |
| Phase 1 | A/B 对比 | 同一章节卡，修改前后各生成 3 次，对比质量 |
| Phase 2 | 集成测试 | CLI/API 端到端创建项目测试 |
| Phase 3 | 回归测试 | 确保已有导出功能不被破坏 |
| Phase 4 | 模拟测试 | 模拟 Cookie 过期、网络错误等场景 |
| Phase 5 | 单元测试 | 中英文提示词对称性测试 |

### 全局回归测试

每个 Phase 完成后，运行完整测试套件：

```bash
cd /Volumes/MACSSD/owen-home/Documents/workspace/bestseller
python -m pytest tests/ -x --timeout=300
```

### 质量基线对比

在开始修改前，先用现有代码生成 5 章作为"质量基线"。每个 Phase 完成后，用相同输入生成 5 章，对比：
- LLM 质量评分（`judge_chapter_commercial_quality`）
- AI 套话出现频率
- 字数分布
- 章节接缝连续性

---

## 回滚策略

### 每个修改的回滚方式

| 修改类型 | 回滚方式 |
|---------|---------|
| 新增代码（如 ai_slop_blacklist.py） | 删除新文件，恢复引用 |
| 修改现有代码 | git revert 对应 commit |
| 配置变更 | 恢复 YAML 文件 |

### 分级回滚

- **Phase 0**：可独立回滚，不影响其他模块
- **Phase 1**：需整体回滚（提示词结构变更较大）
- **Phase 2-5**：可按 Task 粒度回滚
- **Phase 6**：文件拆分可通过 `__init__.py` re-export 保证兼容性

### 紧急回滚

如果修改导致章节质量明显下降（LLM 评分中位数下降 >0.05）：

1. 立即 `git revert` 对应 Phase 的所有 commit
2. 重新运行质量基线对比
3. 分析退化原因后重新修改

---

## 建议执行顺序

```
Week 1:  Phase 0 (数据完整性) + Phase 4 (发布可靠性)  [并行]
Week 2:  Phase 1 (提示词去重)                          [核心]
Week 3:  Phase 2 (路径收敛) + Phase 3 (导出质量)       [并行]
Week 4:  Phase 5 (Planner 治理) + 质量基线对比
Week 5+: Phase 6 (架构治理)                            [渐进]
```

**最高优先级**：Phase 0 的 Task 0.1-0.4 + Phase 1 的 Task 1.2，这 5 个 Task 覆盖了 7 个 P0 级问题中的 4 个（P1-2、P4-2、P6-1、P6-2），另覆盖 P1 级的 P1-3。剩余 P0（P1-1 → Task 2.1、X-1 → Task 1.1-1.3 组合、P7-1 → Task 4.1）依赖各自 Phase。

> 复核注：原文写"33 个问题/8 个 P0/覆盖 6 个"，与分析文档实际不符——分析共 41 个问题；P4-1 复核后从 P0 降为 P1（重复仅存在于场景级路径），P0 现为 7 个。

---

*文档结束*

---

## 2026-07-11 增补：一句话钩子与长篇承载改造落地

本轮不是继续给原提示词叠规则，而是新增并贯通一个可失败的创意合同：

1. 一句话阶段只生成 `CoreStorySeed`，不在同一调用里写卷纲和 500 章说明；
2. 钩子以新鲜度、想点欲、可预测性、人物决策、机制因果、题材保真、大白话七轴硬门筛选；
3. 仅前两名扩展 `SerialityProof`，验证故事单元、进度条、对手生态、累积轨道、问题梯、阶段转换和终局；
4. 200 章以上项目容量不足直接终止 Conception，不进入市场、人物、世界和规划 Agent；
5. 合同写入 BookSpec，并由卷级门禁、章纲门禁和写前上下文持续核对，防止后续阶段换书；
6. 生成模型与裁判模型支持独立 catalog 路由，验证产物记录实际模型键。

最新相关回归为 467 项通过。真实模型可用性仍是外部阻塞：目前只有 MiniMax M2.7/M3
可稳定调用，其他已配置模型的密钥存在但实际请求失败，因此尚不能宣称跨家族独立裁判
已在生产环境生效。Docker 重建和新书“只到概念门禁”的 E2E 作为本增补的最终验收。

### 2026-07-11 最终验收补充

- `concept_seed` 已成为可隔离旧创意包的直接输入通道；
- 500 章最少 167 个 2-4 章微单元，阶段必须连续覆盖 1-500 章；
- HookCard、SerialityProof 与裁判分数以 `quality_evidence` 写入同一 ConceptContract，
  下游 logline gate 只复用通过硬门的真实证据，不再重复调用失败后用 0 分推翻冠军；
- 一句话生成采用 6 路探索 + 最佳近失 2 路定向修复，最多三轮；失败反馈包含原句、失败轴
  与长篇裁判原因，不再无记忆重抽；
- 最终 Docker 真机任务 `c1085b7f-75f0-4b36-af2a-248059ecc48a` 被正确阻断：项目未创建、
  规划未启动。最佳候选因 escalation=7、unit_density=7 未过 7.5 硬线，没有降门放行。

当前剩余质量瓶颈不是“门禁缺失”，而是生成与裁判仍由 MiniMax M3 同一家族承担，且这条
测试种子本身的核心循环偏弱。恢复可用的跨家族生成/裁判模型后，应继续做同题盲测；在此
之前不得把一次随机通过当作框架已达到行业顶尖水平。
