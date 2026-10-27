"""Actual volume-planner false verdict -> debt -> normal prefix hard refusal.

Creative generation, semantic verdict, DB/physical model boundaries are inert;
normal service control flow is real. Not a real model or production acceptance.
"""

from copy import deepcopy
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
from uuid import uuid4
import pytest
from test_chapter_outline_readiness import rig as rig, _approve, service
from bestseller.services import planner
from bestseller.services.finite_book_budget import bind_run
from bestseller.settings import load_settings
from bestseller.infra.db.models import WorkflowRunModel

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


@pytest.fixture(autouse=True)
def no_network_or_model(monkeypatch):
    import socket
    from bestseller.services import llm

    def denied(*a, **kw):
        raise AssertionError("offline normal-planner test forbids model/network")

    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(llm, "complete_text", denied)
    monkeypatch.setattr(planner, "complete_text", denied)


async def test_actual_volume_plan_false_is_debt_but_actual_prefix_hard_admission_still_refuses(
    rig, monkeypatch
):
    p = rig.project
    p.metadata_json = {
        **p.metadata_json,
        "commercial_strict_prewrite": True,
        "creation_intent_contract": {
            "field_sources": {"story_enhancers": "explicit"},
            "story_enhancers": {"effect_skills": ["hype_satisfaction_engine"]},
        },
    }
    explicit = deepcopy(p.metadata_json["creation_intent_contract"])
    settings = load_settings()
    settings.pipeline.enable_rolling_outline = False
    settings.pipeline.enable_prewrite_readiness_gate = False
    source = deepcopy(rig.source)
    imports = []
    runs = []

    async def project(*a, **kw):
        return p

    monkeypatch.setattr(planner, "get_project_by_slug", project)
    monkeypatch.setattr(planner, "_source_bound_cast_enabled", lambda p: False)
    monkeypatch.setattr(
        "bestseller.services.planning_concurrency.cancel_stale_planning_workflows", AsyncMock()
    )
    monkeypatch.setattr(planner, "resolve_novel_category", lambda *a: None)

    async def create(_session, **kw):
        row = NS(
            id=uuid4(),
            project_id=p.id,
            workflow_type=kw["workflow_type"],
            scope_type=kw["scope_type"],
            scope_id=kw["scope_id"],
            metadata_json=kw["metadata"],
            status=kw["status"].value,
            current_step=kw["current_step"],
            idempotency_key=None,
        )
        runs.append(row)
        rig.store.rows[(WorkflowRunModel, row.id)] = row
        return row

    monkeypatch.setattr(
        planner, "planning_artifact_meta", lambda p: {"fixture_metadata_only": True}
    )
    monkeypatch.setattr(planner, "create_workflow_run", create)
    monkeypatch.setattr(planner, "create_workflow_step_run", AsyncMock())

    async def artifact(_session, slug, value):
        row = NS(
            id=uuid4(),
            project_id=p.id,
            version_no=1,
            content=deepcopy(value.content),
            status="approved",
        )
        imports.append((value.artifact_type, row))
        return row

    monkeypatch.setattr(planner, "import_planning_artifact", artifact)
    monkeypatch.setattr(planner, "_normalize_volume_plan_conflict_phases", lambda v, **kw: v)
    monkeypatch.setattr(
        planner,
        "_volume_world_disclosure_prompts",
        lambda *a, **kw: ("fixture system", "fixture source"),
    )
    monkeypatch.setattr(planner, "attach_planner_methodology", lambda v, **kw: v)
    monkeypatch.setattr(
        planner,
        "_generate_structured_artifact",
        AsyncMock(
            return_value=(
                {
                    "new_locations": [],
                    "new_rules_revealed": [],
                    "faction_movements": [],
                    "frontier_summary": "",
                },
                None,
            )
        ),
    )
    monkeypatch.setattr(planner, "_persist_character_drama_map", lambda *a: None)
    monkeypatch.setattr(planner, "_next_chapter_number_for_volume", AsyncMock(return_value=1))
    monkeypatch.setattr(
        planner, "_fallback_chapter_outline_batch", lambda *a, **kw: deepcopy(source)
    )
    monkeypatch.setattr(planner, "_build_revealed_ledger_block", AsyncMock(return_value=""))
    monkeypatch.setattr(
        planner, "_restore_persisted_outline_repair_baseline", lambda *a: (None, None)
    )
    monkeypatch.setattr(
        planner,
        "_restore_best_historical_outline_repair_baseline",
        AsyncMock(return_value=(None, None)),
    )
    monkeypatch.setattr(
        "bestseller.services.outline_recovery_checkpoint.restore_active_surgical_repair_source",
        lambda **kw: (None, None),
    )
    generated = AsyncMock(return_value=(deepcopy(source), [], []))
    monkeypatch.setattr(planner, "_generate_volume_outline_batched", generated)
    false = {
        "passed": False,
        "overall_score": 0.55,
        "blocking_issues": [
            {
                "code": "EDITORIAL_TASTE_FIXTURE",
                "severity": "high",
                "evidence": "A quiet scene.",
                "required_fix": "Editorial preference only.",
            }
        ],
        "semantic_candidates_adjudicated_by_llm": True,
        "repair_directives": ["editorial preference"],
    }
    from bestseller.domain.llm_quality_judge import LLMQualityJudgeResult

    judgement = AsyncMock(
        return_value=LLMQualityJudgeResult(
            scope="outline",
            passed=False,
            overall_score=0.55,
            llm_run_id=str(uuid4()),
            blocking_issues=tuple(false["blocking_issues"]),
        )
    )
    monkeypatch.setattr(
        "bestseller.services.outline_llm_judge.judge_outline_commercial_readiness", judgement
    )
    from bestseller.services import outline_semantic_gate as semantic

    monkeypatch.setattr(
        semantic,
        "evaluate_outline_semantic_gate",
        lambda *a: NS(promotion_allowed=True, to_dict=lambda: {"promotion_allowed": True}),
    )
    monkeypatch.setattr(semantic, "hard_contract_findings", lambda report: [])
    monkeypatch.setattr(semantic, "llm_adjudication_candidates", lambda report: [])
    with bind_run(uuid4(), project_id=p.id, stage="7:plan"):
        result = await planner.generate_volume_plan(
            rig.store,
            settings,
            p.slug,
            1,
            book_spec=rig.foundation["book_spec"].content,
            world_spec=rig.foundation["world_spec"].content,
            cast_spec=rig.foundation["cast_spec"].content,
            volume_plan=[
                {
                    "volume_number": 1,
                    "chapter_count_target": 9,
                    "volume_goal": "Preserve explicit source contracts",
                }
            ],
        )
    assert result.chapter_count == 9 and generated.await_count == judgement.await_count == 1
    wf = runs[-1]
    debt = wf.metadata_json["editorial_quality_debt"]
    assert (
        len(debt) == 1
        and not debt[0]["commercial_passed"]
        and debt[0]["source_hard_admission_required"]
    )
    assert (
        wf.metadata_json["provisional_outline_semantic_gate"]["llm_adjudication"]["passed"] is False
    )
    assert wf.metadata_json["commercial_passed"] is False
    assert p.metadata_json["creation_intent_contract"] == explicit
    assert "outline_commercial_last_approved" not in p.metadata_json
    assert "outline_quality_debt" not in p.metadata_json
    assert not planner._may_promote_outline_quality_debt(p)  # No default/global relaxation.
    # Feed this proposal into the actual normal prefix service. A synthetic
    # known hard-admission rejection cannot become a commercial-debt pass.
    from bestseller.services import planning_continuity_context, planning_draft_admission

    async def context(*a, **kw):
        return {"context_hash": "fixture-source-before2", "before_chapter": 2}

    monkeypatch.setattr(planning_continuity_context, "build_planning_continuity_context", context)
    seen = []

    async def hard_rejection(*a, **kw):
        seen.append(kw)
        return {
            "schema": planning_draft_admission.SCHEMA,
            "hard_admission_policy": planning_draft_admission.HARD_ADMISSION_POLICY,
            "draft_eligible": False,
            "assessment": {
                "draft_eligible": False,
                "hard_violations": [
                    {
                        "code": "EXPLICIT_CREATION_REQUIREMENT_CONFLICT",
                        "evidence": "Fixture exact explicit-source conflict",
                    }
                ],
                "editorial_notes": [],
            },
        }

    monkeypatch.setattr(planning_draft_admission, "assess_planning_draft_admission", hard_rejection)
    # Match the normal autonomous producer's artifact -> _body handoff exactly.
    generated_artifact = next(row for kind, row in imports if str(kind) == "volume_chapter_outline")
    from bestseller.services.autonomous_window_handlers import planning_review_source

    rig.source, handoff = planning_review_source(
        p, generated_artifact, workflow_id=wf.id, volume_number=1
    )
    assert handoff["write_authorized"] is False and handoff["original_verdict_changed"] is False
    assert service._body(rig.source) == service._body(generated_artifact.content)
    # Metadata enrichment is not story authority, and an actual changed story
    # or unrelated producer cannot borrow this candidate's original verdict.
    changed = NS(**vars(generated_artifact))
    changed.content = deepcopy(generated_artifact.content)
    changed.content["chapters"][0]["goal"] = "A different authored event."
    with pytest.raises(ValueError, match="review_source_missing_or_ambiguous"):
        planning_review_source(p, changed, workflow_id=wf.id, volume_number=1)
    with pytest.raises(ValueError, match="review_source_missing_or_ambiguous"):
        planning_review_source(p, generated_artifact, workflow_id=uuid4(), volume_number=1)
    assert any(
        record.get("report", {}).get("passed") is False
        for bucket in p.metadata_json["outline_commercial_judge_checkpoints"].values()
        for record in [bucket.get("latest_candidate") or {}]
    )
    result = await _approve(
        rig, from_chapter=2, through_chapter=2, allow_editorial_debt=True, max_repair_rounds=0
    )
    assert not result["draft_eligible"] and seen and not rig.imports
    assert not rig.judge_inputs
    assert p.metadata_json["creation_intent_contract"] == explicit
