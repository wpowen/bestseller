"""Explicit normal-quickstart SDK response builders, without native authority.

All story material is supplied privately at runtime. Producer builders transform
that one blueprint; review builders require an explicitly authored reply for the
exact physical send. Neither schema presence nor missing material implies pass.
Native schema/codec definitions are imported lazily for the named protocols.
No application startup, DB operations, transport installation or I/O.
"""

from __future__ import annotations

# ruff: noqa: ANN001, ANN201, ANN202, RUF001 -- Runtime JSON boundary; deliberate Chinese punctuation.
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field, replace
import hashlib
import json
import re

from . import (
    keyflow_protocols,
    l1_required_protocols,
    q4_authored_repairs,
    reader_first_protocols,
    reader_revision_protocols,
)
from .contracts import (
    DEFAULT_LIMITS,
    TemplateCatalog,
    canonical,
    digest,
    need,
    text_response,
    tool_response,
)
from .wire_material import bind_authored_material


@dataclass(frozen=True)
class BuilderContext:
    story: Mapping
    chapter_number: int = 1
    call_index: int = 1
    current_body: str | None = None
    source_body_sha256: str | None = None
    wire_body: Mapping | None = None
    expected_wire_sha256: str | None = None
    protocol_subject: Mapping | None = None
    # Exact template:physical_index -> explicit model proposal. No default pass.
    submissions: Mapping = field(default_factory=dict)

    def __post_init__(self) -> None:
        need(isinstance(self.story, Mapping), "SCRIPT_STORY_BLUEPRINT_REQUIRED")
        need(
            str(self.story.get("language", "zh-CN")).startswith("zh"),
            "SCRIPT_LANGUAGE_PROFILE_UNCOVERED",
        )
        need(
            type(self.call_index) is int and 1 <= self.call_index <= 4096,
            "SCRIPT_CALL_INDEX_UNBOUNDED",
        )
        need(type(self.chapter_number) is int and self.chapter_number > 0, "SCRIPT_CHAPTER_INVALID")


def _text(value):
    need(
        type(value) is str and bool(value.strip()) and len(value) <= 16_000,
        "SCRIPT_STORY_MATERIAL_REQUIRED",
    )
    return value.strip()


def _strings(value, *, nonempty=True):
    need(
        type(value) in (list, tuple) and (bool(value) or not nonempty) and len(value) <= 128,
        "SCRIPT_STORY_MATERIAL_REQUIRED",
    )
    return [_text(item) for item in value]


def _actor(story, role):
    value = story.get(role)
    need(isinstance(value, Mapping), "SCRIPT_CAST_MATERIAL_REQUIRED")
    for key in ("name", "occupation", "background", "fear", "flaw", "strength"):
        _text(value.get(key))
    need(
        type(value.get("age")) is int and 0 <= value["age"] <= 200, "SCRIPT_CAST_MATERIAL_REQUIRED"
    )
    need(
        value.get("gender") in {"male", "female", "nonbinary", "unknown"},
        "SCRIPT_CAST_MATERIAL_REQUIRED",
    )
    return value


def _chapters(story):
    rows = story.get("chapters")
    need(type(rows) in (tuple, list) and 1 <= len(rows) <= 32, "SCRIPT_CHAPTER_PLAN_REQUIRED")
    for number, row in enumerate(rows, 1):
        need(
            isinstance(row, Mapping) and row.get("number") == number, "SCRIPT_CHAPTER_PLAN_REQUIRED"
        )
        for key in ("title", "goal", "obstacle", "choice", "consequence", "hook", "location"):
            _text(row.get(key))
        _strings(row.get("reveals"), nonempty=False)
    return rows


def story_spine(context):
    story, person = context.story, _actor(context.story, "protagonist")
    return {
        "who": person["name"] + "，" + person["occupation"],
        "wants": _text(story.get("goal")),
        "why_now": _text(story.get("trigger")),
        "against": _actor(story, "antagonist")["name"],
        "stakes": _text(story.get("stakes")),
        "question": person["name"] + "能否" + _text(story.get("goal")).rstrip("。？?") + "？",
    }


def market_proposal(context):
    story, person = context.story, _actor(context.story, "protagonist")
    return {
        "platform_target": _text(story.get("platform")),
        "reader_promise": person["name"] + "要" + _text(story.get("goal")),
        "selling_points": [_text(story.get(key)) for key in ("trigger", "advantage", "stakes")],
        "trope_keywords": _strings(story.get("tags")),
        "hook_keywords": [_text(story.get("goal")), _text(story.get("advantage"))],
        "opening_strategy": _chapters(story)[0]["obstacle"],
        "chapter_hook_strategy": _chapters(story)[0]["hook"],
        "pacing_profile": "medium",
        "payoff_rhythm": _text(story.get("development")),
        "content_mode": _text(story.get("content_mode")),
    }


def commercial_positioning(context):
    return {
        **market_proposal(context),
        "target_audiences": _strings(context.story.get("audiences")),
        "benchmark_works": [],
        "update_strategy": "按章节计划完成",
        "taboo_topics": [],
        "taboo_words": [],
        "commercial_rationale": _text(context.story.get("reader_rationale")),
        "confidence": 0.0,
        "assumptions": ["合成场景，无真实市场测量"],
    }


def character_proposal(context):
    story = context.story
    protagonist, antagonist, ally = (
        _actor(story, key) for key in ("protagonist", "antagonist", "ally")
    )
    return {
        "protagonist_archetype": protagonist["occupation"],
        "protagonist_name": protagonist["name"],
        "protagonist_name_reasoning": protagonist["background"],
        "protagonist_age": protagonist["age"],
        "protagonist_profession": protagonist["occupation"],
        "career_reality_ledger": _text(story.get("career_ledger")),
        "profession_boundary": _text(story.get("profession_boundary")),
        "protagonist_core_drive": _text(story.get("goal")),
        "golden_finger": _text(story.get("advantage")),
        "growth_curve": _text(story.get("development")),
        "power_tiers": _strings(story.get("power_tiers"), nonempty=False),
        "romance_mode": _text(story.get("romance_mode")),
        "relationship_tension": _text(story.get("relationship_tension")),
        "antagonist_mode": "escalating",
        "conflict_forces": [
            {
                "name": antagonist["name"],
                "force_type": "character",
                "active_volumes": [1],
                "threat_description": _text(story.get("stakes")),
                "relationship_to_protagonist": _text(story.get("relationship_tension")),
                "escalation_path": _text(story.get("development")),
            }
        ],
        "key_characters": [
            {
                "name": actor["name"],
                "role": role,
                "name_reasoning": actor["background"],
                "age_profession": str(actor["age"]) + "岁，" + actor["occupation"],
                "function": actor["strength"],
            }
            for role, actor in (
                ("protagonist", protagonist),
                ("antagonist", antagonist),
                ("ally", ally),
            )
        ],
    }


def world_proposal(context):
    story = context.story
    return {
        "worldbuilding_density": "medium",
        "info_reveal_strategy": _text(story.get("development")),
        "rule_hardness": "hard",
        "power_system_style": _text(story.get("advantage")),
        "mystery_density": "medium",
        "world_era": _text(story.get("era")),
        "core_conflict_source": _text(story.get("trigger")),
        "escalation_mechanism": _text(story.get("development")),
    }


def architect_facets(context):
    story = context.story
    return {
        "primary_genre": _text(story.get("genre")),
        "language": "zh-CN",
        "sub_genres": [_text(story.get("sub_genre"))],
        "setting": _text(story.get("setting")),
        "tone": "balanced",
        "power_system": _text(story.get("advantage")),
        "relationship_mode": _text(story.get("romance_mode")),
        "narrative_drive": _text(story.get("narrative_drive")),
        "emotional_register": "balanced",
        "trope_tags": _strings(story.get("tags")),
        "platform_style": _text(story.get("platform")),
        "gender_channel": _text(story.get("channel")),
    }


def finalize_proposal(context):
    story = context.story
    # Runtime source text is mandatory; do not copy a business fallback or
    # pad a synopsis until a gate threshold happens to be reached.
    adjudications = story.get("review_adjudications")
    need(type(adjudications) is list, "SCRIPT_REVIEW_ADJUDICATION_REQUIRED")
    return {
        "title": _text(story.get("title")),
        "premise": _text(story.get("premise")),
        "synopsis": _text(story.get("synopsis")),
        "tags": _strings(story.get("tags")),
        "story_spine": story_spine(context),
        "review_adjudications": deepcopy(adjudications),
        "writing_profile": {
            "market": market_proposal(context),
            "character": character_proposal(context),
            "world": world_proposal(context),
            "style": {
                "pov_type": "third-limited",
                "tense": "past",
                "custom_rules": [],
                "prose_style": "commercial-web-serial",
                "sentence_style": "mixed",
                "dialogue_ratio": 0.3,
                "tone_keywords": _strings(story.get("tone_keywords")),
            },
            "serialization": {
                "opening_mandate": _chapters(story)[0]["obstacle"],
                "first_three_chapter_goal": _chapters(story)[min(2, len(_chapters(story)) - 1)][
                    "goal"
                ],
                "scene_drive_rule": _text(story.get("development")),
                "chapter_ending_rule": _chapters(story)[0]["hook"],
                "free_chapter_strategy": _text(story.get("reader_rationale")),
            },
        },
    }


def cast_spec(context):
    story = context.story

    def person(role):
        value = _actor(story, role)
        return {
            "name": value["name"],
            "role": role,
            "age": value["age"],
            "gender": value["gender"],
            "background": value["background"],
            "goal": _text(story.get("goal")),
            "fear": value["fear"],
            "flaw": value["flaw"],
            "strength": value["strength"],
            "arc_trajectory": _text(story.get("development")),
        }

    return {
        "protagonist": person("protagonist"),
        "antagonist": person("antagonist"),
        "supporting_cast": [person("ally")],
        "antagonist_forces": [],
        "conflict_map": [],
    }


def world_spec(context):
    story = context.story
    # All rules/locations are exact runtime blueprint objects, not new source IDs.
    rules, locations = story.get("rules"), story.get("locations")
    need(
        type(rules) in (tuple, list) and bool(rules) and len(rules) <= 32,
        "SCRIPT_WORLD_MATERIAL_REQUIRED",
    )
    need(
        type(locations) in (tuple, list) and bool(locations) and len(locations) <= 32,
        "SCRIPT_WORLD_MATERIAL_REQUIRED",
    )
    return {
        "world_name": _text(story.get("setting")),
        "world_premise": _text(story.get("premise")),
        "rules": deepcopy(list(rules)),
        "locations": deepcopy(list(locations)),
        "power_system": deepcopy(story.get("power_system", {})),
        "factions": deepcopy(story.get("factions", [])),
        "history_key_events": deepcopy(story.get("history_key_events", [])),
    }


def book_spec(context):
    story = context.story
    return {
        "logline": _text(story.get("logline")),
        "theme_statement": _text(story.get("theme")),
        "dramatic_question": story_spine(context)["question"],
        "series_engine": _text(story.get("development")),
        "reader_promise": market_proposal(context)["reader_promise"],
        "main_characters": character_proposal(context)["key_characters"],
        "opening_image": _chapters(story)[0]["obstacle"],
        "ending_image": _chapters(story)[-1]["consequence"],
    }


def volume_plan(context):
    rows, story = _chapters(context.story), context.story
    return [
        {
            "volume_number": 1,
            "volume_title": _text(story.get("title")),
            "volume_theme": _text(story.get("theme")),
            "chapter_count_target": len(rows),
            "word_count_target": sum(_word_target(row) for row in rows),
            "volume_goal": _text(story.get("goal")),
            "volume_obstacle": rows[0]["obstacle"],
            "volume_climax": rows[-1]["choice"],
            "key_reveals": [r for row in rows for r in row["reveals"]],
            "foreshadowing_planted": [],
            "foreshadowing_paid_off": [],
            "reader_hook_to_next": rows[-1]["hook"],
        }
    ]


def _word_target(row):
    value = row.get("target_word_count", 2200)
    need(type(value) is int and 1 <= value <= 20_000, "SCRIPT_WORD_TARGET_INVALID")
    return value


def outline_batch(context, *, start=1, end=None):
    rows = _chapters(context.story)
    end = len(rows) if end is None else end
    need(
        type(start) is int and type(end) is int and 1 <= start <= end <= len(rows),
        "SCRIPT_OUTLINE_RANGE_INVALID",
    )
    chapters = []
    for row in rows[start - 1 : end]:
        # Preserve explicit source contracts supplied by the story planner.
        # Missing causal/methodology/world bindings are not auto-approved.
        need(
            all(
                isinstance(row.get(key), Mapping) and bool(row[key])
                for key in ("causal_contract", "methodology_contract")
            )
            and isinstance(row.get("scenes"), list)
            and bool(row["scenes"]),
            "SCRIPT_OUTLINE_SOURCE_CONTRACT_REQUIRED",
        )
        chapters.append(
            {
                "chapter_number": row["number"],
                "volume_number": 1,
                "title": row["title"],
                "chapter_goal": row["goal"],
                "opening_pressure": row["obstacle"],
                "main_conflict": row["obstacle"],
                "required_payoff": row["consequence"],
                "tail_hook": row["hook"],
                "hook_description": row["hook"],
                "target_word_count": _word_target(row),
                "key_reveals": list(row["reveals"]),
                "causal_contract": deepcopy(row.get("causal_contract", {})),
                "methodology_contract": deepcopy(row.get("methodology_contract", {})),
                "scenes": deepcopy(row.get("scenes", [])),
            }
        )
    return {"batch_name": "volume-1-" + str(start) + "-" + str(end), "chapters": chapters}


def render_chapter(context):
    """Compose finite runtime action atoms; never repeat/pad to pass a word gate."""
    if context.current_body is not None:
        return _text(context.current_body)
    rows = _chapters(context.story)
    need(context.chapter_number <= len(rows), "SCRIPT_CHAPTER_INVALID")
    atoms = rows[context.chapter_number - 1].get("prose_atoms")
    need(type(atoms) in (tuple, list) and 1 <= len(atoms) <= 128, "SCRIPT_PROSE_MATERIAL_REQUIRED")
    paragraphs = []
    for atom in atoms:
        need(isinstance(atom, Mapping), "SCRIPT_PROSE_MATERIAL_REQUIRED")
        # Each sentence is built from the same runtime planned action/cause/result.
        parts = [_text(atom.get(key)).rstrip("。") for key in ("action", "reaction", "result")]
        paragraphs.append("。".join(parts) + "。")
    need(len(set(paragraphs)) == len(paragraphs), "SCRIPT_PROSE_REPEATED_ATOM")
    body = "\n\n".join(paragraphs)
    need(len(body) <= 100_000, "SCRIPT_RESPONSE_UNBOUNDED")
    return body


def explicit_submission(template, context):
    """A model proposal for this exact physical send, never a native receipt."""
    key = template + ":" + str(context.call_index)
    value = context.submissions.get(key)
    need(isinstance(value, Mapping) and bool(value), "SCRIPT_SOURCE_BOUND_REPLY_REQUIRED")
    if template in BODY_BOUND_PROTOCOLS:
        need(
            type(context.current_body) is str
            and bool(context.current_body)
            and type(context.source_body_sha256) is str
            and hashlib.sha256(context.current_body.encode()).hexdigest()
            == context.source_body_sha256,
            "SCRIPT_CURRENT_BODY_BINDING_REQUIRED",
        )
    required = BOUND_FIELDS.get(template, ())
    need(set(required) <= set(value), "SCRIPT_REPLY_FIELDS_REQUIRED")
    if context.wire_body is not None:
        value, _receipt = bind_authored_material(
            value, context.wire_body, expected_wire_sha256=context.expected_wire_sha256
        )
    return deepcopy(dict(value))


CAST_PART_PROTOCOLS = frozenset({"planner_cast_person_part", "planner_cast_links_part"})


def _cast_part_document(template, context):
    """Read the exact native JSON prefix; preserve retries in the wire hash."""
    wire = context.wire_body
    need(
        isinstance(wire, Mapping) and context.expected_wire_sha256 == digest(wire),
        "SCRIPT_SOURCE_WIRE_MATCH_REQUIRED",
    )
    need(not wire.get("tools"), "SCRIPT_TOOL_PROTOCOL_UNCOVERED")
    messages = wire.get("messages")
    need(type(messages) is list, "SCRIPT_CAST_PART_WIRE_INVALID")
    users = [row for row in messages if isinstance(row, Mapping) and row.get("role") == "user"]
    need(
        len(users) == 1
        and type(users[0].get("content")) is str
        and len(users[0]["content"].encode()) <= 32_000_000,
        "SCRIPT_CAST_PART_WIRE_INVALID",
    )
    # These native helpers only construct schemas/decode JSON. Never call
    # generate(), prepare(), model validation with defaults, or dispatch APIs.
    from bestseller.services import cast_links_transport, source_cast_parts
    from bestseller.services.source_bound_planning_content import CONTENT_POLICY

    try:
        document, _suffix = cast_links_transport.split(users[0]["content"])
    except (ValueError, TypeError, KeyError, RecursionError):
        need(False, "SCRIPT_CAST_PART_WIRE_INVALID")
    if document.get("schema") == cast_links_transport.POLICY:
        need(template == "planner_cast_links_part", "SCRIPT_CAST_PART_CONTRACT_MISMATCH")
        try:
            document, _suffix = cast_links_transport.decode(users[0]["content"])
        except (ValueError, TypeError, KeyError, RecursionError):
            need(False, "SCRIPT_CAST_PART_WIRE_INVALID")
    packet = document.get("frozen_source_packet")
    contract = document.get("reference_full_cast_contract")
    need(
        document.get("schema") == source_cast_parts.POLICY
        and type(packet) is dict
        and packet.get("kind") == "cast_spec"
        and packet.get("protocol") == CONTENT_POLICY
        and document.get("source_packet_sha256") == digest(packet)
        and type(contract) is dict
        and contract.get("kind") == "cast_spec"
        and contract.get("expected_root") == "object"
        and contract.get("native_submission")
        == source_cast_parts.planning_submission_contract("cast_spec")
        and type(document.get("current_part")) is dict
        and type(document.get("already_generated_people")) is list
        and len(document["already_generated_people"]) <= 128,
        "SCRIPT_CAST_PART_CONTRACT_MISMATCH",
    )
    return document, source_cast_parts


def _cast_person_identity(task):
    source = task.get("source_person")
    part_id, slot, name = (task.get(key) for key in ("part_id", "source_slot", "required_name"))
    need(
        type(part_id) is str
        and re.fullmatch(r"person:(0|[1-9]\d*)", part_id)
        and type(slot) is str
        and re.fullmatch(r"protagonist|antagonist|supporting_cast/(0|[1-9]\d*)", slot)
        and type(name) is str
        and bool(name.strip())
        and type(source) is dict
        and source.get("name") == name
        and (slot != "protagonist" or part_id == "person:0")
        and (slot == "protagonist" or part_id != "person:0"),
        "SCRIPT_CAST_PART_IDENTITY_MISMATCH",
    )
    # Identity is observed on the current native wire, never selected from an
    # enum or filled into a missing authored person.
    return {"id": part_id, "source_slot": slot, "name": name, "source": source}


def _cast_links_people(document):
    people, persons = [], {}
    for index, row in enumerate(document["already_generated_people"]):
        need(
            type(row) is dict
            and type(row.get("part_id")) is str
            and row["part_id"] == "person:" + str(index)
            and type(row.get("person")) is dict
            and type(row["person"].get("name")) is str
            and bool(row["person"]["name"].strip()),
            "SCRIPT_CAST_PART_IDENTITY_MISMATCH",
        )
        people.append(
            {
                "id": row["part_id"],
                "name": row["person"]["name"],
            }
        )
        persons[row["part_id"]] = row["person"]
    need(
        bool(people) and len({person["name"] for person in people}) == len(people),
        "SCRIPT_CAST_PART_IDENTITY_MISMATCH",
    )
    return people, persons


def cast_part_submission(template, context):
    """Validate private authored parts against native contracts; add no fields."""
    material = context.submissions.get(template + ":" + str(context.call_index))
    need(isinstance(material, Mapping) and bool(material), "SCRIPT_SOURCE_BOUND_REPLY_REQUIRED")
    document, native = _cast_part_document(template, context)
    payload, _receipt = bind_authored_material(
        material, context.wire_body, expected_wire_sha256=context.expected_wire_sha256
    )
    if template == "planner_cast_person_part":
        person = _cast_person_identity(document["current_part"])
        schema = native.person_schema(person)
        need(document.get("part_contract") == schema, "SCRIPT_CAST_PART_CONTRACT_MISMATCH")
        if isinstance(payload.get("person"), Mapping) and "name" in payload["person"]:
            need(
                payload["person"]["name"] == person["name"],
                "SCRIPT_CAST_PART_IDENTITY_MISMATCH",
            )
        try:
            native.validate_part(payload, schema)
        except (ValueError, TypeError, KeyError, RecursionError):
            need(False, "SCRIPT_CAST_PART_SCHEMA_MISMATCH")
    else:
        people, persons = _cast_links_people(document)
        schema = native.links_schema(people)
        need(document.get("part_contract") == schema, "SCRIPT_CAST_PART_CONTRACT_MISMATCH")
        need(
            not {"protagonist", "antagonist", "supporting_cast", "_meta"}.intersection(payload),
            "SCRIPT_CAST_PART_SCHEMA_MISMATCH",
        )
        layout = payload.get("layout")
        if (
            type(layout) is dict
            and type(layout.get("protagonist")) is str
            and (layout.get("antagonist") is None or type(layout["antagonist"]) is str)
            and type(layout.get("supporting_cast")) is list
            and all(type(value) is str for value in layout["supporting_cast"])
        ):
            selected = [layout["protagonist"], *layout["supporting_cast"]]
            if layout.get("antagonist") is not None:
                selected.append(layout["antagonist"])
            need(
                layout["protagonist"] == people[0]["id"]
                and len(selected) == len(people)
                and set(selected) == set(persons),
                "SCRIPT_CAST_PART_LAYOUT_MISMATCH",
            )
        try:
            # Return only the authored links. The original native caller still
            # assembles and checks the real people; no role is inferred here.
            native.validate_part(payload, schema)
        except (ValueError, TypeError, KeyError, RecursionError):
            need(False, "SCRIPT_CAST_PART_SCHEMA_MISMATCH")
    return deepcopy(payload)


SOURCE_REVIEW_TEMPLATE = "planner_source_bound_expansion_semantic_review"
SOURCE_REVIEW_VERSIONS = frozenset({"4.2-stable-catalog", "4.3-field-presence"})
SOURCE_REVIEW_DATA_FIELDS = (
    "source_quote_options",
    "candidate_quote_options",
    "explicit_author_controls",
    "candidate_quote_field_bindings",
    "changed_fields",
    "authority_and_baseline",
    "derived_candidates",
)
SOURCE_REVIEW_REPLY_FIELDS = frozenset(
    {
        "decision",
        "core_preserved",
        "core_conflicts",
        "quality_findings",
        "authority_snapshot_hash",
        "candidate_hash",
        "source_evidence",
        "candidate_evidence",
        "feedback",
    }
)


def _review_json_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_review_wire_key")
        result[key] = value
    return result


def _review_json_constant(_value):
    raise ValueError("nonfinite_review_wire_value")


def _decode_review_envelope(encoded):
    """Decode declared native containers only; authored refs remain data."""
    from bestseller.services import lossless_source_encoding as lossless
    from bestseller.services import planning_catalog_transport as catalog
    from bestseller.services import planning_string_pool as pool

    kinds = {lossless.SCHEMA, lossless.INDEXED_SCHEMA, lossless.KEY_DICTIONARY_SCHEMA}
    need(type(encoded) is dict, "SCRIPT_SOURCE_REVIEW_WIRE_INVALID")
    kind = encoded.get("schema")
    try:
        if kind in kinds:
            return lossless.decode_lossless_source(encoded)
        if kind in {catalog.POLICY, catalog.INDEXED_POLICY}:
            return catalog.decode(encoded)
        if kind == pool.SCHEMA:
            inner = pool.decode(encoded)
            need(
                type(inner) is dict and inner.get("schema") in kinds,
                "SCRIPT_SOURCE_REVIEW_WIRE_INVALID",
            )
            return lossless.decode_lossless_source(inner)
    except (ValueError, TypeError, KeyError, RecursionError):
        need(False, "SCRIPT_SOURCE_REVIEW_WIRE_INVALID")
    need(False, "SCRIPT_SOURCE_REVIEW_WIRE_INVALID")


def decode_source_review_wire(wire_body, *, expected_wire_sha256):
    """Return private native sections/hashes, never a verdict or permission.

    The entire physical wire, including retry suffix, is bound first. Only
    pure native codecs are imported; no planner, capacity or dispatch API runs.
    """
    need(
        isinstance(wire_body, Mapping) and digest(wire_body) == expected_wire_sha256,
        "SCRIPT_SOURCE_WIRE_MATCH_REQUIRED",
    )
    need(not wire_body.get("tools"), "SCRIPT_TOOL_PROTOCOL_UNCOVERED")
    messages = wire_body.get("messages")
    need(type(messages) is list, "SCRIPT_SOURCE_REVIEW_WIRE_INVALID")
    users = [row for row in messages if isinstance(row, Mapping) and row.get("role") == "user"]
    need(
        len(users) == 1 and type(users[0].get("content")) is str,
        "SCRIPT_SOURCE_REVIEW_WIRE_INVALID",
    )
    text = users[0]["content"]
    need(len(text.encode()) <= 32_000_000, "SCRIPT_SOURCE_REVIEW_WIRE_INVALID")
    hash_names = ("authority_snapshot_hash", "canonical_baseline_hash", "candidate_hash")
    names = (
        *hash_names,
        *SOURCE_REVIEW_DATA_FIELDS,
        "source_review_data",
        "identity_review_context",
        "input",
    )
    matches = list(re.finditer(r"(?m)^[ \t]*(" + "|".join(names) + r")=", text))
    need(len(matches) == len({match[1] for match in matches}), "SCRIPT_SOURCE_REVIEW_WIRE_INVALID")
    values = {}
    decoder = json.JSONDecoder(
        object_pairs_hook=_review_json_pairs, parse_constant=_review_json_constant
    )
    try:
        for match in matches:
            key, start = match[1], match.end()
            if key in hash_names:
                end = text.find("\n", start)
                value = text[start : end if end >= 0 else len(text)].strip()
                need(
                    bool(re.fullmatch(r"[0-9a-f]{64}", value)), "SCRIPT_SOURCE_REVIEW_WIRE_INVALID"
                )
            else:
                while start < len(text) and text[start].isspace():
                    start += 1
                value, _end = decoder.raw_decode(text, start)
            values[key] = value
    except (ValueError, TypeError, KeyError, RecursionError):
        need(False, "SCRIPT_SOURCE_REVIEW_WIRE_INVALID")
    need(set(hash_names) <= values.keys(), "SCRIPT_SOURCE_REVIEW_WIRE_INVALID")
    if "source_review_data" in values:
        need(
            not set(SOURCE_REVIEW_DATA_FIELDS).intersection(values),
            "SCRIPT_SOURCE_REVIEW_WIRE_INVALID",
        )
        encoded = values["source_review_data"]
        data = _decode_review_envelope(encoded)
        encoding = encoded["schema"]
    else:
        need(set(SOURCE_REVIEW_DATA_FIELDS) <= values.keys(), "SCRIPT_SOURCE_REVIEW_WIRE_INVALID")
        data = {key: values[key] for key in SOURCE_REVIEW_DATA_FIELDS}
        if "input" in values:
            data["input"] = values["input"]
        encoding = "literal"
    need(
        type(data) is dict and set(SOURCE_REVIEW_DATA_FIELDS) <= data.keys(),
        "SCRIPT_SOURCE_REVIEW_WIRE_INVALID",
    )
    need(
        type(data["authority_and_baseline"]) is dict
        and type(data["derived_candidates"]) is dict
        and type(data["candidate_quote_field_bindings"]) is dict
        and all(
            type(data[key]) is list and len(data[key]) <= 4096
            for key in ("source_quote_options", "candidate_quote_options")
        ),
        "SCRIPT_SOURCE_REVIEW_WIRE_INVALID",
    )
    fields = data["changed_fields"]
    need(
        type(fields) is list
        and 1 <= len(fields) <= 128
        and all(
            type(field) is str and re.fullmatch(r"(world|cast)\.[A-Za-z_][A-Za-z_0-9]*", field)
            for field in fields
        )
        and len(set(fields)) == len(fields),
        "SCRIPT_SOURCE_REVIEW_WIRE_INVALID",
    )
    hashes = {key: values[key] for key in hash_names}
    # The authority header names the original snapshot. An identity-only
    # baseline has a narrower snapshot and cannot replace that authority hash.
    need(
        digest(data["authority_and_baseline"]) == hashes["canonical_baseline_hash"]
        and digest(data["derived_candidates"]) == hashes["candidate_hash"],
        "SCRIPT_SOURCE_REVIEW_HASH_MISMATCH",
    )
    identity = values.get("identity_review_context")
    need(
        identity is None or (type(identity) is dict and bool(identity)),
        "SCRIPT_SOURCE_REVIEW_WIRE_INVALID",
    )
    return {
        "data": deepcopy(data),
        "hashes": hashes,
        "identity_review_context": identity,
        "encoding": encoding,
        "wire_sha256": expected_wire_sha256,
    }


def _review_has_binding(value):
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


def _review_quote_in_payload(quote, payload):
    """Native-style literal layout matching, with no semantic inference."""
    if (
        type(quote) is not str
        or not quote.strip()
        or quote.strip().lower() in {"unknown", "n/a", "none"}
    ):
        return False

    def compact(value):
        return "".join(value.replace("：", ":").split())

    wanted, pending, nodes = compact(quote), [payload], 0
    while pending:
        current = pending.pop()
        nodes += 1
        need(nodes <= 100_000, "SCRIPT_SOURCE_REVIEW_EVIDENCE_MISMATCH")
        if type(current) is str and wanted in compact(current):
            return True
        if type(current) is dict:
            name = current.get("name")
            if (
                type(name) is str
                and name.strip()
                and any(
                    key != "name"
                    and type(value) is str
                    and value.strip()
                    and wanted in compact(name + ": " + value)
                    for key, value in current.items()
                )
            ):
                return True
            pending.extend(current.values())
        elif type(current) is list:
            pending.extend(current)
    return False


def _review_field_payload(payload, field):
    section, key = field.split(".", 1)
    current = payload.get(section + "_spec")
    return current.get(key) if type(current) is dict else None


def _source_review_catalogue(options, payload, fields, *, side):
    from bestseller.services.source_bound_field_evidence import (
        SCHEMA,
        compile_field_presence_options,
    )

    catalog = {}
    for option in options:
        need(type(option) is dict, "SCRIPT_SOURCE_REVIEW_EVIDENCE_MISMATCH")
        quote_id, field = option.get("quote_id"), option.get("field")
        need(
            type(quote_id) is str and 1 <= len(quote_id) <= 256 and quote_id not in catalog,
            "SCRIPT_SOURCE_REVIEW_EVIDENCE_MISMATCH",
        )
        if side == "candidate" or option.get("schema") == SCHEMA:
            need(field in fields, "SCRIPT_SOURCE_REVIEW_EVIDENCE_MISMATCH")
        if option.get("schema") == SCHEMA:
            try:
                expected = compile_field_presence_options(payload, fields=[field], side=side)[0]
            except (ValueError, TypeError, KeyError, RecursionError):
                need(False, "SCRIPT_SOURCE_REVIEW_EVIDENCE_MISMATCH")
            need(canonical(option) == canonical(expected), "SCRIPT_SOURCE_REVIEW_EVIDENCE_MISMATCH")
        else:
            whole = _review_field_payload(payload, field) if side == "candidate" else payload
            need(
                option.get("schema") is None
                and _review_quote_in_payload(option.get("quote"), whole),
                "SCRIPT_SOURCE_REVIEW_EVIDENCE_MISMATCH",
            )
        catalog[quote_id] = option
    return catalog


def _validate_source_review_evidence(rows, catalog, fields, *, side):
    need(type(rows) is list and len(rows) == len(fields), "SCRIPT_SOURCE_REVIEW_EVIDENCE_MISMATCH")
    seen = set()
    for row in rows:
        need(
            type(row) is dict and set(row) == {"field", "quote_id", "source_relation"},
            "SCRIPT_SOURCE_REVIEW_EVIDENCE_MISMATCH",
        )
        field, quote_id, relation = (row[key] for key in ("field", "quote_id", "source_relation"))
        need(
            type(field) is str
            and field in fields
            and field not in seen
            and type(quote_id) is str
            and quote_id in catalog
            and type(relation) is str
            and bool(relation.strip()),
            "SCRIPT_SOURCE_REVIEW_EVIDENCE_MISMATCH",
        )
        option = catalog[quote_id]
        if side == "candidate" or option.get("schema") == "source-bound-field-presence.v1":
            need(option.get("field") == field, "SCRIPT_SOURCE_REVIEW_EVIDENCE_MISMATCH")
        seen.add(field)
    need(seen == set(fields), "SCRIPT_SOURCE_REVIEW_EVIDENCE_MISMATCH")


def source_review_submission(context):
    """Validate explicit private judgments and citations without upgrading them."""
    material = context.submissions.get(SOURCE_REVIEW_TEMPLATE + ":" + str(context.call_index))
    need(isinstance(material, Mapping) and bool(material), "SCRIPT_SOURCE_BOUND_REPLY_REQUIRED")
    need(SOURCE_REVIEW_REPLY_FIELDS <= material.keys(), "SCRIPT_REPLY_FIELDS_REQUIRED")
    need(len(canonical(material).encode()) <= 512_000, "SCRIPT_RESPONSE_UNBOUNDED")
    decoded = decode_source_review_wire(
        context.wire_body, expected_wire_sha256=context.expected_wire_sha256
    )
    subject = context.protocol_subject
    version = subject.get("prompt_version") if isinstance(subject, Mapping) else None
    need(
        type(version) is str and version in SOURCE_REVIEW_VERSIONS,
        "SCRIPT_SOURCE_REVIEW_VERSION_UNCOVERED",
    )
    data = decoded["data"]
    presence = any(
        type(row) is dict and row.get("schema") == "source-bound-field-presence.v1"
        for row in data["candidate_quote_options"]
    )
    need((version == "4.3-field-presence") == presence, "SCRIPT_SOURCE_REVIEW_VERSION_MISMATCH")
    if decoded["identity_review_context"] is not None:
        need("protagonist_identity_supported" in material, "SCRIPT_REPLY_FIELDS_REQUIRED")
    for key in (
        "decision",
        "core_preserved",
        "core_conflicts",
        "quality_findings",
        "feedback",
        "protagonist_identity_supported",
    ):
        if key in material:
            need(not _review_has_binding(material[key]), "SCRIPT_VERDICT_MUST_BE_AUTHORED")
    for side in ("source_evidence", "candidate_evidence"):
        if type(material[side]) is list:
            for row in material[side]:
                if isinstance(row, Mapping) and "source_relation" in row:
                    need(
                        not _review_has_binding(row["source_relation"]),
                        "SCRIPT_VERDICT_MUST_BE_AUTHORED",
                    )
    payload, _receipt = bind_authored_material(
        material, context.wire_body, expected_wire_sha256=context.expected_wire_sha256
    )
    need(
        set(payload) <= SOURCE_REVIEW_REPLY_FIELDS | {"protagonist_identity_supported"}
        and type(payload["decision"]) is str
        and payload["decision"] in {"pass", "revise", "uncertain"}
        and (payload["core_preserved"] is None or type(payload["core_preserved"]) is bool)
        and all(
            type(payload[key]) is list
            and len(payload[key]) <= 128
            and all(type(item) is str and bool(item.strip()) for item in payload[key])
            for key in ("core_conflicts", "quality_findings")
        )
        and len(payload["quality_findings"]) <= 3
        and all(len(item) <= 120 for item in payload["quality_findings"])
        and type(payload["feedback"]) is str
        and len(payload["feedback"]) <= 200,
        "SCRIPT_SOURCE_REVIEW_MATERIAL_INVALID",
    )
    if "protagonist_identity_supported" in payload:
        need(
            type(payload["protagonist_identity_supported"]) is bool,
            "SCRIPT_SOURCE_REVIEW_MATERIAL_INVALID",
        )
    need(
        all(
            payload[key] == decoded["hashes"][key]
            for key in ("authority_snapshot_hash", "candidate_hash")
        ),
        "SCRIPT_SOURCE_REVIEW_HASH_MISMATCH",
    )
    fields = data["changed_fields"]
    source = _source_review_catalogue(
        data["source_quote_options"], data["authority_and_baseline"], fields, side="source"
    )
    candidate = _source_review_catalogue(
        data["candidate_quote_options"], data["derived_candidates"], fields, side="candidate"
    )
    bindings = {}
    for quote_id, option in candidate.items():
        bindings.setdefault(option["field"], []).append(quote_id)
    need(
        data["candidate_quote_field_bindings"] == bindings, "SCRIPT_SOURCE_REVIEW_EVIDENCE_MISMATCH"
    )
    _validate_source_review_evidence(payload["source_evidence"], source, fields, side="source")
    _validate_source_review_evidence(
        payload["candidate_evidence"], candidate, fields, side="candidate"
    )
    return deepcopy(payload)


def _title_candidates(context):
    titles = _strings(context.story.get("title_candidates"))
    need(
        1 <= len(titles) <= 8 and len(set(titles)) == len(titles), "SCRIPT_TITLE_MATERIAL_REQUIRED"
    )
    return {
        "candidates": [
            {"title": title, "family": "故事实体", "why_click": context.story["logline"]}
            for title in titles
        ]
    }


# Every dispatch is explicit. Repairs are separate names/slots, not wildcard retries.
PRODUCERS = {
    "story_architect_v1": (architect_facets, "json"),
    "story_architect_v1_repair": (architect_facets, "json"),
    "conception_commercial_positioning": (commercial_positioning, "json"),
    "conception_market": (market_proposal, "json"),
    "conception_character": (character_proposal, "json"),
    "conception_world": (world_proposal, "json"),
    "conception_finalize": (finalize_proposal, "json"),
    "conception_story_spine_polish": (story_spine, "json"),
    "conception_golden_finger_polish": (
        lambda c: {
            k: character_proposal(c)[k] for k in ("golden_finger", "growth_curve", "power_tiers")
        },
        "json",
    ),
    "conception_hook_one_liner_adapt": (
        lambda c: {"reader_promise": market_proposal(c)["reader_promise"]},
        "json",
    ),
    "conception_logline_from_champion": (
        lambda c: {"logline": _text(c.story.get("logline"))},
        "json",
    ),
    "conception_logline_regen": (lambda c: _text(c.story.get("logline")), "text"),
    "conception_blurb_polish": (lambda c: _text(c.story.get("synopsis")), "text"),
    "conception_title_polish": (
        lambda c: "\n".join(row["title"] for row in _title_candidates(c)["candidates"]),
        "text",
    ),
    "title_tournament_candidates": (_title_candidates, "json"),
    "title_platform_revision": (lambda c: _text(c.story.get("title")), "text"),
    "title_story_grounded_revision": (lambda c: _text(c.story.get("title")), "text"),
    "planner_book_spec": (book_spec, "asset:book_spec"),
    "planner_world_spec": (world_spec, "asset:world_spec"),
    "planner_cast_spec": (cast_spec, "asset:cast_spec"),
    "planner_volume_plan": (volume_plan, "asset:volume_plan"),
    "chapter_first_writer": (render_chapter, "text"),
}
for _base in (
    "conception_commercial_positioning",
    "conception_market",
    "conception_character",
    "conception_world",
    "conception_finalize",
    "conception_story_spine_polish",
    "conception_golden_finger_polish",
    "conception_hook_one_liner_adapt",
    "conception_logline_from_champion",
):
    PRODUCERS[_base + "_repair"] = PRODUCERS[_base]
for _alias, _base in reader_first_protocols.PRODUCER_ALIASES.items():
    PRODUCERS[_alias] = PRODUCERS[_base]

# The corresponding opinion/extraction is mandatory. No schema-derived verdict,
# grade, fabricated source ID, empty successful extraction or shared sample slot.
BOUND_PROTOCOLS = {
    **dict.fromkeys(l1_required_protocols.VERSIONS, "json"),
    **dict.fromkeys(q4_authored_repairs.PROTOCOLS, "json"),
    **dict.fromkeys(reader_first_protocols.REVIEW_PROTOCOLS, "json"),
    **dict.fromkeys(reader_revision_protocols.PROTOCOLS, "json"),
    "planner_source_bound_expansion_semantic_review": "json",
    "planner_cast_person_part": "json",
    "planner_cast_links_part": "json",
    "conception_review": "json",
    "conception_review_repair": "json",
    "conception_cast_reality_audit": "json",
    "conception_cast_reality_audit_repair": "json",
    "conception_intent_alignment": "tool:submit_intent_review",
    "title_tournament_arena": "json",
    "planning_draft_admission": "tool:submit_planning_admission",
    "chapter_review": "tool:submit_chapter_critique",
    "chapter_commercial_quality_judge": "tool:submit_chapter_review",
    "draft_continuity_hard_safety": "tool:submit_draft_continuity_safety",
    "chapter_window_quality_judge": "json",
    "volume_quality_checkpoint_judge": "json",
    "chapter_feedback_extraction": "json",
    "chapter_feedback_extraction_repair": "json",
    "chapter_state_snapshot": "json",
    "chapter_state_snapshot_repair": "json",
    "bible_update_v1": "json",
    "bible_update_v1_repair": "json",
    "fanqie_short_semantic_review": "json",
    "fanqie_long_semantic_review": "json",
    "fanqie_short_semantic_evidence_selection": "tool:select_whole_semantic_evidence",
    "fanqie_short_title_semantic_judge": "tool:judge_current_publication_title",
    "book_ending": "tool:submit_book_ending",
}
BODY_BOUND_PROTOCOLS = frozenset(
    {
        "chapter_review",
        "chapter_commercial_quality_judge",
        "draft_continuity_hard_safety",
        "chapter_feedback_extraction",
        "chapter_feedback_extraction_repair",
        "chapter_state_snapshot",
        "chapter_state_snapshot_repair",
        "bible_update_v1",
        "bible_update_v1_repair",
    }
)
BOUND_FIELDS = {
    "conception_review": {"overall_coherence_score", "contradictions", "gaps", "premise_seeds"},
    "conception_cast_reality_audit": {
        "reality_audit_notes",
        "reality_audit_findings",
        "corrections",
    },
    "chapter_review": {"verdict", "scores", "rewrite_direction", "commentary", "citations"},
    "chapter_commercial_quality_judge": {
        "wire_version",
        "pass",
        "overall_score",
        "dimension_scores",
        "issues",
        "rewrite_plan",
    },
    "chapter_feedback_extraction": {"character_states", "canon_facts", "timeline_events"},
    "chapter_state_snapshot": {"facts"},
    "bible_update_v1": {"character_updates", "relationship_updates", "world_updates"},
}
for _base in (
    "conception_review",
    "conception_cast_reality_audit",
    "chapter_feedback_extraction",
    "chapter_state_snapshot",
    "bible_update_v1",
):
    BOUND_FIELDS[_base + "_repair"] = BOUND_FIELDS[_base]


def _resolve(template, context):
    if template in PRODUCERS:
        if template in reader_first_protocols.PRODUCER_ALIASES:
            reader_first_protocols.validate_instruction(template, context)
        builder, kind = PRODUCERS[template]
        return builder(context), kind
    if template in BOUND_PROTOCOLS:
        if template in l1_required_protocols.VERSIONS:
            return l1_required_protocols.authored_submission(template, context), "json"
        if template in reader_revision_protocols.PROTOCOLS:
            return reader_revision_protocols.authored_submission(template, context), "json"
        if template in q4_authored_repairs.PROTOCOLS:
            return q4_authored_repairs.authored_submission(template, context), "json"
        if template in reader_first_protocols.REVIEW_PROTOCOLS:
            return reader_first_protocols.review_submission(template, context), "json"
        if template in CAST_PART_PROTOCOLS:
            return cast_part_submission(template, context), "json"
        if template == SOURCE_REVIEW_TEMPLATE:
            return source_review_submission(context), "json"
        return explicit_submission(template, context), BOUND_PROTOCOLS[template]
    if keyflow_protocols.recognizes(template):
        return keyflow_protocols.authored_payload(template, context), "json"
    # Only this reviewed, finite numeric grammar is expanded. Volume one is the
    # current blueprint profile; other volumes and repair suffixes remain RED.
    match = re.fullmatch(
        r"planner_volume_([1-9]\d*)_chapter_outline(?:_batch_([1-9]\d*)_([1-9]\d*))?", template
    )
    if match:
        need(int(match[1]) == 1, "SCRIPT_VOLUME_PROFILE_UNCOVERED")
        start, end = (
            (int(match[2]), int(match[3])) if match[2] else (1, len(_chapters(context.story)))
        )
        return outline_batch(context, start=start, end=end), "json"
    need(False, "SCRIPT_TEMPLATE_UNCOVERED")


def require_builder(template):
    need(type(template) is str, "SCRIPT_TEMPLATE_INVALID")
    need(
        template in PRODUCERS
        or template in BOUND_PROTOCOLS
        or keyflow_protocols.recognizes(template)
        or bool(
            re.fullmatch(
                r"planner_volume_([1-9]\d*)_chapter_outline(?:_batch_([1-9]\d*)_([1-9]\d*))?",
                template,
            )
        ),
        "SCRIPT_TEMPLATE_UNCOVERED",
    )


def build_payload(template, context):
    need(
        type(template) is str and isinstance(context, BuilderContext),
        "SCRIPT_BUILDER_INPUT_INVALID",
    )
    require_builder(template)
    payload, kind = _resolve(template, context)
    canonical(payload)
    return {"format": kind, "payload": payload}


def build_response(template, context, *, catalog, wire_body, usage, limits=DEFAULT_LIMITS):
    """Pure normal-quickstart response; coordinator supplies identity and wire.

    Exact numeric outline names are added to a local catalog copy only after
    the finite range/volume resolver accepted them. No shared registry mutation.
    """
    need(
        isinstance(catalog, TemplateCatalog) and isinstance(wire_body, Mapping),
        "SCRIPT_BUILDER_INPUT_INVALID",
    )
    context = replace(context, wire_body=wire_body)
    result = build_payload(template, context)
    payload, kind = result["payload"], result["format"]
    if template not in catalog.names:
        need(
            (
                template in CAST_PART_PROTOCOLS
                or template in q4_authored_repairs.PROTOCOLS
                or template in reader_first_protocols.PRODUCER_ALIASES
                or template in reader_first_protocols.REVIEW_PROTOCOLS
                or template in reader_revision_protocols.PROTOCOLS
                or template == SOURCE_REVIEW_TEMPLATE
                or template.startswith("planner_volume_")
                or keyflow_protocols.recognizes(template)
            )
            and kind == "json",
            "SCRIPT_TEMPLATE_UNCOVERED",
        )
        inventory = deepcopy(catalog.inventory)
        keyflow = keyflow_protocols.recognizes(template)
        source = (
            (
                "concept_tournament.py"
                if template in keyflow_protocols.TOURNAMENT
                or template in reader_first_protocols.TOURNAMENT_ALIASES
                else "conception.py"
            )
            if keyflow
            else "planner.py"
        )
        if template in CAST_PART_PROTOCOLS:
            source = "source_cast_parts.py"
        function = "_llm_call_json" if keyflow else "_generate_structured_artifact"
        derived = "explicit-authored-keyflow" if keyflow else "bounded-volume-one-outline"
        if template in CAST_PART_PROTOCOLS:
            function, derived = "generate", "explicit-authored-native-cast-part"
        elif template == SOURCE_REVIEW_TEMPLATE:
            function = "_review_source_bound_expansion"
            derived = "explicit-authored-source-expansion-review"
        if template in reader_first_protocols.INSTRUCTION_STAGES:
            tournament = template in reader_first_protocols.TOURNAMENT_ALIASES
            source = "concept_tournament.py" if tournament else "conception.py"
            function = "_default_generator" if tournament else "_llm_call_json"
            derived = "explicit-reader-first-alias"
        elif template in reader_first_protocols.REVIEW_PROTOCOLS:
            skeleton = reader_first_protocols.REVIEW_PROTOCOLS[template][0] == "skeleton"
            source = "conception.py" if skeleton else "planner.py"
            function = (
                "_review_reader_first_skeleton"
                if skeleton
                else "_run_reader_first_outline_checkpoint"
            )
            derived = "explicit-authored-reader-first-review"
        elif template in q4_authored_repairs.PROTOCOLS:
            if template == q4_authored_repairs.ROLES:
                source, function = "fanqie_short_planner.py", "_short_roles_request"
            elif template == q4_authored_repairs.CONCEPTION_REPAIR:
                source, function = "conception.py", "_repair_reader_first_candidate"
            else:
                source, function = "planner.py", "_repair_outline_commercial_findings_surgically"
            derived = "explicit-authored-reader-first-annotation-or-repair"
        elif template in reader_revision_protocols.PROTOCOLS:
            source, function = "reader_judge.py", "_rr_request"
            derived = "explicit-authored-reader-revision"
        inventory["templates"].append(
            {
                "template": template,
                "historical_only": False,
                "phase": (
                    "review"
                    if template in reader_revision_protocols.PROTOCOLS
                    else "conception"
                    if keyflow or source == "conception.py"
                    else "planning"
                ),
                "sites": [
                    {
                        "file": "src/bestseller/services/" + source,
                        "function": function,
                        "derived": derived,
                    }
                ],
            }
        )
        catalog = TemplateCatalog(inventory)
    if kind.startswith("asset:"):
        asset = kind.split(":", 1)[1]
        tools = wire_body.get("tools") or []
        if not tools:
            return text_response(
                catalog,
                template,
                canonical(payload),
                usage=usage,
                finish_reason="stop",
                limits=limits,
            )
        name = "submit_" + asset
        found = [t.get("function", {}) for t in tools if t.get("function", {}).get("name") == name]
        need(len(found) == 1, "SCRIPT_TOOL_UNCOVERED")
        properties = found[0].get("parameters", {}).get("properties", {})
        arguments = (
            {"asset_json": canonical(payload)}
            if "asset_json" in properties
            else ({"volumes": payload} if asset == "volume_plan" else payload)
        )
        return tool_response(
            catalog,
            template,
            wire_body,
            tool_name=name,
            arguments=arguments,
            usage=usage,
            limits=limits,
        )
    if kind.startswith("tool:"):
        return tool_response(
            catalog,
            template,
            wire_body,
            tool_name=kind.split(":", 1)[1],
            arguments=payload,
            usage=usage,
            limits=limits,
        )
    need(not wire_body.get("tools"), "SCRIPT_TOOL_PROTOCOL_UNCOVERED")
    return text_response(
        catalog,
        template,
        canonical(payload) if kind == "json" else payload,
        usage=usage,
        finish_reason="stop",
        limits=limits,
    )


def builder_inventory():
    return {
        "schema": "normal-quickstart-builders.v1",
        "producers": sorted(PRODUCERS),
        "requires_explicit_physical_reply": dict(sorted(BOUND_PROTOCOLS.items())),
        "keyflow_explicit_reply_protocols": sorted(keyflow_protocols.PROTOCOLS),
        "finite_dynamic_family": (
            "planner_volume_1_chapter_outline[_batch_A_B], 1<=A<=B<=chapter_count<=32"
        ),
        "native_business_acceptance": "Unknown",
        "normal_http_driver_included": False,
    }


def blueprint_requirements():
    """Safe shape metadata for the coordinator's private story generator."""
    return {
        "story_fields": sorted(
            {
                "genre",
                "sub_genre",
                "platform",
                "setting",
                "goal",
                "trigger",
                "stakes",
                "advantage",
                "development",
                "content_mode",
                "reader_rationale",
                "career_ledger",
                "profession_boundary",
                "romance_mode",
                "relationship_tension",
                "era",
                "narrative_drive",
                "channel",
                "title",
                "premise",
                "synopsis",
                "theme",
                "logline",
                "tags",
                "audiences",
                "power_tiers",
                "tone_keywords",
                "review_adjudications",
                "title_candidates",
                "rules",
                "locations",
            }
        ),
        "actors": {
            role: ["name", "occupation", "background", "fear", "flaw", "strength", "age", "gender"]
            for role in ("protagonist", "antagonist", "ally")
        },
        "chapter_fields": [
            "number",
            "title",
            "goal",
            "obstacle",
            "choice",
            "consequence",
            "hook",
            "location",
            "reveals",
            "target_word_count",
            "causal_contract",
            "methodology_contract",
            "scenes",
            "prose_atoms",
        ],
        "prose_atom_fields": ["action", "reaction", "result"],
        "runtime_body_reply_binding": [
            "current_body",
            "source_body_sha256",
            "template:physical_index",
        ],
        "profile": "Chinese; one volume; 1..32 chapters; no implicit review opinions",
    }
