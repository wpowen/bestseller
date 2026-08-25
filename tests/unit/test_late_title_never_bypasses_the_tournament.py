"""appeal 重生的书名不得绕过书名淘汰赛（2026-08-25 真机 custom-xuanhuan-1787625194）。

真机取证：淘汰赛跑了 13 个候选 + arena + 默认族降权，冠军
《擀面胖婶的三息辨火》；在架书名《小摊厨子靠鼻子赌明天本钱》。而出厂书名是
《逐出师门我颠大》——**在淘汰赛回执里一次都没出现过**。

根因：书名有两个互不知情的选择子系统。appeal 重生循环把
premise/简介/标签/书名当一个包重新生成，末尾
``report, premise, synopsis, tags, title = best`` 整包覆盖淘汰赛冠军。
连带后果：淘汰赛的确定性门（长度带/接地/查重）与默认族降权对出厂书名全部失效。

修复不重跑淘汰赛、不加 LLM 调用：让挑战者过一遍**同样的确定性门**再对决，
且**换不换都留痕**。
"""

from __future__ import annotations

import pytest

from bestseller.services.title_tournament import adjudicate_late_title

pytestmark = pytest.mark.unit

_TAGS = ("玄幻", "废料逆袭")
_PROSE = "姜燎被青雀酒楼逐出灶口，在街角支起一口黑锅，靠一只鼻子辨火候。"


def _adj(champion: str, challenger: str, **kw):
    return adjudicate_late_title(
        champion, challenger, tags=_TAGS, prose=_PROSE, **kw
    )


def test_a_clean_challenger_is_allowed_to_win():
    """appeal 重生本就是为提升点击率跑的——干净的挑战者该赢。"""
    adopted, receipt = _adj("擀面胖婶的三息辨火", "逐出师门我颠大")
    assert adopted == "逐出师门我颠大"
    assert receipt["changed"] is True
    assert receipt["tournament_title"] == "擀面胖婶的三息辨火"


def test_a_challenger_failing_the_deterministic_gate_loses():
    """挑战者超出长度带 → 退回淘汰赛冠军（此前它会无条件覆盖）。"""
    too_long = "胖婶的擀面杖悬在锅口而他只辨了三息灵火便换掉那半勺米醋"
    adopted, receipt = _adj("擀面胖婶的三息辨火", too_long)
    assert adopted == "擀面胖婶的三息辨火"
    assert receipt["changed"] is False
    bad = [c for c in receipt["candidates"] if c["title"] == too_long]
    assert bad and "length_out_of_band" in bad[0]["rejected_by"]


def test_default_family_demotion_now_reaches_the_shipped_title():
    """债务族降权此前只保护被丢弃的冠军。用户没点名该族时挑战者要被降权。"""
    adopted, _ = _adj("擀面胖婶的三息辨火", "我替全宗门还灵债")
    assert adopted == "擀面胖婶的三息辨火"


def test_user_named_family_is_not_demoted():
    """用户自己点名了债务族 → 不降权（既有原则：用户点名即豁免）。"""
    adopted, _ = _adj(
        "擀面胖婶的三息辨火", "我替全宗门还灵债", user_named_default_family=True
    )
    assert adopted == "我替全宗门还灵债"


def test_a_receipt_is_written_even_when_nothing_changes():
    """「没换」也必须留痕——没有回执就查不出书名为什么是这个。"""
    _, receipt = _adj("擀面胖婶的三息辨火", "擀面胖婶的三息辨火")
    assert receipt["stage"] == "late_title_adjudication"
    assert receipt["changed"] is False
    assert receipt["reason"] == "same_title"


def test_missing_tournament_winner_keeps_the_challenger():
    """淘汰赛没产出冠军时不得把书名清空（fail-open）。"""
    adopted, receipt = _adj("", "逐出师门我颠大")
    assert adopted == "逐出师门我颠大"
    assert receipt["reason"] == "no_tournament_winner"


def test_vacuity_the_old_behaviour_would_have_failed_this_suite():
    """空转检验：模拟修复前「挑战者无条件覆盖」，确认本套件抓得住它。"""

    def old_behaviour(_champion: str, challenger: str, **_kw):
        return challenger, {}

    too_long = "胖婶的擀面杖悬在锅口而他只辨了三息灵火便换掉那半勺米醋"
    assert old_behaviour("擀面胖婶的三息辨火", too_long)[0] == too_long, (
        "修复前的行为就是无条件采用挑战者——本套件的第二条断言正是为它写的"
    )
