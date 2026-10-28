"""Authorized export projection must remove precisely one metadata leaf."""

from copy import deepcopy
import hashlib
import json
import unittest

from support.planning_prefix_exclusions import (
    exclude_workflow_authorization_ref,
    validate_exclusion_manifest,
)


def digest(value):
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


class PrefixExclusionTests(unittest.TestCase):
    def candidate(self):
        return {
            "project_id": "fa14e681-2bd9-44f5-93d1-7f2ef9900d69",
            "tables": {
                "workflow_runs": [
                    {
                        "id": "owner",
                        "metadata_json": {
                            "full_outline_revision": {
                                "authorization_ref": "PRIVATE_LABEL",
                                "source_sha256": "a" * 64,
                            },
                            "untouched": [1, 2],
                        },
                    }
                ]
            },
            "files": {"ref": {"sha256": "b" * 64, "base64": "original"}},
            "source_plan_sha256": "c" * 64,
        }

    def test_exact_projection_does_not_mutate_input_or_custody_hashes(self):
        source = self.candidate()
        before = deepcopy(source)
        projected = exclude_workflow_authorization_ref(source)
        self.assertEqual(source, before)
        expected = deepcopy(before)
        del expected["tables"]["workflow_runs"][0]["metadata_json"]["full_outline_revision"][
            "authorization_ref"
        ]
        self.assertEqual({k: v for k, v in projected.items() if k != "export_manifest"}, expected)
        item = projected["export_manifest"]["exclusions"][0]
        self.assertEqual(item["original_row_sha256"], digest(before["tables"]["workflow_runs"][0]))
        self.assertEqual(
            item["exported_row_sha256"], digest(expected["tables"]["workflow_runs"][0])
        )
        self.assertEqual(
            item["field_path"],
            "$.tables.workflow_runs[0].metadata_json.full_outline_revision.authorization_ref",
        )
        self.assertNotIn("PRIVATE_LABEL", json.dumps(projected))
        validate_exclusion_manifest(projected)
        from support.planning_prefix_capture import _no_credentials

        _no_credentials(projected)

    def test_only_authorized_source_and_exact_leaf_can_be_excluded(self):
        candidate = self.candidate()
        candidate["project_id"] = "other"
        with self.assertRaises(ValueError):
            exclude_workflow_authorization_ref(candidate)
        candidate = self.candidate()
        candidate["tables"]["workflow_runs"][0]["metadata_json"]["authorization_ref"] = "OTHER"
        projected = exclude_workflow_authorization_ref(candidate)
        self.assertEqual(
            projected["tables"]["workflow_runs"][0]["metadata_json"]["authorization_ref"], "OTHER"
        )

    def test_manifest_binds_projection_and_rejects_modified_or_reintroduced_leaf(self):
        projected = exclude_workflow_authorization_ref(self.candidate())
        projected["tables"]["workflow_runs"][0]["metadata_json"]["untouched"].append(3)
        with self.assertRaises(ValueError):
            validate_exclusion_manifest(projected)
        projected = exclude_workflow_authorization_ref(self.candidate())
        projected["tables"]["workflow_runs"][0]["metadata_json"]["full_outline_revision"][
            "authorization_ref"
        ] = "RETURNED"
        with self.assertRaises(ValueError):
            validate_exclusion_manifest(projected)

    def test_no_exclusion_is_legacy_compatible_but_cannot_claim_nonexistent_rows(self):
        validate_exclusion_manifest(self.candidate())
        projected = exclude_workflow_authorization_ref(self.candidate())
        projected["export_manifest"]["exclusions"][0]["row_id"] = "wrong"
        with self.assertRaises(ValueError):
            validate_exclusion_manifest(projected)
        projected = exclude_workflow_authorization_ref(self.candidate())
        with self.assertRaises(ValueError):
            exclude_workflow_authorization_ref(projected)


class CaptureOptInTests(unittest.IsolatedAsyncioTestCase):
    async def test_default_rejects_explicit_projection_succeeds_other_auth_stays_rejected(self):
        from unittest.mock import AsyncMock, patch

        from support import planning_prefix_capture as prefix

        candidate = PrefixExclusionTests().candidate()
        original = deepcopy(candidate)
        with patch.object(prefix, "_prefix_candidate", AsyncMock(return_value=candidate)):
            with self.assertRaises(prefix.PrefixError):
                await prefix.capture_prefix(None, None, project_id=candidate["project_id"])
            projected = await prefix.capture_prefix(
                None, None, project_id=candidate["project_id"], exclude_authorization_ref=True
            )
        self.assertEqual(candidate, original)
        self.assertEqual(
            projected["sha256"],
            digest({key: value for key, value in projected.items() if key != "sha256"}),
        )
        candidate["tables"]["workflow_runs"][0]["metadata_json"]["headers"] = {
            "authorization": "PRIVATE_OTHER"
        }
        with patch.object(prefix, "_prefix_candidate", AsyncMock(return_value=candidate)):
            with self.assertRaises(prefix.PrefixError):
                await prefix.capture_prefix(
                    None, None, project_id=candidate["project_id"], exclude_authorization_ref=True
                )
