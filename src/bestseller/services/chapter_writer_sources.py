"""Chapter-wide source projection; reads never rewrite approved planning JSON."""
from collections.abc import Mapping, Sequence
from copy import deepcopy
import json
import re
from typing import TYPE_CHECKING, Any
from uuid import UUID

from bestseller.services.prompt_source_policy import content_hash

if TYPE_CHECKING:
    from bestseller.infra.db.models import ChapterModel, ProjectModel
    from bestseller.services.prompt_compiler import PromptBlock


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


def _reader_first_json_pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in items:
        if key in result:
            raise ValueError("reader_first_delivery_duplicate_json_key")
        result[key] = value
    return result


def _reader_first_json_constant(value: str) -> None:
    raise ValueError("reader_first_delivery_nonfinite_json:" + value)


def _reader_first_delivery_census(
    source: dict[str, Any],
    *,
    target_chapters: int,
    binding: dict[str, Any],
) -> None:
    """Recheck every authored chapter and scene, including nonselected chapters."""
    from bestseller.services.chapter_writer_delivery import _reader_first_field_roles
    from bestseller.services.source_bound_planning_content import reader_first_binding

    if reader_first_binding(source) != binding:
        raise ValueError("reader_first_delivery_whole_source_binding_changed")
    chapters = source["outline"]["chapters"]
    if not isinstance(chapters, list) or len(chapters) != target_chapters:
        raise ValueError("reader_first_delivery_complete_chapter_range_required")
    chapter_numbers = []
    for row in chapters:
        if not isinstance(row, dict) or type(row.get("chapter_number")) is not int:
            raise ValueError("reader_first_delivery_chapter_number_invalid")
        chapter_numbers.append(row["chapter_number"])
        scenes = row.get("scenes")
        if not isinstance(scenes, list) or not scenes:
            raise ValueError("reader_first_delivery_complete_scene_range_required")
        scene_numbers = []
        for scene in scenes:
            if not isinstance(scene, dict) or type(scene.get("scene_number")) is not int:
                raise ValueError("reader_first_delivery_scene_number_invalid")
            scene_numbers.append(scene["scene_number"])
        if sorted(scene_numbers) != list(range(1, len(scenes) + 1)):
            raise ValueError("reader_first_delivery_complete_scene_range_required")
        _reader_first_field_roles(row, binding)
    if sorted(chapter_numbers) != list(range(1, target_chapters + 1)):
        raise ValueError("reader_first_delivery_complete_chapter_range_required")


def verify_reader_first_delivery(
    *,
    project: "ProjectModel",
    chapter: "ChapterModel",
    block: "PromptBlock",
) -> dict:
    """Verify a complete v3 transport, without historical-context bypass or approval.

    The production loader owns the original source envelope and native receipts.
    This consumer reparses their retained source/review and checks the entire
    project census. No manuscript body, paid grade or adoption is inferred.
    """
    from bestseller.services.chapter_writer_delivery import (
        READER_FIRST_DELIVERY_SCHEMA,
        READER_FIRST_DELIVERY_SOURCE,
        _reader_first_delivery_text,
        _reader_first_field_roles,
        _source_metadata_paths,
        _verified_reader_first_review,
    )
    from bestseller.services.source_bound_planning_content import reader_first_binding

    binding = reader_first_binding(project)
    if binding is None:
        raise ValueError("reader_first_delivery_project_binding_required")
    if set(binding) != {"protocol", "config_sha256", "user_input_sha256"}:
        raise ValueError("reader_first_delivery_project_binding_fields_invalid")
    target = getattr(project, "target_chapters", None)
    number = getattr(chapter, "chapter_number", None)
    if (
        type(target) is not int
        or target < 1
        or type(number) is not int
        or not 1 <= number <= target
    ):
        raise ValueError("reader_first_delivery_project_census_required")
    try:
        provenance = block.provenance
        if not (
            block.key == "chapter_first.approved_current_delivery"
            and block.source == READER_FIRST_DELIVERY_SOURCE
            and block.channel == "user"
            and block.layer == "scene_spec"
            and block.instruction_family == "writer.current_chapter.delivery"
            and block.authority == 90
            and block.required is True
            and block.trim_policy == "preserve"
            and block.phase == "writer"
            and provenance is not None
            and provenance.kind == "scene_spec"
        ):
            raise ValueError("reader_first_delivery_verified_block_required")
        payload = json.loads(
            block.text.split("\n", 2)[2],
            object_pairs_hook=_reader_first_json_pairs,
            parse_constant=_reader_first_json_constant,
        )
        # Also reject nonfinite values expressed as numeric overflow (1e999).
        json.dumps(payload, ensure_ascii=False, allow_nan=False)
        expected_keys = {
            "schema",
            "project_id",
            "chapter_id",
            "chapter_number",
            "artifact_id",
            "approved_chapter_sha256",
            "readiness_receipt_sha256",
            "reader_first_planning",
            "source_binding",
            "source_chapter_pointer",
            "review_source_sha256",
            "review_source",
            "raw_review",
            "reader_receipt",
            "reader_receipt_sha256",
            "authority",
            "approved_source",
            "narrative_task",
            "continuity_reference",
            "field_sources",
            "writer_instruction",
        }
        if not isinstance(payload, dict) or set(payload) != expected_keys:
            raise ValueError("reader_first_delivery_payload_fields_invalid")
        if isinstance(payload["raw_review"], str):
            raw = json.loads(
                payload["raw_review"],
                object_pairs_hook=_reader_first_json_pairs,
                parse_constant=_reader_first_json_constant,
            )
            json.dumps(raw, ensure_ascii=False, allow_nan=False)
        expected = {
            "project_id": str(project.id),
            "chapter_id": str(chapter.id),
            "chapter_number": number,
            "artifact_id": payload["artifact_id"],
        }
        if (
            payload["schema"] != READER_FIRST_DELIVERY_SCHEMA
            or payload["reader_first_planning"] != binding
            or payload["source_binding"] != expected
            or any(payload[key] != value for key, value in expected.items())
            or type(payload["chapter_number"]) is not int
            or type(payload["source_binding"]["chapter_number"]) is not int
            or payload["authority"]
            != ("Approved future task for this chapter, not already occurred history.")
        ):
            raise ValueError("reader_first_delivery_source_binding_changed")
        for key in ("project_id", "chapter_id", "artifact_id"):
            if not isinstance(payload[key], str) or str(UUID(payload[key])) != payload[key]:
                raise ValueError("reader_first_delivery_source_identity_invalid")
        for key in (
            "approved_chapter_sha256",
            "readiness_receipt_sha256",
            "review_source_sha256",
            "reader_receipt_sha256",
        ):
            if not isinstance(payload[key], str) or not re.fullmatch("[0-9a-f]{64}", payload[key]):
                raise ValueError("reader_first_delivery_source_hash_invalid")
        source = payload["review_source"]
        _reader_first_delivery_census(source, target_chapters=target, binding=binding)
        approved = payload["approved_source"]
        roles, paths = _reader_first_field_roles(approved, binding)
        metadata_paths = tuple(_source_metadata_paths(approved))
        approved_hash = content_hash(approved)
        review = _verified_reader_first_review(
            source=source,
            pointer=payload["source_chapter_pointer"],
            approved=approved,
            binding=binding,
            chapter_number=number,
            raw_review=payload["raw_review"],
            receipt=payload["reader_receipt"],
        )
        if (
            type(approved["chapter_number"]) is not int
            or approved["chapter_number"] != number
            or payload["approved_chapter_sha256"] != approved_hash
            or payload["review_source_sha256"] != content_hash(source)
            or review["source_sha256"] != payload["review_source_sha256"]
            or payload["reader_receipt_sha256"] != content_hash(review)
            or payload["narrative_task"] != [p for p in paths if roles[p] == "narrative_task"]
            or payload["continuity_reference"]
            != [p for p in paths if roles[p] == "continuity_reference"] + list(metadata_paths)
            or payload["field_sources"]
            != {
                p: {
                    "approved_chapter_sha256": approved_hash,
                    "review_source_pointer": payload["source_chapter_pointer"] + p,
                }
                for p in (*paths, *metadata_paths)
            }
            or provenance.source_id != payload["artifact_id"]
            or provenance.source_hash != approved_hash
            or provenance.field_paths != (*paths, *metadata_paths)
            or block.text
            != _reader_first_delivery_text(
                payload,
                getattr(project, "language", None) or "zh",
            )
        ):
            raise ValueError("reader_first_delivery_writer_binding_changed")
    except (KeyError, TypeError, IndexError, AttributeError) as exc:
        raise ValueError("reader_first_delivery_writer_binding_changed") from exc
    return deepcopy(payload)
