"""T4 验收: payoff_evidence_paths 真实消费方."""
import os
from unittest import mock

import pytest


def test_payoff_audit_dict_includes_evidence_paths():
    """payoff_ledger_audit_to_dict 必须把 evidence_paths 写入输出。"""
    with mock.patch.dict(os.environ, {"BESTSELLER_METHODOLOGY_V2": "1"}):
        from bestseller.services.payoff_ledger_runtime import (
            payoff_ledger_audit_to_dict,
        )
        from bestseller.services.payoff_ledger import run_payoff_ledger_audit

        # Audit that won't error
        audit = run_payoff_ledger_audit([], current_chapter=5)
        evidence = [
            {"payoff_code": "p1", "scene_ref": "scene 3", "note": "电梯电话"},
            {"payoff_code": "p2", "scene_ref": "scene 7", "note": "反派揭晓"},
        ]
        d = payoff_ledger_audit_to_dict(audit, evidence_paths=evidence)
        assert "evidence_paths" in d
        assert len(d["evidence_paths"]) == 2
        assert d["evidence_paths"][0]["payoff_code"] == "p1"


def test_rewrite_instructions_include_evidence_zh():
    """_payoff_ledger_rewrite_instructions 必须把 evidence 注入中文 prompt。"""
    with mock.patch.dict(os.environ, {"BESTSELLER_METHODOLOGY_V2": "1"}):
        from bestseller.services.payoff_ledger_runtime import (
            _payoff_ledger_rewrite_instructions,
        )
        from bestseller.services.payoff_ledger import run_payoff_ledger_audit

        audit = run_payoff_ledger_audit(
            [
                type(
                    "P",
                    (),
                    {
                        "payoff_code": "p_due",
                        "target_chapter_number": 5,
                        "label": "due",
                        "description": "due",
                        "source_clue_id": None,
                        "actual_chapter_number": None,
                        "status": "planned",
                        "metadata_json": {},
                    },
                )()
            ],
            current_chapter=5,
        )
        evidence = [
            {"payoff_code": "p_due", "scene_ref": "scene 3", "note": "电话"},
        ]
        instr = _payoff_ledger_rewrite_instructions(
            audit, 5, language="zh-CN", evidence_paths=evidence,
        )
        assert "p_due" in instr
        assert "保留这些" in instr or "策划的兑现证据" in instr


def test_rewrite_instructions_include_evidence_en():
    """_payoff_ledger_rewrite_instructions 必须把 evidence 注入英文 prompt。"""
    with mock.patch.dict(os.environ, {"BESTSELLER_METHODOLOGY_V2": "1"}):
        from bestseller.services.payoff_ledger_runtime import (
            _payoff_ledger_rewrite_instructions,
        )
        from bestseller.services.payoff_ledger import run_payoff_ledger_audit

        audit = run_payoff_ledger_audit(
            [
                type(
                    "P",
                    (),
                    {
                        "payoff_code": "p_due",
                        "target_chapter_number": 5,
                        "label": "due",
                        "description": "due",
                        "source_clue_id": None,
                        "actual_chapter_number": None,
                        "status": "planned",
                        "metadata_json": {},
                    },
                )()
            ],
            current_chapter=5,
        )
        evidence = [
            {"payoff_code": "p_due", "scene_ref": "scene 3", "note": "phone call"},
        ]
        instr = _payoff_ledger_rewrite_instructions(
            audit, 5, language="en-US", evidence_paths=evidence,
        )
        assert "p_due" in instr
        assert "evidence references" in instr


def test_merge_folds_evidence_into_evidence_summary():
    """merge_payoff_ledger_audit_into_chapter_review 必须把 evidence 写入 evidence_summary。"""
    with mock.patch.dict(os.environ, {"BESTSELLER_METHODOLOGY_V2": "1"}):
        from bestseller.services.payoff_ledger_runtime import (
            merge_payoff_ledger_audit_into_chapter_review,
        )
        from bestseller.services.payoff_ledger import run_payoff_ledger_audit
        from bestseller.domain.review import ChapterReviewResult, ChapterReviewScores

        class FakeContract:
            payoff_evidence_paths = [
                {"payoff_code": "p1", "scene_ref": "scene 3", "note": "call"},
            ]

        audit = run_payoff_ledger_audit(
            [
                type(
                    "P",
                    (),
                    {
                        "payoff_code": "p_due",
                        "target_chapter_number": 5,
                        "label": "due",
                        "description": "due",
                        "source_clue_id": None,
                        "actual_chapter_number": None,
                        "status": "planned",
                        "metadata_json": {},
                    },
                )()
            ],
            current_chapter=5,
        )
        review = ChapterReviewResult(
            verdict="pass",
            severity_max="info",
            scores=ChapterReviewScores(
                overall=1.0, goal=1.0, coverage=1.0, coherence=1.0,
                continuity=1.0, main_plot_progression=1.0, subplot_progression=1.0,
                style=1.0, hook=1.0, ending_hook_effectiveness=1.0,
                volume_mission_alignment=1.0, pacing_rhythm=1.0,
                character_voice_distinction=1.0, thematic_resonance=1.0,
                contract_alignment=1.0,
            ),
            findings=[],
            evidence_summary={},
            rewrite_instructions="",
        )
        merged = merge_payoff_ledger_audit_into_chapter_review(
            review, audit, chapter_number=5, language="en-US",
            chapter_contract=FakeContract(),
        )
        audit_dict = merged.evidence_summary.get("payoff_ledger_audit", {})
        assert "evidence_paths" in audit_dict
        assert len(audit_dict["evidence_paths"]) == 1
        assert audit_dict["evidence_paths"][0]["payoff_code"] == "p1"
