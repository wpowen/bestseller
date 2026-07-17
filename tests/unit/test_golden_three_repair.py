"""Guards for the golden-three readiness repair (the required_fix consumer)."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from bestseller.services.golden_three_repair import repair_golden_three_outline


def _chapter(number: int) -> SimpleNamespace:
    return SimpleNamespace(
        chapter_number=number,
        title=f"第{number}章",
        chapter_goal="旧目标",
        opening_situation="旧开场",
        main_conflict="旧冲突",
        hook_description="旧钩子",
        scenes=[
            SimpleNamespace(
                scene_number=1,
                scene_type="development",
                purpose="旧目的",
                entry_state="旧入场",
                exit_state="旧退场",
                hook_requirement="",
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
            "scenes": [{"scene_number": 1, "purpose": "新目的：主动试探"}],
        },
        # chapter 4 in the response must be ignored — repair scope is golden-3
        {"chapter_number": 4, "chapter_goal": "越权改写"},
    ]
    changed, n = _run(chapters, response=json.dumps(revised, ensure_ascii=False))

    assert changed is True and n == 1
    assert chapters[0].chapter_goal == "新目标：沈约主动布局"
    assert chapters[0].main_conflict == "新冲突"
    assert chapters[0].hook_description == "旧钩子"          # untouched field kept
    assert chapters[0].scenes[0].purpose == "新目的：主动试探"
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
