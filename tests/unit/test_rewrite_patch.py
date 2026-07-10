from __future__ import annotations

import hashlib
from dataclasses import replace

import pytest

from bestseller.services.rewrite_patch import (
    ProtectedSpan,
    RewriteEdit,
    RewritePatch,
    apply_rewrite_patch,
)

pytestmark = pytest.mark.unit


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_applies_exact_hash_and_anchor_matched_patch() -> None:
    prefix = "门外风急。雨脚扫过长街。檐角积水坠成一线。"
    suffix = "。灯芯忽然一暗。院墙外又响了一声梆子。"
    parent = f"{prefix}林烬握住刀柄{suffix}"
    target = "林烬握住刀柄"
    start = parent.index(target)
    end = start + len(target)
    patch = RewritePatch(
        parent_hash=_hash(parent),
        edits=(
            RewriteEdit(
                start=start,
                end=end,
                target_hash=_hash(target),
                anchor_before="成一线。",
                anchor_after="。灯芯",
                replacement="林烬拇指顶开刀镡",
            ),
        ),
    )

    result = apply_rewrite_patch(parent, patch)

    assert result.accepted is True
    assert result.candidate_text == f"{prefix}林烬拇指顶开刀镡{suffix}"
    assert result.parent_text == parent
    assert result.parent_hash == _hash(parent)
    assert result.candidate_hash == _hash(result.candidate_text)
    assert result.automatic_promotion_allowed is False
    assert result.failure_codes == ()


def test_applies_multiple_non_overlapping_edits_without_touching_other_text() -> None:
    parent = "甲段保留。坏句一。中段必须原样。坏句二。尾段保留。"
    first = "坏句一"
    second = "坏句二"
    protected_text = "中段必须原样"
    first_start = parent.index(first)
    second_start = parent.index(second)
    protected_start = parent.index(protected_text)
    patch = RewritePatch(
        parent_hash=_hash(parent),
        edits=(
            RewriteEdit(
                start=first_start,
                end=first_start + len(first),
                target_hash=_hash(first),
                anchor_before="保留。",
                anchor_after="。中段",
                replacement="修句一",
            ),
            RewriteEdit(
                start=second_start,
                end=second_start + len(second),
                target_hash=_hash(second),
                anchor_before="原样。",
                anchor_after="。尾段",
                replacement="修句二",
            ),
        ),
        protected_spans=(
            ProtectedSpan(
                start=protected_start,
                end=protected_start + len(protected_text),
                content_hash=_hash(protected_text),
            ),
        ),
    )

    result = apply_rewrite_patch(parent, patch)

    assert result.accepted is True
    assert result.candidate_text == "甲段保留。修句一。中段必须原样。修句二。尾段保留。"
    assert "甲段保留。" in result.candidate_text
    assert "。中段必须原样。" in result.candidate_text
    assert protected_text.encode("utf-8") in result.candidate_text.encode("utf-8")
    assert result.candidate_text.endswith("。尾段保留。")


def test_rejects_overlapping_edits_and_keeps_parent_candidate() -> None:
    parent = "前文。需要局部修理的句子。后文。"
    first = "需要局部修理"
    second = "局部修理的句子"
    first_start = parent.index(first)
    second_start = parent.index(second)
    patch = RewritePatch(
        parent_hash=_hash(parent),
        edits=(
            RewriteEdit(
                start=first_start,
                end=first_start + len(first),
                target_hash=_hash(first),
                anchor_before="前文。",
                anchor_after="的句子",
                replacement="改写一",
            ),
            RewriteEdit(
                start=second_start,
                end=second_start + len(second),
                target_hash=_hash(second),
                anchor_before="需要",
                anchor_after="。后文",
                replacement="改写二",
            ),
        ),
    )

    result = apply_rewrite_patch(parent, patch)

    assert result.accepted is False
    assert result.failure_codes == ("overlapping_edits",)
    assert result.candidate_text == parent
    assert result.candidate_hash == result.parent_hash


def test_rejects_edit_that_touches_a_protected_span() -> None:
    parent = "开头。正典事实：苏晚不能死亡。结尾有坏句。"
    target = "苏晚不能死亡"
    start = parent.index(target)
    protected = ProtectedSpan(
        start=start,
        end=start + len(target),
        content_hash=_hash(target),
    )
    patch = RewritePatch(
        parent_hash=_hash(parent),
        edits=(
            RewriteEdit(
                start=start,
                end=start + len(target),
                target_hash=_hash(target),
                anchor_before="正典事实：",
                anchor_after="。结尾",
                replacement="苏晚在此死亡",
            ),
        ),
        protected_spans=(protected,),
    )

    result = apply_rewrite_patch(parent, patch)

    assert result.accepted is False
    assert result.failure_codes == ("protected_span_touched",)
    assert result.candidate_text.encode("utf-8") == parent.encode("utf-8")


def test_rejects_when_protected_span_hash_no_longer_matches_parent() -> None:
    parent = "开头。正典事实原样保留。结尾坏句。"
    protected_text = "正典事实原样保留"
    protected_start = parent.index(protected_text)
    target = "结尾坏句"
    target_start = parent.index(target)
    patch = RewritePatch(
        parent_hash=_hash(parent),
        edits=(
            RewriteEdit(
                start=target_start,
                end=target_start + len(target),
                target_hash=_hash(target),
                anchor_before="保留。",
                anchor_after="。",
                replacement="结尾落到门响",
            ),
        ),
        protected_spans=(
            ProtectedSpan(
                start=protected_start,
                end=protected_start + len(protected_text),
                content_hash=_hash("已经漂移的正典"),
            ),
        ),
    )

    result = apply_rewrite_patch(parent, patch)

    assert result.accepted is False
    assert result.failure_codes == ("protected_span_mismatch",)
    assert result.candidate_text == parent


def test_rejects_ordinary_patch_when_changed_ratio_exceeds_twenty_five_percent() -> None:
    parent = f"{'A' * 40}bad!!{'Z' * 55}"
    target = "bad!!"
    start = parent.index(target)
    patch = RewritePatch(
        parent_hash=_hash(parent),
        edits=(
            RewriteEdit(
                start=start,
                end=start + len(target),
                target_hash=_hash(target),
                anchor_before="AAAAA",
                anchor_after="ZZZZZ",
                replacement="R" * 30,
            ),
        ),
        max_changed_ratio=1.0,
    )

    result = apply_rewrite_patch(parent, patch)

    assert result.accepted is False
    assert result.failure_codes == ("changed_ratio_exceeded",)
    assert result.changed_ratio == pytest.approx(0.30)
    assert result.candidate_text == parent


def test_rejects_invalid_edit_bounds_before_slicing() -> None:
    parent = "a" * 100
    patch = RewritePatch(
        parent_hash=_hash(parent),
        edits=(
            RewriteEdit(
                start=-1,
                end=2,
                target_hash=_hash(""),
                anchor_before="",
                anchor_after="",
                replacement="x",
            ),
        ),
    )

    result = apply_rewrite_patch(parent, patch)

    assert result.accepted is False
    assert result.failure_codes == ("invalid_edit_bounds",)
    assert result.candidate_text == parent


def test_rejects_edit_without_any_exact_anchor() -> None:
    parent = f"{'a' * 50}bad{'z' * 50}"
    start = parent.index("bad")
    patch = RewritePatch(
        parent_hash=_hash(parent),
        edits=(
            RewriteEdit(
                start=start,
                end=start + 3,
                target_hash=_hash("bad"),
                anchor_before="",
                anchor_after="",
                replacement="fix",
            ),
        ),
    )

    result = apply_rewrite_patch(parent, patch)

    assert result.accepted is False
    assert result.failure_codes == ("missing_edit_anchors",)
    assert result.candidate_text == parent


def test_rejects_empty_patch_as_non_candidate() -> None:
    parent = "父稿必须保留。"

    result = apply_rewrite_patch(
        parent,
        RewritePatch(parent_hash=_hash(parent), edits=()),
    )

    assert result.accepted is False
    assert result.failure_codes == ("empty_patch",)
    assert result.candidate_text == parent


@pytest.mark.parametrize(
    ("mismatch", "expected_code"),
    [
        ("parent", "parent_hash_mismatch"),
        ("target", "target_hash_mismatch"),
        ("anchor", "anchor_mismatch"),
    ],
)
def test_exact_match_failures_are_diagnostic_and_never_replace_parent(
    mismatch: str,
    expected_code: str,
) -> None:
    parent = f"{'前' * 30}目标坏句{'后' * 30}"
    target = "目标坏句"
    start = parent.index(target)
    edit = RewriteEdit(
        start=start,
        end=start + len(target),
        target_hash=_hash(target),
        anchor_before="前前前",
        anchor_after="后后后",
        replacement="目标修句",
    )
    patch = RewritePatch(parent_hash=_hash(parent), edits=(edit,))
    if mismatch == "parent":
        patch = replace(patch, parent_hash=_hash("另一个父稿"))
    elif mismatch == "target":
        patch = replace(patch, edits=(replace(edit, target_hash=_hash("别的目标")),))
    else:
        patch = replace(patch, edits=(replace(edit, anchor_before="错误锚点"),))

    result = apply_rewrite_patch(parent, patch)

    assert result.accepted is False
    assert result.failure_codes == (expected_code,)
    assert result.candidate_text == parent


def test_rewrites_adapter_returns_candidate_without_promotion() -> None:
    from bestseller.services.rewrites import apply_rewrite_patch_candidate

    parent = f"{'前文。' * 12}解释坏句{'后文。' * 12}"
    target = "解释坏句"
    start = parent.index(target)
    patch = RewritePatch(
        parent_hash=_hash(parent),
        edits=(
            RewriteEdit(
                start=start,
                end=start + len(target),
                target_hash=_hash(target),
                anchor_before="前文。",
                anchor_after="后文。",
                replacement="他把信纸推回桌角",
            ),
        ),
    )

    result = apply_rewrite_patch_candidate(parent, patch)

    assert result.accepted is True
    assert result.parent_text == parent
    assert result.candidate_text != parent
    assert result.automatic_promotion_allowed is False
