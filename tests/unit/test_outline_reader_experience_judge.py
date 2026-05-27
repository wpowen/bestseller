from __future__ import annotations

import json

import pytest

from bestseller.services import outline_reader_experience_judge
from bestseller.services.llm import LLMCompletionResult
from bestseller.services.prompt_packs import get_prompt_pack
from bestseller.settings import load_settings

pytestmark = pytest.mark.unit


class FakeSession:
    pass


@pytest.mark.asyncio
async def test_reader_experience_judge_includes_methodology(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, str] = {}

    async def fake_complete_text(session, settings, request):
        captured["system"] = request.system_prompt
        captured["user"] = request.user_prompt
        return LLMCompletionResult(
            content=json.dumps(
                {
                    "pass": True,
                    "overall_score": 0.86,
                    "dimension_scores": {
                        "spatial_coherence": 0.86,
                        "information_density": 0.86,
                        "protagonist_call_plausibility": 0.86,
                        "hook_prerequisite_satisfied": 0.86,
                    },
                    "blocking_issues": [],
                    "audit_issues": [],
                    "rewrite_plan": {"scope": "outline"},
                },
                ensure_ascii=False,
            ),
            provider="mock",
            model_name="mock-critic",
        )

    monkeypatch.setattr(outline_reader_experience_judge, "complete_text", fake_complete_text)

    result = await outline_reader_experience_judge.judge_outline_reader_experience(
        FakeSession(),
        load_settings(env={}),
        chapters_payload=[{"chapter_number": 1, "title": "雨夜委托"}],
        pack=get_prompt_pack("suspense-mystery"),
    )

    assert result.passed is True
    assert "评估时参照的方法论" in captured["system"]
    assert "【opening_rules】" in captured["system"]
    assert "黄金十章大纲" in captured["user"]


@pytest.mark.asyncio
async def test_reader_experience_judge_empty_scope_returns_pass() -> None:
    result = await outline_reader_experience_judge.judge_outline_reader_experience(
        FakeSession(),
        load_settings(env={}),
        chapters_payload=[{"chapter_number": 21, "title": "后续"}],
    )

    assert result.passed is True
    assert result.scope == "reader_experience"
    assert result.audit_issues[0].code == "OUT_OF_SCOPE"
