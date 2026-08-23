"""提升状态机把创建回执整块覆盖掉了——恰恰抹在走得最远的那些稿上。

2026-08-24 真机（书 9）：157 份草稿里 13 份的 ``promotion_reason_codes`` 只剩
``["quality_review_started"]`` 或 ``["quality_eligible"]``，**其中 7 份正是当前
在架稿**。它们不是没写过 origin —— 是被两处直接赋值整块覆盖：

  * ``transition_draft_state``：``draft.promotion_reason_codes = list(reason_codes)``
  * ``_promote_selected``：``draft.promotion_reason_codes = ["quality_eligible"]``

后果是这个字段最该说话的时候哑了：被提升的稿丢掉「谁写的、有没有接管」的记录，
所有按 origin 统计的口径系统性少算（我自己一整天的接管计数就被它压低）。

讽刺的是 ``draft_supersession_codes`` 的长注释正是为了「ch20 五个版本上线的是
68 那版而手上有 32 的，**没有一行记录说明为什么**」才写的 —— 记账加上了，
又被下游擦掉。「两件不同的事共用一个字段，后写的赢」。

修：合并而不是覆盖。创建回执（origin/wrote_as_current/supersedes/hold）保留，
状态转移码追加，去重。
"""

from __future__ import annotations

# ruff: noqa: RUF001, RUF002, RUF003 — 中文标点是刻意的。
import pytest

from bestseller.services.draft_promotion import (
    draft_supersession_codes,
    merge_promotion_reason_codes,
)

pytestmark = pytest.mark.unit


def test_a_promoted_draft_still_says_where_it_came_from() -> None:
    """真机形状：一份接管稿被提升后，origin 与 wrote_as_current 必须还在。"""

    created = draft_supersession_codes(
        origin="rewrite", took_current=True, supersedes_version=2
    )
    after_review = merge_promotion_reason_codes(created, ["quality_review_started"])
    after_promotion = merge_promotion_reason_codes(after_review, ["quality_eligible"])

    assert "origin:rewrite" in after_promotion
    assert "wrote_as_current:yes" in after_promotion
    assert "supersedes:v2" in after_promotion
    assert "quality_eligible" in after_promotion


def test_transition_codes_do_not_accumulate() -> None:
    """状态码只保留本次的——否则回执会变成一串互相矛盾的历史状态。"""

    codes = merge_promotion_reason_codes(
        ["origin:chapter_first", "quality_review_started"], ["quality_eligible"]
    )
    assert "quality_review_started" not in codes
    assert codes == ["origin:chapter_first", "quality_eligible"]


def test_no_duplicates() -> None:
    codes = merge_promotion_reason_codes(
        ["origin:rewrite", "quality_review_started"], ["quality_review_started"]
    )
    assert codes.count("quality_review_started") == 1


def test_empty_inputs_are_safe() -> None:
    assert merge_promotion_reason_codes(None, ["x"]) == ["x"]
    assert merge_promotion_reason_codes(["origin:rewrite"], None) == ["origin:rewrite"]
    assert merge_promotion_reason_codes(None, None) == []


def test_hold_reason_survives_too() -> None:
    """被拒的稿也要留下它当时为什么没上架。"""

    created = draft_supersession_codes(
        origin="rewrite", took_current=False, hold_reason="quality_gate_rejected"
    )
    merged = merge_promotion_reason_codes(created, ["quality_review_started"])
    assert "hold:quality_gate_rejected" in merged
    assert "wrote_as_current:no" in merged


def test_both_overwrite_sites_now_merge() -> None:
    """接线断言：两处直接赋值都必须换成合并（行为证据在上面几条）。"""

    import inspect

    from bestseller.services import draft_promotion

    source = inspect.getsource(draft_promotion)
    assert "draft.promotion_reason_codes = list(reason_codes or [])" not in source
    assert 'draft.promotion_reason_codes = ["quality_eligible"]' not in source
    assert source.count("merge_promotion_reason_codes(") >= 3
