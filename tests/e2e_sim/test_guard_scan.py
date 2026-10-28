"""Metadata-only whole-candidate audit; never authorizes export."""

import json
import unittest

from support import planning_prefix_capture as prefix


class GuardScanTests(unittest.TestCase):
    def test_collects_every_field_and_value_block_without_exposing_values(self):
        document = {
            "tables": {
                "workflow_runs": [
                    {
                        "metadata_json": {
                            "policy": {"authorization_ref": "PRIVATE_REFERENCE"},
                            "headers": {"secret": "PRIVATE_STORY"},
                        }
                    },
                    {"metadata_json": {"cast": {"secret": "人物Abc12345往事"}}},
                ]
            },
            "note": "Bearer " + "PRIVATE_TOKEN",
        }
        before = json.dumps(document, ensure_ascii=False)
        findings = prefix.scan_credential_fields(document)
        self.assertEqual(len(findings), 4)
        self.assertEqual({x["reason"] for x in findings}, {"credential_field", "credential_value"})
        self.assertTrue(any("[1]" in x["path"] for x in findings))
        encoded = json.dumps(findings, ensure_ascii=False)
        for value in ("PRIVATE_REFERENCE", "PRIVATE_STORY", "PRIVATE_TOKEN", "人物Abc12345往事"):
            self.assertNotIn(value, encoded)
        self.assertNotIn("sha256", encoded)
        self.assertEqual(before, json.dumps(document, ensure_ascii=False))
        with self.assertRaises(prefix.PrefixError):
            prefix._no_credentials(document)

    def test_scalar_properties_only_and_no_value_hash(self):
        findings = prefix.scan_credential_fields({"secret": "人物Abc12345。"})
        self.assertEqual(len(findings), 1)
        item = findings[0]
        self.assertEqual(item["field_name"], "secret")
        self.assertEqual(item["value_type"], "str")
        self.assertEqual(item["length"], len("人物Abc12345。"))
        self.assertTrue(item["character_classes"]["han"])
        self.assertTrue(item["character_classes"]["ascii_run_8"])
        self.assertFalse(item["key_format"])
        self.assertNotIn("value", item)


class InternalReferenceTests(unittest.TestCase):
    def test_internal_reference_requires_full_grammar_not_a_uuid_substring(self):
        from uuid import uuid4

        from e2e_sim.guard_scan import parse_internal_reference

        identity = str(uuid4())
        digest = "a" * 64
        parsed = parse_internal_reference("workflow_runs:" + identity + ":" + digest)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed["namespace"], "workflow_runs")
        for text in (
            "human-label-" + identity,
            "workflow_runs:" + identity + ":" + digest + ":extra",
            "https://example.invalid/" + identity,
        ):
            self.assertIsNone(parse_internal_reference(text))

    def test_native_approval_identity_scope_and_content_hash_are_required(self):
        from types import SimpleNamespace
        from uuid import NAMESPACE_URL, UUID, uuid5

        from bestseller.services.prompt_source_policy import content_hash
        from e2e_sim.guard_scan import verify_reference_target

        project = UUID("fa14e681-2bd9-44f5-93d1-7f2ef9900d69")
        data = {
            "schema": "operator-action-approval.v1",
            "source": "trusted_operator_api_explicit_human_instruction",
            "purpose": "prefix_source_proposal",
            "binding": {"project_id": str(project), "target_id": str(project)},
            "capabilities": ["review_exact_prefix"],
            "actor": "operator",
            "authorization_ref": "external-label",
        }
        identity = uuid5(NAMESPACE_URL, "bestseller:operator-approval:" + content_hash(data))
        row = SimpleNamespace(
            id=identity,
            project_id=project,
            workflow_type="operator_action_approval",
            status="completed",
            scope_type="prefix_source_proposal",
            scope_id=project,
            metadata_json=data,
        )
        parsed = {
            "namespace": "workflow_runs",
            "id": identity,
            "content_sha256": content_hash(data),
        }
        self.assertTrue(verify_reference_target(parsed, row, project))
        parsed["content_sha256"] = "b" * 64
        self.assertFalse(verify_reference_target(parsed, row, project))
        parsed["content_sha256"] = content_hash(data)
        row.status = "running"
        self.assertFalse(verify_reference_target(parsed, row, project))


class CollectorSeparationTests(unittest.IsolatedAsyncioTestCase):
    async def test_audit_constructor_cannot_make_ordinary_capture_accept_blocked_body(self):
        from unittest.mock import AsyncMock, patch

        with (
            patch.object(
                prefix, "_prefix_candidate", AsyncMock(return_value={"secret": "PRIVATE"})
            ),
            patch.object(prefix, "_sha") as seal,
        ):
            with self.assertRaises(prefix.PrefixError):
                await prefix.capture_prefix(None, None, project_id="unused")
            seal.assert_not_called()


class AuditPrivacyRegressions(unittest.IsolatedAsyncioTestCase):
    async def test_payload_only_references_are_checked_without_values(self):
        from unittest.mock import AsyncMock

        from e2e_sim.guard_scan import audit_references

        session = AsyncMock()
        session.scalars.return_value = []
        candidate = {
            "tables": {
                "execution_budgets": [],
                "workflow_runs": [
                    {"metadata_json": {"authorization_ref": "PRIVATE_TABLE_REFERENCE"}}
                ],
            }
        }
        result = await audit_references(
            session,
            candidate,
            extra_references=[
                ("payload[1].request.authorization_ref", "PRIVATE_PAYLOAD_REFERENCE")
            ],
        )
        self.assertEqual(len(result), 2)
        self.assertTrue(any("payload[1]" in p for r in result for p in r["locations"]))
        self.assertTrue(all(r["explicit_target_record_exists"] is None for r in result))
        self.assertNotIn("PRIVATE_", json.dumps(result))

    async def test_artifact_type_and_hash_alone_are_not_authorization_proof(self):
        from types import SimpleNamespace
        from uuid import uuid4

        from bestseller.services.prompt_source_policy import content_hash
        from e2e_sim.guard_scan import verify_reference_target

        project = uuid4()
        identity = uuid4()
        content = {"schema": "fabricated"}
        row = SimpleNamespace(
            id=identity,
            project_id=project,
            artifact_type="completion_phase_handoff",
            status="validated",
            content=content,
        )
        parsed = {
            "namespace": "planning_artifact_versions",
            "id": identity,
            "content_sha256": content_hash(content),
        }
        self.assertFalse(verify_reference_target(parsed, row, project))

    async def test_payload_labels_come_from_ref_type_not_payload_values(self):
        from e2e_sim.guard_scan import payload_metadata

        metadata = payload_metadata("runtime/llm_payloads/opaque.request.json", 1)
        self.assertEqual(metadata["kind"], "request")
        self.assertEqual(metadata["file"], "payload[1].request")
        self.assertNotIn("opaque", json.dumps(metadata))


if __name__ == "__main__":
    unittest.main()
