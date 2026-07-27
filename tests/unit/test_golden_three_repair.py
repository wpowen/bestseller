"""Guards for the golden-three readiness repair (the required_fix consumer)."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from bestseller.services.golden_three_repair import repair_golden_three_outline
from bestseller.services import golden_three_repair as repair_services


def _chapter(number: int) -> SimpleNamespace:
    return SimpleNamespace(
        chapter_number=number,
        title=f"第{number}章",
        chapter_goal="旧目标",
        opening_situation="旧开场",
        main_conflict="旧冲突",
        hook_description="旧钩子",
        metadata_json={
            "methodology_contract": {"loop_position": "旧循环"},
            "causal_contract": {"cause": "旧因果"},
        },
        scenes=[
            SimpleNamespace(
                scene_number=1,
                scene_type="development",
                purpose={"summary": "旧目的"},
                entry_state={"summary": "旧入场"},
                exit_state={"summary": "旧退场"},
                hook_requirement="",
                metadata_json={"methodology_contract": {"step": "旧方法"}},
            )
        ],
    )


_JUDGE_PAYLOAD = {
    "blocking_issues": [
        {
            "code": "PROTAGONIST_AGENCY_VACUUM",
            "evidence": "三章全程被动",
            "required_fix": "至少一章由主角主动谋划并执行行动",
        }
    ]
}


def _run(chapters, payload=_JUDGE_PAYLOAD, response=None, raises=False):
    calls = {"n": 0}

    async def complete_fn(system_prompt: str, user_prompt: str) -> str:
        calls["n"] += 1
        if raises:
            raise RuntimeError("llm down")
        assert "PROTAGONIST_AGENCY_VACUUM" in user_prompt
        assert "主动谋划" in user_prompt
        return response

    changed = asyncio.run(
        repair_golden_three_outline(
            None, None,
            chapters=chapters,
            llm_judge_payload=payload,
            complete_fn=complete_fn,
        )
    )
    return changed, calls["n"]


def test_repair_applies_revised_fields_to_golden_chapters() -> None:
    chapters = [_chapter(1), _chapter(4)]
    revised = [
        {
            "chapter_number": 1,
            "chapter_goal": "新目标：沈约主动布局",
            "main_conflict": "新冲突",
            "methodology_contract": {"loop_position": "新循环"},
            "causal_contract": {"cause": "主角先判断，再由成年人行动"},
            "scenes": [
                {
                    "scene_number": 1,
                    "purpose": {"summary": "新目的：主动试探"},
                    "entry_state": {"summary": "新入场"},
                    "methodology_contract": {"step": "新方法"},
                }
            ],
        },
        # chapter 4 in the response must be ignored — repair scope is golden-3
        {"chapter_number": 4, "chapter_goal": "越权改写"},
    ]
    changed, n = _run(chapters, response=json.dumps(revised, ensure_ascii=False))

    assert changed is True and n == 1
    assert chapters[0].chapter_goal == "新目标：沈约主动布局"
    assert chapters[0].main_conflict == "新冲突"
    assert chapters[0].hook_description == "旧钩子"          # untouched field kept
    assert chapters[0].metadata_json["methodology_contract"] == {
        "loop_position": "新循环"
    }
    assert chapters[0].metadata_json["causal_contract"] == {
        "cause": "主角先判断，再由成年人行动"
    }
    assert chapters[0].scenes[0].purpose == {"summary": "新目的：主动试探"}
    assert chapters[0].scenes[0].entry_state == {"summary": "新入场"}
    assert chapters[0].scenes[0].metadata_json["methodology_contract"] == {
        "step": "新方法"
    }
    assert chapters[1].chapter_goal == "旧目标"               # ch4 untouched


def test_repair_fails_open_on_unparseable_response() -> None:
    chapters = [_chapter(1)]
    changed, _ = _run(chapters, response="抱歉，我需要更多信息")
    assert changed is False
    assert chapters[0].chapter_goal == "旧目标"


def test_repair_fails_open_on_llm_error() -> None:
    chapters = [_chapter(1)]
    changed, _ = _run(chapters, raises=True)
    assert changed is False


def test_repair_skips_when_no_actionable_issues() -> None:
    chapters = [_chapter(1)]
    changed, n = _run(chapters, payload={"blocking_issues": []}, response="[]")
    assert changed is False and n == 0


def test_repair_strips_markdown_fences() -> None:
    chapters = [_chapter(1)]
    body = json.dumps([{"chapter_number": 1, "chapter_goal": "新目标"}], ensure_ascii=False)
    changed, _ = _run(chapters, response=f"```json\n{body}\n```")
    assert changed is True
    assert chapters[0].chapter_goal == "新目标"


def test_repair_normalizes_structurally_damaged_json() -> None:
    chapters = [_chapter(1)]
    damaged = '[{"chapter_number": 1 "chapter_goal": "新目标：忍住反应"}]'

    changed, _ = _run(chapters, response=damaged)

    assert changed is True
    assert chapters[0].chapter_goal == "新目标：忍住反应"


def test_default_llm_request_has_non_empty_fallback(monkeypatch) -> None:
    captured = {}

    async def fake_complete_text(_session, _settings, request):
        captured["request"] = request
        return SimpleNamespace(content="[]")

    monkeypatch.setattr(repair_services, "complete_text", fake_complete_text)
    changed = asyncio.run(
        repair_golden_three_outline(
            None,
            None,
            chapters=[_chapter(1)],
            llm_judge_payload=_JUDGE_PAYLOAD,
            project=SimpleNamespace(id=None),
        )
    )

    assert changed is False
    assert captured["request"].fallback_response == "[]"
