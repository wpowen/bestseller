import importlib.util
import json
from types import SimpleNamespace
import unittest
from uuid import uuid4

from scripts.full_flow.simulation.contracts import (
    CallIdentity,
    Limits,
    ScriptContractError,
    TemplateCatalog,
    build_document,
    text_response,
    tool_response,
    validate_response,
)
from scripts.full_flow.simulation.inventory import INVENTORY_SCHEMA

ENDPOINTS = [
    {"scheme": "https", "host": "simulation.invalid", "port": 443, "path": "/v1/chat/completions"}
]
USAGE = {"prompt_tokens": 3, "completion_tokens": 4}


def catalog():
    return TemplateCatalog(
        {
            "schema": INVENTORY_SCHEMA,
            "parse_failures": [],
            "dynamic_sites": [],
            "templates": [
                {"template": name, "historical_only": False}
                for name in ("chapter_first_writer", "chapter_review")
            ],
        }
    )


class ContractTests(unittest.TestCase):
    def error(self, code, fn):
        with self.assertRaises(ScriptContractError) as raised:
            fn()
        self.assertEqual(raised.exception.code, code)

    def test_unknown_template_has_explicit_code_and_no_default_return(self):
        self.error(
            "SCRIPT_TEMPLATE_UNCOVERED",
            lambda: text_response(
                catalog(), "missing_new_template", "", usage=USAGE, finish_reason="stop"
            ),
        )

    def test_any_new_project_or_projectless_wire_hash_works_without_retained_book_uuid(self):
        project, workflow = str(uuid4()), str(uuid4())
        observed = SimpleNamespace(
            prompt_template="chapter_first_writer",
            call_index=1,
            project_id=project,
            workflow_run_id=workflow,
        )
        identity = CallIdentity.from_observation(observed)
        response = text_response(
            catalog(), identity.prompt_template, "", usage=USAGE, finish_reason="stop"
        )
        document = build_document(catalog(), [(identity, [response])], allowed_endpoints=ENDPOINTS)
        self.assertEqual(document["entries"][0]["match"]["project_id"], project)
        projectless = CallIdentity("chapter_first_writer", 1, body_sha256="a" * 64)
        match = projectless.match(catalog(), Limits())
        self.assertNotIn("project_id", match)
        self.assertEqual(match["body_sha256"], "a" * 64)
        self.error(
            "SCRIPT_IDENTITY_UNBOUND",
            lambda: CallIdentity("chapter_first_writer", 1).match(catalog(), Limits()),
        )

    def test_selector_overlap_and_unbounded_sequence_are_rejected(self):
        project = str(uuid4())
        identity = CallIdentity("chapter_first_writer", 1, project_id=project)
        spec = text_response(
            catalog(), "chapter_first_writer", "", usage=USAGE, finish_reason="stop"
        )
        self.error(
            "SCRIPT_SELECTOR_AMBIGUOUS",
            lambda: build_document(
                catalog(), [(identity, [spec]), (identity, [spec])], allowed_endpoints=ENDPOINTS
            ),
        )
        self.error(
            "SCRIPT_CALL_INDEX_UNBOUNDED",
            lambda: CallIdentity("chapter_first_writer", True, project_id=project).match(
                catalog(), Limits()
            ),
        )
        self.error(
            "SCRIPT_RESPONSE_UNBOUNDED",
            lambda: text_response(
                catalog(),
                "chapter_first_writer",
                "x" * 1024,
                usage=USAGE,
                finish_reason="stop",
                limits=Limits(max_response_bytes=64),
            ),
        )

    def test_faults_keep_length_filter_timeout_and_disconnect_as_explicit_modes(self):
        for finish in ("length", "content_filter"):
            response = text_response(
                catalog(), "chapter_review", "", usage=USAGE, finish_reason=finish
            )
            self.assertEqual(response["finish_reason"], finish)
        validate_response({"mode": "timeout"})
        validate_response(
            {
                "mode": "disconnect",
                "disconnect_after_chunks": 1,
                "usage": USAGE,
                "content": "",
                "finish_reason": "length",
            }
        )

    def test_remote_schema_reference_is_rejected_before_validation(self):
        body = {
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "submit_chapter_critique",
                        "parameters": {"$ref": "https://simulation.invalid/schema"},
                    },
                }
            ]
        }
        self.error(
            "SCRIPT_SCHEMA_REMOTE_REF",
            lambda: tool_response(
                catalog(),
                "chapter_review",
                body,
                tool_name="submit_chapter_critique",
                arguments={},
                usage=USAGE,
            ),
        )

    @unittest.skipUnless(
        importlib.util.find_spec("jsonschema"), "native jsonschema dependency unavailable"
    )
    def test_tool_uses_exact_runtime_enum_and_rejects_invented_source_id(self):
        tool = {
            "type": "function",
            "function": {
                "name": "submit_chapter_critique",
                "parameters": {
                    "type": "object",
                    "properties": {"source_id": {"enum": ["runtime-anchor-1"]}},
                    "required": ["source_id"],
                    "additionalProperties": False,
                },
            },
        }
        body = {"tools": [tool], "tool_choice": "required"}
        response = tool_response(
            catalog(),
            "chapter_review",
            body,
            tool_name="submit_chapter_critique",
            arguments={"source_id": "runtime-anchor-1"},
            usage=USAGE,
        )
        self.assertEqual(
            json.loads(response["tool_calls"][0]["function"]["arguments"]),
            {"source_id": "runtime-anchor-1"},
        )
        self.error(
            "SCRIPT_TOOL_ARGUMENT_SCHEMA_MISMATCH",
            lambda: tool_response(
                catalog(),
                "chapter_review",
                body,
                tool_name="submit_chapter_critique",
                arguments={"source_id": "invented-anchor"},
                usage=USAGE,
            ),
        )
        self.error(
            "SCRIPT_TOOL_UNCOVERED",
            lambda: tool_response(
                catalog(),
                "chapter_review",
                body,
                tool_name="different_tool",
                arguments={},
                usage=USAGE,
            ),
        )
