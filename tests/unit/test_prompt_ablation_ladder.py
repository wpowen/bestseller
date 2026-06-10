"""Unit tests for the prompt ablation ladder slicer (scripts/)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "prompt_ablation_ladder.py"
spec = importlib.util.spec_from_file_location("prompt_ablation_ladder", _SCRIPT)
mod = importlib.util.module_from_spec(spec)
sys.modules["prompt_ablation_ladder"] = mod
spec.loader.exec_module(mod)


_FAKE_USER = (
    "【章节体量门 — 硬约束】\n正文不少于2000字。\n"
    "【已验证写作计划 — 正文必须严格执行】\n本场写开门。\n"
    "=== 当前场景执行合同（必须写入正文）===\n标志画面：石门。\n"
    "【市场硬约束 — 本章必须满足】\n前200字出钩。\n"
    "### world_settings\n双宫廷设定。\n"
    "【规则系统约束】\n规则一。\n"
    "## 写法方法论指导\n方法论正文。\n"
    "【emotion_engineering】\n压缩释放。\n"
    "【风格锚点】\n冷峻短句。\n"
    "【物料具体化 · 把抽象机制写成具体血肉（落笔前先做这一步）】\n锚到实体。\n"
    "【rhythm_engineering · 节奏锚点契约】\n节奏。\n"
    "项目：《测试书》\n章节：第2章 石门\n"
)


def test_classify_layers() -> None:
    assert mod.classify("【章节体量门 — 硬约束】") == "L0"
    assert mod.classify("### world_settings") == "STORY"
    assert mod.classify("【已验证写作计划 — 正文必须严格执行】") == "PLAN"
    assert mod.classify("【规则系统约束】") == "CONST"
    assert mod.classify("【emotion_engineering】") == "C1-rules"
    assert mod.classify("【风格锚点】") == "C3-style"
    assert mod.classify("【物料具体化 · 把抽象机制写成具体血肉】") == "C2-proven"
    assert mod.classify("【rhythm_engineering · 节奏锚点契约】") == "C4-emotion"


def test_ladder_arms_are_cumulative() -> None:
    arms = mod.make_arms("ladder", "SYS", _FAKE_USER)
    sizes = [len(arms[k][1]) for k in ("L0-bare", "L1-plan", "L2-const", "L3-craft")]
    assert sizes == sorted(sizes), "ladder arms must grow monotonically"
    assert arms["L4-full"][1] == _FAKE_USER
    # bare arm uses the minimal system prompt, others the full one
    assert arms["L0-bare"][0] != "SYS"
    assert arms["L1-plan"][0] == "SYS"
    # bare arm carries story facts but no methodology
    assert "world_settings" in arms["L0-bare"][1]
    assert "写法方法论指导" not in arms["L0-bare"][1]


def test_craft_mode_isolates_subgroups() -> None:
    arms = mod.make_arms("craft", "SYS", _FAKE_USER)
    base = arms["base+proven"][1]
    assert "物料具体化" in base and "风格锚点" not in base
    assert "风格锚点" in arms["+C3-style"][1]
    assert "rhythm_engineering" in arms["+C4-emotion"][1]
    assert "emotion_engineering" in arms["+C1-rules"][1]
    # all-craft must contain every subgroup
    allc = arms["all-craft"][1]
    for marker in ("物料具体化", "风格锚点", "rhythm_engineering", "emotion_engineering"):
        assert marker in allc


def test_meta_lines_forced_into_every_arm() -> None:
    arms = mod.make_arms("ladder", "SYS", _FAKE_USER)
    for key, (_s, u) in arms.items():
        assert "项目：《测试书》" in u, f"meta missing from {key}"
