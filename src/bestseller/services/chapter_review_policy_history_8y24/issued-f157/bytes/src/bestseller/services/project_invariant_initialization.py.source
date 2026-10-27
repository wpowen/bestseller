"""Missing-only initialization; callers own audit persistence and commit.

Never reseed an existing contract, migrate creative state, or claim a body gate
passed. The returned receipt belongs in workflow audit, not a story prompt.
"""
from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from sqlalchemy import JSON, or_, select, update

from bestseller.infra.db.models import ChapterModel, ProjectModel, StyleGuideModel, WorkflowRunModel
from bestseller.services.content_origin_policy import origin_record
from bestseller.services.invariants import (
    InvariantSeedError, invariants_from_dict, invariants_to_dict, seed_invariants,
)
from bestseller.settings import apply_runtime_llm_profile, llm_runtime_profile_path

POLICY = "missing-project-invariants.v1"
_CONTENT_PATHS = ("opening_archetype_pool", "cliffhanger_policy", "hype_scheme")
_POV = {"first": "first", "first-person": "first", "first_person": "first",
        "close_third": "close_third", "third-limited": "close_third", "third_limited": "close_third",
        "omniscient": "omniscient", "third-omniscient": "omniscient"}


def _sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


async def ensure_missing_project_invariants(session, settings, *, project_id, workflow_id):
    """Initialize SQL/JSON null only using the supplied effective configuration.

    Returns status initialized/existing_valid/existing_invalid, changed and an
    audit receipt. Invalid existing values (including {}) are not overwritten.
    Missing source/scope, unsupported authored styles, or a concurrent change
    raise before seeding/overwrite. No commit, provider or quality mutation.
    Caller must persist the receipt and commit before rebuilding body evidence.
    """
    for obj in (*session.dirty, *session.new, *session.deleted):
        if (isinstance(obj, ProjectModel) and obj.id == project_id) or (
                isinstance(obj, StyleGuideModel) and obj.project_id == project_id):
            raise ValueError("invariant_initialization_uncommitted_project_or_style")
    with session.no_autoflush:
        project = await session.scalar(select(ProjectModel).where(ProjectModel.id == project_id)
            .execution_options(populate_existing=True).with_for_update())
        workflow = (await session.execute(select(WorkflowRunModel.project_id,
            WorkflowRunModel.workflow_type, WorkflowRunModel.scope_type, WorkflowRunModel.scope_id,
        ).where(WorkflowRunModel.id == workflow_id))).one_or_none()
    if project is None or workflow is None or workflow.project_id != project_id:
        raise ValueError("invariant_initialization_project_workflow_mismatch")
    if workflow.scope_type == "chapter":
        with session.no_autoflush:
            chapter_id = await session.scalar(select(ChapterModel.id).where(
                ChapterModel.id == workflow.scope_id, ChapterModel.project_id == project_id))
        if chapter_id is None:
            raise ValueError("invariant_initialization_chapter_scope_mismatch")
    elif workflow.scope_type != "project" or workflow.scope_id != project_id:
        raise ValueError("invariant_initialization_scope_unverified")

    original = deepcopy(project.invariants_json)
    receipt = {"schema": POLICY, "project_id": str(project_id), "workflow_id": str(workflow_id),
        "workflow_type": workflow.workflow_type, "scope_type": workflow.scope_type,
        "scope_id": str(workflow.scope_id), "changed": False, "original_sha256": _sha(original),
        "producer_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "body_gate_pass_asserted": False, "old_quality_modified": False,
        "author_content_authority_granted": False}
    if original is not None:
        try:
            if not isinstance(original, Mapping):
                raise InvariantSeedError("existing_contract_not_mapping")
            parsed = invariants_from_dict(original)
            if parsed.project_id != project_id or parsed.language not in {"en", "zh-CN"}:
                raise InvariantSeedError("existing_contract_project_or_language_mismatch")
        except (InvariantSeedError, KeyError, TypeError, ValueError) as exc:
            receipt.update(status="existing_invalid", reason=type(exc).__name__)
        else:
            receipt["status"] = "existing_valid"
        return receipt

    metadata = deepcopy(project.metadata_json)
    if not isinstance(metadata, dict):
        raise ValueError("invariant_initialization_project_metadata_unverified")
    if metadata.get("adopted_effect_configuration"):
        raise ValueError("adopted_effect_configuration_requires_explicit_invariant_migration")
    origins = metadata.get("invariant_content_origins") or {}
    if not isinstance(origins, dict) or any(key in origins for key in _CONTENT_PATHS):
        raise ValueError("missing_invariants_with_existing_content_origins_requires_review")
    with session.no_autoflush:
        style = await session.scalar(select(StyleGuideModel).where(StyleGuideModel.project_id == project_id)
            .execution_options(populate_existing=True).with_for_update())
    creation = metadata.get("creation_intent_contract") or {}
    sources = (creation.get("field_sources") or {}) if isinstance(creation, Mapping) else {}
    if not isinstance(sources, Mapping):
        raise ValueError("invariant_initialization_creation_sources_malformed")
    has_explicit_pov = sources.get("pov") == "explicit"
    raw_pov = creation.get("pov") if has_explicit_pov else (
        style.pov_type if style is not None else settings.generation.pov)
    pov = _POV.get(raw_pov)
    raw_tense = style.tense if style is not None else "past"
    if pov is None or raw_tense not in {"past", "present"}:
        raise ValueError("invariant_initialization_unsupported_existing_style")
    if project.language not in {"en", "zh-CN"}:
        raise ValueError("invariant_initialization_unsupported_project_language")
    # Runtime profile influences models, not author content. Retain only hashes
    # in this audit receipt; never copy providers/names into story constraints.
    profile_path = llm_runtime_profile_path(settings)
    profile_before = profile_path.read_bytes() if profile_path.exists() else None
    effective = apply_runtime_llm_profile(settings)
    profile_after = profile_path.read_bytes() if profile_path.exists() else None
    if profile_before != profile_after:
        raise ValueError("invariant_initialization_runtime_profile_changed")
    inputs = {"language": project.language,
        "words_per_chapter": effective.generation.words_per_chapter.model_dump(mode="json"),
        "pov": pov, "pov_source": "canonical_explicit" if has_explicit_pov else (
            "persisted_style_guide" if style is not None else "supplied_generation_settings"),
        "tense": raw_tense, "tense_source": "persisted_style_guide" if style is not None else "seed_default",
        "runtime_profile_sha256": hashlib.sha256(profile_before).hexdigest() if profile_before is not None else None,
        "effective_llm_configuration_sha256": _sha(effective.llm.model_dump(mode="json"))}
    inv = seed_invariants(project_id=project_id, language=project.language,
        words_per_chapter=effective.generation.words_per_chapter, pov=pov, tense=raw_tense)
    payload = invariants_to_dict(inv)
    invariants_from_dict(payload)
    metadata["invariant_content_origins"] = {**origins, **{
        key: origin_record(payload[key], kind="framework_default", adopted=False, producer=POLICY)
        for key in _CONTENT_PATHS}}
    # FOR UPDATE is effective on PG; the null + metadata predicates also stop a
    # stale SQLite/no-lock caller from overwriting an independently filled row.
    with session.no_autoflush:
        written = await session.execute(update(ProjectModel).where(ProjectModel.id == project_id,
            or_(ProjectModel.invariants_json.is_(None), ProjectModel.invariants_json == JSON.NULL),
            ProjectModel.metadata_json == project.metadata_json,
        ).values(invariants_json=payload, metadata_json=metadata).execution_options(synchronize_session=False))
    if written.rowcount != 1:
        raise ValueError("invariant_initialization_concurrent_source_change")
    await session.refresh(project, ["invariants_json", "metadata_json"])
    receipt.update(status="initialized", changed=True, invariants_sha256=_sha(payload),
        inputs=inputs, inputs_sha256=_sha(inputs), requires_current_body_gate_recheck=True,
        requires_review_freshness_rebinding=True,
        paid_request_equivalence_asserted=False)
    return receipt
