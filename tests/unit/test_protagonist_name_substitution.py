"""主角名漂移的第二种形态：同姓换名（沈鹊 → 沈髵）。

2026-08-31 真机《攥着残页从渡口骂到寨里》：第 20 章开篇即写「沈髵右手食指…」，
该章「沈鹊」0 次、「沈髵」16 次——整章都改了名。唯一的检测器
`dominant_name_extension` 拦不住：它只认**贴着规范名长出来的**变体
（陈韭 ⊂ 陈韭菜），而这里姓相同、名被整个替换，规范名根本不是它的前缀。
同一个「正文把主角改名了」的病，换一种形态就绕过了整道防线。

两条误伤防线各有真机反例守着：
  · 师父叫沈鸢（99 次、合法角色、在设定里）→ 花名册白名单不许报；
  · 「沈氏」是宗族称谓不是人名 → 宗族/敬称尾字停用表不许报。
"""

import pytest

from bestseller.services.consistency import (
    dominant_name_extension,
    substituted_given_name,
)

pytestmark = pytest.mark.unit

CANON = "沈鹊"
ROSTER = {"沈鹊", "沈鸢", "沈家", "沈爷", "沈姓"}


def _chapter(name: str, times: int) -> str:
    return "。".join(f"{name}抬手按住心口那截残页" for _ in range(times)) + "。"


class TestSubstitutionIsCaught:
    def test_a_chapter_that_renames_the_protagonist_entirely_is_flagged(self):
        """最严重的形态：整章不用规范名，只用错名。"""
        variant, hits, share = substituted_given_name(
            _chapter("沈髵", 16), CANON, known_names=ROSTER
        )
        assert variant == "沈髵"
        assert hits == 16
        assert share == pytest.approx(1.0)

    def test_the_extension_detector_cannot_see_it(self):
        """证明这不是重复造轮子——旧检测器对这个形态确实是瞎的。"""
        assert dominant_name_extension(_chapter("沈髵", 16), CANON) == ("", 0, 0.0)

    def test_mixed_chapter_is_flagged_with_a_partial_share(self):
        text = _chapter("沈髵", 6) + _chapter(CANON, 4)
        variant, hits, share = substituted_given_name(text, CANON, known_names=ROSTER)
        assert variant == "沈髵" and hits == 6
        assert 0.5 < share < 0.7


class TestNoFalsePositives:
    def test_a_real_character_sharing_the_surname_is_not_flagged(self):
        """师父沈鸢在设定里，不能被当成主角的错别字。"""
        text = _chapter("沈鸢", 20) + _chapter(CANON, 5)
        assert substituted_given_name(text, CANON, known_names=ROSTER)[0] == ""

    def test_clan_and_honorific_terms_are_not_names(self):
        """沈氏/沈家/沈爷 同姓同长度且可能不在花名册，仍不是改名。"""
        for term in ("沈氏", "沈家", "沈爷", "沈老", "沈门"):
            assert substituted_given_name(_chapter(term, 9), CANON, known_names=set())[0] == "", term

    def test_a_single_typo_is_below_the_reporting_floor(self):
        text = _chapter(CANON, 20) + "沈髵皱眉。"
        assert substituted_given_name(text, CANON, known_names=ROSTER)[0] == ""

    def test_empty_inputs_are_safe(self):
        assert substituted_given_name("", CANON, known_names=ROSTER) == ("", 0, 0.0)
        assert substituted_given_name(_chapter("沈髵", 9), "", known_names=ROSTER) == ("", 0, 0.0)


class TestWhitelistActuallyDoesSomething:
    def test_without_the_roster_a_real_character_would_be_misreported(self):
        """空转检查：白名单不是摆设——去掉它，合法角色立刻被误报。"""
        text = _chapter("沈鸢", 20) + _chapter(CANON, 5)
        assert substituted_given_name(text, CANON, known_names=set())[0] == "沈鸢"


class TestWiredIntoTheProseCheck:
    def test_the_prose_checker_consumes_the_new_detector(self):
        """接线检查：函数存在 != 被调用。"""
        import inspect

        from bestseller.services import consistency

        src = inspect.getsource(consistency._check_protagonist_name_in_prose)
        assert "substituted_given_name" in src
        call = inspect.getsource(consistency)
        assert "known_names=_known" in call
