import json

import pytest

from bookengine.longutil import ask
from bookengine.q1_extract import context
from bookengine.storage import save
from support_minimal_b import CaptureClient
from tools.runtime_support import ProjectError


def test_long_scene_and_following_scene_reach_writer_as_complete_json(tmp_path):
    scenes = [
        {"action": "他把铜锁交还给姐姐。" * 290},
        {"choice": "姐姐把钥匙收进口袋。", "result": "妹妹只带走信。"},
    ]
    chapter = {"number": 8, "scenes": scenes, "closing": "兄妹分别。"}
    sections = context({}, {}, {"number": 1}, chapter, {}, [], [], "")
    client = CaptureClient(tmp_path, ["正文"])
    ask(client, "r2-ch8-draft", "接着写", sections, input_limit=10000)
    outline = client.calls[0]["prompt"].split("## 本章章纲\n")[1].split("\n阶段退场约束：")[0]
    assert len(outline) > 2500
    actual = json.loads(outline)
    assert actual["scenes"] == scenes and actual["closing"] == chapter["closing"]


def test_style_reference_yields_before_scene_and_fact_when_packing(tmp_path):
    sections = [
        ("授权参考的统计风格特征", "雨" * 9000),
        ("本章章纲", '{"scenes":["去仓库"]}'),
        ("本章义务", "把伞还给她。"),
    ]
    client = CaptureClient(tmp_path, ["正文"])
    client.context_limit = 1400
    drops = []
    ask(client, "r2-ch8-draft", "接着写", sections, input_limit=10000, writer_drops=drops)
    prompt = client.calls[0]["prompt"]
    assert drops == ["授权参考的统计风格特征"]
    assert "去仓库" in prompt and "把伞还给她。" in prompt and "统计风格特征" not in prompt


def test_old_plan_comparison_yields_but_actual_result_stays(tmp_path):
    comparison = "上章冲突及写作避重约束"
    sections = [
        ("上章结尾", "钥匙交给妹妹了。"),
        (comparison, "原计划把钥匙交给姐姐。" * 200),
        ("本章章纲", '{"number":4}'),
    ]
    client = CaptureClient(tmp_path, ["正文"])
    drops = []
    ask(client, "r2-ch4-draft", "续写", sections, input_limit=1400, writer_drops=drops)
    prompt = client.calls[0]["prompt"]
    assert drops == [comparison] and comparison not in prompt
    assert "钥匙交给妹妹了。" in prompt and "交给姐姐" not in prompt


@pytest.mark.parametrize("cache", ["none", "step", "response"])
def test_required_writer_materials_too_large_preserve_saved_work(tmp_path, cache):
    client = CaptureClient(tmp_path, [])
    stage = "r2-ch8-draft"
    sections = [
        ("本章章纲", json.dumps({"action": "甲" * 900}, ensure_ascii=False)),
        ("人物当前事实", "伞在妹妹手里。"),
    ]
    if cache == "none":
        with pytest.raises(ProjectError, match="r2-ch8-draft.*本章章纲.*人物当前事实"):
            ask(client, stage, "写", sections, input_limit=900)
        assert client.calls == []
        return
    if cache == "step":
        save(tmp_path / "steps" / (stage + ".md"), "既有正文")
    else:
        client.calls.append({"stage": stage, "seq": 1})
        save(
            tmp_path / "responses/001.json",
            {"choices": [{"message": {"content": "既有正文"}, "finish_reason": "stop"}]},
        )
    count = len(client.calls)
    assert ask(client, stage, "写", sections, input_limit=900) == "既有正文"
    assert len(client.calls) == count
