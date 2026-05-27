"""Language + 7-段式 结构断言.

After the 2026-05-27 prompt-engineering rewrite, `_build_system_prompt`
returns a 7-section markdown prompt (ROLE / CONTEXT / TASK / CONSTRAINTS /
THINKING / OUTPUT / EXAMPLES) instead of a single tagline. These tests guard
against accidental regression to the old single-line format and verify the
language switch still works.
"""

from bestseller.services.summarization import _build_system_prompt as summary_system
from bestseller.services.voice_drift import _build_system_prompt as voice_drift_system


# ── summarization (rolling_summary) ──────────────────────────────────────


def test_summarization_system_prompt_zh_is_seven_section_zh() -> None:
    prompt = summary_system("zh-CN")
    assert prompt.startswith("# ROLE")
    # 中文角色定义
    assert "长篇小说" in prompt and "连贯性主编" in prompt
    # 四段输出契约
    assert "## 角色状态" in prompt
    assert "## 已埋未回收的线索" in prompt
    assert "## 已建立或破坏的规则" in prompt
    assert "## 当前未解钩子" in prompt


def test_summarization_system_prompt_en_is_seven_section_en() -> None:
    prompt = summary_system("en-US")
    assert prompt.startswith("# ROLE")
    # 英文角色定义
    assert "continuity editor" in prompt.lower()
    # 四段输出契约
    assert "## Character State" in prompt
    assert "## Unresolved Foreshadowing" in prompt


def test_summarization_system_prompt_contains_required_sections() -> None:
    """每个 build 输出都必须含 7 段中至少 5 段 markdown header."""
    for lang in ("zh-CN", "en-US"):
        prompt = summary_system(lang)
        required = ["# ROLE", "# CONTEXT", "# TASK", "# CONSTRAINTS", "# OUTPUT FORMAT"]
        for section in required:
            assert section in prompt, f"{lang} prompt missing section: {section}"


# ── voice_drift_check ────────────────────────────────────────────────────


def test_voice_drift_system_prompt_zh_is_seven_section_zh() -> None:
    prompt = voice_drift_system("zh-CN")
    assert prompt.startswith("# ROLE")
    # 中文角色定义
    assert "小说编辑" in prompt and "角色声音" in prompt
    # CoT THINKING 段
    assert "# THINKING" in prompt
    # OUTPUT FORMAT 段
    assert "# OUTPUT FORMAT" in prompt
    assert '"drift_score"' in prompt
    assert '"drifted_dimensions"' in prompt


def test_voice_drift_system_prompt_en_is_seven_section_en() -> None:
    prompt = voice_drift_system("en-US")
    assert prompt.startswith("# ROLE")
    assert "fiction editor" in prompt.lower()
    assert "# THINKING" in prompt
    assert "# OUTPUT FORMAT" in prompt


def test_voice_drift_system_prompt_contains_evidence_requirement() -> None:
    """要求 evidence 字段必须含原文引用 — 防回退到只给 'analysis' 单字段."""
    zh_prompt = voice_drift_system("zh-CN")
    assert "evidence" in zh_prompt.lower()
    assert "原文" in zh_prompt  # quote 必须来自原文


def test_voice_drift_system_prompt_has_score_calibration() -> None:
    """评分纪律必须明示 0.2 / 0.5 / 0.7 三段阈值."""
    zh = voice_drift_system("zh-CN")
    assert "0.3" in zh and "0.5" in zh and "0.7" in zh
