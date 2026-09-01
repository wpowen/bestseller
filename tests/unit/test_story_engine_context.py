from __future__ import annotations

from uuid import uuid4

from pydantic import ValidationError
import pytest

from bestseller.domain.context import StoryEngineCreativeCore
from bestseller.services.context import story_engine_creative_core_from_metadata
from bestseller.services.drafts import (
    _chapter_first_compiler_section_name,
    _soft_trim_user_prompt,
)
from bestseller.services.story_engine import render_story_engine_creative_core_block


def _core() -> dict[str, object]:
    return {
        "engine_artifact_id": str(uuid4()),
        "engine_version": 2,
        "window_artifact_id": str(uuid4()),
        "chapter_number": 4,
        "choice_id": "publish",
        "pre_state": {"pressure": {"category": "exposure", "value": 3}},
        "pre_state_hash": "pre-hash",
        "known_facts": ["档案室今晚封存"],
        "pressure": "对手正在销毁证据",
        "options": [
            {"choice_id": "publish", "label": "立即公开"},
            {"choice_id": "hide", "label": "暂时隐藏"},
        ],
        "chosen_path": "立即公开并保护证人",
        "alternative_costs": ["隐藏会失去最后窗口"],
        "opponent_strategy": "冻结权限并追查证人",
        "due_obligations": ["保护证人"],
        "required_state_changes": [
            {
                "key": "pressure",
                "category": "exposure",
                "before": 3,
                "operator": "set",
                "after": 4,
                "evidence": "公开证据",
                "monotonic": "any",
            }
        ],
        "expected_post_state_hash": "post-hash",
        "projection_hash": "projection-hash",
        "can_drive_generation": True,
    }


def test_creative_core_forbids_future_window_fields() -> None:
    payload = {**_core(), "future_facts": ["第十章真相"]}

    with pytest.raises(ValidationError):
        StoryEngineCreativeCore.model_validate(payload)


def test_context_projection_requires_authority_and_matching_chapter() -> None:
    payload = _core()

    core = story_engine_creative_core_from_metadata(
        chapter_metadata={"story_engine_projection": payload},
        contract_metadata={},
        chapter_number=4,
    )
    assert core is not None
    assert core.choice_id == "publish"

    assert story_engine_creative_core_from_metadata(
        chapter_metadata={"story_engine_projection": payload},
        contract_metadata={},
        chapter_number=5,
    ) is None
    assert story_engine_creative_core_from_metadata(
        chapter_metadata={
            "story_engine_projection": {**payload, "can_drive_generation": False}
        },
        contract_metadata={},
        chapter_number=4,
    ) is None


def test_contract_projection_takes_precedence_over_chapter_cache() -> None:
    chapter_payload = _core()
    contract_payload = {**_core(), "choice_id": "contract-choice"}

    core = story_engine_creative_core_from_metadata(
        chapter_metadata={"story_engine_projection": chapter_payload},
        contract_metadata={"story_engine_projection": contract_payload},
        chapter_number=4,
    )

    assert core is not None
    assert core.choice_id == "contract-choice"


def test_creative_core_renderer_exposes_current_causality_not_future_window() -> None:
    block = render_story_engine_creative_core_block(_core(), language="zh-CN")

    assert "不可裁剪" in block
    assert "publish" in block
    assert "对手正在销毁证据" in block
    assert "保护证人" in block
    assert "future_facts" not in block
    assert "projections" not in block


def test_chapter_first_soft_trim_preserves_creative_core_verbatim() -> None:
    block = render_story_engine_creative_core_block(_core(), language="zh-CN")
    prompt = ("低优先级背景" * 800) + "\n\n" + block + "\n\n【章末收尾钩子】\n必须留下新问题。"

    trimmed = _soft_trim_user_prompt(
        prompt,
        char_budget=600,
        language="zh-CN",
    )

    assert block in trimmed
    assert "publish" in trimmed
    assert "【章末收尾钩子】" in trimmed


def test_chapter_first_compiler_classifies_creative_core_as_required_section() -> None:
    block = render_story_engine_creative_core_block(_core(), language="en")

    assert _chapter_first_compiler_section_name(block, 7) == "creative_core_line"
