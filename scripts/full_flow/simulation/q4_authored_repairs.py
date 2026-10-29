"""Three finite native annotation/repair codecs for private authored material.

Native helpers only index source leaves, describe targets and apply patches to
detached memory for validation. No async workflow, transport installation,
database write, quota decision, receipt promotion or story generation occurs.
"""

from __future__ import annotations

# ruff: noqa: ANN001, ANN201, ANN202 -- Explicit runtime JSON boundary.
from collections.abc import Mapping
from copy import deepcopy
import hashlib

from . import reader_first_protocols as reader
from .contracts import canonical, digest, need
from .wire_material import bind_authored_material

ROLES = "fanqie_reader_first_field_roles"
CONCEPTION_REPAIR = "conception_reader_first_repair_v1"
OUTLINE_REPAIR = "planner_reader_first_full_outline_commercial_surgical_repair"
PROTOCOLS = {
    ROLES: "fanqie-rf-field-roles-v1",
    CONCEPTION_REPAIR: "reader-first-repair.v1",
    OUTLINE_REPAIR: "rf-outline-repair-v1",
}
_ROLE_VALUES = frozenset({"narrative_task", "continuity_reference"})
_ANNOTATION_ERROR = "SCRIPT_READER_FIRST_ANNOTATION_INVALID"
_REPAIR_ERROR = "SCRIPT_READER_FIRST_REPAIR_INVALID"
_COMPLETE_SOURCE = "\nCOMPLETE READER-FIRST SOURCE (immutable facts plus editable outline):\n"
_PATCH_PROTOCOL = "必须遵守以下可编辑目标、字段和类型协议：\n"  # noqa: RUF001 -- Native protocol delimiter.
_DELIVERY_SCHEMA = "approved-current-chapter-delivery.v3-reader-first"


def _planner():
    from bestseller.services import planner

    return planner


def _context(template, context):
    need(template in PROTOCOLS, "SCRIPT_TEMPLATE_UNCOVERED")
    _wire, messages = reader._wire(context)
    marker = reader._expected_binding(context)
    version = PROTOCOLS[template]
    if marker["protocol"] == "reader-first-planning.v2":
        version = {
            ROLES: "fanqie-rf-field-roles-v2",
            OUTLINE_REPAIR: "rf-outline-repair-v2",
            CONCEPTION_REPAIR: "reader-first-repair.v1",
        }[template]
    need(
        context.protocol_subject.get("prompt_version") == version,
        "SCRIPT_READER_FIRST_VERSION_MISMATCH",
    )
    return messages, marker


def _user(messages, code):
    users = [message.get("content") for message in messages if message.get("role") == "user"]
    need(len(users) == 1 and type(users[0]) is str, code)
    return users[0]


def _json(text, code):
    try:
        result = reader._decoder().decode(text.strip())
    except (ValueError, TypeError, RecursionError):
        need(False, code)
    need(type(result) is dict, code)
    return result


def _instruction(messages, marker, stage):
    # These two repair stages have their own native system instructions. Do not
    # extend or mutate the frozen 17-template alias registry to match them.
    instructions = [
        reader._native().reader_first_instruction(marker, language=language, stage=stage)
        for language in ("zh", "en")
    ]
    count = sum(
        text.count(instruction)
        for message in messages
        if message.get("role") in {"system", "developer"}
        for text in [message.get("content")]
        if type(text) is str
        for instruction in instructions
    )
    need(count == 1, "SCRIPT_READER_FIRST_WIRE_INVALID")


def _material(template, context, *, fields, code):
    material = context.submissions.get(template + ":" + str(context.call_index))
    need(isinstance(material, Mapping) and bool(material), "SCRIPT_SOURCE_BOUND_REPLY_REQUIRED")
    need(set(material) == fields, code)
    need(len(canonical(material).encode()) <= 512_000, "SCRIPT_RESPONSE_UNBOUNDED")
    return material


def _bound(material, context):
    payload, _bindings = bind_authored_material(
        material, context.wire_body, expected_wire_sha256=context.expected_wire_sha256
    )
    return payload


def _chapters(outline, code):
    need(type(outline) is dict and type(outline.get("chapters")) is list, code)
    chapters = outline["chapters"]
    need(bool(chapters) and all(type(chapter) is dict for chapter in chapters), code)
    numbers = [chapter.get("chapter_number") for chapter in chapters]
    need(
        all(type(number) is int for number in numbers)
        and sorted(numbers) == list(range(1, len(chapters) + 1)),
        code,
    )
    for chapter in chapters:
        scenes = chapter.get("scenes")
        need(
            type(scenes) is list and bool(scenes) and all(type(scene) is dict for scene in scenes),
            code,
        )
        scene_numbers = [scene.get("scene_number") for scene in scenes]
        need(
            all(type(number) is int for number in scene_numbers)
            and sorted(scene_numbers) == list(range(1, len(scenes) + 1)),
            code,
        )
    return chapters


def annotation_submission(context):
    """Verify the entire native leaf catalogue and explicitly authored roles."""
    messages, marker = _context(ROLES, context)
    code = _ANNOTATION_ERROR
    document = _json(_user(messages, code), code)
    need(
        set(document)
        == {
            "schema",
            "reader_first_planning",
            "source_sha256",
            "outline",
            "chapter_leaf_values",
            "annotation_attempt",
            "rejected_response",
        },
        code,
    )
    need(
        document["schema"] == "fanqie-reader-first-field-roles-request.v1"
        and document["reader_first_planning"] == marker,
        code,
    )
    attempt = document["annotation_attempt"]
    rejected = document["rejected_response"]
    need(type(attempt) is int and attempt in (0, 1), code)
    need(
        (attempt == 0 and rejected is None)
        or (attempt == 1 and type(rejected) is str and bool(rejected.strip())),
        code,
    )
    outline = document["outline"]
    chapters = _chapters(outline, code)
    need(4 <= len(chapters) <= 8 and document["source_sha256"] == digest(outline), code)
    leaves = {
        str(chapter["chapter_number"]): _planner()._reader_first_leaf_values(chapter)
        for chapter in chapters
    }
    need(canonical(document["chapter_leaf_values"]) == canonical(leaves), code)
    material = _material(ROLES, context, fields={"schema", "source_sha256", "chapters"}, code=code)
    rows = material["chapters"]
    need(type(rows) is list and len(rows) == len(chapters), code)
    for row in rows:
        need(type(row) is dict and set(row) == {"chapter_number", "field_roles"}, code)
        need(not reader._has_binding(row["field_roles"]), "SCRIPT_VERDICT_MUST_BE_AUTHORED")
    payload = _bound(material, context)
    need(
        payload["schema"] == "fanqie-reader-first-field-roles.v1"
        and payload["source_sha256"] == document["source_sha256"],
        code,
    )
    seen = set()
    by_number = {chapter["chapter_number"]: chapter for chapter in chapters}
    for row in payload["chapters"]:
        number, roles = row["chapter_number"], row["field_roles"]
        need(type(number) is int and number in by_number and number not in seen, code)
        seen.add(number)
        need(
            type(roles) is dict
            and set(roles) == set(leaves[str(number)])
            and all(type(role) is str and role in _ROLE_VALUES for role in roles.values()),
            code,
        )
        # Check existing custody, without adding metadata to the native outline.
        meta = by_number[number].get("_meta", {})
        need(type(meta) is dict, code)
        for key, expected in {
            "reader_first_planning": marker,
            "writer_delivery_schema": _DELIVERY_SCHEMA,
            "reader_first_field_roles": roles,
        }.items():
            need(key not in meta or meta[key] == expected, code)
    need(seen == set(by_number), code)
    return deepcopy(payload)


def _failed_review(value, source_hash, code):
    need(
        type(value) is dict
        and value.get("status") == "failed"
        and value.get("source_sha256") == source_hash,
        code,
    )


def _complete_candidate(before, after, *, depth=0):
    code = _REPAIR_ERROR
    need(depth <= 64 and type(before) is type(after), code)
    if type(before) is dict:
        need(set(before) <= set(after), code)
        for key, value in before.items():
            _complete_candidate(value, after[key], depth=depth + 1)
    elif type(before) is list:
        need(len(after) >= len(before), code)
        for index, value in enumerate(before):
            _complete_candidate(value, after[index], depth=depth + 1)


def conception_repair_submission(context):
    """Return an explicit complete candidate; preserve native frozen controls."""
    messages, marker = _context(CONCEPTION_REPAIR, context)
    _instruction(messages, marker, "conception_repair")
    code = _REPAIR_ERROR
    document = _json(_user(messages, code), code)
    need(
        set(document)
        == {"binding", "source_sha256", "config_sha256", "source", "review", "frozen_controls"},
        code,
    )
    need(
        document["binding"] == marker and document["config_sha256"] == marker["config_sha256"], code
    )
    source = document["source"]
    need(
        type(source) is dict
        and set(source) == {"original_user_input", "creation_inputs", "candidate"},
        code,
    )
    original_input = source["original_user_input"]
    need(
        type(original_input) is str
        and bool(original_input.strip())
        and hashlib.sha256(original_input.encode()).hexdigest() == marker["user_input_sha256"]
        and type(source["creation_inputs"]) is dict
        and type(source["candidate"]) is dict,
        code,
    )
    source_hash = digest(source)
    need(document["source_sha256"] == source_hash, code)
    frozen = {key: source[key] for key in ("original_user_input", "creation_inputs")}
    need(canonical(document["frozen_controls"]) == canonical(frozen), code)
    _failed_review(document["review"], source_hash, code)
    material = _material(
        CONCEPTION_REPAIR,
        context,
        fields={
            "schema",
            "source_sha256",
            "config_sha256",
            "user_input_sha256",
            "frozen_controls",
            "candidate",
        },
        code=code,
    )
    need(
        not reader._has_binding(material["candidate"]),
        "SCRIPT_READER_FIRST_REPAIR_MUST_BE_AUTHORED",
    )
    payload = _bound(material, context)
    need(
        payload["schema"] == "reader-first-conception-repair.v1"
        and payload["source_sha256"] == source_hash
        and payload["config_sha256"] == marker["config_sha256"]
        and payload["user_input_sha256"] == marker["user_input_sha256"]
        and canonical(payload["frozen_controls"]) == canonical(frozen),
        code,
    )
    candidate, original = payload["candidate"], source["candidate"]
    need(type(candidate) is dict and bool(original) and set(candidate) == set(original), code)
    need(
        {
            "writing_profile",
            "world_model",
            "story_spine",
            "genre_intent_contract",
            "selected_hook_spec",
        }
        <= original.keys(),
        code,
    )
    _complete_candidate(original, candidate)
    for key, value in original.items():
        need(not (type(value) is str and value.strip()) or bool(candidate[key].strip()), code)
    for key in ("genre_intent_contract", "selected_hook_spec"):
        need(canonical(candidate[key]) == canonical(original[key]), code)
    need(
        type(candidate["writing_profile"]) is dict
        and candidate["writing_profile"].get("reader_first_planning") == marker,
        code,
    )
    need(candidate != original, code)
    return deepcopy(payload)


def _annotated_chapters(outline, marker):
    code = _REPAIR_ERROR
    chapters = _chapters(outline, code)
    for chapter in chapters:
        meta = chapter.get("_meta")
        need(type(meta) is dict, code)
        roles = meta.get("reader_first_field_roles")
        need(
            meta.get("reader_first_planning") == marker
            and meta.get("writer_delivery_schema") == _DELIVERY_SCHEMA
            and type(roles) is dict
            and set(roles) == set(_planner()._reader_first_leaf_values(chapter))
            and all(type(role) is str and role in _ROLE_VALUES for role in roles.values()),
            code,
        )
    return chapters


def decode_outline_repair_wire(context):
    """Decode only the native short JSON envelope or normal full-source tail."""
    messages, marker = _context(OUTLINE_REPAIR, context)
    code = _REPAIR_ERROR
    text = _user(messages, code)
    if text.lstrip().startswith("{"):
        document = _json(text, code)
        fields = {"schema", "source", "source_sha256", "reader_review", "patch_protocol"}
        if marker["protocol"] == "reader-first-planning.v2":
            fields.add("reader_first_planning")
            need(document.get("reader_first_planning") == marker, code)
        need(
            set(document) == fields
            and document["schema"] == "fanqie-reader-first-outline-repair.v1",
            code,
        )
        source, protocol = document["source"], document["patch_protocol"]
        need(
            type(source) is dict
            and {"selected_source", "original_beat", "base_outline", "outline"} <= source.keys(),
            code,
        )
        source_hash = digest(source)
        need(document["source_sha256"] == source_hash, code)
        _failed_review(document["reader_review"], source_hash, code)
        short = True
    else:
        need(marker["protocol"] == "reader-first-planning.v1", code)
        _instruction(messages, marker, "outline_repair")
        need(text.count(_COMPLETE_SOURCE) == 1 and text.count(_PATCH_PROTOCOL) == 1, code)
        prefix, tail = text.split(_COMPLETE_SOURCE)
        source = _json(tail, code)
        need(
            {
                "book_spec",
                "world_spec",
                "cast_spec",
                "volume_plan",
                "outline",
                "premise",
                "original_input_binding",
            }
            <= source.keys()
            and source["original_input_binding"] == marker,
            code,
        )
        protocol_text = prefix.split(_PATCH_PROTOCOL, 1)[1].lstrip()
        try:
            protocol, _end = reader._decoder().raw_decode(protocol_text)
        except (ValueError, TypeError, RecursionError):
            need(False, code)
        source_hash, short = digest(source), False
    need(context.protocol_subject.get("source_sha256") == source_hash, code)
    chapters = _annotated_chapters(source["outline"], marker)
    need(not short or 4 <= len(chapters) <= 8, code)
    need(
        canonical(protocol) == canonical(_planner()._outline_patch_protocol(source["outline"])),
        code,
    )
    return {
        "source": deepcopy(source),
        "source_sha256": source_hash,
        "patch_protocol": deepcopy(protocol),
        "reader_first_planning": marker,
        "route": "short-json" if short else "normal-text",
    }


def _metadata(value, path=""):
    if type(value) is dict:
        result = {}
        for key, child in value.items():
            pointer = path + "/" + key.replace("~", "~0").replace("/", "~1")
            if key == "_meta":
                result[pointer] = child
            else:
                result.update(_metadata(child, pointer))
        return result
    if type(value) is list:
        return {
            pointer: metadata
            for index, child in enumerate(value)
            for pointer, metadata in _metadata(child, path + "/" + str(index)).items()
        }
    return {}


def outline_repair_submission(context):
    """Validate explicit patches in detached memory; return patches, not state."""
    document = decode_outline_repair_wire(context)
    code = _REPAIR_ERROR
    material = _material(OUTLINE_REPAIR, context, fields={"patches"}, code=code)
    need(
        not reader._has_binding(material["patches"]), "SCRIPT_READER_FIRST_REPAIR_MUST_BE_AUTHORED"
    )
    patches = material["patches"]
    need(
        type(patches) is list and 1 <= len(patches) <= document["patch_protocol"]["max_patches"],
        code,
    )
    outline = document["source"]["outline"]
    chapters = {chapter["chapter_number"]: chapter for chapter in outline["chapters"]}
    for patch in patches:
        need(type(patch) is dict, code)
        scope = patch.get("scope")
        need(type(scope) is str and scope in {"chapter", "causal_contract", "scene"}, code)
        fields = {"chapter_number", "scope", "field", "value"} | (
            {"scene_number"} if scope == "scene" else set()
        )
        need(
            set(patch) == fields
            and type(patch["chapter_number"]) is int
            and patch["chapter_number"] in chapters
            and type(patch["field"]) is str,
            code,
        )
        chapter = chapters[patch["chapter_number"]]
        target = chapter if scope == "chapter" else chapter.get("causal_contract")
        if scope == "scene":
            need(type(patch["scene_number"]) is int, code)
            target = next(
                (
                    scene
                    for scene in chapter["scenes"]
                    if scene["scene_number"] == patch["scene_number"]
                ),
                None,
            )
        need(
            type(target) is dict
            and patch["field"] in target
            and patch["field"] in document["patch_protocol"]["fields"][scope],
            code,
        )
    planner = _planner()
    try:
        proposed = planner._apply_outline_commercial_patches(
            deepcopy(outline), deepcopy(patches), strict=True
        )
    except (
        planner.PlannerFallbackError,
        ValueError,
        TypeError,
        KeyError,
        IndexError,
        AttributeError,
        RecursionError,
    ):
        need(False, code)
    need(canonical(_metadata(outline)) == canonical(_metadata(proposed)), code)
    old_chapters = _annotated_chapters(outline, document["reader_first_planning"])
    new_chapters = _annotated_chapters(proposed, document["reader_first_planning"])
    need(len(old_chapters) == len(new_chapters), code)
    for before, after in zip(old_chapters, new_chapters, strict=True):
        need(before["chapter_number"] == after["chapter_number"], code)
        old, new = (
            planner._reader_first_leaf_values(before),
            planner._reader_first_leaf_values(after),
        )
        need(set(old) == set(new), code)
        for path, role in before["_meta"]["reader_first_field_roles"].items():
            if role == "continuity_reference":
                need(canonical(old[path]) == canonical(new[path]), code)
    need(canonical(outline) != canonical(proposed), code)
    return deepcopy(dict(material))


def authored_submission(template, context):
    need(template in PROTOCOLS, "SCRIPT_TEMPLATE_UNCOVERED")
    if template == ROLES:
        return annotation_submission(context)
    if template == CONCEPTION_REPAIR:
        return conception_repair_submission(context)
    return outline_repair_submission(context)
