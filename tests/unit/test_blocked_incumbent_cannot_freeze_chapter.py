"""在架稿自己也不合格时，重写必须能顶上去——否则章节被永久冻结。

2026-08-24 真机（验证书 9 第 8 章）挖到的重写循环核心死锁：

  第 8 章有 7 份草稿，**4 次审稿全部评的是 v3**（23:26 / 23:29 / 23:31 / 00:45），
  而 v5 生成于 23:27、v6 于 23:30、v7 于 00:44 —— 重写产出的新稿从来没被看过。

机制：`review_chapter_draft` 加载的是 `is_current` 的那一份；而重写稿要成为
current，条件是它自己**没被质量门 blocked**。三份稿实测：

    v3（在架）blocked — POV_DRIFT
    v5（候选）blocked — POV_DRIFT
    v6（候选）blocked — POV_DRIFT

判据只拿挑战者对绝对标准判，**从不与在架稿比较**。在架稿同样不合格时，任何
重写都顶不上去 → 审稿永远评同一份旧稿 → 每轮发现完全相同 → 重写永不收敛 →
后续所有重写 token 纯浪费。全书 66 份草稿只有 17 份被评分（26%），
一半章节卡在旧版本上（5/6/8/9/10/12/14 章）。

同族教训：2026-07-26「选优只看下限致发布最差稿」——同样是把「候选是否达标」
和「候选是否比在架好」混为一谈。

判据：**绝不让不合格的挑战者顶掉合格的在架稿**（原有保护不动）；但当在架稿
自己也不合格时，不比它差的挑战者应当上位——否则缺陷被永久固化。
"""

from __future__ import annotations

# ruff: noqa: RUF002 — 中文标点是刻意的。
from bestseller.services.reviews import challenger_takes_current


class TestCleanIncumbentIsProtected:
    def test_blocked_challenger_never_replaces_clean_incumbent(self) -> None:
        assert (
            challenger_takes_current(
                challenger_blocked=True,
                incumbent_blocked=False,
                challenger_violations=1,
                incumbent_violations=0,
            )
            is False
        )

    def test_clean_challenger_always_takes_over(self) -> None:
        assert (
            challenger_takes_current(
                challenger_blocked=False,
                incumbent_blocked=False,
                challenger_violations=0,
                incumbent_violations=0,
            )
            is True
        )


class TestFrozenChapterIsUnblocked:
    def test_real_machine_case_both_blocked_equal_violations(self) -> None:
        """书 9 ch8 原样：v3 与 v6 同被 POV_DRIFT 阻断、违规数相同。

        旧行为 = 挑战者永远上不去、章节冻结；新行为 = 让它上位，
        下一轮审稿才有新文本可评。
        """

        assert (
            challenger_takes_current(
                challenger_blocked=True,
                incumbent_blocked=True,
                challenger_violations=1,
                incumbent_violations=1,
            )
            is True
        )

    def test_challenger_with_more_violations_stays_out(self) -> None:
        assert (
            challenger_takes_current(
                challenger_blocked=True,
                incumbent_blocked=True,
                challenger_violations=3,
                incumbent_violations=1,
            )
            is False
        )

    def test_challenger_with_fewer_violations_takes_over(self) -> None:
        assert (
            challenger_takes_current(
                challenger_blocked=True,
                incumbent_blocked=True,
                challenger_violations=1,
                incumbent_violations=4,
            )
            is True
        )

    def test_no_incumbent_means_challenger_takes_over(self) -> None:
        """首稿：没有在架稿可比，新稿必须上位，否则章节永远没有正文。"""

        assert (
            challenger_takes_current(
                challenger_blocked=True,
                incumbent_blocked=None,
                challenger_violations=2,
                incumbent_violations=None,
            )
            is True
        )


class TestWiring:
    def test_rewrite_path_uses_the_comparison(self) -> None:
        import inspect

        from bestseller.services import reviews

        src = inspect.getsource(reviews.rewrite_chapter_from_task)
        assert "challenger_takes_current(" in src
        # 旧的「只看自己是否 blocked」写法必须消失
        assert "is_current=not quality_gate_rejected_current_promotion" not in src
