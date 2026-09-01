from __future__ import annotations

from bestseller.domain.story_engine import StoryEngineMaturity
from bestseller.domain.story_state import StateCategory
from bestseller.services.story_design_kernel import story_design_kernel_from_dict
from bestseller.services.story_engine import (
    LegacyProjectionStatus,
    project_legacy_story_engine,
)


def _kernel_payload() -> dict[str, object]:
    return {
        "version": 1,
        "shape": {
            "length_class": "long",
            "publication_mode": "web_serial",
            "outline_depth": "chapter",
            "primary_duties": ["forward_pull", "relationship_state_shift"],
            "ending_contract": "close the current loop",
        },
        "reader_promise": "每章都让主动选择改变资源、关系或风险。",
        "premise_contract": {
            "unique_hook": "审计员发现晋升制度依赖一份被隐藏的事故记录。",
            "core_question": "她能否公开记录而不牺牲唯一的证人?",
            "commercial_pull": "调查推进与职场关系代价同步升级。",
            "forbidden_defaults": ["用失忆替代因果"],
        },
        "character_conflict_contracts": [
            {
                "character_key": "protagonist",
                "external_goal": "取得原始事故记录",
                "internal_need": "停止把盟友当作可消耗证据",
                "pressure_source": "管理层持续销毁审计链路",
                "choice_axis": "保护证人还是抢先公开",
                "change_vector": "从独自控制到共同承担",
            }
        ],
        "structure_strategy": {
            "macro_strategy": "证据链与关系链交替推进",
            "chapter_engine": "每章用一个主动选择改变局势",
            "pacing_rule": "短兑现后立即产生新代价",
            "freshness_rule": "连续章节不得复用同一种选择",
        },
        "plot_tree": [
            {
                "key": "mainline",
                "line_type": "main",
                "label": "事故记录调查",
                "role": "驱动外部目标",
                "current_state": "只有二手传闻",
                "target_state": "取得可公开的证据链",
                "failure_if_removed": "故事失去行动目标",
            }
        ],
        "beat_schedule": [
            {
                "chapter_range": "1-10",
                "duty": "取得记录并承受反制",
                "state_change": "资源减少、信任变化、暴露增加",
                "payoff": "证据链第一次闭合",
                "hook_or_aftereffect": "管理层转而追查证人",
            }
        ],
        "change_vectors": ["资源变化", "信任变化", "暴露变化"],
        "uniqueness_constraints": ["不得重复同一种调查动作"],
    }


def _valid_snapshot() -> dict[str, object]:
    return {
        "passed": True,
        "resource_balances": {"protagonist": {"access_token": 2}},
        "rule_state": {
            "audit-freeze": {
                "name": "审计冻结规则",
                "last_visible_effect": "调查权限被冻结",
                "last_cost": "身份暴露",
            }
        },
        "relationship_state": {
            "protagonist -> witness": {
                "axes": {"trust": "fragile"},
                "last_active_choice": "主动交出备份",
            }
        },
        "open_agency_debts": [
            {
                "owner": "protagonist",
                "debt": "保护证人的家属",
                "due_window": "3章内",
            }
        ],
    }


def test_valid_legacy_projection_maps_authoritative_state_but_stays_structure_only() -> None:
    kernel = story_design_kernel_from_dict(_kernel_payload())

    result = project_legacy_story_engine(
        engine_id="legacy-project-1",
        kernel=kernel,
        premium_state_snapshot=_valid_snapshot(),
    )

    assert result.status is LegacyProjectionStatus.STRUCTURE_ONLY
    assert result.maturity is StoryEngineMaturity.STRUCTURE_ONLY
    assert result.can_drive_generation is False
    assert result.engine is not None
    assert result.engine.choices == ()
    assert result.engine.reader_promise == "每章都让主动选择改变资源、关系或风险。"
    assert result.engine.change_vectors == ("资源变化", "信任变化", "暴露变化")
    assert "不得重复同一种调查动作" in result.engine.engine_invariants
    assert result.source_hash
    assert "LEGACY_REAL_CHOICES_UNAVAILABLE" in result.blocking_codes
    state = result.engine.initial_state
    assert state.values["resource:protagonist:access_token"].category is StateCategory.RESOURCE
    assert state.values["resource:protagonist:access_token"].value == 2.0
    relationship = state.values["relationship:protagonist -> witness:trust"]
    assert relationship.category is StateCategory.RELATIONSHIP
    assert relationship.value == "fragile"
    assert state.values["knowledge:rule:audit-freeze"].category is StateCategory.KNOWLEDGE
    assert state.values["debt:protagonist:0"].category is StateCategory.DEBT


def test_invalid_premium_snapshot_fails_closed_as_needs_replan() -> None:
    kernel = story_design_kernel_from_dict(_kernel_payload())

    result = project_legacy_story_engine(
        engine_id="legacy-project-2",
        kernel=kernel,
        premium_state_snapshot={
            "passed": False,
            "blocking_findings": [{"code": "progression_cause_missing"}],
        },
    )

    assert result.status is LegacyProjectionStatus.NEEDS_REPLAN
    assert result.can_drive_generation is False
    assert result.engine is None
    assert "LEGACY_PREMIUM_STATE_SNAPSHOT_INVALID" in result.blocking_codes


def test_valid_premium_ledger_is_materialized_before_projection() -> None:
    kernel = story_design_kernel_from_dict(_kernel_payload())

    result = project_legacy_story_engine(
        engine_id="legacy-project-ledger",
        kernel=kernel,
        premium_state_snapshot=None,
        premium_state_ledger={
            "progression_events": [
                {
                    "event_type": "resource_gained",
                    "subject": "protagonist",
                    "resource_key": "access_token",
                    "delta": 2,
                    "cause": "证人交出备份",
                }
            ]
        },
    )

    assert result.status is LegacyProjectionStatus.STRUCTURE_ONLY
    assert result.engine is not None
    value = result.engine.initial_state.values["resource:protagonist:access_token"]
    assert value.value == 2.0


def test_missing_premium_snapshot_fails_closed_as_needs_replan() -> None:
    kernel = story_design_kernel_from_dict(_kernel_payload())

    result = project_legacy_story_engine(
        engine_id="legacy-project-3",
        kernel=kernel,
        premium_state_snapshot=None,
    )

    assert result.status is LegacyProjectionStatus.NEEDS_REPLAN
    assert result.engine is None
    assert "LEGACY_PREMIUM_STATE_SNAPSHOT_MISSING" in result.blocking_codes


def test_invalid_kernel_mapping_fails_closed() -> None:
    result = project_legacy_story_engine(
        engine_id="legacy-invalid-kernel",
        kernel={"reader_promise": ""},
        premium_state_snapshot=_valid_snapshot(),
    )

    assert result.status is LegacyProjectionStatus.NEEDS_REPLAN
    assert result.engine is None
    assert "LEGACY_STORY_DESIGN_KERNEL_INVALID" in result.blocking_codes


def test_missing_engine_id_fails_closed() -> None:
    kernel = story_design_kernel_from_dict(_kernel_payload())

    result = project_legacy_story_engine(
        engine_id=" ",
        kernel=kernel,
        premium_state_snapshot=_valid_snapshot(),
    )

    assert result.status is LegacyProjectionStatus.NEEDS_REPLAN
    assert "LEGACY_ENGINE_ID_MISSING" in result.blocking_codes


def test_invalid_resource_balance_fails_closed() -> None:
    kernel = story_design_kernel_from_dict(_kernel_payload())
    snapshot = _valid_snapshot()
    snapshot["resource_balances"] = {"protagonist": {"access_token": "many"}}

    result = project_legacy_story_engine(
        engine_id="legacy-invalid-resource",
        kernel=kernel,
        premium_state_snapshot=snapshot,
    )

    assert result.status is LegacyProjectionStatus.NEEDS_REPLAN
    assert result.engine is None
    assert "LEGACY_RESOURCE_BALANCE_INVALID" in result.blocking_codes


def test_faction_pressure_projects_as_exposure_state() -> None:
    kernel = story_design_kernel_from_dict(_kernel_payload())
    snapshot = _valid_snapshot()
    snapshot["faction_pressure_queue"] = [
        {
            "faction": "management",
            "trigger": "证据链闭合",
            "reaction": "冻结证人与调查员的出入权限",
        }
    ]

    result = project_legacy_story_engine(
        engine_id="legacy-faction-pressure",
        kernel=kernel,
        premium_state_snapshot=snapshot,
    )

    assert result.engine is not None
    pressure = result.engine.initial_state.values["exposure:faction:management:0"]
    assert pressure.category is StateCategory.EXPOSURE
