"""Instruction 80: problems found reading 《扫雪剑起》 chapters 21–50 and the re-test book.

- 孟长夜 was the direct opponent of chapters 31–35 and 37–40, 闻白帝 of 45–49 (re-test book: 韩阙 15–20).
- The 剑心 was shattered in chapter 45 and again in 49; the planner never saw what was already finished.
- Chapter 43 skipped the two-way choice chapter 42 ended on.
- Chapter 33 prose said “上章送出的残页”; chapter 27 had nested quotes ““…”…”.
- The re-test book's chapter 21 flagged “沈首席，他仍是杂役！” as a wrong pronoun for 沈照雪.
"""

import json

from bookengine.arc_runs import RUN_LIMIT, opponent_run_issues, planner_arc_section
from bookengine.gender_pronouns import _sentences, pronoun_findings
from bookengine.planning_policy import hard_replan_issues
from bookengine.prose_similarity import with_completed_action_context
from bookengine.speaker_context import GUIDANCE
from bookengine.story_design import SPEC
from bookengine.text_hygiene import inspect_text, nest_inner_quotes

BIBLE = dict(antagonists=[dict(name="孟长夜", stage=4, exit=40), dict(name="闻白帝", stage=5, exit=50)])


def _row(number, opponent, **extra):
    return dict(number=number, opponent=opponent, scenes=[], **extra)


def test_fourth_chapter_in_a_row_against_the_same_opponent_is_replanned():
    history = [_row(n, "孟长夜") for n in (31, 32, 33)]
    batch = [_row(34, "孟长夜与仙盟旧印"), _row(35, "孟长夜")]
    issues = opponent_run_issues(BIBLE, history, batch)
    assert [(i["kind"], i["opponent"], i["chapters"], i["targets"]) for i in issues] == [
        ("opponent_run", "孟长夜", [31, 32, 33, 34], [34])
    ]
    assert hard_replan_issues(issues)[0]["targets"] == [34]


def test_three_in_a_row_breaks_or_other_opponents_are_fine():
    history = [_row(n, "孟长夜") for n in range(31, 31 + RUN_LIMIT - 1)]
    assert opponent_run_issues(BIBLE, history, [_row(33, "孟长夜")]) == []
    broken = [_row(31, "孟长夜"), _row(32, "孟长夜"), _row(33, "旧印牵引")]
    assert opponent_run_issues(BIBLE, broken, [_row(34, "孟长夜"), _row(35, "孟长夜")]) == []
    assert opponent_run_issues(dict(antagonists=[]), [], [_row(1, "孟长夜")]) == []


def test_exit_chapter_and_villain_move_keep_the_opponent_on_stage():
    history = [_row(n, "孟长夜") for n in (37, 38, 39)]
    batch = [_row(40, "孟长夜")]  # his exit chapter
    assert opponent_run_issues(BIBLE, history, batch) == []
    move = dict(kind="villain_move", names=["闻白帝"])
    history = [_row(n, "闻白帝") for n in (45, 46)]
    batch = [_row(47, "闻白帝"), _row(48, "闻白帝", story_duties=[move]), _row(49, "闻白帝")]
    issues = opponent_run_issues(BIBLE, history, batch)
    assert issues and issues[0]["chapters"] == [45, 46, 47, 48] and issues[0]["targets"] == [47]


def test_planner_sees_recent_opponents_targets_and_methods(tmp_path):
    folder = tmp_path / "writing_outlines"
    folder.mkdir()
    for number in (44, 45):
        row = _row(number, "闻白帝", location="封剑海", solution_target="剑心", solution_method="借坠落旧剑")
        (folder / f"{number:03d}.json").write_text(json.dumps(row, ensure_ascii=False))
    [(heading, body)] = planner_arc_section(tmp_path, 46)
    assert "最近几章" in heading and "不能再攻破一次" in body["规则"]
    assert [row["破局目标"] for row in body["最近章节"]] == ["剑心", "剑心"]
    assert planner_arc_section(None, 46) == [] and planner_arc_section(tmp_path, 3) == []


def test_two_way_choice_ending_tells_the_writer_to_state_the_choice(tmp_path):
    folder = tmp_path / "writing_outlines"
    folder.mkdir()
    ending = dict(number=42, ending_type="两难选择", ending_content="顾寒必须在带走弟子和追查入口之间立即取舍")
    (folder / "042.json").write_text(json.dumps(ending, ensure_ascii=False))
    (tmp_path / "chapter-042.body.md").write_text("顾寒站在石缝前。")
    [(_, value)] = with_completed_action_context([("上章结尾", "x")], tmp_path, 43)
    assert "带走弟子" in value and "选了哪一边" in value


def test_meta_chapter_words_and_nested_quotes_are_cleaned():
    assert inspect_text("上章送出的残页还在他袖中。")["counts"]["structural_notes"] == 1
    assert inspect_text("他把残页交给石九。")["counts"]["structural_notes"] == 0
    nested = "““剑影最亮的地方没有阵纹……”你催了……”"
    assert nest_inner_quotes(nested) == "“‘剑影最亮的地方没有阵纹……’你催了……”"
    fixed = "“剑影最亮的地方，“没有阵纹”，你记得。”"
    assert nest_inner_quotes(fixed) == "“剑影最亮的地方，‘没有阵纹’，你记得。”"
    assert nest_inner_quotes("“甲。”“乙。”\n“未闭合") == "“甲。”“乙。”\n“未闭合"


def test_pronoun_after_a_form_of_address_is_not_bound_to_the_earlier_speaker():
    cards = [dict(name="沈照雪", gender="女"), dict(name="顾寒", gender="男")]
    text = "“再扫。”沈照雪站在人群前，手按剑鞘，“让他扫。”\n\n守榜弟子喝道：“沈首席，他仍是杂役！”"
    spans = _sentences(text)
    rows = pronoun_findings(text, cards, 21, spans, lambda *args: dict(status=args[3], name=args[2]))
    assert not [row for row in rows if row["status"] == "Evidence"]


def test_writer_and_speaker_rules_limit_injury_and_habit_repetition():
    general = SPEC["writer"]["general"]
    assert "旧伤只在它真正挡住一个动作" in general and "习惯动作和口头禅" in general
    assert "不用自己的名字称呼自己" in general
    assert "全章最多用一次" in GUIDANCE
