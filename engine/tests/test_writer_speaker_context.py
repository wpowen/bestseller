from bookengine.speaker_context import speaker_briefs
from bookengine.storage import save


def test_explicit_speakers_keep_distinct_canonical_voices_and_scene_intents():
    bible = {
        "characters": [
            {"name": "顾寒", "role": "protagonist", "voice": "只答对方问到的事"},
            {"name": "韩小满", "voice": "拿玩笑掩饰紧张"},
            {"name": "远方弟子", "voice": "抬杠"},
        ],
        "antagonists": [{"name": "魏横", "voice": "让人照做才肯说理由", "exit": 10}],
    }
    outline = {
        "number": 2,
        "opponent": "魏横",
        "scenes": [
            {
                "characters": ["顾寒", "魏横"],
                "obstacle": "魏横逼顾寒交出扫帚",
                "speaker_intents": [
                    {"name": "顾寒", "goal": "瞒住出剑时的手伤"},
                    {"name": "韩小满", "goal": "让魏横给后山弟子留饭", "voice": "厉声大喊"},
                    {"name": "凭空出现的人", "goal": "夺剑"},
                ],
            },
            {"characters": ["顾寒"], "speaker_intents": [{"name": "顾寒", "goal": "让同伴别跟进雪沟"}]},
        ],
    }

    first, second = speaker_briefs(outline, bible)
    speakers = {row["name"]: row for row in first["speakers"]}
    assert list(speakers) == ["顾寒", "魏横", "韩小满"]
    assert speakers["韩小满"] == {
        "name": "韩小满",
        "voice": "拿玩笑掩饰紧张",
        "goal": "让魏横给后山弟子留饭",
    }
    assert speakers["魏横"]["voice"] == "让人照做才肯说理由"
    assert speakers["魏横"]["goal"] == "魏横逼顾寒交出扫帚"
    assert second["speakers"][0]["goal"] == "让同伴别跟进雪沟"


def test_scheduled_entrants_get_voice_and_non_speaking_roles_are_excluded():
    bible = {
        "characters": [
            {"name": "药师", "voice": "讲剂量极准，讥讽时格外客气", "desire": "查清师姐的旧案"},
            {"name": "雪耳", "identity": "幼兽", "can_speak_human": "是", "human_speech_from_chapter": 8},
        ],
        "antagonists": [{"name": "旧对手", "exit": 3}],
    }
    outline = {
        "number": 4,
        "scenes": [
            {
                "characters": ["雪耳", "旧对手", "药童"],
                "speaker_intents": [{"name": "药童", "goal": "问药师要一味药", "voice": "说半句就看对方脸色"}],
            }
        ],
        "story_duties": [{"kind": "appearance", "names": ["药师"]}],
    }
    first, entrances = speaker_briefs(outline, bible)
    assert [p["name"] for p in first["speakers"]] == ["药童"]
    assert first["speakers"][0]["voice"] == "说半句就看对方脸色"
    assert entrances["speakers"][0]["voice"] == "讲剂量极准，讥讽时格外客气"


def test_carries_previous_speakers_only_for_a_continuing_scene(tmp_path):
    bible = {
        "characters": [{"name": "顾寒", "role": "protagonist"}, {"name": "柳听蝉", "voice": "常拿药作比"}],
        "antagonists": [{"name": "退休对手", "exit": 2}],
    }
    save(
        tmp_path / "writing_outlines/002.json",
        {"speaker_briefs": [{"scene": 2, "speakers": [{"name": "柳听蝉", "goal": "让顾寒停手一日"}]}]},
    )
    before = {"number": 3, "scenes": [{"characters": ["顾寒"], "goal": "保住扫帚"}]}

    first, handoff = speaker_briefs(before, bible, ["顾寒", "柳听蝉", "退休对手"], out=tmp_path)
    assert [row["name"] for row in first["speakers"]] == ["顾寒"]
    assert "仅在本章承接同一场" in handoff["scene"]
    assert handoff["speakers"] == [{"name": "柳听蝉", "voice": "常拿药作比", "goal": "让顾寒停手一日"}]
