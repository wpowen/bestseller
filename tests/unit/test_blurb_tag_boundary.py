# ruff: noqa: RUF001
import json
from pathlib import Path

import pytest

from bestseller.services.blurb_appeal_gate import (
    _first_sentence,
    evaluate_blurb_appeal,
    strip_leading_tag_line,
)

pytestmark = pytest.mark.unit


def test_sixth_metadata_is_not_the_story_first_sentence():
    data = json.loads(
        (Path(__file__).parents[1] / "fixtures/sixth-final-packaging.json").read_text()
    )
    assert _first_sentence(data["synopsis"]).startswith("执法殿")
    verdict = evaluate_blurb_appeal(
        title=data["title"],
        synopsis=data["synopsis"],
        premise="收尸人纪白能让死者完成临死时被打断的一个动作，每具只能触发一次。",
        tags=["东方玄幻"],
        genre="玄幻",
        sub_genre="东方玄幻",
    )
    axes = {d.key: d for d in verdict.dimensions}
    assert axes["hook_strength"].score == 3.7
    assert axes["length_format"].evidence["length"] == 167


@pytest.mark.parametrize(
    "text",
    [
        "守门人说：标签：别信他。\n我转身就跑。",
        "第一段是真的。\n标签：名字+身份\n第二段是真的。",
        "标签：真名+假名，是他逃亡时的暗号。\n门开了。",
    ],
)
def test_narrative_label_mentions_are_preserved(text):
    assert strip_leading_tag_line(text) == text


@pytest.mark.parametrize(
    "prefix", ["标签：东方玄幻+升级流", "  标签:东方玄幻＋升级流", "\n标签：东方玄幻+升级流"]
)
def test_generated_leading_metadata_is_removed(prefix):
    assert strip_leading_tag_line(prefix + "\n\n我看见门外的追兵。") == "我看见门外的追兵。"
