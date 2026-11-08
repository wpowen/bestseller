"""Regression coverage for INBOX 45 rhythm replanning and read-only diagnostics."""

from copy import deepcopy
import json

import yaml

from bookengine import chapter_rhythm
from bookengine import outline_diversity
from tools.book_checkup import check_book


def outline(number, kind="对抗"):
    return dict(
        number=number,
        title=chr(0x4E00 + number),
        type=kind,
        rhythm_type=kind,
        conflict_type=f"{kind}冲突",
        opening_conflict="追兵堵住矿道",
        scenes=[
            dict(
                where="旧矿道",
                characters=["顾川序", "霜砚秋"],
                goal="击退追兵夺路",
                obstacle="追兵封住出口",
                choice="顾川序拔刀突围",
                emotion="紧迫",
            ),
            dict(
                where="岔路",
                characters=["顾川序", "霜砚秋"],
                goal="甩开追兵",
                obstacle="伏兵从侧面围堵",
                choice="两人合力击退伏兵",
                emotion="戒备",
            ),
        ],
    )


def emotional_replan(number):
    row = outline(number, "情感")
    row.update(opening_conflict="霜砚秋发现顾川序隐瞒伤势，不肯再照旧配合", conflict_type="信任分歧")
    row["scenes"] = [
        dict(
            where="旧矿道",
            characters=["顾川序", "霜砚秋"],
            goal="说清隐瞒伤势的原因",
            obstacle="霜砚秋不再信任顾川序的判断",
            choice="顾川序坦白恐惧并交出路线决定权",
            emotion="恼怒转迟疑",
        ),
        dict(
            where="避风洞",
            characters=["顾川序", "霜砚秋"],
            goal="重新约定借力边界",
            obstacle="两人对谁承担代价仍有分歧",
            choice="霜砚秋接受新分工但保留拒绝权",
            emotion="疏离转谨慎信任",
        ),
    ]
    return row


def test_failed_rhythm_replanning_stops_after_two_calls_and_keeps_original(tmp_path):
    original = [outline(n) for n in range(1, 5)]
    before = deepcopy(original)
    attempts = []

    def still_combat(current, issues, targets, used, attempt):
        attempts.append(attempt)
        return {"chapters": [dict(outline(4), type="情感", rhythm_type="情感")]}

    result = outline_diversity.repair_batch(
        tmp_path,
        "001-004",
        [],
        original,
        still_combat,
        bible={"volumes": [{"number": 1, "start": 1, "end": 4}]},
    )

    assert attempts == [1, 2]
    assert result == before
    report = json.loads((tmp_path / "outline-diversity/001-004.json").read_text())
    assert report["status"] == "failed_continued"
    assert report["rhythm_replans"][0]["action"] == "kept_original"
    assert not report.get("rhythm_repairs")


def test_valid_model_replan_replaces_only_the_target_chapter(tmp_path):
    original = [outline(n) for n in range(1, 5)]

    def replan(current, issues, targets, used, attempt):
        assert targets == [4]
        return {"chapters": [emotional_replan(4)]}

    result = outline_diversity.repair_batch(
        tmp_path,
        "001-004",
        [],
        original,
        replan,
        bible={"volumes": [{"number": 1, "start": 1, "end": 4}]},
    )

    assert result[:3] == original[:3]
    expected = emotional_replan(4)
    assert all(result[3][key] == value for key, value in expected.items())
    assert result[3]["rhythm_contract_version"] == chapter_rhythm.VERSION
    report = json.loads((tmp_path / "outline-diversity/001-004.json").read_text())
    assert report["rhythm_replans"] == [{"number": 4, "attempts": 1, "action": "accepted_model_replan", "reason": []}]
    assert not chapter_rhythm.rhythm_issues({"volumes": [{"number": 1, "start": 1, "end": 4}]}, [], result)


def test_saved_eval45_template_language_is_a_deterministic_issue():
    saved_outline = dict(
        number=2,
        title="风隙藏身",
        type="谋划",
        rhythm_type="谋划",
        opening_conflict="顾川序与霜砚秋讨论原定目标的两种方案",
        scenes=[
            dict(
                where="旧矿道岔口",
                characters=["顾川序", "霜砚秋"],
                goal="比较原定目标的两种方案",
                obstacle="原章前提：霍岐从三条岔道逼近",
                choice="顾川序选择接受已登场同伴的方案",
            )
        ],
        hook="原定场景背后还有未说明的事实",
    )

    issues = chapter_rhythm.rhythm_issues({}, [], [saved_outline])

    issue = next(item for item in issues if item["kind"] == "rhythm_internal_language")
    assert issue["targets"] == [2]
    assert issue["terms"] == ["原章", "原定场景", "原定目标", "已登场同伴"]


def test_model_semantic_review_can_flag_a_label_content_mismatch():
    from bookengine.rhythm_replanning import model_review_issues

    batch = [emotional_replan(4)]
    review = [dict(number=4, content_type="对抗", status="mismatch", reason="两场都以突围交战为主")]

    assert model_review_issues(batch, review) == [
        dict(
            kind="rhythm_model_mismatch",
            targets=[4],
            allowed=["情感"],
            observed="对抗",
            reason="两场都以突围交战为主",
            requirement=chapter_rhythm.RULE,
        )
    ]


def test_semantic_review_is_one_small_model_call():
    from bookengine.rhythm_replanning import semantic_review

    class Client:
        model = "fixture-model"
        provider = "fixture-provider"

        def __init__(self):
            self.calls = []

        def stage(self, name, prompt, schema=None, **kwargs):
            self.calls.append((name, prompt, schema, kwargs))
            return kwargs["parser"](
                json.dumps(
                    {"chapters": [{"number": 4, "content_type": "对抗", "status": "mismatch", "reason": "仍在突围"}]},
                    ensure_ascii=False,
                )
            )

    client = Client()
    result = semantic_review(client, "001-004", [emotional_replan(4)])

    assert result[0]["status"] == "mismatch"
    assert len(client.calls) == 1
    name, prompt, schema, kwargs = client.calls[0]
    assert name == "plan-q1-rhythm-review-v8-001-004"
    assert all(term in prompt for term in ("只复核章型标签", "对手到场动手", "characters"))
    assert "content_type" in schema
    assert kwargs["cap"] == 1800 and kwargs["effort"] == "low"


def test_replan_context_contains_neighbors_and_observed_stage_facts():
    from bookengine.rhythm_replanning import replan_context

    history = [dict(outline(1), summary="两人逃进旧矿道")]
    batch = [outline(2), outline(3), outline(4)]
    observed = [dict(number=2, summary="顾川序发现妹妹留下的血痕", events=["血痕指向北侧岔口"])]

    context = replan_context(history, batch, [3], observed, stage_start=1, stage_end=4)

    assert [row["number"] for row in context["neighbor_outlines"]] == [2, 3, 4]
    assert context["observed_stage_facts"] == observed
    assert "summary" in context["neighbor_outlines"][0]


def test_checkup_reports_planned_and_actual_type_mismatch_without_writing(tmp_path):
    (tmp_path / "story_bible.yaml").write_text(
        yaml.safe_dump({"volumes": [{"number": 1, "start": 1, "end": 1}]}, allow_unicode=True)
    )
    (tmp_path / "chapters").mkdir()
    (tmp_path / "writing_outlines").mkdir()
    (tmp_path / "chapter-001.body.md").write_text("追兵扑来，顾川序拔刀撞开包围，带着霜砚秋冲出矿道。")
    (tmp_path / "chapters/001.json").write_text(json.dumps({"number": 1, "type": "对抗"}, ensure_ascii=False))
    (tmp_path / "writing_outlines/001.json").write_text(json.dumps(emotional_replan(1), ensure_ascii=False))
    before = {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}

    alignment = check_book(tmp_path, source="draft")["chapter_rhythm"]["outline_alignment"]

    assert alignment["matched"] == 0
    assert alignment["mismatch_chapters"] == [1]
    assert alignment["mismatches"][0] == {
        "chapter": 1,
        "planned_type": "情感",
        "actual_type": "对抗",
        "reason": "保存的正文章型为对抗，章纲要求情感",
        "status": "Inference",
    }
    assert {path: path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()} == before
