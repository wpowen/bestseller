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


def test_compactor_keeps_full_forbidden_leak_list() -> None:
    """Early-leak terms are book-specific and must NOT be sliced per chapter.

    The old per-chapter slicing intersected against one book's hardcoded
    proper nouns, which emptied the list for every other book and leaked the
    青囊 cast across projects. ``_terms_for_chapter`` now intentionally keeps
    the full project-derived list, so the compactor leaves the block intact.
    """
    terms = ["玩家", "副本", "困魂镜", "母镜", "源门", "爷爷", "守夜人"]
    raw = (
        "forbidden_early_leaks_archived: "
        "[玩家, 副本, 困魂镜, 母镜, 源门, 爷爷, 守夜人]\n"
        "chapter_contract_digest: keep"
    )
    compacted, _ = compact_user_prompt(raw, chapter_no=1, forbidden_terms_full=terms)
    # No slicing: every term survives for early chapters (safe, genre-neutral).
    for term in terms:
        assert term in compacted
    assert "chapter_contract_digest: keep" in compacted


def test_compactor_wraps_retention_findings_as_repair_hint() -> None:
    raw = "主合同\nretention_gate_last_findings: [OPENING_NO_ANOMALY]\n下一段"
    compacted, _ = compact_user_prompt(raw, chapter_no=1, forbidden_terms_full=[])
    assert "<REPAIR_HINT>" in compacted
    assert "</REPAIR_HINT>" in compacted


def test_lean_keeps_core_execution_blocks_and_strips_reference_meta() -> None:
    raw = (
        "【本场镜头脚本】 请按以下镜头写连续正文。\n"
        "# 镜头 C001-S01-B1（183-330 字） 位置：废车场 人物：陆衍 看得到的事情：" + "细节。" * 40 + "\n"
        "【方法论 lineage(read only; do not reselect)】 - chapter_role: climax；" + "元数据。" * 60 + "\n"
        "## 蒸馏策略卡(distillation-generic / craft) - 成熟度: review；" + "蒸馏。" * 60 + "\n"
        "【emotion_driven_core · 读者情绪合同】 - 读者情绪承诺：" + "情绪理论。" * 60 + "\n"
        "【public_emotion_methodology · 公共情绪】 - 执行：" + "公共情绪。" * 60 + "\n"
        "=== 写作原理执行约束：事件单元合同 ===\n" + "事件合同。" * 60 + "\n"
        "### scene_templates\n" + "模板参考。" * 60 + "\n"
        "=== 当前场景执行合同（必须写入正文）=== signature_image / cut_point 是硬性义务。\n"
    )
    lean, report = compact_user_prompt(raw, chapter_no=3, forbidden_terms_full=[], lean=True)
    # Reference-only meta-blocks removed
    assert "方法论 lineage" not in lean
    assert "scene_templates" not in lean
    # Core writer execution blocks kept
    assert "蒸馏策略卡" in lean
    assert "emotion_driven_core" in lean
    assert "public_emotion_methodology" in lean
    assert "写作原理执行约束：事件单元合同" in lean
    # Scene-critical content kept
    assert "本场镜头脚本" in lean
    assert "镜头 C001-S01-B1" in lean
    assert "当前场景执行合同" in lean
    assert report.compacted_chars < report.original_chars

    # lean=False preserves the old behavior (no meta stripping)
    full, _ = compact_user_prompt(raw, chapter_no=3, forbidden_terms_full=[], lean=False)
    assert "方法论 lineage" in full


def test_lean_dedupes_verbatim_duplicate_sections() -> None:
    block = "【题材方法论·正文场景】 " + "情绪工程 / 冲突筹码 / 钩子设计 的具体规则。" * 20
    raw = f"【场景目标】写一段。\n{block}\n## 中间分隔\n{block}\n"
    lean, _ = compact_user_prompt(raw, chapter_no=1, forbidden_terms_full=[], lean=True)
    assert lean.count("题材方法论·正文场景") == 1
    assert "场景目标" in lean
