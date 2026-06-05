from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from bestseller.cli.quality_loop import parse_chapter_range
from bestseller.services import artifact_health_audit, causal_attribution, reader_panel_judge
from bestseller.services.llm import LLMCompletionResult
from bestseller.services.quality_attribution_loop import run_quality_attribution_loop
from bestseller.settings import load_settings


class FakeSession:
    pass


def test_quality_loop_cli_parses_chapter_range() -> None:
    assert parse_chapter_range("1-10") == (1, 10)


@pytest.mark.asyncio
async def test_reader_panel_normalizes_strict_feedback_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, str] = {}

    async def fake_complete_text(session: Any, settings: Any, request: Any) -> LLMCompletionResult:
        captured["system"] = request.system_prompt
        return LLMCompletionResult(
            content=json.dumps(
                [
                    {
                        "issue": "第 2 章重复第 1 章的委托结构。",
                        "location": "chapter:2:paragraph:4",
                        "severity": "critical",
                        "evidence": "两章都以陌生委托人雨夜求助开场。",
                        "suggested_attribution_hint": "chapter_outline 缺少差异化场景目标",
                    }
                ],
                ensure_ascii=False,
            ),
            provider="mock",
            model_name="mock-critic",
            llm_run_id=uuid4(),
        )

    monkeypatch.setattr(reader_panel_judge, "complete_text", fake_complete_text)

    feedback = await reader_panel_judge.run_reader_panel(
        FakeSession(),  # type: ignore[arg-type]
        load_settings(env={}),
        {1: "第一章正文", 2: "第二章正文"},
        panel=(reader_panel_judge.DEFAULT_PANEL[0],),
        target_chapter_range=(1, 2),
    )

    assert feedback == [
        {
            "issue": "第 2 章重复第 1 章的委托结构。",
            "location": "chapter:2:paragraph:4",
            "severity": "blocker",
            "role": "普通读者",
            "evidence": "两章都以陌生委托人雨夜求助开场。",
            "suggested_attribution_hint": "chapter_outline 缺少差异化场景目标",
        }
    ]
    # reader-panel prompt rewording (genre-neutral): "不要依赖预设类型" →
    # "不能依赖任何预设类型 / 术语 / 本书问题清单".
    assert "不能依赖任何预设类型" in captured["system"]


@pytest.mark.asyncio
async def test_causal_attribution_returns_artifact_layer_and_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outline = tmp_path / "volume_chapter_outline-120.json"
    outline.write_text("{}", encoding="utf-8")

    async def fake_complete_text(session: Any, settings: Any, request: Any) -> LLMCompletionResult:
        assert "不要建议新增硬规则 gate" in request.system_prompt
        return LLMCompletionResult(
            content=json.dumps(
                [
                    {
                        "issue_id": "issue-1",
                        "root_layer": "chapter_outline",
                        "artifact_path": outline.as_posix(),
                        "missing": "缺少差异化章节目标",
                        "repair_directive": "重写章纲里的场景目标和因果推进。",
                    }
                ],
                ensure_ascii=False,
            ),
            provider="mock",
            model_name="mock-critic",
            llm_run_id=None,
        )

    monkeypatch.setattr(causal_attribution, "complete_text", fake_complete_text)

    records = await causal_attribution.attribute_root_causes(
        FakeSession(),  # type: ignore[arg-type]
        load_settings(env={}),
        [
            {
                "issue": "撞戏",
                "location": "chapter:2",
                "severity": "high",
                "role": "严苛编辑",
                "evidence": "连续两章同一委托结构。",
                "suggested_attribution_hint": "chapter_outline",
            }
        ],
        book_root=tmp_path,
    )

    assert records[0]["root_layer"] == "chapter_outline"
    assert records[0]["artifact_path"] == outline.as_posix()
    assert records[0]["missing"] == "缺少差异化章节目标"


@pytest.mark.asyncio
async def test_artifact_health_audit_flags_template_outline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact = tmp_path / "volume_chapter_outline-120.json"
    artifact.write_text(
        "推进剧情, 制造悬念, 埋下伏笔, 增强冲突, 情绪递进, 揭示秘密。",
        encoding="utf-8",
    )

    async def fake_complete_text(session: Any, settings: Any, request: Any) -> LLMCompletionResult:
        return LLMCompletionResult(
            content=request.fallback_response,
            provider="mock",
            model_name="mock-critic",
            llm_run_id=None,
        )

    monkeypatch.setattr(artifact_health_audit, "complete_text", fake_complete_text)

    health = await artifact_health_audit.audit_artifact_health(
        FakeSession(),  # type: ignore[arg-type]
        load_settings(env={}),
        artifact,
    )

    assert health["is_healthy"] is False
    assert any("模板化套话" in defect for defect in health["defects"])
    assert health["independence_score"] < 0.78


@pytest.mark.asyncio
async def test_quality_attribution_loop_repairs_top_down_order(tmp_path: Path) -> None:
    (tmp_path / "chapter-001.md").write_text("第一章", encoding="utf-8")
    (tmp_path / "chapter-002.md").write_text("第二章", encoding="utf-8")

    calls: list[str] = []

    async def fake_reader_panel(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        if calls:
            return []
        return [
            {
                "issue": "规则突抛",
                "location": "chapter:1",
                "severity": "high",
                "role": "严苛编辑",
                "evidence": "突然出现账线规则。",
                "suggested_attribution_hint": "rule_ledger",
            }
        ]

    async def fake_attributor(*args: Any, **kwargs: Any) -> list[dict[str, str]]:
        return [
            {
                "issue_id": "issue-2",
                "root_layer": "chapter_outline",
                "artifact_path": (tmp_path / "chapter_outline.json").as_posix(),
                "missing": "缺场景差异",
                "repair_directive": "修章纲",
            },
            {
                "issue_id": "issue-1",
                "root_layer": "rule_ledger",
                "artifact_path": (tmp_path / "rule_ledger.json").as_posix(),
                "missing": "缺规则边界",
                "repair_directive": "修规则账本",
            },
        ]

    async def fake_auditor(*args: Any, **kwargs: Any) -> dict[str, Any]:
        path = args[2]
        return {
            "artifact_path": path.as_posix(),
            "is_healthy": False,
            "defects": ["不健康"],
            "fix_directives": ["补足"],
            "independence_score": 0.4,
        }

    async def fake_repairer(
        book_root: Path,
        attribution: dict[str, str],
        health: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        calls.append(attribution["root_layer"])
        return {"root_layer": attribution["root_layer"], "health": health or {}}

    result = await run_quality_attribution_loop(
        FakeSession(),  # type: ignore[arg-type]
        load_settings(env={}),
        tmp_path,
        chapter_range=(1, 2),
        max_iterations=1,
        reader_panel_runner=fake_reader_panel,  # type: ignore[arg-type]
        causal_attributor=fake_attributor,  # type: ignore[arg-type]
        artifact_auditor=fake_auditor,  # type: ignore[arg-type]
        artifact_repairer=fake_repairer,  # type: ignore[arg-type]
        write_reports=False,
    )

    assert result["converged"] is False
    assert calls == ["rule_ledger", "chapter_outline"]
