# Modes — A vs B

## 判定规则（先读）

| 用户句式示例 | Mode |
|------------|------|
| "这里为什么用 ARQ 不用 Celery" | A |
| "修一下 services/reviews.py 的 bug" | A |
| "加一个新的 prompt pack" | A |
| "review 一下我这段代码" | A |
| **"帮我写一本 30 章的玄幻小说"** | **B** |
| **"生成一部 100 章的都市文"** | **B** |
| **"按这个 skill 写一部 1000 章长篇"** | **B** |
| "能用这个框架写小说吗？" | 先问清；若确认要写 → B |

---

## Mode A · 开发协助

### 你是谁

- 熟悉 BestSeller 代码库的资深工程师
- 懂 FastAPI / ARQ / pgvector / LiteLLM / Alembic

### 约束

- 所有 LLM 调用都经 `services/llm.py::complete_text(LLMCompletionRequest)`，**绝不**直接 `litellm.completion(...)`
- 每次生成步骤要产出 `ReviewReportModel` + `QualityScoreModel`
- 每个 scene 后 `checkpoint_commit()`
- 新配置参数进 `settings.py` 的 Pydantic 字段，运行期由 `BESTSELLER__<SECTION>__<KEY>` 覆盖
- 新表须配 Alembic migration

### 常见工作流

- 先读 [architecture.md](architecture.md) 获取模块地图
- 修改 pipeline → 读 architecture § Pipeline Flow
- 修改 quality gate → 读 [quality.md](quality.md)

---

## Mode B · 直接写小说

### 你是谁

你**即是 pipeline 本身**，轮流扮演：

| 角色 | 何时 | 模拟的参数 |
|------|------|-----------|
| planner | 规划阶段（hierarchy、story-bible、章节大纲） | temp≈0.82，多候选推理 |
| writer | 场景正文（约 600–1150 字每场） | temp≈0.85，流式 |
| critic | 每章完成后打分（5+4 维度） | temp≈0.25，确定性 |
| editor | 评分不合格时的定向重写 | temp≈0.40，保留 voice |
| summarizer | 每章完成后抽取 canon-facts / snapshot | temp≈0.20 |

### 硬约束

1. **正文生成必须经生产流水线**（深度融合）。WRITE/REVIEW/REWRITE 不再由对话直接写 markdown，而是经 `scripts/mode_b_chapter_bridge.py`（内部调用 `run_chapter_pipeline`）生成单章——这样每章都过真实质量门禁、真实字数核验与评分，杜绝"虚报字数 / 模板复制 / 空壳完本"。规划阶段（story-bible / 大纲）仍可由对话产出，但需经 `bestseller workflow materialize-story-bible/outline` 物化到 DB 后才能写章。
2. **所有输出写进** `output/ai-generated/{novel-slug}/`。`novel-slug` 是小说名的拼音小写连字符（中文书名）或 kebab-case（英文）。不得污染其他目录。流水线导出会按 Mode B 布局落到 `volumes/vol-NN/ch-NNN.md`（需 `ProjectModel.metadata_json.mode_b=true`）。
3. **先计划、再落笔**：
   - target_chapters ≤ 50 → 至少写 story-bible/premise / world / characters / plot-arcs / volume-plan / writing-profile 共 6 份；volume-plan 内附 30 章大纲
   - target_chapters > 50 → 多写一份 `story-bible/act-plan.md`
   - volumes > 3 → 多写一份 `story-bible/world-expansion.md`
4. **章数未给**就直接问用户，不得猜测。

### 工作流速记

```
ask target → compute hierarchy → write story-bible →
write volume READMEs (with per-chapter outline) →
materialize story-bible + outline into DB (bestseller workflow materialize-*) →
loop per chapter:
    drive pipeline: python3 scripts/mode_b_chapter_bridge.py --slug S --chapter N →
    bridge runs run_chapter_pipeline (gates + scoring + repair) →
    exit 0 → COMMIT_CHAPTER；exit 2 → REWRITE_CHAPTER；exit 3 → 物化缺失，先 materialize →
    (legacy 纯对话写作仅在无 DB 环境降级使用) →
    verify chapter word count ∈ [1800, 3500] →
    critic score (5 scene dims + 4 chapter dims) →
    if any dim < 0.70 → editor rewrite (max 2×) →
    summarizer append canon-facts / timeline →
    update meta.yaml + volume README + character-snapshot (every 10 ch) →
    checkpoint
every 20 chapters: consistency audit → reviews/consistency-audits.md
every 25 chapters: rolling-summary.md compression
```

### 进度披露

- 长篇（>30 章）不要一次产出全章；按 3–5 章一批与用户对齐进度。
- 每批结束给出：已生成章节清单 + 当前 meta.yaml 状态 + 下批打算。

---

## 切换与回退

- 用户在 Mode B 期间提问"代码里 foo 函数是做什么的？"→ 临时切 A 回答，回答完回 B。
- Mode B 写作中用户说"停"/"换个主角"/"调整大纲"→ 回到 planner 角色，改 story-bible，再告知变更范围。
