"""Finite Q4 scripted contracts, bound to current native wire and private input.

No transport installation, story generation or native state writes. Native pure
protocol helpers validate the current config, instruction and quoted evidence.
An authored low-scoring review is a valid response, never upgraded to a pass.
"""

from __future__ import annotations

# ruff: noqa: ANN001, ANN201, ANN202 -- Runtime JSON contract boundary.
from collections.abc import Mapping
from copy import deepcopy
import hashlib
import json
from types import SimpleNamespace
from uuid import UUID

from .contracts import canonical, digest, need
from .wire_material import bind_authored_material

# Literal names only: adding a new native stage requires an explicit contract.
TOURNAMENT_ALIASES = {
    "concept_tournament_candidate_reader_first_v1": "concept_tournament_candidate",
    "concept_tournament_engine_kernel_reader_first_v1": "concept_tournament_engine_kernel",
    "concept_tournament_finalist_judge_reader_first_v1": "concept_tournament_finalist_judge",
    "concept_tournament_finalist_seriality_judge_reader_first_v1": (
        "concept_tournament_finalist_seriality_judge"
    ),
    "concept_tournament_judge_reader_first_v1": "concept_tournament_judge",
    "concept_tournament_premise_judge_reader_first_v1": "concept_tournament_premise_judge",
    "concept_tournament_raw_idea_pool_reader_first_v1": "concept_tournament_raw_idea_pool",
    "concept_tournament_raw_idea_rank_reader_first_v1": "concept_tournament_raw_idea_rank",
    "concept_tournament_seriality_reader_first_v1": "concept_tournament_seriality",
    "concept_tournament_seriality_judge_reader_first_v1": "concept_tournament_seriality_judge",
    "concept_tournament_story_judge_reader_first_v1": "concept_tournament_story_judge",
}
PRODUCER_ALIASES = {
    "conception_finalize_reader_first_v1": "conception_finalize",
    "conception_finalize_reader_first_v1_repair": "conception_finalize_repair",
    "conception_story_spine_polish_reader_first_v1": "conception_story_spine_polish",
    "conception_story_spine_polish_reader_first_v1_repair": "conception_story_spine_polish_repair",
}
INSTRUCTION_STAGES = {
    **dict.fromkeys(TOURNAMENT_ALIASES, "concept_tournament"),
    "conception_finalize_reader_first_v1": "conception_finalize",
    "conception_finalize_reader_first_v1_repair": "conception_finalize",
    "conception_story_spine_polish_reader_first_v1": "conception_story_spine_polish",
    "conception_story_spine_polish_reader_first_v1_repair": "conception_story_spine_polish",
}
REVIEW_PROTOCOLS = {
    "conception_reader_first_skeleton_v1": ("skeleton", "reader-first-review.v1"),
    "planner_reader_first_outline_review": ("outline", "rf-outline-review-v1"),
}
DIMENSIONS = (
    "character_emotion_drives_conflict",
    "reader_promise",
    "emotional_payoff",
    "character_distinctiveness",
)
REVIEW_FIELDS = frozenset(
    {"schema", "checkpoint", "source_sha256", "config_sha256", "dimensions", "issues", "options"}
)
_MARKER_FIELDS = frozenset({"protocol", "config_sha256", "user_input_sha256"})
_SOURCE_FIELDS = {
    "skeleton": frozenset({"original_user_input", "creation_inputs", "candidate"}),
    "outline": frozenset(
        {
            "book_spec",
            "world_spec",
            "cast_spec",
            "volume_plan",
            "outline",
            "premise",
            "original_input_binding",
        }
    ),
}
_SHORT_SOURCE_FIELDS = frozenset({"selected_source", "original_beat", "base_outline", "outline"})
_SHORT_SELECTED_FIELDS = frozenset(
    {
        "project_id",
        "premise",
        "project_inputs",
        "foundation",
        "complete_foundation_content",
        "canonical",
        "book_design_snapshot",
        "effective_pov",
        "explicit_creation_contracts",
        "current_author_fields",
        "required_author_layers",
        "required_author_secondary_roles",
    }
)


def _native():
    from bestseller.services import source_bound_planning_content

    return source_bound_planning_content


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError("duplicate_reader_first_key")
        result[key] = value
    return result


def _constant(_value):
    raise ValueError("nonfinite_reader_first_value")


def _decoder():
    return json.JSONDecoder(object_pairs_hook=_pairs, parse_constant=_constant)


def _wire(context):
    wire = context.wire_body
    need(
        isinstance(wire, Mapping) and context.expected_wire_sha256 == digest(wire),
        "SCRIPT_SOURCE_WIRE_MATCH_REQUIRED",
    )
    need(not wire.get("tools"), "SCRIPT_TOOL_PROTOCOL_UNCOVERED")
    messages = wire.get("messages")
    need(
        type(messages) is list
        and bool(messages)
        and all(isinstance(message, Mapping) for message in messages),
        "SCRIPT_READER_FIRST_WIRE_INVALID",
    )
    need(len(canonical(wire).encode()) <= 32_000_000, "SCRIPT_RESPONSE_UNBOUNDED")
    return wire, messages


def _expected_binding(context):
    subject = context.protocol_subject
    need(
        isinstance(subject, Mapping) and isinstance(subject.get("reader_first_planning"), Mapping),
        "SCRIPT_READER_FIRST_BINDING_REQUIRED",
    )
    marker = subject["reader_first_planning"]
    need(set(marker) == _MARKER_FIELDS, "SCRIPT_READER_FIRST_BINDING_MISMATCH")
    try:
        checked = _native().reader_first_binding({"reader_first_planning": marker})
    except (ValueError, TypeError, KeyError, OSError):
        need(False, "SCRIPT_READER_FIRST_BINDING_MISMATCH")
    return checked


def validate_instruction(template, context):
    """Verify a named native instruction, including its exact binding envelope.

    The instruction can retain a native JSON-repair diagnostics suffix. Quoted
    markers in assistant messages and merely naming the protocol are invalid.
    """
    need(template in INSTRUCTION_STAGES, "SCRIPT_TEMPLATE_UNCOVERED")
    _body, messages = _wire(context)
    marker = _expected_binding(context)
    roles = {"system", "developer"} if template in TOURNAMENT_ALIASES else {"user"}
    stage = INSTRUCTION_STAGES[template]
    matches = 0
    for language in ("zh", "en"):
        instruction = _native().reader_first_instruction(marker, language=language, stage=stage)
        envelope_at = instruction.index('{"binding":')
        prefix = instruction[:envelope_at]
        envelope = _decoder().decode(instruction[envelope_at:])
        for message in messages:
            text = message.get("content")
            if message.get("role") not in roles or type(text) is not str:
                continue
            start = text.find(prefix)
            if start < 0:
                continue
            need(text.find(prefix, start + len(prefix)) < 0, "SCRIPT_READER_FIRST_WIRE_INVALID")
            try:
                observed, _end = _decoder().raw_decode(text[start + len(prefix) :])
            except (ValueError, TypeError, RecursionError):
                need(False, "SCRIPT_READER_FIRST_WIRE_INVALID")
            need(type(observed) is dict, "SCRIPT_READER_FIRST_WIRE_INVALID")
            need(observed.get("binding") == marker, "SCRIPT_READER_FIRST_BINDING_MISMATCH")
            need(observed == envelope, "SCRIPT_READER_FIRST_WIRE_INVALID")
            matches += 1
    need(matches == 1, "SCRIPT_READER_FIRST_WIRE_INVALID")
    return deepcopy(marker)


def _has_binding(value):
    pending, nodes = [value], 0
    while pending:
        current = pending.pop()
        nodes += 1
        need(nodes <= 100_000, "SCRIPT_AUTHORED_MATERIAL_UNBOUNDED")
        if isinstance(current, Mapping):
            if any(type(key) is str and key.startswith("$") for key in current):
                return True
            pending.extend(current.values())
        elif type(current) is list:
            pending.extend(current)
    return False


def _short_outline_source(source, marker):
    """Check the native short envelope without projecting or claiming custody.

    The full selected manifest and opaque original receipt remain hashed input.
    Only native pure segment/coverage helpers are used; no source selection,
    receipt creation, paid verification or chapter adoption is performed here.
    """
    from bestseller.services import fanqie_outline_contract_completion as short
    from bestseller.services import planner

    code = "SCRIPT_READER_FIRST_REVIEW_INVALID"
    need(_SHORT_SOURCE_FIELDS <= source.keys(), code)
    selected, beat = source["selected_source"], source["original_beat"]
    need(type(selected) is dict and set(selected) == _SHORT_SELECTED_FIELDS, code)
    need(
        type(selected["project_inputs"]) is dict
        and type(selected["complete_foundation_content"]) is dict
        and {"book_spec", "world_spec", "cast_spec"}
        <= selected["complete_foundation_content"].keys()
        and type(beat) is dict
        and set(beat) == {"artifact_id", "raw", "receipt"}
        and type(beat["raw"]) is dict
        and type(beat["receipt"]) is dict
        and type(source["base_outline"]) is dict
        and type(source["outline"]) is dict,
        code,
    )
    count = selected["project_inputs"].get("target_chapters")
    need(type(count) is int and 4 <= count <= 8, code)
    need(
        set(source) == _SHORT_SOURCE_FIELDS | ({"segment_contract"} if count != 4 else set()), code
    )
    metadata = {}
    if count != 4:
        segment = source["segment_contract"]
        need(
            type(segment) is dict
            and {"target_chapters", "ordered_chapter_numbers"} <= segment.keys()
            and set(segment)
            <= {"target_chapters", "ordered_chapter_numbers", "creation_segment_count"},
            code,
        )
        if "creation_segment_count" in segment:
            metadata["segment_count"] = segment["creation_segment_count"]
    chapters = source["outline"].get("chapters")
    need(type(chapters) is list and len(chapters) == count, code)
    for chapter in chapters:
        need(type(chapter) is dict and type(chapter.get("chapter_number")) is int, code)
        scenes = chapter.get("scenes")
        need(
            type(scenes) is list
            and bool(scenes)
            and all(
                type(scene) is dict and type(scene.get("scene_number")) is int for scene in scenes
            ),
            code,
        )
    try:
        for value in (selected["project_id"], beat["artifact_id"]):
            need(type(value) is str and str(UUID(value)) == value, code)
        project = SimpleNamespace(target_chapters=count, metadata_json=metadata)
        short._segment_count(project, source)
        planner._reader_first_outline_coverage(source, project, marker)
    except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
        need(False, code)


def decode_review_wire(template, context):
    """Return the entire current source; no projection or inferred identity."""
    need(template in REVIEW_PROTOCOLS, "SCRIPT_TEMPLATE_UNCOVERED")
    _body, messages = _wire(context)
    marker = _expected_binding(context)
    checkpoint, version = REVIEW_PROTOCOLS[template]
    if checkpoint == "outline" and marker["protocol"] == "reader-first-planning.v2":
        version = "rf-outline-review-v2"
    need(
        context.protocol_subject.get("prompt_version") == version,
        "SCRIPT_READER_FIRST_VERSION_MISMATCH",
    )
    users = [message.get("content") for message in messages if message.get("role") == "user"]
    code = "SCRIPT_READER_FIRST_REVIEW_INVALID"
    need(len(users) == 1 and type(users[0]) is str, code)
    try:
        document = _decoder().decode(users[0])
    except (ValueError, TypeError, RecursionError):
        need(False, code)
    need(type(document) is dict, code)
    source = document.get("source")
    need(
        document.get("schema") == "reader-first-review-input.v1"
        and document.get("checkpoint") == checkpoint
        and document.get("binding") == marker
        and document.get("config_sha256") == marker["config_sha256"]
        and document.get("dimensions") == list(DIMENSIONS)
        and type(source) is dict,
        code,
    )
    need(document.get("source_sha256") == digest(source), code)
    if checkpoint == "skeleton":
        need(_SOURCE_FIELDS[checkpoint] <= source.keys(), code)
        original = source["original_user_input"]
        need(
            type(original) is str
            and bool(original.strip())
            and hashlib.sha256(original.encode()).hexdigest() == marker["user_input_sha256"]
            and type(source["creation_inputs"]) is dict
            and type(source["candidate"]) is dict
            and bool(source["candidate"]),
            code,
        )
    elif {"selected_source", "original_beat", "base_outline", "segment_contract"} & source.keys():
        _short_outline_source(source, marker)
    else:
        need(_SOURCE_FIELDS[checkpoint] <= source.keys(), code)
        need(source["original_input_binding"] == marker and type(source["outline"]) is dict, code)
    return deepcopy(document)


def review_submission(template, context):
    """Validate authored raw JSON; do not emit a native receipt or its status."""
    document = decode_review_wire(template, context)
    material = context.submissions.get(template + ":" + str(context.call_index))
    need(isinstance(material, Mapping) and bool(material), "SCRIPT_SOURCE_BOUND_REPLY_REQUIRED")
    code = "SCRIPT_READER_FIRST_REVIEW_INVALID"
    need(set(material) == REVIEW_FIELDS, code)
    need(len(canonical(material).encode()) <= 512_000, "SCRIPT_RESPONSE_UNBOUNDED")
    dimensions = material["dimensions"]
    need(isinstance(dimensions, Mapping) and set(dimensions) == set(DIMENSIONS), code)
    for key in ("issues", "options"):
        need(not _has_binding(material[key]), "SCRIPT_VERDICT_MUST_BE_AUTHORED")
    for row in dimensions.values():
        need(isinstance(row, Mapping) and set(row) == {"score", "reason", "evidence"}, code)
        for key in ("score", "reason"):
            need(not _has_binding(row[key]), "SCRIPT_VERDICT_MUST_BE_AUTHORED")
        evidence = row["evidence"]
        need(type(evidence) is list and bool(evidence), code)
        need(
            all(isinstance(item, Mapping) and set(item) == {"path", "quote"} for item in evidence),
            code,
        )
    payload, _bindings = bind_authored_material(
        material, context.wire_body, expected_wire_sha256=context.expected_wire_sha256
    )
    # These pure native checks reject missing axes, invalid numeric types,
    # blank reasons, fake pointers/quotes and stale source/config hashes. Both
    # valid pass and valid fail are returned unchanged; native workflow alone
    # decides whether a review permits repair, continuation or adoption.
    try:
        receipt = _native().parse_reader_first_review(
            payload,
            source=document["source"],
            binding=document["binding"],
            checkpoint=document["checkpoint"],
        )
    except (ValueError, TypeError, KeyError, OSError, RecursionError):
        need(False, code)
    need(receipt.get("status") in {"passed", "failed"}, code)
    return deepcopy(payload)
