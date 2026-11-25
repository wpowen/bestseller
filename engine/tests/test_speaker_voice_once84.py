"""Each speaker's voice reaches the writer once per chapter (84 real run, chapter 22 over budget)."""

from bookengine.speaker_context import voice_once

LU = "短促冷硬，受辱时反而平静"
BRIEFS = [
    {"scene": 1, "speakers": [{"name": "陆厌", "voice": LU, "goal": "诱使陆重山离开阵心。"}]},
    {
        "scene": 2,
        "speakers": [
            {"name": "陆厌", "voice": LU, "goal": "用残页完成锁印。"},
            {"name": "阿墨", "voice": "童声直接", "goal": "对准锁位。"},
        ],
    },
]


def test_repeated_voice_is_given_only_at_first_appearance():
    result = voice_once(BRIEFS)
    assert result[0]["speakers"][0] == BRIEFS[0]["speakers"][0]
    assert result[1]["speakers"][0] == {"name": "陆厌", "goal": "用残页完成锁印。"}
    assert result[1]["speakers"][1] == BRIEFS[1]["speakers"][1]


def test_changed_voice_and_every_goal_are_kept():
    briefs = [
        {"scene": 1, "speakers": [{"name": "陆厌", "voice": LU, "goal": "甲"}]},
        {"scene": 2, "speakers": [{"name": "陆厌", "voice": "压低声音", "goal": "乙"}]},
    ]
    result = voice_once(briefs)
    assert result[1]["speakers"][0] == {"name": "陆厌", "voice": "压低声音", "goal": "乙"}


def test_input_is_not_mutated():
    before = repr(BRIEFS)
    voice_once(BRIEFS)
    assert repr(BRIEFS) == before


def test_voice_already_in_the_dossier_is_not_repeated():
    from bookengine.speaker_context import voice_once

    dossier = {"陆厌": LU}
    result = voice_once(BRIEFS, known=dossier)
    assert result[0]["speakers"][0] == {"name": "陆厌", "goal": "诱使陆重山离开阵心。"}
    assert result[1]["speakers"][1] == BRIEFS[1]["speakers"][1]  # 阿墨 has no dossier voice


def test_production_prompt_drops_speaker_voices_found_in_the_dossier(tmp_path):
    import json

    from bookengine.q1_draft_prompt import production_prompt
    from bookengine.speaker_context import HEADING
    from test_longform_callbacks84 import Book

    bible = {"characters": [{"name": "陆厌", "voice": LU, "role": "protagonist", "gender": "男"}]}
    outline = dict(
        number=3,
        scenes=[dict(characters=["陆厌"], action="他收刀。")],
        speaker_briefs=[{"scene": 1, "speakers": [{"name": "陆厌", "voice": LU, "goal": "逼问来人。"}]}],
    )
    book = Book("xuanhuan-power-fantasy", 120, 2200, "", writer_input="full")
    _, sections = production_prompt(
        book, bible, {}, dict(number=1, start=1, end=20), outline, {}, [], [], "", [], tmp_path, "b", {"b": ""}
    )
    body = dict(sections)[HEADING]
    assert "逼问来人" in body and LU not in body
    assert LU in json.dumps(dict(sections), ensure_ascii=False)
