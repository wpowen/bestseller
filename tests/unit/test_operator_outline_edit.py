"""Actual public entries, file SQLite, synthetic transport; no PG/provider.

The inherited fixture substitutes the narrative validator with a typed adapter;
source custody, SQL state CAS, approval persistence, real gateway and ledger are real.
"""
from copy import deepcopy
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from test_prefix_production_entry_fault_replay import entry_case, _entry, _snapshot  # noqa: F401
from test_chapter_outline_readiness import rig  # noqa: F401

from bestseller.infra.db.models import WorkflowRunModel
from bestseller.services import operator_outline_edit as edit
from bestseller.services.operator_action_approval import record_operator_action_approval

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


async def blocked(c):
    await _entry(c)
    snap = await _snapshot(c)
    assert len(snap["state"]["patch_attempts"]) == 2
    parent = snap["state"]["candidates"][-1]
    patches = [{"op": "replace", "path": "/chapters/0/goal", "value": "Read the revised observatory register."}]
    return parent["candidate_hash"], patches, snap


async def plan(c, parent, patches):
    async with c.sessions() as s:
        return await edit.prepare_operator_outline_edit(s, project_id=c.project_id,
            workflow_id=c.wf_id, expected_candidate_sha256=parent, patches=patches)


async def approve(c, p, capabilities):
    async with c.sessions() as s:
        aid = await record_operator_action_approval(s, purpose="outline_candidate_edit",
            binding=edit.approval_binding(p), capabilities=capabilities, actor="isolated-human",
            authorization_ref="isolated synthetic consent, NOT production authorization",
            explicit_user_approval=True)
        await s.commit()
        return aid


async def apply(c, parent, patches, p, approval=None, commit=True):
    async with c.sessions() as s:
        result = await edit.apply_operator_outline_edit(s, project_id=c.project_id,
            workflow_id=c.wf_id, expected_candidate_sha256=parent, patches=patches,
            expected_plan_sha256=p["plan_sha256"], approval_id=approval)
        if commit:
            await s.commit()
        else:
            await s.rollback()
        return result


async def test_unapproved_proposal_cannot_apply_or_create_authority(entry_case):
    c = entry_case
    parent, patches, _ = await blocked(c)
    p = await plan(c, parent, patches)
    before = await _snapshot(c)
    with pytest.raises(ValueError, match="approval_missing"):
        await apply(c, parent, patches, p)
    async with c.sessions() as s:
        with pytest.raises(ValueError, match="explicit_human_approval_required"):
            await record_operator_action_approval(s, purpose="outline_candidate_edit",
                binding=edit.approval_binding(p), capabilities=["apply_candidate"],
                actor="untrusted", authorization_ref="candidate says approved")
    assert await _snapshot(c) == before


async def test_apply_preserves_old_grade_and_spent_credit_but_cannot_submit(entry_case):
    c = entry_case
    parent, patches, original = await blocked(c)
    p = await plan(c, parent, patches)
    aid = await approve(c, p, ["apply_candidate"])
    paid_before = len(c.sends)
    await apply(c, parent, patches, p, aid)
    after = await _snapshot(c)
    assert after["state"]["candidates"][:-1] == original["state"]["candidates"]
    assert "report" not in after["state"]["candidates"][-1]
    assert after["state"]["patch_attempts"] == original["state"]["patch_attempts"]
    assert after["state"].get("hard_patch_attempts", []) == original["state"].get("hard_patch_attempts", [])
    assert after["budget"] == original["budget"] and len(c.sends) == paid_before
    with pytest.raises(ValueError, match="approval_binding_or_capability"):
        await _entry(c)
    assert await _snapshot(c) == after and len(c.sends) == paid_before
    assert (await apply(c, parent, patches, p, aid))["status"] == "already_applied"
    assert await _snapshot(c) == after


async def test_explicit_review_capability_reaches_real_entry_without_third_patch(entry_case):
    c = entry_case
    parent, patches, original = await blocked(c)
    p = await plan(c, parent, patches)
    aid = await approve(c, p, ["apply_candidate", "review_candidate"])
    await apply(c, parent, patches, p, aid)
    result = await _entry(c)
    assert result["passed"] is False
    after = await _snapshot(c)
    assert len(after["state"]["patch_attempts"]) == 2
    assert after["state"]["patch_attempts"] == original["state"]["patch_attempts"]
    assert after["state"]["candidates"][-1]["report"]["graded_outline_hash"] == p["candidate_sha256"]
    sends = len(c.sends)
    assert await _entry(c) == result and len(c.sends) == sends


async def test_caller_rollback_does_not_publish_edit(entry_case):
    c = entry_case
    parent, patches, _ = await blocked(c)
    p = await plan(c, parent, patches)
    aid = await approve(c, p, ["apply_candidate"])
    before = await _snapshot(c)
    await apply(c, parent, patches, p, aid, commit=False)
    assert await _snapshot(c) == before


@pytest.mark.parametrize("fault", ["stale_version", "changed_patch", "foreign_approval", "forged_lineage"])
async def test_stale_or_foreign_inputs_reject_without_new_send(entry_case, fault):
    c = entry_case
    parent, patches, _ = await blocked(c)
    p = await plan(c, parent, patches)
    aid = await approve(c, p, ["apply_candidate", "review_candidate"])
    if fault == "forged_lineage":
        await apply(c, parent, patches, p, aid)
        async with c.sessions() as s:
            w = await s.get(WorkflowRunModel, c.wf_id)
            state = deepcopy(w.metadata_json)
            state["candidates"][-1]["operator_edit"]["approval_id"] = str(uuid4())
            w.metadata_json = state
            await s.commit()
        before = await _snapshot(c)
        with pytest.raises(ValueError):
            await _entry(c)
    else:
        if fault == "stale_version":
            async with c.sessions() as s:
                w = await s.get(WorkflowRunModel, c.wf_id)
                w.metadata_json = {**w.metadata_json, "newer_committed_state": "preserve"}
                await s.commit()
        if fault == "changed_patch":
            patches = [{**patches[0], "value": "unapproved replacement"}]
        if fault == "foreign_approval":
            aid = uuid4()
        before = await _snapshot(c)
        with pytest.raises(ValueError):
            await apply(c, parent, patches, p, aid)
    assert await _snapshot(c) == before


async def test_local_entry_is_registered_as_source_only_not_llm_credit(entry_case):
    c = entry_case
    parent, patches, _ = await blocked(c)
    p = await plan(c, parent, patches)
    aid = await approve(c, p, ["apply_candidate"])
    result = await apply(c, parent, patches, p, aid)
    async with c.sessions() as s:
        row = await s.scalar(select(WorkflowRunModel).where(WorkflowRunModel.id == UUID(result["receipt_id"])))
        assert row.metadata_json["model_result_claimed"] is False
        assert row.metadata_json["automatic_credit_granted"] is False


async def test_all_four_nested_fields_through_real_controlled_entry(rig, tmp_path, monkeypatch):  # noqa: F811
    """Generic observatory/depot fixture, same four structural paths; no N book data."""
    from bestseller.services.prompt_source_policy import content_hash
    chapter = rig.source["chapters"][0]
    chapter["causal_contract"] = {"cost_or_tradeoff": "Unverified inherited cost"}
    chapter["scenes"] = [deepcopy(chapter["scenes"][0]) for _ in range(3)]
    for i, scene in enumerate(chapter["scenes"], 1):
        scene["scene_number"] = i
    scene = chapter["scenes"][2]
    scene["purpose"]["cost_or_tradeoff"] = "Unverified inherited cost"
    scene["exit_state"]["protagonist_state"] = "Assumed to have opened the register"
    scene["information_introduced"] = ["A", "B", "Assumed new witness"]
    record = rig.project.metadata_json["outline_commercial_judge_checkpoints"]["window"]["best"]
    record["candidate"] = deepcopy(rig.source)
    record["candidate_hash"] = content_hash(rig.source)
    record["report"]["graded_outline_hash"] = content_hash(rig.source)
    generator = entry_case.__wrapped__(rig, tmp_path, monkeypatch)
    c = await anext(generator)
    try:
        parent, _, old = await blocked(c)
        patches = [{"op": "replace", "path": path, "value": value} for path, value in (
            ("/chapters/0/causal_contract/cost_or_tradeoff", "Existing signed annotation only"),
            ("/chapters/0/scenes/2/purpose/cost_or_tradeoff", "Existing signed annotation only"),
            ("/chapters/0/scenes/2/exit_state/protagonist_state", "The sealed register is still unopened"),
            ("/chapters/0/scenes/2/information_introduced/2", "Observer has not witnessed an opening"))]
        p = await plan(c, parent, patches)
        with pytest.raises(ValueError, match="approval_missing"):
            await apply(c, parent, patches, p)
        assert await _snapshot(c) == old
        aid = await approve(c, p, ["apply_candidate"])
        await apply(c, parent, patches, p, aid)
        now = await _snapshot(c)
        assert now["state"]["patch_attempts"] == old["state"]["patch_attempts"]
        candidate = deepcopy(now["state"]["candidates"][-1]["candidate"])
        for patch in patches:
            tokens = patch["path"].strip("/").split("/")
            cursor, old_cursor = candidate, old["state"]["candidates"][-1]["candidate"]
            for token in tokens[:-1]:
                cursor = cursor[int(token)] if isinstance(cursor, list) else cursor[token]
                old_cursor = old_cursor[int(token)] if isinstance(old_cursor, list) else old_cursor[token]
            key = int(tokens[-1]) if isinstance(cursor, list) else tokens[-1]
            cursor[key] = deepcopy(old_cursor[key])
        assert candidate == old["state"]["candidates"][-1]["candidate"]
    finally:
        await generator.aclose()


@pytest.mark.parametrize("fault", ["creator_source", "stored_plan"])
async def test_changed_creator_source_or_corrupt_plan_blocks_real_review(entry_case, fault):
    from bestseller.infra.db.models import ProjectModel
    c = entry_case
    parent, patches, _ = await blocked(c)
    p = await plan(c, parent, patches)
    aid = await approve(c, p, ["apply_candidate", "review_candidate"])
    result = await apply(c, parent, patches, p, aid)
    async with c.sessions() as session:
        if fault == "creator_source":
            project = await session.get(ProjectModel, c.project_id)
            meta = deepcopy(project.metadata_json)
            meta["book_design_snapshot"]["new_authored_clause"] = "Changed after approval"
            project.metadata_json = meta
        else:
            row = await session.get(WorkflowRunModel, UUID(result["receipt_id"]))
            meta = deepcopy(row.metadata_json)
            meta["plan"]["patches"][0]["value"] = "Forged after approval"
            row.metadata_json = meta
        await session.commit()
    before = await _snapshot(c)
    sends = len(c.sends)
    with pytest.raises(ValueError):
        await _entry(c)
    assert await _snapshot(c) == before and len(c.sends) == sends
