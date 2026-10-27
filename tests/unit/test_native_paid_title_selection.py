"""Offline existing-paid-title selection tests, never a production adoption.

Saved native rows are replayed as historical input; no fresh DB certification is
claimed. Async seams use explicit synthetic sessions only for control flow.
"""
from __future__ import annotations

import ast
from copy import deepcopy
import json
from pathlib import Path
import socket
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
import asyncio

from bestseller.services import native_paid_title_selection as service

ROOT = Path(__file__).resolve().parents[2]
SAVED = ROOT / "reports/project-reliability-review-2026-10-24/implementation/original-four-whole-v4-execution.json"
SELECTED = "我修好旧收音机时，已经交还了铺面钥匙"


def fixture():
    if not SAVED.is_file():
        raise unittest.SkipTest("Saved native proof absent; no live DB/provider read is substituted")
    payload = json.loads(SAVED.read_text())["payload"]
    evidence = payload["approved_contract"]["approval_evidence"]["native_publishing_title_evidence"]
    context = {k: payload[k] for k in ("project_id", "title", "full_text", "manifest", "approved_contract")}
    delegation = {"original_task_id": service.op.TASK_ID,
            "actor": "assistant", "authorization_ref": "synthetic-original-task-delegation-reference-not-live-authority",
            "reason": "SYNTHETIC delegated editorial selection of a retained factual candidate; no human title acceptance"}
    args = {"context": context, "selected_title": SELECTED, "delegation": delegation,
            "project_lock_version": 0, "review_fence_sha256": "f" * 64,
            "current_metadata_sha256": service.op.fingerprint(evidence["project"]["metadata_json"])}
    return evidence, args


class PaidSelectionTests(unittest.TestCase):
    def setUp(self):
        for owner, name in ((socket.socket, "connect"), (socket.socket, "connect_ex"), (socket, "create_connection")):
            guard = patch.object(owner, name, side_effect=AssertionError("offline only"))
            guard.start(); self.addCleanup(guard.stop)

    def test_actual_original_proof_then_delegated_selection_retains_revise_and_debt(self):
        evidence, args = fixture(); before = deepcopy([evidence, args])
        plan = service.prepare_paid_title_selection(evidence, **args)
        self.assertEqual(plan["native_evaluation"]["decision"], "revise")
        self.assertFalse(plan["native_evaluation"]["checks"]["reader_attraction"]["passed"])
        self.assertEqual(plan["native_evaluation"]["checks"]["story_transmission"]["score"], 66)
        self.assertEqual(plan["binding"]["candidate_index"], 0)
        self.assertEqual(plan["binding"]["literal_start"], 0)
        b = plan["binding"];raw=evidence["paid_return"]["completion"]["content"]
        self.assertEqual(raw[b["literal_start"]:b["literal_end"]], SELECTED)
        self.assertNotEqual(plan["workflow_run_id"], evidence["workflow"]["id"])
        self.assertEqual(plan["provider_calls"], 0)
        self.assertFalse(plan["semantic_verdict_granted"])
        self.assertEqual(plan["adoption_basis"], "delegated_editorial_selection_from_existing_paid_candidates")
        self.assertEqual(plan["delegation"]["actor"], "assistant")
        debt = plan["editorial_quality_debt"][0]
        self.assertEqual(debt["status"], "open")
        self.assertEqual(debt["overall_score"], 52)
        self.assertEqual(debt["native_decision"], "revise")
        self.assertFalse(debt["commercial_passed"])
        self.assertEqual(debt["human_attraction_acceptance"], "not_performed")
        self.assertNotIn("explicit_author_selection", plan["delegation"])
        self.assertEqual([evidence, args], before)

    def test_old_six_character_proof_cannot_be_relabelled_to_long_title(self):
        for where in ("context", "origin_project"):
            e,a=fixture()
            if where=="context":
                a["context"]["title"]=SELECTED
                a["context"]["manifest"]["project"]["title"]=SELECTED
            else: e["project"]["title"]=SELECTED
            with self.subTest(where=where), self.assertRaises(service.PaidTitleSelectionError):
                service.prepare_paid_title_selection(e,**a)

    def test_raw_nonliteral_delegation_scope_and_control_drift_fail_closed(self):
        for mutation in ("nonliteral", "fake_human", "scope", "control", "manifest", "paid_bytes"):
            e,a=fixture()
            if mutation=="nonliteral":a["selected_title"]=SELECTED+"，一家团圆"
            elif mutation=="fake_human":a["delegation"]["actor"]="human"
            elif mutation=="scope":a["delegation"]["original_task_id"]="different"
            elif mutation=="control":a["context"]["manifest"]["control_serial"]+=1
            elif mutation=="manifest":a["context"]["manifest"]["sources"][0]["draft_id"]="11111111-1111-4111-8111-111111111111"
            else:e["paid_return"]["completion"]["content"]+="改动原付费候选"
            with self.subTest(mutation=mutation),self.assertRaises(service.PaidTitleSelectionError):
                service.prepare_paid_title_selection(e,**a)

    def test_other_non_attraction_native_failures_are_not_editorially_waived(self):
        e,a=fixture();other=e["paid_return"]["completion"]["content"].splitlines()[1]
        a["selected_title"]=other
        with self.assertRaisesRegex(service.PaidTitleSelectionError,"non_attraction_native_check_failed"):
            service.prepare_paid_title_selection(e,**a)

    def test_original_task_delegation_needs_no_new_human_exact_title_certificate(self):
        e,a=fixture();del a["delegation"]
        plan=service.prepare_paid_title_selection(e,**a)
        self.assertEqual(plan["delegation"]["actor"],"assistant")
        self.assertEqual(plan["delegation"]["original_task_id"],service.op.TASK_ID)
        self.assertIn("按计划修好原四章，不反复问",plan["delegation"]["authorization_ref"])
        self.assertNotIn("explicit_author_selection",plan["delegation"])

    def test_old_lineage_failed_history_canonical_and_raw_are_preserved(self):
        e,a=fixture();plan=service.prepare_paid_title_selection(e,**a)
        before=deepcopy(e["project"]["metadata_json"])
        metadata,lineage,record=service.selection_metadata(before,plan=plan,origin_project=e["project"])
        self.assertEqual(before,e["project"]["metadata_json"])
        self.assertEqual(metadata["title_repair_lineage"],before["title_repair_lineage"])
        self.assertEqual(metadata["title_revision_history"][:-1],before["title_revision_history"])
        self.assertEqual(metadata["title_revision_history"][-1]["previous_native_metadata"]["title_revision_status"],before["title_revision_status"])
        for key in ("book_design_snapshot","book_design_snapshot_hash","creation_canonical_snapshot_id","creation_canonical_snapshot_hash"):
            self.assertEqual(metadata[key],before[key])
        self.assertEqual(lineage["native_evaluation"]["decision"],"revise")
        self.assertEqual(record["llm_run_id"],e["llm_run"]["id"])
        self.assertEqual(metadata["title_revision_status"]["quality_status"],"delegated_selection_with_soft_debt")
        self.assertEqual(lineage["editorial_quality_debt"],plan["editorial_quality_debt"])

    def test_readonly_preflight_and_writer_have_no_model_or_internal_commit(self):
        e,a=fixture();plan=service.prepare_paid_title_selection(e,**a)
        session=SimpleNamespace(scalar=AsyncMock(return_value="on"))
        with patch.object(service,"_inspect",new=AsyncMock(return_value=(plan,None,None))) as inspect:
            out=asyncio.run(service.preflight_paid_title_selection(session,None,selected_title=SELECTED,
                delegation=a["delegation"],expected_manifest=a["context"]["manifest"]))
        self.assertEqual(out,plan);self.assertFalse(inspect.await_args.kwargs["lock"])
        session.scalar.return_value="off"
        with self.assertRaisesRegex(service.PaidTitleSelectionError,"readonly_transaction_required"):
            asyncio.run(service.preflight_paid_title_selection(session,None,selected_title=SELECTED,
                delegation=a["delegation"],expected_manifest=a["context"]["manifest"]))
        tree=ast.parse(Path(service.__file__).read_text())
        names={n.func.attr for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute)}
        self.assertTrue(names.isdisjoint({"complete_text","dispatch","commit","create_async_engine"}))

    def test_writer_revalidates_exact_source_before_any_mutation(self):
        e,a=fixture();plan=service.prepare_paid_title_selection(e,**a)
        project=SimpleNamespace(id=service.UUID(service.op.PROJECT_ID),title=e["project"]["title"],metadata_json=deepcopy(e["project"]["metadata_json"]))
        added=[];session=SimpleNamespace(get=AsyncMock(return_value=None),add=added.append,flush=AsyncMock())
        fresh=deepcopy(plan);fresh["binding"]["review_fence_sha256"]="0"*64
        with patch.object(service,"_inspect",new=AsyncMock(return_value=(fresh,project,e))):
            with self.assertRaisesRegex(service.PaidTitleSelectionError,"selection_source_or_delegation_changed"):
                asyncio.run(service.adopt_paid_title_selection(session,None,plan=plan))
        self.assertFalse(added);session.flush.assert_not_awaited()
        self.assertEqual(project.title,e["project"]["title"])
        with patch.object(service,"_inspect",new=AsyncMock(return_value=(plan,project,e))) as inspect:
            result=asyncio.run(service.adopt_paid_title_selection(session,None,plan=plan))
        self.assertTrue(inspect.await_args.kwargs["lock"])
        self.assertEqual(len(added),2)
        self.assertEqual(added[0].workflow_type,service.WORKFLOW_TYPE)
        self.assertEqual(added[0].metadata_json["origin_project_projection"],e["project"])
        self.assertEqual(added[0].requested_by,"assistant")
        self.assertEqual(added[0].metadata_json["editorial_quality_debt"],plan["editorial_quality_debt"])
        self.assertTrue(result["requires_commit_and_fresh_readback"])
        self.assertEqual(project.title,SELECTED)
        self.assertEqual(project.metadata_json["title_repair_lineage"],e["project"]["metadata_json"]["title_repair_lineage"])


if __name__=="__main__":
    unittest.main(verbosity=2)
