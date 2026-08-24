"""书名里出现「账/债」，把冠军位让给不带它的候选。

用户 2026-08-24 报「债务这块的问题反反复复一直出现」。跨书量书名：

    《别人借力我替他们还债》   cost_style=minimal    ★ 1 子族
    《灶底师祖逼我翻旧账》     cost_style=standard   ★ 1 子族
    2/6 本书名带债务族，**两条路径各一本**

F4 只降权了简介候选，书名淘汰赛没有这道门——而**书名是读者最先看到的东西**。

关键差异：书名太短，`is_debt_dominated` 不会触发（《别人借力我替他们还债》
只有 1 个子族 1 次命中，够不上「支配」的阈值）。所以书名层的判据是
**「有任何族内命中就算」**：6-12 个字的书名里出现「账/债」，母题就在书的名字上。

形状仍与 F4 / 卡片层一致：
  · 比较式：同池里有干净候选才让位
  · 全池同族 → 原样放行，绝不清空池
  · 用户点名该族 → 完全跳过
  · 不向任何 prompt 写一个该族的词
"""

from __future__ import annotations

from bestseller.services.title_tournament import (
    TitleCandidate,
    demote_default_family_titles,
)


def _c(title: str) -> TitleCandidate:
    return TitleCandidate(title=title, family="测试")


_DIRTY_A = _c("别人借力我替他们还债")
_DIRTY_B = _c("灶底师祖逼我翻旧账")
_CLEAN_A = _c("他快死了而我是那道雷")
_CLEAN_B = _c("二十七米封神")


class TestDemotion:
    def test_debt_title_yields_to_a_clean_one(self) -> None:
        kept, demoted = demote_default_family_titles(
            [_DIRTY_A, _CLEAN_A], user_named_family=False
        )
        assert [c.title for c in kept] == ["他快死了而我是那道雷"]
        assert [c.title for c in demoted] == ["别人借力我替他们还债"]

    def test_short_titles_trigger_on_a_single_hit(self) -> None:
        """书名短，`is_debt_dominated` 够不上；单次命中即算。"""

        from bestseller.services.anti_default_motif import is_debt_dominated

        assert not is_debt_dominated(_DIRTY_A.title)  # 记录：支配判据不适用
        kept, demoted = demote_default_family_titles(
            [_DIRTY_B, _CLEAN_B], user_named_family=False
        )
        assert [c.title for c in demoted] == ["灶底师祖逼我翻旧账"]

    def test_order_preserved(self) -> None:
        kept, _ = demote_default_family_titles(
            [_CLEAN_A, _DIRTY_A, _CLEAN_B], user_named_family=False
        )
        assert [c.title for c in kept] == ["他快死了而我是那道雷", "二十七米封神"]


class TestNeverEmpty:
    def test_all_dirty_ships_unchanged(self) -> None:
        kept, demoted = demote_default_family_titles(
            [_DIRTY_A, _DIRTY_B], user_named_family=False
        )
        assert len(kept) == 2 and not demoted

    def test_empty_input(self) -> None:
        assert demote_default_family_titles([], user_named_family=False) == ([], [])


class TestUserIntent:
    def test_user_named_family_is_untouched(self) -> None:
        kept, demoted = demote_default_family_titles(
            [_DIRTY_A, _CLEAN_A], user_named_family=True
        )
        assert len(kept) == 2 and not demoted


class TestNoFalsePositives:
    def test_clean_titles_are_untouched(self) -> None:
        kept, demoted = demote_default_family_titles(
            [_CLEAN_A, _CLEAN_B], user_named_family=False
        )
        assert len(kept) == 2 and not demoted

    def test_real_titles_from_other_books_stay(self) -> None:
        """真机其余四本的书名必须全部判干净。"""

        for t in ("二十七米封神", "为什么我砸的每片炉灰都还活着", "废丹成神",
                  "他快死了而我是那道雷"):
            kept, demoted = demote_default_family_titles(
                [_c(t), _DIRTY_A], user_named_family=False
            )
            assert [c.title for c in kept] == [t], t


def test_the_tournament_uses_it() -> None:
    from pathlib import Path

    import bestseller.services.conception as mod

    src = Path(mod.__file__).read_text(encoding="utf-8")
    body = src.split("    survivors = [row for row in candidates if row.survives]", 1)
    assert len(body) == 2, "书名淘汰赛的 survivors 选择点变了"
    assert "demote_default_family_titles" in body[0][-1500:] + body[1][:900]
