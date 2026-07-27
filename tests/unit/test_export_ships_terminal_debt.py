"""导出门必须区分「没写完」和「写完了但不完美」。

``collect_publication_blockers`` 要求每一章 ``production_state == "ok"``，任何
一章带 debt 就毙掉**整本书**的导出。但 ``quality_debt`` 是修复循环自己做出的
**终态决定**——「不再修了，发布这份最优稿」。导出门拒绝它，等于用一道门推翻
另一道门刚刚做完的裁决，书就此永远卡在管线里。

真机取证（2026-07-26）：两本三章测试书全链跑通、0 失败工作流，三章全部
``quality_debt``，于是两本都无法导出，项目停在 ``revising``。全仓库只有一个
**手动 API** 能把项目置为 COMPLETED，所以没有人点按钮的话，书永远不会完结。

判据：**修复循环认定的终态可以发布，在途/未写不可以。**
- 可发布（带警告）：ok / quality_debt / repair_exhausted / quality_reviewed /
  needs_human_review —— 都是循环已经停下来的状态
- 仍阻断：blocked（还在循环里）/ pending（还没写）/ 未设置

debt 不是被忽略，而是改走 ``preflight_warnings`` → 落进导出产物的
``metadata_json["warnings"]``，可查可审计。
"""

from __future__ import annotations

from types import SimpleNamespace as _NS

import pytest

from bestseller.services.exports import (
    EXPORT_SHIPPABLE_PRODUCTION_STATES,
    collect_publication_blockers,
)

pytestmark = pytest.mark.unit


def _project(language: str = "zh-CN"):
    return _NS(language=language, slug="t", id="p1")


def _pair(number: int, *, status: str = "revision", production_state: str = "ok",
          content: str = "正文。" * 700):
    chapter = _NS(
        chapter_number=number,
        status=status,
        production_state=production_state,
        target_word_count=2000,
        title=f"第{number}章",
        metadata_json={},
        id=f"ch{number}",
    )
    draft = _NS(
        content_md=content,
        assembled_from_scene_draft_ids=[f"chapter_first_scene:{number}"],
        word_count=len(content),
        version_no=1,
        id=f"d{number}",
        metadata_json={},
    )
    return chapter, draft


def _state_blockers(blockers: list[str]) -> list[str]:
    return [b for b in blockers if "门禁状态" in b or "production_state" in b]


class TestTerminalDebtShips:
    def test_quality_debt_chapter_does_not_block_export(self) -> None:
        """THE field case: 三章全 quality_debt 的书必须能导出。"""

        payloads = [_pair(n, production_state="quality_debt") for n in (1, 2, 3)]
        assert _state_blockers(collect_publication_blockers(_project(), payloads)) == []

    @pytest.mark.parametrize(
        "state", ["ok", "quality_debt", "repair_exhausted", "quality_reviewed"]
    )
    def test_every_terminal_state_is_shippable(self, state: str) -> None:
        payloads = [_pair(1, production_state=state)]
        assert _state_blockers(collect_publication_blockers(_project(), payloads)) == []

    def test_needs_human_review_still_ships(self) -> None:
        """用户要求：不允许出现等人工确认的分支。"""

        payloads = [_pair(1, production_state="needs_human_review")]
        assert _state_blockers(collect_publication_blockers(_project(), payloads)) == []

    def test_mixed_ok_and_debt_ships(self) -> None:
        payloads = [
            _pair(1, production_state="ok"),
            _pair(2, production_state="quality_debt"),
            _pair(3, production_state="ok"),
        ]
        assert _state_blockers(collect_publication_blockers(_project(), payloads)) == []


class TestUnfinishedStillBlocks:
    """「没写完」和「不完美」不是一回事——前者仍必须拦。"""

    def test_blocked_chapter_still_blocks(self) -> None:
        payloads = [_pair(1, production_state="blocked")]
        assert _state_blockers(collect_publication_blockers(_project(), payloads))

    def test_pending_chapter_still_blocks(self) -> None:
        payloads = [_pair(1, production_state="pending")]
        assert _state_blockers(collect_publication_blockers(_project(), payloads))

    def test_unset_production_state_still_blocks(self) -> None:
        payloads = [_pair(1, production_state="")]
        assert _state_blockers(collect_publication_blockers(_project(), payloads))

    def test_one_blocked_chapter_blocks_the_whole_book(self) -> None:
        payloads = [
            _pair(1, production_state="ok"),
            _pair(2, production_state="blocked"),
        ]
        assert _state_blockers(collect_publication_blockers(_project(), payloads))


class TestStatusGateFollowsTheSameRule:
    def test_revision_status_with_debt_is_publishable(self) -> None:
        """status 门原本硬编 ``production_state == "ok"``，必须同步放开。"""

        payloads = [_pair(1, status="revision", production_state="quality_debt")]
        status_blockers = [
            b for b in collect_publication_blockers(_project(), payloads)
            if "状态为" in b or "not publishable" in b
        ]
        assert status_blockers == []

    def test_drafting_status_still_blocks(self) -> None:
        payloads = [_pair(1, status="drafting", production_state="ok")]
        status_blockers = [
            b for b in collect_publication_blockers(_project(), payloads)
            if "状态为" in b or "not publishable" in b
        ]
        assert status_blockers


class TestShippableSetIsPinned:
    def test_in_flight_states_are_never_shippable(self) -> None:
        for state in ("blocked", "pending", ""):
            assert state not in EXPORT_SHIPPABLE_PRODUCTION_STATES, (
                f"{state!r} means the chapter is not finished; shipping it "
                "would publish an interrupted book"
            )

    def test_ok_is_shippable(self) -> None:
        assert "ok" in EXPORT_SHIPPABLE_PRODUCTION_STATES
