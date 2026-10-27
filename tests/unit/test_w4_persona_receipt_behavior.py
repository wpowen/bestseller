"""W4-2/W5 行为级回执验证：**实际执行** retention 合并并写入回执（2026-09-11）。

为什么需要这个文件
------------------
既有的 `test_w4_persona_authority.py` 有两类断言：

1. 对纯函数 `_merge_persona_gate_findings` 的行为断言 —— 真实执行，但没有数据库/回执；
2. `test_pipelines_uses_the_helper_and_records_authority` —— 只检索函数**源码字符串**，
   不实际跑一遍。"接线存在"不等于"回执真的写对了"。

二次复审明确指出：现有接线测试主要检索源码字符串，没有实际执行回执写入，
因此**不能声称实际运行回执已验证**。本文件补的就是这一层。

做法：用假 session + 少量协作方打桩，真实调用
`pipelines._evaluate_retention_safety_after_assembly`，再断言
`chapter.metadata_json["persona_gate_evidence"]` 的**实际内容**。

覆盖的两个关键行为：

* 默认（空关系表）下，即使存在**无关**真实失败，persona 也不得追加修复码，
  回执 `authority == "advisory"`，且 `corroborated_persona_codes` 为空；
* 真实（base）失败**原样保留**，不受 persona 降级影响。
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from bestseller.services import pipelines
from bestseller.services.retention_safety_gate import RetentionGateFinding, RetentionGateReport

pytestmark = pytest.mark.unit


class _FakeSession:
    """只需满足 `await session.flush()`，不触达数据库。"""

    def __init__(self) -> None:
        self.flushed = 0

    async def flush(self) -> None:
        self.flushed += 1


def _project() -> SimpleNamespace:
    return SimpleNamespace(
        slug="receipt-behavior",
        title="回执行为验证",
        genre="仙侠",
        language="zh-CN",
        target_chapters=30,
        target_chapter_words=None,
        metadata_json={},
    )


def _chapter() -> SimpleNamespace:
    return SimpleNamespace(
        chapter_number=3,
        title="血枭",
        target_word_count=2400,
        metadata_json={},
    )


def _draft() -> SimpleNamespace:
    return SimpleNamespace(content_md="他把水烧开，倒进壶里。" * 60, word_count=2000)


def _persona_payload() -> dict:
    """A payload that trips all three persona thresholds."""

    return {
        "chapter_position": 3,
        "per_persona": [],
        "weighted_score": 0.10,
        "abandon_rate": 0.90,
        "high_risk_personas": ["p1"],
        "aggregated_concerns": ["节奏慢"],
        "next_chapter_directives": ["加快"],
        "unmeasured_channels": ["novelty"],
    }


@pytest.fixture
def patched(tmp_path, monkeypatch):
    """Install every collaborator the function needs, return captured state."""

    captured: dict = {}

    async def _no_prev(*_args, **_kwargs):
        return ""

    monkeypatch.setattr(pipelines, "_load_prev_chapter_draft_text", _no_prev, raising=False)

    # 真实失败：一个**与 persona 无关**的独立失败（缺少开头钩子回响）
    base_report = RetentionGateReport(
        chapter_position=3,
        findings=(
            RetentionGateFinding(
                code="HOOK_ECHO_MISSING", severity="critical", detail="opening echoes"
            ),
        ),
        auto_repair_codes=("HOOK_ECHO_MISSING",),
    )

    import bestseller.services.retention_safety_gate as rsg

    def _fake_evaluate_retention(*_args, **_kwargs):
        return base_report

    def _fake_stamp(chapter, report):
        captured["stamped_codes"] = tuple(report.auto_repair_codes)
        captured["stamped_findings"] = tuple(report.findings)
        return True

    monkeypatch.setattr(rsg, "evaluate_retention_safety", _fake_evaluate_retention)
    monkeypatch.setattr(rsg, "stamp_retention_block_codes", _fake_stamp)
    monkeypatch.setattr(pipelines, "_project_story_bible_root", lambda *_a, **_k: None)

    # persona 反馈文件真实存在 → 走完整 persona 分支
    feedback = tmp_path / "after-ch-003.json"
    feedback.write_text(
        json.dumps(_persona_payload(), ensure_ascii=False), encoding="utf-8"
    )

    import bestseller.services.persona_feedback_repository as pfr

    monkeypatch.setattr(
        pfr, "resolve_persona_feedback_path", lambda *a, **k: feedback, raising=False
    )
    return captured


@pytest.mark.asyncio
async def test_receipt_records_advisory_and_preserves_real_failure(patched):
    """实际跑一遍：无关真实失败不得让 persona 取得修复权。"""

    session = _FakeSession()
    chapter = _chapter()
    await pipelines._evaluate_retention_safety_after_assembly(
        session,
        project=_project(),
        chapter=chapter,
        chapter_draft=_draft(),
        chapter_number=3,
        output_base_dir="/tmp/whatever",
    )

    # 回执确实被写入（不是只写了空的）
    evidence = chapter.metadata_json.get("persona_gate_evidence")
    assert evidence is not None, "persona 回执必须落盘"
    assert evidence["would_have_blocked"] is True, "persona 确实失败了，应如实记录"

    # 默认空关系表 → advisory，且没有任何 persona 项被互证
    assert evidence["authority"] == "advisory"
    assert evidence["corroborated_persona_codes"] == []
    assert evidence["corroborating_base_codes"] == []
    assert evidence["advisory_reason"] == "synthetic_persona_alone_cannot_block"

    # 真实失败原样保留，且**没有**混入任何 persona 修复码
    assert "HOOK_ECHO_MISSING" in patched["stamped_codes"]
    assert not [
        c for c in patched["stamped_codes"] if c.startswith("PERSONA_")
    ], "无关真实失败不得追加 persona 修复义务"

    # persona 关切仍然可见（降级≠丢弃）
    persona_severities = {
        f.code: f.severity
        for f in patched["stamped_findings"]
        if f.code.startswith("PERSONA_")
    }
    assert persona_severities, "persona finding 必须留痕"
    assert set(persona_severities.values()) == {"advisory"}


@pytest.mark.asyncio
async def test_receipt_marks_unmeasured_channels(patched):
    """W4-3 的未实测通道也必须出现在真实回执里。"""

    chapter = _chapter()
    await pipelines._evaluate_retention_safety_after_assembly(
        _FakeSession(),
        project=_project(),
        chapter=chapter,
        chapter_draft=_draft(),
        chapter_number=3,
        output_base_dir="/tmp/whatever",
    )
    evidence = chapter.metadata_json["persona_gate_evidence"]
    assert evidence["unmeasured_channels"] == ["novelty"]
