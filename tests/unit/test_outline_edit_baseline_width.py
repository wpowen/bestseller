"""章纲修复底稿不许被截成碎片（2026-08-19 真机定罪）。

修复轮把上一轮章纲作为「edit baseline」传给模型做有界重写，但实现
把每个字符串截断到 48 字——而 chapter_goal/main_conflict 普遍 60-120
字，模型看到的是「半句话+…」的残稿，没法在它上面改，只能整批重编。
真机后果：判官修复三轮分数恒定 0.520、directive 恒定 8 条，最后带
质量债放行（未解决项含主角为剧情降智、关键道具逻辑不一致）。
设计意图（定向修复）被实现（截断）自毁。
"""

from __future__ import annotations

import json

import pytest

from bestseller.services.planner import _previous_outline_batch_constraints

pytestmark = pytest.mark.unit

_LONG_GOAL = (
    "钟楼必须在月圆夜子时之前把被换进义冢的杂役送回第三百七十二座坟位，"
    "同时不让巡夜执事发现签到簿上的字迹不是他自己的，否则戒律堂会以窃替"
    "之罪把他连人带簿一起押去后山问斩。"
)


def _payload():
    return {
        "chapters": [
            {
                "chapter_number": 9,
                "title": "换坟",
                "chapter_goal": _LONG_GOAL,
                "main_conflict": _LONG_GOAL,
                "causal_contract": {
                    "protagonist_choice": _LONG_GOAL,
                    "pressure": _LONG_GOAL,
                },
                "scenes": [{"scene_number": 1, "purpose": _LONG_GOAL}],
            }
        ]
    }


def test_load_bearing_fields_keep_enough_text():
    out = _previous_outline_batch_constraints(
        _payload(), chapter_start=9, chapter_end=9, language="zh-CN"
    )
    blob = "\n".join(out)
    assert blob, "底稿必须渲染出来"
    data = json.dumps(json.loads(blob[blob.find("{") : blob.rfind("}") + 1]), ensure_ascii=False) if "{" in blob else blob
    # 承重字段必须保留足够长度（≥100 字），不能是 48 字残句
    for probe in ("第三百七十二座坟位", "签到簿"):
        assert probe in data, f"承重字段被截断，模型看不到「{probe}」"


def test_non_load_bearing_stays_compact():
    payload = _payload()
    payload["chapters"][0]["title"] = "换" * 80
    out = _previous_outline_batch_constraints(
        payload, chapter_start=9, chapter_end=9, language="zh-CN"
    )
    blob = "\n".join(out)
    # 非承重字段仍受紧凑上限约束（防 prompt 膨胀）
    assert "换" * 60 not in blob
