from __future__ import annotations

from bestseller.services.scene_beat_planner import (
    build_scene_beat_sheet,
    load_persisted_scene_beat,
    persist_scene_beat_sheet,
    scene_beat_sheet_to_persisted_dict,
)


def test_scene_beat_sheet_persists_yaml_artifact(tmp_path) -> None:
    sheet = build_scene_beat_sheet(
        chapter_number=4,
        scene_number=1,
        scene_title="303门口",
        time_label="23:53",
        participants=["林渊", "陈默"],
        story_purpose="林渊用罗盘核验陈默藏起的碎玉账。",
        entry_state={"location": "十七栋303门口"},
        exit_state={"visible": "陈默手机亮起回执"},
    )

    path = persist_scene_beat_sheet(sheet, tmp_path)
    loaded = load_persisted_scene_beat(path)

    assert path.name == "ch0004-s01.yaml"
    assert loaded["chapter_no"] == 4
    assert loaded["scene_no"] == 1
    assert loaded["opening_pattern"] == "time_anchor"
    assert "林渊" in loaded["named_entities"]
    assert loaded["ending_hook_target"]


def test_scene_beat_sheet_to_persisted_dict_is_stable() -> None:
    sheet = build_scene_beat_sheet(
        chapter_number=1,
        scene_number=1,
        scene_title="井底账印",
        participants=["林渊"],
        story_purpose="林渊摸到井底账印并确认父亲名字。",
        entry_state={"location": "老宅井底"},
        exit_state={"visible": "账印渗出林正淳三个字"},
    )

    payload = scene_beat_sheet_to_persisted_dict(sheet)

    assert payload["chapter_no"] == 1
    assert len(payload["camera_beats"]) == 3
    assert "老宅井底" in payload["named_entities"]
