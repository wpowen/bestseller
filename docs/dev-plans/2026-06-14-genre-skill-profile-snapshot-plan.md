# Genre Skill Profile Snapshot Plan

## Goal

Introduce a small, versioned strategy layer that lets new projects record which
genre-specific Skill capabilities should guide research, planning, drafting,
review, and repair without changing core pipeline behavior or mutating existing
books.

## Requirements

1. Existing projects with no `genre_skill_profile` metadata must keep their
   current behavior.
2. New projects should receive a deterministic profile snapshot at creation
   time.
3. The snapshot must aggregate existing framework surfaces instead of creating
   a parallel system:
   - research skills from `config/research_skills/`
   - prompt pack key from `config/prompt_packs/`
   - review profile category
   - threshold profile category
   - methodology lineage policy
4. The first version is advisory/audit-only. It must not turn new gates strict
   or block legacy workflows.
5. The profile must be serializable JSON stored in `ProjectModel.metadata_json`
   so no migration is needed.

## Design

Add `bestseller.services.genre_skill_profiles` with:

- `GENRE_SKILL_PROFILE_METADATA_KEY = "genre_skill_profile"`
- `GENRE_SKILL_PROFILE_VERSION`
- `resolve_genre_skill_profile(...)`
- `attach_genre_skill_profile(metadata, profile)`
- `genre_skill_profile_from_metadata(metadata)`

`resolve_genre_skill_profile` builds one compact snapshot:

```json
{
  "version": "2026-06-14.v1",
  "profile_key": "xianxia-upgrade-core",
  "genre": "玄幻",
  "sub_genre": "升级",
  "research_skill_keys": ["base-research-discipline", "xianxia-upgrade"],
  "prompt_pack_key": "xianxia-upgrade-core",
  "review_profile_key": "action-progression",
  "threshold_profile_key": "action-progression",
  "activation": {"scope": "new_project", "gate_mode": "audit_only"},
  "lineage_policy": {"selection_owner": "planner", "downstream_policy": "consume_snapshot"}
}
```

The snapshot is intentionally descriptive first. Downstream stages already have
lineage, prompt-pack, research-skill, review-profile, and threshold loaders; the
profile gives them a stable project-level anchor without forcing a rewrite.

## Rollout

1. Add resolver and metadata helpers.
2. Attach the snapshot inside `create_project` after `writing_profile` is
   resolved.
3. Keep old projects unchanged; no backfill.
4. Tests:
   - profile resolver selects the expected xianxia/suspense/urban capability
     keys
   - metadata helper is immutable and does not invent a profile for legacy
     metadata reads
   - project creation persists the snapshot
5. Verification:
   - targeted unit tests for the new module
   - targeted project service test for creation behavior

## Non-Goals

- No strict gate promotion.
- No database migration.
- No change to generated chapters for existing projects.
- No replacement of prompt packs, review profiles, or methodology lineage.
