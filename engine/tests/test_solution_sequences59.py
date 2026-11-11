"""Instruction 59 B1: detect and repair repeated solution step structures."""

import json

from bookengine.solution_sequences import (
    infer_solution_sequence,
    repair_solution_sequence,
    solution_sequence_issues,
    validate_solution_sequence,
)


EVAL57_CHAPTER_2 = (
    "顾立谦闭上眼，掌心按住它的鼻梁。淡青色的灵纹沿手臂亮起，霜团急促的喘息顿时涌进他感知。"
    "风雪的腥冷被剥开，藏在下面的气味一层层浮出。"
    "顾立谦反向推动粮车，失去轮木的车身歪斜着滑下右侧窄道，恰好卡住岑烈队伍留下的辙印。"
    "左侧雪匪刚扑到近前，便被绷紧的绳索绊倒。"
)

EVAL57_CHAPTER_3 = (
    "顾立谦俯身按住霜团颈背，掌心冻得失去知觉。"
    "雪层下每丝气味都变得锋利，真正的退路气味藏在更深的驿路岔口。"
    "顾立谦说，先把车队截断，再下去救人。"
    "矿工将旧橇横在两处岔路之间，短矛插进冻土，车队若强冲，轮轴必被卡死。"
)


def sequence(text, nouns):
    quotes = [part + "。" for part in text.split("。")]
    quotes = [quote for quote in quotes if quote != "。"]
    return {
        "steps": [
            {"role": "感知", "pattern": "借用感官", "quote": quotes[0]},
            {"role": "判断", "pattern": "辨认线索", "quote": quotes[1]},
            {"role": "行动", "pattern": "选择路径", "quote": quotes[2]},
            {"role": "结果", "pattern": "识破陷阱", "quote": quotes[3]},
        ],
        "surface_nouns": nouns,
    }


def test_eval57_chapters_two_and_three_repeat_steps_even_when_surface_nouns_change():
    first = validate_solution_sequence(sequence(EVAL57_CHAPTER_2, ["冷鼻", "粮车"]), EVAL57_CHAPTER_2)
    second = validate_solution_sequence(sequence(EVAL57_CHAPTER_3, ["兽息", "旧橇"]), EVAL57_CHAPTER_3)
    bible = {"volumes": [{"number": 1, "start": 1, "end": 10}]}

    issues = solution_sequence_issues(
        bible,
        [{"number": 2, "solution_sequence": first}],
        [{"number": 3, "solution_sequence": second}],
    )

    assert first["status"] == second["status"] == "Evidence"
    assert issues == [
        {
            "kind": "solution_sequence_repetition",
            "stage": 1,
            "signature": ["借用感官", "辨认线索", "选择路径", "识破陷阱"],
            "chapters": [2, 3],
            "targets": [3],
        }
    ]


def test_unquoted_or_incomplete_steps_stay_unknown_and_cannot_trigger_a_rewrite():
    raw = sequence(EVAL57_CHAPTER_2, ["冷鼻"])
    raw["steps"][2]["quote"] = "正文没有这句。"

    observed = validate_solution_sequence(raw, EVAL57_CHAPTER_2)

    assert observed["status"] == "Unknown"
    assert observed["signature"] == []


def test_checkup_lists_grounded_duplicate_groups(tmp_path):
    from tools.book_checkup import check_book

    (tmp_path / "story_bible.yaml").write_text("volumes:\n- number: 1\n  start: 1\n  end: 10\n", encoding="utf-8")
    (tmp_path / "chapters").mkdir()
    for number, text, nouns in (
        (2, EVAL57_CHAPTER_2, ["冷鼻", "粮车"]),
        (3, EVAL57_CHAPTER_3, ["兽息", "旧橇"]),
    ):
        (tmp_path / f"chapter-{number:03d}.body.md").write_text(text, encoding="utf-8")
        meta = {
            "number": number,
            "polish": {"solution_sequence": {"after": sequence(text, nouns)}},
        }
        (tmp_path / f"chapters/{number:03d}.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")

    report = check_book(tmp_path, source="draft")["solution_sequences"]

    assert report["duplicate_groups"] == [
        {
            "stage": 1,
            "signature": ["借用感官", "辨认线索", "选择路径", "识破陷阱"],
            "chapters": [2, 3],
        }
    ]
    assert report["unknown_chapters"] == []


def test_planning_requires_four_steps_and_rejects_renamed_repeat():
    bible = {"plot_contract_version": 1, "volumes": [{"number": 1, "start": 1, "end": 10}]}
    first = {
        "number": 2,
        "solution_method": "冷鼻",
        "solution_steps": sequence(EVAL57_CHAPTER_2, [])["steps"],
    }
    renamed = {
        "number": 3,
        "solution_method": "兽息",
        "solution_steps": sequence(EVAL57_CHAPTER_3, [])["steps"],
    }

    assert solution_sequence_issues(bible, [], [{"number": 1, "solution_method": "冷鼻"}])[0]["kind"] == (
        "solution_sequence_missing"
    )
    assert solution_sequence_issues(bible, [first], [renamed])[0]["kind"] == "solution_sequence_repetition"


def test_duplicate_actual_sequence_gets_one_targeted_rewrite_and_final_state_recheck(tmp_path):
    (tmp_path / "chapters").mkdir()
    original = "她借灵犬的鼻子嗅闻。她辨认气味线索。她选择右侧岔路。她识破了雪下陷阱。"
    replacement = "她询问守卫昨夜动静。她对照两份证词。她与管事谈判交换人质。众人终于救出矿工。"
    previous = infer_solution_sequence(original)
    (tmp_path / "chapters/002.json").write_text(
        json.dumps({"number": 2, "polish": {"solution_sequence": {"after": previous}}}, ensure_ascii=False),
        encoding="utf-8",
    )
    calls = []

    def ask(client, key, instruction, sections, schema, **kwargs):
        calls.append(key)
        allowed = dict(sections)["允许修改段"]
        return {"edits": [{"id": 1, "original": allowed[0]["original"], "text": replacement}]}

    def extract(*args, **kwargs):
        return {"state": {}, "events": []}, False, 0

    changed, raw, failed, note = repair_solution_sequence(
        None,
        "r2-ch3",
        original,
        {"state": {}},
        False,
        {},
        {"volumes": [{"number": 1, "start": 1, "end": 10}]},
        {"number": 3},
        tmp_path,
        extract,
        ask,
    )

    assert changed == replacement and raw["events"] == [] and failed is False
    assert note["status"] == "checked" and note["after"]["signature"] != previous["signature"]
    assert calls == ["r2-ch3-solution-sequence-repair"]
