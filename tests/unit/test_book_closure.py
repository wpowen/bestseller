"""书必须能自己判断「写完了」——这个概念此前在代码里根本不存在。

管线终态是 ``REVISING if requires_human_review else WRITING``，没有任何自动
路径通向 COMPLETED；唯一的写入方是一个手动 web 端点。于是两本三章测试书
（2026-07-26，0 失败工作流、三章全部产出）永远停在 ``revising``。

用户要求明确：**不允许出现等人工确认的分支**。所以判据里没有「待确认」这种
结果——书要么写完了（每章都已尘埃落定），要么没写完（还有章缺失或在途）。
quality_debt 不停书：那是修复循环自己的裁决，导出会把 debt 记在产物上。
"""

from __future__ import annotations

from types import SimpleNamespace as _NS

import pytest

from bestseller.services.book_closure import (
    SETTLED_PRODUCTION_STATES,
    evaluate_book_closure,
)

pytestmark = pytest.mark.unit


def _ch(number: int, state: str):
    return _NS(chapter_number=number, production_state=state)


class TestFinishedBooksComplete:
    def test_all_ok_is_complete_and_clean(self) -> None:
        verdict = evaluate_book_closure(
            [_ch(1, "ok"), _ch(2, "ok"), _ch(3, "ok")], expected_chapters=3
        )
        assert verdict.is_complete
        assert verdict.is_clean
        assert verdict.debt_chapters == ()

    def test_all_quality_debt_is_complete_but_not_clean(self) -> None:
        """THE field case: 两本书三章全 quality_debt，必须能完结。"""

        verdict = evaluate_book_closure(
            [_ch(1, "quality_debt"), _ch(2, "quality_debt"), _ch(3, "quality_debt")],
            expected_chapters=3,
        )
        assert verdict.is_complete
        assert not verdict.is_clean
        assert verdict.debt_chapters == (1, 2, 3)
        assert verdict.reason == "all_chapters_settled_with_debt"

    def test_mixed_ok_and_debt_completes_and_names_the_debt(self) -> None:
        verdict = evaluate_book_closure(
            [_ch(1, "ok"), _ch(2, "quality_debt"), _ch(3, "ok")], expected_chapters=3
        )
        assert verdict.is_complete
        assert verdict.debt_chapters == (2,)

    def test_needs_human_review_does_not_park_the_book(self) -> None:
        """用户要求：不允许存在等人工确认的分支。"""

        verdict = evaluate_book_closure(
            [_ch(1, "needs_human_review")], expected_chapters=1
        )
        assert verdict.is_complete

    def test_repair_exhausted_counts_as_settled(self) -> None:
        verdict = evaluate_book_closure([_ch(1, "repair_exhausted")], expected_chapters=1)
        assert verdict.is_complete


class TestUnfinishedBooksDoNotComplete:
    def test_a_blocked_chapter_keeps_the_book_open(self) -> None:
        verdict = evaluate_book_closure(
            [_ch(1, "ok"), _ch(2, "blocked"), _ch(3, "ok")], expected_chapters=3
        )
        assert not verdict.is_complete
        assert verdict.unsettled_chapters == (2,)
        assert verdict.reason == "chapters_unsettled"

    def test_a_missing_chapter_keeps_the_book_open(self) -> None:
        """计划 5 章只写了 3 章 —— 缺的两章必须被点名。"""

        verdict = evaluate_book_closure(
            [_ch(1, "ok"), _ch(2, "ok"), _ch(3, "ok")], expected_chapters=5
        )
        assert not verdict.is_complete
        assert verdict.unsettled_chapters == (4, 5)

    def test_pending_chapter_keeps_the_book_open(self) -> None:
        verdict = evaluate_book_closure([_ch(1, "pending")], expected_chapters=1)
        assert not verdict.is_complete

    def test_no_chapters_is_not_a_finished_book(self) -> None:
        verdict = evaluate_book_closure([], expected_chapters=3)
        assert not verdict.is_complete
        assert verdict.reason == "no_chapters"

    def test_empty_production_state_keeps_the_book_open(self) -> None:
        verdict = evaluate_book_closure([_ch(1, "")], expected_chapters=1)
        assert not verdict.is_complete


class TestDegradesSafely:
    def test_unknown_target_falls_back_to_the_chapter_rows(self) -> None:
        """target_chapters 缺失不该把书永远困住。"""

        verdict = evaluate_book_closure([_ch(1, "ok"), _ch(2, "ok")], expected_chapters=None)
        assert verdict.is_complete
        assert verdict.expected_chapters == 2

    def test_zero_target_falls_back_to_the_chapter_rows(self) -> None:
        verdict = evaluate_book_closure([_ch(1, "ok")], expected_chapters=0)
        assert verdict.is_complete

    def test_state_matching_ignores_case_and_padding(self) -> None:
        verdict = evaluate_book_closure(
            [_NS(chapter_number=1, production_state="  Quality_Debt ")],
            expected_chapters=1,
        )
        assert verdict.is_complete

    def test_extra_chapters_beyond_target_do_not_break_the_verdict(self) -> None:
        verdict = evaluate_book_closure(
            [_ch(1, "ok"), _ch(2, "ok"), _ch(3, "blocked")], expected_chapters=2
        )
        assert verdict.is_complete, "计划 2 章都好了；第 3 章超出计划不该拖住完结"


class TestNoHumanConfirmationOutcomeExists:
    def test_the_verdict_is_binary(self) -> None:
        """没有第三种「待人工」结果——这是本模块的设计约束。"""

        fields = {
            "is_complete", "reason", "settled_chapters", "expected_chapters",
            "debt_chapters", "unsettled_chapters",
        }
        verdict = evaluate_book_closure([_ch(1, "ok")], expected_chapters=1)
        assert fields <= set(vars(verdict))
        assert isinstance(verdict.is_complete, bool)

    def test_settled_states_never_include_an_in_flight_state(self) -> None:
        for state in ("blocked", "pending", "drafting", ""):
            assert state not in SETTLED_PRODUCTION_STATES
