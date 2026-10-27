"""Append an ungraded full-outline repair proposal on the original budget.

Legacy Beat provenance remains unknown. Only the new retained patch is paid
evidence; current source admission and materialization are separate operations.
"""

# ruff: noqa: ANN001, ANN201, ANN202, RUF001 -- Validated heterogeneous JSON receipts.

from copy import deepcopy
import json
import re
from uuid import UUID

from bestseller.domain.fanqie_short import FanqieShortBeatSheet
from bestseller.infra.db import models as db
from bestseller.services import fanqie_short_planner, planner
from bestseller.services.conception_snapshots import snapshot_hash
from bestseller.services.materialized_outline_source_recovery import _read_sources
from bestseller.services.planning_readiness_gate import (
    evaluate_chapter_outline_batch_planning_readiness,
)
from bestseller.services.prompt_source_policy import content_hash
from bestseller.services.retained_planning_generation import _Reader

SCHEMA = "full-outline-revision-proposal.v1"
WORKFLOW = "propose_full_outline_revision"
LOGICAL_NAME = "volume_1_chapter_outline"
CONTEXT_MARKER = "只修补可执行字段与真实因果/时序冲突，不添加模板化情节。\n"
CANDIDATE_MARKER = "当前待修章纲（候选，不是来源授权）："


def _need(ok, code):
    if not ok:
        raise ValueError("outline_proposal:" + code)


def _same(a, b):
    return content_hash(a) == content_hash(b)


async def _inputs(
    session,
    settings,
    *,
    project_slug,
    parent_artifact_id,
    parent_materialization_workflow_id,
    beat_artifact_id,
):
    project, _parent, original, sources, binding = await _read_sources(
        session,
        settings,
        project_slug=project_slug,
        artifact_id=parent_artifact_id,
        materialization_workflow_id=parent_materialization_workflow_id,
    )
    reader = _Reader(session)
    canonical = await reader.get(db.PlanningArtifactVersionModel, UUID(binding["canonical_id"]))
    _need(
        canonical is not None
        and canonical.project_id == project.id
        and canonical.artifact_type == "conception_snapshot"
        and canonical.status == "canonical"
        and canonical.scope_ref_id is None
        and snapshot_hash(canonical.content) == binding["canonical_hash"],
        "canonical_changed",
    )
    beat = await reader.get(db.PlanningArtifactVersionModel, UUID(str(beat_artifact_id)))
    _need(
        beat is not None
        and beat.project_id == project.id
        and beat.artifact_type == "fanqie_beat_sheet"
        and beat.scope_ref_id is None
        and isinstance(beat.content, dict),
        "beat_scope",
    )
    raw_beat = deepcopy(beat.content)
    sheet = FanqieShortBeatSheet.model_validate(planner._without_planning_meta(raw_beat))
    compiled = fanqie_short_planner.build_fanqie_segment_outline_batch(
        project,
        sheet,
        book_spec=sources["book_spec"].content,
        cast_spec=sources["cast_spec"].content,
    )
    numbers = [chapter["chapter_number"] for chapter in compiled["chapters"]]
    _need(
        numbers == list(range(1, project.target_chapters + 1))
        and numbers == [c["chapter_number"] for c in original["chapters"]],
        "full_project_inventory",
    )
    volume = sources["volume_plan"].content
    entries = volume if isinstance(volume, list) else volume.get("volumes", [])
    _need(isinstance(entries, list), "volume_shape")
    matches = [
        entry
        for entry in entries
        if isinstance(entry, dict)
        and type(entry.get("volume_number")) is int
        and entry["volume_number"] == 1
    ]
    _need(len(matches) == 1, "exact_first_volume")
    report = evaluate_chapter_outline_batch_planning_readiness(compiled).model_dump(mode="json")
    context = {
        "schema": SCHEMA,
        "project_id": str(project.id),
        "approved_foundation": {
            kind: {
                "artifact_id": str(row.id),
                "version": row.version_no,
                "content_sha256": content_hash(row.content),
                "content": deepcopy(row.content),
            }
            for kind, row in sources.items()
        },
        "current_canonical": {
            "artifact_id": str(canonical.id),
            "content_sha256": binding["canonical_hash"],
            "content": deepcopy(canonical.content),
        },
        "untrusted_retained_beat": {
            "artifact_id": str(beat.id),
            "project_id": str(beat.project_id),
            "artifact_type": beat.artifact_type,
            "status": beat.status,
            "source_run_id": str(beat.source_run_id) if beat.source_run_id else None,
            "content_sha256": content_hash(raw_beat),
            "content": raw_beat,
            "authority": "untrusted_proposal_only",
            "paid_source_proof_asserted": False,
        },
        "legacy_materialized_base": {
            "binding": deepcopy(binding),
            "candidate_payload": deepcopy(original),
            "paid_full_source_asserted": False,
        },
        "compiler_candidate": deepcopy(planner._without_planning_meta(compiled)),
        "compiler_receipt": deepcopy(compiled.get("_meta", {})),
        "readiness_diagnostics": report,
        "project_source_brief": planner._outline_judge_project_brief(
            project,
            metadata=project.metadata_json,
            semantic_candidates=[],
            world_spec=sources["world_spec"].content,
        ),
        "grade_asserted": False,
    }
    validation = {
        "volume_number": 1,
        "expected_count": len(numbers),
        "chapter_number_offset": 1,
        "cast_spec": deepcopy(sources["cast_spec"].content),
        "volume_entry": deepcopy(matches[0]),
    }
    return project, context, validation


def _directives(context):
    findings = [
        *context["readiness_diagnostics"]["blocking_findings"],
        *context["readiness_diagnostics"]["audit_findings"],
    ]
    chapters = context["compiler_candidate"]["chapters"]
    groups = {c["chapter_number"]: [] for c in chapters}
    global_indices = []
    for index, finding in enumerate(findings):
        match = re.search(r"(?:chapter_outlines|chapters)\[(\d+)\]", finding["path"])
        if match and int(match[1]) < len(chapters):
            groups[chapters[int(match[1])]["chapter_number"]].append(index)
        else:
            global_indices.append(index)
    width = max(1, (len(chapters) + 3) // 4)
    numbers = list(groups)
    return [
        json.dumps(
            {
                "chapter_findings": {str(n): groups[n] for n in numbers[start : start + width]},
                "global_finding_indices": global_indices,
                "instruction": (
                    "按完整当前来源补齐实际可执行字段；诊断不是新增剧情授权。"
                    "核对真实动作先后、持续时长和明确截止点，"
                    "不能删除权威约束或编造新规则来消解冲突。"
                ),
            },
            ensure_ascii=False,
        )
        for start in range(0, len(numbers), width)
    ]


def _json_at(text, marker):
    _need(text.count(marker) == 1, "physical_request_marker")
    tail = text.split(marker, 1)[1]
    value, _ = json.JSONDecoder().raw_decode(tail)
    return value


async def _prove(session, settings, project, workflow_id, state):
    from bestseller.services.foundation_field_recovery import _paid

    result = deepcopy(state["candidate_payload"])
    surgical = result.get("_meta", {}).get("surgical_repair_receipt", {})
    before = state["source_context"]["compiler_candidate"]
    _need(_same(surgical.get("before_candidate"), before), "compiler_preimage")
    failed = surgical.get("protocol_attempts", [])
    _need(isinstance(failed, list) and len(failed) <= 1, "patch_attempt_limit")
    run_ids = [item["llm_run_id"] for item in failed] + [surgical.get("llm_run_id")]
    _need(len(set(run_ids)) == len(run_ids), "duplicate_paid_attempt")
    proofs = []
    for run_id in run_ids:
        run, request, completion, proof = await _paid(session, settings, project, run_id)
        _need(
            run.workflow_run_id == workflow_id
            and request.workflow_run_id == workflow_id
            and request.project_id == project.id
            and request.prompt_template == f"planner_{LOGICAL_NAME}_commercial_surgical_repair"
            and request.prompt_version == "3.4-window-complete-repair",
            "paid_request_owner",
        )
        _need(
            _same(_json_at(request.user_prompt, CONTEXT_MARKER), state["source_context"])
            and _same(_json_at(request.user_prompt, CANDIDATE_MARKER), before),
            "paid_request_sources",
        )
        proofs.append(proof)
    patches = planner._extract_json_payload(completion.content).get("patches")
    _need(isinstance(patches, list), "native_patch_array")
    exact = planner._apply_outline_commercial_patches(before, patches, strict=True)
    _need(
        _same(planner._without_planning_meta(result), planner._without_planning_meta(exact)),
        "patch_result_changed",
    )
    _need(not _same(before, planner._without_planning_meta(result)), "empty_repair")
    return {
        "schema": SCHEMA,
        "source_context_sha256": content_hash(state["source_context"]),
        "compiler_candidate_sha256": content_hash(before),
        "native_patch": deepcopy(patches),
        "candidate_sha256": content_hash(planner._without_planning_meta(result)),
        "paid_patch_attempts": proofs,
        "legacy_beat_paid_full_source_asserted": False,
        "grade_asserted": False,
        "adoption_granted": False,
        "budget_or_retry_allowance_granted": False,
        "actor": state["actor"],
        "authorization_ref": state["authorization_ref"],
    }


async def read_retained_legacy_proposal(session, settings, *, workflow_id,
        project_slug, parent_artifact_id, parent_materialization_workflow_id,
        beat_artifact_id):
    """Read a completed old proof; the retired protocol cannot dispatch again."""
    from bestseller.services.budget import current_execution_scope
    from bestseller.services.retry_ledger import project_execution_scope

    project,context,validation=await _inputs(session,settings,project_slug=project_slug,
        parent_artifact_id=parent_artifact_id,
        parent_materialization_workflow_id=parent_materialization_workflow_id,
        beat_artifact_id=beat_artifact_id)
    owner=await _Reader(session).get(db.WorkflowRunModel,UUID(str(workflow_id)))
    _need(owner is not None and owner.project_id==project.id
        and owner.workflow_type==WORKFLOW and owner.scope_type=='planning_artifact'
        and owner.scope_id==UUID(str(parent_artifact_id)) and owner.status=='completed',
        'legacy_protocol_retired')
    state=owner.metadata_json or {}
    _need(state.get('schema')==SCHEMA and state.get('execution_scope_id')==current_execution_scope()
        ==project_execution_scope(project) and _same(state.get('source_context'),context)
        and _same(state.get('validation_context'),validation),'legacy_sources_changed')
    receipt=await _prove(session,settings,project,owner.id,state)
    _need(_same(receipt,state.get('proposal_receipt')),'legacy_proof_changed')
    return {'candidate_payload':deepcopy(state['candidate_payload']),
        'proposal_receipt':receipt,'workflow_run_id':str(owner.id)}


async def propose_full_outline_revision(session,settings,**kwargs):
    """All prospective proposals use one stable source/transport protocol owner."""
    from bestseller.services.outline_revision_proposal_v2 import propose_full_outline_revision as propose
    return await propose(session,settings,**kwargs)
