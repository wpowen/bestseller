"""Instruction 60 C3: clause-level pronouns and gender-aware generated names."""

from pathlib import Path

import yaml

from bookengine.cast_consistency import cast_findings, repair_cast_consistency
from bookengine.gender_pronouns import pronoun_bindings
from bookengine.identity import annotate_name_gender_notes, name_gender_mismatch, writer_identity_cards


CARDS = {
    "characters": [
        {"name": "顾长风", "gender": "男", "role": "protagonist"},
        {"name": "沈展尧", "gender": "女", "role": "support"},
        {"name": "韩朔言", "gender": "男", "role": "support"},
        {"name": "崔林霏", "gender": "男", "role": "antagonist"},
        {"name": "薛欣妍", "gender": "男", "role": "antagonist"},
    ]
}

CH25 = (
    "整条暗槽震了一下，沉重的闸齿一格格咬合。尽头亮起幽青火光，崔林霏站在闸门之后。"
    "她肩头衣料残留着被反穿时撕裂的痕迹，掌心托着一团青光，那是从顾长风木手中转入她体内的深层权限。"
)
CH28 = "“是往里。”她道，“我原先也以为崔林霏在借阵眼求援。现在看，他是在防着别人从外面进来。”"
CH29 = (
    "最后一处灯座骤然倾斜，承重梁从中断落。崔林霏脚下的石砖随之塌陷，她被迫离开原位，"
    "断钉脱手，向悬空的灯座底座坠去——顾长风才要合拢的五指，僵在了木纹之间。"
)
CH40 = (
    "顾长风点头，目光落向正在缓慢熄灭的主纹。薛欣妍仍站在阵中，禁军与边军却已不再听从她的调动。"
    "他失去了的并非一件兵器，而是作为节度使对整支军队的控制。"
)
CH5_SPEAKER_QUOTE = "沈展尧将铜片收入袖中，道：“你若跪下，他便能借席纹逼你起手。”"


def _gender_rows(text, chapter):
    return [
        row
        for row in cast_findings(text, CARDS, chapter=chapter)
        if row["kind"] == "gender_pronoun" and row["status"] == "Evidence"
    ]


def test_c3_original_clause_shapes_bind_ch25_ch28_ch29_and_ch40_without_extra_repair_targets():
    expected_wrong = {25: ("崔林霏", "她", "他"), 29: ("崔林霏", "她", "他"), 40: ("薛欣妍", "她", "他")}
    for chapter, text in ((25, CH25), (29, CH29), (40, CH40)):
        rows = _gender_rows(text, chapter)
        assert [(row["name"], row["observed"], row["expected"]) for row in rows] == [expected_wrong[chapter]]

    assert _gender_rows(CH28, 28) == []
    assert pronoun_bindings(CH28, CARDS["characters"], 28) == []
    unknown = [
        row
        for row in cast_findings(CH28, CARDS, chapter=28)
        if row["kind"] == "gender_pronoun" and row["status"] == "Unknown"
    ]
    assert len(unknown) == 1


def test_c3_clause_binding_keeps_known_ambiguous_and_object_pronouns_out_of_evidence():
    safe = "顾长风看向她。她脸色苍白，却没有退缩。沈展尧扶住顾长风，两人一起越过断桥。他回头看了一眼。"
    assert _gender_rows(safe, 30) == []


def test_c3_third_person_inside_speakers_quote_never_binds_to_the_speaker():
    findings = [row for row in cast_findings(CH5_SPEAKER_QUOTE, CARDS, chapter=5) if row["kind"] == "gender_pronoun"]

    assert not [row for row in findings if row["status"] == "Evidence"]
    assert [(row["status"], row["observed"], row["name"]) for row in findings] == [("Unknown", "他", "")]
    assert pronoun_bindings(CH5_SPEAKER_QUOTE, CARDS["characters"], 5) == []


def test_c3_gender_errors_use_targeted_leaf_repair_and_recheck_clean():
    for chapter, text in ((25, CH25), (29, CH29), (40, CH40)):
        repaired, _, failed, note = repair_cast_consistency(
            None,
            f"r2-ch{chapter}",
            text,
            {"events": []},
            False,
            {},
            CARDS,
            {"number": chapter},
            lambda *_args, **_kwargs: ({"events": []}, False, 0),
            lambda *_args, **_kwargs: {"edits": []},
        )

        assert _gender_rows(repaired, chapter) == []
        assert failed is False and note["status"] == "repaired"


def test_c3_name_pool_marks_common_gender_impression_and_writer_receives_the_fact():
    rules = yaml.safe_load((Path(__file__).parents[1] / "data/polish_rules.yaml").read_text())
    assert name_gender_mismatch("崔林霏", "男", rules)
    assert name_gender_mismatch("薛欣妍", "男", rules)
    assert not name_gender_mismatch("崔延柯", "男", rules)

    bible = {
        "characters": [
            {"name": "崔林霏", "gender": "男", "role": "antagonist", "identity": "魂灯室控制者"},
            {"name": "沈展尧", "gender": "女", "role": "support", "identity": "巡查弟子"},
        ]
    }
    noted = annotate_name_gender_notes(bible, rules)
    cards = writer_identity_cards(noted)

    assert "常见印象" in noted["characters"][0]["name_gender_note"]
    assert cards[0]["pronoun"] == "他" and cards[0]["name_gender_note"] == noted["characters"][0]["name_gender_note"]
    assert "name_gender_note" not in noted["characters"][1]
