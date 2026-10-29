import json
import unittest
from uuid import uuid4

from scripts.full_flow.simulation.contracts import ScriptContractError, TemplateCatalog, digest
from scripts.full_flow.simulation.inventory import INVENTORY_SCHEMA
from scripts.full_flow.simulation.quickstart_builders import BuilderContext
from scripts.full_flow.simulation.runtime_adapter import create_runtime_tape

from .test_contracts import ENDPOINTS, USAGE
from .test_keyflow_protocols import (
    SOURCE_REVIEW_TEMPLATE,
    SOURCE_REVIEW_VERSIONS,
    native_source_review_case,
)
from .test_quickstart_builders import native_cast_case


class RuntimeAdapterTests(unittest.TestCase):
    def catalog(self):
        return TemplateCatalog(
            {
                "schema": INVENTORY_SCHEMA,
                "parse_failures": [],
                "dynamic_sites": [],
                "templates": [{"template": "conception_logline_regen", "historical_only": False}],
            }
        )

    def test_runtime_send_identity_and_reuse_are_bound_without_sdk_or_network(self):
        # Only P3's pure matcher is invoked when the coordinator later runs this
        # test; no transport installation, SDK/client, server, DB or model.
        from e2e_sim.transport import NativeObservation, wire_fingerprints

        body = {"model": "offline", "messages": [{"role": "user", "content": "runtime-token"}]}
        observation = NativeObservation("conception_logline_regen", 1, str(uuid4()), str(uuid4()))
        tape = create_runtime_tape(
            catalog=self.catalog(),
            context_resolver=lambda obs, wire: BuilderContext(
                {"logline": "runtime-answer"}, call_index=obs.call_index
            ),
            usage_resolver=lambda obs, wire: USAGE,
            allowed_endpoints=ENDPOINTS,
        )
        self.assertEqual(
            tape._choose(body, wire_fingerprints(body), observation)["content"], "runtime-answer"
        )
        self.assertEqual(tape.builder_send_count, 1)
        with self.assertRaises(ScriptContractError) as raised:
            tape._choose(body, wire_fingerprints(body), observation)
        self.assertEqual(raised.exception.code, "SCRIPT_SEND_IDENTITY_REUSED")

    def test_projectless_send_uses_actual_wire_hash_and_unknown_template_stays_red(self):
        from e2e_sim.transport import NativeObservation, wire_fingerprints

        body = {"model": "offline", "messages": [{"role": "user", "content": "runtime-token"}]}
        tape = create_runtime_tape(
            catalog=self.catalog(),
            context_resolver=lambda obs, wire: BuilderContext(
                {"logline": "runtime-answer"}, call_index=obs.call_index
            ),
            usage_resolver=lambda obs, wire: USAGE,
            allowed_endpoints=ENDPOINTS,
        )
        observed = NativeObservation("conception_logline_regen", 1, None, None)
        tape._choose(body, wire_fingerprints(body), observed)
        self.assertNotIn("project_id", tape.document["entries"][0]["match"])
        self.assertEqual(
            tape.document["entries"][0]["match"]["body_sha256"],
            wire_fingerprints(body)["body_sha256"],
        )
        with self.assertRaises(ScriptContractError) as raised:
            tape._choose(
                body,
                wire_fingerprints(body),
                NativeObservation("unknown_new_template", 1, None, None),
            )
        self.assertEqual(raised.exception.code, "SCRIPT_TEMPLATE_UNCOVERED")
        self.assertEqual(tape.builder_send_count, 1)


class NativeCastRuntimeAdapterTests(unittest.TestCase):
    def catalog(self):
        # A catalogue snapshot may predate the two literal native part names.
        # Only the explicit new registrations may extend this local catalogue.
        return TemplateCatalog(
            {
                "schema": INVENTORY_SCHEMA,
                "parse_failures": [],
                "dynamic_sites": [],
                "templates": [],
            }
        )

    def tape(self, template, material, wire, *, catalog=None):
        return create_runtime_tape(
            catalog=self.catalog() if catalog is None else catalog,
            context_resolver=lambda observed, actual_wire: BuilderContext(
                {},
                call_index=observed.call_index,
                expected_wire_sha256=digest(actual_wire),
                submissions={template + ":" + str(observed.call_index): material},
            ),
            usage_resolver=lambda observed, actual_wire: USAGE,
            allowed_endpoints=ENDPOINTS,
        )

    def test_cast_parts_use_text_json_and_actual_wire_with_old_and_new_catalogues(self):
        from e2e_sim.transport import NativeObservation, wire_fingerprints

        for template in ("planner_cast_person_part", "planner_cast_links_part"):
            for includes_parts in (False, True):
                with self.subTest(template=template, catalogue_includes_parts=includes_parts):
                    _, material, wire = native_cast_case(template)
                    inventory = self.catalog().inventory
                    if includes_parts:
                        inventory["templates"] = [{"template": template, "historical_only": False}]
                    catalog = TemplateCatalog(inventory)
                    before = catalog.names
                    tape = self.tape(template, material, wire, catalog=catalog)
                    observed = NativeObservation(template, 17, str(uuid4()), str(uuid4()))
                    selected = tape._choose(wire, wire_fingerprints(wire), observed)
                    self.assertEqual(json.loads(selected["content"]), material)
                    self.assertEqual(selected["finish_reason"], "stop")
                    self.assertFalse(selected.get("tool_calls"))
                    self.assertEqual(tape.document["entries"][0]["match"]["call_index"], 17)
                    self.assertEqual(tape.builder_send_count, 1)
                    self.assertEqual(catalog.names, before)

    def test_cast_text_protocol_rejects_an_unexpected_tool_channel_without_an_entry(self):
        from e2e_sim.transport import NativeObservation, wire_fingerprints

        template = "planner_cast_links_part"
        _, material, wire = native_cast_case(template)
        wire["tools"] = [
            {
                "type": "function",
                "function": {"name": "submit_cast_spec", "parameters": {"type": "object"}},
            }
        ]
        tape = self.tape(template, material, wire)
        with self.assertRaises(ScriptContractError) as raised:
            tape._choose(wire, wire_fingerprints(wire), NativeObservation(template, 17))
        self.assertEqual(raised.exception.code, "SCRIPT_TOOL_PROTOCOL_UNCOVERED")
        self.assertEqual(tape.document["entries"], [])
        self.assertEqual(tape.builder_send_count, 0)


class SourceReviewRuntimeAdapterTests(unittest.TestCase):
    def catalog(self, *, includes_review=False):
        return TemplateCatalog(
            {
                "schema": INVENTORY_SCHEMA,
                "parse_failures": [],
                "dynamic_sites": [],
                "templates": (
                    [{"template": SOURCE_REVIEW_TEMPLATE, "historical_only": False}]
                    if includes_review
                    else []
                ),
            }
        )

    def tape(self, catalog, material, version, *, stale_hash=False):
        return create_runtime_tape(
            catalog=catalog,
            context_resolver=lambda observed, wire: BuilderContext(
                {},
                call_index=observed.call_index,
                expected_wire_sha256="0" * 64 if stale_hash else digest(wire),
                protocol_subject={"prompt_version": version},
                submissions={SOURCE_REVIEW_TEMPLATE + ":" + str(observed.call_index): material},
            ),
            usage_resolver=lambda observed, wire: USAGE,
            allowed_endpoints=ENDPOINTS,
        )

    def test_both_versions_accept_old_and_new_catalogues_as_text_without_invented_wire_version(
        self,
    ):
        from e2e_sim.transport import NativeObservation, wire_fingerprints

        for version in SOURCE_REVIEW_VERSIONS:
            for includes_review in (False, True):
                with self.subTest(version=version, catalogue_includes_review=includes_review):
                    _, material, wire = native_source_review_case(version, codec="string-pool")
                    catalog = self.catalog(includes_review=includes_review)
                    before = catalog.names
                    tape = self.tape(catalog, material, version)
                    observed = NativeObservation(
                        SOURCE_REVIEW_TEMPLATE, 23, str(uuid4()), str(uuid4())
                    )
                    selected = tape._choose(wire, wire_fingerprints(wire), observed)
                    self.assertEqual(json.loads(selected["content"]), material)
                    self.assertEqual(selected["finish_reason"], "stop")
                    self.assertFalse(selected.get("tool_calls"))
                    self.assertNotIn("prompt_version", wire)
                    self.assertEqual(tape.document["entries"][0]["match"]["call_index"], 23)
                    self.assertEqual(tape.builder_send_count, 1)
                    self.assertEqual(catalog.names, before)

    def test_stale_wire_and_unexpected_tool_channel_do_not_append_a_tape_entry(self):
        from e2e_sim.transport import NativeObservation, wire_fingerprints

        for mode in ("stale_wire", "unexpected_tool"):
            with self.subTest(mode=mode):
                version = "4.2-stable-catalog"
                _, material, wire = native_source_review_case(version)
                if mode == "unexpected_tool":
                    wire["tools"] = [
                        {
                            "type": "function",
                            "function": {
                                "name": "submit_planning_admission",
                                "parameters": {"type": "object"},
                            },
                        }
                    ]
                tape = self.tape(self.catalog(), material, version, stale_hash=mode == "stale_wire")
                with self.assertRaises(ScriptContractError) as raised:
                    tape._choose(
                        wire,
                        wire_fingerprints(wire),
                        NativeObservation(SOURCE_REVIEW_TEMPLATE, 23),
                    )
                self.assertEqual(
                    raised.exception.code,
                    "SCRIPT_SOURCE_WIRE_MATCH_REQUIRED"
                    if mode == "stale_wire"
                    else "SCRIPT_TOOL_PROTOCOL_UNCOVERED",
                )
                self.assertEqual(tape.document["entries"], [])
                self.assertEqual(tape.builder_send_count, 0)
