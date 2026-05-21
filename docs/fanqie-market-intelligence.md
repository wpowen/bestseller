# Fanqie Market Intelligence

This lane turns public Fanqie ranking metadata into safe, anonymous planning
constraints. It is designed to learn category mechanics, not to copy books.

## Safety Boundary

- Do not ingest paid chapters or full competitor text.
- Do not ask a model to write in a named author's style.
- Do not reuse source titles, character names, exclusive systems, cases, or
  scene templates.
- Craft profiles must stay anonymous: category rhythm, hook shape, pacing, and
  copy boundaries only.

## CLI Workflow

Analyze a local FanqieHub-style JSON file without database writes:

```bash
uv run bestseller fanqie-market analyze-file ./ranking.json --category 都市脑洞
```

Import the same file into market tables:

```bash
uv run bestseller fanqie-market import-file ./ranking.json --category 都市脑洞
```

Attach the compiled market profile to a project as planning artifacts and
project metadata:

```bash
uv run bestseller fanqie-market import-file ./ranking.json \
  --category 都市脑洞 \
  --project-slug my-book \
  --persist-artifacts
```

Fetch a live category snapshot through the FanqieHub adapter:

```bash
uv run bestseller fanqie-market fetch-category 都市脑洞 \
  --project-slug my-book \
  --persist-artifacts
```

## What Gets Stored

Project artifact types:

- `fanqie_market_snapshot`: raw snapshot plus normalized competitors.
- `fanqie_market_profile`: selected summary for project planning.
- `fanqie_category_profile`: category-level market patterns.
- `fanqie_craft_profile`: anonymous craft card for prompts.
- `fanqie_long_ranking_readiness`: deterministic long-form gate report.

Project metadata receives:

- `fanqie_market_summary`
- `fanqie_category_profile`
- `fanqie_craft_profile`

The draft prompt path reads `fanqie_craft_profile` and renders it as a
`番茄榜单匿名工艺卡`.

## Feature Flags

Fanqie market features are opt-in and default off:

```yaml
pipeline:
  enable_fanqie_market_profile: false
  enable_fanqie_long_ranking_gate: false
  fanqie_long_ranking_block_on_failure: false
```

## Seed Profiles

When no fresh ranking snapshot exists, fallback category profiles live under:

```text
config/market_profiles/fanqie/
```

Current seeds cover:

- `urban-brain`
- `urban-high-martial`
- `xuanhuan-brain`
- `suspense-brain`
- `modern-romance-brain`

## Long-Form Readiness Gate

`evaluate_fanqie_long_ranking_gate` checks:

- first 50/100/300/1000/3000 Chinese-character windows
- first-three-chapter pressure, advantage, and payoff loop
- per-chapter payoff, future hook, exposition-only streaks, and ability cost

Use `evaluate_and_persist_fanqie_long_readiness` to store the report as a
planning artifact.
