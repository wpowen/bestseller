"""Plan a bounded StoryEngine shadow window from real chapter outlines.

Only explicitly selected story-effect contracts are expanded.  The model may
propose events and alternatives; deterministic StoryEngine validation decides
whether the result is stored.  Outputs from this module are always shadow-only
and cannot enter writer context until separate canary evidence promotes them.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import json
import re
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from bestseller.domain.story_engine import (
    StoryEngineDefinition,
    StoryEngineMaturity,
    StoryEngineWindow,
    canonical_json_hash,
    story_engine_window_to_mapping,
    validate_engine_definition,
)
from bestseller.infra.db.models import PlanningArtifactVersionModel
from bestseller.services.llm import LLMCompletionRequest, complete_text
from bestseller.services.story_engine import (
    STORY_ENGINE_ARTIFACT_TYPE,
    build_story_engine_window_artifact_content,
    create_story_engine_window_artifact,
)
from bestseller.settings import AppSettings


class StoryEngineWindowPlanningError(ValueError):
    """Raised when a model proposal cannot become a valid shadow window."""


def _mapping(value: Any) -> Mapping[str, Any]:  # noqa: ANN401
    return value if isinstance(value, Mapping) else {}


def _outline_mapping(value: Any) -> dict[str, Any]:  # noqa: ANN401
    if isinstance(value, Mapping):
        return dict(value)
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        payload = model_dump(mode="json")
        if isinstance(payload, Mapping):
            return dict(payload)
    raise StoryEngineWindowPlanningError("chapter outline must be a mapping")


def _selected_effect_contract(outline: Mapping[str, Any]) -> dict[str, Any]:
    selected = _mapping(outline.get("selected_effect_skills"))
    compact = {
        key: selected[key]
        for key in ("primary", "secondary", "reason", "growth_stage_fit")
        if str(selected.get(key) or "").strip()
    }
    selected_keys = {
        str(compact.get(key) or "").strip()
        for key in ("primary", "secondary")
        if str(compact.get(key) or "").strip()
    }
    payload: dict[str, Any] = {"selected_effect_skills": compact}
    if "brainhole_engine" in selected_keys:
        brainhole = _mapping(outline.get("brainhole_contract"))
        if brainhole:
            payload["brainhole_contract"] = dict(brainhole)
    return payload


def _compact_outline(value: Any) -> dict[str, Any]:  # noqa: ANN401
    outline = _outline_mapping(value)
    payload = {
        "chapter_number": int(outline.get("chapter_number") or 0),
        "title": str(outline.get("title") or "").strip(),
        "chapter_goal": str(
            outline.get("chapter_goal") or outline.get("goal") or ""
        ).strip(),
        "opening_pressure": str(outline.get("opening_pressure") or "").strip(),
        "main_conflict": str(outline.get("main_conflict") or "").strip(),
        "chapter_concrete_actions": [
            str(item).strip()
            for item in (outline.get("chapter_concrete_actions") or [])
            if str(item).strip()
        ],
        "causal_contract": dict(_mapping(outline.get("causal_contract"))),
        **_selected_effect_contract(outline),
    }
    return payload


def _engine_payload(
    engine_artifact: PlanningArtifactVersionModel,
) -> tuple[StoryEngineDefinition, Mapping[str, Any], str]:
    if engine_artifact.artifact_type != STORY_ENGINE_ARTIFACT_TYPE:
        raise StoryEngineWindowPlanningError("source artifact is not StoryEngine V2")
    content = _mapping(engine_artifact.content)
    raw_engine = _mapping(content.get("engine"))
    if not raw_engine:
        raise StoryEngineWindowPlanningError("source engine artifact has no engine")
    meta = _mapping(content.get("_meta"))
    engine_hash = str(meta.get("engine_hash") or "").strip()
    if engine_hash != canonical_json_hash(raw_engine):
        raise StoryEngineWindowPlanningError("source engine artifact hash mismatch")
    try:
        engine = StoryEngineDefinition.from_mapping(raw_engine)
        validate_engine_definition(engine)
    except (TypeError, ValueError) as exc:
        raise StoryEngineWindowPlanningError("source engine artifact is invalid") from exc
    return engine, raw_engine, engine_hash


def build_story_engine_window_planner_prompt(
    *,
    engine_artifact: PlanningArtifactVersionModel,
    chapter_outlines: Sequence[Any],
) -> tuple[str, str, str]:
    """Build a compact prompt with at most two selected effect skills per chapter."""

    engine, raw_engine, engine_hash = _engine_payload(engine_artifact)
    compact_outlines = [_compact_outline(item) for item in chapter_outlines]
    chapter_numbers = [item["chapter_number"] for item in compact_outlines]
    if not compact_outlines or len(compact_outlines) > 10:
        raise StoryEngineWindowPlanningError("shadow window requires one to ten chapters")
    if chapter_numbers != list(
        range(chapter_numbers[0], chapter_numbers[0] + len(chapter_numbers))
    ):
        raise StoryEngineWindowPlanningError("shadow window chapters must be contiguous")
    source_payload = {
        "engine_artifact_id": str(engine_artifact.id),
        "engine_hash": engine_hash,
        "engine": {
            "engine_id": engine.engine_id,
            "version": engine.version,
            "initial_state": raw_engine.get("initial_state"),
            "reader_promise": engine.reader_promise,
            "change_vectors": list(engine.change_vectors),
            "engine_invariants": list(engine.engine_invariants),
        },
        "chapter_outlines": compact_outlines,
    }
    input_hash = canonical_json_hash(source_payload)
    system_prompt = (
        "You are the StoryEngine window planner. Propose causal chapter choices, not "
        "prose. Each chapter needs at least two feasible options with distinct reachable "
        "state hashes, one chosen path, a concrete opponent response or due obligation, "
        "and evidence-bearing state transitions. The first pre-state must equal the "
        "engine initial state; every later pre-state must equal the prior post-state. "
        "Use only the primary and secondary story-effect skills already selected for "
        "that chapter. Return one JSON object only."
    )
    user_prompt = (
        "Create a StoryEngineWindow JSON with keys window_id, engine_id, "
        "engine_version, engine_artifact_id, source_engine_hash, projections. "
        "Each projection needs chapter_number, choice_id, pre_state, known_facts, "
        "pressure, options, chosen_option_id, chosen_path, alternative_costs, "
        "opponent_strategy, due_obligations, required_state_changes, "
        "expected_post_state_hash, fingerprint. Do not output future prose or a full "
        "branch tree.\n\nSOURCE:\n"
        + json.dumps(source_payload, ensure_ascii=False, sort_keys=True)
    )
    return system_prompt, user_prompt, input_hash


def _parse_json_object(raw: str) -> Mapping[str, Any]:
    text = str(raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
        text = re.sub(r"\s*```$", "", text)
    try:
        payload = json.loads(text)
    except (TypeError, ValueError):
        return {}
    return payload if isinstance(payload, Mapping) else {}


async def generate_story_engine_shadow_window(
    session: AsyncSession,
    settings: AppSettings,
    *,
    project_id: UUID,
    engine_artifact: PlanningArtifactVersionModel,
    chapter_outlines: Sequence[Any],
    workflow_run_id: UUID,
) -> PlanningArtifactVersionModel:
    """Generate and persist an LLM-proposed window only after deterministic validation."""

    engine, _, engine_hash = _engine_payload(engine_artifact)
    system_prompt, user_prompt, input_hash = build_story_engine_window_planner_prompt(
        engine_artifact=engine_artifact,
        chapter_outlines=chapter_outlines,
    )
    completion = await complete_text(
        session,
        settings,
        LLMCompletionRequest(
            logical_role="planner",
            model_tier="strong",
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            fallback_response="{}",
            prompt_template="story_engine_shadow_window",
            prompt_version="2.0",
            project_id=project_id,
            workflow_run_id=workflow_run_id,
            metadata={
                "engine_artifact_id": str(engine_artifact.id),
                "engine_hash": engine_hash,
                "source_outline_hash": input_hash,
                "chapter_count": len(chapter_outlines),
                "writer_authority": False,
            },
        ),
    )
    raw_window = _parse_json_object(completion.content)
    if "window" in raw_window and isinstance(raw_window.get("window"), Mapping):
        raw_window = _mapping(raw_window.get("window"))
    try:
        window = StoryEngineWindow.from_mapping(raw_window)
    except (TypeError, ValueError) as exc:
        raise StoryEngineWindowPlanningError(
            "model proposal is not a valid StoryEngine window"
        ) from exc

    requested_chapters = [
        int(_outline_mapping(item).get("chapter_number") or 0)
        for item in chapter_outlines
    ]
    actual_chapters = [
        item.chapter_number for item in window.projections  # type: ignore[union-attr]
    ]
    if actual_chapters != requested_chapters:
        raise StoryEngineWindowPlanningError("model window chapter scope mismatch")
    if (
        window.engine_id != engine.engine_id
        or window.engine_version != engine.version
        or window.engine_artifact_id != str(engine_artifact.id)
        or window.source_engine_hash != engine_hash
    ):
        raise StoryEngineWindowPlanningError("model window engine lineage mismatch")
    first_pre_state = story_engine_window_to_mapping(window)["projections"][0][
        "pre_state"
    ]
    expected_initial_state = _mapping(engine_artifact.content).get("engine", {})
    expected_initial_state = _mapping(expected_initial_state).get("initial_state", {})
    if canonical_json_hash(first_pre_state) != canonical_json_hash(
        expected_initial_state
    ):
        raise StoryEngineWindowPlanningError("model window does not start at engine state")

    content = build_story_engine_window_artifact_content(
        window,
        maturity=StoryEngineMaturity.SHADOW_VALIDATED,
        can_drive_generation=False,
    )
    content["_meta"] = {
        **dict(_mapping(content.get("_meta"))),
        "source_outline_hash": input_hash,
        "llm_run_id": str(completion.llm_run_id) if completion.llm_run_id else None,
        "workflow_run_id": str(workflow_run_id),
        "writer_authority": False,
    }
    idempotency_key = "story-engine-shadow-window:" + input_hash
    return await create_story_engine_window_artifact(
        session,
        project_id=project_id,
        content=content,
        idempotency_key=idempotency_key,
        source_run_id=workflow_run_id,
        notes="dual-write shadow window; not authorized for writer context",
        created_by="story_engine_window_planner",
    )


__all__ = [
    "StoryEngineWindowPlanningError",
    "build_story_engine_window_planner_prompt",
    "generate_story_engine_shadow_window",
]
