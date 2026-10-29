"""RED: exact prospective reader v2 codecs; main alone runs these tests.

Native factories below only construct detached request data when main executes
the tests. No app, async workflow, SDK, database or paid provider is invoked.
The complete source is retained; v1 markers are never migrated to v2.
"""

from copy import deepcopy
from dataclasses import replace
import json
from types import SimpleNamespace
import unittest
from uuid import uuid4

from scripts.full_flow.simulation.contracts import ScriptContractError, canonical, digest
from scripts.full_flow.simulation.q4_authored_repairs import decode_outline_repair_wire
from scripts.full_flow.simulation.quickstart_builders import BuilderContext, build_payload
from scripts.full_flow.simulation.reader_first_protocols import decode_review_wire

from .test_q4_authored_repairs import authored_roles, outline_material, wire_for
from .test_q4_reader_first import native_module
from .test_q4_short_reader_review import short_source

ROLES = "fanqie_reader_first_field_roles"
REVIEW = "planner_reader_first_outline_review"
REPAIR = "planner_reader_first_full_outline_commercial_surgical_repair"
VERSIONS = {
    1: {ROLES: "fanqie-rf-field-roles-v1", REVIEW: "rf-outline-review-v1",
        REPAIR: "rf-outline-repair-v1"},
    2: {ROLES: "fanqie-rf-field-roles-v2", REVIEW: "rf-outline-review-v2",
        REPAIR: "rf-outline-repair-v2"},
}


def marker_for(version):
    return native_module().new_reader_first_binding(
        "original-input-token", protocol="reader-first-planning.v" + str(version)
    )


def context_for(template, version, marker, wire, material, *, source_hash=None):
    subject = {"reader_first_planning": marker, "prompt_version": VERSIONS[version][template]}
    if source_hash is not None:
        subject["source_sha256"] = source_hash
    return BuilderContext(
        {}, call_index=89, wire_body=wire, expected_wire_sha256=digest(wire),
        protocol_subject=subject, submissions={template + ":89": material},
    )


def roles_case(version=2, *, attempt=0):
    from bestseller.services.fanqie_short_planner import _short_roles_request

    marker, outline = marker_for(version), outline_material()
    request = _short_roles_request(
        SimpleNamespace(id=uuid4()), marker, outline, workflow_run_id=uuid4(),
        slot=attempt, rejected=None if attempt == 0 else "rejected-annotation-token",
        requested_by="offline-script-contract",
    )
    assert request.prompt_template == ROLES
    assert request.prompt_version == VERSIONS[version][ROLES]
    wire = wire_for(request.system_prompt, json.loads(request.user_prompt))
    material = {
        "schema": "fanqie-reader-first-field-roles.v1", "source_sha256": digest(outline),
        "chapters": [{"chapter_number": number, "field_roles": authored_roles()}
                     for number in (1, 2, 3, 4)],
    }
    return context_for(ROLES, version, marker, wire, material), material


def review_material(source, marker, *, low):
    # Explicit two opinions, not a schema-derived score or automatic pass.
    scores = (.2, .4, .3, .1) if low else (.82, .86, .85, .83)
    return {
        "schema": "reader-first-review.v1", "checkpoint": "outline",
        "source_sha256": digest(source), "config_sha256": marker["config_sha256"],
        "dimensions": {
            "character_emotion_drives_conflict": {
                "score": scores[0], "reason": "authored-conflict-assessment",
                "evidence": [{"path": "/selected_source/premise", "quote": "source-A  source-B"}],
            },
            "reader_promise": {
                "score": scores[1], "reason": "authored-promise-assessment",
                "evidence": [{"path": "/original_beat/raw/beats/0/goal", "quote": "beat-token-1"}],
            },
            "emotional_payoff": {
                "score": scores[2], "reason": "authored-payoff-assessment",
                "evidence": [{"path": "/base_outline/chapters/0/stakes",
                              "quote": "fixed-reference-token-1"}],
            },
            "character_distinctiveness": {
                "score": scores[3], "reason": "authored-distinctiveness-assessment",
                "evidence": [{"path": "/outline/chapters/0/escaped~1key/~0leaf",
                              "quote": "escaped-token"}],
            },
        },
        "issues": [{"severity": "high" if low else "low", "reason": "authored-specific-issue"}],
        "options": ["authored-direction-one", "authored-direction-two"],
    }


def review_case(version=2, *, low=True, long_source=False):
    native = native_module()
    marker = marker_for(version)
    source = short_source(marker)
    if long_source:
        # An opaque supplied source leaf, not generated prose or padding output.
        foundation = source["selected_source"]["complete_foundation_content"]
        foundation["world_spec"]["opaque_retained_source"] = "q" * 125_000 + "EXACT-END-TOKEN"
        source["selected_source"]["foundation"]["world_spec"]["content_sha256"] = digest(
            foundation["world_spec"]
        )
    request = native.reader_first_review_request(
        SimpleNamespace(id=uuid4()), marker, source, uuid4(),
        metadata={"checkpoint": "cannot-override-native", "source_sha256": "cannot-override"},
    )
    assert request.prompt_template == REVIEW
    assert request.prompt_version == VERSIONS[version][REVIEW]
    assert request.metadata["source_sha256"] == digest(source)
    assert request.metadata["checkpoint"] == "outline"
    wire = wire_for(request.system_prompt, json.loads(request.user_prompt))
    material = review_material(source, marker, low=low)
    return context_for(REVIEW, version, marker, wire, material), material


def repair_case(version=2):
    from bestseller.services import planner

    marker = marker_for(version)
    source = short_source(marker)
    # Literal native short repair envelope from prepare_reader_first_short_outline
    # (repair_request is a closure, not a public callable). v2 adds this marker.
    document = {
        "schema": "fanqie-reader-first-outline-repair.v1", "source": source,
        "source_sha256": digest(source),
        "reader_review": {"status": "failed", "source_sha256": digest(source),
                          "reasons": [{"dimension": "reader_promise",
                                       "reason": "authored-specific-repair-request"}]},
        "patch_protocol": planner._outline_patch_protocol(source["outline"]),
    }
    if version == 2:
        document["reader_first_planning"] = deepcopy(marker)
    material = {"patches": [{"chapter_number": 1, "scope": "chapter", "field": "goal",
                              "value": "explicit-revised-goal-token"}]}
    wire = wire_for("Revise the supplied complete native short-story outline.", document)
    context = context_for(REPAIR, version, marker, wire, material, source_hash=digest(source))
    return context, material


CASES = {ROLES: roles_case, REVIEW: review_case, REPAIR: repair_case}


def with_document(context, document):
    wire = deepcopy(context.wire_body)
    wire["messages"][1]["content"] = canonical(document)
    return replace(context, wire_body=wire, expected_wire_sha256=digest(wire))


def with_material(template, context, material):
    return replace(context, submissions={template + ":89": material})


class ReaderV2VersionRedTests(unittest.TestCase):
    def error(self, code, template, context):
        with self.assertRaises(ScriptContractError) as raised:
            build_payload(template, context)
        self.assertEqual(raised.exception.code, code)

    def test_three_literal_v2_templates_preserve_authored_outputs_and_input(self):
        for template, factory in CASES.items():
            with self.subTest(template=template):
                context, material = factory(2)
                before = canonical(context.wire_body)
                result = build_payload(template, context)
                self.assertEqual(result, {"payload": material, "format": "json"})
                self.assertEqual(canonical(context.wire_body), before)

    def test_legacy_v1_stays_on_its_original_contract(self):
        for template, factory in CASES.items():
            with self.subTest(template=template):
                context, material = factory(1)
                self.assertEqual(build_payload(template, context),
                                 {"payload": material, "format": "json"})

    def test_both_directions_of_version_only_relabel_are_rejected(self):
        for template, factory in CASES.items():
            for native_version, other_version in ((1, 2), (2, 1)):
                with self.subTest(template=template, native_version=native_version):
                    context, _ = factory(native_version)
                    subject = {**context.protocol_subject,
                               "prompt_version": VERSIONS[other_version][template]}
                    self.error("SCRIPT_READER_FIRST_VERSION_MISMATCH", template,
                               replace(context, protocol_subject=subject))

    def test_unknown_versions_are_never_latest_or_wildcard(self):
        for template, factory in CASES.items():
            for value in (None, "", "latest", "*", VERSIONS[2][template] + "-extra", "v3"):
                with self.subTest(template=template, version=value):
                    context, _ = factory(2)
                    subject = {**context.protocol_subject, "prompt_version": value}
                    self.error("SCRIPT_READER_FIRST_VERSION_MISMATCH", template,
                               replace(context, protocol_subject=subject))

    def test_wire_hash_pin_is_still_required(self):
        for template, factory in CASES.items():
            with self.subTest(template=template):
                context, _ = factory(2)
                self.error("SCRIPT_SOURCE_WIRE_MATCH_REQUIRED", template,
                           replace(context, expected_wire_sha256="0" * 64))

    def test_v2_marker_cannot_reuse_v1_config_hash(self):
        for template, factory in CASES.items():
            with self.subTest(template=template):
                context, _ = factory(2)
                subject = deepcopy(context.protocol_subject)
                subject["reader_first_planning"]["config_sha256"] = marker_for(1)["config_sha256"]
                self.error("SCRIPT_READER_FIRST_BINDING_MISMATCH", template,
                           replace(context, protocol_subject=subject))

    def test_wire_marker_must_equal_current_subject_marker(self):
        for template, factory in CASES.items():
            with self.subTest(template=template):
                context, _ = factory(2)
                doc = json.loads(context.wire_body["messages"][1]["content"])
                key = "binding" if template == REVIEW else "reader_first_planning"
                doc[key]["user_input_sha256"] = "0" * 64
                code = ("SCRIPT_READER_FIRST_ANNOTATION_INVALID" if template == ROLES else
                        "SCRIPT_READER_FIRST_REVIEW_INVALID" if template == REVIEW else
                        "SCRIPT_READER_FIRST_REPAIR_INVALID")
                self.error(code, template, with_document(context, doc))

    def test_v2_never_aliases_an_unknown_template(self):
        context, _ = roles_case()
        self.error("SCRIPT_TEMPLATE_UNCOVERED", "fanqie_reader_first_field_roles_v2", context)


class ReaderV2MaterialRedTests(unittest.TestCase):
    error = ReaderV2VersionRedTests.error

    def test_roles_both_native_attempts_keep_explicit_leaf_classifications(self):
        for attempt in (0, 1):
            with self.subTest(attempt=attempt):
                context, material = roles_case(attempt=attempt)
                self.assertEqual(build_payload(ROLES, context),
                                 {"payload": material, "format": "json"})

    def test_roles_missing_extra_or_unknown_role_does_not_get_filled(self):
        for mutation in ("missing", "extra", "unknown"):
            with self.subTest(mutation=mutation):
                context, material = roles_case()
                roles = material["chapters"][0]["field_roles"]
                if mutation == "missing":
                    del roles["/goal"]
                elif mutation == "extra":
                    roles["/invented"] = "narrative_task"
                else:
                    roles["/goal"] = "auto"
                self.error("SCRIPT_READER_FIRST_ANNOTATION_INVALID", ROLES,
                           with_material(ROLES, context, material))

    def test_roles_source_hash_and_complete_catalogue_are_bound(self):
        context, _ = roles_case()
        doc = json.loads(context.wire_body["messages"][1]["content"])
        doc["chapter_leaf_values"]["4"]["/goal"] = "changed-tail-value"
        self.error("SCRIPT_READER_FIRST_ANNOTATION_INVALID", ROLES, with_document(context, doc))

    def test_reader_low_and_positive_values_are_preserved_without_upgrade(self):
        for low in (True, False):
            with self.subTest(low=low):
                context, material = review_case(low=low)
                self.assertEqual(build_payload(REVIEW, context),
                                 {"payload": material, "format": "json"})

    def test_reader_full_source_larger_than_120k_keeps_last_bytes(self):
        context, material = review_case(long_source=True)
        raw = context.wire_body["messages"][1]["content"]
        self.assertGreater(len(raw), 120_000)
        decoded = decode_review_wire(REVIEW, context)
        self.assertEqual(decoded, json.loads(raw))
        leaf = decoded["source"]["selected_source"]["complete_foundation_content"]
        self.assertTrue(leaf["world_spec"]["opaque_retained_source"].endswith("EXACT-END-TOKEN"))
        self.assertEqual(build_payload(REVIEW, context), {"payload": material, "format": "json"})

    def test_reader_changed_unquoted_tail_still_invalidates_source_hash(self):
        context, _ = review_case()
        doc = json.loads(context.wire_body["messages"][1]["content"])
        foundation = doc["source"]["selected_source"]["complete_foundation_content"]
        foundation["world_spec"]["tail"] = "new"
        self.error("SCRIPT_READER_FIRST_REVIEW_INVALID", REVIEW, with_document(context, doc))

    def test_reader_missing_dimension_or_forged_quote_never_passes(self):
        for mutation in ("missing", "quote", "score"):
            with self.subTest(mutation=mutation):
                context, material = review_case()
                row = material["dimensions"]["reader_promise"]
                if mutation == "missing":
                    del material["dimensions"]["reader_promise"]
                elif mutation == "quote":
                    row["evidence"][0]["quote"] = "absent-quote-token"
                else:
                    row["score"] = True
                self.error("SCRIPT_READER_FIRST_REVIEW_INVALID", REVIEW,
                           with_material(REVIEW, context, material))

    def test_v2_short_repair_decodes_explicit_full_binding(self):
        context, material = repair_case()
        document = json.loads(context.wire_body["messages"][1]["content"])
        decoded = decode_outline_repair_wire(context)
        self.assertEqual(decoded["source"], document["source"])
        self.assertEqual(decoded["reader_first_planning"], document["reader_first_planning"])
        self.assertEqual(build_payload(REPAIR, context), {"payload": material, "format": "json"})

    def test_v2_repair_missing_binding_or_added_default_field_rejected(self):
        for mutation in ("missing", "extra", "source_hash", "review_hash"):
            with self.subTest(mutation=mutation):
                context, _ = repair_case()
                doc = json.loads(context.wire_body["messages"][1]["content"])
                if mutation == "missing":
                    del doc["reader_first_planning"]
                elif mutation == "extra":
                    doc["approved"] = True
                elif mutation == "source_hash":
                    doc["source_sha256"] = "0" * 64
                else:
                    doc["reader_review"]["source_sha256"] = "0" * 64
                self.error("SCRIPT_READER_FIRST_REPAIR_INVALID", REPAIR,
                           with_document(context, doc))

    def test_legacy_repair_cannot_accept_v2_envelope(self):
        context, _ = repair_case(1)
        doc = json.loads(context.wire_body["messages"][1]["content"])
        doc["reader_first_planning"] = deepcopy(context.protocol_subject["reader_first_planning"])
        self.error("SCRIPT_READER_FIRST_REPAIR_INVALID", REPAIR, with_document(context, doc))

    def test_repair_cannot_edit_reference_or_empty_patch_its_way_through(self):
        for mutation in ("reference", "empty"):
            with self.subTest(mutation=mutation):
                context, material = repair_case()
                if mutation == "empty":
                    material["patches"] = []
                else:
                    material["patches"][0].update(field="stakes", value="changed-reference-token")
                self.error("SCRIPT_READER_FIRST_REPAIR_INVALID", REPAIR,
                           with_material(REPAIR, context, material))
