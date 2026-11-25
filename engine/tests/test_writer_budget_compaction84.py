"""Over budget, the writer input first drops JSON indentation, never content (84 real run, chapter 22)."""

import json

from bookengine.longutil import _writer_context
from bookengine.text import pack

OUTLINE = {
    "number": 22,
    "scenes": [{"goal": "查清旧局", "action": "取出另一套祖谱", "characters": ["陆厌", "陆霜"]}] * 6,
}


def _sections():
    return [("本章章纲", pack(OUTLINE)), ("上章结尾", "陆厌收刀。")]


def test_slightly_over_budget_is_compacted_without_losing_content():
    full = sum(len(f"## {name}\n{body}") + 2 for name, body in _sections()) - 2
    drops = []
    context, error = _writer_context(_sections(), full - 40, drops)
    assert error == ""
    body = context.split("## 本章章纲\n", 1)[1].split("\n\n## ", 1)[0]
    assert json.loads(body) == OUTLINE
    assert "陆厌收刀。" in context
    assert drops == ["压缩缩进"]


def test_fitting_input_is_left_untouched():
    full = sum(len(f"## {name}\n{body}") + 2 for name, body in _sections()) - 2
    context, error = _writer_context(_sections(), full, [])
    assert error == "" and pack(OUTLINE) in context


def test_far_over_budget_still_stops_with_sizes():
    context, error = _writer_context(_sections(), 50, [])
    assert context == "" and "写手必要材料需要" in error
