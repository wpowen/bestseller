# Book Library Category / Framework Audit

Date: 2026-05-16

Scope: read-only scan of `/Volumes/书籍` by path and filename. No raw book text was copied into this repo.

## Library Snapshot

- Total files under `/Volumes/书籍`: 3,330
- Ebook-like files: 3,327 (`.txt`, `.mobi`, `.azw3`, `.epub`, `.pdf`)
- Estimated unique title rows after simple filename normalization: 2,450
- Main roots:
  - `/Volumes/书籍/Ebook`: 1,262 files, about 1,198 title rows; this is mostly web fiction.
  - `/Volumes/书籍/电子书各种格式收藏（两千多册）`: 2,067 files, about 1,252 title rows; this is mixed literary fiction, classics, nonfiction, and some genre fiction.

## Heuristic Category Coverage

These numbers are filename/path heuristics, not content-level classification.

| Bucket | Estimated title rows |
| --- | ---: |
| literary-or-general-unknown | 862 |
| action-progression | 517 |
| web-fiction-unknown | 342 |
| strategy-worldbuilding | 261 |
| nonfiction-reference | 165 |
| otherworld-cross-system | 139 |
| suspense-mystery | 87 |
| relationship-driven | 31 |
| base-building | 27 |
| esports-competition | 13 |
| eastern-aesthetic | 4 |
| female-growth-ncp | 2 |

For the likely web-fiction root `/Volumes/书籍/Ebook`, the current framework categories or near-categories cover about 856 of 1,198 title rows by filename signal, with 342 still needing content-level classification. For the mixed ebook root, most rows are not a direct web-novel generation target.

## Mapping Assessment

The current framework already has a useful high-level taxonomy:

- `action-progression`
- `relationship-driven`
- `suspense-mystery`
- `strategy-worldbuilding`
- `esports-competition`
- `female-growth-ncp`
- `base-building`
- `eastern-aesthetic`
- `default`

This should be treated as an engine taxonomy, not a one-to-one market shelf taxonomy. The source library has many fine-grained labels and title patterns: xianxia, wuxia, historical travel, game, urban ability, mystery, horror, sci-fi, base building, romance, literary fiction, nonfiction, and classics. Those should map many-to-one into canonical engines, with subgenre adapters layered underneath.

Current near-miss:

- `otherworld-cross-system` exists as a story design grammar and distillation bucket, but it is not a first-class `novel_categories` / review-profile category.
- Distillation accepts buckets such as `urban-contemporary`, `eastern-progression-fantasy`, `science-fiction-progression`, `historical-fiction`, and `romance-relationship`; several do not cleanly line up with the canonical review/profile categories.
- `web-fiction-unknown` is too large to leave to `default`; it needs content preview classification, because filename-only rules miss many genre signals.

## Quality Capability Assessment

The framework is strong in structural control:

- category-specific review profiles and prompt routing;
- story design grammars;
- premium readiness gates for progression, rule systems, faction ecology, relationship agency, decision policy, and state loops;
- whole-book gates for hooks, payoff, action, reveals, decisions, emotional turns, repetition, and momentum;
- distillation artifacts that extract reusable mechanisms, reader rewards, risk patterns, and non-reusable specifics.

But it cannot yet honestly claim it can match or exceed every book in the supplied library. Reasons:

- A large portion of the library is not commercial web fiction.
- The category bridge is not fully normalized across `novel_categories`, `genre_review_profiles`, `story_design_grammars`, and distillation buckets.
- The existing premium benchmark is fixture-based, not a live benchmark against high-quality reference books.
- Filename classification is insufficient for many web novels; first-chapter / TOC / metadata classification is needed.
- Reference quality is not yet converted into category-specific pass/fail rubrics deeply enough to say "same or better quality" by category.

## Recommended Optimization Plan

1. Add a canonical taxonomy bridge.
   - One source of truth maps prompt packs, distillation buckets, filename/content classifiers, and story grammars into canonical generation categories.
   - Decide whether `otherworld-cross-system` becomes first-class, or remains a subgenre adapter under `action-progression` / `strategy-worldbuilding`.

2. Add content-level library classification.
   - Use filename and path as candidate signals.
   - Use first chapter / table-of-contents / metadata preview for final classification.
   - Persist only private-safe identifiers or hashes in repo-visible artifacts.

3. Backfill source package genre buckets.
   - Most `data/distillation/source-*/source_manifest.json` files currently do not have `distillation_genre_bucket`.
   - Run or improve the existing classifier so aggregates are built per real category, not mostly generic or manually hinted buckets.

4. Convert reference books into benchmark rubrics.
   - For each category, distill high-quality sources into: reader promise, core engine, state variables, reward cadence, hook forms, risk patterns, and anti-copy constraints.
   - Turn those into category-specific readiness gates and chapter/volume scorecards.

5. Strengthen weak category engines.
   - Add or split engines for urban contemporary / career-reputation fiction, wuxia-jianghu, and literary-realistic fiction if those are production targets.
   - Keep nonfiction and general classics as reference material only unless the product goal expands beyond web novels.

6. Add reference-distance evaluation.
   - Generated output should be checked for category fit, quality parity, and copy-safety.
   - The target is "same mechanism strength, different expression", not imitation of titles, names, scenes, or source-specific settings.

## Bottom Line

The framework can already cover a large share of the web-fiction subset structurally, especially progression/action, strategy/worldbuilding, suspense, relationship, and base-building stories. It is not yet sufficient for a defensible claim of "same or better quality than the supplied library" across all categories.

The next high-value work is taxonomy normalization plus content-level classification, then category-specific benchmark gates built from the distilled reference corpus.
