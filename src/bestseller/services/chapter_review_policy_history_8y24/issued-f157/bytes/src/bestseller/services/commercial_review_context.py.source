"""Read-only adopted history and semantic context selection for commercial review."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
import hashlib
import json
from typing import Any

# ruff: noqa: ANN401


def _hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
        ).encode()
    ).hexdigest()


async def load_commercial_review_history(
    session: Any, *, project_id: Any, chapter_number: int, allow_draft_references: bool = False
) -> dict:
    """Reuse the adoption guard; never substitute a current candidate for history."""
    from bestseller.services.planning_continuity_context import build_planning_continuity_context

    if chapter_number == 1:
        return {}
    context = await build_planning_continuity_context(
        session,
        project_id=project_id,
        before_chapter=chapter_number,
        allow_draft_references=allow_draft_references,
    )
    if (
        context.get("project_id") != str(project_id)
        or context.get("before_chapter") != chapter_number
    ):
        raise ValueError("commercial_history_scope_mismatch")
    adopted = context.get("adopted_sources") or []
    references = sorted([*adopted,*(context.get('qualified_pending_sources') or [])],key=lambda item:item['chapter_number'])
    if [item.get("chapter_number") for item in references] != list(range(1, chapter_number)):
        raise ValueError("commercial_history_adoption_gap")
    proof = {item["chapter_number"]: item for item in references}
    bodies = sorted([*(context.get("recent_adopted_prose") or []),*(context.get('recent_qualified_pending_prose') or [])],key=lambda item:item['chapter_number'])
    if not bodies or bodies[-1].get("chapter_number") != chapter_number - 1:
        raise ValueError("commercial_immediate_predecessor_missing")
    seen = set()
    for item in bodies:
        number, body = item.get("chapter_number"), item.get("body")
        expected = proof.get(number)
        if (
            number in seen
            or not expected
            or not isinstance(body, str)
            or not body.strip()
            or any(
                item.get(key) != expected.get(key)
                for key in ("chapter_id", "draft_id", "body_sha256", "adoption_state", "reference_state", "qualification_receipt_id", "formal_adoption", "canon_authority")
            )
            or hashlib.sha256(body.encode()).hexdigest() != expected["body_sha256"]
        ):
            raise ValueError("commercial_adopted_body_proof_mismatch")
        seen.add(number)
    if context.get('qualified_pending_sources'):
        context['recent_reference_prose']=bodies
    return deepcopy(context)


async def load_authorized_commercial_history(session: Any, *, project_id: Any, chapter_number: int) -> dict:
    if chapter_number==1:return {}
    from bestseller.services.draft_continuity import draft_mode_enabled
    return await load_commercial_review_history(session,project_id=project_id,chapter_number=chapter_number,
        allow_draft_references=await draft_mode_enabled(session,project_id=project_id))


def select_commercial_bible(
    bible: Mapping[str, Any],
    *,
    participant_names: Sequence[str],
    prose: str,
    chapter_number: int | None,
) -> dict[str, Any]:
    """Select typed current state, never a cast's future development recipes.

    World constraints stay complete. Character state is a reference to be checked
    against adopted prose; selection does not upgrade planner labels to facts.
    """
    selected = {
        key: deepcopy(bible[key])
        for key in (
            "world_rules",
            "worldview",
            "worldview_kernel",
            "power_system",
            "power_systems",
            "rules",
            "rule",
            "glossary",
            "terminology",
            "rule_terms",
            "specialist_terms",
            # These upstream typed rosters/relationships carry scoped state,
            # not cast design recipes. Keep complete records and their scope.
            "relationships",
            "deceased_characters",
            "protected_characters",
            "restricted_characters",
        )
        if key in bible
    }
    state_fields = (
        "character_id",
        "id",
        "name",
        "aliases",
        "role",
        "arc_state",
        "power_tier",
        "knowledge_state",
        "latest_state",
        "emotional_state",
        "physical_state",
        "alive_status",
        "stance",
        "stance_locked_until_chapter",
        "death_chapter_number",
        "latest_chapter_number",
        "latest_scene_number",
        "previous_arc_state",
        "previous_power_tier",
        "previous_emotional_state",
        "previous_alive_status",
        "previous_stance",
        "previous_state_chapter_number",
        "source_ref",
        "source_refs",
        "source_lineage",
        "temporal_status",
        "temporal_scope",
    )
    wanted = set(participant_names)
    for key in ("participants", "characters"):
        records = bible.get(key)
        if not isinstance(records, (list, tuple)):
            continue
        retained = []
        for record in records:
            if not isinstance(record, Mapping):
                continue
            name = record.get("name")
            aliases = record.get("aliases") or []
            labels = [name, *(aliases if isinstance(aliases, (list, tuple)) else [aliases])]
            if not any(
                isinstance(label, str) and label and (label in wanted or label in prose)
                for label in labels
            ):
                continue
            observed = record.get("latest_chapter_number")
            if type(observed) is int and chapter_number is not None and observed >= chapter_number:
                continue  # Do not describe a current/future candidate snapshot as prior state.
            retained.append(
                {field: deepcopy(record[field]) for field in state_fields if field in record}
            )
        if retained:
            selected[key] = retained
    return selected


def project_commercial_source(
    source: Mapping[str, Any] | None,
    *,
    chapter_number: int | None = None,
    content_md: str = "",
    previous_chapters: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Retain complete source channels; exclude typed planning packages with receipts."""
    source = source or {}
    channels = (source.get("acceptance_contract") or {}).get("source_authority_channels") or {}
    chapter = source.get("chapter") or {}
    participants = [
        name
        for scene in source.get("scenes", [])
        if isinstance(scene, Mapping)
        for name in scene.get("participants", [])
        if isinstance(name, str)
    ]
    prose = "\n".join(
        [
            content_md,
            *(str(item.get("body") or item.get("content_md") or "") for item in previous_chapters),
        ]
    )
    bible = source.get("story_bible") or {}
    selected_bible = select_commercial_bible(
        bible,
        participant_names=participants,
        prose=prose,
        chapter_number=chapter_number,
    )
    projected = {
        "schema_version": "commercial-reader-source-input.v2",
        "project": {
            key: value
            for key, value in (source.get("project") or {}).items()
            if key in {"id", "slug", "title", "genre", "sub_genre", "language"}
        },
        "chapter": {key: chapter.get(key) for key in ("chapter_number", "title")},
        "approved_creation_contracts": deepcopy(source.get("approved_creation_contracts") or {}),
        "explicit_creator_requirements": deepcopy(
            channels.get("explicit_creator_requirements")
            or source.get("explicit_creator_requirements")
            or {}
        ),
        "established_facts": deepcopy(
            channels.get("established_facts") or source.get("established_facts") or {}
        ),
        "prior_prose_reference": deepcopy(
            channels.get("prior_prose_reference") or source.get("prior_prose_reference") or {}
        ),
        "story_bible": selected_bible,
        "quality_targets": {
            "target_word_count": (source.get("quality_targets") or {}).get("target_word_count")
        },
        **{
            key: deepcopy(source[key])
            for key in (
                "approved_effect_contracts",
                "approved_chapter_contract",
                "approved_scene_contract",
                "approved_world_authority",
                "identity_authority",
                "adopted_continuity",
                "draft_manuscript_continuity",
            )
            if key in source
        },
        "source_selection_receipt": {
            "policy": "typed-world-and-relevant-current-state.v1",
            "original_source_sha256": _hash(source),
            "original_bible_sha256": _hash(bible),
            "retained_bible_sha256": _hash(selected_bible),
            "original_bible_characters": len(json.dumps(bible, ensure_ascii=False, default=str)),
            "retained_bible_characters": len(
                json.dumps(selected_bible, ensure_ascii=False, default=str)
            ),
            "retained_bible_fields": list(selected_bible),
            "omitted_bible_fields": [key for key in bible if key not in selected_bible],
            "authority": "unverified_context; adopted facts and explicit clauses remain separate",
        },
    }
    return projected
