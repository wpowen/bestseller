# ruff: noqa: RUF001
from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from bestseller.cli.main import app
from bestseller.services.writing_contest import (
    build_writing_contest_brief,
    review_writing_contest_entry,
)

pytestmark = pytest.mark.unit


runner = CliRunner()


def _strong_entry() -> str:
    return (
        "那天晚上九点，医院缴费窗口只剩一盏灯亮着。我攥着母亲塞给我的旧钥匙，"
        "钥匙齿硌得手心疼。她站在我后面，鞋尖沾着从工地带回来的黄土，"
        "一只手按着饭盒，一只手把皱巴巴的缴费单往窗口里推。我当时只觉得她慢，"
        "说话也小声，连护士问第二遍时都要低头看纸。母亲没有回我，她把饭盒打开，"
        "里面是凉透的鸡蛋和一小撮咸菜，热气早散了，只剩铁皮盒子贴着桌面发出轻响。\n\n"
        "我读高中那几年，总嫌她来学校时穿得旧。雨天她送伞到门口，我故意从后门走；"
        "冬天她把棉衣挂在教室外，我嫌袖口有煤灰味。父亲去世后，她在厨房里切菜，"
        "刀背敲着案板，一下比一下轻。我以为那是她不爱说话，后来才知道，她怕我听见她咳。"
        "那把旧钥匙原来不是家门钥匙，是她工棚柜子的钥匙。柜子里放着我的录取通知书、"
        "第一张奖状、还有我初中时嫌丑没有穿的围巾。围巾边角磨白了，她却用塑料袋包了三层。\n\n"
        "直到那天缴费，护士说还差一千二，母亲把饭盒底层掀开，里面压着一卷零钱。"
        "五十、二十、十块，边角被汗泡软，摊在窗口下像一小片潮湿的路。"
        "我听见她说，先给孩子办，别耽误检查。她没有看我，只用拇指一下下抹平纸币。"
        "我忽然想起很多年前，村口下雨，她也是这样抹我作业本上的水印。"
        "那时候我只顾着发脾气，说本子皱了不好交。她说没事，晾一夜就平了。\n\n"
        "检查结束已经快凌晨，走廊里的灯一盏一盏暗下去。母亲把空饭盒扣好，"
        "又从口袋里摸出一张公交卡，说末班车要没了，让我先回学校。"
        "我说她怎么回，她笑了一下，说工友的车就在医院门口。后来我才知道，"
        "那晚根本没有车。她沿着高架下面走了六站路，鞋底磨开一个口子，"
        "第二天还照常去给人家搬水泥。她没把这些告诉我，是隔壁阿姨多年后提起，"
        "说你妈那天回来时裤脚全湿，手里还护着你的检查单，怕雨水把字泡花。\n\n"
        "我那时已经会写很多漂亮句子，却不会问她脚疼不疼。那只鞋后来被她放在门后，"
        "鞋帮裂着，里面塞了一团旧报纸。厨房窗台有风，报纸被吹得沙沙响，"
        "我站在那里很久，第一次觉得自己那些嫌弃都轻得站不住。\n\n"
        "现在我把那只饭盒放在书桌最里面。钥匙还在，齿口已经钝了，摸上去不再疼。"
        "母亲偶尔打电话来，还是先问我吃没吃饭，再说屋里灯坏了、门口路修好了。"
        "我终于明白，她的爱从来没有站到灯下让人看见。它藏在凉掉的鸡蛋里，"
        "藏在煤灰味的棉衣里，也藏在那把旧钥匙里。只是我走了很多年，才学会把门打开。"
    )


def test_writing_contest_brief_builds_high_bar_prompt() -> None:
    brief = build_writing_contest_brief(
        theme="后知后觉的爱",
        track="original_graphic",
        protagonist="我",
        material_seed="母亲的旧钥匙和医院缴费窗口",
    )
    payload = brief.to_dict()

    assert payload["theme"]["prompt"] == "后知后觉的爱"
    assert payload["track"] == "original_graphic"
    assert "最低长度：800个中文字符" in payload["prompt"]
    assert payload["scoring_rubric"]["emotional_restraint"] == 14


def test_writing_contest_review_passes_specific_restrained_entry() -> None:
    report = review_writing_contest_entry(_strong_entry(), theme="belated-love")

    assert report.passed is True
    assert report.score >= 86
    assert report.cjk_chars >= 800
    assert any("物件" in strength for strength in report.strengths)


def test_writing_contest_review_blocks_slogan_summary() -> None:
    weak = "每一种人生都值得被看见。我想写母亲，母亲很伟大很温暖很治愈。总而言之，我很感动。"
    report = review_writing_contest_entry(weak, theme="belated-love")
    codes = {finding.code for finding in report.findings}

    assert report.passed is False
    assert "length_below_contest_floor" in codes
    assert "slogan_language_detected" in codes


def test_writing_contest_ai_track_requires_disclosure() -> None:
    report = review_writing_contest_entry(
        _strong_entry(),
        theme="belated-love",
        track="ai_literary_video",
    )
    codes = {finding.code for finding in report.findings}

    assert report.passed is False
    assert "ai_track_disclosure_missing" in codes


def test_writing_contest_cli_review_json(tmp_path) -> None:
    entry = tmp_path / "entry.txt"
    entry.write_text(_strong_entry(), encoding="utf-8")

    result = runner.invoke(
        app,
        ["writing-contest", "review", str(entry), "--json", "--no-fail"],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["passed"] is True
    assert payload["theme"] == "后知后觉的爱"
