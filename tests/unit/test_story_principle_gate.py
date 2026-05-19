# ruff: noqa: RUF001
from __future__ import annotations

from types import SimpleNamespace

import pytest

from bestseller.domain.workflow import ChapterOutlineBatchInput, ChapterOutlineInput
from bestseller.services.quality_gates_config import load_quality_gates_config
from bestseller.services.story_principle_gate import (
    evaluate_story_principle_contract,
    story_principle_report_to_dict,
)
from bestseller.services.workflows import _sync_chapter_causality_metadata

pytestmark = pytest.mark.unit


def test_story_principle_gate_accepts_event_unit_roles_without_per_chapter_six_step() -> None:
    batch = ChapterOutlineBatchInput.model_validate(
        {
            "batch_name": "v1-event-unit",
            "chapters": [
                {
                    "chapter_number": 1,
                    "goal": "灵田账册突然失衡，主角必须确认异常来源。",
                    "main_conflict": "长老会要求当晚交出解释。",
                    "hook_description": "账册上出现盟友私印。",
                    "chapter_event_role": "trigger",
                    "information_gap_mode": "reader_knows_equal",
                    "event_cycle_contract": {
                        "event_unit_id": "v1-event-1",
                        "emotion_event": "灵田收益突然归零。",
                        "reader_desire": "读者想知道异常是谁造成的。",
                        "event_pressure": "长老会限时追责。",
                        "handoff_to_next": "私印把压力转向盟友。",
                    },
                },
                {
                    "chapter_number": 2,
                    "goal": "主角决定保住盟友同时找到账册漏洞。",
                    "main_conflict": "保护盟友会失去向长老会自证的时间。",
                    "hook_description": "旧账页显示还有第二枚印。",
                    "chapter_event_role": "desire_lock",
                    "information_gap_mode": "protagonist_knows_less",
                    "event_cycle_contract": {
                        "event_unit_id": "v1-event-1",
                        "desire_goal": "主角要在不出卖盟友的前提下修复账册。",
                        "event_pressure": "时间和信任同时收紧。",
                        "reader_desire": "读者期待主角用信任解决经营危机。",
                    },
                },
                {
                    "chapter_number": 3,
                    "goal": "主角寻找绕过公开审计的修复方法。",
                    "main_conflict": "唯一方法会透支外门弟子的信任。",
                    "hook_description": "修复方案需要盟友公开担保。",
                    "chapter_event_role": "method_search",
                    "information_gap_mode": "reader_knows_less",
                    "event_cycle_contract": {
                        "event_unit_id": "v1-event-1",
                        "solution_method": "把账册漏洞转化为公开信任债测试。",
                        "reader_desire": "读者想看方法能否不伤盟友。",
                        "handoff_to_next": "担保要求把方法推向行动。",
                    },
                },
                {
                    "chapter_number": 4,
                    "goal": "主角公开执行信任债测试并承接后果。",
                    "main_conflict": "测试成功会暴露旧制度线索。",
                    "hook_description": "长老认出账册旧印。",
                    "chapter_event_role": "payoff_feedback",
                    "information_gap_mode": "reader_knows_more",
                    "event_cycle_contract": {
                        "event_unit_id": "v1-event-1",
                        "action_resolution": "主角完成公开测试。",
                        "resolution_feedback": "灵田恢复产出，但旧制度线索暴露。",
                        "next_reader_waiting": "读者想知道长老为何认得旧印。",
                    },
                },
            ],
        }
    )

    report = evaluate_story_principle_contract(batch, min_roles_per_batch=4)

    assert report.passed
    assert report.present_roles == {
        "trigger",
        "desire_lock",
        "method_search",
        "payoff_feedback",
    }
    assert "PER_CHAPTER_SIX_STEP_REQUIRED" not in {
        finding.code for finding in report.findings
    }


def test_story_principle_gate_warns_on_homogeneous_event_roles() -> None:
    batch = ChapterOutlineBatchInput.model_validate(
        {
            "batch_name": "flat",
            "chapters": [
                {
                    "chapter_number": number,
                    "goal": f"第{number}章继续承压推进。",
                    "main_conflict": "同一压力源反复制造阻碍。",
                    "hook_description": "下一章继续同一阻碍。",
                    "chapter_event_role": "obstacle_escalation",
                    "event_cycle_contract": {
                        "event_unit_id": "v1-event-flat",
                        "obstacle": "长老会继续阻拦。",
                        "event_pressure": "同一压力持续加码。",
                    },
                }
                for number in range(1, 5)
            ],
        }
    )

    report = evaluate_story_principle_contract(batch, max_same_role_streak=3)

    assert report.passed
    assert "EVENT_ROLE_STREAK" in {finding.code for finding in report.findings}


def test_story_principle_gate_report_serializes_findings() -> None:
    batch = ChapterOutlineBatchInput.model_validate(
        {
            "batch_name": "missing-desire",
            "chapters": [
                {
                    "chapter_number": 1,
                    "goal": "主角处理一件事。",
                    "main_conflict": "事情发生。",
                    "hook_description": "",
                    "chapter_event_role": "trigger",
                    "event_cycle_contract": {"event_unit_id": "v1-event-1"},
                }
            ],
        }
    )

    report = evaluate_story_principle_contract(batch)
    payload = story_principle_report_to_dict(report)

    assert payload["passed"] is True
    assert payload["findings"]
    assert payload["chapter_results"][0]["chapter_number"] == 1


def test_chapter_outline_persists_event_cycle_metadata() -> None:
    chapter = SimpleNamespace(metadata_json={})
    outline = ChapterOutlineInput.model_validate(
        {
            "chapter_number": 8,
            "goal": "主角寻找行动方法。",
            "chapter_event_role": "method_search",
            "information_gap_mode": "reader_knows_less",
            "event_cycle_contract": {
                "event_unit_id": "v1-event-2",
                "solution_method": "用公开测试替代私下解释。",
            },
        }
    )

    _sync_chapter_causality_metadata(chapter, outline)

    assert chapter.metadata_json["chapter_event_role"] == "method_search"
    assert chapter.metadata_json["information_gap_mode"] == "reader_knows_less"
    assert chapter.metadata_json["event_cycle_contract"]["event_unit_id"] == "v1-event-2"


def test_quality_gate_config_loads_story_principle_knobs(tmp_path) -> None:
    path = tmp_path / "quality_gates.yaml"
    path.write_text(
        """
story_principle_gate:
  enabled: true
  default: audit_only
  block_on_failure: false
  min_event_cycle_roles_per_batch: 4
  max_same_role_streak: 2
""",
        encoding="utf-8",
    )

    cfg = load_quality_gates_config(path)

    assert cfg.story_principle.enabled is True
    assert cfg.story_principle.min_event_cycle_roles_per_batch == 4
    assert cfg.story_principle.max_same_role_streak == 2
