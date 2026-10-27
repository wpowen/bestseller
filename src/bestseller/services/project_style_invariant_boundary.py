"""Preserve a fresh project's prose rule while canonicalizing its technical tense.

Only literal, unambiguous aliases are supported.  This runs before planning,
never migrates an existing invariant contract or touches paid reviews/budgets.
The frozen invariant initializer continues to validate its canonical inputs.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import or_, select

from bestseller.infra.db.models import (
    BookProductionControlModel as Control,
)
from bestseller.infra.db.models import (
    ChapterModel as Chapter,
)
from bestseller.infra.db.models import (
    ProjectModel as Project,
)
from bestseller.infra.db.models import (
    QualityScoreModel as Quality,
)
from bestseller.infra.db.models import (
    ReviewReportModel as Review,
)
from bestseller.infra.db.models import (
    StyleGuideModel as Style,
)
from bestseller.infra.db.models import (
    WorkflowRunModel as Workflow,
)
from bestseller.services.prompt_source_policy import content_hash

SCHEMA = "creation-style-invariant-boundary.v1"
_ACTIVE = ("running", "pending", "queued", "stopping")
_ALIASES = {
    "past": "past",
    "past tense": "past",
    "present": "present",
    "present tense": "present",
    "过去时": "past",
    "过去式": "past",
    "现在时": "present",
    "现在式": "present",
    "过去时叙述": "past",
    "现在时叙述": "present",
    # Exact retained native creation rule; no arbitrary suffix classification.
    "过去时叙述，场景内保持当下感。": "past",
}


def canonical_creation_tense(value):
    """Only finite literal aliases; unknown prose never supplies a default."""
    return _ALIASES.get(value.strip().casefold()) if isinstance(value, str) else None


def _profile_tense_bindings(metadata, canonical):
    if not isinstance(metadata, dict):
        raise ValueError("creation_style_boundary_metadata_unverified")
    bindings = {}
    for key in ("writing_profile", "writing_profile_overrides"):
        if key not in metadata:
            continue
        profile = metadata[key]
        if not isinstance(profile, dict):
            raise ValueError("creation_style_boundary_profile_unverified")
        if "style" not in profile:
            if key == "writing_profile":
                raise ValueError("creation_style_boundary_profile_tense_missing")
            continue
        style = profile["style"]
        if not isinstance(style, dict):
            raise ValueError("creation_style_boundary_profile_style_unverified")
        if "tense" in style:
            if canonical_creation_tense(style["tense"]) != canonical:
                raise ValueError("creation_style_boundary_profile_tense_conflict")
            bindings[key] = content_hash(style["tense"])
        elif key == "writing_profile":
            raise ValueError("creation_style_boundary_profile_tense_missing")
    return bindings


async def prepare_creation_style_invariant_boundary(session, *, project_id, requested_by="system"):
    """Caller commits the style projection and append-only workflow receipt."""
    for obj in (*session.new, *session.dirty, *session.deleted):
        if (
            isinstance(obj, (Project, Style))
            and getattr(obj, "project_id", getattr(obj, "id", None)) == project_id
        ):
            raise ValueError("creation_style_boundary_uncommitted_source")
    unchanged = {
        "schema": SCHEMA,
        "project_id": str(project_id),
        "changed": False,
        "provider_calls": 0,
        "body_gate_pass_asserted": False,
        "credit_released": False,
    }
    with session.no_autoflush:
        # The ordinary existing/canonical path acquires no row locks and writes
        # nothing. Only an actual projection enters the control/source lock order.
        project = await session.scalar(
            select(Project)
            .where(Project.id == project_id)
            .execution_options(populate_existing=True)
        )
        if project is None:
            raise ValueError("creation_style_boundary_project_missing")
        if project.invariants_json is not None:
            return {**unchanged, "status": "existing_contract_preserved"}
        style = await session.scalar(
            select(Style)
            .where(Style.project_id == project_id)
            .execution_options(populate_existing=True)
        )
        if style is None or style.tense in {"past", "present"}:
            return {**unchanged, "status": "canonical_or_missing_style"}
        observed = content_hash(
            {
                "status": project.status,
                "metadata": project.metadata_json,
                "pov_type": style.pov_type,
                "tense": style.tense,
                "custom_rules": style.custom_rules,
            }
        )
        control = await session.scalar(
            select(Control)
            .where(Control.project_id == project_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        project = await session.scalar(
            select(Project)
            .where(Project.id == project_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        # A first STOP can be inserted while waiting for a legacy Project row.
        control = await session.scalar(
            select(Control)
            .where(Control.project_id == project_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        style = await session.scalar(
            select(Style)
            .where(Style.project_id == project_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if (
            project is None
            or style is None
            or project.invariants_json is not None
            or observed
            != content_hash(
                {
                    "status": project.status,
                    "metadata": project.metadata_json,
                    "pov_type": style.pov_type,
                    "tense": style.tense,
                    "custom_rules": style.custom_rules,
                }
            )
        ):
            raise ValueError("creation_style_boundary_concurrent_source_change")
        canonical = canonical_creation_tense(style.tense)
        if canonical is None:
            raise ValueError("creation_style_boundary_ambiguous_tense")
        profile_bindings = _profile_tense_bindings(project.metadata_json, canonical)
        if control is not None and control.desired_state != "run":
            raise ValueError("creation_style_boundary_control_not_run")
        if project.status != "planning":
            raise ValueError("creation_style_boundary_not_fresh_planning")
        if (
            await session.scalar(
                select(Chapter.id).where(Chapter.project_id == project_id).limit(1)
            )
            is not None
        ):
            raise ValueError("creation_style_boundary_existing_chapters")
        if (
            await session.scalar(select(Review.id).where(Review.project_id == project_id).limit(1))
            is not None
        ):
            raise ValueError("creation_style_boundary_existing_reviews")
        if (
            await session.scalar(
                select(Quality.id).where(Quality.project_id == project_id).limit(1)
            )
            is not None
        ):
            raise ValueError("creation_style_boundary_existing_quality")
        if (
            await session.scalar(
                select(Workflow.id)
                .where(
                    or_(
                        Workflow.project_id == project_id,
                        (Workflow.scope_type == "project") & (Workflow.scope_id == project_id),
                    ),
                    Workflow.status.in_(_ACTIVE),
                )
                .limit(1)
            )
            is not None
        ):
            raise ValueError("creation_style_boundary_active_workflow")
        if not isinstance(style.custom_rules, list) or not all(
            isinstance(rule, str) for rule in style.custom_rules
        ):
            raise ValueError("creation_style_boundary_rules_unverified")
    before = {
        "pov_type": style.pov_type,
        "tense": style.tense,
        "custom_rules": deepcopy(style.custom_rules),
    }
    raw = style.tense
    receipt = {
        **unchanged,
        "status": "literal_tense_alias_normalized",
        "changed": True,
        "before": before,
        "before_sha256": content_hash(before),
        "canonical_tense": canonical,
        "retained_original_rule": raw,
        "project_metadata_sha256": content_hash(project.metadata_json),
        "profile_tense_source_sha256": profile_bindings,
        "requested_by": requested_by,
        "author_text_removed": False,
        "producer_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    audit_id = uuid5(NAMESPACE_URL, SCHEMA + ":" + str(project_id) + ":" + content_hash(before))
    if await session.get(Workflow, audit_id) is not None:
        raise ValueError("creation_style_boundary_original_projection_replayed")
    style.tense = canonical
    style.custom_rules = (
        [*style.custom_rules, raw] if raw not in style.custom_rules else list(style.custom_rules)
    )
    after = {
        "pov_type": style.pov_type,
        "tense": style.tense,
        "custom_rules": deepcopy(style.custom_rules),
    }
    receipt.update(after=after, after_sha256=content_hash(after), audit_id=str(audit_id))
    session.add(
        Workflow(
            id=audit_id,
            project_id=project_id,
            workflow_type="project_style_invariant_boundary",
            scope_type="project",
            scope_id=project_id,
            status="completed",
            current_step="original_style_rule_retained",
            requested_by=requested_by,
            metadata_json={"normalization": receipt},
        )
    )
    await session.flush()
    return receipt
