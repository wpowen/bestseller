from __future__ import annotations

import pytest

from bestseller.services.prompt_compactor import compact_user_prompt

pytestmark = pytest.mark.unit


def test_compactor_dedupes_chapter_contract_digest() -> None:
    raw = (
        'chapter_contract_digest: {"protagonist_choice":"先压镜脚","must_keep":"时间锚"}\n'
        '"chapter_contract_digest": {"protagonist_choice":"先压镜脚","must_keep":"时间锚"}\n'
        "allowed_time_anchors: [\"23:43\"]\n"
        "characters_must_not_appear: [\"小雨\"]"
    )
    compacted, report = compact_user_prompt(raw, chapter_no=1, forbidden_terms_full=[])
    assert report.compacted_chars <= report.original_chars
    assert compacted.count("chapter_contract_digest") <= 2
    assert "allowed_time_anchors" in compacted
    assert "characters_must_not_appear" in compacted


def test_compactor_slices_forbidden_early_leaks() -> None:
    terms = ["玩家", "副本", "困魂镜", "母镜", "源门", "爷爷", "守夜人"]
    raw = (
        "forbidden_early_leaks_archived: "
        "[玩家, 副本, 困魂镜, 母镜, 源门, 爷爷, 守夜人]\n"
        "chapter_contract_digest: keep"
    )
    compacted, _ = compact_user_prompt(raw, chapter_no=1, forbidden_terms_full=terms)
    assert "forbidden_early_leaks_active" in compacted
    assert "困魂镜" in compacted
    assert "爷爷" not in compacted
    assert "守夜人" not in compacted


def test_compactor_wraps_retention_findings_as_repair_hint() -> None:
    raw = "主合同\nretention_gate_last_findings: [OPENING_NO_ANOMALY]\n下一段"
    compacted, _ = compact_user_prompt(raw, chapter_no=1, forbidden_terms_full=[])
    assert "<REPAIR_HINT>" in compacted
    assert "</REPAIR_HINT>" in compacted
