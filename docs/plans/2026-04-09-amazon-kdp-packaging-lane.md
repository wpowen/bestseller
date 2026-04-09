# Amazon KDP Packaging Lane Implementation Plan

> **For Claude:** Use `${SUPERPOWERS_SKILLS_ROOT}/skills/collaboration/executing-plans/SKILL.md` to implement this plan task-by-task.

**Goal:** Build an Amazon KDP packaging lane that turns a finished English novel in BestSeller into upload-ready KDP artifacts, metadata, and validation reports without changing the existing manuscript source of truth.

**Architecture:** Keep PostgreSQL project and draft data as the only source of truth, add a publication profile in project metadata, and build a KDP-specific packaging orchestrator on top of the existing export services. The lane should generate `EPUB`, `DOCX`, optional print-ready `PDF`, a metadata manifest, a QA report, and a manual upload checklist while blocking packages that fail KDP-oriented validation.

**Tech Stack:** Python, Typer CLI, SQLAlchemy async models, existing `exports.py` service, existing `ExportArtifactModel`, Markdown/HTML/ZIP-based EPUB generation, optional ReportLab PDF output.

---

## Why This Feature Exists

The repo already exports generic `markdown`, `docx`, `epub`, and `pdf`, but those exports are not yet shaped as Amazon KDP deliverables:

- `EPUB` metadata is generic and currently hardcodes `zh-CN`
- there is no KDP-specific metadata contract for title, subtitle, author display name, description, keywords, categories, series, or rights
- there is no packaging manifest that tells an operator what to upload where
- there is no rules engine for KDP content, metadata, bonus-content placement, AI disclosure, or cover/manuscript consistency
- there is no readiness signal for “safe to upload” vs “needs human fixes”

The result is that the system can produce book files, but not a repeatable “publishable KDP bundle”.

## Product Scope

### In Scope

- English novel packaging for Amazon KDP
- `Kindle eBook` as MVP
- `Paperback` interior package as phase 2
- metadata drafting, validation, and packaging
- upload-ready bundle and operator checklist
- rule-based blocking when required KDP inputs are missing or inconsistent

### Explicitly Out of Scope

- direct submission to Amazon KDP
- storing bank, tax, login, or MFA secrets in the repo or database
- cover image generation as a required dependency
- ads, Author Central, A+ Content, pricing automation, or review scraping

## Publishing Model

Because KDP’s documented flow is centered on manual Bookshelf upload and review, this feature should be designed as a `human upload-ready packaging system`, not an “auto-publish bot”. That means the lane produces:

- final book files
- platform-ready metadata
- validation findings
- a deterministic manifest of what to upload
- a checklist of remaining manual actions in KDP

## Canonical Outputs

For a successful run, the lane should produce a new derived directory:

```text
output/{project_slug}/amazon-kdp/
├── manifest.json
├── metadata.json
├── upload-checklist.md
├── qa-report.json
├── qa-report.md
├── ebook/
│   ├── book.epub
│   ├── book.docx
│   └── cover-spec.json
├── paperback/
│   ├── interior.pdf
│   ├── cover-template-spec.json
│   └── spine-calculation.json
└── assets/
    ├── cover.jpg
    └── sample-front-matter.md
```

### Manifest Contract

`manifest.json` should be the operator’s single handoff document. It should include:

- project slug and title
- package version and timestamp
- language
- enabled formats: `ebook`, `paperback`
- artifact paths and checksums
- validation summary: `pass`, `pass_with_warnings`, `fail`
- blocking findings
- KDP detail-page fields ready to paste
- manual actions still required in KDP

## Required Capabilities

### 1. Publication Profile

Add a KDP-oriented publication profile stored inside `project.metadata_json`.

Minimum fields:

- `language`: `en-US`
- `book_title`
- `subtitle`
- `author_display_name`
- `contributors`
- `series_name`
- `series_number`
- `description`
- `keywords`
- `categories`
- `primary_marketplace`
- `publishing_rights`
- `ai_generated_text`
- `ai_generated_images`
- `contains_bonus_content`
- `target_formats`
- `ebook`
- `paperback`

The profile must not include bank or tax details. Only store readiness booleans if needed:

- `identity_verified`
- `tax_profile_complete`
- `payout_method_ready`

### 2. Manuscript Normalization

Normalize a finished project draft into KDP-safe body content:

- front matter ordering
- chapter headings
- scene break normalization
- removal of web-serial artifacts
- optional end matter
- bonus content placement at the end only
- no misleading “free”, giveaway, or cross-store promotional language

### 3. Metadata Drafting

Generate and validate metadata for KDP:

- title and subtitle
- description
- contributor block
- 7 keyword phrases max
- 3 categories max
- series metadata
- rights
- AI disclosure flags

### 4. KDP Rules Engine

Validate against KDP-relevant constraints before packaging succeeds.

Initial checks:

- title contains only title content and not keyword stuffing
- title and subtitle align with cover text expectations
- title plus subtitle length is within KDP limits
- max 7 keywords
- max 3 categories
- keywords do not include banned promotional or misleading terms
- category selection is relevant to content
- bonus content appears after primary content and stays within configured threshold
- project language is English for this lane
- AI disclosure fields are present
- EPUB has navigation and table of contents
- no broken internal links
- no obvious external reward/gift/store CTA patterns
- cover asset presence and dimension checks for eBook
- paperback package refuses generation if trim, page count, or cover inputs are incomplete

### 5. Format Renderers

Produce KDP-oriented exports:

- `EPUB` with correct language metadata and package metadata
- `DOCX` with cleaned headings and front matter
- `PDF` interior for paperback with trim-aware layout
- cover spec files even when final cover art is not yet available

### 6. Packaging and Handoff

Bundle artifacts and human instructions:

- upload order
- what to paste into each KDP screen
- which fields are final and which still need manual confirmation
- warning if package passed but account readiness is incomplete

## User Workflow

### Happy Path

1. Finish the novel in BestSeller and assemble the project draft.
2. Fill or generate the `amazon_kdp` publication profile.
3. Run a strict validator.
4. Fix blocking findings until validation passes.
5. Run package generation for `ebook` and optionally `paperback`.
6. Review the generated `qa-report.md` and `upload-checklist.md`.
7. Upload the artifacts manually to KDP Bookshelf.

### Suggested CLI Shape

```bash
./scripts/run.sh publish-profile init my-story --target amazon-kdp --language en-US
./scripts/run.sh publish-profile show my-story --target amazon-kdp
./scripts/run.sh export amazon-kdp validate my-story --strict
./scripts/run.sh export amazon-kdp package my-story --formats ebook
./scripts/run.sh export amazon-kdp package my-story --formats ebook,paperback
```

## Repo-Specific Design Decisions

### Reuse Existing Export Pipeline

Do not fork a second manuscript truth source. Reuse:

- `src/bestseller/services/exports.py`
- `project.md` assembly path
- `ExportArtifactModel`
- `settings.output.base_dir`

### Keep KDP Logic Separate

Do not overload generic export code with marketplace assumptions. Add a small KDP layer that:

- builds a normalized publication payload
- invokes lower-level renderers
- runs validators
- writes a package manifest

### Prefer Metadata in Project JSON Before DB Migrations

For MVP, store KDP publication profile in `project.metadata_json["publishing"]["amazon_kdp"]`. This avoids unnecessary schema churn while the field set is still evolving.

### Package as “Derived Artifact”

Use existing export artifact registration for generated files. If one package run emits many files, register each file individually and include a manifest that ties them together.

## Implementation Tasks

### Task 1: Define the publication profile contract

**Files:**
- Modify: `src/bestseller/domain/project.py`
- Modify: `src/bestseller/services/writing_profile.py`
- Test: `tests/unit/test_project_domain.py`

**Step 1: Write the failing tests**

Add model tests that validate:

- `amazon_kdp` publication payload accepts English novel metadata
- keywords are capped at 7
- categories are capped at 3
- paperback trim options require explicit values when paperback is enabled
- sensitive fields like bank or tax secrets are not part of the model

**Step 2: Run test to verify it fails**

Run:

```bash
pytest tests/unit/test_project_domain.py -v
```

Expected:

- FAIL because the KDP-specific models do not exist

**Step 3: Write minimal implementation**

Add new Pydantic models in `src/bestseller/domain/project.py`:

- `AmazonKdpContributor`
- `AmazonKdpEbookConfig`
- `AmazonKdpPaperbackConfig`
- `AmazonKdpPublicationProfile`

Add a top-level optional publishing block to project metadata handling in `src/bestseller/services/writing_profile.py`.

**Step 4: Run test to verify it passes**

Run:

```bash
pytest tests/unit/test_project_domain.py -v
```

Expected:

- PASS for the new KDP publication profile cases

**Step 5: Commit**

```bash
git add src/bestseller/domain/project.py src/bestseller/services/writing_profile.py tests/unit/test_project_domain.py
git commit -m "feat: add amazon kdp publication profile"
```

### Task 2: Add metadata drafting and normalization helpers

**Files:**
- Create: `src/bestseller/services/publishing/amazon_kdp.py`
- Create: `src/bestseller/services/publishing/amazon_kdp_rules.py`
- Test: `tests/unit/test_amazon_kdp_metadata.py`

**Step 1: Write the failing tests**

Cover:

- project draft converts into a normalized KDP payload
- keywords and categories are deduped
- title/subtitle and description fields are trimmed and normalized
- AI disclosure defaults are explicit, not implicit

**Step 2: Run test to verify it fails**

Run:

```bash
pytest tests/unit/test_amazon_kdp_metadata.py -v
```

Expected:

- FAIL because the service module does not exist

**Step 3: Write minimal implementation**

Implement helpers that:

- read `project.metadata_json`
- build a canonical KDP payload
- normalize keywords and categories
- infer missing description scaffolds from existing project metadata only when safe
- leave human review required for low-confidence fields

**Step 4: Run test to verify it passes**

Run:

```bash
pytest tests/unit/test_amazon_kdp_metadata.py -v
```

Expected:

- PASS

**Step 5: Commit**

```bash
git add src/bestseller/services/publishing/amazon_kdp.py src/bestseller/services/publishing/amazon_kdp_rules.py tests/unit/test_amazon_kdp_metadata.py
git commit -m "feat: add amazon kdp metadata builder"
```

### Task 3: Add KDP validation rules and QA report generation

**Files:**
- Modify: `src/bestseller/services/publishing/amazon_kdp_rules.py`
- Create: `src/bestseller/domain/inspection.py` if extension is needed
- Test: `tests/unit/test_amazon_kdp_validation.py`

**Step 1: Write the failing tests**

Cover these validations:

- more than 7 keywords fails
- more than 3 categories fails
- missing AI disclosure fails
- bonus content before main body fails
- missing cover asset yields warning or failure depending on format
- wrong language for KDP English lane fails

**Step 2: Run test to verify it fails**

Run:

```bash
pytest tests/unit/test_amazon_kdp_validation.py -v
```

Expected:

- FAIL with missing validator implementation

**Step 3: Write minimal implementation**

Return structured findings with:

- `severity`
- `code`
- `message`
- `fix_hint`
- `blocking`

Generate both machine-readable and markdown QA outputs.

**Step 4: Run test to verify it passes**

Run:

```bash
pytest tests/unit/test_amazon_kdp_validation.py -v
```

Expected:

- PASS

**Step 5: Commit**

```bash
git add src/bestseller/services/publishing/amazon_kdp_rules.py src/bestseller/domain/inspection.py tests/unit/test_amazon_kdp_validation.py
git commit -m "feat: add amazon kdp validation rules"
```

### Task 4: Upgrade the EPUB and DOCX renderers for English KDP output

**Files:**
- Modify: `src/bestseller/services/exports.py`
- Test: `tests/unit/test_exports.py`

**Step 1: Write the failing tests**

Cover:

- EPUB language is set from the project/publication profile instead of hardcoded `zh-CN`
- EPUB metadata includes title and author
- generated navigation and manifest remain valid
- DOCX title page uses publication title and author metadata cleanly

**Step 2: Run test to verify it fails**

Run:

```bash
pytest tests/unit/test_exports.py -v
```

Expected:

- FAIL because current renderers are generic and language is hardcoded

**Step 3: Write minimal implementation**

Refactor exporter helpers so KDP packager can pass:

- language
- author display name
- subtitle
- cover asset reference

Do not break existing generic export commands.

**Step 4: Run test to verify it passes**

Run:

```bash
pytest tests/unit/test_exports.py -v
```

Expected:

- PASS for both legacy exports and new KDP-aware branches

**Step 5: Commit**

```bash
git add src/bestseller/services/exports.py tests/unit/test_exports.py
git commit -m "feat: make exporters kdp-aware"
```

### Task 5: Add paperback interior packaging support

**Files:**
- Modify: `src/bestseller/services/exports.py`
- Modify: `src/bestseller/services/publishing/amazon_kdp.py`
- Test: `tests/unit/test_amazon_kdp_paperback.py`

**Step 1: Write the failing tests**

Cover:

- paperback packaging refuses to run without trim size
- interior PDF generation computes page count metadata
- cover template spec is emitted for manual designer handoff

**Step 2: Run test to verify it fails**

Run:

```bash
pytest tests/unit/test_amazon_kdp_paperback.py -v
```

Expected:

- FAIL

**Step 3: Write minimal implementation**

Support a constrained paperback MVP:

- trim sizes from an allowlist
- black-and-white vs color flag
- cream vs white paper
- left-to-right only
- cover template spec output even if final art is absent

**Step 4: Run test to verify it passes**

Run:

```bash
pytest tests/unit/test_amazon_kdp_paperback.py -v
```

Expected:

- PASS

**Step 5: Commit**

```bash
git add src/bestseller/services/exports.py src/bestseller/services/publishing/amazon_kdp.py tests/unit/test_amazon_kdp_paperback.py
git commit -m "feat: add amazon kdp paperback packaging"
```

### Task 6: Add package manifest and artifact registration

**Files:**
- Modify: `src/bestseller/services/publishing/amazon_kdp.py`
- Modify: `src/bestseller/services/exports.py`
- Test: `tests/unit/test_amazon_kdp_package.py`

**Step 1: Write the failing tests**

Cover:

- package output directory is created deterministically
- `manifest.json`, `metadata.json`, `qa-report.json`, and `upload-checklist.md` are written
- export artifacts are registered with checksums

**Step 2: Run test to verify it fails**

Run:

```bash
pytest tests/unit/test_amazon_kdp_package.py -v
```

Expected:

- FAIL

**Step 3: Write minimal implementation**

Implement a package orchestrator that:

- validates first
- aborts on blocking findings
- writes all files under `output/{slug}/amazon-kdp/`
- records each derived file through `ExportArtifactModel`

**Step 4: Run test to verify it passes**

Run:

```bash
pytest tests/unit/test_amazon_kdp_package.py -v
```

Expected:

- PASS

**Step 5: Commit**

```bash
git add src/bestseller/services/publishing/amazon_kdp.py src/bestseller/services/exports.py tests/unit/test_amazon_kdp_package.py
git commit -m "feat: add amazon kdp package orchestration"
```

### Task 7: Expose the workflow in CLI

**Files:**
- Modify: `src/bestseller/cli/main.py`
- Test: `tests/unit/test_cli.py`

**Step 1: Write the failing tests**

Add CLI coverage for:

- `publish-profile init`
- `publish-profile show`
- `export amazon-kdp validate`
- `export amazon-kdp package`

**Step 2: Run test to verify it fails**

Run:

```bash
pytest tests/unit/test_cli.py -k "amazon_kdp or publish_profile" -v
```

Expected:

- FAIL because commands do not exist

**Step 3: Write minimal implementation**

Add Typer commands that:

- initialize a publication profile from project state
- display current KDP publication profile
- run validation
- build the package

**Step 4: Run test to verify it passes**

Run:

```bash
pytest tests/unit/test_cli.py -k "amazon_kdp or publish_profile" -v
```

Expected:

- PASS

**Step 5: Commit**

```bash
git add src/bestseller/cli/main.py tests/unit/test_cli.py
git commit -m "feat: add amazon kdp cli workflow"
```

### Task 8: Add docs and operator guidance

**Files:**
- Modify: `README.md`
- Create: `docs/amazon-kdp-packaging.md`
- Test: none

**Step 1: Write the docs**

Document:

- what the KDP packaging lane does
- what it does not do
- required metadata fields
- how to prepare cover assets
- how to interpret warnings vs blockers
- manual KDP upload sequence

**Step 2: Review the docs**

Check that examples match real commands and paths from implemented CLI.

**Step 3: Commit**

```bash
git add README.md docs/amazon-kdp-packaging.md
git commit -m "docs: document amazon kdp packaging workflow"
```

## Acceptance Criteria

The feature is complete when:

- a finished English project can produce a KDP package from one command
- the package emits at least `EPUB`, `DOCX`, metadata manifest, and QA report
- validation blocks uploads when required KDP fields are missing or misleading
- language, author, title, and description metadata are package-aware rather than generic
- paperback packaging can be enabled with explicit trim and print settings
- the system never stores bank or tax secrets
- the final bundle is understandable by a human operator without reading the source code

## Rollout Order

1. `ebook-only` package generation
2. validation and QA report hardening
3. paperback interior support
4. optional cover-template tooling
5. future marketplace abstraction if Amazon KDP becomes one of several export lanes

## Notes for Future Iterations

- Add a competitor-research-assisted keyword and category suggester only after the deterministic contract is stable.
- If later needed, add a generic `publishing package run` model instead of overloading `ExportArtifactModel`.
- Do not add direct KDP credential handling unless there is a clear, supported integration path and the security model is defined first.

Plan complete and saved to `docs/plans/2026-04-09-amazon-kdp-packaging-lane.md`. Two execution options:

**1. Subagent-Driven (this session)** - I dispatch fresh subagent per task, review between tasks, fast iteration

**2. Parallel Session (separate)** - Open new session with executing-plans, batch execution with checkpoints

**Which approach?**
