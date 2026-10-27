from types import SimpleNamespace

import pytest

from bestseller.services.drafts import (
    _prompt_safe_forbidden_actions,
    _render_front_chapter_forbidden_terms_block,
)
from bestseller.services.reviews import _render_front10_rewrite_contract_block


@pytest.mark.parametrize("number", [1, 8, 11])
def test_no_front_chapter_content_contract_without_explicit_prohibitions(number):
    chapter = SimpleNamespace(chapter_number=number, metadata_json={}, foreshadowing_actions={})
    assert _render_front_chapter_forbidden_terms_block(chapter) == ""


def test_explicit_prohibition_does_not_require_supernatural_replacement():
    chapter = SimpleNamespace(
        chapter_number=2,
        metadata_json={"object_signal_contract": {"forbidden_signals": ["旋律终止"]}},
        foreshadowing_actions={},
        opening_situation="乐手在剧场排练。",
    )
    writer = _render_front_chapter_forbidden_terms_block(chapter)
    editor = _render_front10_rewrite_contract_block(chapter, None, language="zh-CN")
    assert "1 个精确禁写字符串" in writer
    assert "旋律终止" in editor
    for prompt in (writer, editor):
        assert "血点" not in prompt
        assert "人物本名" not in prompt
        assert "前500字不得" not in prompt
        assert "额外活人" not in prompt


@pytest.mark.parametrize("action", [
    "此次电话只谈排练，不透露演出日期。",
    "不要提前透露祖父的获奖年份。",
    "这次实验不要把试管加热到发烫。",
])
def test_specific_author_prohibition_is_not_expanded_into_category_ban(action):
    assert _prompt_safe_forbidden_actions([action]) == [action]
