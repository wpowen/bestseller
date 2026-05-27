from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest


def _load_module():
    path = Path(__file__).resolve().parents[2] / "scripts/_deprecated/qingnang_repair/repair_qingnang_front10_framework_inputs.py"
    spec = importlib.util.spec_from_file_location("repair_qingnang_front10_framework_inputs", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_chapter_arg_parser_limits_repairs_to_selected_front10_chapters() -> None:
    module = _load_module()

    assert module._chapter_numbers_from_arg("2") == (2,)
    assert module._chapter_numbers_from_arg("1-3,7") == (1, 2, 3, 7)
    assert module._chapter_numbers_from_arg(None) == tuple(range(1, 11))

    with pytest.raises(ValueError, match="front-10"):
        module._chapter_numbers_from_arg("11")


def test_chapter_one_forbids_old_opening_shortcuts_and_longline_leaks() -> None:
    module = _load_module()

    chapter = module.FRONT10[1]
    rendered = str(chapter)
    script_text = (
        Path(__file__).resolve().parents[2] / "scripts/_deprecated/qingnang_repair/repair_qingnang_front10_framework_inputs.py"
    ).read_text()

    assert "不得写电话、来电、手机、微信、短信、语音、录音、快递" in script_text
    assert "不得写困魂镜、账页、入账、收账" in script_text
    assert "不得写铜钱发烫、滚烫" in script_text
    assert "账页翻动声" not in rendered
    assert "纸页摩擦声" in rendered
    assert "细黑账线" not in rendered
    assert "细黑线" in rendered
    assert "电话" not in chapter["opening"]
    assert "快递" not in chapter["opening"]
    assert "铜钱发烫" in chapter["object_signal"]["forbidden_signals"]


def test_chapter_two_scene_four_forbids_repeating_door_swallow_climax() -> None:
    module = _load_module()

    scene = module.FRONT10[2]["scenes"][3]

    assert "303门保持半开" in scene["exit"]
    assert "不吞人不合拢" not in scene["exit"]
    assert any("门吞掉" in action for action in scene["forbidden_actions"])
    assert any("重复第1章" in action for action in scene["forbidden_actions"])


def test_chapter_two_removes_delivery_and_coin_shortcut_from_scene_plan() -> None:
    module = _load_module()

    chapter = module.FRONT10[2]
    rendered = str(chapter)
    script_text = (
        Path(__file__).resolve().parents[2] / "scripts/_deprecated/qingnang_repair/repair_qingnang_front10_framework_inputs.py"
    ).read_text()

    assert "老式铜钥匙" in chapter["opening"]
    assert "王建业站在镜子里朝他招手" in chapter["opening"]
    assert "王建业昨晚从门缝塞给他" not in rendered
    assert "王建业昨晚托他明早代寄" not in rendered
    assert "湿纸条" in chapter["object_signal"]["forbidden_signals"]
    assert "湿纸条背面" not in rendered
    assert "湿票据" not in chapter["opening"]
    assert "电话带人入场" in rendered
    assert "不得写电话、来电、手机通知、寄件、快递、外卖、配送、物流、跑腿" in rendered
    assert "配送单" in rendered
    assert "跑腿转交" in rendered
    assert "镜债递刀子" in rendered
    assert "账本找最近的人" in rendered
    assert "先认动作再认因果" in rendered
    assert "铜钱本章不主动触碰张建军或小雨" in rendered
    assert "不得写小雨是外卖员" in rendered
    assert "不得把铜钱按到小雨手腕上" in rendered
    assert "不得把湿纸条按在、贴在或压在小雨手腕上" in rendered
    assert "外套只能挡住门缝镜光几秒" in rendered
    assert "湿纸条贴住门缝时手抖" not in rendered
    assert "小雨手腕影子" not in rendered
    assert "林渊掌心裂纹扩大" in rendered
    assert "林渊铜钱裂纹扩大" not in rendered
    assert "不得让林渊蹲下摸黑水" in rendered
    assert "不得引出陈默、七号入账、代父、入门、归人" in rendered
    assert "不得让张建军离场" in rendered
    assert "不得用电梯脚印、黑泥鞋印、水渍脚印" in rendered
    assert "不得写张家门契、三代以内、血债血偿" in rendered
    assert "不得改成湿纸条、父亲声音、正淳、第七面镜或七人名单" in rendered
    assert '"林正淳",' in script_text
    assert '"困魂镜",' in script_text
    assert '"号入账",' in script_text
    assert "张建军第二个入账" not in script_text
    assert "不得提前说扣账人、母镜、源门、林正淳、林远山、林家辉" in script_text
    assert "ch2短触救人" not in script_text
    assert "ch2只作旧物和代价提示不得主动触碰救人" in script_text


def test_chapter_ten_scene_three_keeps_forbidden_signal_out_of_story_fields() -> None:
    module = _load_module()

    scene = module.FRONT10[10]["scenes"][2]

    assert "铜钱救场" not in scene["purpose"]
    assert "铜钱救场" not in scene["contract"]["stakes"]
    assert "铜钱救场" not in "".join(scene["contract"]["pressure_stack"])
    assert "林渊不动用铜钱处理危机" in scene["purpose"]
    assert "用铜钱救场" not in str(module.FRONT10[10])
