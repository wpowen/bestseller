"""Main-run RED for the two exact native reader revision JSON codecs.

Only pure _rr_request/_rr_* parsers run when main executes these tests. Bodies
are runtime structural tokens, not stored manuscripts. Native metadata remains
opaque quoted input; these fixtures grant no paid, repair or adoption authority.

BuilderContext.protocol_subject pins logical_role, prompt_version,
reader_first_planning and the complete native reader_revision_binding. Private
answers use the existing submissions[template + ':' + global_ordinal] API.
"""

from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from types import SimpleNamespace
import unittest
from uuid import UUID, uuid4

from scripts.full_flow.simulation.contracts import (
    ScriptContractError,
    TemplateCatalog,
    canonical,
    digest,
)
from scripts.full_flow.simulation.inventory import INVENTORY_SCHEMA
from scripts.full_flow.simulation.quickstart_builders import BuilderContext, build_payload
from scripts.full_flow.simulation.runtime_adapter import create_runtime_tape

from .test_contracts import ENDPOINTS, USAGE
from .test_q4_reader_first import binding

FREEZE = "reader_revision_anchors"
VERIFY = "reader_revision_preservation"
SCHEMAS = {FREEZE: "reader-revision-anchors.v1", VERIFY: "reader-revision-preservation.v1"}
ERRORS = {
    FREEZE: "SCRIPT_READER_REVISION_ANCHORS_INVALID",
    VERIFY: "SCRIPT_READER_REVISION_PRESERVATION_INVALID",
}
WIRE_INVALID = "SCRIPT_READER_REVISION_WIRE_INVALID"


def native():
    from bestseller.services import reader_judge

    return reader_judge


def body_hash(text):
    # Native body hashes cover raw UTF-8, not JSON-quoted strings.
    return hashlib.sha256(text.encode()).hexdigest()


def bodies():
    return tuple(
        "界🧭\n"
        + "\n".join(prefix + "-token-" + str(i) for i in range(1, 10))
        + "\n"
        + prefix
        + "-tail"
        for prefix in ("source", "candidate")
    )


def anchors_for(source, count):
    kinds = (
        "choice",
        "dialogue",
        "action",
        "relationship",
        "reveal",
        "consequence",
        "action",
        "choice",
    )
    result = []
    for index in range(count):
        quote = "source-token-" + str(index + 1)
        start = source.index(quote)
        result.append(
            {
                "anchor_id": "authored-anchor-" + str(index + 1),
                "start": start,
                "end": start + len(quote),
                "quote": quote,
                "kind": kinds[index],
                "narrative_function": "authored-function-" + str(index + 1),
            }
        )
    return result


def revision_case(template, *, count=2, ordinal=89):
    source, candidate = bodies()
    marker, project_id, workflow_id = binding(), uuid4(), uuid4()
    source_binding = {
        "project_id": str(project_id),
        "chapter_id": str(uuid4()),
        "source_draft_id": str(uuid4()),
        "source_body_sha256": body_hash(source),
        "reader_first_binding": marker,
    }
    anchors = anchors_for(source, count)
    request_binding = deepcopy(source_binding)
    if template == VERIFY:
        candidate_id = str(uuid4())
        request_binding.update(
            {
                "candidate_llm_run_id": candidate_id,
                "candidate_body_sha256": body_hash(candidate),
                "raw_body_sha256": body_hash(candidate),
                "anchors_sha256": digest(anchors),
                "derivation": {
                    "schema": "reader-revision-candidate-derivation.v1",
                    "request_template": "reader_chapter_polish",
                    "request_version": "1.0",
                    "source_binding": deepcopy(source_binding),
                    "raw_body_sha256": body_hash(candidate),
                    "candidate_body_sha256": body_hash(candidate),
                    "normalizer": "identity",
                    "arguments": {},
                },
                "candidate_physical": {
                    "llm_run_id": candidate_id,
                    "workflow_run_id": str(workflow_id),
                    "operation_key": "offline-structural-operation-" + uuid4().hex,
                    "input_sha256": digest("offline-input-token"),
                    "request_sha256": digest("offline-request-token"),
                    "response_sha256": digest("offline-response-token"),
                    "raw_body_sha256": body_hash(candidate),
                },
            }
        )
    request = native()._rr_request(
        {"project": SimpleNamespace(id=project_id), "body": source},
        request_binding,
        str(workflow_id),
        anchors=anchors if template == VERIFY else None,
        candidate=candidate if template == VERIFY else None,
    )
    if template == FREEZE:
        material = {
            "schema": SCHEMAS[FREEZE],
            "source_body_sha256": body_hash(source),
            "anchors": anchors,
        }
    else:
        # Explicit opinions, not computed from source, schema or quota. False
        # judgments are valid scripted answers and must remain false.
        authored = (True, False, True, False, True, False, True, False)
        rows = []
        for index, anchor in enumerate(anchors):
            quote = "candidate-token-" + str(index + 1)
            start = candidate.index(quote)
            rows.append(
                {
                    "anchor_id": anchor["anchor_id"],
                    "preserved": authored[index],
                    "evidence": [{"start": start, "end": start + len(quote), "quote": quote}]
                    if authored[index]
                    else [],
                    "reason": "authored-preservation-opinion-" + str(index + 1),
                }
            )
        material = {
            "schema": SCHEMAS[VERIFY],
            "source_body_sha256": body_hash(source),
            "candidate_body_sha256": body_hash(candidate),
            "anchors": rows,
            "voice_preserved": False,
            "no_system_procedure_intrusion": True,
        }
    wire = {
        "model": "offline-reader-revision",
        "stream": True,
        "messages": [
            {"role": "system", "content": request.system_prompt},
            {"role": "user", "content": request.user_prompt},
        ],
    }
    context = BuilderContext(
        {},
        call_index=ordinal,
        wire_body=wire,
        expected_wire_sha256=digest(wire),
        protocol_subject={
            "logical_role": request.logical_role,
            "prompt_version": request.prompt_version,
            "reader_first_planning": deepcopy(marker),
            "reader_revision_binding": deepcopy(request.metadata["reader_revision_binding"]),
        },
        submissions={template + ":" + str(ordinal): material},
    )
    return context, material, json.loads(request.user_prompt), request


def with_packet(context, packet):
    wire = deepcopy(context.wire_body)
    wire["messages"][1]["content"] = canonical(packet)
    return replace(context, wire_body=wire, expected_wire_sha256=digest(wire))


def wire_value(pointer):
    return {"$wire": "/messages/1/content", "decode_json": True, "pointer": pointer}


class CodecAssertions:
    def error(self, template, context, code=None):
        with self.assertRaises(ScriptContractError) as caught:
            build_payload(template, context)
        self.assertEqual(caught.exception.code, code or ERRORS[template])


class ReaderRevisionAnchorCodecTests(CodecAssertions, unittest.TestCase):
    def test_native_freeze_packet_returns_only_explicit_anchors_for_one_two_or_eight(self):
        for count in (1, 2, 8):
            context, material, packet, request = revision_case(FREEZE, count=count)
            before = deepcopy((context.wire_body, material))
            with self.subTest(count=count):
                self.assertEqual(
                    (request.prompt_template, request.prompt_version, request.logical_role),
                    (FREEZE, "1.0", "critic"),
                )
                self.assertEqual(packet["source_body_sha256"], body_hash(packet["source_text"]))
                self.assertNotEqual(packet["source_body_sha256"], digest(packet["source_text"]))
                result = build_payload(FREEZE, context)
                self.assertEqual(result, {"format": "json", "payload": material})
                self.assertEqual(
                    native()._rr_anchor_answer(canonical(material), packet["source_text"]), material
                )
                self.assertEqual((context.wire_body, material), before)

    def test_exact_unicode_span_and_explicit_wire_quote_can_be_bound(self):
        context, material, packet, _ = revision_case(FREEZE, count=1)
        expected = deepcopy(material)
        row = material["anchors"][0]
        self.assertNotEqual(row["start"], len(packet["source_text"][: row["start"]].encode()))
        material["source_body_sha256"] = wire_value("/source_body_sha256")
        row["quote"] = {**wire_value("/source_text"), "quote": row["quote"]}
        self.assertEqual(build_payload(FREEZE, context)["payload"], expected)

    def test_every_anchor_field_type_span_quote_kind_and_function_is_strict(self):
        changes = (
            {"anchor_id": ""},
            {"start": True},
            {"start": -1},
            {"start": 0, "end": 0},
            {"end": 100000},
            {"quote": "invented-token"},
            {"quote": " "},
            {"kind": "default"},
            {"narrative_function": " "},
            {"narrative_function": "x" * 1025},
            {"score": 1.0},
            {"approved": True},
        )
        for change in changes:
            context, material, _, _ = revision_case(FREEZE)
            material["anchors"][0].update(change)
            with self.subTest(change=change):
                self.error(FREEZE, context)
        for field in ("anchor_id", "start", "end", "quote", "kind", "narrative_function"):
            context, material, _, _ = revision_case(FREEZE)
            del material["anchors"][0][field]
            with self.subTest(missing=field):
                self.error(FREEZE, context)

    def test_anchor_count_unique_ids_and_unique_spans_are_explicit(self):
        for failure in ("empty", "nine", "duplicate-id", "duplicate-span"):
            context, material, _, _ = revision_case(FREEZE)
            if failure == "empty":
                material["anchors"] = []
            elif failure == "nine":
                material["anchors"] *= 5
                material["anchors"].pop()
            elif failure == "duplicate-id":
                material["anchors"][1]["anchor_id"] = material["anchors"][0]["anchor_id"]
            else:
                for field in ("start", "end", "quote"):
                    material["anchors"][1][field] = material["anchors"][0][field]
            with self.subTest(failure=failure):
                self.error(FREEZE, context)

    def test_kind_and_narrative_function_cannot_be_inferred_from_wire(self):
        for field in ("kind", "narrative_function"):
            context, material, _, _ = revision_case(FREEZE)
            material["anchors"][0][field] = wire_value("/source_text")
            with self.subTest(field=field):
                self.error(FREEZE, context, "SCRIPT_VERDICT_MUST_BE_AUTHORED")


class ReaderRevisionPreservationCodecTests(CodecAssertions, unittest.TestCase):
    def test_native_verify_keeps_all_authored_false_and_true_judgments(self):
        context, material, packet, request = revision_case(VERIFY)
        before = deepcopy((context.wire_body, material))
        self.assertEqual(
            (request.prompt_template, request.prompt_version, request.logical_role),
            (VERIFY, "1.0", "critic"),
        )
        result = build_payload(VERIFY, context)
        self.assertEqual(result, {"format": "json", "payload": material})
        self.assertEqual(
            native()._rr_verification_answer(
                canonical(material),
                packet["source_text"],
                packet["candidate_text"],
                packet["anchors"],
            ),
            material,
        )
        self.assertEqual([row["preserved"] for row in result["payload"]["anchors"]], [True, False])
        self.assertFalse(result["payload"]["voice_preserved"])
        self.assertEqual((context.wire_body, material), before)

    def test_all_true_and_all_false_are_explicit_material_never_inferred_defaults(self):
        for decision in (False, True):
            context, material, _, _ = revision_case(VERIFY, count=1)
            material["anchors"][0]["preserved"] = decision
            if not decision:
                material["anchors"][0]["evidence"] = []
            material["voice_preserved"] = decision
            material["no_system_procedure_intrusion"] = decision
            with self.subTest(decision=decision):
                self.assertEqual(build_payload(VERIFY, context)["payload"], material)

    def test_wire_anchor_ids_and_hashes_resolve_without_creating_judgments(self):
        context, material, _, _ = revision_case(VERIFY)
        expected = deepcopy(material)
        for field in ("source_body_sha256", "candidate_body_sha256"):
            material[field] = wire_value("/" + field)
        for index, row in enumerate(material["anchors"]):
            row["anchor_id"] = wire_value("/anchors/" + str(index) + "/anchor_id")
        material["anchors"][0]["evidence"][0]["quote"] = {
            **wire_value("/candidate_text"),
            "quote": "candidate-token-1",
        }
        self.assertEqual(build_payload(VERIFY, context)["payload"], expected)

    def test_exact_coverage_and_true_evidence_are_mandatory(self):
        for failure in (
            "missing",
            "duplicate",
            "foreign-id",
            "true-empty",
            "wrong-quote",
            "source-quote",
            "bool-offset",
            "oversized-span",
            "extra-evidence-key",
            "empty-reason",
        ):
            context, material, _, _ = revision_case(VERIFY)
            row = material["anchors"][0]
            if failure == "missing":
                material["anchors"].pop()
            elif failure == "duplicate":
                material["anchors"][1]["anchor_id"] = row["anchor_id"]
            elif failure == "foreign-id":
                row["anchor_id"] = "not-native-anchor"
            elif failure == "true-empty":
                row["evidence"] = []
            elif failure == "empty-reason":
                row["reason"] = " "
            else:
                updates = {
                    "wrong-quote": {"quote": "invented-token"},
                    "source-quote": {"quote": "source-token-1"},
                    "bool-offset": {"start": False},
                    "oversized-span": {"end": 100000},
                    "extra-evidence-key": {"score": 1.0},
                }
                row["evidence"][0].update(updates[failure])
            with self.subTest(failure=failure):
                self.error(VERIFY, context)

    def test_boolean_types_and_explicit_reason_cannot_be_coerced_or_wire_selected(self):
        for field in ("preserved", "voice_preserved", "no_system_procedure_intrusion"):
            for value in (1, 0, "true", None):
                context, material, _, _ = revision_case(VERIFY)
                target = material["anchors"][0] if field == "preserved" else material
                target[field] = value
                with self.subTest(field=field, value=value):
                    self.error(VERIFY, context)
        for field in ("preserved", "reason", "voice_preserved", "no_system_procedure_intrusion"):
            context, material, _, _ = revision_case(VERIFY)
            target = material["anchors"][0] if field in {"preserved", "reason"} else material
            target[field] = wire_value("/source_text")
            with self.subTest(wire_opinion=field):
                self.error(VERIFY, context, "SCRIPT_VERDICT_MUST_BE_AUTHORED")


class ReaderRevisionWireRuntimeTests(CodecAssertions, unittest.TestCase):
    def test_reply_envelopes_have_exact_native_fields_without_authority_or_ratio_exemptions(self):
        for template in SCHEMAS:
            for failure in (
                "missing-schema",
                "schema",
                "source-hash",
                "adoption_authority",
                "approved",
                "score",
                "minimum_preserved_ratio",
            ):
                context, material, _, _ = revision_case(template)
                if failure == "missing-schema":
                    del material["schema"]
                elif failure == "schema":
                    material["schema"] = "unknown"
                elif failure == "source-hash":
                    material["source_body_sha256"] = "0" * 64
                else:
                    material[failure] = True
                with self.subTest(template=template, failure=failure):
                    self.error(template, context)
            for field in ("source_body_sha256", "anchors"):
                context, material, _, _ = revision_case(template)
                del material[field]
                with self.subTest(template=template, missing=field):
                    self.error(template, context)
        context, material, _, _ = revision_case(VERIFY)
        material["candidate_body_sha256"] = "0" * 64
        self.error(VERIFY, context)
        for field in (
            "candidate_body_sha256",
            "anchors",
            "voice_preserved",
            "no_system_procedure_intrusion",
        ):
            context, material, _, _ = revision_case(VERIFY)
            del material[field]
            with self.subTest(missing=field):
                self.error(VERIFY, context)

    def test_changed_full_bodies_or_frozen_anchors_cannot_keep_old_hashes(self):
        for template, field in (
            (FREEZE, "source_text"),
            (VERIFY, "source_text"),
            (VERIFY, "candidate_text"),
            (VERIFY, "anchors"),
        ):
            context, _, packet, _ = revision_case(template)
            if field == "anchors":
                packet[field][-1]["narrative_function"] = "changed-function-token"
            else:
                # Outside all cited spans: a prefix-only digest must not work.
                packet[field] += "\nchanged-tail-token"
            with self.subTest(template=template, field=field):
                self.error(template, with_packet(context, packet), WIRE_INVALID)

    def test_native_binding_cannot_change_even_with_rehashed_wire(self):
        for template in SCHEMAS:
            fields = ["project_id", "chapter_id", "source_draft_id", "reader_first_binding"]
            if template == VERIFY:
                fields += ["candidate_llm_run_id", "anchors_sha256", "candidate_physical"]
            for field in fields:
                context, _, packet, _ = revision_case(template)
                if field == "reader_first_binding":
                    packet[field]["user_input_sha256"] = "0" * 64
                elif field == "candidate_physical":
                    packet[field]["response_sha256"] = "0" * 64
                elif field == "anchors_sha256":
                    packet[field] = "0" * 64
                else:
                    packet[field] = str(uuid4())
                with self.subTest(template=template, field=field):
                    self.error(
                        template,
                        with_packet(context, packet),
                        "SCRIPT_READER_REVISION_BINDING_MISMATCH",
                    )

    def test_request_json_schema_and_single_user_packet_are_strict(self):
        for template in SCHEMAS:
            for failure in ("schema", "duplicate-key", "preamble", "truncated", "second-user"):
                context, _, packet, _ = revision_case(template)
                wire = deepcopy(context.wire_body)
                raw = wire["messages"][1]["content"]
                if failure == "schema":
                    packet["schema"] = "unknown.request"
                    wire["messages"][1]["content"] = canonical(packet)
                elif failure == "duplicate-key":
                    wire["messages"][1]["content"] = '{"schema":"duplicate",' + raw[1:]
                elif failure == "preamble":
                    wire["messages"][1]["content"] = "prefix " + raw
                elif failure == "truncated":
                    wire["messages"][1]["content"] = raw[:-1]
                else:
                    wire["messages"].append(deepcopy(wire["messages"][1]))
                with self.subTest(template=template, failure=failure):
                    self.error(
                        template,
                        replace(context, wire_body=wire, expected_wire_sha256=digest(wire)),
                        WIRE_INVALID,
                    )

    def test_role_version_current_wire_and_global_private_slot_are_exact(self):
        for template in SCHEMAS:
            context, material, _, _ = revision_case(template)
            for failure, changed, code in (
                (
                    "wire",
                    replace(context, expected_wire_sha256="0" * 64),
                    "SCRIPT_SOURCE_WIRE_MATCH_REQUIRED",
                ),
                (
                    "role",
                    replace(
                        context,
                        protocol_subject={**context.protocol_subject, "logical_role": "writer"},
                    ),
                    "SCRIPT_READER_REVISION_ROLE_MISMATCH",
                ),
                (
                    "version",
                    replace(
                        context,
                        protocol_subject={**context.protocol_subject, "prompt_version": "1.1"},
                    ),
                    "SCRIPT_READER_REVISION_VERSION_MISMATCH",
                ),
                ("missing", replace(context, submissions={}), "SCRIPT_SOURCE_BOUND_REPLY_REQUIRED"),
                (
                    "old-slot",
                    replace(context, submissions={template + ":88": material}),
                    "SCRIPT_SOURCE_BOUND_REPLY_REQUIRED",
                ),
            ):
                with self.subTest(template=template, failure=failure):
                    self.error(template, changed, code)
            with self.assertRaises(ScriptContractError) as caught:
                build_payload(template + "_unknown", context)
            self.assertEqual(caught.exception.code, "SCRIPT_TEMPLATE_UNCOVERED")

    def test_both_templates_use_actual_native_observation_and_old_or_new_catalogue(self):
        from e2e_sim.transport import NativeObservation, wire_fingerprints

        for template in SCHEMAS:
            for include in (False, True):
                context, material, _, request = revision_case(template, ordinal=127)
                catalog = TemplateCatalog(
                    {
                        "schema": INVENTORY_SCHEMA,
                        "parse_failures": [],
                        "dynamic_sites": [],
                        "templates": [{"template": template, "historical_only": False}]
                        if include
                        else [],
                    }
                )
                before = deepcopy(catalog.inventory)
                tape = create_runtime_tape(
                    catalog=catalog,
                    context_resolver=lambda obs, body, ctx=context: replace(ctx, wire_body=body),
                    usage_resolver=lambda obs, body: USAGE,
                    allowed_endpoints=ENDPOINTS,
                )
                observed = NativeObservation(
                    template, 127, str(request.project_id), str(request.workflow_run_id)
                )
                with self.subTest(template=template, catalogue=include):
                    selected = tape._choose(
                        context.wire_body, wire_fingerprints(context.wire_body), observed
                    )
                    self.assertEqual(json.loads(selected["content"]), material)
                    match = tape.document["entries"][0]["match"]
                    for key, value in {
                        "prompt_template": template,
                        "call_index": 127,
                        "project_id": str(UUID(str(request.project_id))),
                        "workflow_run_id": str(request.workflow_run_id),
                        "body_sha256": digest(context.wire_body),
                    }.items():
                        self.assertEqual(match[key], value)
                    self.assertEqual(catalog.inventory, before)
                    self.assertFalse(selected.get("tool_calls"))
