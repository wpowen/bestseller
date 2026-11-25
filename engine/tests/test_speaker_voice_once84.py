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
