"""Prompt assembly layers + private-term banlist (quality remediation)."""

from __future__ import annotations

from pathlib import Path

import pytest

from bestseller.services.prompt_assembly import (
    LAYER_CRAFT_BRIEF,
    LAYER_HARD_CANON,
    LAYER_OPTIONAL,
    LAYER_SCENE_SPEC,
    PRIVATE_BOOK_TERM_BANLIST,
    build_prompt_assembly_report,
    genre_wants_reaction_amplification,
    render_instruction_priority_block,
    section_layer,
)

pytestmark = pytest.mark.unit

_REPO = Path(__file__).resolve().parents[2]
_GENERIC_SRC_GLOBS = (
    "src/bestseller/services/drafts.py",
    "src/bestseller/services/reviews.py",
    "src/bestseller/services/planner.py",
    "src/bestseller/services/hook_echo_gate.py",
    "src/bestseller/services/common_sense_gate.py",
    "src/bestseller/services/commercial_planning_readiness.py",
    "src/bestseller/services/writing_presets.py",
    "src/bestseller/services/brainhole_engine.py",
)


def test_section_layers_map_integrity_and_garnish() -> None:
    assert section_layer("timeline_canon_line") == LAYER_HARD_CANON
    assert section_layer("hype_constraints_line") == LAYER_SCENE_SPEC
    assert section_layer("methodology_line") == LAYER_CRAFT_BRIEF
    assert section_layer("retrieval_section") == LAYER_OPTIONAL
    assert section_layer("unknown_future_key") == LAYER_OPTIONAL


def test_instruction_priority_block_mentions_word_band_and_cold_reader() -> None:
    zh = render_instruction_priority_block(is_en=False)
    en = render_instruction_priority_block(is_en=True)
    assert "字数硬带" in zh
    assert "冷读" in zh
    assert "Word-count hard band" in en
    assert "Cold-reader" in en


def test_assembly_report_tracks_drops() -> None:
    before = {
        "timeline_canon_line": "HARD " * 50,
        "methodology_line": "CRAFT " * 200,
        "retrieval_section": "OPT " * 300,
    }
    after = {
        "timeline_canon_line": before["timeline_canon_line"],
        "methodology_line": "CRAFT short",
        "retrieval_section": "",
    }
    report = build_prompt_assembly_report(before, after, budget_tokens=1000, mode="lean")
    assert report.budget_tokens == 1000
    assert "retrieval_section" in report.dropped_keys
    hard = next(layer for layer in report.layers if layer.layer == LAYER_HARD_CANON)
    assert hard.section_count == 1
    assert hard.kept_tokens > 0


def test_reaction_amplification_genre_gate() -> None:
    assert genre_wants_reaction_amplification("xianxia", "upgrade") is True
    assert genre_wants_reaction_amplification("都市修仙") is True
    assert genre_wants_reaction_amplification("romance", "slow-burn") is False
    assert genre_wants_reaction_amplification("cozy-fantasy") is False


def _strip_python_comments_and_strings(source: str) -> str:
    """Rough strip of # comments and triple/single-quoted strings for ban scans."""
    import re

    no_triples = re.sub(r'("""[\s\S]*?"""|\'\'\'[\s\S]*?\'\'\')', '""', source)
    no_hashes = re.sub(r"(?m)#.*?$", "", no_triples)
    no_sq = re.sub(r"'(?:\\.|[^'\\])*'", "''", no_hashes)
    no_dq = re.sub(r'"(?:\\.|[^"\\])*"', '""', no_sq)
    return no_dq


def test_private_book_terms_absent_from_generic_production_paths() -> None:
    """Regression: private nouns must not re-enter as live code (comments OK)."""
    hits: list[str] = []
    for rel in _GENERIC_SRC_GLOBS:
        path = _REPO / rel
        if not path.exists():
            continue
        live = _strip_python_comments_and_strings(path.read_text(encoding="utf-8"))
        for term in PRIVATE_BOOK_TERM_BANLIST:
            if term in live:
                hits.append(f"{rel}:{term}")
    assert hits == [], f"private terms leaked into live code: {hits}"


def test_writer_system_includes_priority_block() -> None:
    from types import SimpleNamespace

    from bestseller.services.drafts import build_scene_draft_prompts

    project = SimpleNamespace(title="测试书", slug="test-priority", metadata_json={}, genre="都市", sub_genre="职场")
    chapter = SimpleNamespace(chapter_number=1, chapter_goal="开场", title="一", target_word_count=2600)
    scene = SimpleNamespace(
        scene_number=1,
        title="场1",
        participants=["主角"],
        purpose={"story": "冲突", "emotion": "紧"},
        time_label="此刻",
        entry_state={},
        exit_state={},
        scene_type="opening",
        target_word_count=870,
    )
    style = SimpleNamespace(pov_type="third-limited", tone_keywords=["克制"])
    system, _user = build_scene_draft_prompts(project, chapter, scene, style)
    assert "指令优先级" in system
    assert "字数硬带" in system
