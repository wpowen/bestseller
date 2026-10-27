"""Run the saved-source factory cases against the integrated runtime module."""

import importlib.util
from copy import deepcopy
from pathlib import Path
import sys
from unittest.mock import patch
from support.semantic_input_history_fixture import history_select, parent_row

from bestseller.services import fanqie_short_semantic_inputs as runtime
from bestseller.services import fanqie_short_semantic_review as semantic
from bestseller.services.fanqie_short_semantic_protocol_recovery import descriptor
from bestseller.services.fanqie_short_semantic_consumer import digest
from bestseller.services.fanqie_short_opening_gate import evaluate_fanqie_short_opening_gate
from bestseller.services.fanqie_short_quality import review_whole_fanqie_short_story
from test_native_publication_title import synthetic_native_rows

STAGE = (
    Path(__file__).resolve().parents[2]
    / "reports/project-reliability-review-2026-10-24/implementation/whole-semantic-input-factory-stage"
)
spec = importlib.util.spec_from_file_location(
    "runtime_semantic_input_cases", STAGE / "test_semantic_inputs.py"
)
cases = importlib.util.module_from_spec(spec)
sys.path.insert(0, str(STAGE))
try:
    spec.loader.exec_module(cases)
finally:
    sys.path.remove(str(STAGE))
cases.factory = runtime
cases.current_v4 = lambda: semantic
cases.FakeSession.execute = history_select


class TestNativeSemanticInputs(cases.FactoryTests):
    """Includes source drift, full publication manifest and stable binding cases."""

    def publication(self):
        # Preserve the historical serialized JSON. Paid proof here is explicitly
        # synthetic and rebuilt using the currently packaged producer/code hash.
        evidence, context = synthetic_native_rows()
        self.project.metadata_json.update(deepcopy(evidence["project"]["metadata_json"]))
        self.project.title = evidence["project"]["title"]
        self.assembly.manifest = deepcopy(context["manifest"])
        self.assembly.full_text = context["full_text"]
        return evidence

    # Current factory contract overrides only the historical stage test here.
    # Keep the archived v4 method and serialized v4 proof bytes untouched.
    async def test_saved_native_fixture_selects_actual_rows_and_recomputes_original_report(self):
        before = deepcopy((self.project, self.assembly, self.session.rows))
        with patch.object(
            runtime, "review_whole_fanqie_short_story", wraps=review_whole_fanqie_short_story
        ) as review:
            result = await self.build()
        review.assert_called_once_with(
            self.assembly.full_text,
            title=self.project.title,
            unlock_line_ratio=0.30,
            protagonist_name="我",
        )
        expected = review_whole_fanqie_short_story(
            self.assembly.full_text,
            title=self.project.title,
            unlock_line_ratio=0.30,
            protagonist_name="我",
        ).to_dict()
        self.assertEqual(result["original_report"], expected)
        self.assertEqual(result["original_report"]["opening_report"],
                         evaluate_fanqie_short_opening_gate(self.assembly.full_text,
                             unlock_line_ratio=.30, protagonist_name="我").to_dict())
        self.assertNotEqual(result["original_report"], {"passed": True})
        self.assertEqual(result["current_scope"], "fanqie-adopted-whole")
        self.assertEqual(runtime.OPERATION_NAMESPACE, "fanqie-whole-semantic-v5")
        self.assertEqual(result["operation_namespace"], "fanqie-whole-semantic-v5")
        self.assertEqual(result["workflow_run_id"], str(self.wid))
        self.assertEqual(
            result["execution_scope_id"], self.project.metadata_json["execution_scope_id"]
        )
        self.assertEqual(
            set(result),
            {
                "project_id",
                "workflow_run_id",
                "current_scope",
                "full_text",
                "title",
                "protagonist",
                "manifest",
                "approved_contract",
                "original_report",
                "operation_namespace",
                "execution_scope_id",
                "unlock_line_ratio",
                "methodology_contract_mode",
                "recovery_parent",
            },
        )
        for name in ("canonical_snapshot", "intent_snapshot"):
            self.assertEqual(
                result["approved_contract"]["approval_evidence"][name],
                cases.APPROVAL["native_approval_evidence"][name],
            )
        self.assertEqual(len(self.session.reads), 3 + self.project.target_chapters)
        self.assertIsNone(result["recovery_parent"])
        self.assertNotIn("knowledge_closed", result)
        self.assertNotIn("ready_for_upload", result)
        self.assertEqual((self.project, self.assembly, self.session.rows), before)
        result["approved_contract"]["creation_intent_contract"]["concept_seed"] = "caller mutation"
        self.assertEqual((self.project, self.assembly, self.session.rows), before)

    async def test_parent_is_exact_native_failed_v4_descriptor_without_mutation(self):
        row = parent_row(self.project, self.assembly)
        before = deepcopy(row)
        self.session.semantic_history_fixture = [row]
        self.project.metadata_json["recovery_parent"] = {"caller_fake": True}
        result = await self.build()
        self.assertEqual(result["recovery_parent"], {
            "artifact_id": str(row["id"]), "workflow_id": str(row["source_run_id"]),
            "content_sha256": digest(before["content"]),
            "error": before["content"]["error"],
            "raw_response_sha256": semantic._sha(before["content"]["raw_response"])})
        self.assertEqual(result["recovery_parent"], descriptor(row))
        self.assertEqual(row, before)
        self.assertNotIn("report", result["recovery_parent"])
        self.assertNotIn("recovery_ordinal", result["recovery_parent"])
        result["recovery_parent"]["error"] = "caller mutation"
        self.assertEqual(row, before)

    async def test_parent_selection_never_uses_latest_success_foreign_or_other_source(self):
        for fault in ("no_parent", "success", "no_error", "blank_error", "wrong_schema", "foreign",
                      "other_body", "other_title", "other_type", "multiple"):
            with self.subTest(fault=fault):
                row = parent_row(self.project, self.assembly)
                rows = [row]
                if fault == "no_parent": rows = []
                elif fault == "success": row["status"] = "validated"
                elif fault == "no_error": row["content"]["error"] = None
                elif fault == "blank_error": row["content"]["error"] = " "
                elif fault == "wrong_schema": row["schema_version"] = "fanqie-short-semantic-stage.v5"
                elif fault == "foreign": row["project_id"] = cases.uuid4()
                elif fault == "other_body": row["content"]["request_payload"]["full_text"] += "changed"
                elif fault == "other_title": row["content"]["request_payload"]["title"] += "changed"
                elif fault == "other_type": row["artifact_type"] = "conception_snapshot"
                elif fault == "multiple": rows.append(parent_row(self.project, self.assembly))
                self.session.semantic_history_fixture = rows
                before = deepcopy(rows)
                result = await self.build()
                self.assertIsNone(result["recovery_parent"])
                self.assertEqual(rows, before)

    async def test_parent_read_failure_is_not_converted_to_fresh_review(self):
        async def broken(*_args, **_kwargs):
            raise RuntimeError("native history read failed")
        self.session.execute = broken
        with self.assertRaisesRegex(RuntimeError, "native history read failed"):
            await self.build()

    def publication_selection(self):
        """Synthetic wrapper routing fixture, never proof of native adoption.

        The mapper/reader owns actual WF, paid and physical joins. These cases
        test only current-input projection and preservation of the old node.
        """
        origin = self.publication()
        self.project.title = "我修好旧收音机时，已经交还了铺面钥匙"
        self.assembly.manifest["project"]["title"] = self.project.title
        self.project.metadata_json["title_paid_selection_lineage"] = {
            "schema": "native-paid-title-selection.v1",
            "fixture_provenance": "synthetic routing fixture, not native authority",
            "selected_title": self.project.title,
            "origin_workflow_run_id": origin["workflow"]["id"],
            "semantic_verdict_granted": False,
            "historical_promise_fulfillment_granted": False,
        }
        self.project.metadata_json["title_revision_status"] = {
            "schema": "native-paid-title-selection.v1",
            "quality_status": "delegated_selection_with_soft_debt",
            "adopted_title": self.project.title,
            "semantic_verdict_granted": False,
        }
        return {
            "schema": "native-paid-title-selection-evidence.v1",
            "project": runtime.project_title_projection(self.project),
            "selection_workflow": {"fixture_provenance": "synthetic, no DB row"},
            "selection_step": {"fixture_provenance": "synthetic, no DB row"},
            "origin": origin,
        }

    async def test_selection_wrapper_keeps_current_title_lineage_and_original_failed_approval(self):
        evidence = self.publication_selection()
        before = deepcopy((self.project, self.assembly, self.session.rows, evidence))
        with patch.object(runtime, "review_whole_fanqie_short_story",
                          wraps=review_whole_fanqie_short_story) as review:
            result = await self.build(native_title_evidence=evidence)
        review.assert_called_once_with(self.assembly.full_text, title=self.project.title,
                                       unlock_line_ratio=.30, protagonist_name="我")
        approval = result["approved_contract"]["approval_evidence"]
        old = evidence["origin"]["paid_return"]["plan"]["literal_source"]["old_native_title_metadata"]
        self.assertEqual(approval["title_revision_status"], old["title_revision_status"])
        self.assertNotEqual(approval["title_revision_status"], self.project.metadata_json["title_revision_status"])
        self.assertEqual(result["manifest"], self.assembly.manifest)
        self.assertEqual(result["title"], self.project.title)
        self.assertNotEqual(result["title"], evidence["origin"]["project"]["title"])
        proof = approval["native_publishing_title_evidence"]
        self.assertEqual(proof, evidence)
        self.assertEqual(proof["project"]["metadata_json"]["title_paid_selection_lineage"],
                         self.project.metadata_json["title_paid_selection_lineage"])
        self.assertEqual((self.project, self.assembly, self.session.rows, evidence), before)
        self.assertEqual(result["original_report"], review_whole_fanqie_short_story(
            self.assembly.full_text, title=self.project.title, unlock_line_ratio=.30,
            protagonist_name="我").to_dict())
        self.assertEqual(approval["canonical_snapshot"], cases.APPROVAL["native_approval_evidence"]["canonical_snapshot"])
        proof["origin"]["project"]["title"] = "caller mutation"
        self.assertEqual((self.project, self.assembly, self.session.rows, evidence), before)

    async def test_selection_wrapper_does_not_substitute_workflow_status_for_paid_literal_source(self):
        evidence = self.publication_selection()
        original = deepcopy(evidence["origin"]["paid_return"]["plan"]["literal_source"]["old_native_title_metadata"])
        # Factory must read the exact paid literal source path, not a convenient
        # new status or a workflow copy. Native mapper separately rejects this
        # deliberately inconsistent synthetic evidence.
        evidence["origin"]["workflow"]["metadata_json"]["literal_source"]["old_native_title_metadata"] = {
            "title_revision_status": {"quality_status": "caller_fake_success"}}
        evidence["selection_workflow"]["metadata_json"] = {
            "literal_source": {"old_native_title_metadata": {
                "title_revision_status": {"quality_status": "caller_fake_success"}}}}
        result = await self.build(native_title_evidence=evidence)
        self.assertEqual(result["approved_contract"]["approval_evidence"]["title_revision_status"],
                         original["title_revision_status"])

    async def test_selection_wrapper_rejects_current_lineage_and_original_source_drift(self):
        valid = self.publication_selection()
        for fault in ("missing_lineage", "changed_lineage", "missing_origin", "wrong_origin_schema",
                      "missing_paid_plan", "missing_failed_history", "manifest_control"):
            with self.subTest(fault=fault):
                evidence = deepcopy(valid)
                if fault == "missing_lineage":
                    evidence["project"]["metadata_json"].pop("title_paid_selection_lineage")
                elif fault == "changed_lineage":
                    evidence["project"]["metadata_json"]["title_paid_selection_lineage"]["selected_title"] += "漂移"
                elif fault == "missing_origin": evidence.pop("origin")
                elif fault == "wrong_origin_schema": evidence["origin"]["schema"] = "unknown"
                elif fault == "missing_paid_plan": evidence["origin"]["paid_return"].pop("plan")
                elif fault == "missing_failed_history":
                    evidence["origin"]["paid_return"]["plan"]["literal_source"].pop("old_native_title_metadata")
                else:
                    evidence["origin"]["paid_return"]["plan"]["literal_source"]["manifest"]["control_serial"] = -1
                with self.assertRaisesRegex(ValueError, "semantic_inputs:native_title_"):
                    await self.build(native_title_evidence=evidence)

    async def test_selection_export_fields_do_not_change_current_input_binding(self):
        evidence = self.publication_selection()
        before = await self.build(native_title_evidence=evidence)
        self.project.status = "exported"
        for key in ("delivery_receipt", "ready_for_upload", "export_status", "updated_at"):
            self.project.metadata_json[key] = {"volatile": True}
            evidence["project"]["metadata_json"][key] = {"volatile": True}
        after = await self.build(native_title_evidence=evidence)
        self.assertEqual(before, after)
        self.assertEqual(set(after["approved_contract"]["approval_evidence"]["native_publishing_title_evidence"]["project"]),
                         {"id", "title", "metadata_json"})

    def test_complete_selection_wrapper_routes_through_native_mapper_without_relabelling_origin(self):
        # Saved original paid input + synthetic selection rows from the owner;
        # this is pure consistency replay, never an actual selection readback.
        from types import SimpleNamespace
        from uuid import UUID
        from test_native_paid_title_selection_wrapper import selection_fixture
        from bestseller.services import native_publication_title as native

        wrapper, context = selection_fixture()
        before = deepcopy((wrapper, context))
        project = SimpleNamespace(id=UUID(wrapper["project"]["id"]),
                                  title=wrapper["project"]["title"],
                                  metadata_json=deepcopy(wrapper["project"]["metadata_json"]))
        proof, old_metadata = runtime._publication_evidence(wrapper, project, context["manifest"])
        contract = deepcopy(context["approved_contract"])
        contract["approval_evidence"]["native_publishing_title_evidence"] = proof
        contract["approval_evidence"]["title_revision_status"] = old_metadata["title_revision_status"]
        receipt = native.validate(proof, **{**context, "approved_contract": contract})
        self.assertEqual(receipt["route"], "native_publishing_title_revision")
        self.assertEqual(receipt["lineage_kind"], "delegated_paid_candidate_selection")
        self.assertEqual(proof["origin"]["project"]["title"], "修好旧收音机")
        self.assertEqual(proof["project"]["title"], "我修好旧收音机时，已经交还了铺面钥匙")
        self.assertEqual(proof["origin"], wrapper["origin"])
        self.assertEqual(contract["approval_evidence"]["title_revision_status"]["blocked"],
                         "title_revision_call_failed")
        self.assertEqual(contract["approval_evidence"]["canonical_snapshot"],
                         context["approved_contract"]["approval_evidence"]["canonical_snapshot"])
        self.assertEqual(receipt["native_evaluation"]["decision"], "revise")
        self.assertEqual(receipt["native_evaluation"]["checks"]["reader_attraction"]["score"], 52)
        self.assertEqual(receipt["editorial_quality_debt"][0]["status"], "open")
        for key in ("historical_promise_fulfillment_granted", "semantic_verdict_granted",
                    "whole_book_pass_granted", "export_authority_granted"):
            self.assertFalse(receipt[key])
        self.assertEqual((wrapper, context), before)
