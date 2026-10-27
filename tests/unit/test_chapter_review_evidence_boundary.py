from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

from bestseller.services.chapter_generation_input_builder import (
    build_chapter_generation_input_bundle,
)


def test_generation_input_excludes_prior_review_evidence_but_keeps_creative_contracts() -> None:
    chapter_metadata = {
        "causal_contract": {"state_change": "主角拿到现场证据"},
        "methodology_contract": {"decision_protocol": {"chosen_action": "先验物证"}},
        "whole_chapter_logic_contract": {"knowledge_boundary": "只写主角看见的异常"},
        "preview": {"opening_image": "湿鞋贴着门槛"},
        "reader_persona_contract": {"promise": "读者先看到异常"},
        "quality_contract": {"minimum_scene_turns": 2},
        "repair_shop_setting": {"location": "旧修理铺"},
        "chapter_continuity_contract": {"fact": "门钥匙始终由主角持有"},
        "current_body_fingerprint": {"digest": "runtime-only"},
        "current_body_review_status": "pending",
        "current_body_draft_id": "new-draft",
        "superseded_body_review_history": [{"reports": {"verdict": "old"}}],
        "deterministic_audit_latest": {"passed": False, "findings": [{"code": "STALE"}]},
        "contract_receipt_latest": {"clean": False},
        "chapter_continuity_latest": {"passed": True},
        "quality_bundle": {"passed": False, "blocking_codes": ["CHAPTER_TOO_SHORT"]},
        "reader_judge": {"prose_quality_score": 0.4},
        "reader_judge_voice_failures": ["模板化"],
        "persona_gate_evidence": {"weighted_score": 0.4},
        "retention_gate_last_findings": [{"code": "CHAPTER_TOO_SHORT"}],
        "gate_adjudication": {"verdict": "rewrite"},
        "common_sense_adjudication": {"dismissed_codes": []},
        "review_result": {"verdict": "rewrite", "evidence": "旧正文原句"},
    }
    original_metadata = deepcopy(chapter_metadata)
    chapter = SimpleNamespace(
        chapter_number=1,
        title="现场证据",
        chapter_goal="主角确认异常并作出选择。",
        opening_situation="雨夜门口有一只湿鞋。",
        main_conflict="主角必须在追问与撤退之间选择。",
        hook_type="现场异常",
        hook_description="湿鞋里传出第二个人的脚步声。",
        metadata_json=chapter_metadata,
    )
    scene = SimpleNamespace(
        scene_number=1,
        title="门口",
        scene_type="opening_pressure",
        time_label="深夜",
        participants=["主角"],
        purpose={"story": "主角检查湿鞋。"},
        entry_state={"location": "门口"},
        exit_state={"location": "门内"},
        target_word_count=700,
        key_dialogue_beats=[],
        sensory_anchors={"sound": "脚步声"},
        forbidden_actions=[],
        metadata_json={
            "methodology_contract": {
                "visible_action_or_reaction": "主角蹲下检查鞋底。",
                "cut_point": "鞋里传出脚步声。",
            }
        },
    )
    context = SimpleNamespace(
        story_bible={"characters": [{"name": "主角", "role": "protagonist"}]},
        previous_scene_summaries=[],
        recent_timeline_events=[],
        active_plot_arcs=[],
        active_arc_beats=[],
        unresolved_clues=[],
        planned_payoffs=[],
    )

    bundle = build_chapter_generation_input_bundle(
        project=SimpleNamespace(slug="boundary", title="边界", genre="悬疑", language="zh-CN"),
        chapter=chapter,
        scenes=[scene],
        context_packet=context,
        target_word_count=2200,
    )

    payload = bundle.chapter["metadata"]
    proposals = bundle.acceptance_contract["source_authority_channels"]["ai_proposals"]["chapter_metadata"]
    for key in ("causal_contract", "methodology_contract", "whole_chapter_logic_contract"):
        assert key not in payload
        assert proposals[key] == chapter_metadata[key]
    for key in (
        "preview",
        "reader_persona_contract",
        "quality_contract",
        "repair_shop_setting",
        "chapter_continuity_contract",
    ):
        assert payload[key] == chapter_metadata[key]
    for key in (
        "deterministic_audit_latest",
        "current_body_fingerprint",
        "current_body_review_status",
        "current_body_draft_id",
        "superseded_body_review_history",
        "contract_receipt_latest",
        "chapter_continuity_latest",
        "quality_bundle",
        "reader_judge",
        "reader_judge_voice_failures",
        "persona_gate_evidence",
        "retention_gate_last_findings",
        "gate_adjudication",
        "common_sense_adjudication",
        "review_result",
    ):
        assert key not in payload
    assert chapter.metadata_json == original_metadata
