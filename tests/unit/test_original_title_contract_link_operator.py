"""Offline guards for the original four-title reference-link operator.

Execute the actual operator's AST functions with explicitly synthetic sessions
and helper/consumer seams. No operator imports, engine, DB, physical paid proof,
provider or PostgreSQL concurrency are exercised or claimed by these tests.
"""
from __future__ import annotations

import ast
import asyncio
from contextlib import redirect_stdout
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace as Row
import unittest
from uuid import UUID, NAMESPACE_URL, uuid5

from sqlalchemy import func, select, text
from bestseller.infra.db import models as m

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "reports/framework-recovery-2026-10-21/link_original_four_title_contract_references.py"
SOURCE = SCRIPT.read_text()
BODY_SHA = "7b82244210cceabf5968beaa012aa2c4b220c309a30aed0a68a8973b50c08198"
TITLE = "我修好旧收音机时，已经交还了铺面钥匙"


def safe(value):
    return json.loads(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str))


def digest(value):
    return hashlib.sha256(json.dumps(safe(value), ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def operator_namespace():
    """Never execute module imports or its CLI; compile only real functions/pins."""
    tree = ast.parse(SOURCE, filename=str(SCRIPT))
    pins = {"PID", "TITLE_ARTIFACT", "TITLE_WORKFLOW", "SCOPE", "TYPE", "WID", "AID"}
    nodes = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
             or (isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id in pins
                                                      for t in node.targets))]
    ns = {"__name__": "isolated_title_reference_AST", "UUID": UUID, "NAMESPACE_URL": NAMESPACE_URL,
          "uuid5": uuid5, "asyncio": asyncio, "json": json, "m": m,
          "select": select, "func": func, "text": text}
    exec(compile(ast.Module(nodes, type_ignores=[]), str(SCRIPT), "exec"), ns)
    return ns


class Fixture:
    """Synthetic integration-control-flow fixture; no connection capability."""
    def __init__(self):
        self.ns = operator_namespace()
        self.events = []; self.added = []; self.commits = 0; self.rollbacks = 0
        self.flushed = False; self.disposed = False; self.fail = None
        self.calls_used = 290; self.database = "bestseller"
        self.manifest = {"assembly_sha256": BODY_SHA, "fixture": "SYNTHETIC manifest"}
        self.project = Row(id=self.ns["PID"], title=TITLE, target_chapters=4,
                           metadata_json={"execution_scope_id": self.ns["SCOPE"]})
        self.intent = "run"; self.command_serial = 28
        self.leaf = {"id": self.ns["TITLE_ARTIFACT"], "project_id": self.ns["PID"],
                     "source_run_id": self.ns["TITLE_WORKFLOW"], "status": "failed",
                     "content": {"fixture": "SYNTHETIC untouched failed response"}}
        self.old_workflow = {"id": self.ns["TITLE_WORKFLOW"], "project_id": self.ns["PID"],
                             "status": "machine_blocked", "metadata_json": {"failure": "retained"}}
        self.prepared = {"workflow_id": self.ns["WID"], "artifact_id": self.ns["AID"],
                         "content": {"fixture": "SYNTHETIC nonpaid reference projection"},
                         "workflow_metadata": {"fixture": "SYNTHETIC linkage metadata"},
                         "native_proof": {"fixture": "NOT an actual paid certificate"}}
        self.originals = deepcopy((self.leaf, self.old_workflow))
        self.settings = Row(database=Row(url="NO-CONNECTION-CAPABILITY"))
        self.ns.update(native=Row(json_safe=safe, _one=self.one), consumer=Row(digest=digest),
            load_control_state=self.control, read_adopted_assembly=self.assembly,
            prepare_current_title_contract_linkage=self.helper,
            project_settings=lambda settings, _: settings, _ledger=self.ledger,
            review_whole_fanqie_short_story=lambda *_a, **_kw: Row(fixture="SYNTHETIC raw report"),
            load_upload_semantic_context=self.common_loader, semantic_readiness_view=self.view,
            load_settings=lambda: self.settings, apply_runtime_llm_profile=lambda value: value,
            create_async_engine=lambda _: self.engine(),
            async_sessionmaker=lambda *_a, **_kw: lambda: Session(self),
            safe_error_chain=lambda error: [{"type": type(error).__name__, "fixture_reason": str(error)}])

    def engine(self):
        async def dispose(): self.disposed = True
        return Row(dispose=dispose)

    async def control(self, session, pid, *, for_update=False):
        self.events.append(("control", for_update))
        return Row(intent=self.intent, command_serial=self.command_serial,
                   to_payload=lambda: {"intent": self.intent, "command_serial": self.command_serial})

    async def assembly(self, session, pid, *, lock=False):
        self.events.append(("assembly", lock))
        manifest = deepcopy(self.manifest)
        if self.fail == "precommit_source" and self.flushed and not self.commits:
            manifest["fixture"] = "SYNTHETIC drift before commit"
        if self.fail == "postcommit_source" and self.commits:
            manifest["fixture"] = "SYNTHETIC drift after commit"
        return Row(manifest=manifest, full_text="SYNTHETIC adopted body")

    async def helper(self, session, settings, *, project, assembly):
        self.events.append(("helper", False))
        if self.fail == "helper": raise ValueError("SYNTHETIC native helper rejection")
        result = deepcopy(self.prepared)
        if self.fail == "locked_prepared_drift" and ("control", True) in self.events:
            result["content"]["fixture"] = "SYNTHETIC drift"
        return result

    async def one(self, session, model, *_conditions):
        self.events.append(("original_leaf_read", False))
        row = deepcopy(self.leaf if model is self.ns["m"].PlanningArtifactVersionModel else self.old_workflow)
        if self.flushed and self.fail == "precommit_leaf" and not self.commits:
            row["fixture_drift"] = True
        if self.commits and self.fail == "postcommit_leaf": row["fixture_drift"] = True
        return row

    async def ledger(self, session, scope, *, lock=False):
        assert scope == self.ns["SCOPE"]
        self.events.append(("ledger", lock))
        result = {"budget": {"calls_used": self.calls_used, "tokens_charged": 99123},
                  "operations": [{"key": "SYNTHETIC paid root", "status": "completed"}],
                  "attempts": [{"id": "SYNTHETIC paid attempt", "status": "completed"}],
                  "all_llm_runs": ["SYNTHETIC body", "SYNTHETIC title"]}
        phase = "postcommit" if self.commits else "precommit"
        if self.flushed and self.fail == phase + "_ledger":
            # Keep calls_used equal: the operator must hash ALL ledger fields.
            result["attempts"][0]["status"] = "unknown"
        return result

    async def common_loader(self, session, *, settings, project, assembly, original_report):
        phase = "postcommit" if self.commits else "precommit"
        self.events.append(("common_loader", phase))
        assert self.flushed, "Common native reader must run after caller flush"
        if self.fail == phase + "_loader": raise ValueError("SYNTHETIC common-loader rejection")
        if self.fail == phase + "_context_none": return None
        return Row(fixture="SYNTHETIC context seam; not native proof")

    def view(self, context, **kwargs):
        phase = "postcommit" if self.commits else "precommit"
        self.events.append(("view", phase))
        if self.fail == phase + "_view": raise ValueError("SYNTHETIC consumer rejection")
        audit = {"workflow_id": str(self.ns["WID"]), "artifact_id": str(self.ns["AID"])}
        derived = {
            "adjudication_trace": {"native_leaves": {"fanqie_whole_title_semantic_review": {
                "native": {"title_contract_linkage": {"nonpaid_audit": audit}}}}},
            "human_attraction_acceptance": "not_performed", "model_grade_asserted": False,
            "open_debt": [{"status": "open", "score": 52, "fixture": "SYNTHETIC retained attraction debt"}],
        }
        for key in ("workflow_id", "artifact_id"):
            if self.fail == phase + "_foreign_" + key:
                audit[key] = "11111111-1111-4111-8111-111111111111"
        if self.fail == phase + "_clear_debt": derived["open_debt"] = []
        if self.fail == phase + "_fake_human": derived["human_attraction_acceptance"] = "accepted"
        if self.fail == phase + "_fake_grade": derived["model_grade_asserted"] = True
        # Other real gates may remain blocked. These tests do not invent PASS.
        return {"fixture": "SYNTHETIC consumer seam", "effective_passed": False,
                "semantic_derived_review": derived}

    def prepare(self, *, lock=False):
        return asyncio.run(self.ns["prepare"](Session(self), self.settings, lock=lock))

    def main(self, *, execute=False, witness=None):
        with redirect_stdout(io.StringIO()) as output:
            result = asyncio.run(self.ns["main"](Row(execute=execute, witness=witness)))
        return result, json.loads(output.getvalue())

    def execute(self, failure=None):
        ok, preflight = self.main()
        assert ok
        self.fail = failure
        return self.main(execute=True, witness=preflight["witness"])


class Session:
    def __init__(self, fixture): self.f = fixture
    async def __aenter__(self): return self
    async def __aexit__(self, kind, *_rest):
        if kind is not None: self.f.rollbacks += 1
    async def execute(self, statement):
        assert str(statement) == "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
        self.f.events.append(("readonly", True))
    async def scalar(self, statement):
        if str(statement) == "SELECT current_database()": return self.f.database
        assert "max(planning_artifact_versions.version_no)" in str(statement)
        return 0
    async def get(self, model, key, **kwargs):
        assert model is m.ProjectModel and key == self.f.ns["PID"]
        self.f.events.append(("project", kwargs.get("with_for_update", False)))
        return self.f.project
    def add(self, row):
        assert type(row) in {m.WorkflowRunModel, m.PlanningArtifactVersionModel}
        self.f.events.append(("add", type(row).__name__))
        self.f.added.append(row)
    async def flush(self):
        self.f.events.append(("flush", True))
        if self.f.fail == "flush": raise ValueError("SYNTHETIC flush failure")
        self.f.flushed = True
    async def commit(self):
        self.f.events.append(("commit", True))
        if self.f.fail == "commit": raise ValueError("SYNTHETIC commit failure")
        self.f.commits += 1
        if self.f.fail == "commit_ack_lost":
            raise ValueError("SYNTHETIC commit persisted but acknowledgement lost")


class TitleContractLinkOperatorTests(unittest.TestCase):
    def test_default_cli_is_readonly_and_failure_exit_is_nonzero(self):
        tree = ast.parse(SOURCE)
        flags = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Attribute) and n.func.attr == "add_argument"]
        execute = next(n for n in flags if n.args and isinstance(n.args[0], ast.Constant)
                       and n.args[0].value == "--execute")
        self.assertEqual({k.arg: ast.literal_eval(k.value) for k in execute.keywords}, {"action": "store_true"})
        cli = next(n for n in tree.body if isinstance(n, ast.If))
        self.assertIn("SystemExit(0 if asyncio.run(main(parser.parse_args())) else 1)", ast.unparse(cli))
        f = Fixture(); ok, result = f.main()
        self.assertTrue(ok); self.assertIn("readonly_preflight_passed", result["status"])
        self.assertFalse(f.added); self.assertEqual(f.commits, 0)
        self.assertNotIn(("control", True), f.events)
        self.assertIn(("readonly", True), f.events); self.assertTrue(f.disposed)

    def test_no_provider_replay_new_paid_uuid_or_physical_rewrite_capabilities(self):
        names = {n.func.attr if isinstance(n.func, ast.Attribute) else n.func.id
                 for n in ast.walk(ast.parse(SOURCE)) if isinstance(n, ast.Call)
                 and isinstance(n.func, (ast.Name, ast.Attribute))}
        self.assertTrue(names.isdisjoint({"complete_text", "_restore_execution_telemetry", "replay",
            "_bind_execution_telemetry", "_persist_prompt_payloads", "write_text", "write_bytes", "uuid4",
            "LlmRunModel", "ExecutionOperationModel", "ExecutionAttemptModel", "ExecutionBudgetModel"}))

    def test_stable_ids_bind_exact_original_failed_title_artifact(self):
        ns = operator_namespace()
        self.assertEqual(str(ns["TITLE_ARTIFACT"]), "0fe39140-9993-5e50-aca4-f8eb1d387690")
        self.assertEqual(str(ns["TITLE_WORKFLOW"]), "97b95283-1d06-5496-b61c-3f1a873df5f2")
        self.assertEqual(ns["WID"], uuid5(NAMESPACE_URL, "title-contract-linkage:" + str(ns["TITLE_ARTIFACT"])))
        self.assertEqual(ns["AID"], uuid5(NAMESPACE_URL, "title-contract-linkage-artifact:" + str(ns["TITLE_ARTIFACT"])))
        for field in ("workflow_id", "artifact_id"):
            f = Fixture(); f.prepared[field] = UUID("11111111-1111-4111-8111-111111111111")
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "stable_link_identity"): f.prepare()

    def test_native_prepare_control_project_source_then_ledger_lock_order(self):
        f = Fixture(); f.prepare(lock=True)
        self.assertEqual([name for name, value in f.events if value is True],
                         ["control", "project", "assembly", "ledger"])
        self.assertLess(f.events.index(("assembly", True)), f.events.index(("helper", False)))
        self.assertLess(f.events.index(("helper", False)), f.events.index(("ledger", True)))
        self.assertFalse(f.added); self.assertEqual(f.commits, 0)

    def test_exact_database_control_source_scope_body_and_call_count_guards(self):
        for case, code in (("database", "database"), ("intent", "current_control"),
                           ("serial", "current_control"), ("title", "source_owner"),
                           ("count", "source_owner"), ("scope", "source_owner"),
                           ("body", "body"), ("calls", "original_two_once_paid_leaves_only")):
            f = Fixture()
            if case == "database": f.database = "another_database"
            elif case == "intent": f.intent = "stop"
            elif case == "serial": f.command_serial += 1
            elif case == "title": f.project.title += "改"
            elif case == "count": f.project.target_chapters = 5
            elif case == "scope": f.project.metadata_json["execution_scope_id"] = "another-scope"
            elif case == "body": f.manifest["assembly_sha256"] = "different"
            else: f.calls_used += 1
            with self.subTest(case=case), self.assertRaisesRegex(ValueError, code): f.prepare()
            self.assertFalse(f.added)

    def test_original_failed_leaf_ownership_source_and_status_guards(self):
        for case in ("project_id", "source_run_id", "artifact_status", "workflow_status"):
            f = Fixture()
            if case in {"project_id", "source_run_id"}: f.leaf[case] = UUID(int=1)
            elif case == "artifact_status": f.leaf["status"] = "validated"
            else: f.old_workflow["status"] = "completed"
            with self.subTest(case=case), self.assertRaisesRegex(ValueError, "original_failed_leaf_required"): f.prepare()

    def test_mapped_width_is_checked_before_any_add_flush_or_commit(self):
        f = Fixture()
        cols = dict(m.WorkflowRunModel.__table__.c.items())
        cols["workflow_type"] = Row(type=Row(length=1))
        proxy = Row(id=m.WorkflowRunModel.id, __table__=Row(c=cols))
        f.ns["m"] = Row(ProjectModel=m.ProjectModel, WorkflowRunModel=proxy,
                        PlanningArtifactVersionModel=m.PlanningArtifactVersionModel)
        with self.assertRaisesRegex(ValueError, "mapped_width_workflow_type"): f.prepare()
        self.assertFalse(f.added); self.assertFalse(f.flushed); self.assertEqual(f.commits, 0)

    def test_witness_binds_full_ledger_and_original_rows_not_only_calls(self):
        f = Fixture(); *_, baseline = f.prepare()
        self.assertEqual(baseline["ledger_sha256"], digest(asyncio.run(f.ledger(Session(f), f.ns["SCOPE"]))))
        self.assertEqual(baseline["original_leaf_sha256"], digest(safe({"artifact": f.leaf, "workflow": f.old_workflow})))
        f.old_workflow["metadata_json"]["failure"] = "different retained reason"
        *_, current = f.prepare()
        self.assertNotEqual(digest(baseline), digest(current))

    def test_execute_missing_or_wrong_witness_stops_before_writes(self):
        for witness in (None, "wrong"):
            f = Fixture(); ok, result = f.main(execute=True, witness=witness)
            with self.subTest(witness=witness):
                self.assertFalse(ok); self.assertEqual(result["status"], "reference_link_not_committed")
                self.assertIn("exact_witness", result["guard_error"])
                self.assertFalse(f.added); self.assertEqual(f.commits, 0)

    def test_locked_preflight_rechecks_witness_before_inserts(self):
        f = Fixture(); ok, result = f.execute("locked_prepared_drift")
        self.assertFalse(ok); self.assertIn("source_or_financial_drift", result["guard_error"])
        self.assertFalse(f.added); self.assertEqual(f.commits, 0)

    def test_native_helper_failure_cannot_be_bypassed_or_dispatch(self):
        f = Fixture(); f.fail = "helper"; ok, result = f.main()
        self.assertFalse(ok); self.assertEqual(result["status"], "reference_link_readonly_preflight_failed")
        self.assertFalse(f.added); self.assertEqual(f.commits, 0)

    def test_precommit_loader_consumer_and_flush_failures_rollback_without_success(self):
        for failure in ("flush", "precommit_loader", "precommit_context_none", "precommit_view", "commit"):
            f = Fixture(); ok, result = f.execute(failure)
            with self.subTest(failure=failure):
                expected = ("reference_link_commit_outcome_requires_readback" if failure == "commit"
                            else "reference_link_not_committed")
                self.assertFalse(ok); self.assertEqual(result["status"], expected)
                self.assertEqual(f.commits, 0); self.assertEqual(f.rollbacks, 1)
                self.assertEqual((f.leaf, f.old_workflow), f.originals)

    def test_commit_exception_is_unknown_even_if_commit_persisted_before_ack_loss(self):
        for failure, committed in (("commit", 0), ("commit_ack_lost", 1)):
            f = Fixture(); ok, result = f.execute(failure)
            with self.subTest(failure=failure):
                self.assertFalse(ok)
                self.assertEqual(result["status"], "reference_link_commit_outcome_requires_readback")
                self.assertEqual(f.commits, committed)
                self.assertIn(("view", "precommit"), f.events)
                self.assertNotIn(("common_loader", "postcommit"), f.events)
                self.assertEqual((f.leaf, f.old_workflow), f.originals)

    def test_foreign_link_audit_workflow_or_artifact_is_rejected_pre_and_postcommit(self):
        for phase in ("precommit", "postcommit"):
            for key in ("workflow_id", "artifact_id"):
                f = Fixture(); ok, result = f.execute(phase + "_foreign_" + key)
                with self.subTest(phase=phase, key=key):
                    self.assertFalse(ok)
                    self.assertEqual(result["status"], "reference_link_not_committed" if phase == "precommit"
                                     else "reference_link_committed_readback_failed")
                    self.assertIn("common_loader_wrong_link_identity", result["guard_error"])
                    self.assertEqual(f.commits, 0 if phase == "precommit" else 1)

    def test_cleared_debt_fake_human_or_model_grade_is_rejected_pre_and_postcommit(self):
        for phase in ("precommit", "postcommit"):
            for corruption in ("clear_debt", "fake_human", "fake_grade"):
                f = Fixture(); ok, result = f.execute(phase + "_" + corruption)
                with self.subTest(phase=phase, corruption=corruption):
                    self.assertFalse(ok)
                    self.assertEqual(result["status"], "reference_link_not_committed" if phase == "precommit"
                                     else "reference_link_committed_readback_failed")
                    self.assertIn("acceptance_or_debt_changed", result["guard_error"])
                    self.assertEqual(f.commits, 0 if phase == "precommit" else 1)

    def test_precommit_full_ledger_failed_leaf_and_source_conservation(self):
        for failure, code in (("precommit_leaf", "original_leaf_changed"),
                              ("precommit_ledger", "ledger_changed"), ("precommit_source", "source_changed")):
            f = Fixture(); ok, result = f.execute(failure)
            with self.subTest(failure=failure):
                self.assertFalse(ok); self.assertEqual(result["status"], "reference_link_not_committed")
                self.assertIn(code, result["guard_error"]); self.assertEqual(f.commits, 0)

    def test_postcommit_failures_are_distinguished_and_not_false_success(self):
        for failure in ("postcommit_loader", "postcommit_context_none", "postcommit_view",
                        "postcommit_leaf", "postcommit_ledger"):
            f = Fixture(); ok, result = f.execute(failure)
            with self.subTest(failure=failure):
                self.assertFalse(ok); self.assertEqual(result["status"], "reference_link_committed_readback_failed")
                self.assertEqual(f.commits, 1)

    def test_postcommit_source_manifest_must_still_equal_witness(self):
        f = Fixture(); ok, result = f.execute("postcommit_source")
        self.assertFalse(ok, "Changed postcommit source must not report verified original linkage")
        self.assertEqual(result["status"], "reference_link_committed_readback_failed")
        self.assertEqual(f.commits, 1)

    def test_success_adds_only_new_WF_and_artifact_common_loader_runs_before_commit(self):
        f = Fixture(); ok, result = f.execute()
        self.assertTrue(ok); self.assertEqual(result["status"], "same_response_reference_link_committed_and_native_readback_verified")
        self.assertEqual(f.commits, 1); self.assertEqual(len(f.added), 2)
        workflow, artifact = f.added
        self.assertIs(type(workflow), m.WorkflowRunModel)
        self.assertIs(type(artifact), m.PlanningArtifactVersionModel)
        self.assertEqual((workflow.id, artifact.id, artifact.source_run_id),
                         (f.ns["WID"], f.ns["AID"], f.ns["WID"]))
        self.assertNotEqual(workflow.id, f.ns["TITLE_WORKFLOW"])
        self.assertNotEqual(artifact.id, f.ns["TITLE_ARTIFACT"])
        self.assertEqual(artifact.schema_version, "link.1")
        self.assertEqual(workflow.metadata_json, f.prepared["workflow_metadata"])
        self.assertEqual(artifact.content, f.prepared["content"])
        self.assertEqual((f.leaf, f.old_workflow), f.originals)
        self.assertLess(f.events.index(("flush", True)), f.events.index(("common_loader", "precommit")))
        self.assertLess(f.events.index(("view", "precommit")), f.events.index(("commit", True)))
        self.assertLess(f.events.index(("commit", True)), f.events.index(("common_loader", "postcommit")))
        self.assertEqual(result["provider_calls"], 0)
        self.assertIs(result["new_grade_asserted"], False)
        self.assertIs(result["export_authority"], False)
        self.assertIs(result["precommit_common_readiness"]["effective_passed"], False)
        for phase in ("precommit", "postcommit"):
            derived = result[phase + "_common_readiness"]["semantic_derived_review"]
            audit = derived["adjudication_trace"]["native_leaves"]["fanqie_whole_title_semantic_review"][
                "native"]["title_contract_linkage"]["nonpaid_audit"]
            self.assertEqual(audit, {"workflow_id": str(f.ns["WID"]), "artifact_id": str(f.ns["AID"])})
            self.assertEqual(derived["human_attraction_acceptance"], "not_performed")
            self.assertIs(derived["model_grade_asserted"], False)
            self.assertEqual(derived["open_debt"][0]["score"], 52)


if __name__ == "__main__":
    unittest.main(verbosity=2)
