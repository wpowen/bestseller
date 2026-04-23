# Material Library — External Import Schema

This document is the **contract** for feeding the multi-dimensional
material library from outside the main `bestseller` process.

Use it when you want to:

- produce seed material with a different LLM (ChatGPT, Gemini, a local
  Llama, a domain expert's curation session) and push the result into
  our library;
- batch-import material a human curator wrote by hand;
- wire an external research tool (scraping pipeline, wiki exporter,
  knowledge graph, etc.) into the library without touching our code.

The entry point is a JSONL file fed to:

```bash
python scripts/import_material_jsonl.py <path-to-file.jsonl>
```

The script is idempotent (upserts by `(dimension, slug)`), safe to
re-run, supports `--dry-run`, and never touches running novels — it
only populates the global shared library.

---

## TL;DR for prompting another LLM

> You will output a JSONL file, one JSON object per line, matching the
> schema below. Each line must be valid JSON on its own (no line
> continuation). Generate N entries that collectively give diverse,
> genre-distinct material for `<dimension>` in `<genre>`. Do NOT repeat
> names or concepts across entries. Each entry MUST include
> `source_citations` referencing real URLs or reputable books.

Give the target LLM:

1. the **dimension vocabulary** (below),
2. the **JSON schema per row** (below),
3. the **genre + sub_genre** you want filled,
4. a count (e.g. "12 entries per dimension"),
5. a "do not reuse these names" list if you are topping up an existing
   bucket (use `scripts/curate_library.py` to get current entries).

Then pipe the LLM's output into
`scripts/import_material_jsonl.py my_seed.jsonl --dry-run` to validate,
and again without `--dry-run` to commit.

---

## Row schema (per line)

Minimum required fields: `dimension`, `slug`, `name`, `narrative_summary`.

```jsonc
{
  // REQUIRED — which bucket this entry belongs to.
  // Must match exactly one of the allowed dimensions (see below).
  "dimension": "power_systems",

  // REQUIRED — internal reference key. Kebab-case, ASCII, no slashes,
  // no whitespace. Unique per dimension. Stable across re-imports.
  "slug": "xianxia-nine-realm-cultivation",

  // REQUIRED — human-readable title. Shown to operators and eventually
  // to the Drafter prompt.
  "name": "九境修炼体系",

  // REQUIRED — one-paragraph self-contained description. 80–400 Chinese
  // characters. Must be understandable without the content_json below.
  "narrative_summary": "一个以'炼气—筑基—金丹—元婴—化神—炼虚—合体—大乘—渡劫'为主线的九级修炼框架。每级突破伴随体质质变与天劫考验，...",

  // OPTIONAL — structured content. Free-form JSON; forges and planners
  // parse what they know about per dimension. Leave {} if you have no
  // structure to offer.
  "content_json": {
    "realms": [
      { "rank": 1, "name": "炼气", "signature": "吐纳灵气入体" },
      { "rank": 2, "name": "筑基", "signature": "灵根稳固" }
    ],
    "tribulation_pattern": "每三境一次天劫"
  },

  // OPTIONAL — genre tag. Use the Chinese label the UI uses
  // (e.g. "仙侠", "都市修仙", "科幻", "历史"). Leave null for cross-genre
  // commons that apply to any book.
  "genre": "仙侠",

  // OPTIONAL — sub-genre label. Leave null if the entry applies to the
  // whole genre.
  "sub_genre": "upgrade-core",

  // OPTIONAL — retrieval tags. Lowercase kebab-case strings.
  "tags": ["cultivation-ladder", "tribulation", "nine-realms"],

  // OPTIONAL — where this entry came from. Defaults to "web_import"
  // when omitted; you can force a value per-row or pass
  // --source-type on the CLI to override for every row.
  //
  // Allowed:
  //   research_agent | llm_synth | user_curated | web_import | mcp_pull
  "source_type": "llm_synth",

  // OPTIONAL but STRONGLY RECOMMENDED — citations so humans can
  // verify. Each item is either a bare URL string or an object.
  "source_citations": [
    { "title": "道教修炼体系 - 维基百科", "url": "https://zh.wikipedia.org/wiki/..." },
    "https://baike.baidu.com/item/..."
  ],

  // OPTIONAL — 0.0–1.0. How confident you are the entry is correct /
  // high-signal. Forges prefer higher confidence on ties. Default 0.5.
  "confidence": 0.8,

  // OPTIONAL — 0.0–1.0. How much this entry contributes to covering
  // its (dimension, genre) bucket. Leave unset to let Curator compute.
  "coverage_score": null,

  // OPTIONAL — "active" | "deprecated" | "review". Default "active".
  // Use "review" when you are uncertain and want a human to vet the
  // row before it joins retrieval.
  "status": "active",

  // OPTIONAL — precomputed 1024-d float vector. If omitted (the common
  // case) the importer computes a hashed embedding from name + summary
  // + tags. Only provide if you have a real bge-m3 (or compatible)
  // embedding available.
  "embedding": null
}
```

**Comments (`//`, `#`) are not real JSON.** The table above is
annotated for readability; the actual JSONL must be pure JSON. Blank
lines and `#`-prefixed lines in the JSONL file are accepted as
top-level file comments and ignored by the importer.

---

## Allowed `dimension` values

The 14 dimensions the current Curator knows about (see
`src/bestseller/services/library_curator.py::_MATERIAL_DIMENSIONS`):

| dimension | what goes here |
|---|---|
| `world_settings` | geography, civilisations, historical events, forbidden lands, eras |
| `power_systems` | levels / cultivation ladders / tech tiers |
| `factions` | sects, companies, alliances, star empires |
| `character_archetypes` | role templates (ruthless strategist, revenant, scholar-warrior) — not named people |
| `character_templates` | named characters usable as drop-ins (name, background, arc) |
| `plot_patterns` | main-line + sub-line patterns (revenge arc, ascension arc, hidden-parentage) |
| `scene_templates` | recurring scene types (face-slap, breakthrough, rescue, farewell) |
| `device_templates` | golden fingers, signature weapons, cursed items, tokens |
| `locale_templates` | settings / atmospheres (sect mountain, abandoned lab, space station) |
| `dialogue_styles` | voice / rhetorical register (wuxia-formal, office-sarcastic) |
| `emotion_arcs` | psychological curves (cold → thaw, hope → despair → rage) |
| `thematic_motifs` | symbols (moon, scar, blade, mirror) + usage patterns |
| `anti_cliche_patterns` | named tropes to **avoid** (and why) |
| `real_world_references` | historical events, scientific laws, religious texts |

Adding a new dimension requires a code change — do **not** invent one
in the JSONL.

---

## Allowed `source_type` values

| value | meaning |
|---|---|
| `research_agent` | produced by the internal Research Agent (reserved; do not use from outside) |
| `llm_synth` | synthesised by an LLM (ours or external). Use this when ChatGPT/Gemini/etc. wrote the row. |
| `user_curated` | human editor wrote or reviewed the row |
| `web_import` | imported from an external batch / scrape / export (default for this script) |
| `mcp_pull` | pulled from an MCP server (reserved; internal use) |

The importer will reject any other value.

---

## Minimal example

Put this in `data/seed_materials/example_min.jsonl` and run
`python scripts/import_material_jsonl.py data/seed_materials/example_min.jsonl --dry-run`:

```jsonl
{"dimension":"power_systems","slug":"xianxia-nine-realm","name":"九境修炼","narrative_summary":"炼气、筑基、金丹、元婴、化神、炼虚、合体、大乘、渡劫的九级修炼体系。","genre":"仙侠","tags":["cultivation","nine-realms"],"source_citations":["https://zh.wikipedia.org/wiki/%E4%BF%AE%E4%BB%99"]}
{"dimension":"factions","slug":"xianxia-liudao-shenzong","name":"六道神宗","narrative_summary":"一个以六道轮回为核心教义的古老宗门，掌握轮回秘术，总部建于忘川之上。","genre":"仙侠","tags":["sect","afterlife","reincarnation"]}
```

A valid file runs clean:

```
Material library import — data/seed_materials/example_min.jsonl
  mode: dry-run
  total rows:         2
  inserted/updated:   2
  rejected:           0
  skipped by novelty: 0
```

Drop the `--dry-run` when ready to commit.

---

## Hand-off prompt template (copy/paste to another LLM)

You can give another model this prompt, then pipe its output through
the importer:

````
You are generating material for a novel-writing system's shared
knowledge library. Output **only** a JSONL document: one JSON object
per line, no prose, no code fences.

Each line must match this schema:
  - dimension (required, string from this enum): {world_settings,
    power_systems, factions, character_archetypes, character_templates,
    plot_patterns, scene_templates, device_templates, locale_templates,
    dialogue_styles, emotion_arcs, thematic_motifs,
    anti_cliche_patterns, real_world_references}
  - slug (required, kebab-case ASCII, unique per dimension)
  - name (required, Chinese OK)
  - narrative_summary (required, 80–400 Chinese characters, self-contained)
  - content_json (optional, structured object)
  - genre (optional, e.g. "仙侠", "都市修仙", "科幻")
  - sub_genre (optional)
  - tags (optional, list of lowercase kebab-case strings)
  - source_type: "llm_synth"
  - source_citations (required, list of {title, url} objects citing
    real, reputable sources — Wikipedia, Baidu Baike, classical texts,
    academic papers)
  - confidence (0.0–1.0, self-estimate)

Task:
  Generate {N} entries for dimension={DIMENSION} under genre={GENRE},
  sub_genre={SUB_GENRE}. No two entries may share a `name`, `slug`, or
  dominant concept. Avoid common clichés listed below. Each entry must
  be culturally / internally consistent with the genre.

Existing slugs (DO NOT DUPLICATE):
  {EXISTING_SLUGS}

Clichés to avoid:
  {TABOOS}

Output only the JSONL.
````

---

## FAQ

**Q: Can I import rows for a dimension that doesn't exist yet?**
No — the allow-list is enforced. Add the dimension to
`_MATERIAL_DIMENSIONS` + the Curator seed plan first.

**Q: What if I'm re-importing the same file?**
Safe. Re-imports upsert by `(dimension, slug)`. `usage_count` and
`last_used_at` are preserved — only content + metadata are overwritten.

**Q: Will this affect novels currently being written?**
No. Importing only populates the global library. Old novels continue
on their existing pipeline. If you want an in-progress novel's next
chapter to see the library, set
`pipeline.enable_library_soft_reference: true` in `config/local.yaml`
(or export `BESTSELLER__PIPELINE__ENABLE_LIBRARY_SOFT_REFERENCE=true`).
Previous chapters are not re-generated.

**Q: Does the importer de-duplicate semantically similar rows?**
Not by default. Pass `--novelty-guard` to run the Batch-3 critic, which
blocks rows that are too close to existing entries. The guard requires
`enable_novelty_guard=true` + the critic module to be installed.

**Q: Can external LLMs produce real bge-m3 embeddings?**
Yes — just include `embedding: [...]` as a length-1024 float list in
each row. If omitted the importer computes a hashed embedding from the
textual fields, which is good enough for genre-level retrieval.
