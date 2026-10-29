import hashlib
from types import SimpleNamespace
import unittest

from scripts.full_flow.simulation.contracts import ScriptContractError
from scripts.full_flow.simulation.private_config import PrivateConfigResolver


class PrivateConfigTests(unittest.TestCase):
    def test_current_body_is_bound_to_native_wire_before_hashing(self):
        config = self.config()
        config["replies"][0]["current_body"] = {"$wire": "/messages/0/content"}
        resolver = PrivateConfigResolver(config)
        observed = SimpleNamespace(prompt_template="concept_tournament_candidate", call_index=9)
        ticket = {
            "global_ordinal": 9,
            "template_ordinal": 2,
            "native_operation_key": "1" * 64,
            "native_input_hash": "2" * 64,
        }
        result = resolver(observed, {"messages": [{"content": "selected-source"}]}, ticket)
        self.assertEqual(result.current_body, "selected-source")
        self.assertEqual(result.source_body_sha256, hashlib.sha256(b"selected-source").hexdigest())

    def config(self):
        return {
            "schema": "normal-quickstart-private.v1",
            "story": {},
            "replies": [
                {
                    "template": "concept_tournament_candidate",
                    "template_ordinal": 2,
                    "wire_assertions": [
                        {"pointer": "/messages/0/content", "contains": "selected-source"}
                    ],
                    "material": {
                        "concept": "authored",
                        "mechanism": "authored",
                        "hook_question": "authored",
                    },
                }
            ],
        }

    def test_explicit_durable_slot_and_source_assertion_select_one_private_reply(self):
        resolver = PrivateConfigResolver(self.config())
        observation = SimpleNamespace(prompt_template="concept_tournament_candidate", call_index=9)
        ticket = {
            "global_ordinal": 9,
            "template_ordinal": 2,
            "native_operation_key": "1" * 64,
            "native_input_hash": "2" * 64,
        }
        context = resolver(observation, {"messages": [{"content": "selected-source"}]}, ticket)
        self.assertEqual(context.call_index, 9)
        self.assertIn("concept_tournament_candidate:9", context.submissions)
        with self.assertRaises(ScriptContractError):
            resolver(observation, {"messages": [{"content": "other-source"}]}, ticket)

    def test_template_only_cannot_be_an_unbounded_positive_default(self):
        config = self.config()
        config["replies"][0].pop("template_ordinal")
        resolver = PrivateConfigResolver(config)
        with self.assertRaises(ScriptContractError) as raised:
            resolver(
                SimpleNamespace(prompt_template="concept_tournament_candidate", call_index=9),
                {"messages": [{"content": "selected-source"}]},
                {"global_ordinal": 9},
            )
        self.assertEqual(raised.exception.code, "SCRIPT_PRIVATE_REPLY_SLOT_REQUIRED")
