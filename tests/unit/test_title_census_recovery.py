# Adapted from the reviewed R1 11 cases + R2 5 cases, preserving their
# counterexamples. Historical fixtures are read-only and explicitly synthetic.
"""Explicit SYNTHETIC closure fixtures. No DB, sockets, settings or model calls.

The saved production preparation provides field shape only, never PASS/KG proof.
"""
from __future__ import annotations

import ast
import asyncio
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace as Row
import unittest
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[2]
STAGE = ROOT / "reports/project-reliability-review-2026-10-24/implementation/title-census-recovery-stage"
REV = STAGE / "revision-2"
from bestseller.services.native_title_operator import title_operator as op
from bestseller.services.native_title_operator.title_operator_native import NativeRuntime

SYNTHETIC_BODY = "SYNTHETIC ONLY，非实际成稿。\r\n已采纳来源🧪。\r第二行。"


def load(name):
    return json.loads((STAGE.parent / name).read_text())


def fixture():
    """Saved manifest/body/Canon + explicitly synthetic post-recovery authority."""
    saved = load("four-adopted-after-v12-corrections-readback.json")
    native = load("original-native-creative-approval-readback.json")
    canonical = deepcopy(native["native_approval_evidence"]["canonical_snapshot"])
    manifest = deepcopy(saved["source_manifest"])
    chapters, drafts = [], []
    for source in manifest["sources"]:
        chapters.append({"id": source["chapter_id"], "chapter_number": source["chapter_number"],
                         "status": "complete", "production_state": "ok", "metadata": {}})
        drafts.append({"id": source["draft_id"], "chapter_id": source["chapter_id"],
            "version_no": source["lineage"]["source_version_no"], "promotion_state": "promoted",
            "is_current": True, "body_sha256": source["raw_body_sha256"]})
    s, c, d = manifest["sources"][-1], chapters[-1], drafts[-1]
    # These data are synthetic and must never serve as an expected production manifest.
    s["raw_body_sha256"] = d["body_sha256"] = op.sha(SYNTHETIC_BODY)
    s["lineage"]["source_body_sha256"] = s["raw_body_sha256"]
    body = {"schema": "chapter_review_body_v1", "algorithm": "sha256", "normalization": "newlines_lf_only",
            "digest": op.sha(SYNTHETIC_BODY.replace("\r\n", "\n").replace("\r", "\n"))}
    proof = deepcopy(load("ch4-v12-truncation-recovery-preparation.jsonl")
        ["original_ch4_truncation_recovery"]["prepared_native_cycle"]["waiting_origin_proof"])
    old_id, new_id = proof["source_cycle_id"], op.sha("chapter-critic-cycle.v1\n" + op.PROJECT_ID + "\n"
        + c["id"] + "\n" + body["digest"] + "\n" + proof["current_policy_source_hash"])
    parent_id, owner_id = proof["previous_workflow_run_id"], "9205b0d6-2eff-40c2-89d6-121c3c98c3d4"
    old_report, old_quality = proof["latest_report_id"], proof["latest_quality_score_id"]
    new_report, new_quality = "5a5a0020-43f8-4335-869e-8d7368d2a5f0", "e34b073a-0ffc-4b45-956b-a99b4bfe57ac"
    llm_id = "11111111-1111-4111-8111-111111111111"
    record = {"state": "completed", "outcome": "unknown", "draft_id": s["draft_id"],
        "body_fingerprint": body, "review_report_id": old_report, "quality_score_id": old_quality}
    old = {"schema_version": "chapter-critic-cycle.v1", "cycle_id": old_id, "state": "waiting_infrastructure",
        "source_draft_id": s["draft_id"], "source_body_hash": body["digest"],
        "review_workflow_run_id": parent_id, "policy_source_hash": proof["previous_policy_source_hash"],
        "semantic_dependencies_sha256": proof["semantic_dependencies_sha256"],
        "attempts": [{"state": "waiting_infrastructure", "infrastructure_history": [record]}]}
    proof.update(source_body_fingerprint=body, source_cycle_sha256=op.fingerprint(old))
    proof["authorization_ref"] = "SYNTHETIC ONLY / NOT AN EXECUTION AUTHORIZATION"
    proof["proof_sha256"] = op.fingerprint({k: v for k, v in proof.items() if k != "proof_sha256"})
    active = {"cycle_id": new_id, "state": "completed", "source_draft_id": s["draft_id"],
        "source_body_hash": body["digest"], "policy_source_hash": proof["current_policy_source_hash"],
        "execution_scope_id": op.SCOPE, "previous_workflow_run_id": parent_id,
        "result_workflow_run_id": owner_id, "waiting_origin_proof": proof}
    final = {**record, "outcome": "available", "review_report_id": new_report, "quality_score_id": new_quality,
             "rewrite_task_id": None, "review_result": {"verdict": "pass"}}
    current = {**old, "cycle_id": new_id, "state": "completed", "review_workflow_run_id": owner_id,
        "policy_source_hash": proof["current_policy_source_hash"], "attempts": [final]}
    c["metadata"] = {"chapter_critic_cycles": {old_id: old, new_id: current},
        "chapter_review_active_cycle": active, "chapter_review_recovery_cycles": [deepcopy(active)]}
    parent = {"id": parent_id, "project_id": op.PROJECT_ID, "scope_id": c["id"], "scope_type": "chapter",
        "workflow_type": "chapter_pipeline", "status": "machine_blocked", "current_step": "chapter_adoption_required",
        "metadata": {"execution_scope_id": op.SCOPE, "critic_cycle_id": old_id,
            "review_report_id": old_report, "quality_score_id": old_quality,
            "terminal_state_history": [{"status": "machine_blocked", "step": "waiting_infrastructure"}],
            "review_recovery_policy": {"cycle_id": new_id, "waiting_origin_proof": deepcopy(proof)}}}
    owner = {**parent, "id": owner_id, "status": "completed", "current_step": "completed", "metadata": {
        "execution_scope_id": op.SCOPE, "critic_cycle_id": new_id, "review_report_id": new_report,
        "quality_score_id": new_quality, "final_verdict": "pass", "requires_human_review": False,
        "actual_adoption_receipt": {"adopted": True, "draft_id": s["draft_id"],
                                   "body_fingerprint": body, "promotion_state": "promoted"}}}
    def report(rid, verdict):
        return {"id": rid, "project_id": op.PROJECT_ID, "target_type": "chapter", "target_id": c["id"],
            "verdict": verdict, "llm_run_id": llm_id if verdict == "pass" else None, "structured_output": {
                "draft_id": s["draft_id"], "reviewed_body_fingerprint": deepcopy(body), "rewrite_instructions": None,
                "critic_evidence": {"status": "verified" if verdict == "pass" else "unavailable", "passed": verdict == "pass"}}}
    def quality(qid, rid, current):
        return {"id": qid, "project_id": op.PROJECT_ID, "target_type": "chapter", "target_id": c["id"],
                "chapter_draft_version_id": s["draft_id"], "review_report_id": rid, "is_current": current}
    return {"test_fixture_authority": "SYNTHETIC ONLY; cannot witness real closure",
        "schema": "title-operator-source.v1", "project_id": op.PROJECT_ID, "project_slug": op.SLUG,
        "manifest": manifest, "full_text": saved["full_text"], "knowledge_closed": [1, 2, 3, 4],
        "chapters": chapters, "drafts": drafts, "open_workflows": [], "open_rewrite_tasks": [],
        "canonical_snapshot": canonical, "design_hash_verified": True,
        "old_metadata": {"book_design_snapshot_status": "locked"},
        "packaging_rows": [], "active_packaging_contract_id": None,
        "dispatch_admissible": True, "adoption_admissible": True,
        "recovery_evidence": {"schema": "title-census-recovery-evidence.v1", "workflows": [parent, owner],
            "reports": [report(old_report, "unknown"), report(new_report, "pass")],
            "quality_scores": [quality(old_quality, old_report, False), quality(new_quality, new_report, True)],
            "llm_runs": [{"id": llm_id, "project_id": op.PROJECT_ID, "workflow_run_id": owner_id}],
            "knowledge": [{"id": c["id"], "draft_id": s["draft_id"], "raw_body_sha256": s["raw_body_sha256"],
                "reviewed_body_fingerprint": body, "lineage": deepcopy(s["lineage"]),
                "status": {k: True for k in ("feedback_complete", "snapshot_complete", "bible_complete", "closed")}}]}}


class Offline(unittest.TestCase):
    def setUp(self):
        for target in ("socket.socket.connect", "socket.create_connection",
                       "sqlalchemy.ext.asyncio.create_async_engine", "sqlalchemy.create_engine"):
            guard = patch(target, side_effect=AssertionError("OFFLINE TEST: external I/O forbidden"))
            guard.start(); self.addCleanup(guard.stop)


class CensusTests(Offline):
    def test_explicit_closed_recovery_only_exempts_old_waiting_without_mutation(self):
        s = fixture(); before = deepcopy(s)
        self.assertEqual(op.open_review_reasons(s), [])
        op.check_snapshot(s, s["manifest"])
        self.assertEqual(s, before)
        old = next(iter(s["chapters"][-1]["metadata"]["chapter_critic_cycles"].values()))
        self.assertEqual(old["state"], "waiting_infrastructure")
        self.assertEqual(old["attempts"][0]["infrastructure_history"][0]["outcome"], "unknown")

    def test_fail_closed_boundary_matrix(self):
        # Mutate actual evidence, not just convenience active status flags.
        cases = {
            "no_lineage": ("chapters", 3, "metadata", "chapter_review_recovery_cycles"),
            "wrong_parent": ("chapters", 3, "metadata", "chapter_review_active_cycle", "previous_workflow_run_id"),
            "wrong_source": ("chapters", 3, "metadata", "chapter_review_active_cycle", "source_draft_id"),
            "wrong_body": ("recovery_evidence", "knowledge", 0, "raw_body_sha256"),
            "wrong_report": ("recovery_evidence", "workflows", 1, "metadata", "review_report_id"),
            "wrong_review_verdict": ("recovery_evidence", "reports", 1, "verdict"),
            "wrong_owner": ("recovery_evidence", "llm_runs", 0, "workflow_run_id"),
            "foreign_owner": ("recovery_evidence", "workflows", 1, "project_id"),
            "owner_not_closed": ("recovery_evidence", "workflows", 1, "status"),
            "active_not_closed": ("chapters", 3, "metadata", "chapter_review_active_cycle", "state"),
            "KG_not_closed": ("recovery_evidence", "knowledge", 0, "status", "closed"),
            "KG_snapshot_not_closed": ("recovery_evidence", "knowledge", 0, "status", "snapshot_complete"),
            "no_adoption": ("recovery_evidence", "workflows", 1, "metadata", "actual_adoption_receipt", "adopted"),
            "unknown_new_evidence": ("recovery_evidence", "reports", 1, "structured_output", "critic_evidence", "passed"),
        }
        for name, path in cases.items():
            with self.subTest(name=name):
                s = fixture(); target = s
                for key in path[:-1]: target = target[key]
                target[path[-1]] = False if isinstance(target[path[-1]], bool) else "SYNTHETIC MISMATCH"
                if path[:4] == ("chapters", 3, "metadata", "chapter_review_active_cycle"):
                    meta=s["chapters"][-1]["metadata"]
                    meta["chapter_review_recovery_cycles"]=[deepcopy(meta["chapter_review_active_cycle"])]
                self.assertIn("unclosed_critic_cycle", op.open_review_reasons(s))

    def test_proof_hash_cycle_bytes_and_actual_old_report_stay_bound(self):
        for kind in ("proof_digest", "source_cycle_digest", "source_cycle_id", "old_record", "old_report"):
            with self.subTest(kind=kind):
                s = fixture(); meta = s["chapters"][-1]["metadata"]; active = meta["chapter_review_active_cycle"]
                proof = active["waiting_origin_proof"]
                if kind == "proof_digest": proof["proof_sha256"] = "0" * 64
                elif kind.startswith("source_cycle"):
                    proof["source_cycle_sha256" if kind.endswith("digest") else "source_cycle_id"] = "0" * 64
                    proof["proof_sha256"] = op.fingerprint({k:v for k,v in proof.items() if k != "proof_sha256"})
                    meta["chapter_review_recovery_cycles"] = [deepcopy(active)]
                    s["recovery_evidence"]["workflows"][0]["metadata"]["review_recovery_policy"]["waiting_origin_proof"] = deepcopy(proof)
                elif kind == "old_record": meta["chapter_critic_cycles"][proof["source_cycle_id"]]["terminal"] = "unknown-new"
                else: s["recovery_evidence"]["reports"][0]["structured_output"]["draft_id"] = "wrong-source"
                self.assertIn("unclosed_critic_cycle", op.open_review_reasons(s))

    def test_completed_active_flag_alone_and_prepared_real_origin_are_not_proof(self):
        s = fixture(); del s["recovery_evidence"]
        self.assertIn("unclosed_critic_cycle", op.open_review_reasons(s))
        s = fixture()
        actual = load("ch4-v12-truncation-recovery-preparation.jsonl")["original_ch4_truncation_recovery"]["prepared_native_cycle"]
        s["chapters"][-1]["metadata"]["chapter_review_active_cycle"] = actual
        # Prepared recovery may have a different synthetic body/cycle ID: add it
        # to the critic map only to exercise the unresolved census, no fake PASS.
        s["chapters"][-1]["metadata"]["chapter_critic_cycles"][actual["cycle_id"]] = deepcopy(actual)
        self.assertIn("unclosed_critic_cycle", op.open_review_reasons(s))

    def test_current_new_candidate_active_UNKNOWN_and_financial_unknown_still_block(self):
        s = fixture(); d = deepcopy(s["drafts"][-1]); d.update(id="synthetic-new-candidate", promotion_state="candidate")
        s["drafts"].append(d)
        self.assertIn("unclosed_new_candidate:" + d["id"], op.open_review_reasons(s))
        s = fixture(); meta=s["chapters"][-1]["metadata"]
        meta["chapter_critic_cycles"][meta["chapter_review_active_cycle"]["cycle_id"]]["attempts"][0]["outcome"] = "unknown"
        self.assertIn("unclosed_critic_cycle", op.open_review_reasons(s))
        for paid in (False, True):
            s = fixture(); s["dispatch_admissible"] = s["adoption_admissible"] = False
            with self.assertRaisesRegex(op.Stop, "paid_adoption_not_permitted" if paid else "execution_scope_not_open"):
                op.check_snapshot(s, s["manifest"], paid=paid)

    def test_unrelated_samebody_waiting_is_not_covered(self):
        s=fixture(); meta=s["chapters"][-1]["metadata"]
        old=deepcopy(next(iter(meta["chapter_critic_cycles"].values())))
        old["cycle_id"]="unrelated-synthetic-cycle"; meta["chapter_critic_cycles"][old["cycle_id"]]=old
        self.assertIn("unclosed_critic_cycle", op.open_review_reasons(s))

    def test_known_older_pointer_needs_strict_current_auto_PASS_and_KG(self):
        # Use actual ch1/ch3 pointer/source identities as SHAPE ONLY. The added
        # versions/current flags and full authority joins remain SYNTHETIC.
        actual=load("original-four-title-census-readback.json")
        for number in (1,3):
            with self.subTest(chapter=number):
                s=fixture(); c=s["chapters"][-1]; source=s["manifest"]["sources"][-1]
                meta=c["metadata"]; current=deepcopy(meta["chapter_critic_cycles"][meta["chapter_review_active_cycle"]["cycle_id"]])
                body=current["source_body_hash"]
                auto_id=op.sha("chapter-critic-cycle.v1\n"+op.PROJECT_ID+"\n"+c["id"]+"\n"+body+"\ninitial")
                current["cycle_id"]=auto_id
                s["recovery_evidence"]["workflows"][1]["metadata"]["critic_cycle_id"]=auto_id
                projected=actual["chapters"][number-1]
                old_active=deepcopy(projected["active"])
                old_cycle=deepcopy(projected["cycles"][old_active["cycle_id"]])
                # Only historical source classification is consumed for this
                # record. Its projected attempts are never used as a PASS.
                meta.update(chapter_review_active_cycle=old_active,chapter_review_recovery_cycles=[],
                    chapter_critic_cycles={old_active["cycle_id"]:old_cycle,auto_id:current})
                s["drafts"].append({"id":old_active["source_draft_id"],"chapter_id":c["id"],
                    "is_current":False,"version_no":source["lineage"]["source_version_no"]-1,
                    "promotion_state":"superseded","body_sha256":"synthetic-raw",
                    "reviewed_body_fingerprint":{"digest":old_active["source_body_hash"]}})
                before=deepcopy(s)
                self.assertEqual(op.open_review_reasons(s),[])
                self.assertEqual(s,before)
                for path,value in [(("drafts",4,"is_current"),True),
                                   (("drafts",4,"version_no"),source["lineage"]["source_version_no"]),
                                   (("drafts",4,"reviewed_body_fingerprint","digest"),"wrong-body"),
                                   (("recovery_evidence","reports",1,"verdict"),"unknown"),
                                   (("recovery_evidence","knowledge",0,"status","closed"),False)]:
                    bad=deepcopy(s);target=bad
                    for key in path[:-1]: target=target[key]
                    target[path[-1]]=value
                    self.assertTrue(op.open_review_reasons(bad),path)

    def test_saved_actual_projection_matches_lineage_shape_but_is_not_certificate(self):
        actual=load("original-four-title-census-readback.json")
        c=actual["chapters"][-1];active=c["active"];proof=active["waiting_origin_proof"]
        old=c["cycles"][proof["source_cycle_id"]];current=c["cycles"][active["cycle_id"]]
        self.assertEqual(proof["source_cycle_sha256"],old["source_cycle_sha256"])
        self.assertNotEqual(op.fingerprint(old),proof["source_cycle_sha256"])
        self.assertEqual(active["previous_workflow_run_id"],old["review_workflow_run_id"])
        self.assertEqual(active["result_workflow_run_id"],current["review_workflow_run_id"])
        self.assertEqual(current["attempts"][-1]["review_report_id"],"5a5a0020-43f8-4335-869e-8d7368d2a5f0")
        reports={r["id"]:r for r in actual["reports"]}
        self.assertEqual(reports[current["attempts"][-1]["review_report_id"]]["verdict"],"pass")
        # Complete native rows, quality joins, owner metadata, LLM owner and
        # fresh KG proof are absent from this projection; never fill them in.
        s=fixture();s["recovery_evidence"]=actual
        self.assertIn("unclosed_critic_cycle",op.open_review_reasons(s))


class NativeReaderTests(Offline):
    def test_actual_native_reader_serializes_source_rows_and_fresh_KG_not_boolean_alias(self):
        from bestseller.services import adopted_knowledge_resume
        from bestseller.services.reviews import chapter_review_body_fingerprint
        s=fixture(); evidence=s["recovery_evidence"]; source=s["manifest"]["sources"][-1]
        manifest={"sources":[source]}
        workflows=[Row(**{**w, "metadata_json": w["metadata"]}) for w in evidence["workflows"]]
        reports=[Row(**r) for r in evidence["reports"]]
        qualities=[Row(**q) for q in evidence["quality_scores"]]; runs=[Row(**r) for r in evidence["llm_runs"]]
        session=Row(scalars=AsyncMock(side_effect=[qualities,runs]))
        chapter=Row(metadata_json=s["chapters"][-1]["metadata"])
        draft=Row(id=source["draft_id"],content_md=SYNTHETIC_BODY)
        with patch.object(adopted_knowledge_resume, "read_adopted_knowledge", new=AsyncMock(
            return_value=(source["lineage"], evidence["knowledge"][0]["status"]))) as kg:
            result=asyncio.run(NativeRuntime()._recovery_evidence(session,manifest,[chapter],[draft],workflows,reports,lock=True))
        self.assertEqual(result,evidence)
        self.assertEqual(kg.await_args.kwargs["chapter_md"], SYNTHETIC_BODY)
        self.assertEqual(kg.await_args.kwargs["expected_source"],source["lineage"])
        self.assertIs(kg.await_args.kwargs["require_bible"], True)
        self.assertNotEqual(op.sha(SYNTHETIC_BODY), chapter_review_body_fingerprint(SYNTHETIC_BODY)["digest"])
        self.assertTrue(all(call.args[0]._for_update_arg is not None for call in session.scalars.await_args_list))

    def test_no_history_no_new_reads_and_native_KG_rejection_propagates(self):
        from bestseller.services import adopted_knowledge_resume
        session=Row(scalars=AsyncMock(return_value=[]))
        result=asyncio.run(NativeRuntime()._recovery_evidence(session,{},[Row(metadata_json={})],[],[],[]))
        self.assertIsNone(result); session.scalars.assert_not_awaited()
        s=fixture(); source=s["manifest"]["sources"][-1]
        with patch.object(adopted_knowledge_resume,"read_adopted_knowledge",new=AsyncMock(side_effect=ValueError("native_KG_changed"))):
            with self.assertRaisesRegex(ValueError,"native_KG_changed"):
                asyncio.run(NativeRuntime()._recovery_evidence(session,{"sources":[source]},
                    [Row(metadata_json=s["chapters"][-1]["metadata"])],
                    [Row(id=source["draft_id"],content_md=SYNTHETIC_BODY)],[],[]))

    def test_stage_mirrors_protected_hashes_fence_and_lock_order(self):
        # Historical runtime hashes belong to immutable preimages, never the
        # upgraded runtime. Current runtime must equal the reviewed R2 mirror.
        for path,digest in json.loads((STAGE/"protected-preimages.json").read_text()).items():
            original = STAGE/"preimage"/Path(path).name if path.startswith("src/") else ROOT/path
            self.assertEqual(op.sha(original.read_text()),digest,str(original))
        reviewed=json.loads((REV/"sourcehashes.json").read_text())["files"]
        for name in ("title_operator.py","title_operator_native.py"):
            active=REV/"active-operator-candidate"/name
            self.assertEqual(op.sha(active.read_text()),reviewed["active-operator-candidate/"+name])
            self.assertEqual(active.read_bytes(),(REV/"runtime-candidate"/name).read_bytes())
            self.assertEqual(active.read_bytes(),(op.STAGE/name).read_bytes())
        text=(op.STAGE/"title_operator_native.py").read_text()
        tree=ast.parse(text); method=next(n for n in ast.walk(tree) if isinstance(n,ast.AsyncFunctionDef) and n.name=="_snapshot")
        body=ast.get_source_segment(text,method)
        self.assertLess(body.index("assembly = await read_adopted_assembly"),body.index("recovery_evidence = await"))
        self.assertLess(body.index("recovery_evidence = await"),body.index("budget = await"))
        self.assertIn('"recovery_evidence": recovery_evidence}',body)
        self.assertIn('"chapter_review_recovery_cycles"',body)
        # New collector never mutates native authority or invokes models.
        method=next(n for n in ast.walk(tree) if isinstance(n,ast.AsyncFunctionDef) and n.name=="_recovery_evidence")
        calls={n.func.attr for n in ast.walk(method) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute)}
        self.assertTrue(calls.isdisjoint({"commit","flush","add","complete_text","dispatch","execute"}))



def with_advice(length):
    snapshot = fixture()
    text = ("SYNTHETIC历史初评建议，保留全文。\n" * (length + 1))[:length]
    snapshot["recovery_evidence"]["reports"][1]["structured_output"]["rewrite_instructions"] = text
    snapshot["review_fence_sha256"] = op.fingerprint(snapshot["recovery_evidence"])
    return snapshot


class HistoricalAdviceTests(Offline):
    def test_nonempty_native_PASS_advice_and_original_fence_preserved(self):
        for length in (7442, 13672, 8509):
            with self.subTest(synthetic_length=length):
                s = with_advice(length); before = deepcopy(s)
                self.assertEqual(op.open_review_reasons(s), [])
                op.check_snapshot(s, s["manifest"])
                self.assertEqual(s, before)
                self.assertEqual(len(s["recovery_evidence"]["reports"][1]["structured_output"]["rewrite_instructions"]), length)

    def test_empty_or_null_advice_still_not_authority(self):
        for value in (None, "", "SYNTHETIC保留建议"):
            s = with_advice(10)
            s["recovery_evidence"]["reports"][1]["structured_output"]["rewrite_instructions"] = value
            s["recovery_evidence"]["reports"][1]["verdict"] = "rewrite"
            self.assertIn("unclosed_critic_cycle", op.open_review_reasons(s))

    def test_native_cycle_verdict_rewrite_task_and_weak_evidence_still_block(self):
        paths = [
            ("recovery_evidence", "workflows", 1, "metadata", "final_verdict"),
            ("recovery_evidence", "reports", 1, "verdict"),
            ("recovery_evidence", "reports", 1, "structured_output", "critic_evidence", "status"),
            ("recovery_evidence", "reports", 1, "structured_output", "critic_evidence", "passed"),
            ("recovery_evidence", "knowledge", 0, "status", "closed"),
            ("recovery_evidence", "workflows", 1, "metadata", "requires_human_review"),
        ]
        for path in paths:
            with self.subTest(path=path):
                s = with_advice(8509); target = s
                for key in path[:-1]: target = target[key]
                target[path[-1]] = not target[path[-1]] if isinstance(target[path[-1]], bool) else "rewrite"
                self.assertIn("unclosed_critic_cycle", op.open_review_reasons(s))
        for key, value in (("rewrite_task_id", "synthetic-rewrite-task"), ("review_result", {"verdict": "rewrite"})):
            s = with_advice(8509); m = s["chapters"][-1]["metadata"]
            m["chapter_critic_cycles"][m["chapter_review_active_cycle"]["cycle_id"]]["attempts"][-1][key] = value
            self.assertIn("unclosed_critic_cycle", op.open_review_reasons(s))

    def test_pending_native_task_blocks_despite_PASS_and_retained_advice(self):
        s = with_advice(8509); source = s["manifest"]["sources"][-1]
        task = {"id": "synthetic-pending-task", "status": "pending", "source_binding": {"draft_id": source["draft_id"]}}
        s["open_rewrite_tasks"] = op.active_rewrites([task], s["drafts"], s["manifest"])
        self.assertIn("unclosed_rewrite:synthetic-pending-task", op.open_review_reasons(s))
        with self.assertRaisesRegex(op.Stop, "new_review_not_closed"):
            op.check_snapshot(s, s["manifest"])

    def test_revision_scope_is_one_removed_predicate_and_collector_unchanged(self):
        old = (REV.parent / "runtime-candidate/title_operator.py").read_text()
        new = (REV / "runtime-candidate/title_operator.py").read_text()
        self.assertEqual(new, old.replace('and payload["critic_evidence"]["passed"] is True and payload.get("rewrite_instructions") is None)',
                                         'and payload["critic_evidence"]["passed"] is True)'))
        self.assertEqual((REV / "runtime-candidate/title_operator_native.py").read_bytes(),
                         (REV.parent / "runtime-candidate/title_operator_native.py").read_bytes())
        for name in ("title_operator.py", "title_operator_native.py"):
            self.assertEqual((REV / "runtime-candidate" / name).read_bytes(),
                             (REV / "active-operator-candidate" / name).read_bytes())



if __name__ == "__main__":
    unittest.main(verbosity=2)
