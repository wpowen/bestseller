from dataclasses import replace
import json

import pytest

from bookengine.config import Book
from bookengine.longutil import ask
from bookengine.q1_draft_prompt import production_prompt
from support_minimal_b import CaptureClient
from tools.runtime_support import ProjectError


BOOK = Book("xuanhuan-power-fantasy", 8, 1600, "摆渡人回家")
TAIL = "阿宁把铜钥匙塞进妹妹怀里，听见院门后有人敲了两下。"


def prepared(book, tmp_path, *, number=2):
    bible = {
        "title": "渡口",
        "selected_card": {"title": "渡口", "premise": "阿宁送妹妹回家"},
        "selected_card_rule_points": ["铜钥匙只能开一次"],
        "characters": [
            {"name": "阿宁", "role": "protagonist", "voice": "少说两句"},
            {"name": "妹妹", "voice": "问完才动"},
        ],
        "story_design": {"total": 8},
    }
    scenes = [
        {
            "where": "渡口",
            "characters": ["阿宁", "妹妹"],
            "goal": "阿宁要进院",
            "obstacle": "院门反锁",
            "choice": "妹妹决定把铜钥匙交给阿宁",
            "action": "妹妹把铜钥匙递给阿宁",
            "result": "阿宁去开院门",
            "emotion": "用于审校的情绪标签",
        },
        {
            "where": "院里",
            "characters": ["阿宁"],
            "goal": "问清敲门声是谁发出的",
            "choice": "阿宁决定先问屋里的人",
            "result": "屋里只剩一把空椅",
        },
    ]
    outline = {
        "number": number,
        "title": "敲门",
        "rhythm_type": "揭秘",
        "solution_type": "审校用解法分类",
        "hook_type": "审校用钩子分类",
        "ending_content": "空椅下压着半张渡票",
        "scenes": scenes,
        "story_duties": [{"id": "ticket", "kind": "plant", "text": "露出半张有红印的渡票"}],
        "speaker_briefs": [{"scene": 1, "speakers": [{"name": "妹妹", "goal": "想知道谁在应门"}]}],
        "disclosure_context": {"listener": "妹妹", "said": "渡票是陶家的"},
    }
    state = {
        "end_location": "渡口",
        "present": ["阿宁", "妹妹"],
        "objects": {
            "铜钥匙": {"owner": "妹妹", "location": "渡口"},
            "无关竹篮": {"owner": "老王", "location": "镇外"},
        },
    }
    prior = {"number": 1, "state": state, "status": "complete", "fact_source": "final_body", "summary": "妹妹收好钥匙"}
    instruction, sections = production_prompt(
        book,
        bible,
        {},
        {"number": 1},
        outline,
        state if number > 1 else {},
        [],
        [prior] if number > 1 else [],
        TAIL if number > 1 else "",
        [],
        tmp_path,
        "b",
        {"b": "旧版正文要求"},
    )
    return instruction, dict(sections)


def test_default_keeps_legacy_record_and_full_prompt(tmp_path):
    assert "writer_input" not in BOOK.record()
    full = prepared(BOOK, tmp_path)
    assert full == prepared(replace(BOOK, writer_input="full"), tmp_path)
    assert "旧版正文要求" in full[0]
    assert any("硬合同" in heading for heading in full[1])
    config = tmp_path / "新书.yaml"
    config.write_text(json.dumps({**BOOK.record(), "writer_input": "compact"}, ensure_ascii=False))
    assert Book.read(config).record()["writer_input"] == "compact"
    config.write_text(json.dumps({**BOOK.record(), "writer_input": "unknown"}))
    with pytest.raises(ValueError, match="writer_input"):
        Book.read(config)


def test_compact_projects_scene_and_preserves_facts(tmp_path):
    instruction, sections = prepared(replace(BOOK, writer_input="compact"), tmp_path)
    assert list(sections) == ["上章与当下事实", "本章章纲", "本场人物与设定"]
    material = json.loads(sections["本章章纲"])
    assert [scene["result"] for scene in material["场面"]] == ["阿宁去开院门", "屋里只剩一把空椅"]
    assert material["场面"][0]["choice"] == "妹妹决定把铜钥匙交给阿宁"
    assert "露出半张有红印的渡票" in material["本章须发生"]
    assert any("核心能力" in due and "代价" in due for due in material["本章须发生"])
    assert material["收尾发生的事"] == "空椅下压着半张渡票"
    assert TAIL in sections["上章与当下事实"]
    facts = json.loads(sections["上章与当下事实"].rsplit("\n\n", 1)[-1])
    assert facts["相关物件现况"]["铜钥匙"] == {"location": "渡口", "owner": "妹妹"}
    assert "渡票是陶家的" in sections["上章与当下事实"]
    assert "想知道谁在应门" in sections["本场人物与设定"]
    all_text = instruction + "\n".join(sections.values())
    assert not any(word in all_text for word in ("无关竹篮", "解法分类", "钩子分类", "情绪标签", "旧版正文要求"))


def test_compact_opening_accepts_legacy_card_and_respects_budget(tmp_path):
    instruction, sections = prepared(replace(BOOK, writer_input="compact"), tmp_path, number=1)
    assert "阿宁送妹妹回家" in sections["本场人物与设定"]
    assert TAIL not in sections["上章与当下事实"]
    client = CaptureClient(tmp_path, ["正文"])
    with pytest.raises(ProjectError, match="本章章纲"):
        ask(client, "r2-ch1-draft", instruction, sections, input_limit=600)
    assert client.calls == []
    assert ask(client, "r2-ch1-draft", instruction, sections, input_limit=10000) == "正文"
