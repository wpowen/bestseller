"""Tests for the opening golden-chapter gate (黄金一章验收)."""

from __future__ import annotations

import pytest

from bestseller.services.gate_registry import (
    advanced_gate_names,
    core_gate_names,
    gate_continuation_impact,
)
from bestseller.services.opening_golden_chapter_gate import (
    NEW_TERM_THRESHOLD_CH1,
    check_opening_golden_chapter_gate,
)

pytestmark = pytest.mark.unit

# Real ch1 excerpt from 《神仙都是我招的》
# (output/zhaoshen-hr-v3-1781180702/chapter-001.md) — a known
# information-overload opening: 《》 titles, 【】 system panels and a
# dense fresh-name roster all land inside the first chapter.
ZHAOSHEN_CH1_EXCERPT = """\
# 第1章：哮天犬要辞职

陈屿的笔尖还没落下去，前台先响了一声。

不是人。是爪子。

一只两米长的黑犬端坐三垣前台，黑亮的人政部工牌挂在颈圈上晃荡，一条前腿压着一张《转编申请》。红戳盖得歪，理由栏八个字烫得人睁不开眼：想自由恋爱，不想值班。

陈屿手里的笔停了。

识人面板弹：【真职：神界·二郎神部·镇守灵兽（编制内）。信力挂账：灌江口·杨戬】。

老金从茶水间探出半个脑袋。茶杯盖磕了两下，盖沿淌出一线茶汤。“小陈，你这工单……是给我泡的？”

“给他。”陈屿抬了抬下巴。

哮天犬没动。一只爪垫上沾着黑泥，泥印按在《转编申请》的左下角。面板边缘闪了一行：【信号：黑泥爪印·净尘反应中】。

“按条例，灵兽转编——”陈屿把《天庭人事操作手册》翻到第七条，“二十四小时内必须驳回上报。上报即连带扣除其主人杨戬的镇守信力百分之三十三。”

“等等。”老金把茶杯搁下，凑近两步，“你看他那爪子。”

“看过了。”

“再看。”老金从抽屉里抽出一张旧照片——三垣前台地面三十年前的样子。“净尘是编制内才能调用的；可净尘不带黑泥。黑泥是凡间的土，编制内的爪垫上沾着凡土——那是神狗想在凡间多踩一脚。”

哮天犬尾巴动了。不是摇，是扫。尾巴尖把那张纸压得更服帖。

陈屿把面板往哮天犬身上多照了一层。真实动机那栏原本只浮一行——“想给四百年没回家的主人省一份犬粮信力”。底下还挂着一行注脚：【若驳回上报，犬方将进入“流浪编制”：神界不收，凡间没籍。七十二小时后黑市贩子会在三垣对面那条巷子蹲到这狗的命格，按市价收购——他们管这叫“自由神犬”】。

陈屿盯着那条线看了三秒，喉结滚了一下。

“我不报。”

老金茶杯端到一半悬住：“你说啥？”

“我说我不按第七条驳回上报。我要开新岗。”陈屿把《天庭人事操作手册》合上，拔开笔盖，“驻人间联络犬。”

“你可想清楚。”老金把茶杯盖转了个方向，“这岗上一个干明白的人叫姜子牙——保编制，给自由，信力走主人的账，不占三垣预算。后来那条岗撤了。”

“为什么撤？”

“因为他干得太明白，把自己干成了封神榜。”

陈屿没接话，笔尖顿了一下，又顿了一下，在《岗位备案草拟纸》抬头写下“驻人间联络犬”五个字。

哮天犬抬起了头。那只两米长的黑犬把另一只没沾黑泥的爪垫伸过来，搭在陈屿手背——凉、干燥，比普通犬只重出三倍。

年糕不知什么时候蹲到了陈屿鞋面上，这猫蹿上桌面，爪子按住哮天犬那只沾黑泥的爪垫，替它挪到了签名栏。

“主人说——”年糕蹲稳，拿爪子点着哮天犬那只没按下去的前腿，一字一顿，“母狗都在人间，神界连只贵宾都没有。他要自由恋爱，不想值班。”

哮天犬低头看了一眼年糕的爪，然后用力按了下去。

爪印落进新岗合同的签名栏，金光顺着爪纹一道道亮起来。

哮天犬扭过头，从颈圈底下叼出一个油布小包——藏了三个月的那种藏法，包角磨得发白，油渍渗进布纹。他把小包往陈屿手边一拍。

包里的东西滚了半圈，磕在陈屿工牌缺角上，停住了。

那是一枚天眼形状的旧徽章。铜质，边缘磨得发亮，天眼瞳孔里嵌着一粒米大小的琉璃。

年糕用爪子把徽章拨正：“主人说，等第七个人事来了，把这个交给他。”

陈屿工牌背面那三个字——“第七任”——烫了一下。

他把徽章翻了一面。背面两行字，刻痕比正面旧得多：“持此者，入内务司可免验。——杨戬。”

老金的茶杯盖转了三圈才停。“小陈，你知道这意味着什么？”

“意味着他主人知道我会来。”陈屿把徽章揣进口袋，“也意味着温故的便利贴贴错了地方。”

楼下有车发动的声音。不是普通车——底盘低、排气闷的越野，停在三垣对面那条巷子里已经三天了。

陈屿抬手揉了揉年糕的脑袋，翻开新一页《岗位备案草拟纸》。抬头是早上刚打印的——“江底水文管理员”。温故今天下午推过来的。明天九点前必须交出第一批背调名单。

他搁下笔，从口袋里摸出那张温故甩过来的便利贴。背面空白处有人用圆珠笔多添了一行，字迹潦草得像是刚从某个旧档案袋里抄来的：“第七任，试用期三十天。前六任平均在职：十一天。”

工牌又烫了一下。这一次烫的位置是缺角那一截——像有人在用指尖点他。

年糕竖起耳朵，盯着窗外那条巷子。哮天犬的尾巴停了，金光刚刚亮过的爪印还留在合同签名栏里，底下压着一行极小的注脚——陈屿凑近才看清：【新岗生效后，主人信力账户将自动开启“代偿预扣”，扣完即停】。

陈屿盯着那行字。代偿扣完即停——和第七条的“扣完即停”用的是同一个句式。

窗外那条巷子里，越野车的引擎又轰了一声。

笔帽拧到一半，陈屿听见身后有人叹气。

叹气声很熟——是老金那种把肺里最后一口烟压成线的叹气。他从柜台后面绕出来，手里端着一只搪瓷缸，缸里泡着半截发黑的枸杞。他没看陈屿，看的是桌上那份还差一个签名栏的《驻人间联络犬岗位草案》。

“第七版了。”老金把搪瓷缸搁在台灯底座上，缸底蹭掉一缕灰，“从戌时写到亥时，纸都快被你戳出筛子。”

陈屿手里那支笔帽是拧开的——拧开有两分钟了，笔尖悬在签名栏上方三厘米，不落。老金看得懂这支笔的犹豫，不催。

哮天犬趴在柜台最里侧，下巴搁在前爪上，毛色灰白，左耳缺了一角。它没看合同——它在看陈屿的工牌，工牌背面那行“第七任”三个字，刚好被台灯照出一道毛边。

年糕站在哮天犬左侧，一只通体漆黑的细犬，前腿笔直，像立正。他替哮天犬开口：

“原话：'人，你那支笔再不落，犬的爪印就凉了。凉了就不好按，按歪了算谁的？'”

陈屿把笔帽重新拧上，又拧开。

“让它等。”陈屿说，声音压得很低，“老金，这岗位一旦备案，信力账单走哪条线？”

老金没立刻答。他从柜台下面抽出一本发黄的册子，封皮用橡皮筋勒着：“走『灵兽在编转岗』附则七。但附则七的上一个执行案例，是九百二十年前。”

陈屿没让他翻。“附则七的原文我记得。'灵兽在编转岗，需新设岗位之荐主签字画押，并以信力为押，保新岗三年不空转。'——押多少？”

老金伸出三根手指。

年糕又开口：“原话：'三成信力，三年押期，押完归还。犬问：人，你押得起吗？'”"""

# A deliberately clean golden opening: protagonist in the first
# sentence, immediate dialogue/conflict, almost no fresh proper nouns,
# and a dialogue + action close.
CLEAN_OPENING = (
    "陈屿推开门的时候，柜台上的电话正响。\n\n"
    "他抓起听筒，那头只有呼吸声。“喂？”没人应。\n\n"
    "“别接。”身后有人低声开口。陈屿回头，门口站着一个浑身湿透的男人，"
    "手里攥着半张被雨水泡烂的纸。\n\n"
    "“你是谁？”陈屿问。\n\n"
    "男人把那半张纸拍在柜台上，转身就走。\n\n"
    "“站住——”陈屿追出门去，巷子里只剩雨声。"
)


def _codes(report) -> set[str]:
    return {f.code for f in report.findings}


# ---------------------------------------------------------------------------
# 信息节流 — info overload
# ---------------------------------------------------------------------------


def test_real_ch1_excerpt_detects_info_overload() -> None:
    report = check_opening_golden_chapter_gate(
        ZHAOSHEN_CH1_EXCERPT,
        chapter_position=1,
        protagonist_name="陈屿",
    )
    assert "OPENING_INFO_OVERLOAD" in _codes(report)
    assert report.metrics["new_term_count"] > NEW_TERM_THRESHOLD_CH1
    assert not report.passed
    # 主角前置/期待点 are satisfied by this excerpt — only throttling fails.
    assert "OPENING_PROTAGONIST_LATE" not in _codes(report)
    assert "OPENING_NO_TENSION_SIGNAL" not in _codes(report)


def test_info_overload_finding_is_ch1_only() -> None:
    report = check_opening_golden_chapter_gate(
        ZHAOSHEN_CH1_EXCERPT,
        chapter_position=2,
        protagonist_name="陈屿",
    )
    assert "OPENING_INFO_OVERLOAD" not in _codes(report)
    # The metric is still recorded for ch2/ch3 visibility.
    assert report.metrics["new_term_count"] > NEW_TERM_THRESHOLD_CH1


def test_clean_opening_passes() -> None:
    report = check_opening_golden_chapter_gate(
        CLEAN_OPENING,
        chapter_position=1,
        protagonist_name="陈屿",
    )
    assert report.passed, [f.code for f in report.findings]
    assert report.metrics["new_term_count"] <= NEW_TERM_THRESHOLD_CH1


# ---------------------------------------------------------------------------
# 章末总结体
# ---------------------------------------------------------------------------


def test_ending_summary_tone_detected() -> None:
    text = (
        CLEAN_OPENING
        + "\n\n他终于明白，这一切才刚刚开始。\n\n"
        + "更大的风暴注定在前方，命运的齿轮已经开始转动。"
    )
    report = check_opening_golden_chapter_gate(
        text, chapter_position=1, protagonist_name="陈屿"
    )
    assert "ENDING_SUMMARY_TONE" in _codes(report)
    findings = [f for f in report.findings if f.code == "ENDING_SUMMARY_TONE"]
    assert all(f.severity == "warn" for f in findings)
    assert all(f.evidence for f in findings)


def test_ending_with_action_and_dialogue_has_no_summary_finding() -> None:
    text = (
        CLEAN_OPENING
        + "\n\n门外传来三下敲门声。\n\n“开门。”陈屿握住了门把。"
    )
    report = check_opening_golden_chapter_gate(
        text, chapter_position=1, protagonist_name="陈屿"
    )
    assert "ENDING_SUMMARY_TONE" not in _codes(report)
    assert "ENDING_HOOK_MISSING" not in _codes(report)


def test_lyrical_close_without_dialogue_or_action_flags_hook_missing() -> None:
    text = (
        CLEAN_OPENING
        + "\n\n夜深沉，他的心里一片宁静，岁月温暖而美好，安然睡去。"
    )
    report = check_opening_golden_chapter_gate(
        text, chapter_position=1, protagonist_name="陈屿"
    )
    assert "ENDING_HOOK_MISSING" in _codes(report)
    findings = [f for f in report.findings if f.code == "ENDING_HOOK_MISSING"]
    assert findings[0].severity == "advice"


# ---------------------------------------------------------------------------
# 主角前置 / 期待点 / 开篇禁忌
# ---------------------------------------------------------------------------


def test_protagonist_late_detected_on_ch1_only() -> None:
    filler = "雨下了整整一夜？谁也说不清这场雨什么时候才会停下来。" * 14
    text = filler + "\n\n陈屿终于出现在巷口。\n\n“你来晚了。”"
    assert "陈屿" not in text[:300]
    ch1 = check_opening_golden_chapter_gate(
        text, chapter_position=1, protagonist_name="陈屿"
    )
    assert "OPENING_PROTAGONIST_LATE" in _codes(ch1)
    ch2 = check_opening_golden_chapter_gate(
        text, chapter_position=2, protagonist_name="陈屿"
    )
    assert "OPENING_PROTAGONIST_LATE" not in _codes(ch2)


def test_protagonist_check_skipped_when_name_unknown() -> None:
    filler = "雨下了整整一夜？谁也说不清这场雨什么时候才会停下来。" * 14
    report = check_opening_golden_chapter_gate(
        filler, chapter_position=1, protagonist_name=None
    )
    assert "OPENING_PROTAGONIST_LATE" not in _codes(report)


def test_static_scenery_opening_detected() -> None:
    text = (
        "夜色像一块浸了水的绒布，覆在城市的天际线之上。月光稀薄，"
        "云层低垂，远山的影子沉默地卧在地平线尽头。风很轻，雾气漫过原野，"
        "湖面平静如镜，倒映出黯淡的星空。"
    )
    report = check_opening_golden_chapter_gate(
        text, chapter_position=1, protagonist_name=None
    )
    assert "OPENING_STATIC_SCENERY" in _codes(report)


# ---------------------------------------------------------------------------
# Scope / registry
# ---------------------------------------------------------------------------


def test_non_opening_chapter_passes_immediately() -> None:
    report = check_opening_golden_chapter_gate(
        ZHAOSHEN_CH1_EXCERPT,
        chapter_position=5,
        protagonist_name="陈屿",
    )
    assert report.passed
    assert report.findings == ()
    assert report.metrics == {"skipped": 1}


def test_empty_text_passes() -> None:
    report = check_opening_golden_chapter_gate(
        "", chapter_position=1, protagonist_name="陈屿"
    )
    assert report.passed


def test_checker_report_is_soft_and_never_blocks_write() -> None:
    report = check_opening_golden_chapter_gate(
        ZHAOSHEN_CH1_EXCERPT,
        chapter_position=1,
        protagonist_name="陈屿",
    ).to_checker_report()
    assert report.agent == "opening-golden-chapter-gate"
    assert not report.blocks_write
    assert all(issue.can_override for issue in report.issues)
    assert all(issue.severity in {"medium", "low"} for issue in report.issues)


def test_gate_is_registered_as_advanced_and_local() -> None:
    assert "opening_golden_chapter_gate" in advanced_gate_names()
    assert "opening_golden_chapter_gate" not in core_gate_names()
    assert gate_continuation_impact("opening_golden_chapter_gate") == "local"
