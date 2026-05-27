# Obsidian Integration Design

## Why Obsidian Fits This Framework

Obsidian is useful here because it treats local Markdown files as the knowledge
base, then adds links, backlinks, graph views, properties, Bases, search, and
Canvas on top. That matches BestSeller's existing principle: PostgreSQL is the
source of truth, while Markdown under `output/` is a rebuildable derivative.

The integration should therefore not create a second writing database. It should
create a readable vault from current framework state:

- `PlanningArtifactVersion` -> planning snapshots and raw JSON traceability.
- Story bible overview -> world rules, characters, relationships, volume
  frontiers, deferred reveals, and expansion gates.
- `CanonFact` -> current facts and fact history.
- `TimelineEvent` -> story-order timeline.
- `ProjectMaterial` -> project-specific forged material.
- current `ChapterDraftVersion` -> readable draft snapshots.
- `MaterialLibraryModel` -> global material dimension coverage.
- `data/distillation/source-*` -> distillation package health and row counts.
- `data/distillation/aggregates/*` -> genre aggregate maturity, material,
  mechanism, and anti-copy availability.
- `config/prompt_packs/*.yaml` -> prompt pack coverage and source-note gaps.
- `data/methodology_sources/*` -> methodology card coverage and validation
  findings.
- human Obsidian notes -> `Inbox/`, not consumed by generation until explicitly
  imported through a future command.

## Integration Point

The first integration point is an export service:

```bash
bestseller export obsidian my-story
bestseller export obsidian my-story --no-system-assets
```

Default output:

```text
output/<project-slug>/obsidian-vault/
```

This sits beside the existing Markdown/DOCX/EPUB/PDF export path. It is
intentionally not wired into `pipelines.py`: vault generation is a review and
maintenance action, not a dependency of drafting.

## Vault Shape

```text
00-主页.md
故事圣经/总览.md
人物/人物索引.md
人物/<角色>.md
关系/关系索引.md
世界观/规则.md
世界观/地点.md
世界观/势力.md
卷纲/卷计划.md
伏笔与揭示/揭示计划.md
Canon/当前事实.md
Canon/事实履历.md
时间线/时间线.md
素材/项目素材.md
规划产物/规划产物索引.md
资料资产/总览.md
资料资产/缺口看板.md
物料库/全局物料维度.md
蒸馏资料/蒸馏包索引.md
蒸馏资料/聚合资产索引.md
提示词/Prompt Pack 索引.md
方法论/方法论卡片索引.md
模型调用索引.md
正文/001-<章节>.md
维护/维护看板.md
Inbox/README.md
raw/
_manifest.json
```

All generated notes include frontmatter so Obsidian Bases or community table
views can filter by `type`, `project`, `character`, `chapter_number`, and
similar properties. Generated notes use wikilinks so backlinks and Graph View can
show relationships between people, world rules, canon facts, chapters, and
maintenance surfaces.

## Asset Workbench Boundary

The asset workbench is enabled by default because it answers the operational
questions authors and agents need during long-form production:

- what project material dimensions are underfilled;
- which global material dimensions exist as seeds;
- which distillation packages are broken or incomplete;
- which aggregate assets are mature enough to use;
- which prompt packs lack fragments, source notes, or metadata;
- which methodology card decks have source or gate-binding gaps;
- which stable assets can be passed into model calls.

Human users inspect these through Markdown notes and Obsidian links. Model
callers should read the raw JSON surfaces:

- `raw/model-call-index.json` for stable asset IDs, types, status, source paths,
  and intended use.
- `raw/material-coverage.json` for project material density and global seeds.
- `raw/asset-workbench.json` for distillation, prompt, and methodology summaries.

This split is intentional: Obsidian remains ergonomic for maintenance, while
LLM prompts consume explicit JSON contracts instead of scraping human notes.

## Guardrails

- DB remains canonical.
- Generated files may be overwritten on the next export.
- User edits belong in `Inbox/`.
- Generated raw JSON is a model-facing derivative, not a new canonical store.
- No deletion of arbitrary vault files during export; this protects human notes.
- Future import must be explicit and typed, for example:
  `bestseller obsidian import-inbox --as canon-fact|planning-artifact|rewrite-task`.

## Follow-up Iterations

1. Generate `.base` views once the desired Obsidian Bases schema is stable.
2. Generate a `.canvas` file for character/relationship/world-rule exploration.
3. Add explicit Inbox import with validation against existing domain schemas.
4. Add Web UI button that calls the same export service.
