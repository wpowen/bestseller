"""结算了的章，它的稿就得算数。

草稿的 ``promotion_state`` 只在**章节评审判定 pass** 时才会变成 ``promoted``：

    if chapter_review_result.verdict == "pass" and chapter_draft is not None:
        await _promote_reviewed_chapter_draft(...)

而 ``quality_debt`` 的章按定义就拿不到 pass——它的意思正是「不再修了，发布这份
最优稿」。于是稿子永远停在 ``candidate``，而全书导出要求 ``promoted``：

    chapters without a promoted draft: 1, 2, 3

真机取证（2026-07-28，urban-power-reversal-1785219308）：全程无人干预跑到
``completed``，三章全部结算，导出仍然是 0，报的就是上面这句。

又是同一个形状：修复循环已经做出「发布这份稿」的裁决，另一套机制却要求一个它
永远拿不到的批准。结算即发布决定，提升只是把这个决定记录下来，不是第二次审批。
所以在**判定完结的同一处**把已结算章的当前稿提升——和导出放在一起，三者不可能
再分家。
"""

from __future__ import annotations

import inspect

import pytest

from bestseller.services import book_closure

pytestmark = pytest.mark.unit


class TestSettlingPromotesItsDrafts:
    def test_closure_promotes_settled_chapter_drafts(self) -> None:
        source = inspect.getsource(book_closure.settle_project_status_on_closure)
        assert "_promote_settled_chapter_drafts" in source, (
            "完结判定必须把已结算章的稿提升——否则导出永远拿不到 promoted"
        )

    def test_promotion_happens_before_export(self) -> None:
        """先提升再导出，否则导出照样报 no promoted draft。"""

        source = inspect.getsource(book_closure.settle_project_status_on_closure)
        assert source.index("_promote_settled_chapter_drafts") < source.index(
            "export_project_markdown"
        )

    def test_it_uses_the_audited_transition(self) -> None:
        """不能直接改字段——提升有审计记录，绕过它等于伪造。"""

        source = inspect.getsource(book_closure._promote_settled_chapter_drafts)
        assert "transition_draft_state" in source
        assert "draft.promotion_state =" not in source


class TestOnlySettledChaptersArePromoted:
    def test_unsettled_chapters_are_not_promoted(self) -> None:
        source = inspect.getsource(book_closure._promote_settled_chapter_drafts)
        assert "SETTLED_PRODUCTION_STATES" in source, (
            "只有终态章可以提升——在途章的稿还会被改写"
        )

    def test_only_the_current_draft_is_promoted(self) -> None:
        source = inspect.getsource(book_closure._promote_settled_chapter_drafts)
        assert "is_current" in source


class TestItWalksTheStateMachine:
    """提升不能跳步：candidate → under_review → eligible → promoted。

    第一版直接 transition 到 promoted，被状态机以
    ``invalid automated promotion transition: candidate -> promoted`` 拒绝——
    而 ``except: continue`` 把这个拒绝吞掉了，于是「零提升」现场没有任何线索。
    """

    def test_it_goes_through_under_review_and_eligible(self) -> None:
        source = inspect.getsource(book_closure._promote_settled_chapter_drafts)
        assert "UNDER_REVIEW" in source and "ELIGIBLE" in source

    def test_failures_are_recorded_not_swallowed(self) -> None:
        source = inspect.getsource(book_closure._promote_settled_chapter_drafts)
        assert "closure_promotion_errors" in source, (
            "吞掉的异常制造了一次纯靠手工探针才找到的盲区"
        )


class TestItNeverBreaksAFinishedBook:
    def test_promotion_failure_is_not_fatal(self) -> None:
        """书确实写完了，一次提升失败不该撤销这个事实。"""

        source = inspect.getsource(book_closure._promote_settled_chapter_drafts)
        assert "except" in source

    def test_an_already_promoted_draft_is_left_alone(self) -> None:
        source = inspect.getsource(book_closure._promote_settled_chapter_drafts)
        assert "PROMOTED" in source
