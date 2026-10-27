"""Idempotent source migration. Preserve originals; retire only optional unadopted inputs."""
from __future__ import annotations

import copy
from typing import Any

from bestseller.services.content_origin_policy import content_hash, origin_record
from bestseller.services.invariants import migrate_invariant_content_defaults
from bestseller.services.hype_engine import HypeScheme, hype_scheme_to_dict
from bestseller.services.prompt_source_policy import (
    REFERENCE_KEYS, archive_metadata, valid_reference_value,
)
from bestseller.services.writing_profile import migrate_writing_profile_defaults


def migrate_project_prompt_state(project: Any, *, apply: bool = False) -> dict:
    meta = copy.deepcopy(dict(project.metadata_json or {}))
    original_meta = copy.deepcopy(meta)
    original_invariants = copy.deepcopy(project.invariants_json or {})
    invariants = copy.deepcopy(original_invariants)
    receipts: dict = {}
    unresolved: list[str] = []
    if meta.get("story_package_path") and not meta.get("story_package_adoption"):
        unresolved.append("story_package_import_requires_adoption")
    raw = meta.get("writing_profile")
    if isinstance(raw, dict):
        migration = migrate_writing_profile_defaults(raw, origins=meta.get("writing_profile_origins"),
                                                     language=project.language)
        meta["writing_profile"] = migration.payload
        meta["writing_profile_origins"] = migration.origins
        # Missing provenance may become "unknown", which changes which
        # optional fields the source policy considers on the next read. Keep
        # the original migration receipt, but summarize the persisted state
        # using its resulting provenance so a second apply is a no-op.
        persisted_review = migrate_writing_profile_defaults(
            migration.payload, origins=migration.origins, language=project.language,
        )
        prior_review = meta.get("framework_state_review") or {}
        if migration.receipt["changes"] or (
            migration.receipt["unresolved_paths"] and prior_review.get("profile_hash") != content_hash(migration.payload)
        ):
            receipts["writing_profile"] = migration.receipt
        meta["framework_state_review"] = {
            "profile_hash": content_hash(migration.payload),
            "unresolved_profile_paths": persisted_review.receipt["unresolved_paths"],
        }
        unresolved.extend("writing_profile." + path for path in persisted_review.receipt["unresolved_paths"])
        # Top-level projections must not resurrect a retired field. Preserve
        # mismatched originals for source review instead of guessing authorship.
        profile_curve = migration.payload.get("character", {}).get("growth_curve")
        if "growth_curve" in meta:
            origin = migration.origins.get("character.growth_curve") or {}
            if origin.get("kind") == "explicit" or origin.get("adopted"):
                if meta["growth_curve"] != profile_curve:
                    meta = archive_metadata(meta, ("growth_curve",), reason="conflicting_legacy_growth_projection")
                    meta["growth_curve"] = profile_curve
                    receipts["growth_curve_projection"] = {"decision":"use_adopted_profile", "conflict_preserved":True}
            else:
                meta = archive_metadata(meta, ("growth_curve",), reason="unadopted_profile_projection")
    if invariants:
        migration = migrate_invariant_content_defaults(invariants, origins=meta.get("invariant_content_origins"))
        invariants = migration.payload
        meta["invariant_content_origins"] = migration.origins
        # Unknown optional event/effect choices stay in the archive, not in the
        # active creative contract. Identity, length, canon and POV are untouched.
        neutral = {"opening_archetype_pool": [], "cliffhanger_policy": {"no_repeat_within": 3, "allowed_types": []},
                   "hype_scheme": hype_scheme_to_dict(HypeScheme())}
        for path in migration.receipt["unresolved_paths"]:
            invariants[path] = copy.deepcopy(neutral[path])
            unresolved.append("invariants." + path)
            # This records a neutral projection, never approval of the old value.
            meta["invariant_content_origins"][path] = origin_record(invariants[path], kind="framework_default")
        if migration.receipt["changes"] or migration.receipt["unresolved_paths"]:
            receipts["invariants"] = migration.receipt
    invalid_caches = tuple(key for key in REFERENCE_KEYS if key in meta and valid_reference_value(project, key) is None)
    meta = archive_metadata(meta, invalid_caches, reason="unverified_or_stale_reference")
    if invalid_caches:
        receipts["reference_cache"] = {"retired_keys": list(invalid_caches)}
    # Unbound baselines cannot acquire authority simply by restarting a run.
    from bestseller.services.prompt_source_policy import outline_dependency_hash
    dep_hash = outline_dependency_hash(meta)
    if meta.get("outline_commercial_repair_baseline") and dep_hash and meta.get("outline_repair_dependency_hash") != dep_hash:
        meta = archive_metadata(meta, tuple(key for key in meta if key.startswith("outline_commercial_")),
                                reason="outline_dependency_mismatch")
        receipts["outline_baseline"] = {"status": "superseded"}
    if receipts:
        archive = dict(meta.get("framework_state_migrations") or {})
        # Input evidence gives an idempotent batch identity; never append on reads.
        identity = content_hash({"profile": receipts.get("writing_profile"), "invariants": original_invariants,
                                 "receipts": receipts})
        archive.setdefault(identity, {"policy": "framework-state.v1", "receipts": receipts,
                                      "original_invariants": original_invariants})
        meta["framework_state_migrations"] = archive
    changed = meta != original_meta or invariants != original_invariants
    if apply and changed:
        project.metadata_json = meta
        project.invariants_json = invariants or None
    return {"policy": "framework-state.v1", "changed": changed, "unresolved_paths": unresolved,
            "retired_reference_keys": list(invalid_caches), "before_hash": content_hash([original_meta, original_invariants]),
            "after_hash": content_hash([meta, invariants]), "receipts": receipts}


def detector_insert_repair_candidate(chapter: Any, draft: Any) -> dict | None:
    """Propose a new edition from recorded producer evidence, never edit current text."""
    from bestseller.services.rewrite_patch import text_hash
    receipt = (chapter.metadata_json or {}).get("deterministic_hook_echo_bridge")
    if not isinstance(receipt, dict) or not isinstance(receipt.get("bridge"), str):
        return None
    bridge = receipt["bridge"].strip()
    if not bridge:
        return None
    body = draft.content_md or ""
    blocks = body.split("\n\n")
    slot = 1 if blocks and blocks[0].lstrip().startswith("#") else 0
    if len(blocks) <= slot or blocks[slot].strip() != bridge:
        return {"status":"needs_source_review", "parent_id":str(draft.id), "parent_hash":text_hash(body)}
    candidate = "\n\n".join(blocks[:slot] + blocks[slot+1:])
    if not candidate.strip():
        return None
    return {"status":"candidate_requires_review", "parent_id":str(draft.id),
            "parent_hash":text_hash(body),"candidate_hash":text_hash(candidate),
            "candidate_text":candidate,"producer_receipt":copy.deepcopy(receipt)}


async def migrate_detector_insertions(session, project: Any, *, apply: bool = False) -> list[dict]:
    from sqlalchemy import select, func
    from uuid import uuid5
    from bestseller.infra.db.models import ChapterModel, ChapterDraftVersionModel
    from bestseller.services.drafts import authoritative_word_count_for_language
    results=[]
    chapters = list(await session.scalars(select(ChapterModel).where(ChapterModel.project_id==project.id).with_for_update()))
    for chapter in chapters:
        if not (chapter.metadata_json or {}).get("deterministic_hook_echo_bridge"):
            continue
        parent = await session.scalar(select(ChapterDraftVersionModel).where(
            ChapterDraftVersionModel.chapter_id==chapter.id,ChapterDraftVersionModel.is_current.is_(True)
        ).order_by(ChapterDraftVersionModel.version_no.desc()).limit(1).with_for_update())
        if parent is None:
            continue
        proposal=detector_insert_repair_candidate(chapter,parent)
        if not proposal:
            continue
        # Legacy producers sometimes edited the body without allocating a new
        # version id. Bind the repair to its exact input, not that id alone.
        candidate_id = (
            uuid5(parent.id, "framework-detector-insert-repair.v2\n"
                  + proposal["parent_hash"] + "\n" + proposal["candidate_hash"])
            if proposal["status"] == "candidate_requires_review" else None
        )
        if apply:
            if proposal["status"]=="candidate_requires_review" and await session.get(ChapterDraftVersionModel,candidate_id) is None:
                latest=await session.scalar(select(func.max(ChapterDraftVersionModel.version_no)).where(ChapterDraftVersionModel.chapter_id==chapter.id))
                session.add(ChapterDraftVersionModel(id=candidate_id,project_id=project.id,chapter_id=chapter.id,
                    version_no=int(latest or 0)+1,content_md=proposal["candidate_text"],
                    word_count=authoritative_word_count_for_language(proposal["candidate_text"],language=project.language),
                    assembled_from_scene_draft_ids=list(parent.assembled_from_scene_draft_ids or []),
                    is_current=False,promotion_state="candidate",promotion_reason_codes=["historical_framework_insert_removed"]))
            record = {
                **{k:v for k,v in proposal.items() if k!="candidate_text"},
                "candidate_id": str(candidate_id) if candidate_id else None,
            }
            metadata = dict(chapter.metadata_json or {})
            prior = metadata.get("framework_injection_repair")
            if prior and prior != record:
                history = dict(metadata.get("framework_injection_repair_history") or {})
                history.setdefault(content_hash(prior), copy.deepcopy(prior))
                metadata["framework_injection_repair_history"] = history
            chapter.metadata_json = {**metadata, "framework_injection_repair": record}
        results.append({"chapter_id":str(chapter.id),"status":proposal["status"],
                        "candidate_id": str(candidate_id) if candidate_id else None})
    return results
