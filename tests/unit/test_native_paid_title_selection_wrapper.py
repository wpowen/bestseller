"""Strict two-node proof replay; all new selection rows are SYNTHETIC.

Historical paid evidence is retained input, not a fresh production certificate.
The async reader uses only a fake same-transaction row store and physical-byte
reader. No database, engine, commit, network or provider is invoked.
"""
from __future__ import annotations

import asyncio
import base64
from contextlib import contextmanager
from copy import deepcopy
import json
from pathlib import Path
import socket
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import UUID, uuid5

from bestseller.infra.db import models
from bestseller.services import native_paid_title_selection as selection
from bestseller.services import native_publication_title as native
from bestseller.services import fanqie_short_title_evidence as reader
from test_native_paid_title_selection import fixture, SELECTED


def selection_fixture():
    """Return (wrapper,current_context); new WF/step are explicitly SYNTHETIC."""
    origin, args = fixture()
    before = deepcopy(origin["project"]["metadata_json"])
    before["execution_scope_id"] = selection.op.SCOPE
    args["current_metadata_sha256"] = selection.op.fingerprint(before)
    plan = selection.prepare_paid_title_selection(origin, **args)
    metadata, lineage, record = selection.selection_metadata(before, plan=plan, origin_project=origin["project"])
    wf = plan["workflow_run_id"]
    workflow = {"id": wf, "project_id": selection.op.PROJECT_ID, "workflow_type": selection.WORKFLOW_TYPE,
        "status": "completed", "scope_type": "project", "scope_id": selection.op.PROJECT_ID,
        "requested_by": "assistant", "current_step": "paid_title_candidate_adopted", "error_message": None,
        "metadata_json": {"schema": selection.SCHEMA, "selection_plan": plan,
            "execution_scope_id": selection.op.SCOPE, "original_task_id": selection.op.TASK_ID,
            "origin_project_projection": deepcopy(origin["project"]), "selection_metadata_before": before,
            selection.LINEAGE_KEY: deepcopy(lineage), "editorial_quality_debt": deepcopy(plan["editorial_quality_debt"])}}
    step = {"id": str(uuid5(UUID(wf), "paid_title_candidate_selection")), "workflow_run_id": wf,
        "step_name": "adopt_existing_paid_title_candidate", "step_order": 1, "status": "completed",
        "error_message": None, "input_ref": {"plan_sha256": plan["plan_sha256"], "source_manifest": deepcopy(args["context"]["manifest"])},
        "output_ref": {selection.LINEAGE_KEY: deepcopy(lineage), "title_record": deepcopy(record)}}
    wrapper = {"schema": native.SELECTION_SCHEMA, "project": {"id": selection.op.PROJECT_ID,
        "title": SELECTED, "metadata_json": metadata}, "selection_workflow": workflow,
        "selection_step": step, "origin": deepcopy(origin)}
    context = deepcopy(args["context"])
    context["title"] = context["manifest"]["project"]["title"] = SELECTED
    return wrapper, context


def row(data, ids=()):
    values=deepcopy(data)
    for key in ids: values[key]=UUID(values[key])
    return SimpleNamespace(**values)


class TransactionRows:
    """Flushed clean rows, intentionally uncommitted and no engine available."""
    def __init__(self, evidence):
        self.new=[];self.dirty=[];self.deleted=[];self.depth=0;self.reads=[]
        self.project=row(evidence["project"],("id",))
        origin=evidence["origin"]
        self.rows={(models.ProjectModel,str(self.project.id)):self.project}
        for section in (evidence["selection_workflow"],origin["workflow"]):
            value=row(section,("id","project_id","scope_id"))
            self.rows[models.WorkflowRunModel,str(value.id)]=value
        value=row(origin["llm_run"],("id","project_id","workflow_run_id"))
        self.rows[models.LlmRunModel,str(value.id)]=value
        root=row(origin["ledger"]["root"])
        self.rows[models.ExecutionOperationModel,root.key]=root
        self.steps={s["workflow_run_id"]:[row(s,("id","workflow_run_id"))]
                    for s in (evidence["selection_step"],origin["step"])}
        self.attempts=[row(a) for a in origin["ledger"]["attempts"]]
        self.transports=[SimpleNamespace(key=reader._sha(("transport-result.v1\n"+selection.op.SCOPE+"\n"+t["attempt_id"]).encode()),
            scope_id=selection.op.SCOPE,input_hash=origin["paid_return"]["plan"]["effective_input_hash"],result_json=deepcopy(t))
            for t in origin["ledger"]["transports"]]
        identity=origin["paid_return"]["plan"]["identity"]
        self.files={str(reader.AUDIT_DIRECTORY/identity/"paid-return.json"):
                    json.dumps(origin["paid_return"],ensure_ascii=False).encode()}
        self.files.update({v["ref"]:base64.b64decode(v["bytes_b64"]) for v in origin["physical_files"].values()})

    @property
    @contextmanager
    def no_autoflush(self):
        self.depth+=1
        try: yield
        finally:self.depth-=1

    async def get(self, model, key, **kwargs):
        assert self.depth and kwargs=={"populate_existing":True}
        self.reads.append((model.__name__,str(key)))
        return self.rows.get((model,str(key)))

    async def scalars(self, query):
        assert self.depth and query.is_select
        model=query.column_descriptions[0]["entity"]
        self.reads.append((model.__name__,"select"))
        if model is models.WorkflowStepRunModel:
            identity=next(iter(query.compile().params.values()))
            return self.steps.get(str(identity),[])
        if model is models.ExecutionAttemptModel:return self.attempts
        if model is models.ExecutionOperationModel:return self.transports
        raise AssertionError("Unexpected database read")

    def physical(self,root,relative,*,limit):
        raw=self.files[str(relative)];assert len(raw)<=limit
        return raw

    def __getattr__(self,name):
        raise AssertionError("No engine/commit/write permitted: "+name)


class PaidSelectionWrapperTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.evidence,cls.context=selection_fixture()

    def setUp(self):
        for owner,name in ((socket.socket,"connect"),(socket.socket,"connect_ex"),(socket,"create_connection")):
            guard=patch.object(owner,name,side_effect=AssertionError("offline only"));guard.start();self.addCleanup(guard.stop)

    def fresh(self):return deepcopy(self.evidence),deepcopy(self.context)

    def test_origin_six_then_current_eighteen_not_relabelled_and_no_semantic_grant(self):
        e,c=self.fresh();before=deepcopy([e,c]);receipt=native.validate(e,**c)
        self.assertEqual(e["origin"]["project"]["title"],"修好旧收音机")
        self.assertEqual(e["project"]["title"],SELECTED)
        self.assertEqual(receipt["lineage_kind"],"delegated_paid_candidate_selection")
        self.assertEqual(receipt["native_evaluation"]["decision"],"revise")
        self.assertEqual(receipt["editorial_quality_debt"][0]["overall_score"],52)
        self.assertEqual(receipt["literal_start"],0);self.assertEqual(receipt["literal_end"],18)
        self.assertFalse(receipt["semantic_verdict_granted"])
        self.assertFalse(receipt["historical_promise_fulfillment_granted"])
        self.assertEqual([e,c],before)

    def test_current_owner_step_scope_origin_and_debt_joins(self):
        cases=[("selection_workflow","project_id"), ("selection_workflow","scope_id"),
               ("selection_workflow","status"), ("selection_workflow","requested_by"),
               ("selection_step","workflow_run_id"), ("selection_step","status"),
               ("selection_step","id"), ("origin","llm_run","workflow_run_id"),
               ("origin","project","title"),
               ("selection_workflow","metadata_json","editorial_quality_debt"),
               ("project","metadata_json",selection.LINEAGE_KEY,"selected_title")]
        for path in cases:
            e,c=self.fresh();target=e
            for key in path[:-1]:target=target[key]
            target[path[-1]]="11111111-1111-4111-8111-111111111111"
            with self.subTest(path=path),self.assertRaises(native.PublicationTitleError):native.validate(e,**c)

    def test_source_body_control_candidate_offset_and_raw_grade_are_bound(self):
        for kind in ("body","control","offset","raw_grade","physical"):
            e,c=self.fresh()
            if kind=="body":c["full_text"]+="更改正文"
            elif kind=="control":c["manifest"]["control_serial"]+=1
            elif kind=="offset":e["selection_workflow"]["metadata_json"]["selection_plan"]["binding"]["literal_end"]+=1
            elif kind=="raw_grade":e["selection_workflow"]["metadata_json"]["selection_plan"]["native_evaluation"]["decision"]="pass"
            else:e["origin"]["physical_files"]["response"]["sha256"]="0"*64
            with self.subTest(kind=kind),self.assertRaises(native.PublicationTitleError):native.validate(e,**c)

    def test_origin_v1_compatibility_still_valid_under_its_actual_title(self):
        e,c=self.fresh();c["title"]=e["origin"]["project"]["title"]
        c["manifest"]=deepcopy(e["selection_workflow"]["metadata_json"]["selection_plan"]["binding"]["manifest_before"])
        receipt=native.validate(e["origin"],**c)
        self.assertEqual(receipt["workflow_run_id"],e["origin"]["workflow"]["id"])

    def test_reader_same_uncommitted_clean_transaction_reads_original_rows_and_files(self):
        e,c=self.fresh();session=TransactionRows(e)
        settings=SimpleNamespace(artifact_store=SimpleNamespace(mode="local",local_dir="/SYNTHETIC-NOT-PRODUCTION"))
        with patch.object(reader,"_read",side_effect=session.physical):
            actual=asyncio.run(reader.load_native_title_evidence(session,settings,project=session.project))
        self.assertEqual(actual["origin"],e["origin"])
        self.assertEqual(actual["selection_workflow"],e["selection_workflow"])
        receipt=native.validate(actual,**c)
        self.assertEqual(receipt["selection_workflow_run_id"],e["selection_workflow"]["id"])
        self.assertIn(("WorkflowRunModel",e["origin"]["workflow"]["id"]),session.reads)
        self.assertIn(("LlmRunModel",e["origin"]["llm_run"]["id"]),session.reads)

    def test_reader_wrong_real_selection_step_cannot_hide_behind_stored_flags(self):
        e,_=self.fresh();session=TransactionRows(e)
        session.steps[e["selection_workflow"]["id"]][0].status="running"
        settings=SimpleNamespace(artifact_store=SimpleNamespace(mode="local",local_dir="/SYNTHETIC-NOT-PRODUCTION"))
        with patch.object(reader,"_read",side_effect=session.physical),self.assertRaises(reader.TitleEvidenceError):
            asyncio.run(reader.load_native_title_evidence(session,settings,project=session.project))


if __name__=="__main__":unittest.main(verbosity=2)
