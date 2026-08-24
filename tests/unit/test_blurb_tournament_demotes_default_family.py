"""上架简介的候选里，默认族的让位给不在族里的——降权，不是杀权。

用户 2026-08-24 报「债务这块的问题反反复复一直出现」。取证：书9 读者实际
看到的三行（书名+简介+一句话）合起来 `is_debt_dominated=True`，3 个子族：

    书名   《别人借力我替他们还债》            1 子族
    简介   「……账本却自己翻到下一页……鞋里夹着欠条」  2 子族  支配
    一句话 「江湖练借力，他练还力；账本自己翻页……讨债人」 3 子族  支配

构思终稿那道门量的是 **premise+synopsis+writing_profile 的整体 blob**，
**从不单独量读者会看到的那几行** —— 而那几行是用户唯一会看到的东西。

文案淘汰赛本来就产出 N 个候选，所以最干净的挂钩点是**选冠军时优先未被支配
的候选**，而不是事后重写。形状与 2026-08-24 卡片层降权一致：

  · 比较式：同池里有不在族里的候选才让位
  · 全池同族 → 原样放行，**绝不清空池**
  · 用户自己点名该族 → 完全跳过，那是用户的选择
  · 不向任何 prompt 写一个该族的词（否定式指令点名母题词＝种词）
"""

from __future__ import annotations

from bestseller.services.blurb_copywriter import (
    BlurbCandidate,
    demote_default_family_candidates,
)

_DIRTY = BlurbCandidate(
    strategy="ledger",
    synopsis="他想收手，账本却自己翻到下一页——门外扔进来一只旧鞋，鞋里夹着欠条。"
    "每还一笔旧账，新债主自动上门，讨债的人排到了门外。",
)
_CLEAN = BlurbCandidate(
    strategy="forge",
    synopsis="他能听见铁器还记得自己被锻打时的形状。每唤醒一件兵器，"
    "它前主人的死法就在他手上重演一次。",
)
_ALSO_CLEAN = BlurbCandidate(
    strategy="thunder",
    synopsis="雷劫生出了嘴，跟他讨价还价。天上那道雷说，你死了我也活不成。",
)


class TestDemotion:
    def test_dominated_candidate_yields_to_a_clean_one(self) -> None:
        kept, demoted = demote_default_family_candidates(
            [_DIRTY, _CLEAN], user_named_family=False
        )
        assert [c.strategy for c in kept] == ["forge"]
        assert [c.strategy for c in demoted] == ["ledger"]

    def test_order_is_preserved_among_survivors(self) -> None:
        kept, _ = demote_default_family_candidates(
            [_CLEAN, _DIRTY, _ALSO_CLEAN], user_named_family=False
        )
        assert [c.strategy for c in kept] == ["forge", "thunder"]


class TestNeverEmpty:
    def test_all_dominated_ships_unchanged(self) -> None:
        """全池同族照常放行——清空池比留下同族更坏。"""

        dirty2 = BlurbCandidate(strategy="ledger2", synopsis=_DIRTY.synopsis)
        kept, demoted = demote_default_family_candidates(
            [_DIRTY, dirty2], user_named_family=False
        )
        assert len(kept) == 2 and not demoted

    def test_empty_input_is_safe(self) -> None:
        assert demote_default_family_candidates([], user_named_family=False) == ([], [])


class TestUserIntent:
    def test_user_named_family_is_never_demoted(self) -> None:
        """用户自己要写债务题材 = 用户的选择，框架不许替他改主意。"""

        kept, demoted = demote_default_family_candidates(
            [_DIRTY, _CLEAN], user_named_family=True
        )
        assert len(kept) == 2 and not demoted


class TestAllClean:
    def test_clean_pool_is_untouched(self) -> None:
        kept, demoted = demote_default_family_candidates(
            [_CLEAN, _ALSO_CLEAN], user_named_family=False
        )
        assert len(kept) == 2 and not demoted


def test_the_tournament_uses_it() -> None:
    from pathlib import Path

    import bestseller.services.blurb_copywriter as mod

    src = Path(mod.__file__).read_text(encoding="utf-8")
    body = src.split("    survivors = [", 1)[1][:900]
    assert "demote_default_family_candidates" in body, body[:300]
