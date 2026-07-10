from __future__ import annotations

import pytest

from bestseller.services.chapter_quality_bundle import (
    ChapterQualityBundleContext,
    run_chapter_quality_bundle,
)
from bestseller.services.chapter_length_gate import (
    CHAPTER_LENGTH_BLOCK_HIGH_CODE,
    count_zh_chars,
)

pytestmark = pytest.mark.unit


def _varied_body(n: int) -> str:
    # Distinct numbered sentences → predictable length, isolates the length gate.
    return "# 第9章\n\n" + "".join(
        f"井口第{i}次泛起水光，编号{i}的证物袋被压进泥里，灯影一层层晃开。\n\n"
        for i in range(n)
    )


def test_bundle_length_block_aligns_with_product_ceiling() -> None:
    # 2026-06-21: the bundle's hard ceiling was target-relative (1.2×) — only
    # 3120 for a 2600-target book, STRICTER than the 3500 product ceiling — so
    # product-compliant chapters in the 3120–3500 CJK band were false-blocked
    # and churned. A chapter in that band must NOT get the length block...
    in_band = _varied_body(120)
    assert 3120 < count_zh_chars(in_band) <= 3500
    codes = {
        f.code
        for f in run_chapter_quality_bundle(
            in_band,
            ChapterQualityBundleContext(chapter_number=9, target_chapter_words=2600),
        ).blocking_findings
    }
    assert CHAPTER_LENGTH_BLOCK_HIGH_CODE not in codes

    # ...while a chapter genuinely over the 3500 product ceiling still blocks.
    over = _varied_body(220)
    assert count_zh_chars(over) > 3500
    codes_over = {
        f.code
        for f in run_chapter_quality_bundle(
            over,
            ChapterQualityBundleContext(chapter_number=9, target_chapter_words=2600),
        ).blocking_findings
    }
    assert CHAPTER_LENGTH_BLOCK_HIGH_CODE in codes_over


def test_chapter_quality_bundle_preserves_multiple_blocking_findings() -> None:
    previous = "# 第70章\n\n这一刻，所有线索都被压回同一条账路上。\n\n旧段落不会重复。"
    current = (
        "# 第75章\n\n"
        "这一刻，所有线索都被压回同一条账路上。\n\n"
        "本章会告诉读者这里是主线钩子。\n\n"
        "短。"
    )

    report = run_chapter_quality_bundle(
        current,
        ChapterQualityBundleContext(
            chapter_number=75,
            previous_chapter_texts=((70, previous),),
            target_chapter_words=2200,
        ),
    )

    codes = {finding.code for finding in report.blocking_findings}
    assert "CHAPTER_OPENING_REPETITION" in codes
    assert "ANTI_META_LEAK" in codes
    assert "CHAPTER_TOO_SHORT" in codes
    assert report.passed is False


def test_chapter_quality_bundle_preserves_over_max_length_code() -> None:
    text = "# 第2章\n\n" + ("陆沉把申诉表压在窗口前，旧楼灯影一层层晃下来。" * 260)

    report = run_chapter_quality_bundle(
        text,
        ChapterQualityBundleContext(chapter_number=2, target_chapter_words=2000),
    )

    codes = {finding.code for finding in report.blocking_findings}
    assert CHAPTER_LENGTH_BLOCK_HIGH_CODE in codes
    assert "CHAPTER_TOO_SHORT" not in codes
    finding = next(
        item for item in report.blocking_findings if item.code == CHAPTER_LENGTH_BLOCK_HIGH_CODE
    )
    assert finding.repair_scope == "chapter"


def test_chapter_quality_bundle_clean_control_passes() -> None:
    beats = [
        "井沿水泥下露出半枚镜片，边缘被压裂成蛛网状。林渊用证物袋托起碎片，苏婉宁的手电压低半寸，光正好照出背面的新指纹。",
        "巡夜员说昨晚十点以后没人进库。苏婉宁没有反驳，只把门轴上的灰线刮进纸包，问他这条新断口为什么还带着潮气。",
        "林渊听见井壁里有一声很轻的滴答。他贴近砖缝，指尖摸到一截红线，线头被人烧黑，像是仓促切断的引信。",
        "“别碰水。”苏婉宁拦住他，把照片翻到背面。照片角落的雨棚倒影里，多出一只戴玉扳指的手。",
        "两名保安互相看了一眼，谁都没说话。林渊却注意到年长那人的袖口沾着药粉，味道和井口的碱灰一模一样。",
        "仓库灯忽然跳灭。黑暗只压下来一秒，林渊已经把镜片扣回证物袋，另一只手按住井绳，绳子正在被下面的人往回收。",
        "苏婉宁开枪打断绳结，枪声撞在铁皮顶棚上。井下有人闷哼，随即把一只湿透的账夹推上来，封皮写着旧医院的缩写。",
        "林渊没有立刻翻账。他先看锁扣，锁舌内侧嵌着一粒玻璃砂，说明账夹曾被放进更大的密封箱里，再被人匆忙撬开。",
        "“他们不是来偷账。”林渊说。苏婉宁接住他的目光：“他们是来确认我们会先看到哪一页。”",
        "账夹第一页夹着半张化验单，患者姓名被刮掉，只剩出生日期。林渊把日期念出来时，保安室里的电话同时响了。",
        "来电没有声音，只有水流。苏婉宁按下录音键，林渊则转身看向井口，刚才断掉的绳子已经被人从下方重新打结。",
        "他把镜片举到灯下，裂纹拼出一个倒置的楼层号。那不是仓库编号，而是旧医院地下二层的手术室门牌。",
        "苏婉宁让所有人后退。林渊却蹲到井沿，闻到砖缝里浮出的福尔马林气味，终于明白为什么账夹外层会发黏。",
        "“下去的人还活着。”他说。井底随即传来三下敲击，间隔精确，像有人在按病历编号求救。",
        "年长保安突然冲向门口。苏婉宁一脚踹翻折叠椅，椅腿卡住他的膝弯，林渊从他掌心抢下一枚沾水的钥匙。",
        "钥匙齿口被磨掉一半，却能套进井旁那只旧药箱。箱盖打开后，里面没有药，只有一卷写满名字的腕带。",
        "林渊数到第七条时停住。腕带上的笔迹和镜片背面的指纹方向一致，写字的人左手受伤，却仍被迫连续登记。",
        "苏婉宁翻出最后一条腕带，颜色比其他都新。上面的日期是今天，姓名栏空着，像在等他们中某个人补上去。",
        "电话里的水声忽然断了，一个变调的男声贴着听筒说：“别翻第九页。”林渊已经把账夹翻到第九页。",
        "纸面只有一张井道示意图。红笔圈住的位置不是井底，而是他们脚下的地面，圈旁写着：镜片只是第一把钥匙。",
        "地板随即震了一下。仓库尽头的冷柜自己亮起，温度显示屏从零下十八度跳成一串住院号。",
        "苏婉宁把枪口转过去，林渊却先看见冷柜门缝渗出的水，水里漂着另一片镜片，完整映出门外站着的人影。",
        "那人没有进来，只把一只病历袋塞过门缝。袋口印泥未干，封条上的签名正是三年前已经被注销的主治医师。",
        "林渊拆开封条，里面的纸只有一句话：你们找到井，说明第一个证人已经醒了。苏婉宁的呼吸顿了一拍。",
        "井下再次传来敲击，这次是四下。林渊把腕带、镜片和病历袋排在地上，三件证物同时指向旧医院的夜班名单。",
        "他终于抬头：“现在不是查谁进过仓库，是查谁能让死人重新排班。”门外的人影转身就跑。",
        "苏婉宁追到门口，走廊尽头只剩一串湿脚印。林渊按住她的肩，低声说脚印每隔三步就少一截，跑的人穿着医院拖鞋。",
        "保安室的监控开始倒放。屏幕里，冷柜门在他们进仓库前就开过一次，一个空白腕带被慢慢贴到镜头上。",
        "林渊把空白腕带的编号抄下，和第九页红圈相互对照。两个数字合在一起，正好是旧医院停尸间的备用门密码。",
        "苏婉宁把密码输入药箱底部的暗锁，箱底弹出一枚银色纽扣。纽扣背面刻着林渊父亲的旧警号，刻痕新得像刚从车床上取下。",
        "林渊攥紧纽扣，指节泛白，却没有停顿。他把纽扣放进镜片中央，裂纹投出的影子正好连成一条通往停尸间的路线。",
        "仓库外传来急刹声。苏婉宁拉开卷帘门，雨里停着一辆没有牌照的救护车，车厢灯亮着，担架上盖着他们刚找到的第七条腕带。",
        "救护车司机趴在方向盘上，后颈贴着冷藏标签。林渊拉开车门，标签下方渗出一点蓝墨，写的是井下敲击的节奏。",
        "苏婉宁检查车厢，担架扣带还在轻微晃动，说明有人刚刚离开。她从扣带夹缝里挑出一粒白色药片，药片边缘被咬掉半圈。",
        "林渊把药片放到腕带旁边，三样证物终于连成同一条线：有人在仓库、井下和救护车之间转移活人，还故意让他们看见每一步。",
        "远处警笛声越来越近，井底却安静下来。林渊知道那不是结束，而是对方已经换了位置，正在等他们用新密码打开下一扇门。",
    ]
    current = "# 第83章\n\n" + "\n\n".join(beats)

    report = run_chapter_quality_bundle(
        current,
        ChapterQualityBundleContext(chapter_number=83, target_chapter_words=900),
    )

    assert report.blocking_findings == ()
    assert report.passed is True


def test_chapter_quality_bundle_allows_in_story_promise_word() -> None:
    text = (
        "林渊把账夹压在镜框下，听见门外三短一长的敲门声。"
        "张建军抬头说那不是承诺，是他当年亲手写下的欠条。"
        "镜面随即渗出一行血字：今晚必须认账。"
    )

    report = run_chapter_quality_bundle(
        text,
        ChapterQualityBundleContext(chapter_number=2, target_chapter_words=0),
    )

    codes = {finding.code for finding in report.blocking_findings}
    assert "ANTI_META_LEAK" not in codes


def test_chapter_quality_bundle_blocks_common_sense_front_chapter_failures() -> None:
    current = (
        "# 第1章\n\n"
        "张建军把配送单递过来，寄件时间写着23:58。"
        "铜钱发烫。青囊账页发烫。罗盘也发烫。"
        "张建军堵在门口问：“认账是不是要拿命填？我是不是已经认账了？”"
        "他盯着“认账”两个字，像早就懂得这条规矩。"
        "他又念了一遍认账，像早就懂得这条规矩。"
    )

    report = run_chapter_quality_bundle(
        current,
        ChapterQualityBundleContext(chapter_number=1, target_chapter_words=2200),
    )

    codes = {finding.code for finding in report.blocking_findings}
    assert "LATE_NIGHT_DELIVERY_PLAUSIBILITY" in codes
    assert "OBJECT_SIGNAL_OVERUSE" in codes
    assert "LAY_CHARACTER_RULE_KNOWLEDGE_LEAK" in codes
    assert report.passed is False


def test_chapter_quality_bundle_blocks_spliced_draft_artifacts() -> None:
    current = (
        "# 第8章\n\n"
        "倒影里柜门开了一条缝，冷气从里面贴着地面爬出来。\n\n"
        "林渊把证物袋压在水线旁，没有立刻伸手。\n\n"
        "倒影里柜门开了一条缝，冷气从里面贴着地面爬出来。"
    )

    report = run_chapter_quality_bundle(
        current,
        ChapterQualityBundleContext(chapter_number=8, target_chapter_words=0),
    )

    codes = {finding.code for finding in report.blocking_findings}
    assert "CHAPTER_SPLICE_REPEATED_SENTENCE" in codes
    assert report.passed is False
