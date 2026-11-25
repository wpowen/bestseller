from hashlib import sha256
import json

import pytest

from tools.deai_reader_trial import compare, prepare


def seat(group, arm, code, text):
    return dict(group=group, arm=arm, code=str(code), sha256=sha256(text.encode()).hexdigest())


def vote(code, rating):
    return f"【题组 · {code}】\nAI 味　{rating}"


def feedback(*rows):
    return "小说 AI 味盲读反馈\n\n" + "\n\n".join(rows)


def test_shuffled_pack_and_duplicate_calibration(tmp_path):
    output = tmp_path / "trial"
    samples = {
        "samples": [
            dict(group="hidden-scene", arm=a, text=t) for a, t in (("raw", "月牙落进水里。"), ("edit", "月落。"))
        ]
    }
    prepare(samples, output, ["hidden-scene", "edit"])
    manifest = json.loads((output / "组织者/盲号.json").read_text())
    text = (output / "读本.txt").read_text()
    assert "hidden-scene" not in text and "edit" not in text and "raw" not in text
    assert text.count("月落。") == 2 and text.count("月牙落进水里。") == 1
    form = (output / "反馈.txt").read_text()
    for row in manifest["seats"]:
        assert text.count(" · " + row["code"] + "】") == 1
        score = "有一点" if row.get("repeat") else "没察觉" if row["arm"] == "edit" else "明显"
        form = form.replace(row["code"] + "】\nAI 味　未评", row["code"] + "】\nAI 味　" + score)
    actual = compare(manifest, form)
    assert actual["changed_text_outcomes"] == {"improved": 1}
    assert actual["identical_text_pairs"][0]["outcome"] == "different"
    assert actual["ratings_by_arm"]["edit"] == {"没察觉": 1}
    assert actual["ratings"] == {"没察觉": 1, "明显": 1, "有一点": 1}
    with pytest.raises(ValueError, match="读本目录"):
        prepare(samples, output)
    with pytest.raises(ValueError, match="批次"):
        compare(manifest, form.replace(manifest["edition"], "2"))
    assert (output / "反馈.txt").read_text().count("未评") == 3


def test_existing_export_partial_uncertain_recognized_and_identical():
    seats = [
        seat(group, arm, code, "同底稿" if group == "D" else arm)
        for group, codes in zip("ABCD", ((10, 11), (20, 21), (30, 31), (40, 41)))
        for arm, code in zip(("raw", "edit"), codes)
    ]
    form = feedback(
        vote(10, "没察觉"),
        vote(20, "明显"),
        vote(21, "说不准"),
        vote(30, "明显") + "\n可能认出原文",
        vote(31, "没察觉"),
        vote(40, "明显") + "\n备注　看不下去\n【随笔】\n可能认出原文",
        vote(41, "没察觉"),
    )
    actual = compare({"seats": seats}, form)
    assert actual["changed_text_outcomes"] == {"missing": 1, "uncertain": 1, "recognized": 1}
    assert actual["pairs"][0]["ratings"] == ["没察觉", "未评"]
    assert actual["pairs"][3]["same_text"] and actual["identical_text_pairs"][0]["outcome"] == "different"
    assert actual["ratings_by_arm"]["edit"]["未评"] == 1


@pytest.mark.parametrize(
    "body",
    [
        feedback(vote(21, "好读")),
        feedback(vote(12, "没察觉")),
        feedback(vote(21, "明显"), vote(21, "没察觉")),
        feedback("【题组 · 11】\n来源判断　像真人"),
        '{"judge":{"more_human":"right"}}',
    ],
)
def test_rejects_invalid_reader_data(body):
    with pytest.raises(ValueError):
        compare({"seats": [seat("A", "raw", 11, "一"), seat("A", "edit", 21, "二")]}, body)


def test_rejects_changed_control_text():
    seats = [seat("A", "raw", 11, "一"), seat("A", "edit", 21, "二"), dict(seat("A", "edit", 31, "三"), repeat=True)]
    with pytest.raises(ValueError, match="重复盲号"):
        compare({"seats": seats}, feedback())
