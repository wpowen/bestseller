# Ranking Readiness Growth Loop Implementation Plan

> **For Claude:** Use `${SUPERPOWERS_SKILLS_ROOT}/skills/collaboration/executing-plans/SKILL.md` to implement this plan task-by-task.

**Goal:** Turn the research report's "文本完成度 × 商业转化度" framework into executable project scoring, productization assets, and platform-facing readiness endpoints.

**Architecture:** Keep the existing BestSeller pipeline strengths: PostgreSQL remains the truth source, existing gates continue to guard chapter/whole-book quality, and the new layer acts as a deterministic commercial-readiness rollup. The layer consumes project metadata, writing profile, story-bible overview, listing profile, optional scorecard quality, optional premium-gate score, and optional behavior metrics.

**Tech Stack:** Python dataclasses, existing FastAPI router, Typer CLI, pytest unit tests.

---

## 综合评估

The project already covers many report requirements:

- `scorecard.py`, `premium_book_gate.py`, `commercial_novel_gate.py`, `whole_book_quality_gate.py` cover local quality, premium genre structure, package promotion, and long-book momentum.
- `book_listing.py` already structures titles, categories, tags, intro, promo copy, and reader promise.
- `writing_profile.py` already encodes opening, serialization, pacing, and reader-contract strategy.

The gaps from the research report were:

- No direct implementation of the report's 100-point prelaunch text score.
- No post-launch behavior score for trial, retention, payment, and spread metrics.
- No single maturity tier translating score into platform action: flagship, strong project, vertical viable, immature.
- Marketing assets existed as promo copy, but not as the recommended 15s / 45s / 90s short-video pack.
- IP readiness was implicit across characters/world/locations, not exposed as a dashboard-ready payload.
- REST and CLI did not expose one combined ranking-readiness report.

## Tasks Executed

### Task 1: Add Ranking Readiness Service

**Files:**
- Create: `src/bestseller/services/ranking_readiness.py`
- Test: `tests/unit/test_ranking_readiness.py`

Implemented:

- report-aligned text dimensions and weights
- 1-5 raw score to weighted 100-point conversion
- behavior score modules and thresholds
- 60/40 text + behavior maturity formula
- tier/action mapping
- deterministic project evidence derivation from existing project structures
- productization plan payload
- short-video asset pack generation
- IP readiness payload

### Task 2: Extend Listing Profile

**Files:**
- Modify: `src/bestseller/services/book_listing.py`
- Test: `tests/unit/test_book_listing.py`

Implemented:

- `marketing_assets.short_video_scripts` with 15s / 45s / 90s scripts
- `ip_readiness` checklist and visual motifs
- compliance check for short-video material readiness

### Task 3: Expose CLI And API

**Files:**
- Modify: `src/bestseller/cli/main.py`
- Modify: `src/bestseller/api/routers/projects.py`
- Test: `tests/unit/test_cli.py`

Implemented:

- `bestseller commercial-gate project <slug> --json`
- `GET /api/v1/projects/{slug}/ranking-readiness`

### Task 4: Verification

Run:

```bash
uv run pytest tests/unit/test_ranking_readiness.py tests/unit/test_book_listing.py tests/unit/test_cli.py -q --no-cov
uv run python -m compileall src/bestseller/services/ranking_readiness.py src/bestseller/services/book_listing.py src/bestseller/cli/main.py src/bestseller/api/routers/projects.py
uv run ruff check src/bestseller/services/ranking_readiness.py
```

Expected:

- ranking scoring tests pass
- listing profile now contains marketing/IP payloads
- CLI JSON output includes ranking readiness and short-video scripts
- changed Python files compile
- new ranking-readiness service passes lint
