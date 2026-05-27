from __future__ import annotations

from bestseller.domain.gate_verdict import GateFinding, GateVerdict
from bestseller.services.material_self_repair import (
    MaterialRepairAction,
    MaterialSelfRepairPlan,
)
from bestseller.services.wip_repair_closure import build_wip_repair_plan_from_gates


def test_wip_repair_plan_turns_splice_findings_into_rewrite_specs() -> None:
    gate = GateVerdict(
        gate_name="chapter_splice_coherence",
        verdict="blocked",
        coverage=0.0,
        metrics={"chapter_number": 5},
        findings=(
            GateFinding(
                code="CHAPTER_SPLICE_PRESENCE_CONTRADICTION",
                severity="critical",
                message="苏婉宁离场后又行动",
                path="line:77",
                repair_action="明确回场或删除误拼动作。",
            ),
        ),
    )
    material = MaterialSelfRepairPlan(
        project_dir="/book",
        actions=(),
        blocking=False,
        metrics={"action_count": 0, "blocking_action_count": 0},
    )

    plan = build_wip_repair_plan_from_gates(
        slug="qingnang",
        repair_start=1,
        repair_end=10,
        splice_gates=(gate,),
        material_plan=material,
    )

    assert plan.to_dict()["task_count"] == 1
    spec = plan.specs[0]
    assert spec.chapter_number == 5
    assert spec.priority == "critical"
    assert spec.cause_ids == ("CHAPTER_SPLICE_PRESENCE_CONTRADICTION",)
    assert spec.patch_points[0]["location"] == "line:77"


def test_wip_repair_plan_adds_material_blocker_to_front_window() -> None:
    material = MaterialSelfRepairPlan(
        project_dir="/book",
        actions=(
            MaterialRepairAction(
                action_type="create_missing_entity_placeholder",
                target="周雪",
                reason="material references an entity that is not registered",
                source_path="story-bible/outline.md:12",
                payload={
                    "context": "周雪家属要看证物",
                    "minimum_fields": ("identity", "role"),
                },
            ),
        ),
        blocking=True,
        metrics={"action_count": 1, "blocking_action_count": 1},
    )

    plan = build_wip_repair_plan_from_gates(
        slug="qingnang",
        repair_start=1,
        repair_end=10,
        splice_gates=(),
        material_plan=material,
    )

    assert plan.to_dict()["task_count"] == 1
    spec = plan.specs[0]
    assert spec.chapter_number == 1
    assert spec.cause_ids == (
        "MATERIAL_SELF_REPAIR_BLOCKING",
        "WIP_FRONT_WINDOW_REPAIR",
    )
    assert spec.patch_points[0]["cause_id"] == "MATERIAL_SELF_REPAIR_BLOCKING"
    assert "identity" in spec.patch_points[0]["repair_action_summary"]
