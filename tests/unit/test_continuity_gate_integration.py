"""Integration tests for the continuity gate merges into chapter review.

Exercises ``_merge_chapter_seam_into_review`` / ``_merge_stitched_draft_into_review`` /
``_merge_name_canon_into_review`` directly (without a DB) and the canon-loader
helper ``_load_character_canon_for_project``. The ``_compute_*_signal``
counterparts depend on the SQLAlchemy session and are exercised in the
existing service-level tests via end-to-end chapter review.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from bestseller.domain.review import (
    ChapterReviewFinding,
    ChapterReviewResult,
    ChapterReviewScores,
)
from bestseller.services.reviews import (
    _load_character_canon_for_project,
    _merge_chapter_seam_into_review,
    _merge_name_canon_into_review,
    _merge_stitched_draft_into_review,
)

pytestmark = pytest.mark.unit


def _baseline_result() -> ChapterReviewResult:
    return ChapterReviewResult(
        verdict="pass",
        severity_max="info",
        scores=ChapterReviewScores(
            overall=0.85, goal=0.85, coverage=0.85, coherence=0.85,
            continuity=0.85, main_plot_progression=0.85, subplot_progression=0.85,
            style=0.85, hook=0.85, ending_hook_effectiveness=0.85,
            volume_mission_alignment=0.85, pacing_rhythm=0.85,
            character_voice_distinction=0.85, thematic_resonance=0.85,
            contract_alignment=0.85, duplication_score=0.0,
        ),
        findings=[],
        evidence_summary={},
        rewrite_instructions=None,
    )


# ---------------------------------------------------------------------------
# Chapter seam merge
# ---------------------------------------------------------------------------


def test_seam_merge_noop_when_findings_empty() -> None:
    result = _merge_chapter_seam_into_review(_baseline_result(), [], {})
    assert result.verdict == "pass"
    assert result.findings == []


def test_seam_critical_finding_forces_rewrite_with_repair_prompt() -> None:
    finding = ChapterReviewFinding(
        severity="critical",
        category="chapter_seam",
        message="前章 immediate_threat open thread「围」在本章开篇 300 字内未被承接。",
    )
    evidence = {
        "chapter_seam_silent_drops": [{"kind": "immediate_threat", "marker": "围"}],
        "chapter_seam_repair_prompt": "【章节断点修复任务】\n- 前章末尾尚有威胁未处理…",
    }
    result = _merge_chapter_seam_into_review(
        _baseline_result(), [finding], evidence,
    )
    assert result.verdict == "rewrite"
    assert result.severity_max == "critical"
    assert "章节断点" in (result.rewrite_instructions or "")
    assert "前章末尾尚有威胁未处理" in (result.rewrite_instructions or "")
    assert "chapter_seam_silent_drops" in result.evidence_summary


def test_seam_major_finding_does_not_force_rewrite() -> None:
    finding = ChapterReviewFinding(
        severity="major",
        category="chapter_seam",
        message="前章 location open thread「门口」未承接。",
    )
    result = _merge_chapter_seam_into_review(
        _baseline_result(), [finding], {"chapter_seam_score": 0.8},
    )
    # Major-only seam findings surface as warnings but don't force rewrite.
    assert result.verdict == "pass"
    assert result.severity_max == "major"
    assert any(f.category == "chapter_seam" for f in result.findings)


# ---------------------------------------------------------------------------
# Stitched draft merge — always forces rewrite when any finding exists
# ---------------------------------------------------------------------------


def test_stitched_merge_noop_when_empty() -> None:
    result = _merge_stitched_draft_into_review(_baseline_result(), [], {})
    assert result.verdict == "pass"


def test_any_stitched_finding_forces_rewrite_and_forbids_merge() -> None:
    finding = ChapterReviewFinding(
        severity="critical",
        category="stitched_draft",
        message="检测到拼接稿（事件签名相似度 0.78）",
    )
    result = _merge_stitched_draft_into_review(
        _baseline_result(),
        [finding],
        {
            "stitched_draft_pairs": [{"a_idx": 3, "b_idx": 4, "similarity": 0.78}],
            "stitched_draft_repair_prompt": "【拼接稿修复任务】\n- 段落 #3 与 #4 …",
        },
    )
    assert result.verdict == "rewrite"
    instructions = result.rewrite_instructions or ""
    # Editor must not merge the two drafts
    assert "禁止合并" in instructions
    assert "二选一" in instructions


# ---------------------------------------------------------------------------
# Name canon merge — forbidden_collision is critical, unknown_name is major
# ---------------------------------------------------------------------------


def test_name_canon_unknown_name_only_does_not_force_rewrite() -> None:
    finding = ChapterReviewFinding(
        severity="major",
        category="name_canon",
        message="L42 [unknown_name] 「韩九」 — 未在 character-aliases.yaml 中登记。",
    )
    result = _merge_name_canon_into_review(_baseline_result(), [finding], {})
    assert result.verdict == "pass"
    assert result.severity_max == "major"


def test_name_canon_forbidden_collision_forces_rewrite() -> None:
    finding = ChapterReviewFinding(
        severity="critical",
        category="name_canon",
        message="L107 [forbidden_collision] 「周元」 — 与角色 ['周元青'] 易混。",
    )
    result = _merge_name_canon_into_review(
        _baseline_result(),
        [finding],
        {
            "name_canon_violations": [{"spelling": "周元", "kind": "forbidden_collision"}],
            "name_canon_repair_prompt": "【人名 Canon 违规修复】\n- L107 「周元」 …",
        },
    )
    assert result.verdict == "rewrite"
    instructions = result.rewrite_instructions or ""
    assert "人名 Canon" in instructions
    assert "周元" in instructions


# ---------------------------------------------------------------------------
# Canon loader path resolution
# ---------------------------------------------------------------------------


def test_load_canon_returns_empty_when_no_yaml_exists(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    project = SimpleNamespace(slug="nonexistent-project")
    canon = _load_character_canon_for_project(project)
    assert canon.entries == ()


def test_load_canon_finds_mode_b_path(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    project = SimpleNamespace(slug="my-novel")

    canon_dir = tmp_path / "output" / "ai-generated" / "my-novel" / "story-bible"
    canon_dir.mkdir(parents=True)
    (canon_dir / "character-aliases.yaml").write_text(
        """
characters:
  - canonical: 宁尘
    aliases: [宁尘]
""",
        encoding="utf-8",
    )

    canon = _load_character_canon_for_project(project)
    assert len(canon.entries) == 1
    assert canon.entries[0].canonical == "宁尘"


def test_load_canon_finds_mode_a_path(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    project = SimpleNamespace(slug="mode-a-novel")

    canon_dir = tmp_path / "output" / "mode-a-novel" / "story-bible"
    canon_dir.mkdir(parents=True)
    (canon_dir / "character-aliases.yaml").write_text(
        """
characters:
  - canonical: 林风
    aliases: [林风, 林师弟]
""",
        encoding="utf-8",
    )

    canon = _load_character_canon_for_project(project)
    assert len(canon.entries) == 1
    assert canon.entries[0].canonical == "林风"
    assert "林师弟" in canon.entries[0].aliases
