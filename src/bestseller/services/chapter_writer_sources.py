"""Chapter-wide source projection; reads never rewrite approved planning JSON."""
from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any

from bestseller.services.prompt_source_policy import content_hash


class ChapterParticipantScope:
    """All chapter actors, with the first scene's temporal cutoff.

    This is a read-only adapter, not an ORM scene clone. Later scenes cannot
    grant their as-yet unwritten knowledge to the beginning of the chapter.
    """
    def __init__(self, scenes: Sequence[Any]):
        if not scenes:
            raise ValueError("chapter_participant_scope_empty")
        self._first = scenes[0]
        self.participants = list(dict.fromkeys(
            name for scene in scenes for name in scene.participants
        ))

    def __getattr__(self, key: str) -> Any:
        return getattr(self._first, key)


def project_current_writer_boundaries(artifacts: Mapping[str, Any], participant_names: Sequence[str] = (), *, volume_number: int | None = None) -> dict[str, Any]:
    """Exact global World boundaries, not a shortened general Bible narrative.

    Characters' knowledge remains a separate temporal channel. World rules are
    already carried by story_state; do not duplicate their materialized text.
    """
    world = artifacts["world_spec"]
    content = world.content
    if not isinstance(content, Mapping):
        raise ValueError("writer_world_source_invalid")
    selected = {}
    if "forbidden_zones" in content:
        selected["/forbidden_zones"] = deepcopy(content["forbidden_zones"])
    power = content.get("power_system")
    if isinstance(power, Mapping) and "hard_limits" in power:
        selected["/power_system/hard_limits"] = deepcopy(power["hard_limits"])
    cast = artifacts.get("cast_spec")
    cast_projection = {}
    if cast is not None:
        raw = cast.content
        if not isinstance(raw, Mapping):
            raise ValueError("writer_cast_source_invalid")
        candidates = [raw.get("protagonist"), raw.get("antagonist"), *(raw.get("supporting_cast") or [])]
        rows = [{key: deepcopy(row[key]) for key in (
            "name", "role", "voice_profile",
        ) if key in row} for row in candidates
                if isinstance(row, Mapping) and row.get("name") in participant_names]
        cast_projection = {"source_artifact_id": str(cast.id),
                           "source_content_sha256": content_hash(raw),
                           "temporal_knowledge_not_asserted": True,
                           "withheld_fields": [{"name": row.get("name"), "path": field,
                               "value_sha256": content_hash(row[field]),
                               "reason": "foundation_cast_has_no_verified_temporal_scope"}
                               for row in candidates if isinstance(row, Mapping)
                               and row.get("name") in participant_names
                               for field in ("knowledge_state", "arc_state") if field in row],
                           "participants": rows,
                           "projection_sha256": content_hash(rows)}
    commitments = []
    book = artifacts.get("book_spec")
    if book is not None:
        engine = book.content.get("series_engine")
        fields = {"/series_engine/" + key: deepcopy(engine[key])
                  for key in ("first_three_chapter_hook", "golden_three_hooks")
                  if isinstance(engine, Mapping) and key in engine}
        commitments.append({"source_artifact_id": str(book.id),
            "source_content_sha256": content_hash(book.content), "fields": fields,
            "field_sha256": {path: content_hash(value) for path, value in fields.items()},
            "authority": "selected_planning_commitment_not_occurred_history"})
    volumes = artifacts.get("volume_plan")
    if volumes is not None and volume_number is not None:
        rows = volumes.content if isinstance(volumes.content, list) else volumes.content.get("volumes", [])
        matches = [(i, row) for i, row in enumerate(rows)
                   if isinstance(row, Mapping) and row.get("volume_number") == volume_number]
        if len(matches) != 1:
            raise ValueError("writer_current_volume_source_ambiguous")
        index, entry = matches[0]
        # Future action fields can contain constraints that apply now. Keep
        # the complete selected volume, rather than guessing sentence timing.
        commitments.append({"source_artifact_id": str(volumes.id),
            "source_content_sha256": content_hash(volumes.content),
            "source_pointer": ("" if isinstance(volumes.content, list) else "/volumes") + "/" + str(index),
            "value": deepcopy(entry), "value_sha256": content_hash(entry),
            "authority": "whole_volume_plan_not_current_facts_or_character_knowledge"})
    return {"selected_planning_commitments": commitments,
            "cast_projection": cast_projection, "schema": "chapter-writer-world-boundaries.v1",
            "source_artifact_id": str(world.id),
            "source_content_sha256": content_hash(content),
            "fields": selected,
            "field_sha256": {path: content_hash(value) for path, value in selected.items()}}


async def load_current_writer_boundaries(session, settings, *, project, participant_names=(), volume_number=None):
    from bestseller.services.foundation_selection import load_selected_foundation_artifacts
    selected = await load_selected_foundation_artifacts(session, project=project, settings=settings)
    return project_current_writer_boundaries(selected, participant_names, volume_number=volume_number) if selected else {}
