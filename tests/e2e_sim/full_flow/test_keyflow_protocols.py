from copy import deepcopy
from dataclasses import replace
import hashlib
import json
import unittest

from scripts.full_flow.simulation.contracts import ScriptContractError, canonical, digest
from scripts.full_flow.simulation.keyflow_protocols import protocol_for
from scripts.full_flow.simulation.quickstart_builders import (
    BuilderContext,
    build_payload,
    builder_inventory,
)
from scripts.full_flow.simulation.wire_material import bind_authored_material


class KeyflowTests(unittest.TestCase):
    def context(self, template, payload, *, wire=None, subject=None):
        wire = wire or {
            "model": "offline",
            "messages": [{"role": "user", "content": "native-source-token"}],
        }
        return BuilderContext(
            {},
            call_index=7,
            wire_body=wire,
            expected_wire_sha256=digest(wire),
            protocol_subject=subject,
            submissions={template + ":7": payload},
        )

    def test_explicit_candidate_payload_is_preserved_not_manufactured(self):
        template = "concept_tournament_candidate"
        payload = {"concept": "concept", "mechanism": "mechanism", "hook_question": "question"}
        self.assertEqual(
            build_payload(template, self.context(template, payload))["payload"], payload
        )
        with self.assertRaises(ScriptContractError):
            build_payload(template, self.context(template, {}))

    def test_fidelity_false_is_not_recomputed_to_pass(self):
        template = "conception_explicit_seed_fidelity_judge"
        payload = {
            "passed": False,
            "hard_conflicts": [],
            "compatible_extensions": [],
            "preserved_facts": [],
            "repair_directives": [],
        }
        self.assertFalse(
            build_payload(template, self.context(template, payload))["payload"]["passed"]
        )
        payload.pop("passed")
        with self.assertRaises(ScriptContractError) as raised:
            build_payload(template, self.context(template, payload))
        self.assertEqual(raised.exception.code, "SCRIPT_REPLY_FIELDS_REQUIRED")

    def test_quote_must_be_exact_native_wire_and_verdict_cannot_be_schema_selected(self):
        wire = {"source": "native-source-token", "schema": {"enum": [True]}}
        payload, proof = bind_authored_material(
            {"passed": False, "source_quote": {"$wire": "/source", "quote": "source-token"}},
            wire,
            expected_wire_sha256=digest(wire),
        )
        self.assertEqual(payload["source_quote"], "source-token")
        self.assertFalse(proof["verdicts_inferred"])
        for material, code in (
            ({"source_quote": "invented"}, "SCRIPT_SOURCE_VALUE_REQUIRES_NATIVE_WIRE"),
            ({"passed": {"$wire": "/schema/enum/0"}}, "SCRIPT_VERDICT_MUST_BE_AUTHORED"),
        ):
            with self.assertRaises(ScriptContractError) as raised:
                bind_authored_material(material, wire, expected_wire_sha256=digest(wire))
            self.assertEqual(raised.exception.code, code)

    def test_packaging_candidate_digest_binds_current_subject(self):
        subject = {"title": "runtime-title", "synopsis": "runtime-synopsis"}
        suffix = hashlib.sha256(
            json.dumps(subject, ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest()[:20]
        name = "conception_final_packaging_seed_fidelity_judge_candidate_v2_" + suffix
        wire = {
            "messages": [{"role": "user", "content": subject["title"] + "\n" + subject["synopsis"]}]
        }
        payload = {"passed": False, "hard_conflicts": [], "repair_directives": []}
        result = build_payload(name, self.context(name, payload, wire=wire, subject=subject))
        self.assertFalse(result["payload"]["passed"])
        with self.assertRaises(ScriptContractError) as raised:
            build_payload(
                name,
                self.context(
                    name, payload, wire=wire, subject={**subject, "synopsis": "different"}
                ),
            )
        self.assertEqual(raised.exception.code, "SCRIPT_PACKAGING_SUBJECT_MISMATCH")

    def test_wire_drift_and_unknown_protocol_never_reuse_verdict(self):
        name = "concept_tournament_judge"
        context = self.context(name, {"freshness": 1, "reason": "explicit-negative"})
        context.wire_body["messages"][0]["content"] = "different-body"
        with self.assertRaises(ScriptContractError) as raised:
            build_payload(name, context)
        self.assertEqual(raised.exception.code, "SCRIPT_SOURCE_WIRE_MATCH_REQUIRED")
        with self.assertRaises(ScriptContractError):
            protocol_for("concept_tournament_unknown_stage")


SOURCE_REVIEW_TEMPLATE = "planner_source_bound_expansion_semantic_review"
SOURCE_REVIEW_VERSIONS = ("4.2-stable-catalog", "4.3-field-presence")
SOURCE_REVIEW_CODECS = (
    "literal",
    "lossless-v1",
    "lossless-v2",
    "lossless-v3",
    "catalog-v1",
    "catalog-v2",
    "string-pool",
)


def render_source_review_wire(data, *, codec="literal", identity_context=None, suffix=""):
    """Structural native wire, using real pure codecs only when main runs tests.

    No planner, capacity preparation, app, DB, HTTP or provider is invoked.
    Hash headers follow planner._approved_payload_hash's JSON representation.
    """
    if codec not in SOURCE_REVIEW_CODECS:
        raise AssertionError("Unknown test codec")
    base = data["authority_and_baseline"]
    header = (
        "Review the immutable source and candidate. Return JSON only.\n"
        "authority_snapshot_hash=" + digest(base["authority_snapshot"]) + "\n"
        "canonical_baseline_hash=" + digest(base) + "\n"
        "candidate_hash=" + digest(data["derived_candidates"]) + "\n"
    )
    if codec == "literal":
        content = "\n".join(key + "=" + canonical(value) for key, value in data.items())
    else:
        from bestseller.services.lossless_source_encoding import encode_lossless_source

        if codec.startswith("lossless-v"):
            encoded = encode_lossless_source(data, version=int(codec[-1]))
        elif codec.startswith("catalog-v"):
            from bestseller.services.planning_catalog_transport import encode

            encoded = encode(data, version=int(codec[-1]))
        elif codec == "string-pool":
            from bestseller.services.planning_string_pool import encode

            encoded = encode(encode_lossless_source(data, version=3))
        else:
            raise AssertionError("Unknown test codec")
        content = "Native lossless review data follows.\nsource_review_data=" + canonical(encoded)
    if identity_context is not None:
        content += "\nidentity_review_context=" + canonical(identity_context)
    return {
        "model": "offline-source-review",
        "messages": [
            {"role": "system", "content": "Bounded source-fidelity review; JSON text only."},
            {"role": "user", "content": header + content + suffix},
        ],
        "stream": True,
    }


def native_source_review_case(version, *, codec="literal", field_state="empty", identity=False):
    """Runtime atoms, explicit judgments, and native catalog/presence wire shapes.

    Literal option rows use the planner's documented output fields. Presence
    IDs are compiled by the actual native pure API, not invented in the reply.
    The fixture author deliberately picks each evidence row and relation.
    """
    if version not in SOURCE_REVIEW_VERSIONS:
        raise AssertionError("Unknown test version")
    controls = {"scope": "fixture-explicit-controls"}
    base = {
        "authority_snapshot": {"engine": "source-engine-0"},
        "world_spec": {"locations": [{"name": "source-place-0"}]},
        "cast_spec": {"protagonist": {"name": "source-actor-0", "goal": "source-goal-0"}},
        "explicit_author_controls": controls,
    }
    candidate = {
        "world_spec": {"locations": [{"name": "candidate-place-0"}]},
        "cast_spec": {"protagonist": {"name": "source-actor-0", "goal": "candidate-goal-0"}},
    }
    fields = ["world.locations", "cast.protagonist"]
    source_options = [
        {"quote_id": "s0", "source_path": "authority_snapshot.engine", "quote": "source-engine-0"},
        {"quote_id": "s1", "source_path": "cast_spec.protagonist.goal", "quote": "source-goal-0"},
    ]
    candidate_options = [
        {"quote_id": "c0", "field": fields[0], "quote": "candidate-place-0"},
        {"quote_id": "c1", "field": fields[1], "quote": "candidate-goal-0"},
    ]
    world_source, world_candidate = source_options[0], candidate_options[0]
    if version == "4.3-field-presence":
        from bestseller.services.source_bound_field_evidence import compile_field_presence_options

        if field_state == "absent":
            candidate["world_spec"].pop("locations")
        elif field_state == "null":
            candidate["world_spec"]["locations"] = None
        elif field_state == "empty":
            candidate["world_spec"]["locations"] = []
        else:
            raise AssertionError("Unknown test field state")
        world_source = compile_field_presence_options(base, fields=[fields[0]], side="source")[0]
        world_candidate = compile_field_presence_options(
            candidate, fields=[fields[0]], side="candidate"
        )[0]
        source_options.append(world_source)
        candidate_options[0] = world_candidate
    data = {
        "source_quote_options": source_options,
        "candidate_quote_options": candidate_options,
        "explicit_author_controls": controls,
        "candidate_quote_field_bindings": {
            fields[0]: [world_candidate["quote_id"]],
            fields[1]: [candidate_options[1]["quote_id"]],
        },
        "changed_fields": fields,
        "authority_and_baseline": base,
        "derived_candidates": candidate,
    }
    if codec.startswith("catalog-v"):
        # The shared native catalog codec applies only with this actual input
        # shape. Ordinary expansion data has no input.source_authority branch.
        data["input"] = {
            "source_authority": {
                "canonical_source": {"root": ["/authority_snapshot/engine"]},
                "candidate_order": {"root": ["/world_spec/locations"]},
            }
        }
    material = {
        "decision": "revise",
        "core_preserved": False,
        "core_conflicts": ["explicit-author-core-conflict-0"],
        "quality_findings": ["explicit-author-quality-finding-0"],
        "authority_snapshot_hash": digest(base["authority_snapshot"]),
        "candidate_hash": digest(candidate),
        "source_evidence": [
            {
                "field": fields[0],
                "quote_id": world_source["quote_id"],
                "source_relation": "explicit-author-source-relation-0",
            },
            {
                "field": fields[1],
                "quote_id": source_options[1]["quote_id"],
                "source_relation": "explicit-author-source-relation-1",
            },
        ],
        "candidate_evidence": [
            {
                "field": fields[0],
                "quote_id": world_candidate["quote_id"],
                "source_relation": "explicit-author-candidate-relation-0",
            },
            {
                "field": fields[1],
                "quote_id": candidate_options[1]["quote_id"],
                "source_relation": "explicit-author-candidate-relation-1",
            },
        ],
        "feedback": "explicit-author-narrow-feedback",
    }
    identity_context = None
    if identity:
        identity_context = {
            "canonical_protagonist": base["cast_spec"]["protagonist"]["name"],
            "approved_full_premise": "identity-source-token-0",
            "candidate_gender": "unknown",
            "selected_identity_evidence": "identity-source-token-0",
            "source_quote_id": source_options[1]["quote_id"],
            "candidate_quote_id": candidate_options[1]["quote_id"],
        }
        material["protagonist_identity_supported"] = False
    wire = render_source_review_wire(data, codec=codec, identity_context=identity_context)
    return data, material, wire


class SourceBoundExpansionReviewTests(unittest.TestCase):
    def context(self, material, wire, *, version="4.2-stable-catalog", ordinal=23):
        # Expected native request version is out-of-band; do not invent an SDK
        # body.prompt_version field or derive a judgment from the selected mode.
        return BuilderContext(
            {},
            call_index=ordinal,
            wire_body=wire,
            expected_wire_sha256=digest(wire),
            protocol_subject={"prompt_version": version},
            submissions={SOURCE_REVIEW_TEMPLATE + ":" + str(ordinal): deepcopy(material)},
        )

    def reject(self, code, context):
        with self.assertRaises(ScriptContractError) as raised:
            build_payload(SOURCE_REVIEW_TEMPLATE, context)
        self.assertEqual(raised.exception.code, code)

    def test_one_literal_template_registers_an_explicit_json_reply_without_wildcard_repairs(self):
        registered = builder_inventory()["requires_explicit_physical_reply"]
        self.assertEqual(registered.get(SOURCE_REVIEW_TEMPLATE), "json")
        with self.assertRaises(ScriptContractError) as raised:
            build_payload(SOURCE_REVIEW_TEMPLATE + "_repair", BuilderContext({}))
        self.assertEqual(raised.exception.code, "SCRIPT_TEMPLATE_UNCOVERED")

    def test_both_versions_preserve_authored_revise_and_uncertain_without_mutation(self):
        for version in SOURCE_REVIEW_VERSIONS:
            for decision, core in (("revise", False), ("uncertain", None)):
                with self.subTest(version=version, decision=decision):
                    _, material, wire = native_source_review_case(version)
                    material.update(decision=decision, core_preserved=core)
                    before = deepcopy((material, wire))
                    result = build_payload(
                        SOURCE_REVIEW_TEMPLATE, self.context(material, wire, version=version)
                    )
                    self.assertEqual(result, {"format": "json", "payload": material})
                    self.assertEqual((material, wire), before)

    def test_every_native_codec_and_retry_suffix_preserve_the_exact_authored_reply(self):
        for version in SOURCE_REVIEW_VERSIONS:
            for codec in SOURCE_REVIEW_CODECS:
                with self.subTest(version=version, codec=codec):
                    data, material, _ = native_source_review_case(version, codec=codec)
                    wire = render_source_review_wire(
                        data,
                        codec=codec,
                        suffix="\nSTRICT FORMAT RETRY: identical immutable candidate.",
                    )
                    result = build_payload(
                        SOURCE_REVIEW_TEMPLATE, self.context(material, wire, version=version)
                    )
                    self.assertEqual(result["payload"], material)

    def test_missing_authored_fields_private_slot_and_current_wire_never_get_defaults(self):
        _, material, wire = native_source_review_case("4.2-stable-catalog")
        for field in material:
            with self.subTest(missing=field):
                missing = deepcopy(material)
                missing.pop(field)
                self.reject("SCRIPT_REPLY_FIELDS_REQUIRED", self.context(missing, wire))
        context = self.context(material, wire)
        context.submissions.clear()
        self.reject("SCRIPT_SOURCE_BOUND_REPLY_REQUIRED", context)
        context.submissions[SOURCE_REVIEW_TEMPLATE + ":22"] = material
        self.reject("SCRIPT_SOURCE_BOUND_REPLY_REQUIRED", context)
        self.reject(
            "SCRIPT_SOURCE_WIRE_MATCH_REQUIRED",
            replace(self.context(material, wire), wire_body=None),
        )
        context = self.context(material, wire)
        wire["messages"][1]["content"] += "\nchanged physical retry"
        self.reject("SCRIPT_SOURCE_WIRE_MATCH_REQUIRED", context)

    def test_unknown_version_and_presence_version_mismatch_are_explicitly_rejected(self):
        _, material, wire = native_source_review_case("4.3-field-presence")
        self.reject(
            "SCRIPT_SOURCE_REVIEW_VERSION_UNCOVERED",
            self.context(material, wire, version="4.4-unregistered"),
        )
        self.reject("SCRIPT_SOURCE_REVIEW_VERSION_MISMATCH", self.context(material, wire))

    def test_schema_or_wire_values_cannot_author_judgments_or_source_relations(self):
        _, material, wire = native_source_review_case("4.2-stable-catalog")
        for field in (
            "decision",
            "core_preserved",
            "core_conflicts",
            "quality_findings",
            "feedback",
        ):
            with self.subTest(field=field):
                invalid = deepcopy(material)
                invalid[field] = {"$wire": "/model"}
                self.reject("SCRIPT_VERDICT_MUST_BE_AUTHORED", self.context(invalid, wire))
        for side in ("source_evidence", "candidate_evidence"):
            with self.subTest(side=side):
                invalid = deepcopy(material)
                invalid[side][0]["source_relation"] = {"$wire": "/model"}
                self.reject("SCRIPT_VERDICT_MUST_BE_AUTHORED", self.context(invalid, wire))

    def test_authored_pass_requires_full_evidence_and_correct_hashes_not_an_empty_return(self):
        for version in SOURCE_REVIEW_VERSIONS:
            with self.subTest(version=version):
                _, material, wire = native_source_review_case(version)
                material.update(
                    decision="pass", core_preserved=True, core_conflicts=[], quality_findings=[]
                )
                self.assertEqual(
                    build_payload(
                        SOURCE_REVIEW_TEMPLATE, self.context(material, wire, version=version)
                    )["payload"],
                    material,
                )
                for side in ("source_evidence", "candidate_evidence"):
                    invalid = deepcopy(material)
                    invalid[side] = []
                    self.reject(
                        "SCRIPT_SOURCE_REVIEW_EVIDENCE_MISMATCH",
                        self.context(invalid, wire, version=version),
                    )
                for field in ("authority_snapshot_hash", "candidate_hash"):
                    invalid = deepcopy(material)
                    invalid[field] = "0" * 64
                    self.reject(
                        "SCRIPT_SOURCE_REVIEW_HASH_MISMATCH",
                        self.context(invalid, wire, version=version),
                    )

    def test_quote_ids_require_current_catalog_membership_field_ownership_and_full_coverage(self):
        _, material, wire = native_source_review_case("4.2-stable-catalog")
        for mode in (
            "unknown",
            "unknown_source",
            "cross_field",
            "duplicate",
            "missing",
            "foreign_field",
            "raw_quote",
            "blank_relation",
        ):
            with self.subTest(mode=mode):
                invalid = deepcopy(material)
                rows = invalid["candidate_evidence"]
                if mode == "unknown":
                    rows[0]["quote_id"] = "not-on-current-wire"
                elif mode == "unknown_source":
                    invalid["source_evidence"][0]["quote_id"] = "not-on-current-wire"
                elif mode == "cross_field":
                    rows[0]["quote_id"] = rows[1]["quote_id"]
                elif mode == "duplicate":
                    rows.append(deepcopy(rows[0]))
                elif mode == "missing":
                    rows.pop()
                elif mode == "foreign_field":
                    rows[0]["field"] = "cast.supporting_cast"
                elif mode == "raw_quote":
                    rows[0]["quote"] = "invented-quote-token"
                else:
                    rows[0]["source_relation"] = ""
                self.reject("SCRIPT_SOURCE_REVIEW_EVIDENCE_MISMATCH", self.context(invalid, wire))

    def test_literal_catalog_quotes_and_field_bindings_must_match_current_payloads(self):
        for mode in ("source_quote", "candidate_quote", "field_bindings"):
            with self.subTest(mode=mode):
                data, material, _ = native_source_review_case("4.2-stable-catalog")
                if mode == "source_quote":
                    data["source_quote_options"][0]["quote"] = "not-in-source-payload"
                elif mode == "candidate_quote":
                    data["candidate_quote_options"][0]["quote"] = "not-in-candidate-payload"
                else:
                    data["candidate_quote_field_bindings"]["world.locations"] = [
                        data["candidate_quote_options"][1]["quote_id"]
                    ]
                wire = render_source_review_wire(data)
                self.reject("SCRIPT_SOURCE_REVIEW_EVIDENCE_MISMATCH", self.context(material, wire))

    def test_presence_ids_distinguish_absent_null_empty_and_cannot_override_native_state(self):
        ids = set()
        for state in ("absent", "null", "empty"):
            with self.subTest(state=state):
                data, material, wire = native_source_review_case(
                    "4.3-field-presence", field_state=state
                )
                ids.add(material["candidate_evidence"][0]["quote_id"])
                result = build_payload(
                    SOURCE_REVIEW_TEMPLATE,
                    self.context(material, wire, version="4.3-field-presence"),
                )
                self.assertEqual(result["payload"], material)
                data["candidate_quote_options"][0]["value_hash"] = "0" * 64
                changed_wire = render_source_review_wire(data)
                self.reject(
                    "SCRIPT_SOURCE_REVIEW_EVIDENCE_MISMATCH",
                    self.context(material, changed_wire, version="4.3-field-presence"),
                )
        self.assertEqual(len(ids), 3)

    def test_current_candidate_and_source_changes_cannot_reuse_old_hashes_or_quotes(self):
        for side in ("source", "candidate"):
            with self.subTest(side=side):
                data, material, _ = native_source_review_case("4.2-stable-catalog")
                if side == "source":
                    data["authority_and_baseline"]["authority_snapshot"]["engine"] = (
                        "changed-engine"
                    )
                else:
                    data["derived_candidates"]["cast_spec"]["protagonist"]["goal"] = "changed-goal"
                current_wire = render_source_review_wire(data)
                self.reject(
                    "SCRIPT_SOURCE_REVIEW_HASH_MISMATCH", self.context(material, current_wire)
                )

    def test_native_envelope_integrity_unknown_codecs_and_ambiguous_blocks_fail_closed(self):
        for codec in SOURCE_REVIEW_CODECS[1:]:
            with self.subTest(codec=codec):
                _, material, wire = native_source_review_case("4.2-stable-catalog", codec=codec)
                content = wire["messages"][1]["content"]
                prefix, serialized = content.split("source_review_data=", 1)
                encoded = json.loads(serialized)
                if "original_sha256" in encoded:
                    encoded["original_sha256"] = "0" * 64
                else:
                    encoded["receipt"]["original_sha256"] = "0" * 64
                wire["messages"][1]["content"] = prefix + "source_review_data=" + canonical(encoded)
                self.reject("SCRIPT_SOURCE_REVIEW_WIRE_INVALID", self.context(material, wire))
        _, material, wire = native_source_review_case("4.2-stable-catalog", codec="lossless-v1")
        content = wire["messages"][1]["content"]
        prefix, serialized = content.split("source_review_data=", 1)
        encoded = json.loads(serialized)
        encoded["schema"] = "lossless-source-encoding.v99"
        wire["messages"][1]["content"] = prefix + "source_review_data=" + canonical(encoded)
        self.reject("SCRIPT_SOURCE_REVIEW_WIRE_INVALID", self.context(material, wire))
        data, material, wire = native_source_review_case("4.2-stable-catalog", codec="lossless-v1")
        wire["messages"][1]["content"] += "\nsource_review_data=" + canonical(data)
        self.reject("SCRIPT_SOURCE_REVIEW_WIRE_INVALID", self.context(material, wire))

    def test_identity_branch_requires_an_authored_boolean_and_preserves_explicit_false(self):
        _, material, wire = native_source_review_case("4.2-stable-catalog", identity=True)
        result = build_payload(SOURCE_REVIEW_TEMPLATE, self.context(material, wire))
        self.assertIs(result["payload"]["protagonist_identity_supported"], False)
        missing = deepcopy(material)
        missing.pop("protagonist_identity_supported")
        self.reject("SCRIPT_REPLY_FIELDS_REQUIRED", self.context(missing, wire))
        invalid = deepcopy(material)
        invalid["protagonist_identity_supported"] = {"$wire": "/model"}
        self.reject("SCRIPT_VERDICT_MUST_BE_AUTHORED", self.context(invalid, wire))

    def test_identity_only_baseline_does_not_replace_the_original_authority_header_hash(self):
        data, material, wire = native_source_review_case("4.2-stable-catalog", identity=True)
        narrow = digest(data["authority_and_baseline"]["authority_snapshot"])
        complete = digest(
            {
                "original_authority": data["authority_and_baseline"]["authority_snapshot"],
                "additional_source_field": "original-source-token",
            }
        )
        self.assertNotEqual(narrow, complete)
        wire["messages"][1]["content"] = wire["messages"][1]["content"].replace(
            "authority_snapshot_hash=" + narrow, "authority_snapshot_hash=" + complete, 1
        )
        material["authority_snapshot_hash"] = complete
        result = build_payload(SOURCE_REVIEW_TEMPLATE, self.context(material, wire))
        self.assertEqual(result["payload"], material)
