"""Compile explicit private four-part authoring into finite SDK configuration.

This is a file compiler, not an application/HTTP/test runner. It never creates
native state or invents judgments. Missing codecs/material produce a BLOCKED
bundle containing script.pending.json, never the executable script.json name.
All manuscript/input/reply bytes must enter from a private authoring document.
"""

from __future__ import annotations

# ruff: noqa: ANN001, ANN201, ANN202 -- Private JSON compiler boundary.
import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import stat

from .contracts import ScriptContractError, canonical, digest, need
from . import l1_required_protocols
from .quickstart_builders import (
    BODY_BOUND_PROTOCOLS,
    BuilderContext,
    blueprint_requirements,
    builder_inventory,
    render_chapter,
    require_builder,
)
from .wire_material import pointer

SCHEMA = "normal-l1-private-authoring.v1"
PROFILE = "ordinary-urban-family-four-part.reader-first.v1"
MAX_BYTES = 32_000_000
# Source-reachable lower bound, NOT an inferred full execution order/count.
# Native complete/research branches and repair decisions need explicit slots.
REQUIRED_TEMPLATES = {
    "concept_methodology": "concept_methodology_agent.py::select_concept_methodology",
    "conception_source_effect_contract": "conception.py::run_conception_pipeline",
    "conception_finalize_reader_first_v1": "conception.py::run_conception_pipeline",
    "conception_reader_first_skeleton_v1": "conception.py::_review_reader_first_skeleton",
    "planner_fanqie_beat_sheet": "fanqie_beat_recovery.py::build_current_request",
    "fanqie_reader_first_field_roles": "fanqie_short_planner.py::_short_roles_request",
    "planner_reader_first_outline_review": "planner.py::_run_reader_first_outline_checkpoint",
    "reader_judge": "pipelines.py::judge_chapter_readability (enabled default)",
}
WRITER_TEMPLATES = ("chapter_first_writer", "scene_writer")
CONDITIONAL_TEMPLATES = {
    "planner_structured_field_correction": "missing short-outline fields",
    "planner_cast_field_correction": "native Cast field repair",
    "planner_source_bound_expansion_evidence_repair": "invalid expansion evidence",
    "prewrite_plan_manifest": "scene-generation path",
    "scene_writer": "scene-generation or deslop revision path",
}
ROW_FIELDS = frozenset(
    {
        "template",
        "template_ordinal",
        "global_ordinal",
        "native_operation_key",
        "native_input_hash",
        "wire_sha256",
        "wire_assertions",
        "protocol_subject",
        "chapter_number",
        "current_body",
        "source_body_sha256",
        "material",
    }
)
OPINIONS = frozenset(
    {
        "passed",
        "pass",
        "verdict",
        "decision",
        "status",
        "classification",
        "score",
        "scores",
        "dimension_scores",
        "confidence",
        "result",
        "reason",
        "issues",
        "options",
        "preserved",
        "voice_preserved",
        "no_system_procedure_intrusion",
        "core_preserved",
        "core_conflicts",
        "quality_findings",
        "feedback",
        "kind",
        "narrative_function",
    }
)
NATIVE_SOURCES = (
    "scripts/full_flow/web_entry.py",
    "src/bestseller/web/server.py",
    "src/bestseller/services/conception.py",
    "src/bestseller/services/concept_tournament.py",
    "src/bestseller/services/planner.py",
    "src/bestseller/services/production_strategy.py",
    "src/bestseller/services/fanqie_short_pipeline.py",
    "src/bestseller/services/fanqie_short_planner.py",
    "src/bestseller/services/fanqie_beat_recovery.py",
    "src/bestseller/services/fanqie_outline_contract_completion.py",
    "src/bestseller/services/source_bound_planning_content.py",
    "src/bestseller/services/reader_judge.py",
    "src/bestseller/services/pipelines.py",
    "src/bestseller/domain/fanqie_short.py",
    "config/quality_gates.yaml",
    "config/methodology_profiles/reader_first_planning_v1.yaml",
)
QUICKSTART_FIELDS = frozenset(
    {
        "creation_mode",
        "length_key",
        "pov",
        "genre_key",
        "selection",
        "production_profile",
        "stop_after_conception",
        "draft_mode",
        "chapter_count",
        "llm_model_id",
        "concept_seed",
        "client_schema_version",
        "client_intent_manifest",
        "story_enhancers",
        "creative_key",
        "audience_orientation",
        "narrative_scale",
        "tone_preference",
        "autonomous_book",
        "execution_budget_grant_id",
    }
)


def authoring_contract():
    """Return field requirements, never story text or generated good scores."""
    return {
        "schema": SCHEMA,
        "profile": PROFILE,
        "required": [
            "schema",
            "profile",
            "original_input",
            "quickstart",
            "story",
            "execution_plan",
            "replies",
        ],
        "story": blueprint_requirements(),
        "chapter_count": 4,
        "prose_atom_recipe": "Explicit string or ordered list of private strings / "
        "{story_pointer: JSON Pointer}; no repetition or padding",
        "execution_plan_row": ["template", "template_ordinal", "prompt_version", "source_site"],
        "reply_fields": sorted(ROW_FIELDS),
        "reply_required": ["template", "template_ordinal", "chapter_number"],
        "reply_wire_binding": "wire_sha256 OR nonempty wire_assertions; exact current wire only",
        "reply_material": "Non-producers require explicit nonempty material per durable slot",
        "source_reachable_lower_bound": REQUIRED_TEMPLATES,
        "native_writer_route_one_of": list(WRITER_TEMPLATES),
        "conditional_missing_candidates": CONDITIONAL_TEMPLATES,
        "unknown_template": "SCRIPT_TEMPLATE_UNCOVERED",
        "native_acceptance": "Unknown; main owns route selection, native checks and execution",
    }


def _text(value):
    need(
        type(value) is str and bool(value.strip()) and len(value) <= 16_000,
        "SCRIPT_AUTHORING_TEXT_REQUIRED",
    )
    return value


def _integer(value, maximum=4096):
    need(type(value) is int and 1 <= value <= maximum, "SCRIPT_AUTHORING_INTEGER_REQUIRED")
    return value


def _hash(value):
    return type(value) is str and bool(re.fullmatch(r"[0-9a-f]{64}", value))


def _blueprint(source):
    need(type(source) is dict, "SCRIPT_STORY_BLUEPRINT_REQUIRED")
    story = deepcopy(source)
    required = blueprint_requirements()
    arrays = {
        "audiences",
        "locations",
        "power_tiers",
        "review_adjudications",
        "rules",
        "tags",
        "title_candidates",
        "tone_keywords",
    }
    need(set(required["story_fields"]) <= story.keys(), "SCRIPT_STORY_MATERIAL_REQUIRED")
    for key in required["story_fields"]:
        if key in arrays:
            need(
                type(story[key]) is list and len(story[key]) <= 128,
                "SCRIPT_STORY_MATERIAL_REQUIRED",
            )
            need(
                bool(story[key]) or key in {"power_tiers", "review_adjudications"},
                "SCRIPT_STORY_MATERIAL_REQUIRED",
            )
        else:
            _text(story[key])
    for role, fields in required["actors"].items():
        person = story.get(role)
        need(type(person) is dict and set(fields) <= person.keys(), "SCRIPT_CAST_MATERIAL_REQUIRED")
        for key in fields:
            if key not in {"age", "gender"}:
                _text(person[key])
        need(
            type(person["age"]) is int and 0 <= person["age"] <= 200,
            "SCRIPT_CAST_MATERIAL_REQUIRED",
        )
        need(
            person["gender"] in {"male", "female", "nonbinary", "unknown"},
            "SCRIPT_CAST_MATERIAL_REQUIRED",
        )
    chapters = story.get("chapters")
    need(type(chapters) is list and len(chapters) == 4, "SCRIPT_AUTHORING_FOUR_PART_REQUIRED")
    bodies = {}
    for number, chapter in enumerate(chapters, 1):
        need(
            type(chapter) is dict and set(required["chapter_fields"]) <= chapter.keys(),
            "SCRIPT_CHAPTER_PLAN_REQUIRED",
        )
        need(
            type(chapter["number"]) is int and chapter["number"] == number,
            "SCRIPT_CHAPTER_PLAN_REQUIRED",
        )
        _integer(chapter["target_word_count"], 20_000)
        for key in ("title", "goal", "obstacle", "choice", "consequence", "hook", "location"):
            _text(chapter[key])
        need(type(chapter["reveals"]) is list, "SCRIPT_CHAPTER_PLAN_REQUIRED")
        for value in chapter["reveals"]:
            _text(value)
        for key in ("causal_contract", "methodology_contract"):
            need(
                type(chapter[key]) is dict and bool(chapter[key]),
                "SCRIPT_OUTLINE_SOURCE_CONTRACT_REQUIRED",
            )
        need(
            type(chapter["scenes"]) is list and bool(chapter["scenes"]),
            "SCRIPT_OUTLINE_SOURCE_CONTRACT_REQUIRED",
        )
        atoms = chapter["prose_atoms"]
        need(type(atoms) is list and 1 <= len(atoms) <= 128, "SCRIPT_PROSE_MATERIAL_REQUIRED")
        for atom in atoms:
            need(
                type(atom) is dict and set(atom) == {"action", "reaction", "result"},
                "SCRIPT_PROSE_MATERIAL_REQUIRED",
            )
            for key, recipe in atom.items():
                if type(recipe) is list:
                    need(1 <= len(recipe) <= 64, "SCRIPT_PROSE_MATERIAL_REQUIRED")
                    parts = []
                    for token in recipe:
                        if type(token) is dict:
                            need(set(token) == {"story_pointer"}, "SCRIPT_AUTHORING_RECIPE_INVALID")
                            # Only explicit immutable story leaves; no eval/format or wire access.
                            need(
                                type(token["story_pointer"]) is str
                                and not token["story_pointer"].startswith("/chapters"),
                                "SCRIPT_AUTHORING_RECIPE_INVALID",
                            )
                            token = pointer(source, token["story_pointer"])
                        parts.append(_text(token))
                    atom[key] = "".join(parts)
                _text(atom[key])
        bodies[f"chapter-{number:02d}.txt"] = render_chapter(
            BuilderContext(story, chapter_number=number)
        )
    return story, bodies


def _wire_recipes(value, *, opinion=False, depth=0):
    need(depth <= 64, "SCRIPT_AUTHORED_MATERIAL_UNBOUNDED")
    if type(value) is dict:
        if "$wire" in value:
            need(not opinion, "SCRIPT_VERDICT_MUST_BE_AUTHORED")
            need(
                set(value) <= {"$wire", "decode_json", "pointer", "quote", "key_index"},
                "SCRIPT_WIRE_BINDING_INVALID",
            )
            for key in ("$wire", "pointer"):
                if key in value:
                    need(
                        type(value[key]) is str and (not value[key] or value[key].startswith("/")),
                        "SCRIPT_WIRE_POINTER_INVALID",
                    )
            if "decode_json" in value:
                need(type(value["decode_json"]) is bool, "SCRIPT_WIRE_BINDING_INVALID")
            if "quote" in value:
                _text(value["quote"])
            if "key_index" in value:
                need(
                    type(value["key_index"]) is int and value["key_index"] >= 0,
                    "SCRIPT_WIRE_BINDING_INVALID",
                )
            return
        for key, item in value.items():
            _wire_recipes(
                item, opinion=opinion or key in OPINIONS or key.endswith("_score"), depth=depth + 1
            )
    elif type(value) is list:
        for item in value:
            _wire_recipes(item, opinion=opinion, depth=depth + 1)


def _slot(row):
    need(type(row) is dict, "SCRIPT_PRIVATE_REPLY_SLOT_REQUIRED")
    template = row.get("template")
    need(
        type(template) is str and bool(re.fullmatch(r"[A-Za-z0-9_]+", template)),
        "SCRIPT_TEMPLATE_INVALID",
    )
    return template, _integer(row.get("template_ordinal"))


def _quickstart(document):
    value = deepcopy(document["quickstart"])
    need(type(value) is dict, "SCRIPT_AUTHORING_QUICKSTART_INVALID")
    need(
        value.get("creation_mode") == "fanqie_short"
        and value.get("length_key") == "fanqie-short-8k"
        and value.get("pov") in {"first_person", "third_limited"}
        and value.get("production_profile") in {"complete", "research"}
        and value.get("stop_after_conception") is False
        and value.get("draft_mode") is False,
        "SCRIPT_AUTHORING_QUICKSTART_INVALID",
    )
    if "chapter_count" in value:
        need(
            type(value["chapter_count"]) is int and value["chapter_count"] == 4,
            "SCRIPT_AUTHORING_FOUR_PART_REQUIRED",
        )
    need(
        not {
            "project_slug",
            "slug",
            "new_book_budget_grant",
            "grade",
            "current",
            "approved",
            "approval",
            "promoted",
            "configuration_submission",
        }
        & value.keys(),
        "SCRIPT_AUTHORING_NATIVE_STATE_FORBIDDEN",
    )
    need(set(value) <= QUICKSTART_FIELDS, "SCRIPT_AUTHORING_QUICKSTART_FIELD_UNCOVERED")
    need(
        bool(value.get("genre_key"))
        or (type(value.get("selection")) is dict and bool(value["selection"])),
        "SCRIPT_AUTHORING_QUICKSTART_INVALID",
    )
    _text(value.get("llm_model_id"))  # Main chooses an actual available normal model route.
    seed = _text(document["original_input"])
    need(
        seed == seed.strip() and len(seed) <= 500 and value.get("concept_seed") == seed,
        "SCRIPT_AUTHORING_ORIGINAL_INPUT_MISMATCH",
    )
    expected = {
        "schema_version": "quickstart-client.v2",
        "concept_seed_present": True,
        "concept_seed_length": len(seed.encode("utf-8")),
        "concept_seed_hash": hashlib.sha256(seed.encode("utf-8")).hexdigest(),
    }
    need(
        value.get("client_schema_version", "quickstart-client.v2") == "quickstart-client.v2"
        and value.get("client_intent_manifest", expected) == expected,
        "SCRIPT_AUTHORING_CLIENT_MANIFEST_MISMATCH",
    )
    # A checksum of the supplied input, never a native approval/grade receipt.
    value["client_schema_version"] = "quickstart-client.v2"
    value["client_intent_manifest"] = expected
    return value


def compile_private_bundle(document):
    """Pure compilation; reports incomplete private material without filling it.

    The caller authors every finite slot and its source/version. The lower-bound
    check cannot prove all dynamic native calls; normal coverage stays Unknown.
    No native business parser, transport, application, DB or LLM is invoked.
    """
    need(
        type(document) is dict
        and set(document) == set(authoring_contract()["required"])
        and document["schema"] == SCHEMA
        and document["profile"] == PROFILE,
        "SCRIPT_AUTHORING_SCHEMA",
    )
    need(len(canonical(document).encode()) <= MAX_BYTES, "SCRIPT_DOCUMENT_UNBOUNDED")
    quickstart = _quickstart(document)
    story, bodies = _blueprint(document["story"])
    inventory = builder_inventory()
    producers = set(inventory["producers"])
    known = (
        producers
        | set(inventory["requires_explicit_physical_reply"])
        | set(inventory["keyflow_explicit_reply_protocols"])
    )
    plan, replies = document["execution_plan"], document["replies"]
    need(
        type(plan) is list
        and 1 <= len(plan) <= 4096
        and type(replies) is list
        and len(replies) <= 4096,
        "SCRIPT_ENTRY_COUNT_UNBOUNDED",
    )
    blockers, planned, seen, output_rows = [], {}, set(), []

    def block(code, **metadata: object):
        blockers.append({"code": code, **metadata})

    for row in plan:
        key = _slot(row)
        need(
            set(row) == {"template", "template_ordinal", "prompt_version", "source_site"},
            "SCRIPT_AUTHORING_PLAN_INVALID",
        )
        need(
            (row["prompt_version"] is None and key[0] in l1_required_protocols.UNSET_VERSION_TEMPLATES)
            or (type(row["prompt_version"]) is str and bool(row["prompt_version"].strip())),
            "SCRIPT_AUTHORING_PROTOCOL_VERSION_INVALID",
        )
        _text(row["source_site"])
        need(key not in planned, "SCRIPT_AUTHORING_DUPLICATE_SLOT")
        planned[key] = row
        try:
            require_builder(key[0])
            match = re.fullmatch(
                r"planner_volume_(\d+)_chapter_outline(?:_batch_(\d+)_(\d+))?", key[0]
            )
            if match:
                need(
                    match[1] == "1"
                    and (match[2] is None or 1 <= int(match[2]) <= int(match[3]) <= 4),
                    "SCRIPT_OUTLINE_RANGE_INVALID",
                )
                producers.add(key[0])
        except ScriptContractError as exc:
            block(exc.code, template=key[0], template_ordinal=key[1])
    templates = {key[0] for key in planned}
    for template in templates:
        ordinals = sorted(key[1] for key in planned if key[0] == template)
        need(
            ordinals == list(range(1, len(ordinals) + 1)),
            "SCRIPT_AUTHORING_NONCONTIGUOUS_TEMPLATE_SLOTS",
        )
    for template, site in REQUIRED_TEMPLATES.items():
        if template not in templates:
            block(
                "SCRIPT_AUTHORING_REQUIRED_TEMPLATE_SLOT_MISSING",
                template=template,
                source_site=site,
            )
        if template not in known and template not in templates:
            block("SCRIPT_TEMPLATE_UNCOVERED", template=template)
    if not templates.intersection(WRITER_TEMPLATES):
        block("SCRIPT_AUTHORING_WRITER_ROUTE_MISSING", alternatives=list(WRITER_TEMPLATES))
    global_slots = set()
    for row in replies:
        key = _slot(row)
        need(
            set(row) <= ROW_FIELDS and key not in seen,
            "SCRIPT_AUTHORING_DUPLICATE_OR_INVALID_REPLY",
        )
        need(key in planned, "SCRIPT_AUTHORING_UNPLANNED_REPLY")
        seen.add(key)
        _integer(row.get("chapter_number"), 4)
        if "global_ordinal" in row:
            ordinal = _integer(row["global_ordinal"])
            need(ordinal not in global_slots, "SCRIPT_AUTHORING_DUPLICATE_GLOBAL_SLOT")
            global_slots.add(ordinal)
        for field in (
            "wire_sha256",
            "native_operation_key",
            "native_input_hash",
            "source_body_sha256",
        ):
            if field in row:
                need(_hash(row[field]), "SCRIPT_AUTHORING_HASH_INVALID")
        assertions = row.get("wire_assertions", [])
        need(type(assertions) is list, "SCRIPT_WIRE_ASSERTION_INVALID")
        if not row.get("wire_sha256") and not assertions:
            block("SCRIPT_SOURCE_WIRE_MATCH_REQUIRED", template=key[0], template_ordinal=key[1])
        for assertion in assertions:
            need(
                type(assertion) is dict
                and set(assertion)
                in ({"pointer", "sha256"}, {"pointer", "equals"}, {"pointer", "contains"}),
                "SCRIPT_WIRE_ASSERTION_INVALID",
            )
            need(
                type(assertion["pointer"]) is str and assertion["pointer"].startswith("/"),
                "SCRIPT_WIRE_POINTER_INVALID",
            )
            if "sha256" in assertion:
                need(_hash(assertion["sha256"]), "SCRIPT_AUTHORING_HASH_INVALID")
            if "contains" in assertion:
                _text(assertion["contains"])
        if key[0] not in producers and not (type(row.get("material")) is dict and row["material"]):
            block("SCRIPT_SOURCE_BOUND_REPLY_REQUIRED", template=key[0], template_ordinal=key[1])
        if key[0] in BODY_BOUND_PROTOCOLS and type(row.get("current_body")) is not dict:
            block("SCRIPT_CURRENT_BODY_BINDING_REQUIRED", template=key[0], template_ordinal=key[1])
        if "reader_first" in key[0] or key[0] in {
            "reader_revision_anchors",
            "reader_revision_preservation",
        }:
            subject = row.get("protocol_subject")
            if not (type(subject) is dict and type(subject.get("reader_first_planning")) is dict):
                block(
                    "SCRIPT_READER_FIRST_BINDING_REQUIRED", template=key[0], template_ordinal=key[1]
                )
            elif subject.get("prompt_version") != planned[key]["prompt_version"]:
                block(
                    "SCRIPT_AUTHORING_PROTOCOL_VERSION_MISMATCH",
                    template=key[0],
                    template_ordinal=key[1],
                )
        if key[0] in l1_required_protocols.VERSIONS:
            subject = row.get("protocol_subject")
            if not (type(subject) is dict and "prompt_version" in subject
                    and subject["prompt_version"] == planned[key]["prompt_version"]):
                block("SCRIPT_AUTHORING_PROTOCOL_VERSION_MISMATCH",
                      template=key[0], template_ordinal=key[1])
        _wire_recipes(row)
        output_rows.append(deepcopy(row))
    for key in sorted(planned.keys() - seen):
        block("SCRIPT_AUTHORED_REPLY_NOT_UNIQUE", template=key[0], template_ordinal=key[1])
    config = {"schema": "normal-quickstart-private.v1", "story": story, "replies": output_rows}
    report = {
        "schema": "normal-l1-private-authoring-result.v1",
        "state": "BLOCKED" if blockers else "STRUCTURE_READY_FOR_MAIN_REVIEW",
        "blockers": blockers,
        "literal_registry_count": len(known),
        "planned_slots": len(plan),
        "authored_slots": len(replies),
        "config_sha256": digest(config),
        "input_sha256": digest(document),
        "registry_sha256": digest(inventory),
        "execution_coverage": "Unknown",
        "native_acceptance": "Unknown",
        "execution_started_by_authoring": False,
        "main_owned_prerequisites": [
            "Verify selected model uses web_entry's exact scripted endpoint",
            "Native new-book execution_budget_grant_id, never a generated authority object",
            "Verify native chapter/scene route and all conditional slots from actual configuration",
            "Normal HTTP orchestration and first-adoption readback/stop boundary",
        ],
        "conditional_template_requirements": CONDITIONAL_TEMPLATES,
        "body_sha256": {
            name: hashlib.sha256(text.encode()).hexdigest() for name, text in bodies.items()
        },
        "private_requirements": authoring_contract(),
    }
    bundle = {
        "config": config,
        "quickstart": quickstart,
        "bodies": bodies,
        "execution_plan": deepcopy(plan),
        "report": report,
    }
    # A custody checksum of the complete compiler output, never native approval.
    report["bundle_sha256"] = digest(bundle)
    return bundle


def _private_path(path, repository_root):
    path, repo = Path(path), Path(repository_root).resolve(strict=True)
    need(path.is_absolute() and ".." not in path.parts, "SCRIPT_PRIVATE_ABSOLUTE_PATH_REQUIRED")
    need(not any(p.is_symlink() for p in (path, *path.parents)), "SCRIPT_PRIVATE_PATH_SYMLINK")
    target = path.resolve()
    need(
        not target.is_relative_to(repo) and not repo.is_relative_to(target),
        "SCRIPT_PRIVATE_ROOT_OVERLAPS_REPOSITORY",
    )
    need(
        not any((p / ".git").exists() for p in (target, *target.parents)),
        "SCRIPT_PRIVATE_INSIDE_CHECKOUT",
    )
    return path


def read_private_authoring(path, *, repository_root):
    path = _private_path(path, repository_root)
    need(stat.S_IMODE(path.parent.stat().st_mode) == 0o700, "SCRIPT_PRIVATE_DIRECTORY_MODE")
    need(stat.S_ISREG(path.stat().st_mode), "SCRIPT_PRIVATE_CONFIG_MODE")
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), "rb") as stream:
        info = os.fstat(stream.fileno())
        need(
            stat.S_ISREG(info.st_mode)
            and stat.S_IMODE(info.st_mode) == 0o600
            and info.st_size <= MAX_BYTES,
            "SCRIPT_PRIVATE_CONFIG_MODE",
        )

        def pairs(items):
            value = {}
            for key, item in items:
                need(key not in value, "SCRIPT_AUTHORING_DUPLICATE_KEY")
                value[key] = item
            return value

        try:
            raw = stream.read(MAX_BYTES + 1)
            need(len(raw) <= MAX_BYTES, "SCRIPT_DOCUMENT_UNBOUNDED")
            value = json.loads(
                raw,
                object_pairs_hook=pairs,
                parse_constant=lambda _: need(False, "SCRIPT_JSON_INVALID"),
            )
        except (ValueError, TypeError, RecursionError) as exc:
            if isinstance(exc, ScriptContractError):
                raise
            raise ScriptContractError("SCRIPT_JSON_INVALID") from None
    return value


def write_private_bundle(bundle, destination, *, repository_root):
    """Create a NEW external 0700 bundle; never touch an existing checkpoint.

    On failure partial files stay private. script.json is written last, only
    after structural compilation without blockers; native acceptance is Unknown.
    """
    root = _private_path(destination, repository_root)
    need(root.parent.is_dir() and not root.exists(), "SCRIPT_PRIVATE_DESTINATION_EXISTS")
    need(
        type(bundle) is dict
        and set(bundle) == {"config", "quickstart", "bodies", "execution_plan", "report"}
        and type(bundle["bodies"]) is dict
        and set(bundle["bodies"]) == {f"chapter-{n:02d}.txt" for n in range(1, 5)}
        and bundle["report"]["config_sha256"] == digest(bundle["config"]),
        "SCRIPT_AUTHORING_BUNDLE_CHANGED",
    )
    original = deepcopy(bundle)
    seal = original["report"].pop("bundle_sha256", None)
    need(type(seal) is str and seal == digest(original), "SCRIPT_AUTHORING_BUNDLE_CHANGED")
    payloads = {
        "blueprint.json": canonical(bundle["config"]["story"]) + "\n",
        "replies.json": canonical(bundle["config"]["replies"]) + "\n",
        "quickstart.request.json": canonical(bundle["quickstart"]) + "\n",
        "execution-plan.json": canonical(bundle["execution_plan"]) + "\n",
        **bundle["bodies"],
    }
    report = deepcopy(bundle["report"])
    repo = Path(repository_root).resolve(strict=True)
    paths = [
        *NATIVE_SOURCES,
        *(
            str(p.relative_to(repo))
            for p in sorted((repo / "scripts/full_flow/simulation").glob("*.py"))
        ),
    ]
    report["source_sha256"] = {
        path: hashlib.sha256((repo / path).read_bytes()).hexdigest() for path in paths
    }
    report["artifact_sha256"] = {
        name: hashlib.sha256(text.encode()).hexdigest() for name, text in payloads.items()
    }
    payloads["authoring-status.json"] = canonical(report) + "\n"
    name = "script.pending.json" if report["blockers"] else "script.json"
    payloads[name] = canonical(bundle["config"]) + "\n"
    need(
        all(len(value.encode()) <= MAX_BYTES for value in payloads.values()),
        "SCRIPT_DOCUMENT_UNBOUNDED",
    )
    os.mkdir(root, 0o700)
    need(stat.S_IMODE(root.stat().st_mode) == 0o700, "SCRIPT_PRIVATE_DIRECTORY_MODE")
    for name, payload in payloads.items():
        descriptor = os.open(
            root / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
        )
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload.encode())
            stream.flush()
            os.fsync(stream.fileno())
    fd = os.open(root, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--authoring-input", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args(argv)
    repo = Path(__file__).resolve().parents[3]
    try:
        authored = read_private_authoring(args.authoring_input, repository_root=repo)
        bundle = compile_private_bundle(authored)
        report = write_private_bundle(bundle, args.output_root, repository_root=repo)
    except (ScriptContractError, OSError) as exc:
        # Do not leak private text, paths or JSON parser excerpts to stdout.
        print(
            json.dumps(
                {
                    "state": "BLOCKED",
                    "code": exc.code
                    if isinstance(exc, ScriptContractError)
                    else "SCRIPT_AUTHORING_PRIVATE_IO_ERROR",
                }
            )
        )
        return 2
    print(
        json.dumps(
            {
                key: report[key]
                for key in (
                    "state",
                    "literal_registry_count",
                    "planned_slots",
                    "authored_slots",
                    "config_sha256",
                )
            },
            sort_keys=True,
        )
    )
    return 2 if report["blockers"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
