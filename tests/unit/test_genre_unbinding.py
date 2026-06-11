"""通用性回归：框架机制不得绑定具体题材内容。

500章跑书暴露的问题：signature mandate 锚词字典是仙侠/探案味、
hook echo 域 token 写死了《青囊镜》一本书的词汇。框架只提供机制，
锚词/域词必须来自书本身（imagery system）。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from bestseller.services.hook_echo_gate import check_hook_echo, extract_hook_tokens
from bestseller.services.imagery_system_design import imagery_anchor_phrases

pytestmark = pytest.mark.unit


def test_extract_hook_tokens_picks_up_book_domain_tokens() -> None:
    text = "他把灵务局工牌拍在桌上。审批红章还没盖下去，门外脚步声响起。"
    tokens = extract_hook_tokens(
        text, extra_domain_tokens=("灵务局工牌", "审批红章", "缺席词")
    )

    assert "灵务局工牌" in tokens
    assert "审批红章" in tokens
    assert "缺席词" not in tokens


def test_book_domain_tokens_rank_before_generic_layers() -> None:
    text = "他握紧灵务局工牌，突然，门外脚步声响起。"
    tokens = extract_hook_tokens(text, extra_domain_tokens=("灵务局工牌",))

    assert tokens[0] == "灵务局工牌"


def test_check_hook_echo_uses_book_domain_tokens_consistently() -> None:
    prev = "章末他把灵务局工牌锁进抽屉。门外脚步声响起，名单还在他怀中。"
    curr = "灵务局工牌在抽屉里震了一下。他打开门，走廊空无一人，名单不见了。"
    report = check_hook_echo(
        prev_chapter_text=prev,
        current_chapter_text=curr,
        current_chapter_position=2,
        prev_chapter_position=1,
        extra_domain_tokens=("灵务局工牌",),
    )

    assert "灵务局工牌" in report.finding.prev_hook_tokens
    assert "灵务局工牌" in report.finding.matched_tokens


def test_imagery_anchor_phrases_extracts_from_artifact() -> None:
    project = SimpleNamespace(
        metadata_json={
            "imagery_system": {
                "theme_core": "编制与修行",
                "images": [
                    {"name": "灵务局工牌", "carrier": "磨损的塑封工牌"},
                    {"name": "审批红章", "carrier": "永远差一个的公章"},
                ],
            }
        }
    )

    phrases = imagery_anchor_phrases(project)

    assert "灵务局工牌" in phrases
    assert "磨损的塑封工牌" in phrases
    assert "审批红章" in phrases


def test_imagery_anchor_phrases_empty_without_artifact() -> None:
    assert imagery_anchor_phrases(SimpleNamespace(metadata_json={})) == ()
    assert imagery_anchor_phrases(SimpleNamespace(metadata_json=None)) == ()
