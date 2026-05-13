# Narrative Diversity Systemic Repair Implementation Plan

> **For Claude:** Use `${SUPERPOWERS_SKILLS_ROOT}/skills/collaboration/executing-plans/SKILL.md` to implement this plan task-by-task.

**Goal:** Stop the planner from converging every book toward the same parent/sibling loss mystery, generic world-stakes, generic personality, generic chapter-outline, and duplicate-character patterns.

**Architecture:** Add a deterministic planning-sameness audit layer, then fix the upstream generators that currently emit generic defaults. Existing per-project tools such as `diversity_budget.py`, `plan_fingerprint.py`, `narrative_contracts.py`, and `foundation_richness.py` should be reused, but they need cross-project trope detection and stricter blocking before planning artifacts are persisted.

**Tech Stack:** Python, SQLAlchemy/Postgres JSONB, Pydantic domain models, pytest unit tests.

---

## Audit Snapshot

The 2026-05-12 DB audit found these systemic issues across the 7 active projects:

- 7/7 project metadata payloads contain placeholder name leakage: `林逸` or `Alex Reed`.
- 7/7 contain generic global stakes such as `更大范围的秩序会因此崩坏` / `如果幕后计划成功...秩序都会被改写`.
- 6/7 contain the generic protagonist wound pair: `再次因为自己的决定害死重要的人` / `主角一直怀疑过去的失败并非表面原因`.
- 6/7 contain the same fallback personality profile markers: `INTJ`, `6w5`, `威胁扫描`, `责任过度归因`.
- 7/7 top-level plans are truth/old-case/identity heavy.
- 5 long projects have large character registry bloat: `Shadowbound to the Crown` has 888 character rows and 297 likely duplicate-name rows; `The Witness Protocol` has 1100 rows and 314 likely duplicate-name rows.
- Chapter plans contain the generic bridge sentence `承接上一章尾钩，主角没有空档去长篇解释设定。` in 333-503 planned chapters for five long projects.
- English title fallbacks overuse a narrow word pool: `cipher` appears 20 times in `The Witness Protocol`, `protocol` 21 times in `Breaking Point`, `shadow` 11 times in `Shadowbound to the Crown`.
- Existing code already recognizes some generic patterns in `narrative_contracts.py`, but polluted artifacts are still persisted, so the gates are not applied early or strictly enough.

Primary source locations:

- `src/bestseller/services/planner.py:1683` and `src/bestseller/services/planner.py:1694` define fallback names `Alex Reed` / `林逸`.
- `src/bestseller/services/planner.py:2423` hard-codes generic core-wound fallback.
- `src/bestseller/services/planner.py:2436` and `src/bestseller/services/planner.py:2451` hard-code `INTJ` / `6w5`.
- `src/bestseller/services/planner.py:3930` hard-codes generic social/existential stakes.
- `src/bestseller/services/planner.py:4107` uses `生存压力` as a default force label.
- `src/bestseller/services/planner.py:6006` and `src/bestseller/services/planner.py:6019` define narrow fallback title pools.
- `src/bestseller/services/planner.py:6568` to `src/bestseller/services/planner.py:6579` define generic chapter-goal templates.
- `src/bestseller/services/planner.py:6844` emits the generic opening bridge.
- `src/bestseller/services/narrative_contracts.py:35` flags that same bridge as generic, but current pipelines still persisted it.
- `config/novel_categories/action-progression.yaml:147`, `config/novel_categories/female-growth-ncp.yaml:148`, and `config/novel_categories/relationship-driven.yaml:146` bias protagonist wounds toward loss/betrayal.
- `config/writing_methodology.yaml:75` uses `妹妹` as the first concrete stakes example.

---

## Task 1: Add A Planning Sameness Audit Service

**Files:**
- Create: `src/bestseller/services/planning_sameness_audit.py`
- Create: `tests/unit/test_planning_sameness_audit.py`

**Step 1: Write failing tests**

Add tests for deterministic detectors:

```python
def test_detects_placeholder_name_leakage():
    report = scan_project_payload({"dramatic_question": "林逸能否完成目标？"}, language="zh-CN")
    assert "placeholder_name_leak" in report.codes

def test_detects_generic_world_stakes():
    payload = {"stakes": {"social": "更大范围的秩序会因此崩坏，更多无辜者将被牵连。"}}
    report = scan_project_payload(payload, language="zh-CN")
    assert "generic_world_stakes" in report.codes

def test_detects_generic_personhood_template():
    payload = {"protagonist": {"psych_profile": {"mbti": "INTJ", "enneagram": "6w5"}}}
    report = scan_project_payload(payload, language="zh-CN")
    assert "generic_personhood_template" in report.codes

def test_detects_family_loss_mystery_only_when_family_and_loss_both_present():
    clean = scan_project_payload({"logline": "主角因一次公开失败失去职位。"}, language="zh-CN")
    bad = scan_project_payload({"logline": "主角追查父亲失踪真相。"}, language="zh-CN")
    assert "family_loss_mystery" not in clean.codes
    assert "family_loss_mystery" in bad.codes

def test_cross_project_repetition_blocks_over_budget():
    reports = [
        scan_project_payload({"logline": "父亲失踪，主角追查旧案。"}, language="zh-CN")
        for _ in range(3)
    ]
    summary = summarize_cross_project_sameness(reports, max_same_trope_ratio=0.5)
    assert "cross_project_trope_overuse" in summary.codes
```

**Step 2: Implement minimal service**

Expose:

```python
@dataclass(frozen=True)
class PlanningSamenessFinding:
    code: str
    severity: str
    path: str
    message: str
    evidence: dict[str, Any] = field(default_factory=dict)

@dataclass(frozen=True)
class PlanningSamenessReport:
    findings: tuple[PlanningSamenessFinding, ...]

    @property
    def codes(self) -> set[str]: ...
    @property
    def has_blocking(self) -> bool: ...

def scan_project_payload(payload: Mapping[str, Any], *, language: str | None) -> PlanningSamenessReport: ...
def scan_chapter_outline_payload(payload: Mapping[str, Any], *, language: str | None) -> PlanningSamenessReport: ...
def scan_character_rows(rows: Sequence[Mapping[str, Any]], *, language: str | None) -> PlanningSamenessReport: ...
def summarize_cross_project_sameness(reports: Sequence[PlanningSamenessReport], *, max_same_trope_ratio: float = 0.35) -> PlanningSamenessReport: ...
```

Initial detector codes:

- `placeholder_name_leak`
- `generic_world_stakes`
- `generic_protagonist_wound`
- `generic_personhood_template`
- `family_loss_mystery`
- `old_case_identity_default`
- `truth_overload`
- `generic_opening_bridge`
- `meta_hook_language`
- `functional_title_template`
- `english_outline_contains_cjk_boilerplate`
- `character_alias_bloat`

**Step 3: Run tests**

Run:

```bash
pytest tests/unit/test_planning_sameness_audit.py -q
```

Expected: PASS.

---

## Task 2: Block Placeholder Names And Generic Project Stakes

**Files:**
- Modify: `src/bestseller/services/planner.py`
- Modify: `tests/unit/test_planner_services.py`
- Test: `tests/unit/test_planning_sameness_audit.py`

**Step 1: Write failing tests**

Add coverage that `_fallback_book_spec()` does not emit `林逸`/`Alex Reed` inside `dramatic_question`, `stakes`, or nested protagonist payload when a concrete protagonist name exists in `book_spec` or generated names.

Add a test that generated fallback stakes are specific and do not equal:

- `更大范围的秩序会因此崩坏，更多无辜者将被牵连。`
- `如果幕后计划成功，整个世界的基本运行秩序都会被改写。`

**Step 2: Replace fallback names with safe internal sentinels**

Change `_role_label()` so the planner never persists `林逸` / `Alex Reed` as real names. If no name is available, use an internal sentinel such as `__PROTAGONIST_NAME_REQUIRED__` and make `_ensure_book_spec_bible_fields()` raise or request regeneration instead of persisting.

**Step 3: Repair `_fallback_dramatic_question()`**

Before formatting, assert:

```python
if protagonist_name in {"林逸", "Alex Reed", "主角", "Protagonist"}:
    protagonist_name = _non_empty_string(project.title, "主角")
```

Prefer the actual protagonist from normalized `book_spec["protagonist"]["name"]`.

**Step 4: Replace generic stakes**

Add a helper:

```python
def _fallback_stakes(project: ProjectModel, protagonist_name: str, category_key: str | None) -> dict[str, str]:
    ...
```

Make it category-specific and concrete:

- Action progression: resource, body, rank, enemy attention.
- Mystery: evidence window, witness safety, jurisdiction/procedure.
- Romance: public reputation, relationship boundary, social cost.
- Apocalypse: supply, route, medicine, shelter control.

Do not emit world-order stakes unless the premise explicitly contains apocalypse/cosmic/global terms.

**Step 5: Run tests**

Run:

```bash
pytest tests/unit/test_planner_services.py tests/unit/test_planning_sameness_audit.py -q
```

Expected: PASS.

---

## Task 3: Add Trope Budgeting For Core Motivation

**Files:**
- Create: `config/narrative_diversity.yaml`
- Modify: `src/bestseller/services/planner.py`
- Modify: `config/novel_categories/action-progression.yaml`
- Modify: `config/novel_categories/female-growth-ncp.yaml`
- Modify: `config/novel_categories/relationship-driven.yaml`
- Modify: `config/novel_categories/suspense-mystery.yaml`
- Modify: `config/writing_methodology.yaml`
- Create: `tests/unit/test_planner_trope_budget.py`

**Step 1: Write failing tests**

Test that no default core-wound path chooses parent/sibling loss unless the premise explicitly includes it.

Test that several generated fallback core wounds across different category keys do not all map to `family_loss_mystery`.

**Step 2: Add trope taxonomy**

Create `config/narrative_diversity.yaml`:

```yaml
motivation_tropes:
  family_loss_mystery:
    markers: ["父亲", "母亲", "父母", "妹妹", "sister", "father", "mother"]
    max_cross_project_ratio: 0.30
  professional_disgrace:
    examples: ["误判导致职位被剥夺", "公开失败后被体系放逐"]
  resource_debt:
    examples: ["欠下不可逃避的资源债", "关键配给被剥夺"]
  public_identity_fall:
    examples: ["身份被公开污名化", "阶层位置被夺走"]
  bodily_limit:
    examples: ["身体缺陷或能力代价限制行动"]
  moral_compromise:
    examples: ["曾为正确目标做过错误选择"]
  chosen_obligation:
    examples: ["自愿承担危险职责，而非被亲属旧案推动"]
```

**Step 3: Rewrite category core wounds**

Change `core_wound_*` defaults from concrete loss/betrayal toward non-family-specific pressure. Example:

```yaml
core_wound_zh: "曾因一次公开失败被体系判定为无用，从此把每一次成长都当成翻案。"
core_wound_en: "A public failure made the system mark them as useless; every gain since then is a rebuttal."
```

Keep family examples only inside optional candidate pools, not as first defaults.

**Step 4: Update CastSpec prompt**

In the CastSpec prompt near `planner.py:7253`, add:

```text
除非 BookSpec/premise 明确指定，禁止默认把主角核心伤口写成父母失踪、父母死亡、妹妹被抓、家族旧案或身世真相。
家庭可以存在，但不能自动成为长线悬念引擎。
```

**Step 5: Run tests**

Run:

```bash
pytest tests/unit/test_planner_trope_budget.py tests/unit/test_planner_services.py -q
```

Expected: PASS.

---

## Task 4: Diversify Personhood Fallbacks

**Files:**
- Modify: `src/bestseller/services/planner.py`
- Modify: `tests/unit/test_planner_services.py`
- Modify: `tests/unit/test_story_bible_services.py`

**Step 1: Write failing tests**

Add tests proving two different project seeds do not receive identical full psych profiles and do not both default to `INTJ` / `6w5`.

Add tests proving fallback `ip_anchor.core_wound` does not contain:

- `相信过错误叙事`
- `无法补偿的人替自己付出代价`
- `once trusted the wrong version of events`

**Step 2: Replace hard-coded profile with seeded profile pool**

Add helper:

```python
def _seeded_personhood_profile(seed_text: str, *, language: str | None) -> dict[str, Any]:
    ...
```

Use a small coherent pool:

- Analytical skeptic: high openness/conscientiousness, low extraversion.
- Impulsive protector: high agreeableness/neuroticism, medium extraversion.
- Status climber: high conscientiousness, low agreeableness.
- Communal builder: high agreeableness, secure attachment.
- Avoidant strategist: current INTJ-like profile, but only one option.

**Step 3: Replace generic core wound fallback**

Use `basis` and category to create a concrete, non-family event:

```python
"第一次公开判断失误让{name}失去进入核心圈的资格。"
"A public misread cost {name} access to the only institution that could protect them."
```

**Step 4: Run tests**

Run:

```bash
pytest tests/unit/test_planner_services.py tests/unit/test_story_bible_services.py -q
```

Expected: PASS.

---

## Task 5: Make Chapter Outline Generic Language Blocking

**Files:**
- Modify: `src/bestseller/services/planner.py`
- Modify: `src/bestseller/services/narrative_contracts.py`
- Modify: `src/bestseller/services/workflows.py`
- Modify: `tests/unit/test_narrative_contracts.py`
- Modify: `tests/unit/test_workflow_services.py`
- Modify: `tests/unit/test_planner_services.py`

**Step 1: Write failing tests**

Add tests that a `ChapterOutlineBatchInput` with any of these fields fails before materialization:

- `opening_situation = "承接上一章尾钩，主角没有空档去长篇解释设定。"`
- `hook_description` containing `每章结尾设置`, `每3-5章`, `避免纯悬念钩子`
- `chapter_goal` containing `一种环境或体系层面的威胁出现`
- English chapter outline fields containing Chinese boilerplate.

**Step 2: Enforce blocking in materialization**

Find where `validate_narrative_contract_batch()` or equivalent gates are called in `workflows.py`. If findings are currently warning-only or bypassed in fallback paths, make generic outline findings blocking before rows are persisted.

**Step 3: Replace fallback opening bridge**

In `_fallback_chapter_outline_batch()`, replace the static opening bridge with a concrete string from prior hook/current location/current participants:

```python
opening_situation = _fallback_opening_situation(
    previous_hook=previous_hook,
    current_goal=chapter_goal,
    protagonist=protagonist_name,
    language=project.language,
)
```

Never emit `承接上一章尾钩`.

**Step 4: Replace meta hook leakage**

If `writing_profile.market.chapter_hook_strategy` contains strategy text such as `每3-5章`, do not copy it into `hook_description`. Convert it into a concrete event through `_fallback_hook_description()` or leave it only in prompts.

**Step 5: Run tests**

Run:

```bash
pytest tests/unit/test_narrative_contracts.py tests/unit/test_workflow_services.py tests/unit/test_planner_services.py -q
```

Expected: PASS.

---

## Task 6: Replace Functional Title Fallback Pools

**Files:**
- Modify: `src/bestseller/services/planner.py`
- Modify: `src/bestseller/domain/workflow.py`
- Modify: `src/bestseller/services/diversity_budget.py`
- Modify: `tests/unit/test_title_cooldown.py`
- Modify: `tests/unit/test_planner_services.py`

**Step 1: Write failing tests**

Add tests proving `_chapter_fallback_subtitle()` does not emit combinations from `_FALLBACK_TITLE_PREFIXES` + `_FALLBACK_TITLE_SUFFIXES`.

Add tests for English titles proving repeated tokens such as `cipher`, `threshold`, `protocol`, `shadow`, and `genesis` are cooled down.

**Step 2: Prefer event-derived titles**

Make fallback titles extract concrete nouns from `chapter_goal`, `main_conflict`, `hook_description`, or `unique_beat`.

Chinese examples:

- `旧药圃对质`
- `铜牌验血`
- `三号门落锁`

English examples:

- `The Depot Consent Form`
- `Maya's Second Photograph`
- `The Locked Exit`

**Step 3: Extend title cooldown to English**

`diversity_budget.extract_title_ngrams()` currently focuses on CJK. Add English token cooldown for non-stopword title tokens and bigrams.

**Step 4: Run tests**

Run:

```bash
pytest tests/unit/test_title_cooldown.py tests/unit/test_planner_services.py -q
```

Expected: PASS.

---

## Task 7: Canonicalize Character Registry Writes

**Files:**
- Modify: `src/bestseller/services/character_identity_resolver.py`
- Modify: `src/bestseller/services/story_bible.py`
- Modify: `src/bestseller/services/workflows.py`
- Create: `scripts/repair_character_registry_alias_bloat.py`
- Modify: `tests/unit/test_character_identity_resolver.py`
- Modify: `tests/unit/test_story_bible_services.py`

**Step 1: Write failing tests**

Add tests for aliases:

```python
assert normalize_character_label("Rowan Ashford (The Nineteenth)") == "Rowan Ashford"
assert normalize_character_label("林鸢 (038号)") == "林鸢"
assert normalize_character_label("Celeste (Rowan's mother)") == "Celeste"
```

Add tests that inserting an alias updates the canonical character row rather than creating a new one.

**Step 2: Implement canonical label normalization**

Rules:

- Strip parenthetical qualifiers.
- Move qualifier into `metadata.alias_qualifiers`.
- Treat slash labels such as `林鸢/姜澄` as invalid for a single character row unless explicitly marked as group/entity.
- Treat generic labels such as `Her mother`, `His father`, `The Entity` as role/entity references, not named characters, unless already canonized.

**Step 3: Add DB repair script**

`scripts/repair_character_registry_alias_bloat.py` should:

1. Snapshot affected rows to JSONL in `artifacts/character_registry_repair/<timestamp>/`.
2. Group rows by normalized label per project.
3. Pick canonical row by priority: protagonist/antagonist > named non-placeholder > earliest created.
4. Merge aliases and metadata.
5. Update direct FK tables where present.
6. Mark ambiguous rows for manual review rather than deleting blindly.

**Step 4: Run tests**

Run:

```bash
pytest tests/unit/test_character_identity_resolver.py tests/unit/test_story_bible_services.py -q
```

Expected: PASS.

---

## Task 8: Add DB Audit And Repair CLI

**Files:**
- Modify: `src/bestseller/cli/main.py`
- Create: `src/bestseller/services/project_sameness_report.py`
- Create: `tests/unit/test_project_sameness_report.py`

**Step 1: Write failing tests**

Test a pure function that summarizes project audit counts:

```python
summary = summarize_project_sameness(projects=[...], chapters=[...], characters=[...])
assert summary["placeholder_name_leak_projects"] == 1
assert summary["generic_opening_bridge_chapters"] == 10
```

**Step 2: Add CLI command**

Add:

```bash
python -m bestseller.cli.main audit-sameness --format markdown
python -m bestseller.cli.main audit-sameness --format json
```

Report:

- project-level leakage
- trope overuse
- chapter boilerplate
- title token overuse
- character alias bloat
- English/CJK localization contamination

**Step 3: Add repair command for metadata-only fields**

Add a dry-run first:

```bash
python -m bestseller.cli.main repair-sameness --metadata-only --dry-run
```

This may repair:

- placeholder names in `dramatic_question` / `stakes`
- generic social/existential stakes
- generic `theme_statement` fallback when clearly stale

Do not rewrite chapter prose in this command.

**Step 4: Run tests**

Run:

```bash
pytest tests/unit/test_project_sameness_report.py tests/unit/test_cli.py -q
```

Expected: PASS.

---

## Task 9: Clean Existing Project Metadata Safely

**Files:**
- Create: `scripts/repair_existing_sameness_metadata.py`
- Create: `tests/unit/test_existing_sameness_metadata_repair.py`

**Step 1: Write failing tests**

Use fixture payloads copied from current DB symptoms:

- `林逸能否...`
- `Alex Reed must...`
- generic global stakes

Assert repair uses the actual project protagonist/title and category-specific stakes.

**Step 2: Implement dry-run script**

Script behavior:

```bash
python scripts/repair_existing_sameness_metadata.py --dry-run
python scripts/repair_existing_sameness_metadata.py --apply
```

Dry-run output lists per project:

- fields to change
- old value
- new value
- whether manual review is required

**Step 3: Guard with snapshot**

Before applying, dump affected project rows to:

```text
artifacts/sameness_repair/<timestamp>/projects_before.jsonl
```

**Step 4: Run tests**

Run:

```bash
pytest tests/unit/test_existing_sameness_metadata_repair.py -q
```

Expected: PASS.

---

## Task 10: Re-Audit And Set Release Gate

**Files:**
- Modify: `docs/whole-book-quality-verification-matrix.md`
- Modify: `config/quality_gates.yaml`
- Test: existing affected tests.

**Step 1: Add verification checklist**

Update the quality matrix with:

- zero placeholder-name leakage in project metadata
- zero generic project stakes
- zero generic opening bridge in newly planned chapters
- no more than 30% of active projects using `family_loss_mystery`
- no exact generic personhood fallback in generated cast specs
- title token cooldown passes for Chinese and English
- character alias bloat reviewed before continuation

**Step 2: Enable gate in config**

Add a gate setting such as:

```yaml
planning_sameness:
  enabled: true
  block_on:
    - placeholder_name_leak
    - generic_world_stakes
    - generic_opening_bridge
    - meta_hook_language
    - english_outline_contains_cjk_boilerplate
  warn_on:
    - family_loss_mystery
    - old_case_identity_default
    - truth_overload
    - character_alias_bloat
```

**Step 3: Run verification**

Run:

```bash
pytest tests/unit/test_planning_sameness_audit.py \
  tests/unit/test_planner_services.py \
  tests/unit/test_narrative_contracts.py \
  tests/unit/test_workflow_services.py \
  tests/unit/test_character_identity_resolver.py \
  tests/unit/test_title_cooldown.py \
  tests/unit/test_cli.py -q
```

Expected: PASS.

Then run:

```bash
python -m bestseller.cli.main audit-sameness --format markdown
```

Expected after applying metadata-only and registry repairs:

- `placeholder_name_leak_projects = 0`
- `generic_world_stakes_projects = 0`
- newly generated chapter outlines have `generic_opening_bridge_chapters = 0`
- existing old chapters may remain historical unless scheduled for rewrite.

---

## Rollout Notes

- Do not bulk rewrite already drafted prose just to remove old planning smells. Repair metadata, future outlines, and character registry first.
- For active books, replan only future chapters from the current frontier unless a book-specific continuity repair requires earlier changes.
- Keep family-loss plots where they are already core canon, but mark them as spent tropes so the next projects cannot default to the same engine.
- Character registry repair is the highest-risk data change. It must start with dry-run snapshots and manual review for ambiguous rows.
