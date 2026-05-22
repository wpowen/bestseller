from __future__ import annotations

from types import SimpleNamespace

import pytest

from bestseller.services.hook_echo_gate import check_hook_echo, render_hook_echo_block
from bestseller.services.pipelines import _apply_retention_retry_budget
from bestseller.services.quality_gates_config import OriginalityEngineConfig
from bestseller.services.retention_safety_gate import (
    HOOK_ECHO_BLOCK_CODE,
    evaluate_retention_safety,
    stamp_retention_block_codes,
)

pytestmark = pytest.mark.integration


def test_retention_full_loop_blocks_prompts_and_passes_after_echo_repair() -> None:
    prev_chapter = (
        "倒计时已经开始。"
        "下一刻，门外脚步声响起，那份名单还在他怀里。"
        "未完。"
    )
    first_bad_draft = "三日后，清晨。李四走进客栈，要了一壶酒。"
    repaired_draft = (
        "门口的足音越来越近，时间在倒着走。"
        "他翻开账册，第一行名字正是昨夜名单上的人。"
    )
    chapter = SimpleNamespace(metadata_json={}, status="complete", production_state="ok")

    failed = evaluate_retention_safety(
        chapter_position=2,
        chapter_text=first_bad_draft,
        prev_chapter_text=prev_chapter,
        prev_chapter_position=1,
        skip_signature=True,
        skip_exposition=True,
    )
    stamp_retention_block_codes(chapter, failed)
    exhausted = _apply_retention_retry_budget(
        chapter,
        tuple(chapter.metadata_json["auto_repair_last_block_codes"]),
        OriginalityEngineConfig(retention_max_retries=5, retention_escalate_after=3),
    )
    hook_report = check_hook_echo(
        prev_chapter_text=prev_chapter,
        current_chapter_text=first_bad_draft,
        current_chapter_position=2,
        prev_chapter_position=1,
    )
    prompt_block = render_hook_echo_block(hook_report)

    assert exhausted is False
    assert chapter.production_state == "blocked"
    assert HOOK_ECHO_BLOCK_CODE in chapter.metadata_json["auto_repair_last_block_codes"]
    assert "钩子回环" in prompt_block
    assert "倒计时" in prompt_block

    passed = evaluate_retention_safety(
        chapter_position=2,
        chapter_text=repaired_draft,
        prev_chapter_text=prev_chapter,
        prev_chapter_position=1,
        skip_signature=True,
        skip_exposition=True,
    )

    assert passed.passed
    assert passed.auto_repair_codes == ()
