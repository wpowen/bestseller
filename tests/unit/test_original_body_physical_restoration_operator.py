"""Offline regression for the one-original-UUID restoration operator.

Only actual operator AST bodies run. All sessions, paid data and physical bytes
below are explicitly synthetic isolated fixtures, not DB/paid certificates.
No operator imports/CLI, real engine, HTTP, provider or production write runs.
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

from sqlalchemy import func, or_, select, text
from bestseller.infra.db import models as m

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "reports/framework-recovery-2026-10-21/restore_original_body_physical_receipt.py"
SOURCE = SCRIPT.read_text()
PAID_RID = "97f2c008-2f18-410d-bebd-3bce54a4b05a"
PHYSICAL_HASHES = {
    "request": "d74e3d77c611701a0b593e1715e8dbe9e7cb382c8b8ea1a8bc028176151d90fa",
    "response": "65c71dc8e2e1c1d2e540827c84335d75e0b813f1523855558b11b6d1af7eec8e",
}


def safe(value):
    return json.loads(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str))


def digest(value):
    return hashlib.sha256(json.dumps(safe(value), ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def operator_namespace():
    tree = ast.parse(SOURCE, filename=str(SCRIPT))
    names = {"PID", "WID", "RID", "PARENT", "SCOPE", "AID", "EXPECTED_PAIR"}
    nodes = [n for n in tree.body if (
        isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in {"need", "safe", "prepare", "main"}
    ) or (isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id in names for t in n.targets))]
    namespace = {"__name__": "isolated_restoration_AST", "UUID": UUID, "NAMESPACE_URL": NAMESPACE_URL,
        "uuid5": uuid5, "asyncio": asyncio, "deepcopy": deepcopy, "hashlib": hashlib, "json": json,
        "m": m, "select": select, "func": func, "or_": or_, "text": text}
    exec(compile(ast.Module(nodes, type_ignores=[]), str(SCRIPT), "exec"), namespace)
    return namespace


class Fixture:
    """Synthetic dependencies; capability surface cannot dispatch/connect."""
    def __init__(self):
        self.ns = operator_namespace()
        self.events=[];self.added=[];self.commits=0;self.rollbacks=0;self.disposed=False
        self.fail=None;self.verifications=0;self.attempt_count=1;self.existing_run=False;self.existing_artifact=False
        self.manifest={"assembly_sha256":"7b82244210cceabf5968beaa012aa2c4b220c309a30aed0a68a8973b50c08198"}
        self.plan={"binding_sha256":"8b95757805227aecc5a76e2937d1d908e8a62acff44ab70c6159ec74f9b2df4f"}
        self.fields={"operation_id":"SYNTHETIC-original-operation", "prompt_template":"fanqie_short_semantic_evidence_selection",
            "prompt_version":"selection.1", "system_prompt":"SYNTHETIC system", "user_prompt":"SYNTHETIC literal user",
            "tools":[{"type":"function","function":{"name":"SYNTHETIC"}}],
            "metadata":{"selection_binding_sha256":self.plan["binding_sha256"],"evidence_selection_only":True}}
        self.key=hashlib.sha256((self.ns["SCOPE"]+"\n"+self.fields["operation_id"]+":sample:0").encode()).hexdigest()
        self.receipts={"llm_run_flushed":False,"paid_call_origin":{"workflow_run_id":str(self.ns["WID"]),"logical_role":"critic"},
            "effective_input_receipt":{"hash":"e"*64},"execution_receipt":{"operation_key":self.key},
            "runtime_provenance":{"fixture":"synthetic"}}
        self.paid={"content":"", "tool_calls":[{"id":"synthetic-paid-tool"}],"raw_message":{"synthetic":True},
            "provider":"synthetic-provider-not-dispatched", "model_name":"synthetic-model-not-dispatched",
            "input_tokens":64091,"output_tokens":8323,"latency_ms":75310,"finish_reason":"tool_calls",
            "fallback_used":False,"fallback_source":None,"llm_run_id":None,"metadata":deepcopy(self.receipts)}
        self.root=Row(status="completed",scope_id=self.ns["SCOPE"],input_hash="e"*64,result_json=deepcopy(self.paid))
        self.attempt=Row(id="synthetic-original-attempt",status="completed",actual_tokens=72414)
        self.transports=[Row(key=hashlib.sha256(("transport-result.v1\n"+self.ns["SCOPE"]+"\n"+self.attempt.id).encode()).hexdigest(),
            status="transport_result",scope_id=self.ns["SCOPE"],input_hash=self.root.input_hash,
            result_json={"schema":"transport-result.v1","operation_key":self.key,"attempt_id":self.attempt.id,
                         "input_hash":self.root.input_hash,"result":deepcopy(self.paid)})]
        self.wf=Row(id=self.ns["WID"],project_id=self.ns["PID"],status="machine_blocked",
            current_step="typed_result_or_retained_recovery_required",metadata_json={
                "no_resend":True,"source_manifest":self.manifest,"plan_binding_sha256":self.plan["binding_sha256"],
                "native_save_failure":[{"type":"StringDataRightTruncationError","sqlstate":"22001"}],
                "semantic_selection_parent":{"artifact_id":str(self.ns["PARENT"])}})
        self.budget=Row(calls_used=289,tokens_charged=72414)
        common={"llm_run_id":str(self.ns["RID"]),"project_id":str(self.ns["PID"]),
                "workflow_run_id":str(self.ns["WID"]),"replayable":True,"replayability_reasons":[]}
        self.docs={"request":{**common,"kind":"request","prompt_template":self.fields["prompt_template"],
            "prompt_version":"selection.1","messages":{r:{"text":self.fields[r+"_prompt"],"truncated":False}
                for r in ("system","user")},"requested":{"tools":self.fields["tools"],"tool_choice":"required"}},
            "response":{**common,"kind":"response",**{k:self.paid[k] for k in
                ("tool_calls","raw_message","input_tokens","output_tokens")},"content":{"text":""}}}
        self.files={k:json.dumps(v,ensure_ascii=False).encode() for k,v in self.docs.items()}
        # Isolated synthetic-byte hashes only. A separate test pins the actual
        # operator's unchanged production EXPECTED_PAIR and original RID.
        self.ns["EXPECTED_PAIR"]={k:hashlib.sha256(v).hexdigest() for k,v in self.files.items()}
        self.settings=Row(database=Row(url="NO-CONNECTION-CAPABILITY"),artifact_store=Row(local_dir="/SYNTHETIC"))
        self.ns.update(native=Row(json_safe=safe,_read_file=self.read_file,_one=self.one,_load_artifact_native=self.load_native),
            consumer=Row(sha=lambda value:hashlib.sha256(value).hexdigest(),digest=digest,strict_json=json.loads),
            body=Row(SCHEMA="fanqie-short-semantic-evidence-selection.v1",prepare_selection=lambda _:self.plan,
                     native_request_fields=lambda *_a,**_kw:deepcopy(self.fields),validate_selection=lambda *_a: {"fixture":"structural-only"}),
            LEAF_SCHEMA_VERSION="selection.1",project_settings=lambda settings,_:settings,
            load_control_state=self.control,read_adopted_assembly=self.assembly,load_selection_parent=self.parent,
            _ledger=self.ledger,llm=Row(_bind_execution_telemetry=self.bind),verify_selection_tool_native=self.verify,
            load_settings=lambda:self.settings,apply_runtime_llm_profile=lambda value:value,
            create_async_engine=lambda _:self.engine(),async_sessionmaker=lambda *_a,**_kw:lambda:Session(self),
            safe_error_chain=lambda error:[{"type":type(error).__name__,"fixture_reason":str(error)}])

    def engine(self):
        async def dispose():self.disposed=True
        return Row(dispose=dispose)

    async def control(self,session,pid,*,for_update=False):
        self.events.append(("control",for_update));return Row(intent="run",command_serial=28)

    async def assembly(self,session,pid,*,lock=False):
        self.events.append(("assembly",lock));return Row(manifest=deepcopy(self.manifest))

    async def parent(self,*_a,**_kw):return {"parent_report":{}}

    async def ledger(self,*_a,lock=False,**_kw):
        self.events.append(("budget",lock))
        return {"budget":{"calls_used":289,"tokens_charged":72414},
                "operations":{self.key:digest(self.root.result_json)},"attempts":{self.attempt.id:"unchanged"}}

    def read_file(self,root,ref,run,kind):
        assert run==PAID_RID and ref==f"runtime/llm_payloads/{PAID_RID}.{kind}.json"
        return self.files[kind]

    async def bind(self,session,candidate,key):
        self.events.append(("root_bind",key))
        assert key==self.key
        if self.fail=="wrong_bound_uuid":return Row(id=UUID("11111111-1111-4111-8111-111111111111"),metadata_json=candidate.metadata_json)
        session.add(candidate);return candidate

    async def one(self,session,model,*_conditions):
        if model is m.WorkflowRunModel:return {"id":self.wf.id,"status":self.wf.status}
        return {"id":self.ns["AID"]}

    async def load_native(self,*_args):return {"fixture":"native-loader-control-flow-only"},{}

    def verify(self,*_args,**_kwargs):
        self.verifications+=1;self.events.append(("verify",self.verifications))
        if self.fail==("precommit_verify" if self.verifications==1 else "postcommit_verify"):
            raise ValueError(self.fail)
        return {"fixture":"not-a-native-paid-proof"}

    def prepare(self,*,lock=False):return asyncio.run(self.ns["prepare"](Session(self),self.settings,lock=lock))

    def main(self,*,execute,witness=None):
        with redirect_stdout(io.StringIO()) as output:
            result=asyncio.run(self.ns["main"](Row(execute=execute,witness=witness)))
        return result,json.loads(output.getvalue())


class Session:
    def __init__(self,fixture):self.f=fixture
    async def __aenter__(self):return self
    async def __aexit__(self,kind,*_rest):
        if kind is not None:self.f.rollbacks+=1
    async def execute(self,statement):
        assert str(statement)=="SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
        self.f.events.append(("readonly",True))
    async def get(self,model,key,**kwargs):
        if model is m.ProjectModel:
            self.f.events.append(("project",kwargs.get("with_for_update",False)));return Row()
        if model is m.ExecutionOperationModel:return self.f.root
        if model is m.ExecutionBudgetModel:return self.f.budget
        if model is m.WorkflowRunModel:return self.f.wf
        raise AssertionError("No unexpected DB model")
    async def scalar(self,statement):
        if str(statement)=="SELECT current_database()":return "bestseller"
        entity=statement.column_descriptions[0]["entity"]
        if entity is m.WorkflowRunModel:
            self.f.events.append(("workflow",statement._for_update_arg is not None));return self.f.wf
        return 0
    async def scalars(self,statement):
        entity=statement.column_descriptions[0]["entity"]
        if entity is m.ExecutionAttemptModel:return [deepcopy(self.f.attempt) for _ in range(self.f.attempt_count)]
        if entity is m.ExecutionOperationModel:return self.f.transports
        if entity is m.LlmRunModel:return [PAID_RID] if self.f.existing_run else []
        if entity is m.PlanningArtifactVersionModel:return ["synthetic-existing-artifact"] if self.f.existing_artifact else []
        raise AssertionError("No unexpected DB model")
    def add(self,row):
        assert type(row) in {m.LlmRunModel,m.PlanningArtifactVersionModel}
        self.f.added.append(row)
    async def flush(self):self.f.events.append(("flush",True))
    async def commit(self):
        self.f.events.append(("commit",True))
        if self.f.fail=="commit":raise ValueError("synthetic commit failure")
        self.f.commits+=1


class RestorationOperatorTests(unittest.TestCase):
    def test_production_UUID_and_original_physical_hash_pins_unchanged(self):
        ns=operator_namespace()
        self.assertEqual(str(ns["RID"]),PAID_RID)
        self.assertEqual(ns["EXPECTED_PAIR"],PHYSICAL_HASHES)

    def test_no_model_replay_payload_regeneration_or_paid_root_inserts(self):
        tree=ast.parse(SOURCE)
        names={n.func.attr if isinstance(n.func,ast.Attribute) else n.func.id
               for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,(ast.Attribute,ast.Name))}
        self.assertTrue(names.isdisjoint({"complete_text","_restore_execution_telemetry","_persist_prompt_payloads",
            "write_text","write_bytes","uuid4","ExecutionOperationModel","ExecutionAttemptModel"}))
        self.assertIn("_bind_execution_telemetry",names)

    def test_prepare_locks_control_project_sources_workflow_then_budget(self):
        f=Fixture();f.prepare(lock=True)
        locked=[name for name,value in f.events if value is True]
        self.assertEqual(locked,["control","project","assembly","workflow","budget"])
        self.assertFalse(f.added);self.assertEqual(f.commits,0)

    def test_prepare_preserves_raw_paid_receipts_and_uses_short_artifact_tag(self):
        f=Fixture();before=deepcopy(f.root.result_json)
        _,wf,_,completion,content,identity=f.prepare()
        self.assertEqual(f.root.result_json,before)
        self.assertIsNone(before["llm_run_id"])
        self.assertFalse(before["metadata"]["llm_run_flushed"])
        for key in ("paid_call_origin","effective_input_receipt","execution_receipt","runtime_provenance"):
            self.assertEqual(completion["metadata"][key],before["metadata"][key])
        self.assertEqual(completion["llm_run_id"],PAID_RID)
        self.assertEqual(content["provider_receipt"]["metadata"],completion["metadata"])
        self.assertGreater(len(content["schema"]),32)
        self.assertEqual(f.ns["LEAF_SCHEMA_VERSION"],"selection.1")
        self.assertEqual(identity["restoration"]["original_failure_sha256"],digest(wf.metadata_json["native_save_failure"]))
        self.assertEqual(identity["restoration"]["provider_calls"],0)

    def test_prepare_rejects_unknown_payment_repeat_telemetry_and_missing_failure(self):
        for case,code in (("root","paid_root"),("attempt","one_paid_attempt"),("two_attempts","one_paid_attempt"),
                          ("run","telemetry_already_exists"),("artifact","artifact_already_exists"),("failure","original_storage_failure")):
            f=Fixture()
            if case=="root":f.root.status="unknown"
            elif case=="attempt":f.attempt.status="sent"
            elif case=="two_attempts":f.attempt_count=2
            elif case=="run":f.existing_run=True
            elif case=="artifact":f.existing_artifact=True
            else:f.wf.metadata_json["native_save_failure"]=[{"sqlstate":"different"}]
            with self.subTest(case=case),self.assertRaisesRegex(ValueError,code):f.prepare()
            self.assertFalse(f.added)

    def test_either_changed_physical_file_is_rejected_before_any_binding(self):
        for kind in ("request","response"):
            f=Fixture();f.files[kind]+=b" "
            with self.subTest(kind=kind),self.assertRaisesRegex(ValueError,"original_physical_bytes_"+kind):f.prepare()
            self.assertFalse(f.added)

    def test_transport_and_existing_retention_receipt_cannot_be_replaced(self):
        for case,code in (("missing","one_original_transport"),("envelope","original_transport_envelope"),
                          ("result","original_transport_result"),("receipt","retention_receipt_conflict")):
            f=Fixture()
            if case=="missing":f.transports=[]
            elif case=="envelope":f.transports[0].result_json["attempt_id"]="another-attempt"
            elif case=="result":f.transports[0].result_json["result"]["output_tokens"]+=1
            else:f.root.result_json["metadata"]["prompt_payload_receipt"]={"status":"wrong"}
            with self.subTest(case=case),self.assertRaisesRegex(ValueError,code):f.prepare()
            self.assertFalse(f.added)

    def test_default_mode_readonly_no_telemetry_commit_or_gateway(self):
        f=Fixture();ok,out=f.main(execute=False)
        self.assertTrue(ok);self.assertIn("readonly_preflight_passed",out["status"])
        self.assertFalse(f.added);self.assertEqual(f.commits,0)
        self.assertNotIn("root_bind",[name for name,_ in f.events])
        self.assertTrue(f.disposed)

    def test_execute_bad_witness_does_not_report_readonly_success(self):
        f=Fixture();ok,out=f.main(execute=True,witness="wrong")
        self.assertFalse(ok);self.assertEqual(out["status"],"original_physical_restoration_not_committed")
        self.assertFalse(f.added);self.assertEqual(f.commits,0)

    def test_precommit_failure_wrong_bound_UUID_and_commit_failure_are_not_success(self):
        for failure in ("precommit_verify","wrong_bound_uuid","commit"):
            f=Fixture();_,out=f.main(execute=False);f.fail=failure
            with self.subTest(failure=failure):
                ok,result=f.main(execute=True,witness=out["witness"])
                self.assertFalse(ok);self.assertEqual(result["status"],"original_physical_restoration_not_committed")
                self.assertEqual(f.commits,0)

    def test_postcommit_failure_is_distinguished_and_not_false_success(self):
        f=Fixture();_,out=f.main(execute=False);f.fail="postcommit_verify"
        ok,result=f.main(execute=True,witness=out["witness"])
        self.assertFalse(ok);self.assertEqual(result["status"],"original_physical_restoration_committed_readback_failed")
        self.assertEqual(f.commits,1)

    def test_success_uses_original_RID_checks_before_commit_and_never_changes_paid_root(self):
        f=Fixture();before=deepcopy(f.root.result_json);_,out=f.main(execute=False)
        ok,result=f.main(execute=True,witness=out["witness"])
        self.assertTrue(ok);self.assertEqual(result["status"],"original_physical_receipt_restored_and_native_readback_verified")
        self.assertEqual(f.commits,1);self.assertEqual(f.verifications,2)
        runs=[row for row in f.added if type(row) is m.LlmRunModel]
        self.assertEqual(len(runs),1);self.assertEqual(str(runs[0].id),PAID_RID)
        self.assertEqual(runs[0].prompt_hash,f.receipts["effective_input_receipt"]["hash"])
        artifacts=[row for row in f.added if type(row) is m.PlanningArtifactVersionModel]
        self.assertEqual(len(artifacts),1);self.assertEqual(artifacts[0].schema_version,"selection.1")
        self.assertEqual(f.root.result_json,before)
        self.assertLess(f.events.index(("budget",True)),next(i for i,v in enumerate(f.events) if v[0]=="root_bind"))
        self.assertLess(f.events.index(("verify",1)),f.events.index(("commit",True)))
        self.assertLess(f.events.index(("commit",True)),f.events.index(("verify",2)))
        self.assertEqual(f.wf.metadata_json["native_save_failure"],[{"type":"StringDataRightTruncationError","sqlstate":"22001"}])


if __name__=="__main__":unittest.main(verbosity=2)
