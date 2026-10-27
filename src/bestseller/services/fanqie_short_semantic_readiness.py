"""One read-only semantic seam for native publication and package readback.

No model dispatch, stored approval flags, staging imports or artifact-selected
code. Missing current artifacts retain the original heuristic behavior.
"""
from __future__ import annotations

import json

from sqlalchemy import select

from bestseller.infra.db.models import PlanningArtifactVersionModel, ProjectModel

ARTIFACT_TYPE = "fanqie_whole_semantic_review"


def _equal(left, right):
    def canonical(value):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return canonical(left) == canonical(right)


def _dependencies():
    # These are reviewed code dependencies, never selected by stored JSON.
    # If integration is incomplete, a present artifact cannot grant permission.
    from bestseller.services import fanqie_short_semantic_adapter as adapter
    from bestseller.services import fanqie_short_semantic_consumer as consumer
    from bestseller.services import fanqie_short_semantic_inputs as factory
    from bestseller.services import fanqie_short_semantic_review as semantic
    return adapter, consumer, factory, semantic


async def _fresh_inputs(session, project, assembly, source_run_id, *, settings):
    _, _, factory, _ = _dependencies()
    if settings is None:
        raise ValueError("fanqie_semantic_project_settings_required")
    from bestseller.services.fanqie_short_title_evidence import load_native_title_evidence
    title_evidence = await load_native_title_evidence(session, settings, project=project)
    # The operator and consumer MUST call the same factory with the same native
    # evidence. Do not merge/alias/normalize its publication manifest afterward.
    inputs = await factory.build_current_inputs(
        session, project=project, assembly=assembly, workflow_run_id=source_run_id,
        native_title_evidence=title_evidence,
    )
    if title_evidence is not None and not _equal(inputs["manifest"], assembly.manifest):
        raise ValueError("fanqie_semantic_publication_manifest_mismatch")
    return inputs


async def load_upload_semantic_context(session, *, project, assembly, original_report, settings=None):
    """Read authoritative rows/files afresh, or return None for absent artifacts.

    Only a provably different historical body/title can be ignored. Malformed,
    ambiguous or invalid current evidence fails closed through the same adapter.
    """
    from bestseller.services import fanqie_short_semantic_selection_adapter as selection_adapter
    selection_present = await selection_adapter.current_selection_presence(session, project=project, assembly=assembly)
    with session.no_autoflush:
        for row in (*session.new, *session.dirty):
            if (isinstance(row, PlanningArtifactVersionModel) and row.project_id == project.id
                    and row.artifact_type == ARTIFACT_TYPE):
                raise ValueError("fanqie_semantic_pending_artifact")
        rows = (await session.execute(select(PlanningArtifactVersionModel.id, PlanningArtifactVersionModel.content).where(
            PlanningArtifactVersionModel.project_id == project.id,
            PlanningArtifactVersionModel.artifact_type == ARTIFACT_TYPE,
        ))).all()
    relevant = False
    for _identity, content in rows:
        payload = content.get("request_payload") if isinstance(content, dict) else None
        if not isinstance(payload, dict) or not isinstance(payload.get("full_text"), str) or not isinstance(payload.get("title"), str):
            raise ValueError("fanqie_semantic_artifact_unclassifiable")
        if payload["full_text"] == assembly.full_text and payload["title"] == project.title:
            relevant = True
    if not relevant and not selection_present:
        return None
    if settings is None:
        from bestseller.services.creation_configuration import project_settings
        from bestseller.settings import apply_runtime_llm_profile, load_settings
        expected_title = project.title
        with session.no_autoflush:
            if any(isinstance(row, ProjectModel) and row.id == project.id
                   for row in (*session.new, *session.dirty)):
                raise ValueError("fanqie_semantic_pending_project_settings")
            fresh_project = await session.scalar(select(ProjectModel).where(
                ProjectModel.id == project.id).execution_options(populate_existing=True))
        if fresh_project is None or fresh_project.title != expected_title:
            raise ValueError("fanqie_semantic_settings_project_changed")
        # Legacy delivery readers have no settings parameter. Resolve current
        # project-scoped paths afresh, never a cached global settings object.
        settings = project_settings(apply_runtime_llm_profile(load_settings()), fresh_project)
    adapter, consumer, _, semantic = _dependencies()
    async def current_inputs_factory(session, fresh_project, fresh_assembly, source_run_id):
        return await _fresh_inputs(
            session, fresh_project, fresh_assembly, source_run_id, settings=settings,
        )
    if selection_present:
        from bestseller.services import fanqie_short_selection_readiness as selection_readiness
        return await selection_readiness.load_current_selection_context(
            session, settings, project=project, assembly=assembly, original_report=original_report,
            current_inputs_factory=current_inputs_factory,
        )
    loaded = await adapter.load_current_semantic_context(
        session, settings, project=project, assembly=assembly, title=project.title,
        original_report=original_report, current_inputs_factory=current_inputs_factory,
        semantic=semantic, consumer=consumer,
    )
    return loaded["semantic_context"]


def semantic_readiness_view(semantic_context, *, full_text, title, unlock_line_ratio, protagonist_name):
    """Require fresh evidence; never accept a stored derived verdict.

    A native selection loader may seal its already computed proof for this
    exact unchanged in-memory object. New loads always revalidate native rows.
    """
    if semantic_context is None:
        return None
    _, consumer, _, semantic = _dependencies()
    inputs = semantic_context.current_inputs
    if (inputs.get("full_text") != full_text or inputs.get("title") != title
            or not _equal(inputs.get("unlock_line_ratio"), unlock_line_ratio)
            or inputs.get("protagonist") != protagonist_name):
        raise ValueError("fanqie_semantic_readiness_input_mismatch")
    from bestseller.services.fanqie_short_quality import review_whole_fanqie_short_story
    report = review_whole_fanqie_short_story(
        full_text, title=title, unlock_line_ratio=unlock_line_ratio,
        protagonist_name=protagonist_name,
    ).to_dict()
    if not _equal(inputs.get("original_report"), report):
        raise ValueError("fanqie_semantic_current_heuristic_mismatch")
    from bestseller.services.fanqie_short_semantic_selection_adapter import SelectionContext
    if isinstance(semantic_context, SelectionContext):
        from bestseller.services.fanqie_short_selection_readiness import selection_readiness_view
        view = selection_readiness_view(semantic_context)
    else:
        view = consumer.semantic_readiness_gates(
            current_inputs=inputs, evidence=semantic_context.evidence, semantic=semantic,
        )
    derived = view["semantic_derived_review"]
    if (derived["adjudication_trace"]["status"] != "verified"
            or not _equal(derived["original_report"], report)):
        raise ValueError("fanqie_semantic_readiness_unverified")
    return view
