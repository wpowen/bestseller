"""RED only: the native short source of planner_reader_first_outline_review.

Main executes these offline; there is no transport installation or async native
workflow here. Structural tokens are constructed in memory. The manifest and
opaque receipt are quoted input, never evidence of paid or adoption authority.
"""

from copy import deepcopy
from dataclasses import replace
import json
from types import SimpleNamespace
import unittest
from uuid import uuid4

from scripts.full_flow.simulation.contracts import (
    ScriptContractError,
    TemplateCatalog,
    canonical,
    digest,
)
from scripts.full_flow.simulation.inventory import INVENTORY_SCHEMA
from scripts.full_flow.simulation.quickstart_builders import BuilderContext, build_payload
from scripts.full_flow.simulation.reader_first_protocols import decode_review_wire
from scripts.full_flow.simulation.runtime_adapter import create_runtime_tape

from .test_contracts import ENDPOINTS, USAGE
from .test_q4_authored_repairs import authored_roles, outline_material
from .test_q4_reader_first import DIMENSIONS, binding, native_module

TEMPLATE = "planner_reader_first_outline_review"
VERSION = "rf-outline-review-v1"
INVALID = "SCRIPT_READER_FIRST_REVIEW_INVALID"


def short_source(marker, *, count=4, creation_segment_count=None):
    # The shape comes from fanqie_outline_contract_completion._sources, then
    # fanqie_short_planner.prepare_reader_first_short_outline adds only outline.
    # Never call either async producer or manufacture normal-planner aliases.
    from bestseller.services.fanqie_outline_contract_completion import (
        _segment_binding,
        _segment_count,
    )
    from bestseller.services.planner import _reader_first_outline_coverage

    base = outline_material()
    for number in range(5, count + 1):
        chapter = deepcopy(base["chapters"][-1])
        chapter.update(
            chapter_number=number,
            goal="goal-token-" + str(number),
            stakes="fixed-reference-token-" + str(number),
        )
        base["chapters"].append(chapter)
    annotated = deepcopy(base)
    for chapter in annotated["chapters"]:
        chapter["_meta"].update(
            reader_first_planning=deepcopy(marker),
            writer_delivery_schema="approved-current-chapter-delivery.v3-reader-first",
            reader_first_field_roles=authored_roles(),
        )
    foundation = {
        "book_spec": {"theme": "theme-token"},
        "world_spec": {"escaped/key": {"~leaf": "world-token"}},
        "cast_spec": {"characters": [{"name": "actor-token"}]},
    }
    canonical_content = {"premise": "canonical-premise-token"}
    project = SimpleNamespace(
        id=uuid4(),
        target_chapters=count,
        metadata_json=(
            {} if creation_segment_count is None else {"segment_count": creation_segment_count}
        ),
    )
    source = {
        "selected_source": {
            "project_id": str(project.id),
            "premise": "source-A  source-B",
            "project_inputs": {
                "title": "title-token",
                "genre": "genre-token",
                "sub_genre": "subgenre-token",
                "language": "zh",
                "target_chapters": count,
                "target_word_count": 4000,
            },
            "foundation": {
                kind: {"id": str(uuid4()), "version": 1, "content_sha256": digest(content)}
                for kind, content in foundation.items()
            },
            "complete_foundation_content": foundation,
            "canonical": {
                "id": str(uuid4()),
                "content": canonical_content,
                "content_sha256": digest(canonical_content),
            },
            "book_design_snapshot": None,
            "effective_pov": {
                "style_pov_type": "third-limited",
                "beat_sheet_pov": "third_person",
            },
            "explicit_creation_contracts": {},
            "current_author_fields": {},
            "required_author_layers": [],
            "required_author_secondary_roles": [],
        },
        "original_beat": {
            "artifact_id": str(uuid4()),
            "raw": {
                "beats": [
                    {"segment_number": number, "goal": "beat-token-" + str(number)}
                    for number in range(1, count + 1)
                ]
            },
            # No paid-proof, quality approval or author-authority flags.
            "receipt": {"fixture_note": "opaque-original-receipt-token"},
        },
        "base_outline": base,
        "outline": annotated,
    }
    if count != 4:
        source["segment_contract"] = _segment_binding(count, project=project)
    # These are pure native input checks, invoked only by main's test process.
    _segment_count(project, source)
    _reader_first_outline_coverage(source, project, marker)
    return source


def short_case(*, ordinal=73, count=4, creation_segment_count=None):
    marker = binding()
    source = short_source(marker, count=count, creation_segment_count=creation_segment_count)
    system, user = native_module().reader_first_review_prompts(
        marker, source=source, checkpoint="outline"
    )
    material = {
        "schema": "reader-first-review.v1",
        "checkpoint": "outline",
        "source_sha256": digest(source),
        "config_sha256": marker["config_sha256"],
        "dimensions": {
            "character_emotion_drives_conflict": {
                "score": 0.2,
                "reason": "authored-short-conflict-objection",
                "evidence": [{"path": "/selected_source/premise", "quote": "source-A  source-B"}],
            },
            "reader_promise": {
                "score": 0.4,
                "reason": "authored-short-promise-objection",
                "evidence": [{"path": "/original_beat/raw/beats/0/goal", "quote": "beat-token-1"}],
            },
            "emotional_payoff": {
                "score": 0.3,
                "reason": "authored-short-payoff-objection",
                "evidence": [
                    {"path": "/base_outline/chapters/0/stakes", "quote": "fixed-reference-token-1"}
                ],
            },
            "character_distinctiveness": {
                "score": 0.1,
                "reason": "authored-short-distinctiveness-objection",
                "evidence": [
                    {"path": "/outline/chapters/0/escaped~1key/~0leaf", "quote": "escaped-token"}
                ],
            },
        },
        "issues": [{"severity": "high", "reason": "authored-short-issue"}],
        "options": ["authored-short-direction-one", "authored-short-direction-two"],
    }
    wire = {
        "model": "offline-q4-short-review",
        "stream": True,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
    }
    context = BuilderContext(
        {},
        call_index=ordinal,
        wire_body=wire,
        expected_wire_sha256=digest(wire),
        protocol_subject={"reader_first_planning": marker, "prompt_version": VERSION},
        submissions={TEMPLATE + ":" + str(ordinal): material},
    )
    return context, material, json.loads(user)


def with_document(context, document):
    wire = deepcopy(context.wire_body)
    wire["messages"][1]["content"] = canonical(document)
    return replace(context, wire_body=wire, expected_wire_sha256=digest(wire))


def with_material(context, material):
    return replace(context, submissions={TEMPLATE + ":" + str(context.call_index): material})


def rebind_source(context, material, document):
    # Test mutations explicitly update identity, never any opinion or evidence.
    document["source_sha256"] = digest(document["source"])
    material["source_sha256"] = document["source_sha256"]
    return with_material(with_document(context, document), material)


class Q4ShortReaderReviewTests(unittest.TestCase):
    def error(self, context, code=INVALID):
        with self.assertRaises(ScriptContractError) as caught:
            build_payload(TEMPLATE, context)
        self.assertEqual(caught.exception.code, code)

    def test_native_short_packet_keeps_full_source_and_authored_low_scores(self):
        context, material, document = short_case()
        before = deepcopy((context.wire_body, material))
        self.assertEqual(
            set(document["source"]), {"selected_source", "original_beat", "base_outline", "outline"}
        )
        self.assertNotIn("original_input_binding", document["source"])
        self.assertEqual(decode_review_wire(TEMPLATE, context), document)
        result = build_payload(TEMPLATE, context)
        self.assertEqual(result, {"format": "json", "payload": material})
        receipt = native_module().parse_reader_first_review(
            result["payload"],
            source=document["source"],
            binding=document["binding"],
            checkpoint="outline",
        )
        self.assertEqual(receipt["status"], "failed")
        self.assertEqual([receipt["scores"][key] for key in DIMENSIONS], [0.2, 0.4, 0.3, 0.1])
        self.assertNotIn("status", result["payload"])
        self.assertEqual((context.wire_body, material), before)

    def test_nonfour_packet_keeps_native_segment_contract_and_last_chapter(self):
        for creation_count in (None, 6):
            context, material, document = short_case(count=6, creation_segment_count=creation_count)
            with self.subTest(creation_segment_count=creation_count):
                decoded = decode_review_wire(TEMPLATE, context)
                self.assertEqual(decoded, document)
                self.assertEqual(len(decoded["source"]["outline"]["chapters"]), 6)
                self.assertEqual(
                    decoded["source"]["segment_contract"]["ordered_chapter_numbers"],
                    list(range(1, 7)),
                )
                self.assertEqual(build_payload(TEMPLATE, context)["payload"], material)

    def test_explicit_wire_bindings_resolve_identity_and_full_source_quote_only(self):
        context, material, _ = short_case()
        expected = deepcopy(material)
        for field in ("source_sha256", "config_sha256"):
            material[field] = {
                "$wire": "/messages/1/content",
                "decode_json": True,
                "pointer": "/" + field,
            }
        material["dimensions"][DIMENSIONS[0]]["evidence"][0]["quote"] = {
            "$wire": "/messages/1/content",
            "decode_json": True,
            "pointer": "/source/selected_source/premise",
            "quote": "source-A  source-B",
        }
        self.assertEqual(build_payload(TEMPLATE, context)["payload"], expected)

    def test_changed_unquoted_source_rejects_old_review_despite_current_wire_hash(self):
        for branch in ("foundation", "original-beat", "base-outline", "last-chapter", "receipt"):
            context, material, document = short_case()
            source = document["source"]
            if branch == "foundation":
                source["selected_source"]["complete_foundation_content"]["book_spec"]["theme"] = (
                    "changed-token"
                )
            elif branch == "original-beat":
                source["original_beat"]["raw"]["beats"][-1]["goal"] = "changed-token"
            elif branch == "base-outline":
                source["base_outline"]["chapters"][-1]["goal"] = "changed-token"
            elif branch == "receipt":
                source["original_beat"]["receipt"]["fixture_note"] = "changed-token"
            else:
                source["outline"]["chapters"][-1]["goal"] = "changed-token"
            document["source_sha256"] = digest(source)
            changed = with_document(context, document)
            with self.subTest(branch=branch):
                self.assertNotEqual(material["source_sha256"], document["source_sha256"])
                self.error(changed)

    def test_new_full_source_hash_can_keep_explicit_low_opinions_after_repair(self):
        context, material, document = short_case()
        document["source"]["outline"]["chapters"][-1]["goal"] = "explicit-repaired-goal-token"
        changed = rebind_source(context, material, document)
        self.assertEqual(decode_review_wire(TEMPLATE, changed), document)
        self.assertEqual(build_payload(TEMPLATE, changed)["payload"], material)

    def test_complete_native_source_parts_cannot_be_omitted_or_projected(self):
        for missing in ("selected_source", "original_beat", "base_outline", "outline"):
            context, material, document = short_case()
            del document["source"][missing]
            with self.subTest(missing=missing):
                self.error(rebind_source(context, material, document))
        for value in ({}, [], None, "source-token"):
            context, material, document = short_case()
            document["source"]["selected_source"] = value
            with self.subTest(selected_source=value):
                self.error(rebind_source(context, material, document))

    def test_target_and_every_native_segment_sequence_must_match(self):
        for failure in (
            "target",
            "bool-target",
            "beat-prefix",
            "base-prefix",
            "outline-prefix",
            "duplicate-chapter",
            "segment-contract",
        ):
            context, material, document = short_case(count=6)
            source = document["source"]
            if failure in {"target", "bool-target"}:
                source["selected_source"]["project_inputs"]["target_chapters"] = (
                    True if failure == "bool-target" else 5
                )
            elif failure == "beat-prefix":
                source["original_beat"]["raw"]["beats"].pop()
            elif failure == "base-prefix":
                source["base_outline"]["chapters"].pop()
            elif failure == "outline-prefix":
                source["outline"]["chapters"].pop()
            elif failure == "duplicate-chapter":
                source["outline"]["chapters"][-1]["chapter_number"] = 1
            else:
                source["segment_contract"]["ordered_chapter_numbers"].pop()
            with self.subTest(failure=failure):
                self.error(rebind_source(context, material, document))

    def test_every_chapter_binding_delivery_roles_and_scene_scope_remain_native(self):
        for failure in (
            "binding",
            "delivery",
            "roles-missing",
            "role-value",
            "leaf-missing",
            "extra-role",
            "scene-number",
            "empty-scenes",
        ):
            context, material, document = short_case()
            chapter = document["source"]["outline"]["chapters"][-1]
            meta = chapter["_meta"]
            if failure == "binding":
                meta["reader_first_planning"]["user_input_sha256"] = "2" * 64
            elif failure == "delivery":
                meta["writer_delivery_schema"] = "unknown"
            elif failure == "roles-missing":
                del meta["reader_first_field_roles"]
            elif failure == "role-value":
                meta["reader_first_field_roles"]["/goal"] = "automatic"
            elif failure == "leaf-missing":
                del meta["reader_first_field_roles"]["/null_value"]
            elif failure == "extra-role":
                meta["reader_first_field_roles"]["/absent"] = "narrative_task"
            elif failure == "scene-number":
                chapter["scenes"][0]["scene_number"] = 2
            else:
                chapter["scenes"] = []
            with self.subTest(failure=failure):
                self.error(rebind_source(context, material, document))

    def test_source_and_config_hashes_are_full_current_native_identities(self):
        for failure in (
            "projected-source",
            "prefix-source",
            "envelope-source",
            "config",
            "original-binding",
        ):
            context, material, document = short_case()
            if failure == "projected-source":
                material["source_sha256"] = digest(document["source"]["outline"])
            elif failure == "prefix-source":
                partial = deepcopy(document["source"])
                partial["outline"]["chapters"].pop()
                material["source_sha256"] = digest(partial)
            elif failure == "envelope-source":
                document["source_sha256"] = "0" * 64
            elif failure == "config":
                material["config_sha256"] = "0" * 64
            else:
                document["binding"]["user_input_sha256"] = "1" * 64
            with self.subTest(failure=failure):
                self.error(with_document(context, document))

    def test_quotes_use_full_source_pointer_and_exact_native_text(self):
        for evidence in (
            {"path": "/source/selected_source/premise", "quote": "source-A  source-B"},
            {"path": "/selected_source/premise", "quote": "source-A source-B"},
            {"path": "/outline/chapters/0/goal", "quote": "source-A  source-B"},
            {"path": "/original_beat/raw/beats/00/goal", "quote": "beat-token-1"},
            {"path": "/outline/chapters/0/escaped~2key/~0leaf", "quote": "escaped-token"},
        ):
            context, material, _ = short_case()
            material["dimensions"][DIMENSIONS[0]]["evidence"] = [evidence]
            with self.subTest(evidence=evidence):
                self.error(context)

    def test_judgments_are_required_and_never_taken_from_source_or_schema(self):
        for field in ("score", "reason", "evidence", "issues", "options"):
            context, material, _ = short_case()
            target = (
                material
                if field in {"issues", "options"}
                else material["dimensions"][DIMENSIONS[0]]
            )
            del target[field]
            with self.subTest(missing=field):
                self.error(context)
        for field in ("score", "reason", "issues", "options"):
            context, material, _ = short_case()
            target = (
                material
                if field in {"issues", "options"}
                else material["dimensions"][DIMENSIONS[0]]
            )
            target[field] = {
                "$wire": "/messages/1/content",
                "decode_json": True,
                "pointer": "/source/selected_source/premise",
            }
            with self.subTest(bound_judgment=field):
                self.error(context, "SCRIPT_VERDICT_MUST_BE_AUTHORED")

    def test_current_wire_version_and_private_ordinal_slot_are_mandatory(self):
        context, material, _ = short_case()
        for failure, changed, code in (
            (
                "wire",
                replace(context, expected_wire_sha256="0" * 64),
                "SCRIPT_SOURCE_WIRE_MATCH_REQUIRED",
            ),
            (
                "version",
                replace(
                    context,
                    protocol_subject={**context.protocol_subject, "prompt_version": "unknown"},
                ),
                "SCRIPT_READER_FIRST_VERSION_MISMATCH",
            ),
            ("missing", replace(context, submissions={}), "SCRIPT_SOURCE_BOUND_REPLY_REQUIRED"),
            (
                "previous-ordinal",
                replace(context, submissions={TEMPLATE + ":72": material}),
                "SCRIPT_SOURCE_BOUND_REPLY_REQUIRED",
            ),
        ):
            with self.subTest(failure=failure):
                self.error(changed, code)
        with self.assertRaises(ScriptContractError) as caught:
            build_payload(TEMPLATE + "_unknown_short", context)
        self.assertEqual(caught.exception.code, "SCRIPT_TEMPLATE_UNCOVERED")


class Q4ShortReaderReviewRuntimeTests(unittest.TestCase):
    def tape(self, context, *, include=True):
        catalog = TemplateCatalog(
            {
                "schema": INVENTORY_SCHEMA,
                "parse_failures": [],
                "dynamic_sites": [],
                "templates": [{"template": TEMPLATE, "historical_only": False}] if include else [],
            }
        )
        return create_runtime_tape(
            catalog=catalog,
            context_resolver=lambda obs, body: replace(
                context, call_index=obs.call_index, wire_body=body
            ),
            usage_resolver=lambda obs, body: USAGE,
            allowed_endpoints=ENDPOINTS,
        )

    def test_exact_template_version_path_uses_real_ids_full_wire_and_global_slot(self):
        from e2e_sim.transport import NativeObservation, wire_fingerprints

        for include in (False, True):
            context, material, document = short_case(ordinal=113)
            tape = self.tape(context, include=include)
            observation = NativeObservation(
                TEMPLATE, 113, document["source"]["selected_source"]["project_id"], str(uuid4())
            )
            wire = context.wire_body
            with self.subTest(catalogue=include):
                selected = tape._choose(wire, wire_fingerprints(wire), observation)
                self.assertEqual(json.loads(selected["content"]), material)
                match = tape.document["entries"][0]["match"]
                for key, value in {
                    "prompt_template": TEMPLATE,
                    "call_index": 113,
                    "project_id": observation.project_id,
                    "workflow_run_id": observation.workflow_run_id,
                    "body_sha256": wire_fingerprints(wire)["body_sha256"],
                }.items():
                    self.assertEqual(match[key], value)
                self.assertFalse(selected.get("tool_calls"))

    def test_next_global_slot_cannot_reuse_previous_private_opinions(self):
        from e2e_sim.transport import NativeObservation, wire_fingerprints

        context, _, _ = short_case(ordinal=113)
        tape = self.tape(context)
        observed = NativeObservation(TEMPLATE, 114, str(uuid4()), str(uuid4()))
        with self.assertRaises(ScriptContractError) as caught:
            tape._choose(context.wire_body, wire_fingerprints(context.wire_body), observed)
        self.assertEqual(caught.exception.code, "SCRIPT_SOURCE_BOUND_REPLY_REQUIRED")
        self.assertEqual(tape.document["entries"], [])
